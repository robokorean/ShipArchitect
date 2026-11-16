"""Viewport culling, render-only LOD, and progressive exact overlay preparation."""
from collections import OrderedDict
from time import perf_counter

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPen, QPolygonF

from .chunks import checkpoint
from .diagnostics import event
from .model import Bounds
from .render_settings import Performance, validated
from .space_display import alpha, color, config, visible
from .space_geometry import feature_lines
from .space_model import bbox
from .space_tiles import ByteCache, build_tiles, in_view, screen_boxes
from .workers import Task


def clip_signature(view):
    size = view.doc.schematic.size
    lo, hi = [0, 0, 0], [n - 1 for n in size]
    state = getattr(view, 'display_state', None)
    if state is not None:
        lo, hi = state.clip_bounds(lo, hi, view.doc, view.camera())
    if view.zone_only:
        lo = [max(a, b) for a, b in zip(lo, view.doc.selection.lo)]
        hi = [min(a, b) for a, b in zip(hi, view.doc.selection.hi)]
    if view.cut or view.single_layer:
        hi[1] = min(hi[1], view.layer)
    if view.single_layer:
        lo[1] = max(lo[1], view.layer)
    if view.ghost_floors and view.floor_range:
        lo[1], hi[1] = max(lo[1], view.floor_range[0]), min(hi[1], view.floor_range[1])
    levels = None if view.visible_y is None else frozenset(view.visible_y)
    return tuple(lo), tuple(hi), levels


class MeshTask(Task):
    partial=Signal(object)


class SpaceRenderer:
    def __init__(self, view):
        self.view = view
        self.performance = Performance()
        self.meshes = ByteCache(16 * 1024**2, 512)
        self.tiles = ByteCache(8 * 1024**2, 4096)
        self.metadata = OrderedDict()
        self.failed = OrderedDict()
        self.lod = {}
        self.groups = OrderedDict()
        self.worker = None
        self.partial = {}
        self.epoch = 0
        self.mesh_ms = self.clip_ms = self.cancelled = 0
        self.requested = {}
        self.priority = None
        self.revision = None
        self.halted = False
        self.last_failure = ""
        self.display_states={}

    def reset(self):
        self.epoch += 1
        self.revision = None
        self.halted = False
        if self.worker is not None:
            self.worker.requestInterruption()
        self.metadata.clear()
        self.groups.clear()
        self.partial.clear()
        self.meshes = ByteCache(self.meshes.limit, self.meshes.entries)
        self.tiles = ByteCache(self.tiles.limit, self.tiles.entries)
        self.failed.clear()
        self.lod.clear()

    def begin(self):
        self.performance.begin()
        self.display_states={}
        self.config = validated(getattr(self.view, 'render_quality', {}))
        self.meshes.limit = int(self.config['cache_mib'] * 1024**2 * 2 / 3)
        self.meshes.trim()
        self.requested = {}
        revision = self.view.doc.revision
        if self.revision != revision:
            if self.revision is not None:
                self.epoch += 1
                self.groups.clear()
                self.metadata.clear()
                self.partial.clear()
                if self.worker is not None:
                    self.worker.requestInterruption()
            self.revision = revision
        self.clip = clip_signature(self.view)
        bench = getattr(self.view, 'workbench', None)
        priority = (getattr(bench, 'selection_key', None), self.clip)
        if self.priority is not None and self.priority != priority and self.worker is not None:
            self.partial.clear()
            self.worker.requestInterruption()
        self.priority = priority
        self.used = [0, 0, 0, 0]
        self.small_prepared = 0
        self.primary = getattr(bench, 'selection_key', None)
        self.last_failure = next(reversed(self.failed.values()), '')
        frames = self.performance.frames
        target = 1000 / self.config['fps']
        ratio = (target / max(target, sum(frames) / len(frames))) if frames else 1
        if self.config['quality'] != 'auto':
            ratio = 1
        if self.view.interaction_active:
            ratio = min(ratio, .32)
        # Keep the selected volume on a separate budget, independent of the background.
        self.budget = [max(700, int(self.config['faces'] * max(.22, ratio))),
                       self.config['selection_faces']]
        self.budget.extend((min(3200,self.budget[0]),self.config['selection_faces']))
        if self.view.interaction_active:
            self.budget=[0,min(160,self.budget[1]),0,0]
        self.performance.current['lod_budget'] = self.budget[0]

    def finish(self):
        self.performance.end()
        if self.worker is not None or not self.requested or self.halted:
            return
        batch = sorted(self.requested.values(), key=lambda item: item[0])[:16]
        epoch = self.epoch
        tile_cache = self.tiles
        tile_cache.limit = int(self.config['cache_mib'] * 1024**2 / 3)
        tile_cache.trim()

        def work(_, cancel):
            results = []
            for rank, key, runs, outline, clip in batch:
                checkpoint(cancel)
                started = perf_counter()
                try:
                    def publish(tiles, done, total):
                        task.partial.emit((key, runs, tiles, done, total))
                    tiles, clip_ms, mesh_ms = build_tiles(runs, clip, tile_cache, cancel, publish if rank[0]==0 else None)
                    if outline and tiles:
                        q = np.concatenate([t.quads for t in tiles])
                        n = np.concatenate([t.normals for t in tiles])
                        if len(q) <= 30000:
                            edges = feature_lines(q, n)
                        else:
                            edges = None
                    else:
                        edges = None
                    mesh_ms = (perf_counter() - started) * 1000 - clip_ms
                    results.append((key, (runs, tiles, edges), clip_ms, mesh_ms, None))
                except ValueError as exc:
                    results.append((key, None, 0, 0, str(exc)))
            return results

        task = MeshTask(work, self.view)
        self.worker = self.view.geometry_worker = task

        def accept(results):
            if epoch != self.epoch or self.halted:
                return
            for key, packet, clip_ms, mesh_ms, error in results:
                if error:
                    self.reject(key, error)
                else:
                    _, tiles, edges = packet
                    size = sum(t.nbytes + 256 for t in tiles) + (edges.nbytes if edges is not None else 0) + 256
                    if size > self.meshes.limit:
                        self.reject(key, '캐시 예산보다 큰 공간 · 캐시 예산을 높이거나 2D 단면으로 확인하세요.')
                    else:
                        self.meshes.put(key, packet, size)
                self.clip_ms, self.mesh_ms = clip_ms, mesh_ms

        def partial(value):
            key, runs, tiles, count, total = value
            if epoch == self.epoch and not self.halted and key[0] == self.primary and key[2] == self.clip:
                self.partial[key] = (runs, tiles, None), count, total
                self.view.update()

        def done():
            self.partial.clear()
            self.worker = self.view.geometry_worker = None
            self.view.update()

        def cancelled():
            self.cancelled += 1

        task.result.connect(accept)
        task.partial.connect(partial)
        task.failed.connect(lambda error: [self.reject(item[1], error) for item in batch] if epoch == self.epoch else None)
        task.cancelled.connect(cancelled)
        task.finished.connect(done)
        task.finished.connect(task.deleteLater)
        task.start()

    def reject(self, key, error):
        self.failed[key] = error
        self.last_failure = error
        while len(self.failed) > 128:
            self.failed.popitem(last=False)
        event('space_mesh_display_failed', reason=error, space_id=key[0])

    def stats(self):
        return self.performance.snapshot() | {
            'cache_bytes': self.meshes.bytes + self.tiles.bytes,
            'cache_hits': self.meshes.hits + self.tiles.hits,
            'cache_misses': self.meshes.misses + self.tiles.misses,
            'clip_ms': round(self.clip_ms, 2), 'mesh_ms': round(self.mesh_ms, 2),
            'queued': min(16, len(self.requested)), 'worker_active': self.worker is not None,
            'cancelled': self.cancelled, 'failure': self.last_failure,
            'active_tile_progress': list(next(iter(self.partial.values()), (None,0,0))[1:]),
        }

    def bounds_for(self, space, runs, marker):
        key = (space['id'], id(runs), self.clip)
        if key in self.metadata:
            self.metadata.move_to_end(key)
            return self.metadata[key][1:]
        low, high = (space['lo'], space['hi']) if 'lo' in space and 'hi' in space else bbox({'runs':runs})
        clip_lo, clip_hi, levels = self.clip
        low = tuple(max(a, b) for a, b in zip(low, clip_lo))
        high = tuple(min(a, b) for a, b in zip(high, clip_hi))
        point = None
        if marker and all(a <= b for a, b in zip(low, high)):
            cx, cy, cz = [(a + b) // 2 for a, b in zip(low, high)]
            best = float('inf')
            for y, z, a, b in runs:
                if not low[1] <= y <= high[1] or not low[2] <= z <= high[2] or levels is not None and y not in levels:
                    continue
                a, b = max(a, low[0]), min(b, high[0])
                if a > b:
                    continue
                x = min(b, max(a, cx))
                score = abs(x-cx) + abs(y-cy) + abs(z-cz)
                if score < best:
                    point, best = (x + .5, y + .5, z + .5), score
                if score == 0:
                    break
        self.metadata[key] = (runs, low, high, point)
        while len(self.metadata) > 8500:
            self.metadata.popitem(last=False)
        return low, high, point

    def packet(self, scene_key, runs, outline, priority):
        key = scene_key, id(runs), self.clip, outline
        packet = self.meshes.get(key)
        if packet is not None or key in self.failed or self.halted:
            return packet, key in self.failed
        if len(runs) <= 64 and self.small_prepared < 2 and not self.view.interaction_active:
            self.small_prepared += 1
            try:
                tiles, self.clip_ms, self.mesh_ms = build_tiles(runs, self.clip, ByteCache(0, 0))
                edges = feature_lines(np.concatenate([t.quads for t in tiles]),
                                      np.concatenate([t.normals for t in tiles])) if outline and tiles else None
                packet = runs, tiles, edges
                self.meshes.put(key, packet, sum(t.nbytes + 256 for t in tiles) + 512)
                return packet, False
            except ValueError as exc:
                self.reject(key, str(exc))
                return None, True
        if (not self.view.interaction_active and (len(self.requested) < 128 or priority[0] < 4)) or priority[0] == 0:
            self.requested[key] = (priority, key, runs, outline, self.clip)
        return None, False


def _project_faces(view, tiles, direction):
    if not tiles:
        return np.empty((0, 4, 3)), np.empty((0, 4, 2)), 0
    low, high = screen_boxes(view, [t.lo for t in tiles], [t.hi for t in tiles])
    active = [t for t, show in zip(tiles, in_view(view, low, high)) if show]
    if not active:
        return np.empty((0, 4, 3)), np.empty((0, 4, 2)), 0
    q = np.concatenate([t.quads[t.normals @ direction > 1e-6] for t in active])
    points = view.project(q)
    visible_pixels = in_view(view, points.min(axis=1), points.max(axis=1))
    return q[visible_pixels], points[visible_pixels], len(active)


def _choose_faces(screen, depth, allowed):
    if allowed >= len(depth):
        return np.argsort(depth)
    if allowed <= 0:
        return np.empty(0, dtype=int)
    # Preserve coherent patches in screen bins rather than truncating by world sort order.
    centers = screen.mean(axis=1)
    span = np.maximum(1, centers.max(axis=0) - centers.min(axis=0))
    step = np.sqrt(np.prod(span) / max(1, allowed))
    bins = np.floor((centers - centers.min(axis=0)) / max(1, step)).astype(int)
    large_first = np.argsort(-np.prod(np.maximum(1, screen.max(axis=1) - screen.min(axis=1)), axis=1))
    _, unique_indices = np.unique(bins[large_first], axis=0, return_index=True)
    result = large_first[np.sort(unique_indices)[:allowed]]
    return result[np.argsort(depth[result])]


def draw_volumes(view, painter, candidates=False, spaces_override=None, selectable=True, highlight=False):
    renderer = view.space_renderer
    stats = renderer.performance.current
    shapes = spaces_override if spaces_override is not None else view.candidate_boxes if candidates else view.doc.spaces
    cfg = renderer.config
    selected = getattr(view, 'scene_selection', set())
    prior = renderer.primary
    summaries = []
    entries = []
    preview=bool(spaces_override and spaces_override[0].get('_edit_preview'))
    before_projection = perf_counter()
    hull = getattr(view, 'workbench', None)
    direction = view.camera()
    if spaces_override is None:
        presentation = config(view)
        group_key=(candidates,id(shapes),renderer.clip,tuple(sorted(selected)),
                   view.exact_space_only,view.doc.active_space_id,id(getattr(view,'changed_candidate_ids',None)),
                   presentation.get('mode'),presentation.get('normal_alpha'),presentation.get('candidate_alpha'),
                   presentation.get('external'),presentation.get('only_selected'),presentation.get('show_candidates'),
                   presentation.get('organized'),presentation.get('min_volume'),presentation.get('use_filter'),
                   tuple(presentation.get('visibility',{}).items()))
    else:
        group_key=None
    if group_key in renderer.groups:
        renderer.groups.move_to_end(group_key)
        _,entries,lower,upper,hidden,clipped=renderer.groups[group_key]
        stats['hidden']+=hidden
    else:
        hidden=0
        clipped=[]
        for item in shapes:
            scene_key = item.get('_scene_key', ('candidate:' if candidates else 'space:') + item['id'])
            if spaces_override is None and scene_key in selected:
                continue
            if spaces_override is None and (not visible(view, item, candidates) or
                    not candidates and view.exact_space_only and item['id'] != view.doc.active_space_id):
                hidden += 1
                continue
            runs = item.get('runs', item.get('volume_runs'))
            if not runs:
                continue
            opacity = item.get('opacity', 60) if spaces_override is not None else alpha(view, item, candidates)
            if not opacity:
                hidden += 1
                continue
            lo, hi, point = renderer.bounds_for(item, runs, True)
            if point is None:
                clipped.append(scene_key)
                hidden += 1
                continue
            entries.append((scene_key, item, runs, opacity, lo, hi, point))
        stats['hidden']+=hidden
        lower=np.asarray([entry[4] for entry in entries])
        upper=np.asarray([entry[5] for entry in entries])+1
        if group_key is not None:
            renderer.groups[group_key]=shapes,entries,lower,upper,hidden,clipped
            while len(renderer.groups)>8:
                renderer.groups.popitem(last=False)
    renderer.display_states.update(dict.fromkeys(clipped,'clip'))
    if not entries:
        return
    low, high = screen_boxes(view, lower, upper)
    showing = in_view(view, low, high)
    stats['offscreen'] += len(entries) - int(showing.sum())
    for i in np.flatnonzero(~showing):
        renderer.display_states[entries[i][0]]='outside'
    extent = (high - low).max(axis=1)
    if view.interaction_active and not highlight and config(view).get('geometry')!='bounds':
        stats['projection_ms'] += (perf_counter() - before_projection) * 1000
        draw_summaries(view,painter,[(entries[i][0],entries[i][-1]) for i in np.flatnonzero(showing)],False,stats)
        return
    # Reserve the first slot on a warm or cold frame for the active selection.
    organized=config(view).get('organized',False)
    kinds=getattr(view,'space_classes',{}) if organized else {}
    ranks={'major':3,'support':4,'movement':4}
    def rank(key):
        if key==prior and highlight:return 0
        if highlight:return 1
        if preview:return 1
        if key.startswith('space:'):return 2
        return ranks.get(kinds[key].kind,5) if key in kinds else 5
    order = sorted(np.flatnonzero(showing), key=lambda i: (rank(entries[i][0]), -extent[i], entries[i][0]))
    majors=sum(rank(entries[i][0])==3 for i in order)
    stats['projection_ms'] += (perf_counter() - before_projection) * 1000
    for i in order:
        key, item, runs, opacity, lo, hi, point = entries[i]
        priority=rank(key)
        lane=3 if preview else 1 if highlight else 2 if priority==3 else 0
        if spaces_override is None and config(view).get('geometry') == 'bounds':
            view.box(painter, Bounds(lo, hi))
            stats['summary'] += 1
            renderer.display_states[key]='summary'
            continue
        active = highlight and key == prior
        cutoff = (22 if view.interaction_active else 8) * (1.8 if cfg['quality'] == 'low' else 1)
        detail = extent[i] >= cutoff * (0.85 if renderer.lod.get(key, False) else 1.15)
        renderer.lod[key] = detail
        if len(renderer.lod) > 8500:
            renderer.lod.clear()
        remaining = renderer.budget[lane] - renderer.used[lane]
        threshold = max(40, int(2600 * (extent[i] / max(1, view.width())) ** 1.3))
        allowed = remaining if active or not cfg['lod'] else min(remaining, threshold)
        if preview:
            detail=True
            allowed=remaining
        if priority==3:
            majors-=1
            # Leave shape capacity for other major rooms in the same view.
            allowed=max(0,remaining-min(majors*96,remaining//2))
            detail=True
        if highlight:
            stats['selection_expected'] += 1
        if allowed <= 0 or not highlight and cfg['lod'] and not detail:
            summaries.append((key,point))
            continue
        packet, failure = renderer.packet(key, runs, highlight,(priority,-extent[i]))
        partial = False
        if packet is None:
            stats['failed' if failure else 'pending'] += 1
            renderer.display_states[key]='failed' if failure else 'pending'
            inflight = renderer.partial.get((key,id(runs),renderer.clip,highlight))
            if inflight is None:
                summaries.append((key,point))
                continue
            packet, partial = inflight[0], True
        _, tiles, edges = packet
        started = perf_counter()
        q, screen, count = _project_faces(view, tiles, direction)
        stats['visible_tiles'] += count
        if not len(q):
            stats['offscreen'] += 1
            renderer.display_states[key]='outside'
            continue
        depth = (q @ direction).mean(axis=1)
        selected_faces = _choose_faces(screen, depth, allowed)
        stats['projection_ms'] += (perf_counter() - started) * 1000
        if not len(selected_faces):
            summaries.append((key,point))
            continue
        renderer.used[lane] += len(selected_faces)
        stats['faces'] += len(selected_faces)
        exact = len(selected_faces) == len(q) and not partial
        stats['exact' if exact else 'simplified'] += 1
        renderer.display_states[key]='exact' if exact else 'simplified'
        started = perf_counter()
        tint = QColor(item.get('color', '#ffce62') if highlight else '#54bfc9' if candidates else color(item))
        fill = QColor(tint)
        fill.setAlpha(opacity)
        painter.setBrush(fill)
        painter.setPen(Qt.PenStyle.NoPen)
        xray = not highlight or hull is None or hull.selection_xray.isChecked()
        drawn=0
        for index in selected_faces:
            if not xray and view.occluded_point(q[index].mean(axis=0)):
                continue
            poly = QPolygonF([QPointF(*pt) for pt in screen[index]])
            painter.drawPolygon(poly)
            drawn+=1
            if selectable:
                if key.startswith('space:'):
                    view.space_hits.append((item['id'], poly))
                else:
                    view.scene_hits.append((key, poly, float(depth[index])))
        if not drawn and not xray:renderer.display_states[key]='covered'
        if highlight and edges is not None:
            pixels = view.project(edges)
            inside = in_view(view, pixels.min(axis=1), pixels.max(axis=1))
            painter.setPen(QPen(tint, 1.7))
            hit = np.flatnonzero(inside)
            # Preserve bounded responsiveness when thousands of edges project together.
            limit = 200 if view.interaction_active else 12000
            stats['selection_exact'] += int(exact and len(hit)<=limit)
            if len(hit) > limit:
                hit = hit[::(1+len(hit)//limit)]
            for j in hit:
                if xray or not view.occluded_point(edges[j].mean(axis=0)):
                    painter.drawLine(QPointF(*pixels[j,0]), QPointF(*pixels[j,1]))
        stats['draw_ms'] += (perf_counter() - started) * 1000
    draw_summaries(view,painter,summaries,highlight,stats)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def draw_summaries(view, painter, points, highlight, stats):
    if not points:
        return
    started=perf_counter()
    keys,positions=zip(*points)
    xy=view.project(positions)
    on_screen=in_view(view,xy,xy)
    stats['summary'] += int(on_screen.sum())
    stats['not_drawn'] += len(xy)-int(on_screen.sum())
    for key,show in zip(keys,on_screen):
        view.space_renderer.display_states.setdefault(key,'summary' if show else 'off_marker')
    painter.setPen(QPen(QColor('#ffce62' if highlight else '#6aafba'), 3 if highlight else 2))
    painter.drawPoints(QPolygonF([QPointF(*pt) for pt in xy[on_screen]]))
    stats['draw_ms'] += (perf_counter()-started)*1000
