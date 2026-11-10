"""Compact right task tabs and a collapsible shared result drawer; no duplicate documents."""
import copy
from PySide6.QtCore import Qt,QObject,QEvent,QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,QTabWidget,QSplitter,QScrollArea,QHeaderView,QComboBox,QSizePolicy,QPlainTextEdit)
from .spatial_ui import SpatialBatch
from .analysis_state import EQUIPMENT_TYPES,commit
from .diagnostics import event

class TableFit(QObject):
    def __init__(self,table):
        super().__init__(table);self.table=table;self.pending=False;table.viewport().installEventFilter(self)
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.Resize and not self.pending:
            self.pending=True;QTimer.singleShot(0,self.fit)
        return False
    def fit(self):
        self.pending=False;width=max(300,self.table.viewport().width()-4)
        for i,fraction in enumerate((.18,.32,.15,.25,.1)):self.table.setColumnWidth(i,max(50,int(width*fraction)))

class Workspace93:
    def __init__(self,ui):
        self.ui=ui;self.w=ui.owner;w=self.w;self.b=w.workbench;n=w.network_view;p=w.space_view
        p.bulk=SpatialBatch(p)
        self.bottom=QTabWidget();self.result_dock=self.b.dock('분석 결과·검토·진단','results93',self.bottom,Qt.DockWidgetArea.BottomDockWidgetArea);self.result_dock.setMinimumHeight(130)
        self.b.menu.addAction(self.result_dock.toggleViewAction());self.result_dock.hide()
        # Each legacy widget is moved, not copied; existing signals still point at it.
        self.connection_results=QSplitter();self.connection_results.addWidget(n.connections);self.connection_results.addWidget(n.details);self.connection_results.setSizes([500,450]);self.bottom.addTab(self.connection_results,'연결·경로 결과')
        self.bottom.addTab(n.table,'설비 전체 목록');self.space_results=QSplitter();self.space_results.addWidget(p.list);self.space_results.addWidget(p.text);self.bottom.addTab(self.space_results,'공간 검토')
        w.review_view.map.setMinimumHeight(110);w.review_view.text.setMinimumHeight(60)
        at=ui.properties.indexOf(w.review_view);ui.properties.removeTab(at);self.bottom.addTab(w.review_view,'변경 안전 검토')
        review=QWidget();rl=QVBoxLayout(review);button=QPushButton('하단 변경 안전 검토 열기');button.clicked.connect(lambda:self.show(w.review_view));rl.addWidget(button);info=QLabel('변경 수·재료·범위·보호/대량 변경 검토는 공통 하단 결과 패널에서 확인합니다.');info.setWordWrap(True);rl.addWidget(info);rl.addStretch();ui.properties.insertTab(3,review,'안전 검토')
        chat=w.ai_panel.chat_view;at=w.tabs.indexOf(chat)
        if at>=0:w.tabs.removeTab(at)
        self.bottom.addTab(chat,'AI 대화 기록')
        self.diag=QLabel('작업 오류와 좌표는 좌측 진단 요약 및 도구 → 오류 진단 ZIP에서 확인합니다.');self.diag.setWordWrap(True);self.bottom.addTab(self.diag,'진단')
        ai_scroll=QScrollArea();ai_scroll.setWidgetResizable(True);ai_scroll.setWidget(w.ai_panel);ui.properties.addTab(ai_scroll,'AI 작업');w.ai_panel.owner_scroll=ai_scroll
        w.tabs.routes[chat]=lambda:self.show(chat);w.tabs.routes[w.review_view]=lambda:self.show(w.review_view)
        self.toggle=QPushButton('분석 결과 펼치기');self.toggle.setCheckable(True);self.toggle.toggled.connect(self.toggle_results);w.statusBar().addPermanentWidget(self.toggle);self.result_dock.visibilityChanged.connect(self.toggle.setChecked)
        self.compact_network();self.compact_space()
        self.b.scene_dock.setMinimumWidth(220);self.b.editor_dock.setMinimumWidth(300);ui.analysis_dock.setMinimumWidth(300)
        for dock in (self.b.scene_dock,self.b.editor_dock,ui.analysis_dock,self.b.view_dock):
            dock.setMaximumWidth(600);dock.topLevelChanged.connect(lambda floating,d=dock:d.setMaximumWidth(16777215 if floating else 600))
        w.resizeDocks([self.b.scene_dock,self.b.editor_dock],[270,390],Qt.Orientation.Horizontal)
    def show(self,widget):
        self.bottom.setCurrentWidget(widget);self.result_dock.show();self.result_dock.raise_();
        if not self.result_dock.isFloating():self.w.resizeDocks([self.result_dock],[230],Qt.Orientation.Vertical)
    def toggle_results(self,on):
        self.result_dock.setVisible(on)
        if on and not self.result_dock.isFloating():self.w.resizeDocks([self.result_dock],[230],Qt.Orientation.Vertical)
    def compact_space(self):
        p=self.w.space_view
        # All top-level analysis actions are short, wrapping in 2 columns.
        for row in range(p.layout().count()):
            item=p.layout().itemAt(row)
            if item.layout() is None:continue
            layout=item.layout()
            if isinstance(layout,QHBoxLayout):
                widgets=[]
                while layout.count():
                    child=layout.takeAt(0)
                    if child.widget():widgets.append(child.widget())
                grid=QGridLayout()
                for i,widget in enumerate(widgets):
                    if isinstance(widget,QPushButton):
                        full=widget.text();widget.setToolTip(full);widget.setText({'현재 선택의 공간 후보 분석':'3D 후보 분석','후보를 3D 선택으로 확인':'후보 초점','확인한 후보를 구역으로 저장':'선택 후보 확정'}.get(full,full));widget.setMinimumWidth(0)
                    grid.addWidget(widget,i//2,i%2)
                layout.addLayout(grid)
        p.status.setText('3D 연결성·지지 바닥·머리 공간·경계 구조를 검사합니다. 명확한 후보만 자동 등록하며, 검토·제외 목록은 하단에서 확인하세요.')
    def compact_network(self):
        w=self.w;n=w.network_view;old=n.layout()
        for button in n.findChildren(QPushButton):button.hide()
        for splitter in n.findChildren(QSplitter):splitter.hide()
        # Clear layout ownership without deleting controls that remain connected.
        while old.count():
            item=old.takeAt(0)
            if item.widget():item.widget().setParent(None)
        self.endpoints=QWidget();ends=QVBoxLayout(self.endpoints);ends.setContentsMargins(0,0,0,0)
        for label,combo in [('출발 공간',n.source),('도착 공간',n.target)]:
            ends.addWidget(QLabel(label));combo.setMinimumContentsLength(8);combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);combo.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);combo.view().setMinimumWidth(420);ends.addWidget(combo);summary=QLabel(combo.currentText());summary.setWordWrap(True);combo.currentTextChanged.connect(summary.setText);combo.currentTextChanged.connect(combo.setToolTip);ends.addWidget(summary)
        old.addWidget(self.endpoints);n.pages=QTabWidget();old.addWidget(n.pages)
        def page(title):
            pane=QWidget();lay=QVBoxLayout(pane);lay.setAlignment(Qt.AlignmentFlag.AlignTop);n.pages.addTab(pane,title);return lay
        connect=page('연결');connect.addWidget(n.local);self.button(connect,'연결 분석',n.analyze);self.button(connect,'연결 결과 펼치기',lambda:self.show(self.connection_results));self.button(connect,'선택 공간 초점',n.focus)
        route=page('경로');self.button(route,'경로 찾기',n.find);self.button(route,'선택한 두 공간 경로',self.b.route_selected);self.button(route,'경로 지우기',n.clear_route);self.button(route,'경로 결과 펼치기',lambda:self.show(self.connection_results))
        equip=page('설비');self.button(equip,'모드 설비 분석',n.scan_equipment);equip.addWidget(n.filter);equip.addWidget(n.page);self.rule_state=QLabel('목록 또는 3D에서 분류할 블록을 선택하세요.');self.rule_state.setWordWrap(True);equip.addWidget(self.rule_state)
        self.rule_scope=QComboBox();self.rule_scope.addItems(['같은 블록 ID 전체 상태','이 정확한 블록 상태만']);equip.addWidget(self.rule_scope);self.rule_type=QComboBox();self.rule_type.addItems(EQUIPMENT_TYPES);equip.addWidget(self.rule_type);self.button(equip,'분류 규칙 저장·재분석',self.save_rule);self.button(equip,'선택 ID 규칙 삭제·재분석',self.delete_rule);self.button(equip,'설비 전체 목록 펼치기',lambda:self.show(n.table))
        n.equipment_details=QPlainTextEdit();n.equipment_details.setReadOnly(True);n.equipment_details.setMaximumHeight(150);equip.addWidget(n.equipment_details)
        old.addWidget(n.status);old.addStretch();n.pages.currentChanged.connect(lambda i:self.endpoints.setVisible(i!=2))
        n.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);n.table.horizontalHeader().setStretchLastSection(True);n.table.setWordWrap(True)
        self.table_fit=TableFit(n.table);n.table.itemSelectionChanged.connect(self.selected_equipment)
        # Compact controls use the existing controller methods, not hidden duplicated rows.
        self.b.node_page=n.pages
    def button(self,layout,text,fn):
        b=QPushButton(text);b.clicked.connect(fn);layout.addWidget(b);return b
    def selected_state(self):
        n=self.w.network_view;row=n.table.currentRow()
        if 0<=row<len(n.visible_records):return n.visible_records[row]['block']
        xyz=self.b.selected_xyz
        if xyz:
            x,y,z=xyz;return self.w.doc.schematic.palette[int(self.w.doc.schematic.blocks[y,z,x])]
        raise ValueError('설비 목록 또는 3D에서 블록을 먼저 선택하세요.')
    def selected_equipment(self):
        try:self.rule_state.setText(self.selected_state())
        except ValueError:pass
    def rule_key(self):
        state=self.selected_state();return state.split('[')[0] if self.rule_scope.currentIndex()==0 else state
    def save_rule(self):
        try:
            d=self.w.doc;state=copy.deepcopy(d.analysis_state);state.setdefault('equipment_rules',{})[self.rule_key()]=self.rule_type.currentText();commit(d,state,action='equipment_rule');self.w.refresh_history();self.w.network_view.invalidate();self.w.network_view.scan_equipment()
        except Exception as e:self.w.error(e)
    def delete_rule(self):
        try:
            d=self.w.doc;state=copy.deepcopy(d.analysis_state);state.setdefault('equipment_rules',{}).pop(self.rule_key(),None);commit(d,state,action='equipment_rule_remove');self.w.refresh_history();self.w.network_view.invalidate();self.w.network_view.scan_equipment()
        except Exception as e:self.w.error(e)
    def attach(self):self.w.space_view.bulk.attach();self.w.view.validation_spaces=[]
