"""User-controlled material bindings, searchable registry and recompiled previews."""
import copy,json,uuid
from collections import Counter
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QPlainTextEdit,QTableWidget,QTableWidgetItem,QHeaderView,QDialog,QLineEdit,QListWidget,QDialogButtonBox,QFileDialog)
from .block_registry import vanilla
from .materials import CATALOG
from .ai_design import build_context,compile_plan

ROLES=(('floor','바닥'),('walls','벽'),('ceiling','천장'),('column','기둥'))


class BlockPicker(QDialog):
    def __init__(self,doc,parent=None):
        super().__init__(parent);self.doc=doc;self.selected=None;self.setWindowTitle('Minecraft 1.21.1 블록 목록 / 기존 팔레트');self.resize(720,520)
        layout=QVBoxLayout(self);self.search=QLineEdit();self.search.setPlaceholderText('한국어 이름 / minecraft:ID / 모드 ID 검색');layout.addWidget(self.search)
        self.list=QListWidget();layout.addWidget(self.list);self.status=QLabel('장치와 미검증 모드 배치는 차단합니다.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.entries={key:row['ko'] for key,row in vanilla().items()}
        for state in doc.schematic.palette.values():self.entries.setdefault(state,'기존 파일의 정확한 상태')
        for state in doc.schematic.mod_registry:self.entries.setdefault(state,'등록한 모드 블록')
        self.search.textChanged.connect(self.refresh);self.refresh()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.choose);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.list.itemDoubleClicked.connect(lambda _:self.choose())
    def refresh(self):
        query=''.join(self.search.text().lower().split());self.list.clear()
        for key,label in sorted(self.entries.items()):
            if not query or query in ''.join((key+' '+label).lower().split()):
                self.list.addItem(label+' | '+key);self.list.item(self.list.count()-1).setData(Qt.ItemDataRole.UserRole,key)
    def choose(self):
        item=self.list.currentItem()
        if not item:return
        try:self.selected=self.doc.validate_material(item.data(Qt.ItemDataRole.UserRole))
        except ValueError as e:self.status.setText(str(e));return
        self.accept()


class MaterialEditor(QWidget):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.populating=False;layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        label=QLabel('역할별 재료 지정 · 빈칸은 요청문에서 해석');label.setWordWrap(True);layout.addWidget(label)
        self.roles={}
        for kind,title in ROLES:
            row=QHBoxLayout();row.addWidget(QLabel(title));combo=QComboBox();combo.setEditable(True);combo.addItem('');combo.addItems(CATALOG);combo.lineEdit().setPlaceholderText('이름 또는 정확한 블록 ID[상태]');combo.setCurrentIndex(0);row.addWidget(combo,1);layout.addLayout(row);self.roles[kind]=combo
        row=QHBoxLayout();self.pick_role=QComboBox()
        for kind,title in ROLES:self.pick_role.addItem(title,kind)
        pick=QPushButton('블록 목록에서 선택');pick.clicked.connect(self.pick);row.addWidget(self.pick_role);row.addWidget(pick);layout.addLayout(row)
        import_button=QPushButton('모드 블록 검증 목록 불러오기');import_button.clicked.connect(self.import_registry);layout.addWidget(import_button)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['연산','상대 범위','블록 ID · 직접 수정'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(120);self.table.setMaximumHeight(210);self.table.setWordWrap(False);self.table.setColumnWidth(0,48);self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Interactive);self.table.setColumnWidth(1,80);self.table.cellChanged.connect(self.edited);layout.addWidget(self.table)
        self.rebuild_button=QPushButton('수정한 재료로 다시 검증 · 미리보기');self.rebuild_button.clicked.connect(self.rebuild);layout.addWidget(self.rebuild_button)
        self.counts=QPlainTextEdit();self.counts.setReadOnly(True);self.counts.setMaximumHeight(110);self.counts.setPlaceholderText('미리보기의 최종 블록 ID별 변경 수');layout.addWidget(self.counts)

    def bindings(self):return {k:v.currentText().strip() for k,v in self.roles.items() if v.currentText().strip()}

    def pick(self):
        dlg=BlockPicker(self.panel.owner.doc,self)
        if dlg.exec()==QDialog.DialogCode.Accepted:self.roles[self.pick_role.currentData()].setCurrentText(dlg.selected)

    def import_registry(self):
        path,_=QFileDialog.getOpenFileName(self,'모드 블록 목록',filter='블록 목록 (*.json)')
        if not path:return
        try:
            p=Path(path)
            if p.stat().st_size>2_000_000:raise ValueError('모드 목록은 2MB 이하여야 합니다.')
            self.panel.owner.reject();self.panel.owner.doc.import_mod_registry(json.loads(p.read_text(encoding='utf-8')))
            self.panel.request_status.setText('모드 목록/상태를 등록했습니다. 현재 게임 설치 여부의 실시간 확인은 아닙니다. NBT 장치는 계속 보호합니다.')
        except Exception as e:self.panel.request_status.setText(str(e))

    def show_result(self,result):
        self.rebuild_button.setEnabled(True)
        plan=result['plan'];p=result['proposal'];doc=self.panel.owner.doc
        self.populating=True;self.table.setRowCount(len(plan['operations']))
        for row,op in enumerate(plan['operations']):
            for col,value in enumerate((dict(ROLES).get(op['kind'],op['kind']),str(op['min'])+' → '+str(op['max']),op['material'])):
                item=QTableWidgetItem(value)
                if col!=2:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(value);self.table.setItem(row,col,item)
        self.populating=False
        counts=Counter(doc.schematic.palette[int(i)] for i in p.after)
        states=sorted({op['material'] for op in plan['operations']})
        self.counts.setPlainText('\n'.join(f'{state} : 최종 변경 {counts[state]:,}개\n  '+doc.registry().provenance(state) for state in states))

    def clear(self):
        self.populating=True;self.table.setRowCount(0);self.populating=False;self.counts.clear()
        for combo in self.roles.values():combo.setCurrentText('')

    def edited(self,row,column):
        if self.populating or column!=2:return
        self.panel.owner.reject()  # an edited table must never approve the stale preview
        self.counts.setPlainText('재료 수정 미검증 · 다시 검증/미리보기 후 승인하세요.')
        self.panel.request_status.setText('재료가 수정되어 이전 미리보기 승인을 해제했습니다.')

    def rebuild(self):
        panel=self.panel;owner=panel.owner;doc=owner.doc;b=doc.selection
        if panel.job is not None or owner.operation is not None or owner.loading:return
        if panel.last_plan is None or panel.last_result is None or panel.last_scope!=(id(doc),b):panel.request_status.setText('같은 구역에서 먼저 계획을 생성하세요.');return
        try:
            plan=copy.deepcopy(panel.last_plan);states=[]
            for row,op in enumerate(plan['operations']):
                raw=self.table.item(row,2).text().strip()
                state=doc.registry().resolve(raw) if op['kind']=='erase' else doc.validate_material(raw)
                op['material']=state;states.append(state)
            # No partial registrations on unknown names or invalid states.
            for state in states:
                if state!='minecraft:air':doc.add_material(state)
            owner.reject();panel.sync_palette();revision=doc.revision;epoch=owner.preview_epoch;serial=panel.serial
            trace=uuid.uuid4().hex[:16];panel.current_trace=trace
            panel.chat_store.start(doc.project_id,trace,'재료 직접 수정: '+plan['summary'],{'lo':list(b.lo),'hi':list(b.hi)},parent_trace=(panel.last_result['proposal'].trace_id),model='사용자 재료 수정',bindings={},allow_existing=panel.last_result['context']['allow_existing'])
            allow=panel.last_result['context']['allow_existing']
            def worker(progress,cancel):
                ctx=build_context(doc,b,allow,cancel);ctx['allowed_materials']=list(dict.fromkeys(states+ctx['allowed_materials']))[:128]
                p=compile_plan(doc,b,plan,ctx,revision,cancel,trace)
                return {**panel.last_result,'plan':plan,'context':ctx,'proposal':p,'attempts':0}
            def completed(result):
                panel._result(result,doc,b,revision,epoch,serial)
                panel.request_status.setText('사용자 재료 수정 검증 완료 · 새 3D 미리보기 확인 후 승인하세요.')
                for kind,_ in ROLES:
                    chosen={op['material'] for op in plan['operations'] if op['kind']==kind}
                    if len(chosen)==1:self.roles[kind].setCurrentText(next(iter(chosen)))
            owner.run_operation(worker,completed,'재료 수정 검증')
        except Exception as e:
            owner.reject();panel.request_status.setText('재료 검증 실패 · 적용하지 않았습니다.\n'+str(e))
