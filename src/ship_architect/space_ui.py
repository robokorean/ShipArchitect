"""Persistent space manager and exact-cell plan editing, sharing the engine's runs."""
import copy,json,hashlib
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt,QRectF,QPointF,QTimer
from PySide6.QtGui import QColor,QPen,QPolygonF,QPainter,QImage
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QListWidget,QListWidgetItem,QLabel,QLineEdit,QComboBox,QSpinBox,QCheckBox,QPlainTextEdit,QAbstractItemView,QColorDialog,QScrollArea)
from .model import Bounds
from .space_display import visible as space_visible,alpha as space_alpha,candidate_spaces,color as display_color
from .space_model import new_space,box_runs,bbox,normalize,subtract,from_candidate,statistics,connections,analyze_connections,contains,validate_space,plane_rectangles
from .views import LayerView,BACKGROUND
from .schematic import atomic_write
from .display import overlay_rectangles

def draw_spaces_3d(view,painter):
    from .space_geometry import draw_volumes
    draw_volumes(view,painter)

class SpacePlan(LayerView):
    def __init__(self,manager):
        super().__init__();self.manager=manager
        bar=QHBoxLayout();mode=QComboBox()
        for i in range(manager.edit_mode.count()):mode.addItem(manager.edit_mode.itemText(i),manager.edit_mode.itemData(i))
        mode.currentIndexChanged.connect(manager.edit_mode.setCurrentIndex);manager.edit_mode.currentIndexChanged.connect(mode.setCurrentIndex)
        bar.addWidget(mode);height=QCheckBox('공간 전체 높이');height.toggled.connect(manager.all_height.setChecked);manager.all_height.toggled.connect(height.setChecked);bar.addWidget(height)
        back=QPushButton('공간 목록·속성');back.clicked.connect(lambda:manager.owner.tabs.setCurrentWidget(manager));bar2=QHBoxLayout();bar2.addWidget(back)
        compare=QPushButton('미승인 전/후 전환');compare.clicked.connect(lambda:manager.owner.compare.setCurrentIndex(1-manager.owner.compare.currentIndex()) if manager.owner.proposal is not None else None);bar2.addWidget(compare)
        layout=QVBoxLayout(self);layout.addLayout(bar);layout.addLayout(bar2);layout.addStretch()
        self.setToolTip('공간 2D: 모드를 선택하고 드래그해 셀 경계를 추가/제거/분리합니다. 선택 모드는 클릭으로 공간 선택.')
    def refresh(self):
        super().refresh()
        owner=self.manager.owner
        if self.doc is None or not hasattr(owner.view,'display_state') or self.image is None:return
        # Plane image sampling is inherited; use matching exact world coordinates for visibility.
        from PySide6.QtGui import QPainter
        w,h,l=self.doc.schematic.size;step=max(1,int(np.ceil(max(w,l)/1536)))
        xx,zz=np.meshgrid(np.arange(0,w,step),np.arange(0,l,step));ii=self.layer*w*l+zz*w+xx
        visible=owner.view.display_state.visible_indices(ii.ravel(),self.doc,owner.view).reshape(xx.shape)
        overlay=np.zeros((*visible.shape,4),np.uint8);overlay[~visible]=[16,26,40,255];image=QImage(overlay.data,overlay.shape[1],overlay.shape[0],overlay.strides[0],QImage.Format.Format_RGBA8888)
        painter=QPainter(self.image);painter.drawImage(0,0,image);painter.end();self.update()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.doc is None:return
        p=QPainter(self);
        if not self.manager.blocks.isChecked():
            p.fillRect(QRectF(self.ox,self.oz,self.doc.schematic.size[0]*self.scale,self.doc.schematic.size[2]*self.scale),BACKGROUND)
        if self.manager.only.isChecked() and self.doc.active_space() is not None:
            # Clip original floor image to exact selected strips, without copying the volume.
            p.fillRect(QRectF(self.ox,self.oz,self.doc.schematic.size[0]*self.scale,self.doc.schematic.size[2]*self.scale),BACKGROUND)
            if self.manager.blocks.isChecked():
                for a,z,b,Z in plane_rectangles(self.doc.active_space(),self.layer):
                    w,h,l=self.doc.schematic.size
                    p.drawImage(QRectF(self.ox+a*self.scale,self.oz+z*self.scale,(b-a)*self.scale,(Z-z)*self.scale),self.image,QRectF(a/w*self.image.width(),z/l*self.image.height(),(b-a)/w*self.image.width(),(Z-z)/l*self.image.height()))
        self.layout_map();m=self.manager
        if m.overlays.isChecked() or candidate_spaces(m.owner.view):
            for s in (self.doc.spaces if m.overlays.isChecked() else [])+candidate_spaces(m.owner.view):
                if not space_visible(m.owner.view,s,s.get('_candidate',False)) or m.only.isChecked() and s['id']!=self.doc.active_space_id:continue
                c=QColor(display_color(s));c.setAlpha(space_alpha(m.owner.view,s,s.get('_candidate',False)));p.setBrush(c);p.setPen(QPen(QColor(s['color']),.4))
                for a,z,b,Z in (overlay_rectangles(s,self.layer,self.doc,self.manager.owner.view) if hasattr(self.manager.owner.view,'display_state') else plane_rectangles(s,self.layer)):p.drawRect(QRectF(self.ox+a*self.scale,self.oz+z*self.scale,(b-a)*self.scale,(Z-z)*self.scale))
        # Orange lines are sampled air-to-air faces from the old partition.
        p.setPen(QPen(QColor('#ff9d42'),2))
        for sample in getattr(m.owner.view,'partition_faces',[]):
            axis=sample['axis_yzx']
            if axis==0:continue
            for x,y,z in sample['positions']:
                if y!=self.layer:continue
                if axis==1:
                    z+=int(sample['sign']>0);end_x,end_z=x+1,z
                else:
                    x+=int(sample['sign']>0);end_x,end_z=x,z+1
                p.drawLine(QPointF(self.ox+x*self.scale,self.oz+z*self.scale),
                           QPointF(self.ox+end_x*self.scale,self.oz+end_z*self.scale))
        for difference in getattr(m.owner.view,'validation_spaces',[]):
            if not difference['id'].startswith('session-'):
                continue
            color=QColor(difference['color']);color.setAlpha(165)
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(color)
            for a,z,b,Z in plane_rectangles(difference,self.layer):
                p.drawRect(QRectF(self.ox+a*self.scale,self.oz+z*self.scale,(b-a)*self.scale,(Z-z)*self.scale))
        # Pending diff is exact, separate from permission / space visibility.
        proposal=self.manager.owner.proposal
        if proposal is not None and self.manager.owner.compare.currentIndex()==0:
            sc=self.doc.schematic;w,h,l=sc.size;ii=proposal.indices;valid=ii//(w*l)==self.layer;ii=ii[valid]
            after=np.full(len(proposal.indices),proposal.target) if proposal.after is None else proposal.after
            air={i for i,s in sc.palette.items() if s in ('minecraft:air','minecraft:cave_air','minecraft:void_air')}
            step=max(1,int(np.ceil(max(w,l)/1536)));width=(w+step-1)//step;depth=(l+step-1)//step
            rgba=np.zeros((depth,width,4),np.uint8);old=proposal.before[valid];new=after[valid]
            colors=np.tile(np.array([239,195,116,210],np.uint8),(len(ii),1));colors[np.isin(old,list(air))]=[116,215,162,210];colors[np.isin(new,list(air))]=[233,133,128,210]
            rgba[(ii//w%l)//step,(ii%w)//step]=colors
            image=QImage(rgba.data,width,depth,rgba.strides[0],QImage.Format.Format_RGBA8888)
            p.drawImage(QRectF(self.ox,self.oz,w*self.scale,l*self.scale),image)
        if self.drag is not None and getattr(self,'end',None) is not None:
            b=Bounds.between(self.drag,self.end);p.setPen(QPen(QColor('#ffffff'),2));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(self.ox+b.lo[0]*self.scale,self.oz+b.lo[2]*self.scale,(b.hi[0]-b.lo[0]+1)*self.scale,(b.hi[2]-b.lo[2]+1)*self.scale))
        from .network_ui import draw_route_2d
        draw_route_2d(self,p)
        if hasattr(self.manager.owner,'workbench'):self.manager.owner.workbench.draw_2d(self,p)
        p.end()
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton:self.drag=self.coord(e.position());self.end=self.drag;self.update()
    def mouseMoveEvent(self,e):
        if self.drag is not None:self.end=self.coord(e.position());self.update()
    def mouseReleaseEvent(self,e):
        if self.drag is None:return
        end=self.coord(e.position());start=self.drag;self.drag=None;self.end=None
        if end is None:return
        m=self.manager
        if m.edit_mode.currentData()=='select':
            if start==end:m.select_at(end)
            else:m.owner.set_selection(Bounds.between(start,end))
        else:m.edit_bounds(Bounds.between(start,end),m.edit_mode.currentData())
        self.update()

class SpaceManager(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.restoring=False;self.saved_revision=None
        self.autosave=QTimer(self);self.autosave.setSingleShot(True);self.autosave.setInterval(500);self.autosave.timeout.connect(self.save_metadata)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();lay=QVBoxLayout(content);scroll.setWidget(content);outer.addWidget(scroll)
        def buttons(items):
            row=QHBoxLayout()
            for label,slot in items:
                b=QPushButton(label);b.clicked.connect(slot);row.addWidget(b)
            lay.addLayout(row)
        buttons([('현재 선택으로 공간 생성',self.create),('분석 후보 전체 가져오기',self.import_candidates),('v0.7 공간 구역 가져오기',self.import_legacy),('일반 사각 선택으로 돌아가기',self.clear_active)])
        row=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('공간 이름·용도 검색');self.sort=QComboBox();self.sort.addItems(['기존 순서','이름순','크기순','층별 그룹','용도별 그룹','자동/사용자 그룹'])
        self.search.textChanged.connect(self.refresh);self.sort.currentIndexChanged.connect(self.refresh);row.addWidget(self.search,1);row.addWidget(self.sort);lay.addLayout(row)
        self.list=QListWidget();self.list.setMinimumHeight(140);self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.list.currentItemChanged.connect(self.select_item);self.list.itemChanged.connect(self.visibility_changed);lay.addWidget(self.list,2)
        buttons([('전체 표시',lambda:self.show_all(True)),('전체 숨김',lambda:self.show_all(False)),('선택 공간 합치기',self.merge),('선택 공간 삭제',self.delete)])
        row=QHBoxLayout();self.name=QLineEdit();self.name.setPlaceholderText('공간 이름');row.addWidget(self.name);self.purpose=QLineEdit();self.purpose.setPlaceholderText('용도: 함교/격납고/통로 등');row.addWidget(self.purpose)
        self.opacity=QSpinBox();self.opacity.setRange(0,255);self.opacity.setValue(80);self.opacity.setPrefix('투명도 α ');row.addWidget(self.opacity);lay.addLayout(row)
        self.space_color='#4fc7b0';buttons([('색상 선택',self.choose_color),('이름·용도·색상·투명도 적용',self.properties)])
        row=QHBoxLayout();self.edit_mode=QComboBox()
        for name,key in [('공간 클릭 선택','select'),('경계에 사각 셀 추가','add'),('경계에서 사각 셀 제거','remove'),('사각 부분을 새 공간으로 분리','split')]:self.edit_mode.addItem(name,key)
        row.addWidget(self.edit_mode);self.all_height=QCheckBox('2D 드래그를 공간 전체 높이에 적용');row.addWidget(self.all_height);lay.addLayout(row)
        buttons([('현재 3D 선택을 경계에 추가',lambda:self.edit_bounds(owner.doc.selection,'add')),('현재 3D 선택을 경계에서 제거',lambda:self.edit_bounds(owner.doc.selection,'remove')),('현재 3D 선택으로 분리',lambda:self.edit_bounds(owner.doc.selection,'split')),('공간 2D 열기',self.open_plan),('선택 공간으로 카메라 이동',self.focus)])
        row=QHBoxLayout();self.overlays=QCheckBox('공간 오버레이');self.overlays.setChecked(True);self.blocks=QCheckBox('실제 블록');self.blocks.setChecked(True);self.only=QCheckBox('선택 공간만');self.ghost=QCheckBox('다른 층 반투명')
        for c in (self.overlays,self.blocks,self.only,self.ghost):row.addWidget(c);c.toggled.connect(self.display)
        lay.addLayout(row)
        row=QHBoxLayout();self.floor=QComboBox();self.floor.currentIndexChanged.connect(self.select_floor);row.addWidget(self.floor);self.floor_on=QCheckBox('이 층 표시');self.floor_on.setChecked(True);self.floor_on.toggled.connect(self.floor_visibility);row.addWidget(self.floor_on)
        auto=QPushButton('층 다시 찾기');auto.clicked.connect(self.detect_floors);row.addWidget(auto);lay.addLayout(row)
        buttons([('현재 Y를 층으로 등록',self.add_floor),('현재 Y의 수동 층 기준 제거',self.remove_floor)])
        buttons([('선택 공간 통계·연결 분석',self.analyze),('공간 작업 기록',self.show_history),('공간별 실행 취소',self.undo_space)])
        self.status=QLabel('정확한 셀 구간으로 관리합니다. Ctrl로 여러 공간 선택 후 합치기. 2D 경계 편집은 공간만 변경하며 블록은 미리보기 후 승인합니다.');self.status.setWordWrap(True);lay.addWidget(self.status)
        self.details=QPlainTextEdit();self.details.setMinimumHeight(120);self.details.setReadOnly(True);lay.addWidget(self.details,2)
        self.plan=SpacePlan(self)
        owner.view.space_picked.connect(self.select_id)
    def geometry_key(self):
        events=[h for h in self.owner.doc.history if h.get('blocks',0)>0 and h['action'] in ('approve','undo','redo')]
        return hashlib.sha256(json.dumps(events,sort_keys=True).encode()).hexdigest()
    def path(self):return Path(self.owner.data_dir)/('spaces-'+self.owner.doc.project_id+'.json')
    def save_metadata(self):
        if self.restoring:return
        try:
            d=self.owner.doc;payload={'version':1,'revision':d.revision,'geometry_key':self.geometry_key(),'project_id':d.project_id,'size':list(d.schematic.size),'spaces':d.spaces,'active':d.active_space_id,'floor_visibility':getattr(d,'floor_visibility',{}),'floor_starts':getattr(d,'floor_starts',[])}
            atomic_write(self.path(),json.dumps(payload,ensure_ascii=False).encode());self.saved_revision=d.revision
        except Exception as e:self.status.setText('공간 자동 저장 실패: '+str(e))
    def attach(self):
        self.autosave.stop();d=self.owner.doc
        d.analysis_display_origin='프로젝트 복원' if getattr(d,'from_project',False) else '원본 열기'
        if self.path().exists():
            try:
                if self.path().stat().st_size>64_000_000:raise ValueError('공간 자동 저장 파일 크기 초과')
                q=json.loads(self.path().read_text(encoding='utf8'))
                if q.get('revision',-1)<d.revision or q.get('geometry_key')!=self.geometry_key():q=None
                if q is None:raise LookupError('저장한 프로젝트의 블록 이력을 우선합니다.')
                if q['project_id']!=d.project_id or q['size']!=list(d.schematic.size):raise ValueError('문서 크기/ID 불일치')
                spaces=[validate_space(s,d.schematic.size) for s in q['spaces']]
                if len(spaces)>512 or len({s['id'] for s in spaces})!=len(spaces):raise ValueError('공간 수/ID 오류')
                floor_starts=q.get('floor_starts',[])
                floor_visibility=q.get('floor_visibility',{})
                active=q.get('active')
                if active is not None and active not in {s['id'] for s in spaces}:
                    raise ValueError('자동 저장 선택 공간 ID 오류')
                if not isinstance(floor_starts,list) or any(type(y) is not int or not 0<=y<d.schematic.size[1] for y in floor_starts):
                    raise ValueError('사용자 층 기준 오류')
                if not isinstance(floor_visibility,dict) or any(not str(y).isdigit() or not 0<=int(y)<d.schematic.size[1] or type(v) is not bool for y,v in floor_visibility.items()):
                    raise ValueError('자동 저장 층 표시 형식 오류')
                if spaces!=d.spaces or active!=d.active_space_id:
                    d.set_spaces(spaces,'space_recover',active)
                d.floor_visibility=floor_visibility
                d.floor_starts=floor_starts
                d.analysis_display_origin='로컬 저장 공간 복원'
            except LookupError:pass
            except Exception as e:self.status.setText('자동 저장 공간 복원 실패: '+str(e))
        self.plan.doc=d;self.plan.blocks=d.schematic.blocks;self.plan.layer=self.owner.layer.value();self.refresh();self.detect_floors();self.plan.refresh();self.owner.view.rebuild()
    def changed(self,spaces,action,active=None):
        try:
            self.owner.reject()
            from .analysis_sessions import timestamp, block_signature
            from . import __version__
            d=self.owner.doc
            old={s['id']:s for s in d.spaces}
            at=timestamp()
            signature=block_signature(d)
            stamped=[]
            for space in spaces:
                previous=old.get(space['id'])
                if previous!=space:
                    meta=dict(space.get('provenance',{}))
                    if previous is None and not meta:
                        meta.update(program_version=__version__,created_at=at,origin='manual')
                    meta.update(modified_at=at,modified_by=__version__,last_action=action,
                                modified_block_signature=signature,user_modified=True)
                    space=dict(space,provenance=meta)
                stamped.append(space)
            d.set_spaces(stamped,action,active)
            if self.owner.doc.active_space() is not None:
                lo,hi=bbox(self.owner.doc.active_space());self.owner.set_selection(Bounds(lo,hi))
            self.refresh();self.owner.refresh_history();self.owner.update_summary();self.detect_floors();self.owner.view.rebuild();self.autosave.start()
        except Exception as e:self.owner.error(e)
    def refresh(self,*_):
        self.restoring=True;d=self.owner.doc;self.list.clear();current=None
        spaces=list(d.spaces);mode=self.sort.currentText();group=None
        def key(s):
            if mode=='이름순':return s['name']
            if mode=='크기순':return -sum(b-a+1 for _,_,a,b in s['runs'])
            if mode=='층별 그룹':return (bbox(s)[0][1],s['name'])
            if mode=='용도별 그룹':return (s['purpose'],s['name'])
            if mode=='자동/사용자 그룹':return (s.get('origin','legacy'),s['name'])
            return 0
        if mode!='기존 순서':spaces.sort(key=key)
        query=self.search.text().casefold()
        for s in spaces:
            if query and query not in (s['name']+' '+s['purpose']).casefold():continue
            from .floors import provenance
            source,modified=provenance(d,s)
            origin={'auto':'자동','manual':'사용자','legacy':'구버전 미분류'}[source]+('·수동 보정' if modified else '')
            label=f"Y={bbox(s)[0][1]}" if mode=='층별 그룹' else s['purpose'] if mode=='용도별 그룹' else origin if mode=='자동/사용자 그룹' else None
            if label is not None and label!=group:
                header=QListWidgetItem('── '+label+' ──');header.setFlags(Qt.ItemFlag.NoItemFlags);self.list.addItem(header);group=label
            n=sum(b-a+1 for _,_,a,b in s['runs']);item=QListWidgetItem(f"{s['name']} · {s['purpose']} · {n:,}칸 · {origin}");item.setData(Qt.ItemDataRole.UserRole,s['id']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked if space_visible(self.owner.view,s) else Qt.CheckState.Unchecked);self.list.addItem(item)
            if s['id']==d.active_space_id:current=item
        if current:self.list.setCurrentItem(current)
        self.restoring=False;self.load_properties();self.display();self.autosave.start()
        if hasattr(self.owner,'network_view'):self.owner.network_view.refresh_spaces()
    def selected_ids(self):return {item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems() if item.data(Qt.ItemDataRole.UserRole)}
    def select_item(self,item,*_):
        if item is not None and not self.restoring:self.select_id(item.data(Qt.ItemDataRole.UserRole),True)
    def select_id(self,sid,from_list=False):
        d=self.owner.doc;s=next((s for s in d.spaces if s['id']==sid),None)
        if s is None:return
        self.owner.reject();lo,hi=bbox(s);self.owner.set_selection(Bounds(lo,hi));d.active_space_id=sid;d.dirty=True
        from PySide6.QtCore import QItemSelectionModel
        self.restoring=True
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole)==sid:self.list.setCurrentItem(self.list.item(i),QItemSelectionModel.SelectionFlag.NoUpdate if from_list else QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.restoring=False;self.load_properties();self.display();self.autosave.start()
        self.status.setText(f"선택: {s['name']} · 실제 셀만 건축 허용 · 경계 상자 {lo} → {hi}")
    def select_at(self,xyz):
        w,h,l=self.owner.doc.schematic.size;idx=xyz[1]*w*l+xyz[2]*w+xyz[0]
        for s in reversed(self.owner.doc.spaces):
            if space_visible(self.owner.view,s) and contains(s,[idx],(w,h,l))[0]:self.select_id(s['id']);self.refresh();return
    def clear_active(self):
        self.owner.reject();self.owner.doc.active_space_id=None;self.refresh();self.status.setText('일반 사각 선택입니다. 공간 경계 제한이 해제됩니다. 보호 장치는 계속 보존합니다.')
    def create(self):
        try:
            d=self.owner.doc;s=new_space(box_runs(d.selection),d.schematic.size,f'공간 {len(d.spaces)+1}');self.changed(d.spaces+[s],'space_create',s['id'])
        except Exception as e:self.owner.error(e)
    def import_legacy(self):
        d=self.owner.doc
        try:
            existing={s['name'] for s in d.spaces};converted=[]
            for zone in d.zones:
                if zone['name'] in existing:continue
                if zone.get('space_volume_runs'):
                    from .spatial3d import fingerprint
                    if fingerprint(zone['space_volume_runs']) not in {fingerprint(s['runs']) for s in d.spaces}:
                        converted.append(new_space(zone['space_volume_runs'],d.schematic.size,zone['name']))
                    continue
                if not zone.get('space_footprint'):continue
                if len(zone['space_footprint'])>=4096:raise ValueError('구버전 경계 잘림 여부를 확인할 수 없습니다. 작은 영역으로 재분석하세요.')
                c={'id':zone['name'],'floor_y':zone['lo'][1],'hi':zone['hi'],'footprint':zone['space_footprint'],'kind':zone.get('space_kind','room')}
                sp=from_candidate(d,c);sp['name']=zone['name'];converted.append(sp)
            if not converted:self.status.setText('가져올 v0.7 바닥 경계가 없습니다. 기존 구역과 블록은 보존됩니다.');return
            self.changed(d.spaces+converted,'space_legacy_import',converted[0]['id'])
        except Exception as e:self.owner.error(e)
    def import_candidates(self):
        d=self.owner.doc
        if self.owner.operation is not None:return
        if d.space_revision!=d.revision:self.status.setText('공간 후보 분석을 먼저 실행하세요.');return
        result=d.analysis_state.get('last_result')
        if result and result.get('method') in ('3d_air_components','exterior_voxel_mesh'):
            from .analysis_state import register,commit
            revision=d.revision;state=copy.deepcopy(d.analysis_state);snapshot=copy.deepcopy(result)
            def done_safe(value):
                if self.owner.doc is not d or d.revision!=revision:return
                spaces,r,added=value;state['last_result']=r;commit(d,state,spaces,'space_safe_import');self.owner.space_view.bulk.attach();self.refresh();self.owner.refresh_history();self.status.setText(f'구조 검사 통과 후보 {len(added)}개 등록 · 검토/제외 및 기존 공간 보호')
            self.owner.run_operation(lambda p,c:register(d,snapshot,True),done_safe,'검증된 3D 후보 가져오기');return
        revision=d.revision;candidates=copy.deepcopy([c for c in d.space_candidates if not c.get('exclude_from_bulk',False)])
        def work(progress,cancel):
            from .chunks import checkpoint
            out=[]
            for i,c in enumerate(candidates):
                checkpoint(cancel);out.append(from_candidate(d,c,cancel));progress(int(100*(i+1)/max(1,len(candidates))),'정확한 공간 셀 생성')
            if not out:raise ValueError('인식 후보가 없습니다. 빈 프로젝트는 현재 선택으로 공간을 만드세요.')
            return out
        def done(spaces):
            if self.owner.doc is d and d.revision==revision:
                from .space_model import token
                existing={token(s).split(':',1)[1] for s in d.spaces};fresh=[s for s in spaces if token(s).split(':',1)[1] not in existing]
                if fresh:self.changed(d.spaces+fresh,'space_import',fresh[0]['id'])
                self.status.setText(f'가져오기 {len(fresh)}개 · 동일 형태 {len(spaces)-len(fresh)}개 제외 · 기존 수동 경계 보존')
            else:self.status.setText('분석 중 문서가 바뀌어 가져오기를 취소했습니다.')
        self.owner.run_operation(work,done,'공간 후보 가져오기')

    def merge(self):
        d=self.owner.doc;ids=self.selected_ids();chosen=[s for s in d.spaces if s['id'] in ids]
        if len(chosen)<2:self.status.setText('Ctrl로 두 공간 이상 선택하세요.');return
        try:
            s=new_space([r for s in chosen for r in s['runs']],d.schematic.size,' + '.join(s['name'] for s in chosen)[:200],chosen[0]['purpose']);self.changed([s for s in d.spaces if s['id'] not in ids]+[s],'space_merge',s['id'])
        except Exception as e:self.owner.error(e)
    def delete(self):
        d=self.owner.doc;ids=self.selected_ids() or {d.active_space_id}
        if hasattr(self.owner,'space_tools'):self.owner.space_tools.delete(ids=ids-{None})
        else:self.changed([s for s in d.spaces if s['id'] not in ids],'space_delete')
    def edit_bounds(self,b,mode):
        d=self.owner.doc;s=d.active_space()
        if s is None:self.status.setText('편집할 공간을 먼저 선택하세요.');return
        try:
            b.validate(d.schematic.size)
            if self.all_height.isChecked():
                lo,hi=bbox(s);b=Bounds((b.lo[0],lo[1],b.lo[2]),(b.hi[0],hi[1],b.hi[2]))
            cut=box_runs(b);rest=subtract(s['runs'],cut,d.schematic.size);spaces=copy.deepcopy(d.spaces);target=next(q for q in spaces if q['id']==s['id'])
            if mode=='add':target['runs']=normalize(target['runs']+cut,d.schematic.size)
            elif mode=='remove':target['runs']=rest
            elif mode=='split':
                moved=subtract(s['runs'],rest,d.schematic.size)
                if not moved or not rest:raise ValueError('분리는 원래 공간의 일부를 선택해야 합니다. 빈 선택/전체 선택은 분리할 수 없습니다.')
                target['runs']=rest;spaces.append(new_space(moved,d.schematic.size,s['name']+' 분리',s['purpose']))
            if not target['runs']:raise ValueError('공간 전체 제거는 공간 삭제 버튼을 사용하세요.')
            self.changed(spaces,'space_'+mode,s['id'])
        except Exception as e:self.owner.error(e)
    def load_properties(self):
        s=self.owner.doc.active_space()
        if s:self.name.setText(s['name']);self.purpose.setText(s['purpose']);self.opacity.setValue(s['opacity']);self.space_color=s['color']
    def choose_color(self):
        c=QColorDialog.getColor(QColor(self.space_color),self)
        if c.isValid():self.space_color=c.name()
    def properties(self):
        d=self.owner.doc;spaces=copy.deepcopy(d.spaces)
        for s in spaces:
            if s['id']==d.active_space_id:s.update(name=self.name.text().strip() or '공간',purpose=self.purpose.text().strip() or '미지정',color=self.space_color,opacity=self.opacity.value())
        self.changed(spaces,'space_properties',d.active_space_id)
    def visibility_changed(self,item):
        if not self.restoring and hasattr(self.owner,'space_tools'):self.owner.space_tools.visibility({item.data(Qt.ItemDataRole.UserRole)},item.checkState()==Qt.CheckState.Checked)
    def show_all(self,value):
        if hasattr(self.owner,'space_tools'):self.owner.space_tools.visibility({s['id'] for s in self.owner.doc.spaces},value)
    def display(self,*_):
        if self.restoring:return
        if hasattr(self.owner,'ai_panel'):
            s=self.owner.doc.active_space()
            self.owner.ai_panel.scope_status.setText(f"공간: {s['name']} · {s['purpose']} · 정확한 셀 {sum(b-a+1 for _,_,a,b in s['runs']):,}개 (현재 사각 선택과 교집합)" if s else '일반 사각 선택 · 공간 제한을 쓰려면 공간 관리에서 선택하세요.')
        v=self.owner.view;v.show_blocks=self.blocks.isChecked();v.show_spaces=self.overlays.isChecked()
        scope=self.owner.doc.scope_token()
        rebuild=(v.exact_space_only!=self.only.isChecked() or v.ghost_floors!=self.ghost.isChecked() or self.only.isChecked() and getattr(v,'display_scope',None)!=scope)
        v.display_scope=scope
        v.exact_space_only=self.only.isChecked();v.ghost_floors=self.ghost.isChecked();self.plan.update();v.update()
        if rebuild and v.doc is not None:v.rebuild()
    def open_plan(self):self.owner.tabs.setCurrentWidget(self.plan)
    def focus(self):
        if self.owner.doc.active_space() is None:return
        self.owner.view.focus_selection()
        self.owner.tabs.setCurrentWidget(self.owner.split_tab if self.owner.split_mode.isChecked() else self.owner.view)

    def detect_floors(self):
        d=self.owner.doc;sc=d.schematic
        from .floors import detect_decks
        self.floor_evidence=detect_decks(d);levels=set(self.floor_evidence['levels'])
        self.floor.blockSignals(True);self.floor.clear();self.levels=sorted(levels)
        for y in self.levels:self.floor.addItem(f'층 시작 Y={y}',y)
        target=max([y for y in self.levels if y<=self.owner.layer.value()] or [self.levels[0]]);self.floor.setCurrentIndex(self.levels.index(target));self.floor.blockSignals(False);self.update_floor_range()
    def update_floor_range(self):
        if not getattr(self,'levels',None):return
        y=self.floor.currentData();i=self.levels.index(y);end=self.levels[i+1]-1 if i+1<len(self.levels) else self.owner.doc.schematic.size[1]-1
        self.owner.view.floor_range=(y,end);self.floor_on.blockSignals(True);self.floor_on.setChecked(getattr(self.owner.doc,'floor_visibility',{}).get(str(y),True));self.floor_on.blockSignals(False)
        hidden=getattr(self.owner.doc,'floor_visibility',{});visible=[]
        for j,start in enumerate(self.levels):
            stop=self.levels[j+1] if j+1<len(self.levels) else self.owner.doc.schematic.size[1]
            if hidden.get(str(start),True):visible.extend(range(0 if j==0 else start,stop))
        self.owner.view.visible_y=visible if len(visible)<self.owner.doc.schematic.size[1] else None
    def add_floor(self):
        d=self.owner.doc;d.floor_starts=sorted(set(getattr(d,'floor_starts',[])+[self.owner.layer.value()]));d.dirty=True;self.detect_floors();self.autosave.start();self.status.setText('현재 Y를 사용자 층 기준으로 등록했습니다. 블록은 변경하지 않았습니다.')
    def remove_floor(self):
        d=self.owner.doc;y=self.owner.layer.value();d.floor_starts=[v for v in getattr(d,'floor_starts',[]) if v!=y];d.dirty=True;self.detect_floors();self.autosave.start();self.status.setText('수동 층 기준을 제거했습니다. 구조상 자동 후보는 계속 표시될 수 있습니다.')
    def select_floor(self,*_):
        if self.floor.currentData() is None:return
        self.update_floor_range();self.owner.layer.setValue(self.floor.currentData());self.plan.layer=self.floor.currentData();self.plan.refresh();self.owner.view.rebuild()
    def floor_visibility(self,value):
        if self.restoring or self.floor.currentData() is None:return
        d=self.owner.doc
        if not hasattr(d,'floor_visibility'):d.floor_visibility={}
        d.floor_visibility[str(self.floor.currentData())]=bool(value);d.dirty=True;self.update_floor_range();self.owner.view.rebuild();self.autosave.start()
    def analyze(self):
        d=self.owner.doc;s=d.active_space()
        if s is None:self.status.setText('분석할 공간을 선택하세요.');return
        if self.owner.operation is not None:return
        revision=d.revision
        def work(progress,cancel):
            q=statistics(d,s,cancel);q['connections']=analyze_connections(d,d.spaces,cancel);q['note']='연결은 셀 접촉 후보입니다. 문/계단 통과 가능성과 모드 작동은 사용자가 확인해야 합니다.';return q
        def done(q):
            if self.owner.doc is d and d.revision==revision:self.details.setPlainText(json.dumps(q,ensure_ascii=False,indent=2))
        self.owner.run_operation(work,done,'공간 통계·연결 분석')
    def show_history(self):
        d=self.owner.doc;self.details.setPlainText(json.dumps([h for h in d.history if h.get('space_id')==d.active_space_id],ensure_ascii=False,indent=2))
    def undo_space(self):
        d=self.owner.doc
        if not d.undo_stack:self.status.setText('실행 취소 이력이 없습니다.');return
        r=d.undo_stack[-1]
        if r.get('space_id')!=d.active_space_id:self.status.setText('다른 공간/설정의 후속 작업이 있습니다. 전체 실행 취소로 역순으로 되돌리세요.');return
        self.owner.undo()
