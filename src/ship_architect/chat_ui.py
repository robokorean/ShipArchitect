"""Searchable chat view. AI claims and Python verified results are separate bubbles."""
import html,json
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QListWidget,QTextBrowser,QPushButton,QFileDialog,QApplication,QSplitter,QLabel
from .model import Bounds

STATUS={'running':'설계 중','preview':'미승인 미리보기','blocked':'차단','approved':'승인·적용','cancelled':'취소','error':'오류','no_change':'변경 없음','discarded':'오래된 결과 폐기','undone':'실행 취소','redone':'다시 실행','interrupted':'이전 실행 중단','preview_expired':'이전 미승인 제안 종료'}

class ChatView(QWidget):
    def __init__(self,panel):
        super().__init__();self.panel=panel;self.version=None;self.project=None
        layout=QVBoxLayout(self);self.search=QLineEdit();self.search.setPlaceholderText('명령 · 응답 · 상태 · 작업 ID 검색');layout.addWidget(self.search)
        splitter=QSplitter();self.list=QListWidget();self.browser=QTextBrowser();self.browser.setOpenExternalLinks(False);splitter.addWidget(self.list);splitter.addWidget(self.browser);splitter.setStretchFactor(1,2);layout.addWidget(splitter,1)
        row=QHBoxLayout()
        for title,action in [('명령 복사',self.copy),('수정할 명령 불러오기',self.load),('새 계획 재실행',self.rerun),('TXT/JSON 내보내기',self.export)]:
            button=QPushButton(title);button.clicked.connect(action);row.addWidget(button)
        layout.addLayout(row);self.status=QLabel('대화는 프로젝트별 로컬 자동 저장됩니다. 미승인 블록은 적용하지 않습니다.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.list.currentRowChanged.connect(self.display);self.search.textChanged.connect(lambda _:self.refresh(True))
        self.timer=QTimer(self);self.timer.setInterval(750);self.timer.timeout.connect(self.refresh);self.timer.start()

    def refresh(self,force=False):
        if self.panel.closed:return
        doc=self.panel.owner.doc
        if doc is None:return
        store=self.panel.chat_store
        if doc.chat_error:self.status.setText('대화 자동 저장 오류: '+doc.chat_error+' · 저장 경로를 확인하세요.')
        if not force and self.project==doc.project_id and self.version==store.generation:return
        selected=self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.project=doc.project_id;self.version=store.generation;query={'승인':'approved','취소':'cancelled','오류':'error','차단':'blocked'}.get(self.search.text(),self.search.text());entries=store.entries(self.project,query);self.list.blockSignals(True);self.list.clear()
        for e in entries:
            self.list.addItem(STATUS.get(e['status'],e['status'])+' · '+e['prompt'][:65]+'\n'+e['trace_id']);self.list.item(self.list.count()-1).setData(Qt.ItemDataRole.UserRole,e['trace_id'])
        row=next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole)==selected),0)
        self.list.setCurrentRow(row);self.list.blockSignals(False);self.display()
        if doc.chat_error:self.status.setText('대화 자동 저장 오류: '+doc.chat_error+' · 저장 경로를 확인하세요.')

    def selected(self):
        item=self.list.currentItem()
        return self.panel.chat_store.get(self.panel.owner.doc.project_id,item.data(Qt.ItemDataRole.UserRole)) if item else None

    def display(self,*_):
        e=self.selected()
        if not e:self.browser.setPlainText('이 프로젝트의 대화가 없습니다.');return
        def bubble(label,text,color):return '<div style="background:'+color+';padding:12px;margin:8px"><b>'+label+'</b><pre style="white-space:pre-wrap">'+html.escape(str(text))+'</pre></div>'
        parts=[bubble('사용자',e['prompt'],'#29415b'),bubble('작업 / 선택 구역',e['trace_id']+'\n'+json.dumps(e['selection'],ensure_ascii=False),'#223344')]
        verified={k:v for k,v in e.get('actual',{}).items() if k in ('changed','added','removed','replaced','bbox','materials','warnings','space_id','outside_space_skipped','occupied_skipped','exterior_skipped','locked_skipped','operation_diagnostics','applied')}
        verified['protected_skipped']=e.get('protected_skipped',0)
        parts.append(bubble('Python 실제 결과 / 상태: '+STATUS.get(e['status'],e['status']),json.dumps(verified,ensure_ascii=False,indent=2),'#34384b'))
        for raw in e.get('responses',[]):parts.append(bubble('AI 응답 · 제안이며 미실행 주장',raw,'#263b3d'))
        if e.get('plan'):parts.append(bubble('검증된 설계 JSON',json.dumps(e['plan'],ensure_ascii=False,indent=2),'#263b3d'))
        if e.get('error'):parts.append(bubble('오류 · 적용하지 않음',e['error'],'#573434'))
        parts.append(bubble('상태 이력',json.dumps(e.get('steps',[]),ensure_ascii=False,indent=2),'#223344'));self.browser.setHtml(''.join(parts))

    def copy(self):
        e=self.selected()
        if e:QApplication.clipboard().setText(e['prompt'])

    def load(self):
        e=self.selected()
        if not e:return False
        try:
            doc=self.panel.owner.doc;sid=e['selection'].get('space_id')
            if sid:
                sp=next((s for s in doc.spaces if s['id']==sid),None)
                if sp is None:raise ValueError('이 기록의 공간이 삭제되었습니다. 공간을 복원한 뒤 다시 실행하세요. 경계 상자로 대체하지 않습니다.')
                from .space_model import bbox
                b=Bounds(*bbox(sp))
            else:b=Bounds(tuple(e['selection']['lo']),tuple(e['selection']['hi']))
            b.validate(doc.schematic.size)
            self.panel.owner.reject()
            doc.active_space_id=sid;self.panel.owner.set_selection(b);self.panel.owner.space_manager.refresh();self.panel.prompt.setPlainText(e['prompt'])
            self.panel.last_plan=e.get('plan');self.panel.last_scope=(id(self.panel.owner.doc),b);self.panel.last_result=None
            self.panel.material_editor.populating=True;self.panel.material_editor.table.setRowCount(0);self.panel.material_editor.populating=False
            self.panel.material_editor.counts.setPlainText('과거 설계는 참고용입니다. 새 계획 생성·검증 후 승인하세요.');self.panel.material_editor.rebuild_button.setEnabled(False)
            for kind,combo in self.panel.material_editor.roles.items():combo.setCurrentText(e.get('bindings',{}).get(kind,''))
            self.panel.allow_existing.setChecked(e.get('allow_existing',False));self.panel.parent_trace=e['trace_id'];self.panel.show_panel();return True
        except Exception as exc:self.status.setText('현재 문서에서 기록을 재실행할 수 없습니다: '+str(exc));return False

    def rerun(self):
        if self.load():self.panel.generate(bool(self.panel.last_plan))

    def export(self):
        doc=self.panel.owner.doc;path,_=QFileDialog.getSaveFileName(self,'현재 프로젝트 대화 내보내기','chat_'+doc.project_id[:8]+'.json','JSON (*.json);;TXT (*.txt)')
        if not path:return
        try:self.panel.chat_store.export(doc.project_id,path,'txt' if path.lower().endswith('.txt') else 'json');self.status.setText('대화 기록 내보내기 완료')
        except Exception as exc:self.status.setText(str(exc))
