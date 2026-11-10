"""Docking workspace, scene tree, view-only inspection and persistent layout."""
import json,copy,hashlib,time
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt,QTimer,QPointF,QRectF,QByteArray,QItemSelectionModel
from PySide6.QtGui import QAction,QKeySequence,QPen,QColor,QPolygonF
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QDockWidget,QTreeWidget,QTreeWidgetItem,QLineEdit,QComboBox,QSpinBox,QCheckBox,QPushButton,QLabel,QPlainTextEdit,QSplitter,QToolBar,QScrollArea)
from .display import DisplayState,validate_settings
from .axis_plan import AxisPlan
from .space_model import bbox,contains
from .space_display import visible as space_visible
from .model import Bounds
from .schematic import atomic_write
from .navigation import state_parts

ROLE=Qt.ItemDataRole.UserRole
class Workbench:
    def __init__(self,owner):
        self.owner=owner;self.display_exterior=None;self.busy=False;self.signature=None;self.selected_xyz=None;self.selected_kind=None;self.hidden_blocks=set();self.hidden_overlays=set();self.hidden_groups=set();self.evidence=[];self.equipment=[];self.selection_key=None;self.expanded=[];self.use_default_expansion=True
        self.timer=QTimer(owner);self.timer.setSingleShot(True);self.timer.setInterval(350);self.timer.timeout.connect(self.save_display)
        self.axis_plan=AxisPlan(owner);owner.tabs.addTab(self.axis_plan,'다방향 2D')
        self.tree=QTreeWidget();self.tree.setHeaderLabels(['장면 / 대상','상태']);self.tree.header().setStretchLastSection(False);self.tree.setColumnWidth(0,235);self.tree.setColumnWidth(1,115);self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection);self.tree.itemSelectionChanged.connect(self.selected);self.tree.itemChanged.connect(self.on_item_changed)
        container=QWidget();lay=QVBoxLayout(container);row=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('장면 검색: 공간·설비·연결');self.search.textChanged.connect(self.filter);row.addWidget(self.search);self.sort=QComboBox();self.sort.addItems(['층 / 이름','이름','큰 공간 우선']);self.sort.currentIndexChanged.connect(lambda _:self.rebuild_tree(True));row.addWidget(self.sort);lay.addLayout(row);lay.addWidget(self.tree)
        row=QHBoxLayout()
        for text,fn in [('초점 F',self.focus),('목록 합치기',self.merge_selected),('펼치기',self.tree.expandAll),('접기',self.tree.collapseAll)]:
            button=QPushButton(text);button.clicked.connect(fn);row.addWidget(button)
        lay.addLayout(row);self.scene_dock=self.dock('장면 목록','scene_tree',container,Qt.DockWidgetArea.LeftDockWidgetArea)
        # Move the existing editor, rather than duplicate it or its document state.
        self.inspector=QPlainTextEdit();self.inspector.setReadOnly(True);self.inspector.setMaximumHeight(170)
        right=QWidget();r=QVBoxLayout(right);r.addWidget(QLabel('선택 속성 · NBT 읽기 전용'));r.addWidget(self.inspector)
        self.property_name=QLineEdit();self.property_name.setPlaceholderText('선택 공간 이름');self.property_purpose=QLineEdit();self.property_purpose.setPlaceholderText('선택 공간 용도');self.property_alpha=QSpinBox();self.property_alpha.setRange(0,255);self.property_alpha.setPrefix('알파 ');r.addWidget(self.property_name);r.addWidget(self.property_purpose);r.addWidget(self.property_alpha)
        row=QHBoxLayout();apply=QPushButton('공간 속성 적용');apply.clicked.connect(self.apply_properties);row.addWidget(apply);color=QPushButton('공간 색상');color.clicked.connect(owner.space_manager.choose_color);row.addWidget(color);r.addLayout(row)
        owner.panel_scroll.setMaximumWidth(16777215);r.addWidget(owner.panel_scroll,1)
        self.editor_dock=self.dock('선택 속성·편집','properties_editor',right,Qt.DockWidgetArea.RightDockWidgetArea)
        self.view_panel=QWidget();vl=QVBoxLayout(self.view_panel)
        row=QHBoxLayout();self.cut_axis=QComboBox();self.cut_axis.addItems(['none','X','Y','Z']);row.addWidget(QLabel('단면 축'));row.addWidget(self.cut_axis);self.cut_position=QSpinBox();self.cut_position.setRange(0,32767);row.addWidget(self.cut_position);self.cut_side=QComboBox();self.cut_side.addItems(['below','above','slice']);row.addWidget(self.cut_side);vl.addLayout(row)
        self.exterior=QCheckBox('원본 외피 임시 숨김');self.roof=QCheckBox('선택 공간 위 천장 숨김');self.occluders=QCheckBox('카메라 방향의 가림 구조 단면')
        for widget in (self.exterior,self.roof,self.occluders):vl.addWidget(widget)
        row=QHBoxLayout()
        for text,fn in [('고립 /',self.isolate),('내부 관찰',self.interior),('표시 전체 복원 Alt+H',self.restore_visibility)]:
            b=QPushButton(text);b.clicked.connect(fn);row.addWidget(b)
        vl.addLayout(row);self.rule=QLabel('숨긴 블록은 클릭/인접 선택에서 제외합니다. 숨겨진 셀을 실제로 바꾸는 제안은 승인 전에 거부합니다. 경계 그리기는 메타데이터 작업입니다.');self.rule.setWordWrap(True);vl.addWidget(self.rule)
        self.view_dock=self.dock('단면·내부 관찰','section_controls',self.view_panel,Qt.DockWidgetArea.RightDockWidgetArea);owner.tabifyDockWidget(self.editor_dock,self.view_dock);self.editor_dock.raise_()
        self.floating={};self.return_buttons={}
        for kind in ('3D','2D'):
            holder=QWidget();layout=QVBoxLayout(holder);button=QPushButton('중앙으로 돌리기');button.clicked.connect(lambda _,k=kind:self.attach_view(k));layout.addWidget(button)
            dock=self.dock(kind+' 분리 뷰','floating_'+kind,holder,Qt.DockWidgetArea.BottomDockWidgetArea);dock.hide();self.floating[kind]=dock;self.return_buttons[kind]=holder
        self.menu=owner.menuBar().addMenu('작업공간');self.toolmenu=owner.menuBar().addMenu('도구 모음');self.toolmenu.addAction(owner.toolbar.toggleViewAction());self.modebars=[]
        for dock in (self.scene_dock,self.editor_dock,self.view_dock,*self.floating.values()):self.menu.addAction(dock.toggleViewAction())
        for label,fn,shortcut in [('3D 화면 분리',lambda:self.detach_view('3D'),None),('2D 화면 분리',lambda:self.detach_view('2D'),None),('선택 초점',self.focus,'F'),('선택 고립',self.isolate,'/'),('선택 숨김',self.hide_selected,'H'),('표시 전체 복원',self.restore_visibility,'Alt+H'),('카메라 초기화',owner.view.reset_camera,'Home'),('기본 도킹 복원',self.reset_docks,None),('작업공간 저장',self.save,'Ctrl+Shift+L')]:
            action=QAction(label,owner);action.triggered.connect(fn)
            if shortcut:action.setShortcut(QKeySequence(shortcut));action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);owner.addAction(action)
            self.menu.addAction(action)
        for title,tools in [('공간',[('공간 관리',lambda:self.open_tab(owner.space_manager)),('경계 편집',lambda:self.open_tab(owner.space_manager.plan))]),('건축',[('수동 미리보기',owner.make_preview),('AI 설계',owner.ai_panel.show_panel)]),('연결·경로',[('분석 화면',lambda:self.open_tab(owner.network_view)),('연결 분석',owner.network_view.analyze),('경로 찾기',owner.network_view.find),('선택 2공간 경로',self.route_selected)]),('설비',[('설비 분석',owner.network_view.scan_equipment)]),('검증',[('변경 안전 검토',lambda:self.open_tab(owner.review_view)),('진단 ZIP',owner.export_diagnostics)])]:
            bar=QToolBar(title,owner);bar.setObjectName('tools_'+title);bar.setMovable(True);bar.setFloatable(True)
            for text,fn in tools:action=bar.addAction(text);action.triggered.connect(fn)
            owner.addToolBar(bar);self.toolmenu.addAction(bar.toggleViewAction());bar.hide();self.modebars.append(bar)
        self.mode=QComboBox();self.mode.addItems(['통합','공간','건축','연결·경로','설비','검증']);owner.toolbar.addWidget(QLabel('작업 모드'));owner.toolbar.addWidget(self.mode);self.mode.currentTextChanged.connect(self.mode_changed)
        self.tree.itemDoubleClicked.connect(lambda *_:self.focus());self.tree.itemExpanded.connect(self.expand_equipment)
        for widget in (self.cut_axis,self.cut_side):widget.currentIndexChanged.connect(self.apply)
        self.cut_position.valueChanged.connect(self.apply)
        for widget in (self.roof,self.occluders):widget.toggled.connect(self.apply)
        self.exterior.toggled.connect(self.toggle_exterior)
        owner.view.workbench=self;owner.view.display_state=DisplayState(dimensions=owner.doc.schematic.size);owner.view.show_routes=True
        self.isolation=None;self.connect_owner();self.attach_document()
        self.poll=QTimer(owner);self.poll.setInterval(750);self.poll.timeout.connect(self.poll_changes);self.poll.start()
    def shutdown(self):
        self.timer.stop()
        if hasattr(self,'poll'):self.poll.stop()
        for dock in (self.scene_dock,self.editor_dock,self.view_dock,*self.floating.values()):dock.hide()
        for bar in self.modebars:bar.hide()
        self.owner.toolbar.hide()
        if hasattr(self.owner,'integrated_ui'):
            ui=self.owner.integrated_ui;ui.sync_timer.stop();ui.analysis_dock.hide();ui.ribbon_bar.hide()
            if hasattr(ui,'tasks'):ui.tasks.result_dock.hide()

    def poll_changes(self):
        if self.owner.closing:return
        self.rebuild_tree()
        pending=getattr(self,'pending_route',None)
        if pending and self.owner.operation is None:
            ids,identity,started=pending;network=self.owner.network_view
            if identity!=id(self.owner.doc):self.pending_route=None
            elif network.nav is not None and network.snapshot==network.signature():self.pending_route=None;self.route_selected(ids)
            elif time.monotonic()-started>3:self.pending_route=None;network.status.setText('연결 분석을 완료하지 못했습니다. 범위/오류를 확인한 뒤 다시 시도하세요.')

    def route_selected(self,ids=None):
        if not isinstance(ids,(list,tuple)):ids=[item.data(0,ROLE).split(':',1)[1] for item in self.tree.selectedItems() if str(item.data(0,ROLE)).startswith('space:')]
        if len(ids)!=2:self.owner.network_view.status.setText('장면 트리에서 Ctrl로 공간 2개를 선택하세요.');self.open_tab(self.owner.network_view);return
        network=self.owner.network_view
        network.source.setCurrentIndex(network.source.findData(ids[0]));network.target.setCurrentIndex(network.target.findData(ids[1]))
        if network.nav is None or network.snapshot!=network.signature():
            self.pending_route=(list(ids),id(self.owner.doc),time.monotonic());network.analyze();return
        if self.owner.operation is None:network.find()


    def dock(self,title,name,widget,area):
        dock=QDockWidget(title,self.owner);dock.setObjectName(name);dock.setWidget(widget);dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas);dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable|QDockWidget.DockWidgetFeature.DockWidgetMovable|QDockWidget.DockWidgetFeature.DockWidgetFloatable);self.owner.addDockWidget(area,dock);return dock
    def open_tab(self,widget):
        if hasattr(self.owner,'integrated_ui'):
            route=self.owner.tabs.routes.get(widget)
            if route is not None:return route()
        drawing=self.axis_plan if hasattr(self.owner,'integrated_ui') else self.owner.space_manager.plan
        if widget is self.owner.view and self.floating['3D'].widget().layout().indexOf(widget)>=0:self.floating['3D'].show();self.floating['3D'].raise_();return
        if widget is drawing and self.floating['2D'].widget().layout().indexOf(widget)>=0:self.floating['2D'].show();self.floating['2D'].raise_();return
        if widget in (self.owner.view,drawing) and self.owner.split_mode.isChecked():self.owner.tabs.setCurrentWidget(self.owner.split_tab)
        else:self.owner.tabs.setCurrentWidget(widget)
    def mode_changed(self,title):
        for bar in self.modebars:bar.setVisible(bar.windowTitle()==title)
        if title=='공간':self.scene_dock.show()
        if title=='연결·경로':self.open_tab(self.owner.network_view)
        if title=='설비':self.open_tab(self.owner.network_view)
    def detach_view(self,kind):
        widget=self.owner.view if kind=='3D' else self.axis_plan if hasattr(self.owner,'integrated_ui') else self.owner.space_manager.plan;dock=self.floating[kind]
        at=self.owner.tabs.indexOf(widget)
        if at>=0:self.owner.tabs.removeTab(at)
        dock.widget().layout().addWidget(widget);widget.show();dock.setFloating(True);dock.resize(850,650);dock.show();dock.raise_();self.save()
    def attach_view(self,kind):
        widget=self.owner.view if kind=='3D' else self.axis_plan if hasattr(self.owner,'integrated_ui') else self.owner.space_manager.plan;widget.setParent(None)
        if self.owner.split_mode.isChecked():self.owner.design_splitter.addWidget(widget);widget.show();self.owner.tabs.setCurrentWidget(self.owner.split_tab)
        else:self.owner.tabs.insertTab(0 if kind=='3D' else 1,widget,'3D 함선' if kind=='3D' else '공통 도면 편집기');self.owner.tabs.setCurrentWidget(widget)
        self.floating[kind].hide();self.owner.view.schedule_render();self.save()
    def reset_docks(self):
        for kind in self.floating:
            if self.return_buttons[kind].layout().count()>1:self.attach_view(kind)
        for dock,area in ((self.scene_dock,Qt.DockWidgetArea.LeftDockWidgetArea),(self.editor_dock,Qt.DockWidgetArea.RightDockWidgetArea),(self.view_dock,Qt.DockWidgetArea.RightDockWidgetArea)):
            dock.setFloating(False);self.owner.addDockWidget(area,dock);dock.show()
        self.owner.tabifyDockWidget(self.editor_dock,self.view_dock);self.editor_dock.raise_();
        if hasattr(self.owner,'integrated_ui'):
            dock=self.owner.integrated_ui.analysis_dock;dock.setFloating(False);self.owner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,dock);self.owner.tabifyDockWidget(self.editor_dock,dock);dock.hide()
        if hasattr(self.owner,'integrated_ui') and hasattr(self.owner.integrated_ui,'tasks'):
            dock=self.owner.integrated_ui.tasks.result_dock;dock.setFloating(False);self.owner.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,dock);dock.hide()
        self.owner.resizeDocks([self.scene_dock,self.editor_dock],[280,360],Qt.Orientation.Horizontal)
    def save(self):
        try:
            payload={'state':bytes(self.owner.saveState(91).toBase64()).decode(),'geometry':bytes(self.owner.saveGeometry().toBase64()).decode(),'floating_views':[k for k,h in self.return_buttons.items() if h.layout().count()>1],'mode':self.mode.currentText(),'expanded':self.expanded_keys(),'tree_header':bytes(self.tree.header().saveState().toBase64()).decode()}
            atomic_write(self.owner.data_dir/'workspace091.json',json.dumps(payload,ensure_ascii=False).encode());self.save_display()
        except OSError as exc:self.owner.statusBar().showMessage('작업공간 저장 실패: '+str(exc))
    def restore(self):
        try:
            p=json.loads((self.owner.data_dir/'workspace091.json').read_text())
            for kind in p.get('floating_views',[]):
                if kind in self.floating:self.detach_view(kind)
            self.owner.restoreGeometry(QByteArray.fromBase64(p['geometry'].encode()));self.mode.setCurrentText(p.get('mode','통합'));
            if p.get('tree_header'):self.tree.header().restoreState(QByteArray.fromBase64(p['tree_header'].encode()))
            self.owner.restoreState(QByteArray.fromBase64(p['state'].encode()),91);self.expanded=p.get('expanded',[]);self.rebuild_tree(True)
            # Keep a recovery path when a previously used monitor is absent.
            from PySide6.QtGui import QGuiApplication
            for dock in (self.scene_dock,self.editor_dock,self.view_dock,*self.floating.values(),*([self.owner.integrated_ui.analysis_dock] if hasattr(self.owner,'integrated_ui') else []),*([self.owner.integrated_ui.tasks.result_dock] if hasattr(self.owner,'integrated_ui') and hasattr(self.owner.integrated_ui,'tasks') else [])):
                if dock.isFloating() and not any(s.availableGeometry().intersects(dock.frameGeometry()) for s in QGuiApplication.screens()):dock.move(QGuiApplication.primaryScreen().availableGeometry().topLeft())
        except (OSError,ValueError,KeyError,TypeError):self.owner.resizeDocks([self.scene_dock,self.editor_dock],[280,360],Qt.Orientation.Horizontal)
    def display_path(self):return self.owner.data_dir/('display091-'+self.owner.doc.project_id+'.json')
    def save_display(self):
        if self.busy:return
        payload=self.settings();self.owner.doc.view_settings=payload
        try:atomic_write(self.display_path(),json.dumps({'size':self.owner.doc.schematic.size,'settings':payload},ensure_ascii=False).encode())
        except OSError as e:self.owner.statusBar().showMessage('표시 설정 저장 실패: '+str(e))
    def settings(self):return {'space_presentation':getattr(self.owner.view,'space_display',{}),'axis':self.cut_axis.currentText(),'position':self.cut_position.value(),'side':self.cut_side.currentText(),'exterior':self.exterior.isChecked(),'roof':self.roof.isChecked(),'occluders':self.occluders.isChecked(),'hidden':sorted(self.hidden_blocks),'hidden_overlays':sorted(self.hidden_overlays),'hidden_groups':sorted(self.hidden_groups),'isolation':self.isolation,'selected':self.selection_key,'selected_keys':[i.data(0,ROLE) for i in self.tree.selectedItems() if i.data(0,ROLE)],'selected_xyz':self.selected_xyz,'selected_kind':self.selected_kind,'layer':self.owner.layer.value(),'plane_orientation':self.axis_plan.orientation.currentText(),'plane_slice':self.axis_plan.slice.value(),'plane_projection':self.axis_plan.projection.isChecked(),'show_blocks':self.owner.view.show_blocks,'show_spaces':self.owner.view.show_spaces,'only_space':self.owner.space_manager.only.isChecked(),'ghost_floors':self.owner.space_manager.ghost.isChecked(),'legacy_cut':self.owner.clip.isChecked(),'single_layer':self.owner.single_layer.isChecked(),'zone_clip':self.owner.zone_clip.isChecked(),'expanded':self.expanded_keys()}
    def attach_document(self):
        self.busy=True;self.tree.clear();self.signature=None;self.pending_route=None;self.selected_xyz=None;self.selection_key=None;self.isolation=None;self.display_exterior=None;self.hidden_blocks=set();self.hidden_overlays=set();self.hidden_groups=set();self.evidence=[];self.equipment=[]
        p=getattr(self.owner.doc,'view_settings',{})
        try:
            q=json.loads(self.display_path().read_text())
            if q.get('size')==list(self.owner.doc.schematic.size):p=q['settings']
        except (OSError,ValueError,KeyError,TypeError):pass
        p=validate_settings(p,self.owner.doc.schematic.size)
        self.owner.doc.view_settings=p
        manager=self.owner.space_manager
        for widget,key,default in ((manager.blocks,'show_blocks',True),(manager.overlays,'show_spaces',True),(manager.only,'only_space',False),(manager.ghost,'ghost_floors',False),(self.owner.clip,'legacy_cut',False),(self.owner.single_layer,'single_layer',False),(self.owner.zone_clip,'zone_clip',False)):
            widget.blockSignals(True);widget.setChecked(p.get(key,default));widget.blockSignals(False)
        self.owner.view.show_blocks=manager.blocks.isChecked();self.owner.view.show_spaces=manager.overlays.isChecked();self.owner.view.exact_space_only=manager.only.isChecked();self.owner.view.ghost_floors=manager.ghost.isChecked();self.owner.view.cut=self.owner.clip.isChecked();self.owner.view.single_layer=self.owner.single_layer.isChecked();self.owner.view.zone_only=self.owner.zone_clip.isChecked()
        self.cut_axis.setCurrentText(p.get('axis','none'));self.cut_position.setValue(max(0,min(max(self.owner.doc.schematic.size)-1,int(p.get('position',0)))));self.cut_side.setCurrentText(p.get('side','below'));self.exterior.setChecked(bool(p.get('exterior',False)));self.roof.setChecked(bool(p.get('roof',False)));self.occluders.setChecked(bool(p.get('occluders',False)))
        self.hidden_blocks={i for i in p.get('hidden',[]) if type(i) is int and 0<=i<self.owner.doc.schematic.blocks.size};self.hidden_overlays=set(p.get('hidden_overlays',[]));self.hidden_groups=set(p.get('hidden_groups',[]));self.selection_key=p.get('selected');self.restore_selected_keys=p.get('selected_keys',[]);self.expanded=p.get('expanded',[]);self.use_default_expansion='expanded' not in p
        xyz=p.get('selected_xyz')
        if isinstance(xyz,(list,tuple)) and len(xyz)==3 and all(type(v) is int and 0<=v<n for v,n in zip(xyz,self.owner.doc.schematic.size)):self.selected_xyz=tuple(xyz);self.selected_kind=p.get('selected_kind','block')
        isolation=p.get('isolation')
        if isolation:
            try:b=Bounds(tuple(isolation[0]),tuple(isolation[1]));b.validate(self.owner.doc.schematic.size);self.isolation=(b.lo,b.hi)
            except (ValueError,TypeError):pass
        self.owner.view.display_state=DisplayState(dimensions=self.owner.doc.schematic.size)
        space=self.owner.doc.active_space();default_y=min(r[0] for r in space['runs']) if space else 0
        self.owner.layer.setValue(max(0,min(self.owner.doc.schematic.size[1]-1,int(p.get('layer',default_y)))))
        self.busy=False;self.owner.view.route_path=[];self.axis_plan.orientation.setCurrentText(p.get('plane_orientation','평면도 (위)'));self.axis_plan.orientation_changed();self.axis_plan.slice.setValue(int(p.get('plane_slice',self.owner.layer.value())));self.axis_plan.projection.setChecked(bool(p.get('plane_projection',False)));self.apply(reject=False);self.rebuild_tree(True)
        self.inspector.clear();self.property_name.clear();self.property_purpose.clear()
        if self.selected_xyz:self.block_selected(self.selected_xyz)
        else:self.inspect_space()
        if 'expanded' in p:
            self.busy=True;self.tree.collapseAll()
            for key in p['expanded']:
                if key in self.nodes:self.nodes[key].setExpanded(True)
            self.busy=False
    def snapshot(self):
        d=self.owner.doc;s=d.active_space();roof_y=bbox(s)[1][1] if s else self.owner.layer.value()
        return DisplayState(self.cut_axis.currentText(),self.cut_position.value(),self.cut_side.currentText(),self.exterior.isChecked() and (d.exterior is not None or self.display_exterior is not None),self.roof.isChecked(),self.occluders.isChecked(),tuple(sorted(self.hidden_blocks)),self.isolation,d.exterior if d.exterior is not None else self.display_exterior,d.schematic.size,roof_y)
    def apply(self,*_,reject=True):
        if self.busy:return
        axis=self.cut_axis.currentText();self.cut_position.blockSignals(True);self.cut_position.setRange(0,self.owner.doc.schematic.size['XYZ'.index(axis)]-1 if axis in ('X','Y','Z') else max(self.owner.doc.schematic.size)-1);self.cut_position.blockSignals(False)
        if reject and self.owner.proposal is not None:self.owner.reject()
        self.owner.view.display_state=self.snapshot();self.owner.view.show_routes='routes' not in self.hidden_groups and 'route:current' not in self.hidden_overlays;self.owner.view.rebuild();self.axis_plan.refresh();self.owner.space_manager.plan.refresh();self.timer.start()
    def toggle_exterior(self,value):
        if self.busy:return
        d=self.owner.doc
        if value and d.exterior is None:
            if self.owner.operation is not None:self.exterior.blockSignals(True);self.exterior.setChecked(False);self.exterior.blockSignals(False);return
            def work(progress,cancel):
                from .protection import build_exterior
                return d.prepare_protection(progress,cancel) if d.source else build_exterior(d.schematic,progress,cancel)
            def ready(result):
                if self.owner.doc is d:self.display_exterior=result;self.apply()
            self.owner.run_operation(work,ready,'외피 표시 마스크 분석 · 데이터 변경 없음')
        else:self.apply()
    def restore_visibility(self):
        self.owner.view.validation_spaces=[]
        self.busy=True
        for w in (self.exterior,self.roof,self.occluders):w.setChecked(False)
        self.cut_axis.setCurrentText('none');self.hidden_blocks.clear();self.hidden_overlays.clear();self.hidden_groups.clear();self.isolation=None;
        if hasattr(self.owner,'space_tools'):self.owner.view.space_display['visibility']={};self.owner.space_tools.only.setChecked(False)
        self.busy=False
        self.owner.clip.setChecked(False);self.owner.single_layer.setChecked(False);self.owner.zone_clip.setChecked(False);self.owner.space_manager.only.setChecked(False);self.owner.space_manager.ghost.setChecked(False);self.owner.space_manager.blocks.setChecked(True);self.owner.space_manager.overlays.setChecked(True)
        self.owner.doc.floor_visibility={};self.owner.space_manager.update_floor_range();self.owner.space_manager.show_all(True);self.apply();self.rebuild_tree(True)
    def isolate(self):
        if self.selected_xyz:
            low=high=self.selected_xyz;self.owner.space_manager.only.setChecked(False)
        elif self.owner.doc.active_space() is not None:low,high=bbox(self.owner.doc.active_space());self.owner.space_manager.only.setChecked(True)
        else:return
        self.isolation=(tuple(low),tuple(high));self.apply()
    def interior(self):
        if self.owner.doc.active_space() is None:return
        self.roof.setChecked(True);self.occluders.setChecked(True);self.owner.view.focus_selection();self.apply();self.open_tab(self.owner.view)
    def focus(self):
        record=self.records.get(self.selection_key,{})
        if len(record.get('positions',[]))>1:
            points=np.asarray(record['positions']);self.owner.view.focus_bounds=Bounds(tuple(int(max(0,v-1)) for v in points.min(axis=0)),tuple(int(min(n-1,v+1)) for n,v in zip(self.owner.doc.schematic.size,points.max(axis=0))));self.owner.view.zoom=1.;self.owner.view.pan[:]=0;self.owner.view.schedule_render()
        elif self.selected_xyz:
            xyz=self.selected_xyz;self.owner.view.focus_bounds=Bounds(tuple(max(0,v-2) for v in xyz),tuple(min(dim-1,v+2) for dim,v in zip(self.owner.doc.schematic.size,xyz)));self.owner.view.zoom=1.;self.owner.view.pan[:]=0;self.owner.view.schedule_render()
        else:self.owner.view.focus_selection()
        self.open_tab(self.owner.view)
    def hide_selected(self):
        if self.selected_xyz:
            w,h,l=self.owner.doc.schematic.size;x,y,z=self.selected_xyz;self.hidden_blocks.add(y*w*l+z*w+x);self.apply()
        else:self.owner.space_tools.visibility(self.owner.space_tools.ids(),False)
        self.rebuild_tree(True)
    def guard(self,p):
        if not len(p.indices):return True
        if not self.owner.view.display_state.visible_indices(p.indices,self.owner.doc,self.owner.view).all():
            raise ValueError('숨김·단면·고립으로 보이지 않는 변경 셀이 있습니다. 표시 전체 복원(Alt+H) 후 미리보기/승인하세요. 실제 블록은 바꾸지 않았습니다.')
        return True
    def expanded_keys(self):
        out=[]
        def walk(item):
            for i in range(item.childCount()):
                child=item.child(i)
                if child.isExpanded() and child.data(0,ROLE):out.append(child.data(0,ROLE))
                walk(child)
        walk(self.tree.invisibleRootItem());return out
    def node(self,parent,text,key,checked=True,status=''):
        item=QTreeWidgetItem(parent,[text,status]);item.setData(0,ROLE,key);item.setToolTip(0,text);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(0,Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        if hasattr(self,'nodes'):self.nodes[key]=item
        return item
    def rebuild_tree(self,force=False):
        if getattr(self,'_changing_item',False):return
        if self.busy:return
        d=self.owner.doc;nav=self.owner.network_view;signature=(id(d),d.revision,id(nav.nav),id(nav.equipment),tuple(getattr(self.owner.view,'route_path',[])[0]) if getattr(self.owner.view,'route_path',[]) else None)
        if not force and signature==self.signature:return
        self.signature=signature;expanded=self.expanded_keys() if self.tree.topLevelItemCount() else self.expanded;selected_keys={i.data(0,ROLE) for i in self.tree.selectedItems()} or set(getattr(self,'restore_selected_keys',[]));self.busy=True;self.tree.clear();self.nodes={};self.records={}
        root=self.tree.invisibleRootItem();spaces=self.node(root,'공간','spaces',self.owner.view.show_spaces);floors=self.node(root,'층','floors',any(d.floor_visibility.get(str(y),True) for y in self.owner.space_manager.levels));blocks=self.node(root,'실제 블록','blocks',self.owner.view.show_blocks)
        routes=self.node(root,'경로','routes','routes' not in self.hidden_groups);connections=self.node(root,'연결 후보','connections','connections' not in self.hidden_groups);equipment=self.node(root,'설비','equipment','equipment' not in self.hidden_groups)
        if not nav.equipment:equipment.setDisabled(True);equipment.setText(1,'설비 분석 필요')
        candidate_root=self.node(root,'인식 후보','candidates',getattr(self.owner.view,'space_display',{}).get('show_candidates',True));candidate_groups={}
        from .space_display import external as external_space
        for c in d.space_candidates:
            label='외부·큰 미분리' if external_space(c,d) else {'auto':'자동 등록 가능','registered':'등록됨','review':'검토 필요','exclude':'제외'}.get(c.get('decision'),'이전 인식')
            if label not in candidate_groups:candidate_groups[label]=self.node(candidate_root,label,'candgroup:'+label)
            key='candidate:'+c['id'];self.node(candidate_groups[label],c['id']+' · '+str(c.get('volume',c.get('area',0)))+'셀',key,space_visible(self.owner.view,c,True),label);self.records[key]=c
        order=sorted(d.spaces,key=lambda s:(bbox(s)[0][1],s['name']) if self.sort.currentIndex()==0 else s['name'] if self.sort.currentIndex()==1 else -sum(b-a+1 for _,_,a,b in s['runs']))
        groups={}
        for s in order:
            y=bbox(s)[0][1]
            if y not in groups:groups[y]=self.node(spaces,'층 Y='+str(y),'spacefloor:'+str(y))
            key='space:'+s['id'];self.nodes[key]=self.node(groups[y],s['name'],key,space_visible(self.owner.view,s),s['purpose']);self.records[key]=s
        for y in self.owner.space_manager.levels:self.nodes['floor:'+str(y)]=self.node(floors,'Y='+str(y),'floor:'+str(y),d.floor_visibility.get(str(y),True))
        self.nodes['route:current']=self.node(routes,'현재 경로','route:current','route:current' not in self.hidden_overlays,'표시' if getattr(self.owner.view,'route_path',[]) else '경로 찾기 필요')
        path=getattr(self.owner.view,'route_path',[])
        if path:self.records['route:current']={'positions':path,'status':'통행 모델 경로','points':len(path)}
        self.equipment=(nav.equipment or {}).get('records',[]);categories={};self.equip_types={};self.equip_type_nodes={}
        for record in self.equipment:self.equip_types.setdefault(record['block'],[]).append(record)
        for state,records in sorted(self.equip_types.items()):
            category=records[0]['category']
            if category not in categories:categories[category]=self.node(equipment,category,'equipgroup:'+category,'equipgroup:'+category not in self.hidden_groups)
            total=(nav.equipment or {}).get('by_block',{}).get(state,len(records));group=self.node(categories[category],state+' · '+str(total)+'블록','equiptype:'+state,'equiptype:'+state not in self.hidden_groups and 'equipgroup:'+category not in self.hidden_groups,'대표 '+str(len(records))+'좌표 · 전체 좌표는 설비 검색/페이지');self.equip_type_nodes[state]=group
            QTreeWidgetItem(group,['좌표 펼치기',''])
            for record in records:
                key='equip:'+','.join(map(str,record['position']));self.records[key]=record
        for record in self.evidence[:4096]:
            key=record['key'];self.nodes[key]=self.node(connections,record['label'],key,key not in self.hidden_overlays,record['status']);self.records[key]=record
        def expand(item):
            for i in range(item.childCount()):
                child=item.child(i);child.setExpanded(child.data(0,ROLE) in expanded or getattr(self,'use_default_expansion',False) and child.data(0,ROLE)=='spaces');expand(child)
        expand(root);self.use_default_expansion=False
        for key in selected_keys:
            if key in self.nodes:self.nodes[key].setSelected(True)
        self.busy=False;self.filter();self.sync_selection()
    def expand_equipment(self,item):
        key=item.data(0,ROLE)
        if not isinstance(key,str) or not key.startswith('equiptype:'):return
        if item.childCount() and item.child(0).data(0,ROLE):return
        was_busy=self.busy;self.busy=True;item.takeChildren();state=key.split(':',1)[1];records=self.equip_types.get(state,[]);w,h,l=self.owner.doc.schematic.size
        for record in records[:2000]:
            x,y,z=record['position'];index=y*w*l+z*w+x;key='equip:'+','.join(map(str,[x,y,z]));self.nodes[key]=self.node(item,str([x,y,z]),key,index not in self.hidden_blocks and key not in self.hidden_overlays,'작동 미확인')
        total=(self.owner.network_view.equipment or {}).get('by_block',{}).get(state,len(records))
        if total>min(2000,len(records)):QTreeWidgetItem(item,[f'대표 좌표 {min(2000,len(records))}/{total} · 전체는 설비 페이지 검색',''])
        self.busy=was_busy
    def filter(self,*_):
        query=self.search.text().casefold()
        def visit(item):
            any_child=False
            for i in range(item.childCount()):any_child=visit(item.child(i)) or any_child
            match=query in item.text(0).casefold() or query in item.text(1).casefold() or any_child
            item.setHidden(not match);return match
        for i in range(self.tree.topLevelItemCount()):visit(self.tree.topLevelItem(i))
    def on_item_changed(self,item,column=0):
        if self.busy:return
        self._changing_item=True
        try:self.checked(item,column)
        finally:self._changing_item=False
        QTimer.singleShot(0,lambda:self.rebuild_tree(True) if not self.owner.closing else None)

    def checked(self,item,column):
        if self.busy or column!=0:return
        key=item.data(0,ROLE);value=item.checkState(0)==Qt.CheckState.Checked;d=self.owner.doc
        if key=='spaces':self.owner.space_manager.overlays.setChecked(value)
        elif key=='blocks':self.owner.space_manager.blocks.setChecked(value)
        elif key=='floors':
            d.floor_visibility={str(y):value for y in self.owner.space_manager.levels};self.owner.space_manager.update_floor_range();self.apply()
        elif key.startswith('space:') or key.startswith('spacefloor:'):
            ids={s['id'] for s in d.spaces if key=='space:'+s['id'] or key=='spacefloor:'+str(bbox(s)[0][1])}
            self.owner.space_tools.visibility(ids,value)
        elif key=='candidates':
            self.owner.view.space_display['show_candidates']=value;self.owner.space_tools.refresh()
        elif key.startswith('candidate:') or key.startswith('candgroup:'):
            ids={c['id'] for c in d.space_candidates if key=='candidate:'+c['id'] or key.startswith('candgroup:') and self.nodes.get('candidate:'+c['id']).parent().data(0,ROLE)==key}
            self.owner.space_tools.visibility(ids,value)
        elif key.startswith('floor:'):
            d.floor_visibility[key.split(':')[1]]=value;self.owner.space_manager.update_floor_range();self.apply()
        elif key.startswith('equip:'):
            record=self.records[key];x,y,z=record['position'];w,h,l=d.schematic.size;idx=y*w*l+z*w+x
            if value:self.hidden_blocks.discard(idx);self.hidden_overlays.discard(key)
            else:self.hidden_blocks.add(idx);self.hidden_overlays.add(key)
            self.apply()
        elif key.startswith('equipgroup:') or key.startswith('equiptype:'):
            w,h,l=d.schematic.size
            if value:self.hidden_groups.discard(key)
            else:self.hidden_groups.add(key)
            from .equipment import classification as equipment_category
            inv=self.owner.network_view.equipment or {};runs_by_state=inv.get('position_runs',{})
            for state,runs in runs_by_state.items():
                cat=equipment_category(self.owner.doc,state)
                if key.startswith('equipgroup:') and key.split(':',1)[1]!=cat or key.startswith('equiptype:') and key.split(':',1)[1]!=state:continue
                for y,z,a,b in runs:
                    for x in range(a,b+1):
                        idx=y*w*l+z*w+x;overlay='equip:'+','.join(map(str,[x,y,z]))
                        if value:self.hidden_blocks.discard(idx);self.hidden_overlays.discard(overlay)
                        else:self.hidden_blocks.add(idx);self.hidden_overlays.add(overlay)
            self.apply()
        elif key in ('routes','connections','equipment'):
            if key=='equipment':
                w,h,l=d.schematic.size
                if value:self.hidden_groups={k for k in self.hidden_groups if not k.startswith(('equiptype:','equipgroup:'))}
                for runs in (self.owner.network_view.equipment or {}).get('position_runs',{}).values():
                    for y,z,a,b in runs:
                        for x in range(a,b+1):
                            index=y*w*l+z*w+x;overlay='equip:'+','.join(map(str,[x,y,z]))
                            if value:self.hidden_blocks.discard(index);self.hidden_overlays.discard(overlay)
                            else:self.hidden_blocks.add(index);self.hidden_overlays.add(overlay)
            if value:self.hidden_groups.discard(key)
            else:self.hidden_groups.add(key)
            self.apply()
        else:
            if value:self.hidden_overlays.discard(key)
            else:self.hidden_overlays.add(key)
            self.apply()
        self.rebuild_tree(True)
    def selected(self):
        if self.busy:return
        items=self.tree.selectedItems();self.restore_selected_keys=[i.data(0,ROLE) for i in items if i.data(0,ROLE)]
        if not items:return
        item=self.tree.currentItem() or items[-1];key=item.data(0,ROLE);self.selection_key=key
        if key and key.startswith('space:'):
            self.selected_xyz=None;self.selected_kind='space';self.owner.space_manager.select_id(key.split(':',1)[1]);self.inspect_space()
        elif key and key.startswith('candidate:'):
            panel=self.owner.space_view;panel.bulk.group.setCurrentIndex(0);panel.bulk.reason.setCurrentIndex(0)
            for i in range(panel.list.count()):
                if panel.list.item(i).data(ROLE)==key.split(':',1)[1]:panel.list.setCurrentRow(i);break
            self.inspector.setPlainText(json.dumps({k:v for k,v in self.records[key].items() if k not in ('volume_runs','footprint')},ensure_ascii=False,indent=2))
        elif key in self.records:
            record=self.records[key];self.selected_kind='equipment' if key.startswith('equip:') else 'connection';xyz=record.get('position') or record.get('positions',[None])[0]
            if xyz:self.selected_xyz=tuple(xyz);self.owner.layer.setValue(xyz[1])
            self.inspector.setPlainText(json.dumps({k:(v[:64] if k=='positions' else v) for k,v in record.items()},ensure_ascii=False,indent=2))
            if key=='route:current':
                points=np.asarray(record['positions']);self.owner.view.focus_bounds=Bounds(tuple(map(int,points.min(axis=0))),tuple(map(int,points.max(axis=0))));self.owner.view.zoom=.85;self.owner.view.pan[:]=0;self.owner.view.schedule_render()
            self.owner.view.update();self.axis_plan.refresh()
            if xyz:self.focus()
        self.timer.start()
        if self.mode.currentText()=='연결·경로':
            ids=[i.data(0,ROLE).split(':',1)[1] for i in self.tree.selectedItems() if str(i.data(0,ROLE)).startswith('space:')]
            if len(ids)==2:self.route_selected(ids)
    def merge_selected(self):
        ids={item.data(0,ROLE).split(':',1)[1] for item in self.tree.selectedItems() if str(item.data(0,ROLE)).startswith('space:')};manager=self.owner.space_manager
        if len(ids)<2:return
        manager.search.clear();manager.sort.setCurrentText('기존 순서');manager.list.clearSelection()
        for i in range(manager.list.count()):
            item=manager.list.item(i)
            if item.data(ROLE) in ids:item.setSelected(True)
        manager.merge()
    def sync_selection(self):
        if self.busy or getattr(self,'_changing_item',False):return
        key='space:'+str(self.owner.doc.active_space_id)
        if self.selected_xyz is None and key in self.nodes:
            self.busy=True;self.tree.setCurrentItem(self.nodes[key],0,QItemSelectionModel.SelectionFlag.NoUpdate);self.busy=False
    def apply_properties(self):
        manager=self.owner.space_manager
        if self.owner.doc.active_space() is None:return
        manager.name.setText(self.property_name.text());manager.purpose.setText(self.property_purpose.text());manager.opacity.setValue(self.property_alpha.value());manager.properties();self.inspect_space()
    def inspect_space(self):
        space=self.owner.doc.active_space()
        if space:
            self.property_name.setText(space['name']);self.property_purpose.setText(space['purpose']);self.property_alpha.setValue(space['opacity'])
            self.inspector.setPlainText(json.dumps({k:v for k,v in space.items() if k!='runs'}|{'정확한 셀':sum(b-a+1 for _,_,a,b in space['runs']),'구간수':len(space['runs']),'bbox':bbox(space)},ensure_ascii=False,indent=2))
    def nbt_info(self,xyz):
        sc=self.owner.doc.schematic;container=sc.root['Blocks'] if sc.version==3 else sc.root
        for entity in container.get('BlockEntities',[]):
            pos=entity.get('Pos')
            if pos is None or tuple(map(int,pos))!=tuple(xyz):continue
            out={}
            for key,value in list(entity.items())[:40]:
                if isinstance(value,(dict,list,np.ndarray)):out[str(key)]={'type':type(value).__name__,'count':len(value)}
                else:out[str(key)]=str(value)[:1000]
            return out
        return None
    def block_selected(self,xyz):
        self.selected_xyz=tuple(xyz);self.selected_kind='block';self.selection_key='block:'+','.join(map(str,xyz));d=self.owner.doc;sc=d.schematic;state=sc.palette[int(sc.blocks[xyz[1],xyz[2],xyz[0]])]
        self.inspector.setPlainText(json.dumps({'XYZ':list(xyz),'block_state':state,'protection':d.protection_at(xyz),'BlockEntity':xyz in sc.entity_positions()[0],'NBT 읽기 전용':self.nbt_info(xyz),'주의':'속성은 읽기 전용. 실제 변경은 미리보기·승인 필요'},ensure_ascii=False,indent=2));key='equip:'+','.join(map(str,xyz))
        if key not in self.nodes:
            record=next((r for r in self.equipment if tuple(r['position'])==tuple(xyz)),None)
            if record and record['block'] in self.equip_type_nodes:
                group=self.equip_type_nodes[record['block']];self.expand_equipment(group);group.setExpanded(True)
        if key in self.nodes:self.busy=True;self.tree.setCurrentItem(self.nodes[key]);self.busy=False
        self.owner.view.update();self.axis_plan.refresh();self.timer.start()
    def collect_evidence(self):
        nav=self.owner.network_view;d=self.owner.doc
        if nav.nav is None:self.evidence=[];return
        out=[];sc=d.schematic
        from .chunks import regions,slices
        ids={i:state for i,state in sc.palette.items() if state.split('[')[0].endswith(('_door','_stairs'))}
        for lo,hi in regions(sc.blocks.shape):
            a=sc.blocks[slices(lo,hi)]
            for Y,Z,X in np.argwhere(np.isin(a,list(ids))):
                if len(out)>=4096:break
                xyz=[int(X+lo[0]),int(Y+lo[1]),int(Z+lo[2])];state=ids[int(a[Y,Z,X])];name,props=state_parts(state)
                if props.get('half')=='upper':continue
                label='계단' if name.endswith('_stairs') else '문';status='불확실' if not name.startswith('minecraft:') else '닫힘' if props.get('open')=='false' else '상태 확인·통행 모델 별도'
                out.append({'key':'connection:'+','.join(map(str,xyz)),'position':xyz,'label':label+' '+str(xyz),'block':state,'status':status,'props':props})
            if len(out)>=4096:break
        for record in (nav.equipment or {}).get('elevator_candidates',[])[:512]:
            key='lift:'+hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()[:16];out.append(dict(record,key=key,label='승강기 층 연결 후보',status='불확실'))
        self.evidence=out
    def connect_owner(self):
        # Hooks keep original UI entry points alive and share a single document/view state.
        manager=self.owner.space_manager;original=manager.refresh
        def refresh(*args,**kwargs):
            result=original(*args,**kwargs)
            if hasattr(self.owner,'workbench'):self.rebuild_tree();self.sync_selection();self.axis_plan.refresh()
            return result
        manager.refresh=refresh
        original_select=manager.select_id
        def select(sid,*args,**kwargs):
            self.selected_xyz=None;result=original_select(sid,*args,**kwargs)
            space=self.owner.doc.active_space()
            if space:
                self.owner.layer.setValue(min(r[0] for r in space['runs']))
                if self.isolation is not None:self.isolation=bbox(space)
                if self.roof.isChecked() or self.occluders.isChecked() or self.isolation is not None:self.apply(reject=False)
            self.axis_plan.focus_space();self.sync_selection();self.inspect_space();return result
        manager.select_id=select
        original_list=self.owner.network_view.list_connections
        def update(*args,**kwargs):
            r=original_list(*args,**kwargs)
            if hasattr(self.owner,'workbench'):
                signature=(id(self.owner.network_view.nav),id(self.owner.network_view.equipment))
                if signature!=getattr(self,'evidence_signature',None):self.evidence_signature=signature;self.collect_evidence();self.rebuild_tree(True)
            return r
        old_equipment_selected=self.owner.network_view.equipment_selected
        def equipment_selected():
            old_equipment_selected();network=self.owner.network_view;row=network.table.currentRow()
            if 0<=row<len(getattr(network,'visible_records',[])):self.block_selected(tuple(network.visible_records[row]['position']))
        self.owner.network_view.table.itemSelectionChanged.disconnect();self.owner.network_view.table.itemSelectionChanged.connect(equipment_selected)
        self.owner.network_view.source.currentIndexChanged.disconnect()
        self.owner.network_view.list_connections=update;self.owner.network_view.source.currentIndexChanged.connect(update)
    def draw_3d(self,painter):
        view=self.owner.view
        if self.selected_xyz:
            xyz=np.array(self.selected_xyz,float);painter.setPen(QPen(QColor('#ffdf76'),2));view.box(painter,Bounds(tuple(map(int,xyz)),tuple(map(int,xyz))))
        if view.interaction_active:return
        if 'equipment' not in self.hidden_groups:
            for record in self.equipment[:2000]:
                key='equip:'+','.join(map(str,record['position']))
                if key in self.hidden_overlays:continue
                xyz=record['position']
                if not view.display_state.contains_xyz(xyz,self.owner.doc,view):continue
                point=view.project([np.array(xyz)+.5])[0];painter.setPen(QPen(QColor('#cd97ec'),1.5));painter.drawEllipse(QPointF(*point),3,3)
        if 'connections' not in self.hidden_groups:
            for record in self.evidence:
                if record['key'] in self.hidden_overlays:continue
                points=record.get('positions') or [record['position']]
                if not any(view.display_state.contains_xyz(xyz,self.owner.doc,view) for xyz in points):continue
                pixels=view.project(np.asarray(points,float)+.5);painter.setPen(QPen(QColor('#eac36b') if record['status']=='불확실' else QColor('#83c9e6'),2,Qt.PenStyle.DashLine))
                if len(pixels)>1:painter.drawLine(QPointF(*pixels[0]),QPointF(*pixels[-1]))
                else:painter.drawEllipse(QPointF(*pixels[0]),5,5)
    def draw_2d(self,plan,painter):
        if 'equipment' not in self.hidden_groups:
            painter.setPen(QPen(QColor('#d09af2'),2))
            for record in self.equipment[:2000]:
                x,y,z=record['position']
                if y==plan.layer and 'equip:'+','.join(map(str,[x,y,z])) not in self.hidden_overlays:painter.drawEllipse(QPointF(plan.ox+(x+.5)*plan.scale,plan.oz+(z+.5)*plan.scale),3,3)
        if 'connections' not in self.hidden_groups:
            painter.setPen(QPen(QColor('#ebc269'),2))
            for record in self.evidence:
                if record['key'] in self.hidden_overlays:continue
                points=record.get('positions') or [record['position']]
                for x,y,z in points:
                    if y==plan.layer:painter.drawEllipse(QPointF(plan.ox+(x+.5)*plan.scale,plan.oz+(z+.5)*plan.scale),5,5)

    def draw_axis(self,canvas,painter):
        panel=canvas.panel;u,v,f,flip,size=canvas.mapping();position=panel.slice.value()
        if 'equipment' not in self.hidden_groups:
            painter.setPen(QPen(QColor('#cd97ec'),2))
            for record in self.equipment[:2000]:
                xyz=record['position'];key='equip:'+','.join(map(str,xyz))
                if (panel.projection.isChecked() or xyz[f]==position) and key not in self.hidden_overlays and self.owner.view.display_state.contains_xyz(xyz,self.owner.doc,self.owner.view):painter.drawEllipse(canvas.point(xyz),3,3)
        if 'connections' not in self.hidden_groups:
            painter.setPen(QPen(QColor('#eac36b'),2,Qt.PenStyle.DashLine))
            for record in self.evidence:
                if record['key'] in self.hidden_overlays:continue
                points=record.get('positions') or [record['position']]
                if len(points)>1 and (panel.projection.isChecked() or min(p[f] for p in points)<=position<=max(p[f] for p in points)):painter.drawLine(canvas.point(points[0]),canvas.point(points[-1]))
                for xyz in points:
                    if panel.projection.isChecked() or xyz[f]==position:painter.drawEllipse(canvas.point(xyz),5,5)
