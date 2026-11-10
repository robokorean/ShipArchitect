import json
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QListWidget,QPlainTextEdit,QLabel,QCheckBox
from .model import Bounds
from .spaces import recognize

class SpaceView(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.result=None;layout=QVBoxLayout(self)
        row=QHBoxLayout();scan=QPushButton('현재 선택의 공간 후보 분석');scan.clicked.connect(self.scan);row.addWidget(scan)
        stop=QPushButton('분석 취소');stop.clicked.connect(lambda:owner.operation.requestInterruption() if owner.operation else None);row.addWidget(stop)
        self.overlay=QCheckBox('선택 후보의 3D 부피 표시');self.overlay.setChecked(True);self.overlay.toggled.connect(self.show_overlay);row.addWidget(self.overlay);layout.addLayout(row)
        self.status=QLabel('높이 3칸 이상, 최대 8백만 칸. 바닥 → 연결된 머리 공간 → 벽·천장 검사. 자동 적용하지 않습니다.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.list=QListWidget();self.list.currentRowChanged.connect(self.describe);layout.addWidget(self.list,1)
        self.text=QPlainTextEdit();self.text.setReadOnly(True);layout.addWidget(self.text,1)
        row=QHBoxLayout();select=QPushButton('후보를 3D 선택으로 확인');select.clicked.connect(self.select);row.addWidget(select);confirm=QPushButton('확인한 후보를 구역으로 저장');confirm.clicked.connect(self.confirm);row.addWidget(confirm);layout.addLayout(row)
    def scan(self):
        if hasattr(self,'bulk'):return self.bulk.scan()
        if self.owner.operation is not None or self.owner.loading:return
        doc=self.owner.doc;b=doc.selection
        self.owner.run_operation(lambda p,c:recognize(doc,b,p,c),lambda r:self.ready(r,doc),'공간 후보 분석')
    def ready(self,result,doc):
        if result.get('method') in ('3d_air_components','exterior_voxel_mesh') and hasattr(self,'bulk'):return self.bulk.receive(result,doc)
        if self.owner.doc is not doc or doc.revision!=result['revision']:self.status.setText('설계가 바뀌어 분석 결과를 폐기했습니다.');return
        self.result=result;doc.space_candidates=result['candidates'];doc.space_revision=doc.revision;self.list.clear()
        names={'room':'방 후보','corridor':'통로 후보','open_area':'열린 공간 후보'}
        for c in doc.space_candidates:self.list.addItem(f"{names[c['kind']]} · Y={c['floor_y']} · 바닥 {c['area']}칸 · 벽 {c['wall_ratio']:.0%} / 천장 {c['ceiling_ratio']:.0%}")
        self.status.setText(f"후보 {len(doc.space_candidates)}개 · "+result['note']);self.show_overlay()
        if doc.space_candidates:self.list.setCurrentRow(0)
    def current(self):
        if self.list.currentItem() is not None:
            from PySide6.QtCore import Qt
            key=self.list.currentItem().data(Qt.ItemDataRole.UserRole)
            if key:return next((c for c in self.owner.doc.space_candidates if c['id']==key),None)
        row=self.list.currentRow();c=self.owner.doc.space_candidates
        return c[row] if 0<=row<len(c) else None
    def describe(self,*_):
        c=self.current()
        if c:self.text.setPlainText(json.dumps({k:v for k,v in c.items() if k not in ('volume_runs','footprint')}|{'footprint_rows':len(c.get('footprint',[])),'exact_volume_runs':len(c.get('volume_runs',[]))},ensure_ascii=False,indent=2));self.show_overlay()
    def show_overlay(self,*_):
        self.owner.view.candidate_boxes=([self.current()] if self.current() is not None else []) if self.overlay.isChecked() else [];self.owner.view.update()
    def select(self):
        if self.owner.doc.space_revision!=self.owner.doc.revision:self.status.setText('문서가 바뀌었습니다. 공간 후보를 다시 분석하세요.');return
        c=self.current()
        if not c:return
        self.owner.set_selection(Bounds(tuple(c['lo']),tuple(c['hi'])));self.owner.view.focus_selection();self.owner.tabs.setCurrentWidget(self.owner.view)
    def confirm(self):
        if self.current() and self.current().get('method') in ('3d_air_components','exterior_voxel_mesh') and hasattr(self,'bulk'):return self.bulk.confirm()
        d=self.owner.doc
        if d.space_revision!=d.revision:self.status.setText('문서가 바뀌었습니다. 공간 후보를 다시 분석하세요.');return
        c=self.current()
        if not c or self.owner.operation is not None:return
        from .space_model import from_candidate
        revision=d.revision
        def done(space):
            if self.owner.doc is not d or d.revision!=revision:self.status.setText('문서가 바뀌어 공간 저장을 취소했습니다.');return
            try:
                if len(d.spaces)>=512 or sum(len(s['runs']) for s in d.spaces)+len(space['runs'])>500_000:raise ValueError('전체 공간 셀 구간/공간 수 한도 초과')
                self.owner.reject();b=Bounds(tuple(c['lo']),tuple(c['hi']));name='확인 공간 '+c['id'];d.add_zone(name,b);c['confirmed']=True;d.space_revision=d.revision
                d.zones[-1]['space_footprint']=c['footprint'];d.zones[-1]['space_kind']=c['kind']
                record=d.undo_stack[-1];record['spaces_before']=json.loads(json.dumps(d.spaces));record['active_before']=d.active_space_id
                d.spaces.append(space);d.active_space_id=space['id'];record['spaces_after']=json.loads(json.dumps(d.spaces));record['active_after']=d.active_space_id
                record['zones_after']=json.loads(json.dumps(d.zones))
                self.owner.space_manager.refresh();self.owner.space_manager.detect_floors();self.owner.refresh_zones();self.owner.refresh_history();self.status.setText('정확한 공간과 확인 구역을 저장했습니다. 블록은 변경하지 않았습니다.')
            except Exception as exc:self.owner.error(exc)
        if c['area']*(c['hi'][1]-c['floor_y']+1)>100_000:
            self.owner.run_operation(lambda p,cancel:from_candidate(d,c,cancel),done,'정확한 공간 저장')
        else:
            try:done(from_candidate(d,c))
            except Exception as exc:self.owner.error(exc)
    def reset(self):
        self.result=None;self.list.clear();self.text.clear();self.owner.view.candidate_boxes=[];self.owner.view.update()
