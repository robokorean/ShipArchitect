"""One review panel and shared 2D / 3D evidence overlays."""
from collections import OrderedDict
from copy import deepcopy
import json

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .analysis_state import commit
from .axis_plan import sampled_space_mask
from .display import visible_space_runs
from .model import Bounds
from .space_display import external
from .structure_review import LAYERS

ROLE = Qt.ItemDataRole.UserRole
AIR = {'external_air', 'interior'}
DASHED = {'virtual', 'interior'}
LABELS = {
    'closed_interior':'폐쇄형 내부','open_interior':'개방형 내부',
    'partial_partition':'부분 구획','unpartitioned_interior':'미구획 내부',
    'exposed_facility':'외부·노출 시설','exterior_air':'외부 공기','uncertain':'미확정',
}


class StructureReview(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.identity = None
        self.review = {}
        self._face_cache = OrderedDict()
        self._plane_cache = OrderedDict()
        self.visible = set()
        layout = QVBoxLayout(self)
        commands = QHBoxLayout()
        for label, callback in [('신규 구조 분석', self.scan), ('공통 도면', lambda: owner.integrated_ui.open_drawing())]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            commands.addWidget(button)
        layout.addLayout(commands)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.layers = QTreeWidget()
        self.layers.setHeaderLabels(['검증 레이어', '셀 / 단위면'])
        self.layers.setMaximumHeight(215)
        layout.addWidget(self.layers)
        self.nodes = {}
        for title, keys in [
            ('관측 구조와 시설', ['blocks','hull','decks','walls','ceilings','supports','equipment','terrain']),
            ('개구부·추정 경계', ['openings','virtual','manual']),
            ('공기·공간', ['external_air','interior','final','hidden']),
        ]:
            group = QTreeWidgetItem(self.layers, [title])
            group.setFlags(group.flags()|Qt.ItemFlag.ItemIsAutoTristate)
            group.setCheckState(0,Qt.CheckState.Unchecked)
            for key in keys:
                title = LAYERS[key][0] if key in LAYERS else {
                    'blocks':'원본 블록','final':'최종 공간 후보','hidden':'기본 숨김 후보','manual':'수동 가상 경계'}[key]
                item = QTreeWidgetItem(group,[title])
                item.setData(0,ROLE,key)
                item.setCheckState(0,Qt.CheckState.Unchecked)
                item.setToolTip(0,LAYERS.get(key,('','',''))[2])
                self.nodes[key]=item
            group.setExpanded(True)
        self.layers.resizeColumnToContents(0)
        self.layers.itemChanged.connect(self.layer_changed)
        self.layers.currentItemChanged.connect(self.inspect_layer)
        options = QFormLayout()
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(.10,1.0)
        self.opacity.setSingleStep(.10)
        self.opacity.setValue(.65)
        self.opacity.valueChanged.connect(self.redraw)
        options.addRow('레이어 불투명도', self.opacity)
        layout.addLayout(options)
        self.results = QListWidget()
        self.results.setMinimumHeight(75)
        self.results.currentItemChanged.connect(self.inspect_space)
        layout.addWidget(self.results,1)
        selection = QHBoxLayout()
        focus = QPushButton('선택에 초점')
        focus.clicked.connect(self.focus)
        selection.addWidget(focus)
        hold = QPushButton('보류 / 해제')
        hold.clicked.connect(self.hold)
        selection.addWidget(hold)
        layout.addLayout(selection)
        self.axis = QComboBox()
        self.axis.addItems(list('XYZ'))
        boundary = QHBoxLayout()
        boundary.addWidget(self.axis)
        for label, callback in [('선택면 지정·교체',self.mark),('선택 경계 제거',self.remove)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            boundary.addWidget(button)
        layout.addLayout(boundary)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(100)
        layout.addWidget(self.details,1)

    def scan(self):
        self.owner.space_view.bulk.sessions.scan(True)

    def attach(self):
        doc = self.owner.doc
        result = doc.analysis_state.get('last_result',{})
        review = result.get('structure_review',{})
        identity = (id(doc), id(result) if review else None, doc.block_epoch)
        cfg = self.owner.view.space_display
        signature = (self.owner.view.show_blocks,cfg.get('show_candidates',True),cfg.get('external',False))
        if identity == self.identity and signature == getattr(self,'presentation_signature',None):
            if review:self.sync_selected_candidate()
            return
        same_doc = self.identity and self.identity[0]==id(doc)
        changed = identity != self.identity
        self.identity = identity
        self.presentation_signature = signature
        self.review = review
        if changed:
            self.stale = bool(review and (result.get('archive_read_only') or
                result.get('block_epoch')!=doc.block_epoch and
                result.get('provenance',{}).get('block_signature')!=self.block_signature(doc)))
        if changed:
            self._face_cache.clear()
            self._plane_cache.clear()
        if not same_doc:
            self.visible = set(doc.view_settings.get('structure_layers',[])) if getattr(doc,'from_project',False) else set()
            self.opacity.blockSignals(True)
            self.opacity.setValue(doc.view_settings.get('structure_opacity',.65) if getattr(doc,'from_project',False) else .65)
            self.opacity.blockSignals(False)
        self.layers.blockSignals(True)
        special = dict(zip(('blocks','final','hidden'),signature))
        counts = self.review.get('layers',{})
        candidates = doc.space_candidates
        hidden = [c for c in candidates if c['decision']=='exclude' or external(c,doc)]
        marks = review.get('manual_boundaries',[])
        for key, item in self.nodes.items():
            enabled = key in special or bool(review) and not self.stale
            item.setDisabled(not enabled)
            on = special.get(key, key in self.visible and enabled)
            item.setCheckState(0,Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            layer = counts.get(key,{})
            value = len(hidden) if key=='hidden' else len(candidates) if key=='final' else len(marks) if key=='manual' else layer.get('count')
            item.setText(1,'' if value is None else f'{value:,}')
        self.layers.blockSignals(False)
        if changed:
            current=self.results.currentItem()
            selected=current.data(ROLE) if current else None
            self.results.blockSignals(True)
            self.results.clear()
            self.result_rows = {}
            self.last_scene_candidate = None
            self.records={r['id']:r for r in review.get('spaces',[])}
            self.marks={m['id']:m for m in marks}
            for item in list(self.marks.values())+list(self.records.values()):
                kind = LABELS.get(item.get('classification'),'수동 가상 경계')
                label=f"{kind} · {item['id']}"
                if item.get('hold'): label='보류 · '+label
                row=QListWidgetItem(label)
                row.setData(ROLE,item['id'])
                self.results.addItem(row)
                self.result_rows[item['id']]=row
                if item['id']==selected:self.results.setCurrentItem(row)
            self.results.blockSignals(False)
            self.inspect_space(self.results.currentItem())
        if not review:
            summary='신규 구조 분석을 실행하면 관측 구조와 추정 경계를 비교할 수 있습니다.'
        elif self.stale:
            summary='현재 블록과 다른 구조 분석입니다. 신규 분석으로 갱신하세요.'
        else:
            self.sync_selected_candidate()
            summary=f"분석 범위 {review['scope']['lo']} → {review['scope']['hi']} · {len(self.records)} 후보. 관측면은 채움, 추정면은 점선입니다. 3D는 조밀한 면을 샘플링합니다."
        self.status.setText(summary+f' 기본 숨김 {len(hidden)}개는 제외 판정 또는 대형·외부 후보입니다.')
        self.redraw()

    def sync_selected_candidate(self):
        selected=getattr(self.owner.view,'selected_candidate_id',None)
        if selected==getattr(self,'last_scene_candidate',None):return
        self.last_scene_candidate=selected
        row=self.result_rows.get(selected)
        if row is not None:self.results.setCurrentItem(row)

    @staticmethod
    def block_signature(doc):
        from .analysis_sessions import block_signature
        return block_signature(doc)

    def layer_changed(self,*_):
        current={key for key,item in self.nodes.items() if item.checkState(0)==Qt.CheckState.Checked and not item.isDisabled()}
        view = self.owner.view
        cfg = view.space_display
        manager = self.owner.space_manager
        if manager.blocks.isChecked()!=('blocks' in current):
            manager.blocks.setChecked('blocks' in current)
        if 'final' in current and cfg.get('mode') in ('none','confirmed'):
            self.owner.space_tools.set_mode('both')
        if cfg.get('show_candidates',True)!=('final' in current):
            self.owner.space_view.overlay.setChecked('final' in current)
            cfg['show_candidates']='final' in current
        self.visible = current - {'blocks','final','hidden'}
        self.owner.space_tools.show_external.setChecked('hidden' in current)
        self.owner.space_tools.refresh()
        self.redraw()

    def redraw(self,*_):
        w=self.owner
        w.view.update()
        w.space_manager.plan.update()
        w.workbench.axis_plan.canvas.update()
        w.workbench.timer.start()

    def inspect_layer(self,item,*_):
        if item is None:return
        key=item.data(0,ROLE)
        data=self.review.get('layers',{}).get(key)
        if data is None:return
        title,_,basis=LAYERS[key]
        self.details.setPlainText(f'{title}\n{basis}\n기록 {data["count"]:,}셀/면\n저장 한도로 생략한 구간 {data["rows_omitted"]:,}개')

    def inspect_space(self,item,*_):
        if item is None:
            self.details.clear()
            return
        key=item.data(ROLE)
        record=self.records.get(key) or self.marks.get(key)
        if record is None:return
        if key in self.records:
            self.owner.view.selected_candidate_id=key
            self.last_scene_candidate=key
        shown=dict(record)
        if 'classification' in shown:shown['classification']=LABELS[shown['classification']]
        self.details.setPlainText(json.dumps(shown,ensure_ascii=False,indent=2))
        self.redraw()

    def focus(self):
        item=self.results.currentItem()
        if item is None:return
        key=item.data(ROLE)
        r=self.records.get(key) or self.marks.get(key)
        if r is None:return
        b=Bounds(tuple(r['lo']),tuple(r['hi']))
        w=self.owner
        w.set_selection(b)
        w.layer.setValue(b.lo[1])
        w.workbench.axis_plan.focus_space()
        w.view.focus_selection()
        if key in self.records:
            w.view.selected_candidate_id=key
        self.redraw()

    def edit(self, callback):
        w=self.owner
        if not self.review or self.stale or w.loading or w.operation is not None:
            return
        if w.doc.analysis_state.get('session_side')=='old' or w.doc.analysis_state.get('last_result',{}).get('archive_read_only'):
            self.status.setText('저장 비교 결과는 읽기 전용입니다. 신규 분석 결과로 전환하세요.')
            return
        state=deepcopy(w.doc.analysis_state)
        if callback(state['last_result']['structure_review']) is False:return
        try:
            commit(w.doc,state,action='structure_review_edit')
        except ValueError as e:
            self.status.setText(str(e))
            return
        w.refresh_history()
        self.attach()
        self.redraw()

    def hold(self):
        item=self.results.currentItem()
        if item is None:return
        def update(review):
            key=item.data(ROLE)
            record=next((r for r in review['spaces'] if r['id']==key),None)
            if record is None:return False
            record['hold']=not record.get('hold',False)
        self.edit(update)

    def mark(self):
        b=self.owner.doc.selection
        axis=self.axis.currentIndex()
        lo,hi=list(b.lo),list(b.hi)
        hi[axis]=lo[axis]
        item=self.results.currentItem()
        key=item.data(ROLE) if item is not None else None
        from uuid import uuid4
        replacing=key in self.marks
        ident=key if replacing else 'M-'+uuid4().hex[:10]
        def update(review):
            marks=review.setdefault('manual_boundaries',[])
            if not replacing and len(marks)>=256:
                self.status.setText('수동 경계는 분석당 최대 256개까지 저장할 수 있습니다.')
                return False
            marks[:]=[m for m in marks if m['id']!=ident]
            marks.append({'id':ident,'lo':lo,'hi':hi,'axis':axis,'confidence':'manual',
                          'reason':'선택 범위 최솟값의 분석 전용 가상 경계'})
        self.edit(update)
        if ident in self.marks:
            self.nodes['manual'].setCheckState(0,Qt.CheckState.Checked)

    def remove(self):
        item=self.results.currentItem()
        key=item.data(ROLE) if item is not None else None
        if key not in self.marks:return
        self.edit(lambda review: review.update(manual_boundaries=[
            m for m in review['manual_boundaries'] if m['id']!=key]))

    def clip(self):
        view,doc=self.owner.view,self.owner.doc
        lo,hi=view.display_state.clip_bounds([0,0,0],[x-1 for x in doc.schematic.size],doc,view.camera())
        if view.zone_only:
            lo=list(map(max,lo,doc.selection.lo))
            hi=list(map(min,hi,doc.selection.hi))
        if view.cut:
            hi[1]=min(hi[1],view.layer)
        if view.single_layer:
            lo[1],hi[1]=max(lo[1],view.layer),min(hi[1],view.layer)
        if view.ghost_floors and view.floor_range:
            lo[1],hi[1]=max(lo[1],view.floor_range[0]),min(hi[1],view.floor_range[1])
        return tuple(lo), tuple(hi), None if view.visible_y is None else tuple(view.visible_y)

    def active_layers(self):
        if not self.review or self.stale:return
        for key in LAYERS:
            if key in self.visible:
                yield key, self.review['layers'].get(key,{})

    def overlay_image(self,u,v,f,position,project=False):
        if not self.visible or self.stale or not self.review:return None
        size=self.owner.doc.schematic.size
        step=max(1,(max(size[u],size[v])+1199)//1200)
        shape=((size[v]+step-1)//step,(size[u]+step-1)//step)
        token=(u,v,f,position,project,tuple(sorted(self.visible)),self.clip(),self.opacity.value())
        if token in self._plane_cache:return self._plane_cache[token]
        rgba=np.zeros((*shape,4),np.uint8)
        for key,layer in self.active_layers():
            rows=layer.get('runs') or [r[2:] for r in layer.get('faces',[])]
            if not rows:continue
            # Clip once using the same display policy as confirmed spaces.
            if not project and f==1:rows=[r for r in rows if r[0]==position]
            elif not project and f==2:rows=[r for r in rows if r[1]==position]
            elif not project:rows=[r for r in rows if r[2]<=position<=r[3]]
            rows=visible_space_runs({'runs':rows},self.owner.doc,self.owner.view)
            mask=sampled_space_mask({'runs':rows},f,None if project else position,step,shape)
            if key in DASHED:
                yy,xx=np.ogrid[:shape[0],:shape[1]]
                mask &= (xx+yy)%4 < 2
            color=QColor(LAYERS[key][1])
            rgba[mask]=[color.red(),color.green(),color.blue(),int((85 if key in AIR else 210)*self.opacity.value())]
        if 'manual' in self.visible:
            lo,hi,levels=self.clip()
            for mark in self.marks.values():
                a,b=mark['lo'],mark['hi']
                low=[max(x,y) for x,y in zip(a,lo)];high=[min(x,y) for x,y in zip(b,hi)]
                if any(x>y for x,y in zip(low,high)) or not project and not low[f]<=position<=high[f]:continue
                r=slice((low[v]+step-1)//step,high[v]//step+1)
                c=slice((low[u]+step-1)//step,high[u]//step+1)
                rgba[r,c]=[255,145,116,190]
        image=QImage(rgba.data,shape[1],shape[0],rgba.strides[0],QImage.Format.Format_RGBA8888).copy()
        self._plane_cache[token]=image
        while len(self._plane_cache)>7:self._plane_cache.popitem(last=False)
        return image

    def quads_3d(self,key,layer,clip):
        token=(key,clip)
        if token not in self._face_cache:
            lo,hi,levels=clip
            rows=layer.get('faces',[])
            if rows:
                quads=[]
                rows=[(axis,sign,y,z,max(a,lo[0]),min(b,hi[0]))
                      for axis,sign,y,z,a,b in rows if lo[1]<=y<=hi[1] and lo[2]<=z<=hi[2]
                      and (levels is None or y in levels) and a<=hi[0] and b>=lo[0]]
                step=max(1,(len(rows)+5999)//6000)
                for axis,sign,y,z,a,b in rows[::step]:
                    low=np.array([a,y,z],float)
                    high=np.array([b+1,y+1,z+1],float)
                    axes=[i for i in range(3) if i!=axis]
                    positions=range(a,b+1) if axis==0 else (low[axis],)
                    for position in positions:
                        low[axis]=position+int(sign>0)
                        q=np.tile(low,(4,1))
                        q[[1,2],axes[0]]=high[axes[0]]
                        q[[2,3],axes[1]]=high[axes[1]]
                        quads.append(q)
                result=np.asarray(quads).reshape(-1,4,3)
                result=result[::max(1,(len(result)+5999)//6000)]
            else:
                from .space_geometry import boundary_quads
                runs=visible_space_runs({'runs':layer.get('runs',[])},self.owner.doc,self.owner.view)
                # Keep exact occupied intervals. Use a capped subset for highly
                # fragmented display; section view still shows all saved rows.
                stride=max(1,(len(runs)+7999)//8000)
                result,_=boundary_quads(runs[::stride])
                result=result[::max(1,(len(result)+5999)//6000)]
            self._face_cache[token]=result
            while len(self._face_cache)>20:self._face_cache.popitem(last=False)
        return self._face_cache[token]

    def draw_3d(self,painter):
        if not self.visible or self.stale or not self.review:return
        view=self.owner.view
        clip=self.clip()
        stride=8 if view.interaction_active else 1
        for key,layer in self.active_layers():
            if view.interaction_active and (key,clip) not in self._face_cache:continue
            quads=self.quads_3d(key,layer,clip)[::stride]
            if not len(quads):continue
            pixels=view.project(quads.reshape(-1,3)).reshape(-1,4,2)
            color=QColor(LAYERS[key][1])
            color.setAlpha(int(self.opacity.value()*145))
            if key in DASHED:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(color,1,Qt.PenStyle.DashLine))
            else:
                edge=QColor(color)
                edge.setAlpha(min(255,color.alpha()+35))
                painter.setPen(QPen(edge,.4))
                painter.setBrush(color)
            for poly in pixels:
                painter.drawPolygon(QPolygonF([QPointF(*p) for p in poly]))
        if 'manual' in self.visible:
            lo,hi,levels=clip
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#ff916f'),2,Qt.PenStyle.DashLine))
            for mark in self.marks.values():
                low=np.maximum(lo,mark['lo'])
                high=np.minimum(hi,mark['hi'])
                if np.any(low>high):continue
                axis=mark['axis']
                a,b=[value for value in range(3) if value!=axis]
                quad=np.tile(low.astype(float),(4,1))
                quad[[1,2],a]=high[a]+1
                quad[[2,3],b]=high[b]+1
                painter.drawPolygon(QPolygonF([QPointF(*p) for p in view.project(quad)]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
