"""Ribbon and one routed workspace per task. All tools reuse existing controllers."""
import json
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QTabBar,QStackedWidget,QToolButton,QLabel,QFrame,QTabWidget,QPushButton,QScrollArea,QSizePolicy,QCheckBox,QComboBox,QToolBar,QGridLayout,QMainWindow,QPlainTextEdit)
from .schematic import atomic_write

class WorkspaceTabs(QTabWidget):
    """Keep old entry points while routing them to their consolidated workspace."""
    def __init__(self):
        super().__init__();self.routes={}
    def setCurrentWidget(self,widget):
        callback=self.routes.get(widget)
        if callback is not None:return callback()
        super().setCurrentWidget(widget)

class IntegratedUI:
    CATEGORIES=('프로젝트','공간','설계','분석','보기','도구')
    def __init__(self,owner):
        self.owner=owner;self.b=owner.workbench;self.buttons={};self.groups={};self.saving=False
        self.path=owner.data_dir/'ribbon091.json'
        from .archive_ui import AnalysisArchive
        owner.analysis_archive=AnalysisArchive(owner)
        self.configure_drawing();self.configure_properties();self.configure_routes();self.configure_visibility();self.build_ribbon()
        self.b.scene_dock.setWindowTitle('장면 탐색기');self.b.editor_dock.setWindowTitle('속성 편집기')
        owner.setWindowTitle('Ship Architect v0.9.3.5 · 통합 설계 작업공간')
        # Legacy toolbar actions remain the same QAction objects used by the ribbon.
        for bar in (owner.toolbar,*self.b.modebars):bar.hide()
        self.b.toolmenu.clear();self.b.toolmenu.addAction('리본 그룹 전체 복원',self.restore_groups)
        for key,widget in self.groups.items():
            action=QAction(key,owner);action.setCheckable(True);action.setChecked(True);action.toggled.connect(widget.setVisible);action.toggled.connect(self.save);self.b.toolmenu.addAction(action)
        self.b.toolmenu.setTitle('리본 설정')
        oldsave=self.b.save
        def save():oldsave();self.save()
        self.b.save=save
        self.restore();self.b.scene_dock.show();self.b.editor_dock.show();self.b.editor_dock.raise_()
        self.install_shortcuts();self.properties.currentChanged.connect(self.save);self.install_panel_diagnostics()
        from .workspace93 import Workspace93
        self.tasks=Workspace93(self);self.tasks.attach()
        self.sync_timer=QTimer(owner);self.sync_timer.setInterval(120);self.sync_timer.timeout.connect(self.sync_actions);self.sync_timer.start();self.sync_actions()
    def configure_drawing(self):
        w=self.owner;m=w.space_manager;a=self.b.axis_plan
        # One orientation selector, one editing mode, one exact top-plan engine.
        for old in (w.plan,m.plan):
            at=w.tabs.indexOf(old)
            if at>=0:w.tabs.removeTab(at)
        a.layout().removeWidget(a.canvas);self.plane_stack=QStackedWidget();self.plane_stack.addWidget(m.plan);self.plane_stack.addWidget(a.canvas);a.layout().insertWidget(1,self.plane_stack)
        for child in m.plan.findChildren(QWidget):child.hide()
        row=QHBoxLayout();row.addWidget(QLabel('경계 도구'));row.addWidget(m.edit_mode);row.addWidget(m.all_height);a.layout().insertLayout(1,row)
        m.edit_mode.show();m.all_height.show()
        a.orientation.currentIndexChanged.connect(self.sync_plane);a.projection.toggled.connect(self.sync_plane);a.inspect.toggled.connect(self.sync_plane)
        a.slice.valueChanged.connect(self.plane_slice_changed);w.layer.valueChanged.connect(self.layer_changed)
        a.status.setText('단면에서 경계를 드래그합니다. Shift 두 점/3D 부피 선택도 동일한 공간 경계 엔진을 사용합니다.')
        self.sync_plane()
        at=w.tabs.indexOf(a);w.tabs.setTabText(at,'공통 도면 편집기');w.tabs.setTabText(w.tabs.indexOf(w.view),'3D 함선')
        w.design_splitter.setOrientation(Qt.Orientation.Vertical)
        w.space_manager.open_plan=lambda:self.b.open_tab(a)
    def sync_plane(self,*_):
        a=self.b.axis_plan;top=a.orientation.currentText()=='평면도 (위)'
        self.plane_stack.setCurrentIndex(0 if top and not a.projection.isChecked() and not a.inspect.isChecked() else 1)
        if top and a.slice.value()!=self.owner.layer.value():self.owner.layer.setValue(a.slice.value())
        self.owner.space_manager.plan.refresh()
    def plane_slice_changed(self,value):
        if self.b.axis_plan.orientation.currentText()=='평면도 (위)' and self.owner.layer.value()!=value:self.owner.layer.setValue(value)
    def layer_changed(self,value):
        a=self.b.axis_plan
        if a.orientation.currentText()=='평면도 (위)' and a.slice.value()!=value:a.slice.setValue(value)
        a.refresh()
    def configure_properties(self):
        w=self.owner;b=self.b;m=w.space_manager;right=b.editor_dock.widget();layout=right.layout()
        layout.removeWidget(w.panel_scroll)
        self.properties=QTabWidget();layout.addWidget(self.properties,1)
        self.properties.addTab(m,'공간·경계');self.properties.addTab(w.panel_scroll,'건축·선택');space_scroll=QScrollArea();space_scroll.setWidgetResizable(True);space_scroll.setWidget(w.space_view);self.properties.addTab(space_scroll,'공간 인식');self.properties.addTab(w.review_view,'안전 검토')
        # Existing manager remains the single controller; its duplicate list/form is internal.
        hidden={m.search,m.sort,m.list,m.name,m.purpose,m.opacity}
        duplicate={'전체 표시','전체 숨김','선택 공간 합치기','선택 공간 삭제','색상 선택','이름·용도·색상·투명도 적용','공간 2D 열기','선택 공간으로 카메라 이동','현재 선택으로 공간 생성','분석 후보 전체 가져오기','v0.7 공간 구역 가져오기','일반 사각 선택으로 돌아가기'}
        for widget in hidden:widget.hide()
        for button in m.findChildren(QPushButton):
            if button.text() in duplicate:button.hide()
        # These operations now have one home in the properties editor.
        row=QHBoxLayout()
        for label,fn in [('공간 추가',m.create),('합치기',b.merge_selected),('선택 부피로 분리',lambda:m.edit_bounds(w.doc.selection,'split')),('공간 삭제',self.delete_spaces)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        m.layout().insertLayout(0,row)
        m.status.setText('장면 탐색기에서 공간을 선택합니다. 공통 도면에서 경계를 편집하고 위 속성에서 이름·용도를 지정합니다.')
        # Reflow the existing space commands instead of leaving wide legacy rows.
        oldscroll=m.findChild(QScrollArea);content=QWidget();compact=QVBoxLayout(content);grid=QGridLayout();compact.addLayout(grid)
        labels={'현재 3D 선택을 경계에 추가':'선택 부피 추가','현재 3D 선택을 경계에서 제거':'선택 부피 제거','현재 3D 선택으로 분리':'선택 부피 분리','현재 Y의 수동 층 기준 제거':'수동 층 기준 제거','선택 공간 통계·연결 분석':'공간 통계·연결','공간별 실행 취소':'공간별 실행 취소'}
        commands=[button for button in m.findChildren(QPushButton) if button.text() not in duplicate and button.parent() is not m]
        for index,button in enumerate(commands):
            full=button.text();button.setToolTip(full);button.setText(labels.get(full,full));grid.addWidget(button,index//2,index%2);button.show()
        compact.addWidget(m.status);compact.addWidget(m.details,1);m.details.setMinimumHeight(100)
        oldscroll.hide();scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(content);m.layout().addWidget(scroll,1)
        at=w.tabs.indexOf(w.network_view)
        if at>=0:w.tabs.removeTab(at)
        analysis_scroll=QScrollArea();analysis_scroll.setWidgetResizable(True);analysis_scroll.setWidget(w.network_view)
        self.analysis_dock=b.dock('구조 분석 작업공간','structure_analysis',analysis_scroll,Qt.DockWidgetArea.RightDockWidgetArea);w.tabifyDockWidget(b.editor_dock,self.analysis_dock);self.analysis_dock.hide();b.menu.addAction(self.analysis_dock.toggleViewAction())
    def delete_spaces(self):
        m=self.owner.space_manager;ids={i.data(0,Qt.ItemDataRole.UserRole).split(':',1)[1] for i in self.b.tree.selectedItems() if str(i.data(0,Qt.ItemDataRole.UserRole)).startswith('space:')}
        m.list.clearSelection()
        for i in range(m.list.count()):
            item=m.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) in ids:item.setSelected(True)
        m.delete()
    def configure_routes(self):
        w=self.owner
        w.tabs.routes.update({w.plan:lambda:self.open_drawing(),w.space_manager.plan:lambda:self.open_drawing(),w.space_manager:lambda:self.open_properties(0),w.space_view:lambda:self.open_properties(2),w.review_view:lambda:self.open_properties(3),w.network_view:self.open_analysis})
        for widget in (w.space_manager,w.space_view,w.review_view):
            at=w.tabs.indexOf(widget)
            if at>=0:w.tabs.removeTab(at)
        # Opening AI focuses the same existing panel in the properties editor.
        old=w.ai_panel.show_panel
        def show_ai():self.open_properties(4);old()
        w.ai_panel.show_panel=show_ai
    def configure_visibility(self):
        m=self.owner.space_manager;viewlayout=self.b.view_panel.layout();row=QHBoxLayout()
        for widget in (m.overlays,m.blocks,m.only,m.ghost):row.addWidget(widget);widget.show()
        viewlayout.insertLayout(0,row)
        row=QHBoxLayout()
        for widget in (m.floor,m.floor_on):row.addWidget(widget);widget.show()
        viewlayout.insertLayout(1,row)
        # Explorer is the only entry for global visibility; switches live in the view dock.
        row=QHBoxLayout();button=QPushButton('표시 설정');button.clicked.connect(self.show_view_controls);row.addWidget(button);button=QPushButton('표시 복원');button.clicked.connect(self.b.restore_visibility);row.addWidget(button)
        self.b.scene_dock.widget().layout().insertLayout(1,row)
        for widget in (m.overlays,m.blocks,m.only,m.ghost):widget.toggled.connect(self.refresh_visibility)
    def refresh_visibility(self,*_):self.b.axis_plan.refresh();self.owner.space_manager.plan.refresh();self.b.rebuild_tree(True);self.b.timer.start()
    def show_view_controls(self):self.b.view_dock.show();self.b.view_dock.raise_()
    def open_properties(self,index=0):self.b.editor_dock.show();self.b.editor_dock.raise_();self.properties.setCurrentIndex(index)
    def open_drawing(self):self.b.open_tab(self.b.axis_plan)
    def open_analysis(self):
        self.analysis_dock.show();self.analysis_dock.raise_();self.log_ui('analysis_page')
    def dispatch(self,text,fn):
        w=self.owner
        if w.loading or w.operation is not None:
            w.statusBar().showMessage('현재 작업이 완료된 뒤 실행하세요. 취소는 진행 표시줄의 작업 취소를 사용하세요.');return
        if text=='승인·적용' and not w.approve_button.isEnabled():return
        if text=='제안 취소' and not w.reject_button.isEnabled():return
        fn()
    def action(self,text,fn=None,shortcut=None):
        a=QAction(text,self.owner)
        callback=fn.trigger if isinstance(fn,QAction) else fn
        a.triggered.connect(lambda:self.dispatch(text,callback))
        if isinstance(fn,QAction):a.setToolTip(fn.shortcut().toString())
        if shortcut:a.setShortcut(QKeySequence(shortcut));self.owner.addAction(a)
        return a
    def sync_actions(self):
        w=self.owner;busy=w.loading or w.operation is not None
        for (category,text),button in self.buttons.items():
            enabled=not busy
            if text=='승인·적용':enabled &= w.approve_button.isEnabled()
            elif text=='제안 취소':enabled &= w.reject_button.isEnabled()
            button.setEnabled(enabled)
    def add_group(self,row,category,title,items):
        group=QFrame();group.setObjectName('ribbonGroup');lay=QVBoxLayout(group);lay.setContentsMargins(9,5,9,3);buttons=QHBoxLayout();buttons.setSpacing(5)
        for text,callback in items:
            a=self.action(text,callback);button=QToolButton();button.setDefaultAction(a);button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly);button.setMinimumHeight(33);buttons.addWidget(button);self.buttons[(category,text)]=button
        lay.addLayout(buttons);label=QLabel(title);label.setAlignment(Qt.AlignmentFlag.AlignCenter);label.setStyleSheet('color:#93a9bf;font-size:10px');lay.addWidget(label);row.addWidget(group);self.groups[category+' · '+title]=group
    def space_deletion_menu(self):
        from PySide6.QtGui import QCursor
        self.owner.space_tools.deletion_menu(self.owner).exec(QCursor.pos())

    def build_ribbon(self):
        w=self.owner;b=self.b;m=w.space_manager;n=w.network_view;self.ribbon=QWidget();self.ribbon.setObjectName('ribbon');layout=QVBoxLayout(self.ribbon);layout.setContentsMargins(8,2,8,4);layout.setSpacing(2)
        self.categories=QTabBar();self.categories.setExpanding(False)
        for title in self.CATEGORIES:self.categories.addTab(title)
        layout.addWidget(self.categories);self.pages=QStackedWidget();layout.addWidget(self.pages)
        actions={a.text():a for a in w.toolbar.actions()}
        def common(labels):return [(t,actions[t]) for t in labels]
        specs={
          '프로젝트':[('파일',common(['새 프로젝트','.schem 열기','프로젝트 열기','저장','.schem 내보내기'])),('기록',common(['실행 취소','다시 실행','대화 기록']))],
          '공간':[('공간 생성·편집',[('공간 인식',lambda:self.open_properties(2)),('공간 추가',m.create),('합치기',b.merge_selected),('분리',lambda:m.edit_bounds(w.doc.selection,'split')),('경계 편집',self.open_drawing)]),('속성',[('이름·용도',lambda:self.open_properties(0)),('공간 통계',m.analyze),('공간 기록',m.show_history),('공간별 실행 취소',m.undo_space)]),('가져오기',[('분석 후보 가져오기',m.import_candidates),('이전 구역 가져오기',m.import_legacy),('사각 선택 복귀',m.clear_active)]),('분석 관리',[('저장 분석',w.analysis_archive.save),('분석 불러오기',w.analysis_archive.restore),('공간 표시',lambda:w.space_tools.presentation_menu()),('공간 삭제 메뉴',self.space_deletion_menu)])],
          '설계':[('건축',[('수동 편집',lambda:self.open_properties(1)),('AI 건축',w.ai_panel.show_panel),('변경 미리보기',w.make_preview),('변경 전후',self.compare)]),('승인',[('안전 검토',lambda:self.open_properties(3)),('승인·적용',w.approve),('제안 취소',w.reject)]),('기록',common(['실행 취소','다시 실행','대화 기록']))],
          '분석':[('구조·통행',[('구조 분석 작업공간',self.open_analysis),('연결 분석',self.analyze),('두 공간 경로',b.route_selected),('경로 지우기',n.clear_route)]),('설비',[('설비 분석',self.equipment),('선택 초점',b.focus)])],
          '보기':[('뷰',[('3D 함선',lambda:b.open_tab(w.view)),('공통 도면',self.open_drawing),('2D/3D 동시 보기',lambda:w.split_mode.setChecked(not w.split_mode.isChecked())),('시점 초기화',w.view.reset_camera)]),('내부',[('선택 초점',b.focus),('내부 보기',b.interior),('고립',b.isolate),('숨김',b.hide_selected),('표시 전체 복원',b.restore_visibility),('단면·표시',self.show_view_controls)]),('작업공간',[('3D 분리',lambda:b.detach_view('3D')),('2D 분리',lambda:b.detach_view('2D')),('배치 저장',b.save),('기본 배치',b.reset_docks)])],
          '도구':[('진단',[('오류 진단 ZIP',w.export_diagnostics),('성능·제한 안내',self.performance)]),('설정',[('리본 그룹 복원',self.restore_groups),('카메라 설정',self.show_view_controls)])]
        }
        for category in self.CATEGORIES:
            page=QWidget();row=QHBoxLayout(page);row.setContentsMargins(0,0,0,0)
            for title,items in specs[category]:self.add_group(row,category,title,items)
            row.addStretch();scroll=QScrollArea();scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setWidgetResizable(True);scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);scroll.setWidget(page);self.pages.addWidget(scroll)
        self.ribbon.setFixedHeight(108);self.ribbon.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);self.ribbon_bar=QToolBar('카테고리 리본',w);self.ribbon_bar.setObjectName('category_ribbon');self.ribbon_bar.setMovable(False);self.ribbon_bar.setFloatable(False);self.ribbon_bar.addWidget(self.ribbon);w.addToolBar(Qt.ToolBarArea.TopToolBarArea,self.ribbon_bar)
        self.categories.currentChanged.connect(self.category_changed);self.category_changed(0)
        self.ribbon.setStyleSheet('QTabBar::tab{padding:7px 24px;color:#cad5e5;background:#192331;border-bottom:2px solid transparent;}QTabBar::tab:selected{background:#263e60;color:white;border-bottom:2px solid #53a8f5;}QFrame#ribbonGroup{background:#1d2b3d;border:1px solid #34475d;border-radius:5px;}QToolButton{padding:5px 9px;background:#263950;border-radius:4px;}QToolButton:hover{background:#385e87;}')
    def category_changed(self,index):
        self.pages.setCurrentIndex(index);category=self.categories.tabText(index)
        self.b.mode.setCurrentText({'공간':'공간','설계':'건축','분석':'연결·경로'}.get(category,'통합'))
        for bar in (self.owner.toolbar,*self.b.modebars):bar.hide()
        if category=='공간':self.open_properties(0)
        elif category=='설계':self.open_properties(1)
        self.save()
    def compare(self):
        if self.owner.proposal is not None:self.owner.compare.setCurrentIndex(1-self.owner.compare.currentIndex());self.open_properties(1)
    def analyze(self):self.open_analysis();self.owner.network_view.analyze()
    def equipment(self):self.open_analysis();self.owner.network_view.scan_equipment()
    def performance(self):
        self.open_properties(3);self.owner.statusBar().showMessage('CPU 전체 메시 유지: 드래그 중 16만 픽셀, 종료 후 정밀 복원. 실측·미검증: 별도 개발 검증 자료',20000)
    def restore_groups(self):
        for a in self.b.toolmenu.actions():
            if a.isCheckable():a.setChecked(True)
    def install_shortcuts(self):
        a=self.action('장면 검색',lambda:(self.b.scene_dock.show(),self.b.search.setFocus()),'Ctrl+F')
        self.search_action=a
    def save(self,*_):
        if self.saving or not hasattr(self,'categories'):return
        try:
            payload={'category':self.categories.currentIndex(),'property_page':self.properties.currentIndex(),'hidden_groups':[key for key,g in self.groups.items() if g.isHidden()]}
            atomic_write(self.path,json.dumps(payload,ensure_ascii=False).encode())
        except OSError as e:self.owner.statusBar().showMessage('리본 설정 저장 실패: '+str(e))
    def restore(self):
        self.saving=True
        try:
            p=json.loads(self.path.read_text());self.categories.setCurrentIndex(max(0,min(5,int(p.get('category',0)))));self.properties.setCurrentIndex(max(0,min(4,int(p.get('property_page',0)))))
            hidden=p.get('hidden_groups',[])
            for a in self.b.toolmenu.actions():
                if a.isCheckable():a.setChecked(a.text() not in hidden)
        except (OSError,ValueError,TypeError):pass
        finally:self.saving=False

    def ui_state(self):
        w=self.owner
        return {'space_presentation':getattr(w.view,'space_display',{}),'selected_space':w.doc.active_space_id,'mode':self.categories.tabText(self.categories.currentIndex()),'property_page':self.properties.tabText(self.properties.currentIndex()),'central':w.tabs.tabText(w.tabs.currentIndex()),'window':[w.width(),w.height()], 'docks':[{'name':d.objectName(),'visible':d.isVisible(),'floating':d.isFloating(),'area':w.dockWidgetArea(d).value,'geometry':[d.x(),d.y(),d.width(),d.height()]} for d in (self.b.scene_dock,self.b.editor_dock,self.b.view_dock,self.analysis_dock,*([self.tasks.result_dock] if hasattr(self,'tasks') else []),*self.b.floating.values())]}
    def log_ui(self,reason):
        from .diagnostics import event
        if not hasattr(self,'ui_summary'):return
        state=self.ui_state();self.ui_summary.setPlainText(json.dumps(state,ensure_ascii=False,indent=2));event('ui_layout',reason=reason,state=state)
    def install_panel_diagnostics(self):
        w=self.owner
        w.setDockOptions(QMainWindow.DockOption.AllowTabbedDocks|QMainWindow.DockOption.AllowNestedDocks)
        w.setCorner(Qt.Corner.BottomLeftCorner,Qt.DockWidgetArea.LeftDockWidgetArea);w.setCorner(Qt.Corner.BottomRightCorner,Qt.DockWidgetArea.RightDockWidgetArea)
        scene=self.b.scene_dock.widget();self.scene_pages=QTabWidget();self.scene_pages.addTab(scene,'장면');self.ui_summary=QPlainTextEdit();self.ui_summary.setReadOnly(True);self.scene_pages.addTab(self.ui_summary,'진단 요약');self.b.scene_dock.setWidget(self.scene_pages)
        self.properties.currentChanged.connect(lambda *_:self.log_ui('property_page'))
        self.scene_pages.currentChanged.connect(lambda *_:self.log_ui('scene_page'))
        for dock in (self.b.scene_dock,self.b.editor_dock,self.b.view_dock,self.analysis_dock,*([self.tasks.result_dock] if hasattr(self,'tasks') else []),*self.b.floating.values()):
            dock.topLevelChanged.connect(lambda *_:self.log_ui('dock_float'));dock.dockLocationChanged.connect(lambda *_:self.log_ui('dock_location'))
        self.log_ui('workspace_ready')

    def normalize_panels(self):
        dock=self.analysis_dock;visible=dock.isVisible()
        if not dock.isFloating():
            self.owner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,dock);self.owner.tabifyDockWidget(self.b.editor_dock,dock)
            if visible:dock.raise_()
            else:dock.hide()
        self.log_ui('panel_layout_migrated')
