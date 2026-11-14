from __future__ import annotations
from collections import Counter
from pathlib import Path
import traceback
import os
from PySide6.QtCore import Qt, QStandardPaths, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QPushButton, QLabel, QSpinBox, QComboBox, QCheckBox,
    QPlainTextEdit, QListWidget, QLineEdit, QTabWidget, QScrollArea, QSplitter, QGridLayout,
    QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QToolBar, QProgressBar)
from .schematic import Schematic, load_file
from .model import Bounds, Document, decorative
from .views import View3D, LayerView
from .ai_ui import AIPanel
from .workers import Task
from .chunks import Cancelled, count_states
from .diagnostics import setup as setup_diagnostics, event as diagnostic_event, report as diagnostic_report, hook_unhandled
from .safety import audit, review
from .review_ui import ReviewView
from .spaces_ui import SpaceView
from .space_ui import SpaceManager
from .network_ui import NetworkView
from .workbench import Workbench

STYLE = '''
QMainWindow, QDialog, QWidget { background: #172333; color: #e4edf6; font-size: 12px; }
QToolBar { background: #203146; border: none; padding: 6px; spacing: 5px; }
QToolButton, QPushButton { background: #2b425a; border: 1px solid #3d5973; border-radius: 5px; padding: 7px 9px; }
QToolButton:hover, QPushButton:hover { background: #365675; }
QPushButton:disabled { color: #6e8296; background: #233244; }
QGroupBox { border: 1px solid #34485d; border-radius: 6px; margin-top: 13px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #9acbd9; }
QSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QListWidget { background: #101c2b; border: 1px solid #34485d; border-radius: 3px; padding: 4px; }
QTabBar::tab { padding: 10px 24px; background: #203146; }
QTabBar::tab:selected { color: #8be1d7; border-bottom: 2px solid #73d5c9; }
QStatusBar { background: #203146; }
'''


class Loader(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    progress = Signal(int,str)
    cancelled = Signal()

    def __init__(self, path, backup):
        super().__init__()
        self.path, self.backup = path, backup

    def run(self):
        try:
            if self.path.lower().endswith('.shipproj'):
                doc = Document.open_project(self.path,self.progress.emit,self.isInterruptionRequested)
            else:
                schematic, original = load_file(self.path, self.backup,self.progress.emit,self.isInterruptionRequested)
                doc = Document(schematic, original, self.path)
            doc.prepare_protection(self.progress.emit,self.isInterruptionRequested)
            doc.analyze(doc.selection,self.isInterruptionRequested)
            self.loaded.emit(doc)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:
            self.failed.emit(f'{type(e).__name__}: {e}')


class NewDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('빈 프로젝트 생성')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('블록이 없는 설계 공간을 만듭니다. 좌표는 0부터 시작합니다.'))
        form = QFormLayout()
        self.fields = []
        for label, default in [('X 폭', 40), ('Y 높이', 16), ('Z 길이', 64), ('Minecraft DataVersion', 3955)]:
            s = QSpinBox()
            s.setRange(1, 100000 if 'DataVersion' in label else 32767)
            s.setValue(default)
            form.addRow(label, s)
            self.fields.append(s)
        layout.addLayout(form)
        layout.addWidget(QLabel('작업 공간 제한: 디스크 저장소 예산 2 GiB\nDataVersion 기본값: Minecraft Java 1.21.1 (3955)'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Ship Architect 0.7  |  함선 설계 작업실')
        self.resize(1380, 880)
        self.setMinimumSize(1000, 700)
        self.doc = None
        self.proposal = None
        self.preview_epoch = 0
        self.preview_blocks = None
        self.comparison_caches = {}
        self.last_point = None
        self.corner = None
        self.project_path = None
        self.loader = None
        self.operation = None
        self.analysis_worker = None
        self.analysis_pending = False
        self.analysis_cache = {}
        self.resume_after_save = None
        self.load_cancelled = False
        self.closing=False
        self.loading = False
        self.data_dir = Path(os.environ.get('SHIP_ARCHITECT_DATA_DIR') or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        setup_diagnostics(self.data_dir / 'diagnostics')
        toolbar = QToolBar('프로젝트');toolbar.setObjectName('project_tools')
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label, shortcut, callback in [
            ('새 프로젝트', 'Ctrl+N', self.new_project), ('.schem 열기', 'Ctrl+O', self.open_schem),
            ('프로젝트 열기', 'Ctrl+Shift+O', self.open_project), ('저장', 'Ctrl+S', self.save_project),
            ('.schem 내보내기', 'Ctrl+E', self.export), ('실행 취소', 'Ctrl+Z', self.undo),
            ('다시 실행', 'Ctrl+Y', self.redo), ('샘플 열기', '', self.demo),
            ('오류 진단 ZIP', '', self.export_diagnostics)]:
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            toolbar.addAction(action)
        ai_action=QAction('AI 설계',self)
        ai_action.triggered.connect(lambda:self.ai_panel.show_panel())
        toolbar.addAction(ai_action)
        chat_action=QAction('대화 기록',self);chat_action.triggered.connect(lambda:self.tabs.setCurrentWidget(self.ai_panel.chat_view));toolbar.addAction(chat_action)
        self.toolbar = toolbar
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        self.heading = QLabel()
        self.heading.setStyleSheet('font-size: 16px; padding: 6px; color: #9fddd5;')
        self.heading.setWordWrap(True)
        root.addWidget(self.heading)
        self.progress_row=QWidget()
        pr=QHBoxLayout(self.progress_row)
        pr.setContentsMargins(0,0,0,0)
        self.progress_text=QLabel('준비')
        self.progress_bar=QProgressBar()
        self.progress_bar.setRange(0,100)
        self.cancel_button=QPushButton('작업 취소')
        self.cancel_button.clicked.connect(self.cancel_tasks)
        pr.addWidget(self.progress_text,2);pr.addWidget(self.progress_bar,3);pr.addWidget(self.cancel_button)
        root.addWidget(self.progress_row)
        self.progress_row.hide()
        splitter = QSplitter();self.main_splitter=splitter
        root.addWidget(splitter, 1)
        workspace = QWidget()
        work = QVBoxLayout(workspace)
        work.setContentsMargins(0, 0, 0, 0)
        from .integrated_ui import WorkspaceTabs
        self.tabs = WorkspaceTabs()
        self.view = View3D()
        self.plan = LayerView()
        self.tabs.addTab(self.view, '3D 설계')
        self.tabs.addTab(self.plan, 'Y층 단면 · 구역 선택')
        self.review_view=ReviewView(self);self.tabs.addTab(self.review_view,'변경 위치 · 안전 검토')
        self.space_view=SpaceView(self);self.tabs.addTab(self.space_view,'공간 후보')
        self.space_manager=SpaceManager(self);self.tabs.addTab(self.space_manager,'공간 관리');self.tabs.addTab(self.space_manager.plan,'공간 2D 편집')
        self.network_view=NetworkView(self);self.tabs.addTab(self.network_view,'연결·경로·설비')
        self.split_tab=QWidget();split_layout=QVBoxLayout(self.split_tab);self.design_splitter=QSplitter();split_layout.addWidget(self.design_splitter)
        work.addWidget(self.tabs, 1)
        controls = QGridLayout()
        controls.addWidget(QLabel('현재 Y층'),0,0)
        self.layer = QSpinBox()
        controls.addWidget(self.layer,0,1)
        self.clip = QCheckBox('현재 Y층 위 숨김')
        controls.addWidget(self.clip,0,2)
        self.zone_clip = QCheckBox('선택 구역만 3D 표시')
        controls.addWidget(self.zone_clip,0,3)
        fit = QPushButton('시점 초기화')
        fit.clicked.connect(self.view.reset_camera)
        controls.addWidget(fit,1,0,1,2)
        self.invert_x=QCheckBox('회전 X축 반전')
        self.invert_x.setChecked(self.view.invert_x)
        self.invert_x.toggled.connect(self.view.set_invert_x)
        self.invert_x.setToolTip('마우스의 수평 회전 방향만 뒤집고 설정을 저장합니다.')
        controls.addWidget(self.invert_x,1,2)
        self.single_layer=QCheckBox('현재 Y층만')
        controls.addWidget(self.single_layer,1,3)
        self.split_mode=QCheckBox('2D/3D 분할 화면');self.split_mode.toggled.connect(self.toggle_split);controls.addWidget(self.split_mode,2,0,1,2)
        work.addLayout(controls)
        self.inspect = QLabel('블록 클릭: 좌표와 블록 상태 확인')
        self.inspect.setWordWrap(True)
        work.addWidget(self.inspect)
        splitter.addWidget(workspace)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(430)
        panel = QWidget()
        self.editor_panel=panel
        side = QVBoxLayout(panel)
        box = QGroupBox('구역 선택  ·  양 끝 블록 포함')
        selection_layout = QVBoxLayout(box)
        self.coords = []
        for label in ['최소 X / Y / Z', '최대 X / Y / Z']:
            selection_layout.addWidget(QLabel(label))
            row = QHBoxLayout()
            spins = []
            for axis in range(3):
                s = QSpinBox()
                row.addWidget(s)
                spins.append(s)
            self.coords.append(spins)
            selection_layout.addLayout(row)
        row = QHBoxLayout()
        for label, callback in [('좌표 적용', self.coordinates_selected), ('전체', self.select_all), ('현재 층', self.select_layer)]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            row.addWidget(b)
        selection_layout.addLayout(row)
        self.analysis = QLabel()
        self.analysis.setWordWrap(True)
        selection_layout.addWidget(self.analysis)
        self.select_mode=QComboBox()
        self.select_mode.addItems(['3D 왼쪽 드래그: 회전','3D 왼쪽 드래그: 구역 선택'])
        self.select_mode.currentIndexChanged.connect(lambda i:setattr(self.view,'selection_mode',i==1))
        selection_layout.addWidget(self.select_mode)
        focus=QPushButton('선택 구역에 시점 맞추기');focus.clicked.connect(self.view.focus_selection)
        selection_layout.addWidget(focus)
        row=QHBoxLayout()
        point=QPushButton('클릭한 한 칸 선택');point.clicked.connect(self.select_point)
        adjacent=QPushButton('3D 면 옆 한 칸');adjacent.clicked.connect(self.select_adjacent)
        row.addWidget(point);row.addWidget(adjacent);selection_layout.addLayout(row)
        side.addWidget(box)
        zones = QGroupBox('저장한 구역')
        zl = QVBoxLayout(zones)
        self.zone_list = QListWidget()
        self.zone_list.setMaximumHeight(105)
        self.zone_list.itemClicked.connect(self.zone_selected)
        zl.addWidget(self.zone_list)
        self.zone_name = QLineEdit()
        self.zone_name.setPlaceholderText('예: 격납고 1층 / 앞쪽 통로')
        zl.addWidget(self.zone_name)
        row = QHBoxLayout()
        for label, callback in [('구역 저장', self.add_zone), ('선택 구역 삭제', self.delete_zone)]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            row.addWidget(b)
        zl.addLayout(row)
        lock=QPushButton('현재 구역을 보호 구역으로 저장')
        lock.clicked.connect(self.lock_zone);zl.addWidget(lock)
        side.addWidget(zones)
        self.ai_panel=AIPanel(self);side.addWidget(self.ai_panel)
        editing = QGroupBox('건축 제안  ·  승인 후 적용')
        el = QVBoxLayout(editing)
        self.material = QComboBox()
        self.material.setMinimumContentsLength(14)
        self.material.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.material.setToolTip('가져온 설계는 실제 사용된 팔레트가 기본 재료입니다.\n모드/장치 블록 배치는 후속 버전에서 지원합니다.')
        el.addWidget(self.material)
        from .materials import CATALOG
        addrow=QHBoxLayout()
        self.catalog=QComboBox();self.catalog.addItems(CATALOG)
        self.catalog.setMinimumContentsLength(12)
        self.catalog.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        addbutton=QPushButton('재료 추가');addbutton.clicked.connect(self.add_material)
        addrow.addWidget(self.catalog,1);addrow.addWidget(addbutton);el.addLayout(addrow)
        self.mode = QComboBox()
        for title, key in [('바닥 (선택 최소 Y)', 'floor'), ('천장 (선택 최대 Y)', 'ceiling'),
                           ('벽 (구역 네 면)', 'walls'), ('선택 내부 채우기', 'fill'), ('선택 블록 지우기', 'erase'), ('빈 공간에 블록 추가', 'add'), ('지정 재료만 교체', 'replace')]:
            self.mode.addItem(title, key)
        el.addWidget(self.mode)
        self.source_material=QComboBox()
        self.source_material.setMinimumContentsLength(12)
        self.source_material.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source_material.setToolTip('교체 도구: 이 상태만 교체. 모든 배치 블록을 선택하면 공기는 제외합니다.')
        el.addWidget(self.source_material)
        self.allow_exterior=QCheckBox('선택 범위의 기존 외피 변경 허용')
        self.allow_exterior.setToolTip('기본은 원본 외피 보호. 허용하더라도 장치/BlockEntity/잠금 구역은 보호하며 적용 전에 추가 확인합니다.')
        el.addWidget(self.allow_exterior)
        self.replace = QCheckBox('기존 장식 블록 교체 허용')
        self.replace.setToolTip('기본은 빈 공간에만 배치합니다. 모드/장치/블록 엔티티는 항상 보호합니다.')
        el.addWidget(self.replace)
        self.preview_button = QPushButton('수동 변경 미리보기 만들기')
        self.preview_button.clicked.connect(self.make_preview)
        el.addWidget(self.preview_button)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(190)
        self.summary.setPlaceholderText('범위 · 재료 · 개수 · 충돌 결과가 여기에 표시됩니다.')
        el.addWidget(self.summary)
        self.compare=QComboBox()
        self.compare.addItems(['변경 후 미리보기','변경 전 승인된 설계'])
        self.compare.setEnabled(False)
        self.compare.currentIndexChanged.connect(self.compare_preview)
        el.addWidget(self.compare)
        row = QHBoxLayout()
        self.approve_button = QPushButton('승인 · 적용')
        self.approve_button.setStyleSheet('background: #27665f;')
        self.approve_button.clicked.connect(self.approve)
        self.reject_button = QPushButton('제안 취소')
        self.reject_button.clicked.connect(self.reject)
        row.addWidget(self.approve_button)
        row.addWidget(self.reject_button)
        el.addLayout(row)
        side.addWidget(editing)
        hbox=QGroupBox('변경 이력 · 최근 60건')
        hl=QVBoxLayout(hbox)
        self.history_view=QPlainTextEdit();self.history_view.setReadOnly(True);self.history_view.setMaximumHeight(140)
        hl.addWidget(self.history_view);side.addWidget(hbox)
        note = QLabel('금색: 선택 구역 / 보라색: 모드 블록\nv0.9.3.6: 외형·갑판·개구부 · 2D/3D 구조 검증\n장치 연결·동작 검증은 아직 지원하지 않습니다.')
        note.setWordWrap(True)
        note.setStyleSheet('color: #9cacc0;')
        side.addWidget(note)
        side.addStretch()
        self.panel_scroll = scroll
        scroll.setWidget(panel)
        splitter.addWidget(scroll)
        splitter.setSizes([980, 360])
        self.setCentralWidget(central)
        self.layer.valueChanged.connect(self.layer_changed)
        self.clip.toggled.connect(self.layer_changed)
        self.zone_clip.toggled.connect(self.layer_changed)
        self.single_layer.toggled.connect(self.layer_changed)
        self.view.mesh_progress.connect(self.show_progress)
        self.view.mesh_ready.connect(self.mesh_complete)
        self.view.picked.connect(self.picked)
        self.view.selected.connect(lambda a,b:self.set_selection(Bounds.between(a,b)))
        self.view.selection_finished.connect(lambda:self.layer_changed() if self.zone_clip.isChecked() else None)
        self.plan.picked.connect(self.picked)
        self.plan.selected.connect(lambda a, b: self.set_selection(Bounds.between(a, b)))
        self.material.currentIndexChanged.connect(self.reject)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.source_material.currentIndexChanged.connect(self.reject)
        self.allow_exterior.toggled.connect(self.reject)
        self.replace.toggled.connect(self.reject)
        self.set_document(Document(Schematic.empty(40, 16, 64)), '빈 프로젝트')
        self.workbench=Workbench(self)
        from .integrated_ui import IntegratedUI
        self.integrated_ui=IntegratedUI(self)
        self.restore_layout();self.workbench.restore();self.integrated_ui.normalize_panels()
        self.integrated_ui.category_changed(self.integrated_ui.categories.currentIndex())

    def toggle_split(self,enabled):
        drawing=self.workbench.axis_plan if hasattr(self,'integrated_ui') else self.space_manager.plan
        if enabled:
            for widget in (self.view,drawing):
                at=self.tabs.indexOf(widget)
                if at>=0:self.tabs.removeTab(at)
                self.design_splitter.addWidget(widget);widget.show()
            if self.tabs.indexOf(self.split_tab)<0:self.tabs.addTab(self.split_tab,'2D/3D 분할')
            self.tabs.setCurrentWidget(self.split_tab);self.design_splitter.setSizes([600,600])
        else:
            self.view.setParent(None);drawing.setParent(None)
            self.tabs.insertTab(0,self.view,'3D 함선');self.tabs.insertTab(1,drawing,'공통 도면 편집기')
            at=self.tabs.indexOf(self.split_tab)
            if at>=0:self.tabs.removeTab(at)
            self.tabs.setCurrentWidget(self.view)
        self.view.schedule_render();self.space_manager.plan.refresh();self.save_layout()
        if hasattr(self,'workbench'):
            for kind,holder in self.workbench.return_buttons.items():
                if holder.layout().count()==1:self.workbench.floating[kind].hide()

    def save_layout(self):
        import json
        from .schematic import atomic_write
        try:
            atomic_write(self.data_dir/'layout.json',json.dumps({'width':self.width(),'height':self.height(),'panels':([sum(self.main_splitter.sizes()),self.workbench.editor_dock.width()] if hasattr(self,'workbench') else self.main_splitter.sizes()),'design':self.design_splitter.sizes(),'split':self.split_mode.isChecked()}).encode())
        except OSError as exc:self.statusBar().showMessage('레이아웃 저장 실패: '+str(exc))

    def restore_layout(self):
        import json
        try:
            p=json.loads((self.data_dir/'layout.json').read_text());self.resize(max(1000,min(4000,int(p['width']))),max(700,min(2400,int(p['height']))))
            if p.get('split'):self.split_mode.setChecked(True)
            self.main_splitter.setSizes(p.get('panels',[900,350]));self.design_splitter.setSizes(p.get('design',[600,600]))
        except (OSError,ValueError,KeyError,TypeError):pass

    def export_diagnostics(self):
        confirm=QMessageBox.question(self,'개발 진단 ZIP',
            '개발 로그에는 AI에게 입력한 명령과 응답, 선택 좌표 등이 포함될 수 있습니다.\n'
            '함선 설계도 원본은 포함되지 않습니다.\n'
            '파일을 공유하기 전에 내용을 확인하세요. 내보낼까요?')
        if confirm!=QMessageBox.StandardButton.Yes:return
        path,_=QFileDialog.getSaveFileName(self,'개발 진단 ZIP 저장',
                                           'ShipArchitect_Diagnostic.zip','ZIP (*.zip)')
        if not path:return
        if not path.lower().endswith('.zip'):path+='.zip'
        doc=self.doc;ui_state=self.integrated_ui.ui_state() if hasattr(self,'integrated_ui') else None
        diagnostic_event('report_requested',critical=True)
        def done(target):
            QMessageBox.information(self,'진단 ZIP 저장 완료',f'{target.name} 저장 완료\n공유 전에 프롬프트와 로그 내용을 확인하세요.')
        self.run_operation(lambda p,c:diagnostic_report(path,document=doc,ui_state=ui_state),done,'상세 진단 ZIP 생성')

    def error(self, error):
        diagnostic_event('ui_error',critical=True,error=str(error),traceback=traceback.format_exc())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / 'errors.log').open('a', encoding='utf-8') as f:
            f.write(str(error) + '\n' + traceback.format_exc() + '\n')
        QMessageBox.critical(self, '작업 오류', str(error))

    def can_leave(self,resume=None):
        if self.loading or self.operation is not None:
            return False
        if self.doc and (self.doc.dirty or self.proposal is not None):
            answer = QMessageBox.question(self, '저장하지 않은 작업',
                '승인된 설계를 저장한 뒤 계속할까요? 미승인 제안은 저장되지 않습니다.',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save:
                saved=self.save_project()
                if not saved and self.operation is not None:self.resume_after_save=resume
                return saved
        return True

    def closeEvent(self, event):
        self.space_manager.save_metadata();self.save_layout()
        if hasattr(self,'workbench'):self.workbench.save()
        self.closing=True
        self.analysis_pending=False
        jobs=[self.loader,self.operation,self.analysis_worker]
        running=self.loading or self.operation is not None or self.analysis_worker is not None or self.view.workers_running() or self.ai_panel.busy()
        if running:
            self.ai_panel.shutdown()
            self.cancel_tasks()
            self.view.cancel_jobs()
            if self.analysis_worker and self.analysis_worker.isRunning(): self.analysis_worker.requestInterruption()
            event.ignore()
            QTimer.singleShot(60,self.close)
            return
        if self.can_leave(self.close):
            if self.proposal is not None:self.doc.chat_update(self.proposal.trace_id,status='preview_expired',reason='프로그램 종료 · 미승인 블록 미적용')
            diagnostic_event('application_close',critical=True)
            self.ai_panel.shutdown();self.ai_panel.chat_store.close()
            if hasattr(self,'workbench'):self.workbench.shutdown()
            event.accept()
        else:
            self.closing=False
            self.ai_panel.closed=False;self.ai_panel.timer.start();self.ai_panel.chat_view.timer.start()
            self.update_summary()
            event.ignore()

    def show_progress(self,value,message):
        self.progress_row.show()
        self.progress_bar.setValue(value)
        self.progress_text.setText(message)

    def cancel_tasks(self):
        for job in (self.loader,self.operation):
            if job and job.isRunning():job.requestInterruption()
        self.view.cancel_jobs()
        self.progress_text.setText('취소 요청됨…')

    def mesh_complete(self):
        if not any(j and j.isRunning() for j in (self.loader,self.operation)):
            self.progress_row.hide()

    def run_operation(self,function,success,message):
        if self.operation is not None:return
        self.editor_panel.setEnabled(False);self.toolbar.setEnabled(False)
        self.show_progress(0,message)
        job=Task(function,self)
        self.operation=job;job.stage=message
        job.result.connect(success)
        job.progress.connect(self.show_progress)
        job.failed.connect(self.error)
        job.cancelled.connect(lambda:self.statusBar().showMessage('작업을 취소했습니다.'))
        job.finished.connect(self._operation_finished)
        job.finished.connect(job.deleteLater)
        job.start()

    def _operation_finished(self):
        self.operation=None
        self.editor_panel.setEnabled(True);self.toolbar.setEnabled(True)
        if hasattr(self,'integrated_ui'):self.integrated_ui.sync_actions()
        if not self.view.mesh_busy:self.progress_row.hide()
        resume=self.resume_after_save
        self.resume_after_save=None
        if resume and not self.doc.dirty:
            self.reject()
            QTimer.singleShot(0,resume)

    def set_document(self, doc, title='프로젝트'):
        diagnostic_event('document_opened', title=title, size=doc.schematic.size,revision=doc.revision)
        if self.doc is not None and self.proposal is not None:self.doc.chat_update(self.proposal.trace_id,status='cancelled',reason='문서 전환으로 미승인 제안 종료')
        self.ai_panel.reset_document()
        self.preview_button.setEnabled(True);self.preview_button.setText('수동 변경 미리보기 만들기')
        if self.doc is None and not doc.source and not getattr(doc,'from_project',False):
            doc.project_id=self.ai_panel.settings.value('chat/draft_id',doc.project_id)
        if not doc.source and not getattr(doc,'from_project',False):self.ai_panel.settings.setValue('chat/draft_id',doc.project_id)
        if self.doc is not None:
            self.space_manager.save_metadata()
            if hasattr(self,'workbench'):self.workbench.save_display()
        self.doc, self.proposal, self.corner = doc, None, None
        self.ai_panel.attach_document(doc);self.space_view.reset()
        self.preview_blocks=None;self.comparison_caches={};self.last_point=None
        self.compare.blockSignals(True);self.compare.setCurrentIndex(0);self.compare.blockSignals(False);self.compare.setEnabled(False)
        self.allow_exterior.blockSignals(True);self.allow_exterior.setChecked(False);self.allow_exterior.blockSignals(False)
        self.analysis_cache.clear()
        self.title = title
        self.material.blockSignals(True)
        self.material.clear()
        used = set(doc.schematic.counts or doc.schematic.palette) if doc.source else set(doc.schematic.palette)
        for i, state in sorted(doc.schematic.palette.items()):
            if i in used:
                self.material.addItem(state, i)
        for index in range(self.material.count()):
            state = self.material.itemText(index)
            if decorative(state) and state not in {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}:
                self.material.setCurrentIndex(index)
                break
        self.material.blockSignals(False)
        self.source_material.blockSignals(True);self.source_material.clear();self.source_material.addItem('모든 배치 블록 (공기 제외)',None)
        for i,state in sorted(doc.schematic.palette.items()):
            if state.split('[')[0] not in {'minecraft:air','minecraft:cave_air','minecraft:void_air'}:self.source_material.addItem(state,i)
        self.source_material.blockSignals(False)
        self.mode_changed()
        w, h, l = doc.schematic.size
        for spins in self.coords:
            for s, dim in zip(spins, (w, h, l)):
                s.setRange(0, dim-1)
        self.layer.blockSignals(True)
        self.layer.setRange(0, h-1)
        self.layer.setValue(0)
        self.layer.blockSignals(False)
        self.clip.blockSignals(True)
        self.single_layer.blockSignals(True)
        self.single_layer.setChecked(False)
        self.single_layer.blockSignals(False)
        self.zone_clip.blockSignals(True)
        self.clip.setChecked(False)
        self.zone_clip.setChecked(False)
        self.clip.blockSignals(False)
        self.zone_clip.blockSignals(False)
        self.view.layer, self.view.cut, self.view.zone_only, self.view.single_layer = 0, False, False, False
        self.view.pending = False
        self.plan.doc = doc
        self.plan.blocks = doc.schematic.blocks
        self.plan.layer = 0
        self.view.set_document(doc)
        self.plan.refresh()
        self.sync_selection()
        self.refresh_zones()
        self.refresh_history()
        self.update_summary()
        self.summary.clear()
        self.approve_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.inspect.setText('클릭: 블록 상태 / Shift+클릭 두 번: 구역 / Y 단면 드래그: 빈 공간 선택')
        self.statusBar().showMessage('준비됨 · 원본 .schem은 덮어쓰지 않습니다.')

        self.space_manager.attach();self.network_view.refresh_spaces()
        if hasattr(self,'integrated_ui') and hasattr(self.integrated_ui,'tasks'):self.integrated_ui.tasks.attach()
        if hasattr(self,'workbench'):self.workbench.attach_document()

    def update_summary(self):
        if self.closing:return
        sc=self.doc.schematic
        w,h,l=sc.size
        star=' *' if self.doc.dirty else ''
        if sc.counts is None and sc.blocks.size<=500_000:
            sc.counts=count_states(sc.blocks)
        counts=sc.counts
        actual=sum(n for i,n in counts.items() if sc.palette[i].split('[')[0] not in {'minecraft:air','minecraft:cave_air','minecraft:void_air'}) if counts is not None else None
        actual_text=f'{actual:,}' if actual is not None else '계산 중'
        self.heading.setText(f'{self.title}{star} | X {w} × Y {h} × Z {l} | 전체 부피(공기 포함) {sc.blocks.size:,}칸 · 실제 배치 {actual_text}블록')
        key=(id(self.doc),self.doc.revision,self.doc.selection)
        if key in self.analysis_cache:
            self._show_analysis(self.analysis_cache[key])
            return
        whole=self.doc.selection.lo==(0,0,0) and self.doc.selection.hi==tuple(v-1 for v in sc.size)
        if self.doc.selection.volume<=500_000 or (whole and counts is not None and (sc.blocks.size<=500_000 or (self.doc.revision,self.doc.selection) in self.doc._analysis)):
            self._show_analysis(self.doc.analyze(self.doc.selection))
            return
        if self.analysis_worker is not None:
            self.analysis_pending=True
            self.analysis_worker.requestInterruption()
            return
        doc,bounds=self.doc,self.doc.selection
        job=Task(lambda p,c:doc.analyze(bounds,c),self)
        self.analysis_worker=job
        self.analysis.setText('선택 영역을 청크별 분석 중…')
        job.result.connect(lambda data:self._analysis_done(key,data))
        job.failed.connect(lambda error:self.analysis.setText(error))
        job.finished.connect(self._analysis_finished)
        job.finished.connect(job.deleteLater)
        job.start()

    def _show_analysis(self,data):
        self.analysis.setText(f"선택 부피(공기 포함) {data['volume']:,}칸\n실제 배치 {data['solid']:,} · 공기 {data['empty']:,}\n보호 {data['protected']:,} · 상태 {len(data['palette'])}종")

    def _analysis_done(self,key,data):
        self.analysis_cache[key]=data
        if len(self.analysis_cache)>24:self.analysis_cache.pop(next(iter(self.analysis_cache)))
        if key==(id(self.doc),self.doc.revision,self.doc.selection):self._show_analysis(data)

    def _analysis_finished(self):
        self.analysis_worker=None
        if self.closing:
            self.analysis_pending=False;return
        if self.analysis_pending:
            self.analysis_pending=False
            self.update_summary()
        elif self.doc.schematic.counts is not None:
            self.update_summary()

    def sync_selection(self):
        for spins, values in zip(self.coords, [self.doc.selection.lo, self.doc.selection.hi]):
            for s, value in zip(spins, values):
                s.setValue(value)
        self.view.update()
        self.plan.update()

    def set_selection(self, bounds):
        try:
            bounds.validate(self.doc.schematic.size)
            if self.doc.selection != bounds:
                self.reject()
                self.doc.selection = bounds
                self.doc.dirty = True
            self.sync_selection()
            self.update_summary()
            if self.view.zone_only and self.view.drag_corner is None:
                self.view.rebuild()
        except Exception as e:
            self.error(e)

    def coordinates_selected(self):
        points = [tuple(s.value() for s in spins) for spins in self.coords]
        self.set_selection(Bounds.between(*points))

    def select_all(self):
        self.set_selection(Bounds((0, 0, 0), tuple(v-1 for v in self.doc.schematic.size)))

    def select_layer(self):
        w, h, l = self.doc.schematic.size
        y = self.layer.value()
        self.set_selection(Bounds((0, y, 0), (w-1, y, l-1)))

    def picked(self, xyz, shift=False):
        if hasattr(self,'workbench'):self.workbench.block_selected(xyz)
        x, y, z = xyz
        material = self.doc.schematic.palette[int(self.view.blocks[y, z, x])]
        self.last_point=(x,y,z)
        reasons=self.doc.protection_at((x,y,z))
        self.inspect.setText(f'X {x} / Y {y} / Z {z}   |   {material}' + ('  ['+', '.join(reasons)+']' if reasons else ''))
        if shift:
            if self.corner is None:
                self.corner = xyz
                self.statusBar().showMessage(f'구역 첫 점 {xyz} · Shift+클릭으로 두 번째 점을 지정하세요.')
            else:
                self.set_selection(Bounds.between(self.corner, xyz))
                self.corner = None
                self.statusBar().showMessage('3D 구역 선택 완료')

    def layer_changed(self, *_):
        if not self.doc:
            return
        self.view.layer = self.plan.layer = self.layer.value()
        self.view.cut = self.clip.isChecked()
        self.view.zone_only = self.zone_clip.isChecked()
        self.view.single_layer = self.single_layer.isChecked()
        self.view.rebuild()
        self.plan.refresh()
        self.space_manager.plan.layer=self.layer.value();self.space_manager.plan.refresh()
        m=self.space_manager
        if getattr(m,'levels',None):
            y=max([y for y in m.levels if y<=self.layer.value()] or [m.levels[0]])
            m.floor.blockSignals(True);m.floor.setCurrentIndex(m.levels.index(y));m.floor.blockSignals(False);m.update_floor_range()
            if self.view.ghost_floors:self.view.rebuild()

    def refresh_views(self,changed_indices=None,cache=None):
        if self.doc.space_revision!=self.doc.revision:self.view.candidate_boxes=[]
        self.view.blocks=self.plan.blocks=self.doc.schematic.blocks
        self.view.pending=False
        if cache is not None:
            self.view.cache=cache
        else:self.view.invalidate_data(changed_indices)
        self.view.rebuild();self.plan.refresh();self.update_summary();self.refresh_history()
        if hasattr(self,'space_manager'):
            self.space_manager.plan.blocks=self.view.blocks;self.space_manager.plan.refresh();self.space_manager.refresh()

    def refresh_history(self):
        names={'approve':'승인','undo':'실행 취소','redo':'다시 실행','zone_add':'구역 추가','zone_delete':'구역 삭제'}
        rows=[]
        for item in self.doc.history[-60:]:
            row=f"{item.get('time','')} | {names.get(item['action'],item['action'])}"
            if item.get('origin')=='ai':row+=' | AI 설계' 
            if 'mode' in item:row+=f" | { {'floor':'바닥','walls':'벽','ceiling':'천장','fill':'채우기','add':'추가','replace':'교체','erase':'삭제','ai_plan':'AI 계획','zone':'구역 설정'}.get(item['mode'],item['mode'])} {item.get('blocks',0):,}개"
            if item.get('source_id') is not None:row+=f" | {self.doc.schematic.palette.get(item['source_id'],item['source_id'])}"
            if 'state' in item:row+=f" → {item['state']}"
            if 'bounds' in item:row+=f" | {item['bounds']['lo']} → {item['bounds']['hi']}"
            if 'name' in item:row+=f" | {item['name']}"
            rows.append(row)
        self.history_view.setPlainText('\n'.join(rows))
        self.history_view.verticalScrollBar().setValue(self.history_view.verticalScrollBar().maximum())

    def add_material(self):
        self.reject()
        try:
            state=self.catalog.currentText();i=self.doc.add_material(state)
            index=self.material.findData(i)
            if index<0:
                self.material.addItem(state,i);index=self.material.count()-1
                self.source_material.addItem(state,i)
            self.material.setCurrentIndex(index)
            self.refresh_history();self.update_summary()
        except Exception as e:self.error(e)

    def select_point(self):
        if self.last_point is not None:self.set_selection(Bounds(self.last_point,self.last_point))

    def select_adjacent(self):
        point=getattr(self.view,'last_pick_position',None)
        xyz=self.view.adjacent(point) if point is not None else None
        if xyz is None:
            self.statusBar().showMessage('먼저 3D에서 블록 면을 클릭하세요. 설계 경계 밖 칸은 선택할 수 없습니다.')
        else:self.set_selection(Bounds(xyz,xyz))

    def mode_changed(self,*_):
        self.reject()
        is_replace=self.mode.currentData()=='replace'
        self.source_material.setEnabled(is_replace)
        self.replace.setEnabled(not is_replace and self.mode.currentData() in {'floor','ceiling','walls','fill'})

    def compare_preview(self,index):
        if self.proposal is None:return
        side='before' if index else 'after'
        self.view.blocks=self.plan.blocks=self.doc.schematic.blocks if index else self.preview_blocks
        self.view.cache=self.comparison_caches[side]
        self.view.pending=True;self.view.preview_side='변경 전' if index else '변경 후'
        self.view.rebuild();self.plan.refresh()
        self.space_manager.plan.blocks=self.view.blocks;self.space_manager.plan.refresh()

    def refresh_zones(self):
        self.zone_list.clear()
        for zone in self.doc.zones:
            self.zone_list.addItem(('[보호] ' if zone.get('protected') else '')+zone['name'])

    def add_zone(self):
        self.reject()
        try:
            self.doc.add_zone(self.zone_name.text(), self.doc.selection)
            self.refresh_history()
            self.refresh_zones()
            self.update_summary()
            self.zone_name.clear()
        except Exception as e:
            self.error(e)

    def lock_zone(self):
        self.reject()
        try:
            name=self.zone_name.text().strip() or f'보호 구역 {len(self.doc.zones)+1}'
            self.doc.add_zone(name,self.doc.selection,protected=True)
            self.refresh_zones();self.refresh_history();self.update_summary();self.zone_name.clear()
        except Exception as e:self.error(e)

    def delete_zone(self):
        self.reject()
        i = self.zone_list.currentRow()
        if i >= 0:
            self.doc.delete_zone(i)
            self.refresh_history()
            self.refresh_zones()
            self.update_summary()

    def zone_selected(self, item):
        zone = self.doc.zones[self.zone_list.row(item)]
        self.set_selection(Bounds(tuple(zone['lo']), tuple(zone['hi'])))

    def make_preview(self):
        if self.proposal is not None and self.proposal.origin=='ai':
            self.statusBar().showMessage('AI 미리보기가 준비되어 있습니다. 재료 수정은 AI 재료 표에서 다시 검증하세요.')
            return
        try:
            self.reject()
            target=self.material.currentData()
            if target is None:raise ValueError('재료를 선택하세요.')
            epoch=self.preview_epoch
            doc,bounds,mode,replace=self.doc,self.doc.selection,self.mode.currentData(),self.replace.isChecked()
            source=self.source_material.currentData() if mode=='replace' else None
            allow=self.allow_exterior.isChecked()
            if bounds.volume>500_000 or (doc.source and doc.exterior is None):
                self.run_operation(lambda p,c:doc.propose(bounds,target,mode,replace,c,source,allow),lambda proposal:self._preview_ready(proposal,epoch),'변경 제안 / 보호 검사')
            else:self._preview_ready(doc.propose(bounds,target,mode,replace,source_id=source,allow_exterior=allow))
        except Exception as e:self.error(e)

    def _preview_ready(self,proposal,epoch=None):
        if hasattr(self,'workbench'):
            try:self.workbench.guard(proposal)
            except ValueError as exc:
                self.doc.chat_update(proposal.trace_id,status='blocked',reason=str(exc));diagnostic_event('view_proposal_blocked',trace_id=proposal.trace_id,critical=True,reason=str(exc));self.error(exc);return False
        if proposal.revision!=self.doc.revision or proposal.bounds!=self.doc.selection or (epoch is not None and epoch!=self.preview_epoch):
            self.statusBar().showMessage('구역 또는 설계가 변경되어 이전 제안을 폐기했습니다.')
            return
        try:
            change=review(self.doc,proposal)
            self.review_view.display(self.doc,proposal,change)
        except ValueError as exc:
            diagnostic_event('proposal_blocked',trace_id=proposal.trace_id,critical=True,error=str(exc))
            self.error(exc)
            return
        self.proposal=proposal
        diagnostic_event('proposal_preview',trace_id=proposal.trace_id,origin=proposal.origin,
                         mode=proposal.mode,scope={'lo':proposal.bounds.lo,'hi':proposal.bounds.hi},change=change)
        self.preview_button.setEnabled(proposal.origin!='ai')
        self.preview_button.setText('AI 미리보기 준비됨 · 아래에서 비교/승인' if proposal.origin=='ai' else '수동 변경 미리보기 만들기')
        p=proposal
        self.preview_blocks=self.doc.preview(p)
        base_cache=self.view.cache
        self.comparison_caches={'before':base_cache,'after':base_cache.fork_invalidated(p.indices,self.doc.schematic.size)}
        self.compare.blockSignals(True);self.compare.setCurrentIndex(0);self.compare.blockSignals(False);self.compare.setEnabled(True)
        self.compare_preview(0)
        before=Counter(self.doc.schematic.palette[int(i)] for i in p.before)
        self.summary.setPlainText(f'범위: {p.bounds.lo} → {p.bounds.hi}\n재료: {self.doc.schematic.palette[p.target]}\n변경: {len(p.indices):,}개\n보호 제외: {p.protected:,}개\n기존 장식과 겹침: {p.occupied:,}개\n외피 보호 제외: {p.exterior_blocked:,} / 잠금 제외: {p.locked_blocked:,}\n외피 변경 영향: {p.exterior_affected:,}개\n변경 전: '+', '.join(f'{k} {v:,}개' for k,v in before.items()))
        if p.origin=='ai':
            after=Counter(self.doc.schematic.palette[int(i)] for i in p.after)
            self.summary.setPlainText('AI 계획: '+p.plan['summary']+f'\n범위: {p.bounds.lo} → {p.bounds.hi}\n최종 변경 {len(p.indices):,}개 · 연산 {len(p.plan["operations"])}개\n재료: '+', '.join(f'{k} {v:,}개' for k,v in after.items())+f'\n연산별 보호 제외 누적 {p.protected:,} · 외피 제외 {p.exterior_blocked:,}\n연산 중 겹침 누적 (앞선 계획 포함) {p.occupied:,}\n'+'\n'.join(p.plan['notes']))
        self.summary.appendPlainText(f'\n공간 셀 밖 제외 누적: {p.outside_space:,}개 · 선택 공간: {self.doc.active_space_id or "일반 사각 선택"}')
        if not len(p.indices):
            import json
            self.summary.appendPlainText('미완료 · 변경 0개. 자동으로 범위를 확대하지 않았습니다.\n'+json.dumps(p.reasons or {},ensure_ascii=False,indent=2))
        self.space_manager.plan.blocks=self.preview_blocks;self.space_manager.plan.refresh()
        if change['bbox']:
            self.summary.appendPlainText(
                f"\n실제 변경 좌표: {change['bbox']['lo']} → {change['bbox']['hi']}" +
                f"\n설계도 바깥쪽 면 변경: {change['outer_face_changed']:,}개")
        if change['requires_confirmation']:
            self.summary.appendPlainText('⚠ AI 대량 변경: 승인 시 별도의 경고가 표시됩니다.')
        self.summary.appendPlainText(f"\nPython 실제 예상: 추가 {change['added']:,} / 삭제 {change['removed']:,} / 교체 {change['replaced']:,}\n"+'\n'.join(change['warnings']))
        self.approve_button.setEnabled(len(p.indices)>0 and not change['hull_risk'])
        if change['hull_risk']:self.tabs.setCurrentWidget(self.review_view)
        self.reject_button.setEnabled(True)
        self.panel_scroll.ensureWidgetVisible(self.approve_button,0,24)
        QTimer.singleShot(0,lambda:self.panel_scroll.ensureWidgetVisible(self.approve_button,0,24))
        self.statusBar().showMessage('미승인 미리보기 · 범위/재료/개수를 확인한 뒤 승인하세요.')
        return True

    def approve(self):
        if self.proposal:
            try:
                p=self.proposal
                if hasattr(self,'workbench'):self.workbench.guard(p)
                allow=False
                if p.exterior_affected:
                    allow=QMessageBox.question(self,'원본 외피 변경 승인',
                        f'범위 {p.bounds.lo} → {p.bounds.hi}\n원본 외피 {p.exterior_affected:,}개를 변경합니다.\n재료: {self.doc.schematic.palette[p.target]}\n장치와 잠금 구역은 계속 보호합니다. 적용할까요?')==QMessageBox.StandardButton.Yes
                    if not allow:return
                mass=False
                check=review(self.doc,p)
                if p.origin=='ai' and check['hull_risk']:
                    diagnostic_event('approval_blocked',trace_id=p.trace_id,critical=True,reason='document outer faces')
                    raise ValueError('AI가 설계도 외곽 여러 면을 대량 변경하려 하므로 차단했습니다. 작은 구역을 선택해 다시 요청하세요.')
                if check['requires_confirmation']:
                    detail=(f"AI 변경 {check['changed']:,}개\n"
                            f"추가 {check['added']:,} / 삭제 {check['removed']:,} / 교체 {check['replaced']:,}\n실제 변경 범위: {check['bbox']['lo']} → {check['bbox']['hi']}\n"
                            f"원본 설계도 바깥쪽 면에 닿은 변경 {check['outer_face_changed']:,}개\n"
                            '큰 변경입니다. 미리보기와 재료를 확인했나요?')
                    mass=QMessageBox.warning(self,'AI 대량 변경 별도 승인',detail,
                                             QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
                    diagnostic_event('mass_approval_decision',trace_id=p.trace_id,critical=True,approved=mass)
                    if not mass:
                        self.doc.chat_update(p.trace_id,status='preview',reason='대량 변경 승인 거부 · 미적용');return
                indices=self.doc.approve(p,approve_exterior=allow,approve_mass=mass)
                cache=self.comparison_caches.get('after')
                self.proposal=None;self.preview_blocks=None;self.comparison_caches={}
                self.preview_button.setEnabled(True);self.preview_button.setText('수동 변경 미리보기 만들기')
                self.compare.setEnabled(False)
                self.approve_button.setEnabled(False);self.reject_button.setEnabled(False)
                self.refresh_views(indices,cache)
                self.summary.setPlainText(f'{len(p.indices):,}개 승인 완료 · Ctrl+Z로 되돌리기')
                self.statusBar().showMessage(f'{len(p.indices):,}개 승인 · 수정 청크와 이웃만 갱신합니다.')
            except Exception as e:
                self.doc.chat_update(self.proposal.trace_id if self.proposal else None,status='error',error=str(e))
                self.error(e)

    def reject(self,*_):
        self.preview_button.setEnabled(True);self.preview_button.setText('수동 변경 미리보기 만들기')
        self.preview_epoch+=1
        if self.proposal:
            self.doc.chat_update(self.proposal.trace_id,status='cancelled',reason='미승인 제안 취소/변경')
            diagnostic_event('proposal_rejected',trace_id=self.proposal.trace_id,
                             origin=self.proposal.origin,changed=len(self.proposal.indices))
            cache=self.comparison_caches.get('before')
            self.proposal=None;self.preview_blocks=None;self.comparison_caches={}
            self.refresh_views(cache=cache)
            self.summary.clear()
            self.statusBar().showMessage('미승인 제안을 취소했습니다.')
        self.compare.setEnabled(False)
        self.approve_button.setEnabled(False);self.reject_button.setEnabled(False)

    def undo(self):
        self.reject()
        try:
            changed=self.doc.undo()
            if changed is not None:
                self.refresh_views(changed);self.refresh_zones()
                if hasattr(self,'integrated_ui') and hasattr(self.integrated_ui,'tasks'):self.integrated_ui.tasks.attach()
                if getattr(self.doc,'save_empty_spaces',False):self.space_manager.save_metadata()
        except Exception as e:self.error(e)

    def redo(self):
        self.reject()
        try:
            changed=self.doc.redo()
            if changed is not None:
                self.refresh_views(changed);self.refresh_zones()
                if hasattr(self,'integrated_ui') and hasattr(self.integrated_ui,'tasks'):self.integrated_ui.tasks.attach()
                if getattr(self.doc,'save_empty_spaces',False):self.space_manager.save_metadata()
        except Exception as e:self.error(e)

    def new_project(self):
        if not self.can_leave(self.new_project):
            return
        dialog = NewDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                values = [s.value() for s in dialog.fields]
                if values[0]*values[1]*values[2]>500_000:
                    def create(progress,cancel):
                        sc=Schematic.empty(*values)
                        return Document(sc,sc.to_bytes(cancel))
                    self.run_operation(create,self._new_project_ready,'새 작업 공간 생성')
                else:self._new_project_ready(Document(Schematic.empty(*values)))
            except Exception as e:
                self.error(e)

    def _new_project_ready(self,doc):
        doc.dirty=True
        self.project_path=None
        self.set_document(doc,'새 프로젝트')

    def open_schem(self):
        if not self.can_leave(self.open_schem):
            return
        path, _ = QFileDialog.getOpenFileName(self, '.schem 불러오기', '', 'Sponge Schematic (*.schem)')
        if path:
            self.load_path(path)

    def open_project(self):
        if not self.can_leave(self.open_project):
            return
        path, _ = QFileDialog.getOpenFileName(self, '프로젝트 열기', '', 'Ship Architect (*.shipproj)')
        if path:
            self.load_path(path)

    def load_path(self, path):
        if self.loading:
            return
        self.loading=True
        self.load_cancelled=False
        self.statusBar().showMessage('파일 분석 중…')
        self.editor_panel.setEnabled(False)
        self.toolbar.setEnabled(False)
        self.loader = Loader(str(path), self.data_dir / 'backups')
        self.loader.loaded.connect(lambda doc: self._loaded(doc, str(path)))
        self.loader.failed.connect(self.error)
        self.loader.progress.connect(self.show_progress)
        self.loader.cancelled.connect(self._load_cancelled)
        self.loader.finished.connect(self._load_finished)
        self.loader.start()

    def _loaded(self, doc, path):
        self.project_path = path if path.lower().endswith('.shipproj') else None
        self.set_document(doc, Path(path).name)

    def _load_cancelled(self):
        self.load_cancelled=True
        self.statusBar().showMessage('불러오기를 취소했습니다. 기존 설계를 유지합니다.')

    def _load_finished(self):
        self.loading=False
        self.editor_panel.setEnabled(True)
        self.toolbar.setEnabled(True)
        if hasattr(self,'integrated_ui'):self.integrated_ui.sync_actions()
        if not self.view.mesh_busy:self.progress_row.hide()
        if not self.load_cancelled:self.statusBar().showMessage('파일 분석 완료 · 3D는 청크별 백그라운드 처리 중입니다.')

    def demo(self):
        if self.can_leave(self.demo):
            path = Path(__file__).resolve().parents[2] / 'examples' / 'demo_ship.schem'
            self.load_path(path)

    def save_project(self):
        if hasattr(self,'workbench'):self.workbench.save_display()
        path = self.project_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, '승인된 설계 저장', 'design.shipproj', 'Ship Architect (*.shipproj)')
        if not path:
            return False
        if not path.lower().endswith('.shipproj'):
            path += '.shipproj'
        try:
            if self.doc.schematic.blocks.size>500_000:
                doc=self.doc
                self.run_operation(lambda p,c:doc.save_project(path,c),lambda _:self._saved_project(path),'프로젝트 저장 중')
                return False  # can_leave must wait; no unsaved work is silently discarded.
            self.doc.save_project(path)
            self.project_path = path
            self.title = Path(path).name
            self.update_summary()
            self.statusBar().showMessage('프로젝트 저장 완료 · 원본 백업/구역/승인된 변경/이력 포함, 미승인 제안 제외')
            return True
        except Exception as e:
            self.error(e)
            return False

    def _saved_project(self,path):
        self.project_path=path
        self.title=Path(path).name
        self.update_summary()
        self.statusBar().showMessage('프로젝트 저장 완료')

    def export(self):
        if self.proposal:
            QMessageBox.information(self, '미승인 제안', '미리보기를 승인하거나 취소한 뒤 내보내세요.')
            return
        if QMessageBox.question(self, '.schem 내보내기 승인',
            '현재 승인된 전체 설계를 .schem으로 내보낼까요?\n원본 파일은 덮어쓰지 않습니다.\n모드 블록의 실제 연결·작동과 게임 내 붙여넣기는 별도 확인이 필요합니다.') != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, '다른 이름으로 내보내기', 'design_approved.schem', 'Sponge Schematic (*.schem)')
        if path:
            if not path.lower().endswith('.schem'):
                path += '.schem'
            try:
                if self.doc.schematic.blocks.size>500_000:
                    doc=self.doc
                    self.run_operation(lambda p,c:doc.export(path,approved=True,cancel=c),self._export_done,'내보내기 및 검증 중')
                    return
                self._export_done(self.doc.export(path,approved=True))
            except Exception as e:
                self.error(e)

    def _export_done(self,report):
        self.statusBar().showMessage(f"저장 및 재불러오기 검증 완료: {Path(report['path']).name} · 블록/팔레트/NBT 일치")
        self.summary.setPlainText(f"저장: {report['path']}\n재불러오기 검증: 통과\n좌표 {report['blocks']:,}개 · NBT/팔레트 검증 통과\nSHA-256: {report['sha256']}")


def run():
    import sys
    hook_unhandled()
    print('Startup: creating Qt application',flush=True)
    app = QApplication(sys.argv)
    app.setApplicationName('ShipArchitect')
    app.setOrganizationName('ShipArchitect')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    from PySide6.QtWidgets import QSplashScreen
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtCore import Qt
    pixmap=QPixmap(520,155)
    pixmap.fill(QColor('#152439'))
    splash=QSplashScreen(pixmap)
    splash.showMessage('Ship Architect · 작업 공간과 렌더러 준비',Qt.AlignmentFlag.AlignCenter,Qt.GlobalColor.white)
    splash.show()
    app.processEvents()
    print('Startup: loading settings, editor and scene',flush=True)
    window = MainWindow()
    print('Startup: workspace ready',flush=True)
    splash.showMessage('Ship Architect · 작업 공간 준비 완료',Qt.AlignmentFlag.AlignCenter,Qt.GlobalColor.white)
    app.processEvents()
    window.show()
    splash.finish(window)
    if len(sys.argv) > 1:
        window.load_path(sys.argv[1])
    return app.exec()
