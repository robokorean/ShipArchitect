"""Exact integer orthographic planes; raster sampling changes display only."""
import math
import numpy as np
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QPainter,QPen,QColor,QImage
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QSpinBox,QLabel,QCheckBox
from .model import Bounds
from .display import overlay_indices_visible,visible_space_runs
from .space_display import visible as space_visible,alpha as space_alpha,candidate_spaces,color as display_color
from .views import color,BACKGROUND

VIEWS={'평면도 (위)':(0,2,1,False),'정면도':(0,1,2,False),'후면도':(0,1,2,True),'좌측면도':(2,1,0,True),'우측면도':(2,1,0,False)}
def sampled_space_mask(space,f,index,step,shape):
    out=np.zeros(shape,bool)
    for y,z,a,b in space['runs']:
        if f==1:
            if (index is not None and y!=index) or z%step:continue
            row=z//step;lo=(a+step-1)//step;hi=b//step+1
        elif f==2:
            if (index is not None and z!=index) or y%step:continue
            row=y//step;lo=(a+step-1)//step;hi=b//step+1
        else:
            if (index is not None and not a<=index<=b) or y%step or z%step:continue
            row=y//step;lo=z//step;hi=lo+1
        if row<out.shape[0]:out[row,max(0,lo):min(out.shape[1],hi)]=True
    return out

class PlaneCanvas(QWidget):
    def __init__(self,panel):
        super().__init__();self.panel=panel;self.drag=None;self.end=None;self.setMinimumSize(250,220);self.setMouseTracking(True)
    def mapping(self):
        p=self.panel;u,v,f,flip=VIEWS[p.orientation.currentText()];size=p.owner.doc.schematic.size
        self.scale=min((self.width()-30)/size[u],(self.height()-45)/size[v]);self.ox=(self.width()-size[u]*self.scale)/2;self.oy=(self.height()-size[v]*self.scale)/2+10
        return u,v,f,flip,size
    def coord(self,point):
        u,v,f,flip,size=self.mapping();U=math.floor((point.x()-self.ox)/self.scale);V=math.floor((point.y()-self.oy)/self.scale)
        if not(0<=U<size[u] and 0<=V<size[v]):return None
        if flip:U=size[u]-1-U
        if v==1:V=size[v]-1-V
        xyz=[0,0,0];xyz[u]=U;xyz[v]=V;xyz[f]=self.panel.slice.value()
        if self.panel.projection.isChecked():return self.panel.ray_pick(xyz)
        return tuple(xyz)
    def point(self,xyz):
        u,v,f,flip,size=self.mapping();U=xyz[u];V=xyz[v]
        if flip:U=size[u]-1-U
        if v==1:V=size[v]-1-V
        return QPointF(self.ox+(U+.5)*self.scale,self.oy+(V+.5)*self.scale)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),BACKGROUND)
        if self.panel.owner.doc is None:p.end();return
        u,v,f,flip,size=self.mapping();image=self.panel.image
        if image is not None:p.drawImage(QRectF(self.ox,self.oy,size[u]*self.scale,size[v]*self.scale),image)
        owner=self.panel.owner
        for s in owner.doc.spaces:
            if not space_visible(owner.view,s):continue
            low=s['runs'][0] if s['runs'] else None
            # Exact overlay mask is built into the plane image; no permission bbox approximation.
        panel=getattr(owner,'structure_review',None)
        if panel is not None:
            overlay=panel.overlay_image(u,v,f,self.panel.slice.value(),self.panel.projection.isChecked())
            if overlay is not None:
                if flip or v==1:overlay=overlay.mirrored(flip,v==1)
                p.drawImage(QRectF(self.ox,self.oy,size[u]*self.scale,size[v]*self.scale),overlay)
        selected=getattr(getattr(owner,'workbench',None),'selected_xyz',None)
        if selected and selected[f]==self.panel.slice.value():p.setPen(QPen(QColor('#ffdf76'),2));p.drawEllipse(self.point(selected),6,6)
        path=getattr(owner.view,'route_path',[]);p.setPen(QPen(QColor('#ffda65'),3))
        if getattr(owner.view,'show_routes',True):
            for a,b in zip(path,path[1:]):
                if min(a[f],b[f])<=self.panel.slice.value()<=max(a[f],b[f]):p.drawLine(self.point(a),self.point(b))
        if hasattr(owner,'workbench'):owner.workbench.draw_axis(self,p)
        if self.drag is not None and self.end is not None:
            p.setPen(QPen(QColor('#ffffff'),2));p.drawRect(QRectF(self.point(self.drag),self.point(self.end)).normalized())
        p.setPen(QColor('#b6cedd'));p.drawText(12,22,f'{self.panel.orientation.currentText()} · {"XYZ"[f]}={self.panel.slice.value()} · 정확한 셀 좌표 편집');p.end()
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton:self.drag=self.coord(e.position());self.end=self.drag
    def mouseMoveEvent(self,e):
        xyz=self.coord(e.position())
        if xyz:self.panel.status.setText('X/Y/Z = '+str(xyz))
        if self.drag is not None:self.end=xyz;self.update()
    def mouseReleaseEvent(self,e):
        start=self.drag;end=self.coord(e.position());self.drag=None;self.end=None
        if start is None or end is None:return
        owner=self.panel.owner;mode=owner.space_manager.edit_mode.currentData()
        if self.panel.projection.isChecked() and mode!='select':self.panel.status.setText('투영은 선택/검사용입니다. 경계 편집은 투영을 끄고 단면 좌표를 지정하세요.');return
        if mode=='select':
            if start==end:
                if owner.view.display_state.contains_xyz(end,owner.doc,owner.view):owner.picked(end)
                if not self.panel.inspect.isChecked():owner.space_manager.select_at(end)
            else:owner.set_selection(Bounds.between(start,end))
        else:owner.space_manager.edit_bounds(Bounds.between(start,end),mode)
        self.panel.refresh()

class AxisPlan(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.image=None;lay=QVBoxLayout(self);row=QHBoxLayout();self.orientation=QComboBox();self.orientation.addItems(VIEWS);row.addWidget(self.orientation)
        self.slice=QSpinBox();row.addWidget(QLabel('단면 좌표'));row.addWidget(self.slice);self.inspect=QCheckBox('블록 검사');row.addWidget(self.inspect);self.projection=QCheckBox('앞면 투영');self.projection.toggled.connect(self.refresh);row.addWidget(self.projection);lay.addLayout(row);self.canvas=PlaneCanvas(self);lay.addWidget(self.canvas,1);self.status=QLabel('공간 선택 후 실제 바닥/층으로 이동. 경계 도구는 공간 관리와 공유합니다.');self.status.setWordWrap(True);lay.addWidget(self.status)
        self.orientation.currentIndexChanged.connect(self.orientation_changed);self.slice.valueChanged.connect(self.refresh)
    def orientation_changed(self,*_):
        if self.owner.doc is None:return
        u,v,f,flip=VIEWS[self.orientation.currentText()];self.slice.blockSignals(True);self.slice.setRange(0,self.owner.doc.schematic.size[f]-1);self.slice.setValue(self.owner.doc.selection.lo[f]);self.slice.blockSignals(False);self.refresh()
    def focus_space(self):
        if self.owner.doc is None:return
        u,v,f,flip=VIEWS[self.orientation.currentText()];value=self.owner.layer.value() if f==1 else self.owner.doc.selection.lo[f];self.slice.setRange(0,self.owner.doc.schematic.size[f]-1);self.slice.setValue(value);self.refresh()
    def ray_pick(self,xyz):
        doc=self.owner.doc;size=doc.schematic.size;u,v,f,flip=VIEWS[self.orientation.currentText()];depth=np.arange(size[f],dtype=np.int64);coords=[np.full(len(depth),int(n),np.int64) for n in xyz];coords[f]=depth;x,y,z=coords;ii=(y*size[2]+z)*size[0]+x;values=self.owner.view.blocks[y,z,x]
        valid=self.owner.view.display_state.visible_indices(ii,doc,self.owner.view)&doc.schematic.solid(values)
        hits=np.flatnonzero(valid)
        if not len(hits):return None
        reverse=self.orientation.currentText() in ('평면도 (위)','후면도','우측면도');chosen=int(hits[-1] if reverse else hits[0]);point=list(xyz);point[f]=chosen;return tuple(point)
    def refresh(self,*_):
        if self.owner.doc is None or not hasattr(self.owner.view,'display_state'):return
        doc=self.owner.doc;sc=doc.schematic;u,v,f,flip=VIEWS[self.orientation.currentText()];size=sc.size;index=min(self.slice.value(),size[f]-1)
        # Bounded 2D samples; editing maps back to original integer XYZ, never sampled indices.
        step=max(1,math.ceil(max(size[u],size[v])/1200));U=np.arange(0,size[u],step);V=np.arange(0,size[v],step);uu,vv=np.meshgrid(U,V);xyz=[np.zeros(uu.shape,np.int64) for _ in range(3)];xyz[u]=uu;xyz[v]=vv;xyz[f][:]=index
        x,y,z=xyz;ii=(y*size[2]+z)*size[0]+x;a=self.owner.view.blocks[y,z,x];rgba=np.zeros((*a.shape,4),np.uint8);rgba[:]=[16,26,40,255]
        palette=np.zeros((max(sc.palette)+1,4),np.uint8)
        for i,state in sc.palette.items():
            c=color(state);palette[i]=[c.red(),c.green(),c.blue(),255]
        if self.projection.isChecked():
            if sc.blocks.size>8_000_000:self.status.setText('앞면 투영은 8백만 셀 이하 문서에서 사용하세요. 큰 문서는 단면을 사용하세요.');self.projection.blockSignals(True);self.projection.setChecked(False);self.projection.blockSignals(False);self.refresh();return
            filled=np.zeros(a.shape,bool);reverse=self.orientation.currentText() in ('평면도 (위)','후면도','우측면도');depths=range(size[f]-1,-1,-1) if reverse else range(size[f])
            if self.owner.view.show_blocks:
                for depth in depths:
                    xyz[f][:]=depth;x,y,z=xyz;ii=(y*size[2]+z)*size[0]+x;values=self.owner.view.blocks[y,z,x]
                    visible=self.owner.view.display_state.visible_indices(ii.ravel(),doc,self.owner.view).reshape(a.shape)
                    keep=(~filled)&visible&sc.solid(values);rgba[keep]=palette[values[keep]];filled|=keep
                    if filled.all():break
        else:
            visible=self.owner.view.display_state.visible_indices(ii.ravel(),doc,self.owner.view).reshape(a.shape)
            if self.owner.view.show_blocks:
                keep=visible&sc.solid(a);rgba[keep]=palette[a[keep]]
        if self.owner.view.show_spaces or candidate_spaces(self.owner.view):
            from .space_model import contains
            for s in (doc.spaces if self.owner.view.show_spaces else [])+candidate_spaces(self.owner.view):
                if not space_visible(self.owner.view,s,s.get('_candidate',False)):continue
                if self.owner.view.exact_space_only and s['id']!=doc.active_space_id:continue
                inside=sampled_space_mask({'runs':visible_space_runs(s,doc,self.owner.view)},f,None if self.projection.isChecked() else index,step,a.shape)
                if not self.projection.isChecked():
                    xyz[f][:]=index;x,y,z=xyz;overlay_ii=(y*size[2]+z)*size[0]+x;inside &= overlay_indices_visible(overlay_ii,doc,self.owner.view)
                c=QColor(display_color(s));alpha=space_alpha(self.owner.view,s,s.get('_candidate',False))/255
                rgba[inside,:3]=(rgba[inside,:3]*(1-alpha)+np.array([c.red(),c.green(),c.blue()])*alpha).astype(np.uint8)
        if v==1:rgba=rgba[::-1]
        if flip:rgba=rgba[:,::-1]
        rgba=np.ascontiguousarray(rgba);self.image=QImage(rgba.data,rgba.shape[1],rgba.shape[0],rgba.strides[0],QImage.Format.Format_RGBA8888).copy();self.canvas.update()
