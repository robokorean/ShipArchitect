"""Read-only evidence for exterior recognition and artificial partition repair."""

from collections import defaultdict
from itertools import product

import numpy as np

from .chunks import checkpoint
from .spatial3d import fingerprint


SOILS = frozenset('minecraft:' + name for name in (
    'grass_block', 'dirt', 'coarse_dirt', 'rooted_dirt', 'podzol', 'mycelium',
))
ALGORITHM_VERSION = '0.9.3.4.1'
MAX_MERGED_VOLUME = 50000


def terrain_mask(blocks, palette, bounds, size, queue, components, cancel):
    """Recognize broad, thin soil connected to the real bottom and lateral edge."""
    mask = np.zeros(blocks.shape, dtype=bool)
    ids = [key for key, state in palette.items() if state.split('[')[0] in SOILS]
    report = {'ignored_cells': 0, 'components': [], 'rule': 'broad_thin_boundary_soil'}
    if bounds.lo[1] != 0 or not ids or not np.isin(blocks[0], ids).any():
        return mask, report

    soil = np.isin(blocks, ids)
    labels, _ = components(soil, queue, cancel)
    bottom_ids, areas = np.unique(labels[0], return_counts=True)
    # A partial selection edge is not evidence of a terrain boundary.
    edges = [
        edge for axis, high, edge in (
            (0, False, labels[0, :, 0]), (0, True, labels[0, :, -1]),
            (2, False, labels[0, 0, :]), (2, True, labels[0, -1, :]),
        )
        if (bounds.hi[axis] == size[axis] - 1 if high else bounds.lo[axis] == 0)
    ]
    border_ids = set(np.unique(np.concatenate(edges))) if edges else set()
    for ident, area in zip(bottom_ids, areas):
        checkpoint(cancel)
        if ident == 0 or area < 64 or ident not in border_ids:
            continue
        region = labels == ident
        y, z, x = (np.flatnonzero(region.any(axis=axes))
                   for axes in ((1, 2), (0, 2), (0, 1)))
        height, depth, width = (int(a[-1] - a[0] + 1) for a in (y, z, x))
        if min(depth, width) < 8 or area < .4 * width * depth:
            continue
        if height > max(2, min(depth, width) // 12):
            continue
        mask |= region
        offset = np.asarray(bounds.lo)
        report['components'].append({
            'lo': (offset + [x[0], y[0], z[0]]).tolist(),
            'hi': (offset + [x[-1], y[-1], z[-1]]).tolist(),
            'cells': int(region.sum()), 'bottom_area': int(area),
        })
    report['ignored_cells'] = int(mask.sum())
    return mask, report


def _contacts(labels, cancel):
    """Collect only actual face neighbours of different nonzero labels."""
    contacts = defaultdict(list)
    for axis in range(3):
        checkpoint(cancel)
        lo = [slice(None)] * 3
        hi = lo.copy()
        lo[axis] = slice(0, -1)
        hi[axis] = slice(1, None)
        a, b = labels[tuple(lo)], labels[tuple(hi)]
        mask = (a != b) & (a > 0) & (b > 0)
        points = np.argwhere(mask)
        pairs = np.sort(np.column_stack((a[mask], b[mask])), axis=1)
        order = np.lexsort(pairs.T)
        keys = pairs[order]
        cuts = np.r_[0, np.flatnonzero(np.any(keys[1:] != keys[:-1], axis=1)) + 1, len(keys)]
        for start, stop in zip(cuts[:-1], cuts[1:]):
            if start != stop:
                contacts[tuple(map(int, keys[start]))].append((axis, points[order[start:stop]]))
    return contacts


def _support_levels(hull, points, step):
    """Closest actual occupied surface in each contact column."""
    y, z, x = points.T
    level = np.full(len(y), -1, dtype=np.int32)
    remaining = np.arange(len(y))
    for distance in range(1, hull.shape[0]):
        yy = y[remaining] + step * distance
        inside = (yy >= 0) & (yy < hull.shape[0])
        remaining, yy = remaining[inside], yy[inside]
        if not len(remaining):
            break
        hit = hull[yy, z[remaining], x[remaining]]
        level[remaining[hit]] = yy[hit]
        remaining = remaining[~hit]
    return level


def _runs(labels, ident, offset):
    points = np.argwhere(labels == ident)
    runs = []
    if len(points):
        changes = np.any(points[1:, :2] != points[:-1, :2], axis=1)
        changes |= points[1:, 2] != points[:-1, 2] + 1
        cuts = np.r_[0, np.flatnonzero(changes) + 1, len(points)]
        x0, y0, z0 = offset
        for a, b in zip(cuts[:-1], cuts[1:]):
            y, z, x = map(int, points[a])
            runs.append([y + y0, z + z0, x + x0, int(points[b - 1, 2]) + x0])
    return runs


def repair_partitions(labels, hull, blocks, palette, bounds, protected, rejected, cancel):
    """Join wide wall-free seams with matching real floor and roof evidence."""
    counts = np.bincount(labels.ravel())
    contacts = _contacts(labels, cancel)
    passage_ids = [key for key, state in palette.items()
                   if any(word in state.split('[')[0] for word in ('door', 'stairs', 'elevator'))]
    parent = np.arange(len(counts))
    group_sizes = counts.copy()
    decisions = []
    source_fingerprints = {}
    claimed = set()
    offset = np.asarray(bounds.lo)

    def root(i):
        while parent[i] != i:
            i = int(parent[i])
        return i

    for (a, b), interfaces in sorted(contacts.items()):
        checkpoint(cancel)
        if min(counts[a], counts[b]) < 27 or counts[a] + counts[b] > MAX_MERGED_VOLUME:
            continue
        horizontal = [(axis, p) for axis, p in interfaces if axis != 0]
        area = sum(len(p) for _, p in horizontal)
        if area < 12:
            continue
        vertical = sum(len(p) for axis, p in interfaces if axis == 0)
        left = np.concatenate([p for _, p in horizontal])
        rights = []
        for axis, points in horizontal:
            other = points.copy()
            other[:, axis] += 1
            rights.append(other)
        right = np.concatenate(rights)
        heights, sections = np.unique(left[:, 0], return_counts=True)
        width = max(len(np.unique(p[:, 3-axis])) for axis, p in horizontal)
        flat = max(len(p) for _, p in horizontal) / area
        height = len(heights)
        width_ratio = width / np.sqrt(min(counts[a], counts[b]) / max(1, height))
        floor_a, floor_b = (_support_levels(hull, p, -1) for p in (left, right))
        roof_a, roof_b = (_support_levels(hull, p, 1) for p in (left, right))
        both_supported = (floor_a >= 0) & (floor_b >= 0) & (roof_a >= 0) & (roof_b >= 0)
        agreement = float(np.mean(both_supported & (abs(floor_a-floor_b) <= 1) & (abs(roof_a-roof_b) <= 1)))
        nearby_devices = False
        if passage_ids:
            for p in (left, right):
                for delta in product(range(-1, 2), repeat=3):
                    # Face neighbours capture door leaves, stair tread and elevator openings.
                    if sum(map(abs, delta)) > 1:
                        continue
                    q = p + delta
                    q = q[np.all((q >= 0) & (q < labels.shape), axis=1)]
                    if np.isin(blocks[tuple(q.T)], passage_ids).any():
                        nearby_devices = True
                        break
                if nearby_devices:
                    break

        reasons = []
        if height < 2 or width < 4 or width_ratio < (.83 if flat >= .85 else .48):
            reasons.append('narrow_or_wall_aligned_interface')
        if vertical > .2 * area:
            reasons.append('between_floor_interface')
        if agreement < .72:
            reasons.append('different_or_missing_floor_roof')
        if nearby_devices:
            reasons.append('passage_device_near_interface')
        decision = {
            'labels': [a, b], 'horizontal_faces': area, 'vertical_faces': vertical,
            'width': width, 'height_levels': height, 'max_layer_faces': int(sections.max()),
            'width_ratio': round(float(width_ratio), 4), 'support_agreement': round(agreement, 4),
            'samples': [{'axis_yzx': axis, 'sign': 1,
                         'positions': (points[::max(1, (len(points)+31)//32)][:, [2, 0, 1]] + offset).tolist()}
                        for axis, points in horizontal],
        }
        if not reasons:
            for ident in (a, b):
                if ident not in source_fingerprints:
                    source_fingerprints[ident] = fingerprint(_runs(labels, ident, bounds.lo))
            keys = sorted(source_fingerprints[i] for i in (a, b))
            decision['source_fingerprints'] = keys
            if protected.intersection(keys) or ':'.join(keys) in rejected:
                reasons.append('protected_or_rejected_merge')
        ra, rb = root(a), root(b)
        if ra != rb and group_sizes[ra] + group_sizes[rb] > MAX_MERGED_VOLUME:
            reasons.append('merged_group_volume_limit')
        if a in claimed or b in claimed:
            reasons.append('competing_partition_requires_review')
        decision['merged'] = not reasons
        decision['reasons'] = reasons or ['wide_air_seam_with_continuous_floor_roof']
        decisions.append(decision)
        if not reasons and ra != rb:
            claimed.update((a, b))
            parent[rb] = ra
            group_sizes[ra] += group_sizes[rb]

    roots = np.array([root(i) for i in range(len(parent))], dtype=np.int32)
    merged = roots[labels]
    for decision in decisions:
        decision['result_labels'] = [int(roots[i]) for i in decision['labels']]
    return merged, decisions


def exposure_class(candidate, faces):
    """Distinguish geometry-supported spaces from sky/underside air."""
    exterior = candidate['virtual_boundary']['raw_exterior_cells'] > 0
    if not exterior:
        return 'closed_interior' if candidate['decision'] == 'auto' else 'uncertain'
    total = faces['hull'] + faces['air'] + faces['terrain']
    hull_ratio = faces['hull'] / max(1, total)
    roof_ratio = faces['roof'] / max(1, faces['top_boundary'])
    floor_ratio = faces['floor'] / max(1, faces['bottom_boundary'])
    if floor_ratio < .18 and hull_ratio < .7:
        return 'exterior_air'
    if roof_ratio < .22 and floor_ratio >= .55:
        return 'exposed_deck'
    if roof_ratio >= .68 and floor_ratio >= .63 and hull_ratio >= .78:
        horizontal = faces['open_side'] + faces['other_label_side']
        if candidate['volume'] >= 400 and horizontal >= max(24, .12 * min(faces['floor'], faces['roof'])):
            return 'hangar_or_large_opening'
        return 'open_interior'
    return 'uncertain'


def split_deck_complexes(labels, hull, raw_exterior, bounds, cancel):
    """Partition broad exterior-connected complexes at paired physical deck faces.

    The cut changes labels only. Air passages at the deck remain in the result
    as virtual interfaces. Every derived piece stays unresolved for registration.
    """
    reports = {}
    counts = np.bincount(labels.ravel())
    new_label = len(counts)
    for ident in np.flatnonzero(counts > 100_000):
        if not ident:
            continue
        checkpoint(cancel)
        mask = labels == ident
        if not np.any(mask & raw_exterior):
            continue
        depth, width = mask.any(axis=(0,2)).sum(), mask.any(axis=(0,1)).sum()
        if width < .8*mask.shape[2] or depth < .8*mask.shape[1]:
            continue
        layers = mask.sum(axis=(1,2))
        floor_contacts = (mask[1:] & hull[:-1]).sum(axis=(1,2))
        roof_contacts = (mask[:-1] & hull[1:]).sum(axis=(1,2))
        air_contacts = (mask[:-1] & mask[1:]).sum(axis=(1,2))
        record = {
            'source_label': int(ident), 'source_cells': int(counts[ident]),
            'layers': [{'y': int(y)+bounds.lo[1], 'cells': int(layers[y]),
                        'through_to_next': int(air_contacts[y]) if y<len(air_contacts) else 0,
                        'roof_contact': int(roof_contacts[y]) if y<len(roof_contacts) else 0,
                        'floor_contact': int(floor_contacts[y-1]) if y>0 else 0}
                       for y in np.flatnonzero(layers)],
            'cuts': [], 'parts': [], 'reason': 'insufficient_paired_deck_evidence',
        }
        # A thick, physically occupied deck must occlude most of the profiles
        # on both sides. The residual vertical air is the observed opening.
        for y in range(1,len(layers)-1):
            lower, upper = int(layers[y-1]), int(layers[y+1])
            if min(lower,upper)<1200:
                continue
            below = float(roof_contacts[y-1])/lower
            above = float(floor_contacts[y])/upper
            shared = int(air_contacts[y])
            if min(below,above)<.55 or shared/min(lower,upper)>.45:
                continue
            # Keep the narrow passage on the lower side; split at its upper face.
            cut=y+1
            if record['cuts'] and cut+bounds.lo[1]-record['cuts'][-1]['cut_y']<4:
                continue
            points = np.argwhere(mask[y] & mask[y+1])
            record['cuts'].append({
                'cut_y': cut+bounds.lo[1], 'roof_contact_below': round(below,4),
                'floor_contact_above': round(above,4), 'air_faces': shared,
                'reason': 'paired_physical_deck_faces',
                'sample_xyz': [[int(x)+bounds.lo[0], y+bounds.lo[1], int(z)+bounds.lo[2]]
                               for z,x in points[::max(1,(len(points)+63)//64)]],
            })
        reports[int(ident)] = record
        if not record['cuts']:
            continue
        limits = [0]+[c['cut_y']-bounds.lo[1] for c in record['cuts']]+[len(layers)]
        record['reason'] = 'deck_groups_with_unresolved_room_identity'
        for lo,hi in zip(limits,limits[1:]):
            plane = labels[lo:hi]
            cells = mask[lo:hi]
            count = int(cells.sum())
            if not count:
                continue
            plane[cells] = new_label
            record['parts'].append({'label':new_label,'cells':count,
                                    'y_lo':lo+bounds.lo[1],'y_hi':hi-1+bounds.lo[1]})
            new_label += 1
    return reports
