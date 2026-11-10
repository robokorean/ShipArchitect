"""Optional Ollama panel. Its workers never own or disable the manual editor."""
import json
import uuid
from .diagnostics import event
from PySide6.QtCore import QTimer,QSettings,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QLineEdit,QComboBox,QSpinBox,QPlainTextEdit,QCheckBox,QTabWidget
from .workers import Task
from .ollama_client import OllamaClient
from .ai_design import generate_design,prepare_material_request
from .material_ui import MaterialEditor
from .conversations import ConversationStore
from .chat_ui import ChatView
from .safety import review


class AIPanel(QWidget):
    def __init__(self,owner):
        super().__init__()
        self.owner=owner;self.job=None;self.probe_job=None;self.serial=0;self.closed=False
        self.last_plan=None;self.last_scope=None;self.last_result=None;self.current_trace=None
        self.settings=QSettings('ShipArchitect','ShipArchitect')
        self.parent_trace=None;self.active_doc=None
        try:self.chat_store=ConversationStore(owner.data_dir/'conversations.sqlite3')
        except Exception as exc:
            self.chat_store=ConversationStore(':memory:');owner.statusBar().showMessage('대화 자동 저장 경로 오류 · 이번 실행은 임시 기록: '+str(exc))
        self.chat_view=ChatView(self);owner.tabs.addTab(self.chat_view,'AI 대화 기록')
        layout=QVBoxLayout(self);layout.setContentsMargins(0,4,0,4)
        self.toggle=QPushButton('AI 건축 설계 · 펼치기')
        self.toggle.clicked.connect(self.toggle_panel);layout.addWidget(self.toggle)
        self.server_status=QLabel('Ollama 연결 확인 대기 · 수동 편집 가능');self.server_status.setWordWrap(True)
        self.server_status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.server_status)
        self.body=QWidget();body=QVBoxLayout(self.body);body.setContentsMargins(0,0,0,0)
        self.url=QLineEdit(self.settings.value('ai/url','http://127.0.0.1:11434'))
        self.url.setPlaceholderText('로컬 Ollama 주소');body.addWidget(self.url)
        row=QHBoxLayout();self.model=QComboBox();self.model.setEditable(True)
        self.model.addItem(self.settings.value('ai/model','qwen3:4b'))
        self.connect_button=QPushButton('다시 연결');self.connect_button.clicked.connect(self.probe)
        row.addWidget(self.model,1);row.addWidget(self.connect_button);body.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('AI 응답 제한(초)'));self.timeout=QSpinBox();self.timeout.setRange(10,600)
        self.timeout.setValue(int(self.settings.value('ai/timeout',300)));row.addWidget(self.timeout);body.addLayout(row)
        self.scope_status=QLabel('일반 사각 선택');self.scope_status.setWordWrap(True);self.scope_status.setTextFormat(Qt.TextFormat.PlainText);body.addWidget(self.scope_status)
        self.prompt=QPlainTextEdit();self.prompt.setMaximumHeight(100)
        self.prompt.setPlaceholderText('예: 선택 구역에 흰 콘크리트 바닥과 벽, 유리 천장이 있는 방을 설계해 줘.')
        body.addWidget(self.prompt)
        self.material_editor=MaterialEditor(self);body.addWidget(self.material_editor)
        self.allow_existing=QCheckBox('기존 일반 장식의 교체·삭제를 계획에 허용')
        self.allow_existing.setToolTip('기본은 원래 공기였던 칸만 변경. 원본 외피/장치/CC/BlockEntity/잠금 구역은 AI가 해제할 수 없습니다.')
        body.addWidget(self.allow_existing)
        row=QHBoxLayout();self.generate_button=QPushButton('AI 계획 생성');self.refine_button=QPushButton('수정·재생성')
        self.generate_button.clicked.connect(lambda:self.generate(False));self.refine_button.clicked.connect(lambda:self.generate(True))
        row.addWidget(self.generate_button);row.addWidget(self.refine_button);body.addLayout(row)
        self.cancel_button=QPushButton('AI 요청 취소');self.cancel_button.clicked.connect(self.cancel);self.cancel_button.setEnabled(False);body.addWidget(self.cancel_button)
        self.request_status=QLabel('AI는 계획만 생성합니다. 적용은 3D 미리보기 확인 후 승인하세요.')
        self.request_status.setWordWrap(True);self.request_status.setTextFormat(Qt.TextFormat.PlainText);body.addWidget(self.request_status)
        tabs=QTabWidget();self.plan_text=QPlainTextEdit();self.plan_text.setReadOnly(True)
        self.context_text=QPlainTextEdit();self.context_text.setReadOnly(True)
        tabs.addTab(self.plan_text,'검증된 계획');tabs.addTab(self.context_text,'전달한 구역 정보');tabs.setMaximumHeight(210);body.addWidget(tabs)
        layout.addWidget(self.body);self.body.hide()
        self.timer=QTimer(self);self.timer.setInterval(15000);self.timer.timeout.connect(self.probe);self.timer.start()
        QTimer.singleShot(250,self.probe)

    def toggle_panel(self):
        self.body.setVisible(not self.body.isVisible())
        self.toggle.setText('AI 건축 설계 · 접기' if self.body.isVisible() else 'AI 건축 설계 · 펼치기')

    def show_panel(self):
        self.body.show();self.toggle.setText('AI 건축 설계 · 접기')
        QTimer.singleShot(0,lambda:getattr(self,'owner_scroll',self.owner.panel_scroll).ensureWidgetVisible(self.toggle,0,18))

    def probe(self):
        if self.closed or self.probe_job is not None or self.job is not None:return
        try:client=OllamaClient(self.url.text())
        except Exception as e:self.server_status.setText(str(e)+' · 수동 편집 가능');return
        self.settings.setValue('ai/url',self.url.text())
        self.server_status.setText('Ollama 자동 연결 확인 중…')
        job=Task(lambda p,c:client.models(c),self);self.probe_job=job
        job.result.connect(self._models);job.failed.connect(lambda e:self.server_status.setText('Ollama 연결 실패 · 수동 편집 가능\n'+e))
        job.finished.connect(self._probe_finished);job.finished.connect(job.deleteLater);job.start()

    def _models(self,names):
        desired=self.model.currentText()
        self.model.blockSignals(True);self.model.clear();self.model.addItems(names)
        if desired not in names:self.model.addItem(desired)
        self.model.setCurrentText(desired);self.model.blockSignals(False)
        self.server_status.setText('Ollama 연결됨 · '+(desired+' 준비됨' if desired in names else desired+' 미설치: ollama pull '+desired))

    def _probe_finished(self):self.probe_job=None

    def generate(self,refine=False):
        if self.closed or self.job is not None:return
        if self.owner.loading or self.owner.operation is not None:
            self.request_status.setText('현재 수동 작업이 끝난 뒤 요청하세요.');return
        prompt=self.prompt.toPlainText().strip()
        if not prompt:self.request_status.setText('한국어 건축 요청을 입력하세요.');return
        doc=self.owner.doc;bounds=doc.selection
        trace_id=uuid.uuid4().hex[:16];self.current_trace=trace_id;self.active_doc=doc
        try:
            self.chat_store.start(doc.project_id,trace_id,prompt,{'lo':list(bounds.lo),'hi':list(bounds.hi),'space_id':doc.active_space_id,'space_token':doc.scope_token()},model=self.model.currentText(),bindings=self.material_editor.bindings(),allow_existing=self.allow_existing.isChecked(),parent_trace=self.parent_trace,refine=refine)
        except Exception as exc:doc.chat_error=str(exc);event('chat_save_error',trace_id=trace_id,error=str(exc))
        self.parent_trace=None
        event('ai_request_started',trace_id=trace_id,critical=True,prompt=prompt,
              selection={'lo':bounds.lo,'hi':bounds.hi},revision=doc.revision)
        scope=(id(doc),bounds)
        previous=self.last_plan if refine and scope==self.last_scope else None
        if refine and previous is None:
            doc.chat_update(trace_id,status='error',error='같은 구역에서 먼저 AI 계획을 생성하세요.')
            self.request_status.setText('같은 구역에서 먼저 AI 계획을 생성하세요.');return
        try:client=OllamaClient(self.url.text())
        except Exception as e:
            doc.chat_update(trace_id,status='error',error=str(e))
            event('ai_request_error',trace_id=trace_id,error=str(e))
            self.request_status.setText(str(e));return
        model=self.model.currentText().strip();timeout=self.timeout.value()
        self.settings.setValue('ai/url',self.url.text());self.settings.setValue('ai/model',model);self.settings.setValue('ai/timeout',timeout)
        self.owner.reject()
        bindings=self.material_editor.bindings()
        try:
            pinned,states=prepare_material_request(doc,prompt,bindings)
            self.sync_palette()
        except Exception as e:
            doc.chat_update(trace_id,status='error',error=str(e))
            event('ai_material_error',trace_id=trace_id,critical=True,error=str(e))
            self.request_status.setText('재료 해석 실패 · 블록 목록에서 선택하세요.\n'+str(e));return
        epoch=self.owner.preview_epoch;revision=doc.revision
        self.serial+=1;serial=self.serial
        event('ai_request',trace_id=trace_id,critical=True,prompt=prompt,model=model,
              selection={'lo':bounds.lo,'hi':bounds.hi,'space_id':doc.active_space_id,'space_token':doc.scope_token()}, revision=revision,
              allow_existing=self.allow_existing.isChecked(), refine=bool(refine),bindings=bindings)
        self.generate_button.setEnabled(False);self.refine_button.setEnabled(False);self.cancel_button.setEnabled(True)
        self.request_status.setText('AI 계획 생성 중 · 수동 편집 가능 (설계/구역이 바뀌면 결과 폐기)')
        allow=self.allow_existing.isChecked()
        job=Task(lambda p,c:generate_design(client,model,doc,bounds,prompt,allow,previous,timeout,c,p,bindings,trace_id,lambda **fields:doc.chat_update(trace_id,**fields)),self)
        self.job=job
        job.progress.connect(lambda n,t:self.request_status.setText(t) if serial==self.serial else None)
        job.result.connect(lambda result:self._result(result,doc,bounds,revision,epoch,serial))
        job.failed.connect(lambda e:self._failure(e,serial,doc,trace_id))
        job.cancelled.connect(lambda:doc.chat_update(trace_id,status='cancelled'))
        job.cancelled.connect(lambda:self.request_status.setText('AI 요청을 취소했습니다. 수동 편집은 유지됩니다.'))
        job.finished.connect(self._finished);job.finished.connect(job.deleteLater);job.start()

    def _result(self,result,doc,bounds,revision,epoch,serial):
        trace=result['proposal'].trace_id or self.current_trace
        if self.closed or serial!=self.serial:
            doc.chat_update(trace,status='discarded',reason='취소/닫힘 후 응답');return
        if self.owner.doc is not doc or doc.revision!=revision or doc.selection!=bounds or self.owner.preview_epoch!=epoch:
            doc.chat_update(trace,status='discarded',reason='선택/설계 변경 후 오래된 응답')
            event('ai_result_discarded',trace_id=self.current_trace,critical=True,reason='selection/revision/preview changed')
            self.request_status.setText('요청 중 구역/설계/미리보기가 변경되어 AI 결과를 폐기했습니다. 다시 생성하세요.');return
        actual=review(doc,result['proposal']);result['actual']=actual
        doc.chat_update(trace,status='blocked' if actual['hull_risk'] else 'preview' if actual['changed'] else 'no_change',plan=result['plan'],actual=actual,model_claim=result['plan']['summary'],protected_skipped=result['proposal'].protected,occupied_skipped=result['proposal'].occupied,outside_space_skipped=result['proposal'].outside_space,space_id=doc.active_space_id)
        try:self.chat_store.changes(doc.project_id,trace,result['proposal'],doc.schematic.palette,doc.schematic.size)
        except Exception as exc:doc.chat_error=str(exc)
        self.last_plan=result['plan'];self.last_scope=(id(doc),bounds);self.last_result=result
        self.plan_text.setPlainText(json.dumps(result['plan'],ensure_ascii=False,indent=2))
        self.context_text.setPlainText(json.dumps(result['context'],ensure_ascii=False,indent=2))
        self.material_editor.show_result(result)
        presented=self.owner._preview_ready(result['proposal'],epoch)
        if presented is False:
            self.request_status.setText('표시 상태 때문에 미리보기를 차단했습니다. 표시 전체 복원(Alt+H) 후 다시 생성/검증하세요. 실제 변경은 없습니다.');self.chat_view.refresh(True);return
        count=len(result['proposal'].indices)
        event('ai_preview_ready',trace_id=self.current_trace,critical=True,changed=count,
              attempts=result['attempts'],plan=result['plan'])
        self.chat_view.refresh(True)
        self.request_status.setText(f"Python 검증 완료 · 변경 {count:,}개 · {result['attempts']}회 응답. 3D 비교 후 승인하세요." if count else '변경 0개. 계획 설명/보호 제외를 확인하고 요청을 수정하세요.')

    def sync_palette(self):
        owner=self.owner
        for index,state in owner.doc.schematic.palette.items():
            if owner.material.findData(index)<0:owner.material.addItem(state,index)
            if state!='minecraft:air' and owner.source_material.findData(index)<0:owner.source_material.addItem(state,index)

    def _failure(self,error,serial,doc=None,trace=None):
        (doc or self.owner.doc).chat_update(trace or self.current_trace,status='error',error=error)
        if serial==self.serial:
            event('ai_failure',trace_id=self.current_trace,critical=True,error=error)
            self.request_status.setText('AI 생성/검증 실패 · 수동 편집 가능\n'+error)

    def _finished(self):
        self.job=None;self.generate_button.setEnabled(True);self.refine_button.setEnabled(True);self.cancel_button.setEnabled(False)

    def cancel(self):
        if self.job is not None and self.active_doc:self.active_doc.chat_update(self.current_trace,status='cancelled',reason='사용자 취소/문서 전환')
        if self.job is not None:event('ai_cancel_requested',trace_id=self.current_trace,critical=True)
        self.serial+=1
        if self.job is not None:self.job.requestInterruption()
        self.request_status.setText('AI 취소 요청 중…')

    def reset_document(self):
        self.cancel();self.active_doc=None;self.current_trace=None;self.last_plan=None;self.last_scope=None;self.last_result=None
        self.plan_text.clear();self.context_text.clear();self.material_editor.clear()
        self.request_status.setText('AI는 계획만 생성합니다. 적용은 3D 미리보기 확인 후 승인하세요.')

    def attach_document(self,doc):
        doc.chat_store=self.chat_store
        if doc.chat_seed:
            try:
                if doc.chat_seed.get('project_id')!=doc.project_id:raise ValueError('대화 프로젝트 ID 불일치')
                self.chat_store.restore(doc.chat_seed)
            except Exception as exc:doc.chat_error=str(exc)
        self.chat_store.recover(doc.project_id);self.chat_view.refresh(True)

    def busy(self):return self.job is not None or self.probe_job is not None

    def shutdown(self):
        self.closed=True;self.timer.stop();self.chat_view.timer.stop();self.cancel()
        if self.probe_job is not None:self.probe_job.requestInterruption()
