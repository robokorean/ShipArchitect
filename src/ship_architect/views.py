"""Orthographic software 3D renderer. Greedy surface rectangles, no OpenGL dependency."""
from __future__ import annotations
import hashlib
import math
import time,uuid
from .diagnostics import event as diagnostic_event
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer, QSettings
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QImage
from PySide6.QtWidgets import QWidget
from .chunks import checkpoint
from .mesh import MeshScene, MeshCache, build_scene
from .model import Bounds
from .workers import Task
from .render import render_scene

BACKGROUND = QColor('#101a28')


def color(state):
    if not state.startswith('minecraft:'):
        digest = hashlib.sha256(state.encode()).digest()
        return QColor(157 + digest[0] % 55, 100 + digest[1] % 55, 200)
    for name, rgb in [('lime', '#a5c969'), ('white', '#d7e0e5'), ('gray', '#677a8b'),
                      ('glass', '#71cbd4'), ('oak', '#be9a67'), ('iron', '#aab9c9'),
                      ('stone_bricks', '#9a9d9f'), ('stone', '#8293a5'), ('andesite', '#7b8c97'), ('dirt', '#98785d')]:
        if name in state:
            return QColor(rgb)
    digest = hashlib.sha256(state.encode()).digest()
    return QColor(*(100 + c % 110 for c in digest[:3]))


def rectangles(plane):
    """Merge equal material runs across adjacent rows; -1 is no face."""
    active = {}
    for v in range(plane.shape[1] + 1):
        runs = {}
        if v < plane.shape[1]:
            row = plane[:, v]
            edges = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
            for a, b in zip(edges[:-1], edges[1:]):
                material = int(row[a])
                if material >= 0:
                    key = (int(a), int(b), material)
                    runs[key] = active.pop(key, v)
        for (a, b, material), start in active.items():
            yield a, b, start, v, material
        active = runs


def surface_mesh(blocks, solid, limit=60_000, halo=None):
    """Return (quad, normal, material) in XYZ. Hard limit is explicit, no sampling."""
    a = blocks.transpose(2, 0, 1)  # XYZ
    occupied = solid.transpose(2, 0, 1)
    result = []
    for axis in range(3):
        uv = [i for i in range(3) if i != axis]
        for sign in (-1, 1):
            neighbor = np.zeros_like(occupied)
            dst = [slice(None)] * 3
            src = [slice(None)] * 3
            dst[axis] = slice(0, -1) if sign == 1 else slice(1, None)
            src[axis] = slice(1, None) if sign == 1 else slice(0, -1)
            neighbor[tuple(dst)] = occupied[tuple(src)]
            if halo is not None:
                window=[slice(1,-1)]*3
                window[axis]=slice(2,None) if sign==1 else slice(None,-2)
                neighbor=halo[tuple(window)]
            exposed = occupied & ~neighbor
            normal = np.zeros(3)
            normal[axis] = sign
            for level in range(a.shape[axis]):
                index = [slice(None)] * 3
                index[axis] = level
                exposed_plane = exposed[tuple(index)]
                if not np.any(exposed_plane):
                    continue
                plane = np.where(exposed_plane, a[tuple(index)], -1)
                for u0, u1, v0, v1, material in rectangles(plane):
                    quad = np.zeros((4, 3), dtype=float)
                    quad[:, axis] = level + (sign > 0)
                    quad[:, uv[0]] = [u0, u1, u1, u0]
                    quad[:, uv[1]] = [v0, v0, v1, v1]
                    result.append((quad, normal.copy(), material))
                    if len(result) > limit:
                        raise ValueError('표면이 60,000개를 초과했습니다. Y 단면에서 구역을 선택한 후 3D 구역 한정을 켜세요.')
    return result


def rasterize_quads(faces, width, height, cancel=None):
    """Exact orthographic per-pixel depth and face-ID buffers for opaque voxel faces.

    faces: (screen quad, camera depths, RGB tuple, mesh face index).
    A projected axis-aligned world face is a parallelogram: interpolate its
    two coordinates rather than sorting faces by their average distance.
    """
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    depth_buffer = np.full((height, width), -np.inf, dtype=np.float32)
    pick_buffer = np.full((height, width), -1, dtype=np.int32)
    for face_no,(screen, depths, rgb, face_id) in enumerate(faces):
        if face_no%64==0: checkpoint(cancel)
        low = np.maximum(np.floor(screen.min(axis=0)).astype(int), [0, 0])
        high = np.minimum(np.ceil(screen.max(axis=0)).astype(int), [width, height])
        x0, y0 = low
        x1, y1 = high
        if x1 <= x0 or y1 <= y0:
            continue
        edges = np.stack([screen[1]-screen[0], screen[3]-screen[0]], axis=1)
        if abs(np.linalg.det(edges)) < 1e-8:
            continue
        inverse = np.linalg.inv(edges)
        dx = np.arange(x0, x1)[None, :] + .5 - screen[0, 0]
        dy = np.arange(y0, y1)[:, None] + .5 - screen[0, 1]
        u = inverse[0, 0]*dx + inverse[0, 1]*dy
        v = inverse[1, 0]*dx + inverse[1, 1]*dy
        depth = depths[0] + u*(depths[1]-depths[0]) + v*(depths[3]-depths[0])
        old_depth = depth_buffer[y0:y1, x0:x1]
        visible = (u >= -1e-7) & (u <= 1+1e-7) & (v >= -1e-7) & (v <= 1+1e-7) & (depth > old_depth)
        old_depth[visible] = depth[visible]
        rgba[y0:y1, x0:x1][visible] = (*rgb, 255)
        pick_buffer[y0:y1, x0:x1][visible] = face_id
    return rgba, pick_buffer


class View3D(QWidget):
    space_picked = Signal(str)
    picked = Signal(object, bool)
    mesh_progress = Signal(int,str)
    mesh_ready = Signal()
    selected = Signal(object,object)
    selection_finished = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(350, 320)
        self.doc = None
        self.blocks = None
        self.mesh = MeshScene()
        self.hits = []
        self.pick_buffer = None
        self.yaw = .72
        self.pitch = .55
        self.focus_bounds=None
        self.zoom = 1.
        self.pan = np.zeros(2)
        self.layer = 0
        self.cut = False
        self.single_layer = False
        self.zone_only = False
        self.pending = False
        self.preview_side = '변경 후'
        self.selection_mode = False
        self.drag_corner = None
        self.error = ''
        self.start = None
        self.invert_x = QSettings('ShipArchitect','ShipArchitect').value('camera/invert_x',False,type=bool)
        self.mesh_worker=None
        self.render_worker=None
        self.mesh_serial=0
        self.render_serial=0
        self.cache=MeshCache()
        self.mesh_request=None
        self.render_request=None
        self.frame=None
        self.show_blocks=True;self.show_spaces=True;self.exact_space_only=False;self.ghost_floors=False
        self.visible_y=None;self.floor_range=None;self.space_hits=[]
        self.render_factor=1.
        self.render_stats={}
        self.interactive_quads=np.empty((0,4,3),np.float32);self.interactive_materials=[];self.interactive_frames=0;self.interaction_active=False
        self.mesh_busy=False
        self.mesh_key=None;self.interaction_renders=0;self.render_generation=0;self.frame_camera=None
        self.render_busy=False
        self.render_timer=QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._request_render)
        self.setToolTip('왼쪽 드래그: 회전 / 오른쪽 드래그: 이동 / 휠: 확대\n클릭: 좌표 확인 / Shift+클릭 두 번: 구역 양 끝점')

    def set_document(self, doc):
        self.doc = doc
        self.blocks = doc.schematic.blocks
        self.mesh=MeshScene();self.mesh_key=None;self.frame=None;self.frame_camera=None;self.validation_spaces=[]
        self.invalidate_data()
        self.reset_camera()
        self.rebuild()

    def set_invert_x(self, enabled):
        self.invert_x=bool(enabled)
        QSettings('ShipArchitect','ShipArchitect').setValue('camera/invert_x',self.invert_x)

    def invalidate_data(self,changed_indices=None):
        # Running jobs retain their own old cache; a new data revision gets a new cache.
        self.cache=self.cache.fork_invalidated(changed_indices,self.doc.schematic.size) if changed_indices is not None else MeshCache()
        self.pick_buffer=None

    def focus_selection(self):
        if self.doc is None:return
        self.focus_bounds=self.doc.selection
        self.zoom=1.;self.pan[:]=0
        self.schedule_render()

    def reset_camera(self):
        self.focus_bounds=None
        self.yaw, self.pitch, self.zoom = .72, .55, 1.
        self.pan[:] = 0
        self.schedule_render()

    def rebuild(self):
        if not self.doc: return
        self.error=''
        lo=[0,0,0];hi=[v-1 for v in self.doc.schematic.size]
        if self.zone_only:
            lo=list(self.doc.selection.lo);hi=list(self.doc.selection.hi)
        if self.cut: hi[1]=min(hi[1],self.layer)
        if self.single_layer:
            lo[1]=max(lo[1],self.layer);hi[1]=min(hi[1],self.layer)
        bounds=None if hi[1]<lo[1] else Bounds(tuple(lo),tuple(hi))
        display=getattr(self,'display_state',None)
        if display is not None:lo,hi=display.clip_bounds(lo,hi,self.doc,self.camera())
        bounds=None if any(b<a for a,b in zip(lo,hi)) else Bounds(tuple(lo),tuple(hi))
        space=self.doc.active_space() if self.exact_space_only else None
        from .space_model import token
        key=(id(self.blocks),id(self.cache),bounds,token(space) if space is not None else None,tuple(self.visible_y) if self.visible_y is not None else None,self.floor_range if self.ghost_floors else None,display.key() if display else None)
        if key==self.mesh_key:
            self.update();return
        self.mesh_key=key;self.mesh_serial+=1;self.render_generation+=1;self.render_serial+=1;self.render_request=None;self.render_timer.stop();self.pick_buffer=None
        if self.render_worker and self.render_worker.isRunning():self.render_worker.requestInterruption()
        diagnostic_event('render_display_update',generation=self.render_generation,frame_retained=self.frame is not None,mesh_faces=len(self.mesh),reason='geometry mask/data changed')
        self.mesh_request=(self.mesh_serial,self.doc.schematic,self.blocks,bounds,self.cache,space,self.visible_y,self.floor_range if self.ghost_floors else None,display)
        if self.mesh_worker is not None:
            self.mesh_worker.requestInterruption()
            return
        self._start_mesh()

    def _start_mesh(self):
        if not self.mesh_request: return
        serial,schematic,blocks,bounds,cache,space,visible_y,floor_range,display=self.mesh_request
        self.mesh_request=None
        self.mesh_busy=True
        def build(progress,cancel):
            if bounds is None:return MeshScene()
            if floor_range is not None:
                lo=list(bounds.lo);hi=list(bounds.hi);lo[1]=max(lo[1],floor_range[0]);hi[1]=min(hi[1],floor_range[1])
                scene=MeshScene() if hi[1]<lo[1] else build_scene(schematic,blocks,Bounds(tuple(lo),tuple(hi)),cache,progress,cancel,space,visible_y,display)
                others=[y for y in range(bounds.lo[1],bounds.hi[1]+1) if not floor_range[0]<=y<=floor_range[1] and (visible_y is None or y in visible_y)]
                scene.context_scene=build_scene(schematic,blocks,bounds,cache,progress,cancel,space,others,display)
                if scene.stats.get('bytes',0)+scene.context_scene.stats.get('bytes',0)>192*1024**2:raise ValueError('층 반투명 메시 예산 초과. 표시 층을 줄이세요.')
                return scene
            return build_scene(schematic,blocks,bounds,cache,progress,cancel,space,visible_y,display)
        if blocks.size<=100_000:
            self._accept_mesh(build(None,None),serial)
            self.mesh_busy=False
            return
        job=Task(build,self)
        self.mesh_worker=job
        job.progress.connect(self.mesh_progress.emit)
        job.result.connect(lambda scene:self._accept_mesh(scene,serial))
        job.failed.connect(lambda error:self._mesh_error(error,serial))
        job.finished.connect(self._mesh_finished)
        job.finished.connect(job.deleteLater)
        job.start()
        self.update()

    def _accept_mesh(self,scene,serial):
        if serial!=self.mesh_serial:return
        self.mesh=scene
        # Interaction and precise frames share this exact scene. No sampled substitute model.
        self.interactive_quads=np.empty((0,4,3),np.float32)
        diagnostic_event('mesh_ready',faces=len(scene),stats=scene.stats)
        self.hits=[]
        self.mesh_busy=False
        self.mesh_ready.emit()
        self.schedule_render()

    def _mesh_error(self,error,serial):
        if serial==self.mesh_serial:
            diagnostic_event('render_error',stage='mesh',generation=serial,error=error)
            self.error=error
            self.mesh_key=None
            self.mesh=MeshScene()
            self.mesh_busy=False
            self.mesh_ready.emit()
            self.update()

    def _mesh_finished(self):
        self.mesh_worker=None
        self.mesh_busy=False
        if self.mesh_request:self._start_mesh()
        else:self.mesh_ready.emit()
        self.update()

    def schedule_render(self):
        self.render_serial+=1
        self.pick_buffer=None
        if not self.render_timer.isActive():self.render_timer.start(18)
        self.update()

    def _request_render(self):
        if not self.doc or self.mesh_busy:return
        toward=self.camera()
        factor=min(1.,math.sqrt(650_000/max(1,self.width()*self.height())))
        if self.interaction_active:factor=min(factor,math.sqrt(160_000/max(1,self.width()*self.height())))
        self.render_request=(self.render_serial,self.mesh,self.center.copy(),self.basis.copy(),
            self.origin.copy(),self.scale,toward,self.width(),self.height(),factor)
        if self.render_worker is not None:return
        self._start_render()

    def _start_render(self):
        if not self.render_request:return
        serial,scene,center,basis,origin,scale,toward,w,h,factor=self.render_request
        self.render_request=None
        self.render_busy=True
        colors={}
        for material in self.doc.schematic.palette:
            c=color(self.doc.schematic.palette[material])
            for axis in range(3):
                for sign in (-1,1):
                    normal=[0,0,0];normal[axis]=sign
                    shade=.66+.28*max(0,np.dot(normal,[.3,.85,.43]))
                    colors[(material,tuple(normal))]=(int(c.red()*shade),int(c.green()*shade),int(c.blue()*shade))
        generation=self.render_generation;interactive=self.interaction_active;trace_id=uuid.uuid4().hex
        def render(progress,cancel):
            rgba,picks,stats=render_scene(scene,colors,center,basis,origin,scale,toward,w,h,factor,cancel)
            if hasattr(scene,'context_scene'):
                ghost,_,_=render_scene(scene.context_scene,colors,center,basis,origin,scale,toward,w,h,factor,cancel)
                background=np.array([16,26,40],np.float32)
                faded=(ghost[:,:,:3].astype(np.float32)*.22+background*.78).astype(np.uint8)
                rgba[:,:,:3]=np.where((picks>=0)[:,:,None],rgba[:,:,:3],faded)
                rgba[:,:,3]=255
            stats.update(trace_id=trace_id,generation=generation,interaction=interactive,scene_faces=len(scene),camera={'center':center.tolist(),'basis':basis.tolist(),'origin':origin.tolist(),'scale':float(scale),'toward':toward.tolist()})
            return rgba,picks,stats
        if len(scene)<1500 and self.blocks.size<=100_000:
            self._accept_render(render(None,None),serial,scene)
            self.render_busy=False
            return
        job=Task(render,self);job.trace_id=trace_id
        self.render_worker=job
        job.result.connect(lambda result:self._accept_render(result,serial,scene))
        job.failed.connect(lambda error:self._render_error(error,serial))
        job.finished.connect(self._render_finished)
        job.finished.connect(job.deleteLater)
        job.start()
        self.update()

    def _accept_render(self,result,serial,scene):
        rgba,picks,stats=result
        moving=self.interaction_active and stats.get('interaction',False)
        if stats.get('generation',self.render_generation)!=self.render_generation:return
        if serial!=self.render_serial and not moving:return
        self.frame=QImage(rgba.data,rgba.shape[1],rgba.shape[0],rgba.strides[0],QImage.Format.Format_RGBA8888).copy()
        self.pick_buffer=None if moving else picks
        if moving:self.interaction_renders+=1
        self.render_factor=stats['factor']
        self.render_stats=stats
        self.frame_camera=stats.get('camera')
        if moving or self.render_generation!=getattr(self,'_logged_generation',None):
            diagnostic_event('render_frame',mode='interaction_full_mesh' if moving else 'precise',**stats);self._logged_generation=self.render_generation
        self.render_busy=False
        # Retain the exact scene used by the pick buffer.
        self.pick_scene=scene
        self.hits=[]
        if len(scene)<1500:
            toward=self.camera()
            self.hits=[(self.polygon(q),q,n) for q,n,i in scene if n@toward>1e-6]
        self.update()

    def _render_error(self,error,serial):
        if serial==self.render_serial:
            diagnostic_event('render_error',stage='raster',generation=serial,error=error)
            self.error=error
            self.render_busy=False
            self.update()

    def _render_finished(self):
        self.render_worker=None
        self.render_busy=False
        if self.render_request:self._start_render()
        elif self.interaction_active:self.render_timer.start(12)

    def resizeEvent(self,event):
        self.schedule_render()
        super().resizeEvent(event)

    def cancel_jobs(self):
        self.interaction_active=False
        self.mesh_serial+=1;self.render_serial+=1
        self.mesh_request=self.render_request=None
        self.render_timer.stop()
        self.mesh_busy=self.mesh_worker is not None
        self.render_busy=self.render_worker is not None
        for job in (self.mesh_worker,self.render_worker):
            if job and job.isRunning():job.requestInterruption()

    def workers_running(self):
        return bool(self.mesh_busy or self.render_busy or self.mesh_request is not None or self.render_request is not None
            or self.mesh_worker is not None or self.render_worker is not None)

    def camera(self):
        if getattr(self,'painting_frame',False):return self.frame_toward
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        right = np.array([cy, 0, -sy])
        up = np.array([-sy*sp, cp, -cy*sp])
        toward = np.array([sy*cp, sp, cy*cp])
        size = np.array(self.doc.schematic.size)
        self.center = size / 2
        if self.focus_bounds is not None:
            lo=np.array(self.focus_bounds.lo);hi=np.array(self.focus_bounds.hi)+1
            self.center=(lo+hi)/2;size=hi-lo
        self.basis = np.stack([right, -up], axis=1)
        if self.focus_bounds is not None:
            span=np.abs(self.basis).T@size
            self.scale=max(.001,min(max(1,self.width()-72)/max(1,span[0]),max(1,self.height()-100)/max(1,span[1])))*self.zoom
        else:
            self.scale = max(1., min(self.width(), self.height()) / max(np.linalg.norm(size), 1) * 1.4) * self.zoom
        self.origin = np.array([self.width()/2, self.height()/2]) + self.pan
        return toward

    def project(self, points):
        return (np.asarray(points) - self.center) @ self.basis * self.scale + self.origin

    def polygon(self, points):
        return QPolygonF([QPointF(float(x), float(y)) for x, y in self.project(points)])

    def box(self, painter, bounds):
        lo = np.array(bounds.lo)
        hi = np.array(bounds.hi) + 1
        vertices = np.array([[hi[i] if (n >> i) & 1 else lo[i] for i in range(3)] for n in range(8)])
        p = self.project(vertices)
        painter.setPen(QPen(QColor('#f4c873'), 1.7))
        for n in range(8):
            for bit in (1, 2, 4):
                if not n & bit:
                    painter.drawLine(QPointF(*p[n]), QPointF(*p[n | bit]))

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), BACKGROUND)
        if not self.doc:
            p.end()
            return
        self.painting_frame=False
        toward = self.camera()
        if self.frame is not None and self.frame_camera is not None:
            c=self.frame_camera;self.center=np.asarray(c['center']);self.basis=np.asarray(c['basis']);self.origin=np.asarray(c['origin']);self.scale=c['scale'];self.frame_toward=np.asarray(c['toward']);self.painting_frame=True
        w, h, l = self.doc.schematic.size
        p.setPen(QPen(QColor('#263b50'), 1))
        step = max(1, math.ceil(max(w, l)/40))
        grid_y=float(self.layer) if self.selection_mode else 0.
        for x in range(0, w+1, step):
            pts = self.project([[x, grid_y, 0], [x, grid_y, l]])
            p.drawLine(QPointF(*pts[0]), QPointF(*pts[1]))
        for z in range(0, l+1, step):
            pts = self.project([[0, grid_y, z], [w, grid_y, z]])
            p.drawLine(QPointF(*pts[0]), QPointF(*pts[1]))
        if self.show_blocks:
            if self.frame is not None:p.drawImage(QRectF(self.rect()),self.frame)
            if self.interaction_active:self.interactive_frames+=1
        p.setBrush(Qt.BrushStyle.NoBrush)
        self.box(p, self.doc.selection)
        self.space_hits=[]
        if self.show_spaces and not self.interaction_active:
            from .space_ui import draw_spaces_3d
            draw_spaces_3d(self,p)
        from .network_ui import draw_route_3d
        draw_route_3d(self,p)
        workbench=getattr(self,'workbench',None)
        if workbench is not None:workbench.draw_3d(p)
        if getattr(self,'candidate_boxes',[]) and not self.interaction_active:
            from .space_geometry import draw_volumes
            draw_volumes(self,p,candidates=True)
        if getattr(self,'validation_spaces',[]) and not self.interaction_active:
            from .space_geometry import draw_volumes
            draw_volumes(self,p,spaces_override=self.validation_spaces,selectable=False)
        for axis, label, c in [(0, '+X', '#ef978e'), (1, '+Y', '#91d9aa'), (2, '+Z', '#87b8ef')]:
            points = np.zeros((2, 3))
            points[1, axis] = max(w, h, l)*.18
            projected = self.project(points)
            p.setPen(QPen(QColor(c), 2))
            p.drawLine(QPointF(*projected[0]), QPointF(*projected[1]))
            p.drawText(QPointF(*projected[1]), label)
        p.setPen(QColor('#e7eef5'))
        p.drawText(16, 26, '3D  /  ORTHOGRAPHIC     ' + ('미승인 · '+self.preview_side if self.pending else '승인된 설계'))
        p.setPen(QColor('#94a9bd'))
        p.drawText(16, self.height()-18, f'{len(self.mesh):,} 표면 · {self.mesh.stats.get("visible_chunks",0):,} 청크' + (' · 메시 생성 중' if self.mesh_busy else ' · 렌더링 중' if self.render_busy else ''))
        if self.error:
            p.setPen(QColor('#ffba8a'))
            p.drawText(QRectF(20, 45, self.width()-40, 110), Qt.TextFlag.TextWordWrap, self.error)
        p.end();self.painting_frame=False

    def pick(self, point):
        if not self.show_blocks and not self.selection_mode:return None
        if self.interaction_active:return None
        if self.pick_buffer is None: return None
        pixel = np.array([point.x(), point.y()])
        face_id = -1
        px, py = int(point.x()*self.render_factor), int(point.y()*self.render_factor)
        if self.pick_buffer is not None and 0 <= py < self.pick_buffer.shape[0] and 0 <= px < self.pick_buffer.shape[1]:
            face_id = int(self.pick_buffer[py, px]) if self.show_blocks else -1
        if face_id >= 0:
            quad, normal, _ = self.pick_scene[face_id]
            axis = int(np.argmax(np.abs(normal)))
            uv = [i for i in range(3) if i != axis]
            origin = quad[0]
            rhs = (pixel-self.project([origin])[0]) / self.scale
            values = np.linalg.solve(self.basis[uv].T, rhs)
            xyz = origin.copy()
            xyz[uv] += values
            xyz -= normal * 1e-5
            xyz = np.floor(xyz).astype(int)
            return tuple(int(np.clip(xyz[i], 0, self.doc.schematic.size[i]-1)) for i in range(3))
        # Empty projects/air can be selected on the current Y plane.
        origin = np.array([0., float(self.layer), 0.])
        try:
            values = np.linalg.solve(self.basis[[0, 2]].T, (pixel-self.project([origin])[0]) / self.scale)
        except np.linalg.LinAlgError:
            return None
        x, z = np.floor(values).astype(int)
        w, h, l = self.doc.schematic.size
        if not (0<=x<w and 0<=z<l):return None
        xyz=(int(x),self.layer,int(z))
        if hasattr(self,'display_state') and self.doc.schematic.solid(np.array([self.blocks[xyz[1],xyz[2],xyz[0]]]))[0] and not self.display_state.contains_xyz(xyz,self.doc,self):return None
        return xyz

    def adjacent(self,point):
        xyz=self.pick(point)
        if xyz is None:return None
        px,py=int(point.x()*self.render_factor),int(point.y()*self.render_factor)
        if self.pick_buffer is None or not (0<=py<self.pick_buffer.shape[0] and 0<=px<self.pick_buffer.shape[1]):return xyz
        face_id=int(self.pick_buffer[py,px])
        if face_id<0:return xyz
        _,normal,_=self.pick_scene[face_id]
        result=tuple(int(a+b) for a,b in zip(xyz,normal))
        return result if all(0<=v<n for v,n in zip(result,self.doc.schematic.size)) else None

    def mousePressEvent(self, event):
        self.interaction_active=False
        self.last_pick_position=event.position()
        self.start = event.position()
        self.previous = event.position()
        self.moved = False
        if event.button()==Qt.MouseButton.LeftButton and self.selection_mode:
            self.drag_corner=self.pick(event.position())

    def mouseMoveEvent(self, event):
        if self.start is None:
            return
        delta = event.position() - self.previous
        if self.selection_mode and event.buttons() & Qt.MouseButton.LeftButton:
            if self.drag_corner is not None:
                end=self.pick(event.position())
                if end is not None:self.selected.emit(self.drag_corner,end)
            self.previous=event.position()
            return
        if (event.position()-self.start).manhattanLength() > 4:
            self.moved = True
        if event.buttons() & Qt.MouseButton.RightButton:
            self.pan += [delta.x(), delta.y()]
        elif event.buttons() & Qt.MouseButton.LeftButton and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.yaw += delta.x()*.008*(1 if self.invert_x else -1)
            self.pitch = float(np.clip(self.pitch+delta.y()*.008, -1.5, 1.5))
        self.interaction_active=True
        self.previous = event.position()
        self.schedule_render()

    def mouseReleaseEvent(self, event):
        self.interaction_active=False
        self.last_pick_position=event.position() if not self.moved else None
        clicked=self.pick(event.position()) if self.doc and not self.moved and event.button()==Qt.MouseButton.LeftButton else None
        if self.selection_mode and self.drag_corner is not None and event.button()==Qt.MouseButton.LeftButton:
            end=self.pick(event.position())
            if end is not None:self.selected.emit(self.drag_corner,end)
            self.drag_corner=None
            self.selection_finished.emit()
        if self.doc and not self.moved and event.button() == Qt.MouseButton.LeftButton:
            if not self.selection_mode and not event.modifiers() & (Qt.KeyboardModifier.ShiftModifier|Qt.KeyboardModifier.ControlModifier):
                for sid,polygon in reversed(self.space_hits):
                    if polygon.containsPoint(event.position(),Qt.FillRule.OddEvenFill):
                        self.space_picked.emit(sid);self.start=None;return
            xyz = clicked
            if xyz is not None:
                self.picked.emit(xyz, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        self.start = None
        if self.moved:
            if getattr(getattr(self,'display_state',None),'occluders',False):self.rebuild()
            else:self.schedule_render()

    def wheelEvent(self, event):
        self.zoom = float(np.clip(self.zoom * (1.15 if event.angleDelta().y() > 0 else 1/1.15), .1, 30))
        self.schedule_render()


class LayerView(QWidget):
    selected = Signal(object, object)
    picked = Signal(object, bool)

    def __init__(self):
        super().__init__()
        self.doc = None
        self.blocks = None
        self.layer = 0
        self.drag = None
        self.image = None
        self.setMinimumSize(350, 320)
        self.setToolTip('드래그: 현재 Y층의 X/Z 사각 구역 선택. 빈 공간도 선택할 수 있습니다.')

    def refresh(self):
        if self.doc:
            step=max(1,math.ceil(max(self.blocks.shape[1:])/1536))
            a = self.blocks[self.layer,::step,::step]
            rgb = np.zeros((*a.shape, 3), dtype=np.uint8)
            rgb[:] = [19, 31, 46]
            for i in np.unique(a):
                state = self.doc.schematic.palette[int(i)]
                if state.split('[')[0] in {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}:
                    continue
                c = color(state)
                rgb[a == i] = [c.red(), c.green(), c.blue()]
            self.image = QImage(rgb.data, a.shape[1], a.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.update()

    def layout_map(self):
        w, h, l = self.doc.schematic.size
        self.scale = min((self.width()-50)/w, (self.height()-75)/l)
        self.ox = (self.width()-w*self.scale)/2
        self.oz = (self.height()-l*self.scale)/2+10

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), BACKGROUND)
        if not self.doc:
            p.end()
            return
        self.layout_map()
        w, h, l = self.doc.schematic.size
        p.drawImage(QRectF(self.ox, self.oz, w*self.scale, l*self.scale), self.image)
        if self.scale > 8:
            p.setPen(QPen(QColor('#304357'), .5))
            for x in range(w+1):
                p.drawLine(QPointF(self.ox+x*self.scale, self.oz), QPointF(self.ox+x*self.scale, self.oz+l*self.scale))
            for z in range(l+1):
                p.drawLine(QPointF(self.ox, self.oz+z*self.scale), QPointF(self.ox+w*self.scale, self.oz+z*self.scale))
        b = self.doc.selection
        if b.lo[1] <= self.layer <= b.hi[1]:
            p.setPen(QPen(QColor('#f4c873'), 2))
            p.setBrush(QColor(244, 200, 115, 35))
            p.drawRect(QRectF(self.ox+b.lo[0]*self.scale, self.oz+b.lo[2]*self.scale,
                (b.hi[0]-b.lo[0]+1)*self.scale, (b.hi[2]-b.lo[2]+1)*self.scale))
        p.setPen(QColor('#c2d1e0'))
        p.drawText(18, 25, f'Y={self.layer}  /  +X →  +Z ↓    드래그로 구역 선택')
        p.end()

    def coord(self, point):
        self.layout_map()
        x = math.floor((point.x()-self.ox)/self.scale)
        z = math.floor((point.y()-self.oz)/self.scale)
        w, h, l = self.doc.schematic.size
        return (x, self.layer, z) if 0 <= x < w and 0 <= z < l else None

    def mousePressEvent(self, event):
        if self.doc and event.button() == Qt.MouseButton.LeftButton:
            self.drag = self.coord(event.position())
            if self.drag:
                self.picked.emit(self.drag, False)

    def mouseMoveEvent(self, event):
        if self.drag:
            end = self.coord(event.position())
            if end:
                self.selected.emit(self.drag, end)

    def mouseReleaseEvent(self, event):
        if self.drag:
            end = self.coord(event.position())
            if end:
                self.selected.emit(self.drag, end)
        self.drag = None
