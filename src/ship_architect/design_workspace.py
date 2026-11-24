"""A task-oriented front door over the existing document and safety controllers."""
import json
import numpy as np
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QToolButton,QMenu,QToolBar,QStackedWidget,QComboBox,QLineEdit,QPlainTextEdit,
    QCheckBox,QInputDialog,QDockWidget,QScrollArea)


class DesignWorkspace:
    def __init__(self,ui):
        self.ui=ui;self.w=ui.owner;self.b=ui.b;self.advanced=False
        self.signature=None;self.preview_identity=None
        from .preferences import settings
        self.settings=settings(self.w.data_dir)
        self.w.design_workspace=self
        self.bar=QToolBar('설계 작업 순서',self.w)
        self.bar.setObjectName('design_workflow094')
        self.bar.setMovable(False)
        self.actions={}
        opening=QToolButton();opening.setText('열기');opening.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(opening)
        for title in ('프로젝트 열기','.schem 열기','새 프로젝트'):
            menu.addAction(self.command('프로젝트',title))
        opening.setMenu(menu);self.bar.addWidget(opening)
        for title,callback,help_text in (
            ('공간 분석',self.b.space_review.analyze,'함선의 실제 빈 공간과 설비 근거를 분석합니다.'),
            ('선택·수정',lambda:self.goto(0),'왼쪽에서 고른 공간이나 시설을 수정합니다.'),
            ('AI 내부 설계',lambda:self.goto(1),'선택 공간·시설의 용도·설비·경로를 근거로 설계안을 요청합니다.'),
            ('변경 검토',lambda:self.goto(2),'실제 변경 블록과 위치를 확인하고 수정·승인합니다.'),
            ('저장',self.command('프로젝트','저장').trigger,'공간·시설·승인된 편집을 프로젝트로 저장합니다.')):
            action=self.bar.addAction(title);action.triggered.connect(callback)
            action.setToolTip(help_text);self.actions[title]=action
        more=QToolButton();more.setText('더 보기');more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        options=QMenu(more)
        for category,title in (('보기','3D 함선'),('보기','공통 도면'),('보기','2D/3D 동시 보기'),
                               ('프로젝트','실행 취소'),('프로젝트','다시 실행'),
                               ('프로젝트','.schem 내보내기'),('도구','오류 진단 ZIP')):
            options.addAction(self.command(category,title))
        options.addAction('모든 기능 검색',self.search_commands)
        options.addAction('AI 연결·재료 설정',self.ai_settings)
        options.addAction('화면 배치 저장',lambda:self.b.save())
        options.addAction('고급/검증 작업공간',lambda:self.set_advanced(True))
        more.setMenu(options);self.bar.addWidget(more)
        self.w.addToolBar(Qt.ToolBarArea.TopToolBarArea,self.bar)
        self.back=self.w.menuBar().addAction('기본 설계 화면')
        self.back.triggered.connect(lambda:self.set_advanced(False))

        root=QWidget();layout=QVBoxLayout(root)
        self.next_step=QLabel();self.next_step.setWordWrap(True)
        layout.addWidget(self.next_step)
        self.stage=QComboBox();self.stage.addItems(['선택·수정','AI 내부 설계','변경 검토'])
        layout.addWidget(self.stage)
        self.pages=QStackedWidget();layout.addWidget(self.pages,1)
        self.make_selection_page();self.make_design_page();self.make_review_page()
        self.stage.currentIndexChanged.connect(self.goto)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(root)
        self.dock=QDockWidget('설계 작업',self.w);self.dock.setObjectName('design_tasks094')
        self.dock.setWidget(scroll);self.dock.setMinimumWidth(300);self.dock.setMaximumWidth(600)
        self.w.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.dock)
        for dock in (self.b.editor_dock,self.b.view_dock,ui.analysis_dock):
            self.w.tabifyDockWidget(self.dock,dock)
        self.dock.topLevelChanged.connect(lambda floating:self.dock.setMaximumWidth(16777215 if floating else 600))
        self.timer=QTimer(self.w);self.timer.setInterval(180);self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.set_advanced(self.settings.value('workspace094/advanced',False,type=bool))
        state=self.settings.value('workspace094/layout')
        if state is not None:
            from PySide6.QtCore import QByteArray
            if isinstance(state,QByteArray):self.w.restoreState(state)
        previous_save=self.b.save
        def save():
            previous_save()
            self.save_layout()
        self.b.save=save
        self.layout_timer=QTimer(self.w);self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self.save_layout)
        for dock in (self.dock,self.b.scene_dock,self.b.editor_dock,self.ui.analysis_dock,self.b.view_dock):
            dock.topLevelChanged.connect(lambda *args:self.layout_timer.start(800))
            dock.dockLocationChanged.connect(lambda *args:self.layout_timer.start(800))
        self.refresh()

    def command(self,category,title):
        return self.ui.buttons[category,title].defaultAction()

    def page(self):
        widget=QWidget();layout=QVBoxLayout(widget);self.pages.addWidget(widget)
        return layout

    def make_selection_page(self):
        layout=self.page()
        self.selection_info=QLabel('왼쪽에서 공간 또는 시설을 선택하세요.')
        self.selection_info.setWordWrap(True);self.selection_info.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.selection_info)
        self.fields=QWidget();form=QVBoxLayout(self.fields);form.setContentsMargins(0,0,0,0)
        self.name=QLineEdit();self.name.setPlaceholderText('공간 이름')
        self.purpose=QLineEdit();self.purpose.setPlaceholderText('사용자가 확인한 용도')
        form.addWidget(self.name);form.addWidget(self.purpose)
        apply=QPushButton('이름·용도 저장');apply.clicked.connect(self.apply_space)
        form.addWidget(apply);layout.addWidget(self.fields)
        self.context=QToolButton();self.context.setText('선택 대상 작업')
        self.context.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.context)
        manual=QPushButton('수동 블록 편집')
        manual.clicked.connect(lambda:self.open_legacy(1))
        layout.addWidget(manual)
        layout.addStretch()
        note=QLabel('표시 체크는 화면만 바꿉니다. 삭제·분할·병합은 확인과 실행 취소를 제공합니다.')
        note.setWordWrap(True);layout.addWidget(note)

    def make_design_page(self):
        layout=self.page()
        self.ai_state=QLabel();self.ai_state.setWordWrap(True)
        layout.addWidget(self.ai_state)
        label=QLabel('어떤 내부 시설을 만들까요? 기존 함선에서는 공간 또는 시설을 먼저 선택하세요.')
        label.setWordWrap(True);layout.addWidget(label)
        self.prompt=QPlainTextEdit()
        self.prompt.setPlaceholderText('예: 선택한 정비 구역에 참나무 작업대 구역과 유리 칸막이를 설계하고 기존 통로와 장치는 보존해 줘.')
        self.prompt.setMaximumHeight(160);layout.addWidget(self.prompt)
        row=QHBoxLayout()
        self.generate=QPushButton('설계안 생성');self.generate.clicked.connect(lambda:self.request(False))
        self.refine=QPushButton('요청 수정·재생성');self.refine.clicked.connect(lambda:self.request(True))
        row.addWidget(self.generate);row.addWidget(self.refine);layout.addLayout(row)
        self.cancel=QPushButton('AI 요청 취소');self.cancel.clicked.connect(self.w.ai_panel.cancel)
        layout.addWidget(self.cancel)
        settings=QPushButton('연결·사용 재료 설정');settings.clicked.connect(self.ai_settings)
        layout.addWidget(settings)
        self.ai_progress=QLabel();self.ai_progress.setWordWrap(True)
        self.ai_progress.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.ai_progress)
        layout.addStretch()

    def make_review_page(self):
        layout=self.page()
        self.change_info=QLabel();self.change_info.setWordWrap(True)
        self.change_info.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.change_info)
        row=QHBoxLayout()
        for title,index in (('변경 전 보기',1),('변경 후 보기',0)):
            button=QPushButton(title);button.clicked.connect(lambda checked=False,index=index:self.w.compare.setCurrentIndex(index))
            row.addWidget(button)
        layout.addLayout(row)
        self.explain=QLabel('중앙 2D/3D에서 실제 변경을 확인하세요. 승인은 기존 보호·대량 변경 검사를 거칩니다.')
        self.explain.setWordWrap(True);layout.addWidget(self.explain)
        detail=QCheckBox('설계 명령 상세 수정')
        layout.addWidget(detail)
        self.plan_editor=QPlainTextEdit();self.plan_editor.setMaximumHeight(220);self.plan_editor.hide()
        self.plan_editor.textChanged.connect(self.plan_changed)
        detail.toggled.connect(self.plan_editor.setVisible);layout.addWidget(self.plan_editor)
        recheck=QPushButton('수정한 설계 검증·미리보기');recheck.hide()
        detail.toggled.connect(recheck.setVisible)
        recheck.clicked.connect(lambda:self.w.ai_panel.revalidate_edited_plan(self.plan_editor.toPlainText()))
        layout.addWidget(recheck)
        self.approve=QPushButton('확인한 변경 승인·적용')
        self.approve.clicked.connect(self.w.approve);layout.addWidget(self.approve)
        self.reject=QPushButton('설계안 취소');self.reject.clicked.connect(self.w.reject)
        layout.addWidget(self.reject)
        layout.addStretch()

    def plan_changed(self):
        if self.w.proposal is not None and self.w.proposal.origin=='ai':
            self.w.reject()
            self.w.ai_panel.request_status.setText('설계 명령이 바뀌었습니다. 수정한 설계를 다시 검증해 미리보기하세요.')

    def goto(self,index):
        self.stage.blockSignals(True);self.stage.setCurrentIndex(index);self.stage.blockSignals(False)
        self.pages.setCurrentIndex(index)
        if index==1:self.w.ai_panel.interior_mode.setChecked(True)
        self.dock.show();self.dock.raise_()
        if index==2:
            self.b.open_tab(self.w.view);self.b.axis_plan.refresh()
        self.refresh()

    def request(self,refine):
        self.w.ai_panel.interior_mode.setChecked(True)
        self.w.ai_panel.prompt.setPlainText(self.prompt.toPlainText())
        self.w.ai_panel.generate(refine)

    def open_legacy(self,index):
        self.b.editor_dock.show();self.b.editor_dock.raise_()
        self.ui.properties.setCurrentIndex(index)

    def ai_settings(self):
        self.w.ai_panel.body.show()
        for index in range(self.ui.properties.count()):
            if self.ui.properties.tabText(index)=='AI 작업':
                self.open_legacy(index)
                return

    def apply_space(self):
        if self.w.operation is not None:
            return
        if self.w.doc.active_space() is None:
            return
        self.b.property_name.setText(self.name.text());self.b.property_purpose.setText(self.purpose.text())
        self.b.apply_properties();self.signature=None;self.refresh()

    def set_advanced(self,value):
        self.advanced=bool(value)
        self.ui.ribbon_bar.setVisible(value);self.bar.setVisible(not value)
        for action in self.w.menuBar().actions():
            action.setVisible(value or action is self.back)
        if value:
            self.dock.hide();self.b.editor_dock.show();self.b.editor_dock.raise_()
        else:
            for dock in (self.b.editor_dock,self.b.view_dock,self.ui.analysis_dock,self.ui.tasks.result_dock):
                dock.hide()
            self.dock.show();self.dock.raise_()
        self.b.scene_dock.show()
        self.w.resizeDocks([self.b.scene_dock,self.dock],[270,350],Qt.Orientation.Horizontal)

    def save_layout(self):
        if not self.w.closing:
            self.settings.setValue('workspace094/advanced',self.advanced)
            self.settings.setValue('workspace094/layout',self.w.saveState())
            self.settings.sync()

    def search_commands(self):
        choices={category+' / '+title:button.defaultAction() for (category,title),button in self.ui.buttons.items()}
        name,ok=QInputDialog.getItem(self.w,'모든 기능 검색','기능 이름',sorted(choices),0,True)
        if ok and name in choices:
            self.set_advanced(True);choices[name].trigger()

    def refresh(self):
        if self.w.closing:
            self.timer.stop()
            if hasattr(self,'layout_timer'):self.layout_timer.stop()
            return
        d=self.w.doc
        if d is None:
            return
        key=self.b.selection_key
        signature=(id(d),d.revision,key)
        if signature!=self.signature:
            self.signature=signature
            self.selection_info.setText(self.b.summary_properties.toPlainText() or '왼쪽 탐색기에서 공간이나 시설을 선택하세요.')
            space=d.active_space()
            self.fields.setVisible(space is not None)
            if space:
                self.name.setText(space['name']);self.purpose.setText(space['purpose'])
            menu=QMenu(self.context)
            if key and key.startswith('facility:'):
                fid=key.split(':',1)[1]
                menu.addAction('시설 이름·용도 설정',lambda:self.b.facilities.edit(fid))
                menu.addAction('시설 그룹 해제',lambda:self.b.facilities.remove(fid))
            elif key and key.startswith('space:'):
                menu.addAction('경계 편집',self.ui.open_drawing)
                menu.addAction('시설로 묶기',lambda:self.b.facilities.edit())
                menu.addAction('선택 부피로 나누기',lambda:self.b.space_review.split())
                menu.addAction('선택 공간 숨기기',self.b.hide_selected)
            elif key and key.startswith('candidate:'):
                menu.addAction('후보 확인 후 공간 등록',lambda:self.w.space_view.bulk.confirm({key[10:]}))
                menu.addAction('후보 분석 근거',lambda:self.open_legacy(2))
            else:
                menu.addAction('선택 위치로 이동',self.b.focus)
            self.context.setMenu(menu)
        review_node=getattr(self.b,'nodes',{}).get('review_classes')
        if review_node is not None and not self.advanced and not self.b.search.text():
            review_node.setHidden(True)
        result=self.w.ai_panel.last_result
        if result is not None and id(result)!=self.preview_identity:
            self.preview_identity=id(result)
            self.plan_editor.blockSignals(True)
            self.plan_editor.setPlainText(json.dumps(result['plan'],ensure_ascii=False,indent=2))
            self.plan_editor.blockSignals(False)
        if result is None and self.preview_identity is not None:
            self.plan_editor.clear();self.preview_identity=None
        p=self.w.proposal
        busy=self.w.loading or self.w.operation is not None
        self.generate.setEnabled(not busy and self.w.ai_panel.job is None)
        self.refine.setEnabled(self.generate.isEnabled() and result is not None)
        self.cancel.setEnabled(self.w.ai_panel.job is not None)
        self.approve.setEnabled(self.w.approve_button.isEnabled())
        self.reject.setEnabled(self.w.reject_button.isEnabled())
        self.ai_state.setText(self.w.ai_panel.server_status.text())
        self.ai_progress.setText(self.w.ai_panel.request_status.text())
        if p is not None:
            if getattr(self,'proposal_for_counts',None) is not p:
                self.proposal_for_counts=p
                after=p.after if p.after is not None else np.full(len(p.indices),p.target,dtype=np.int32)
                ids,totals=np.unique(after,return_counts=True)
                self.material_counts={d.schematic.palette[int(value)]:int(count) for value,count in zip(ids,totals)}
            counts=self.material_counts
            text='실제 변경'
            self.change_info.setText(f'{text} {len(p.indices):,}블록\n범위 {p.bounds.lo} → {p.bounds.hi}\n'+
                                     '\n'.join(f'{state}  {count:,}개' for state,count in counts.items())+
                                     f'\n보호 제외 {p.protected:,} · 공간 밖 제외 {p.outside_space:,}')
            self.next_step.setText('다음 단계 · 변경 전후와 실제 변경량을 확인하고 승인하거나 취소하세요.')
        elif self.w.ai_panel.job is not None:
            self.change_info.setText('설계안을 생성·검증하고 있습니다.')
            self.next_step.setText('AI 처리 중 · 수동 편집이 바뀌면 오래된 설계안은 폐기됩니다.')
        else:
            self.change_info.setText('아직 검토할 변경이 없습니다. 설계안을 먼저 생성하세요.')
            self.next_step.setText('다음 단계 · 공간을 선택해 수정하거나 AI 내부 설계에서 원하는 시설을 설명하세요.'
                                  if d.spaces or d.space_candidates else '다음 단계 · 프로젝트를 열고 공간 분석을 실행하세요. 빈 프로젝트는 수동 또는 AI 설계로 시작합니다.')
