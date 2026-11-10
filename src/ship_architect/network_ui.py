"""Connection inspection, conservative routes and read-only equipment inventory UI."""
import json,copy
import numpy as np
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QColor,QPen
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QComboBox,QCheckBox,QLabel,QListWidget,QPlainTextEdit,QSplitter,QLineEdit,QTableWidget,QTableWidgetItem,QSpinBox
from .navigation import Navigation
from .equipment import inventory,search_records
from .diagnostics import event as diagnostic_event
from .model import Bounds
from .space_model import bbox
STATUS={'connected':'모델상 통행 가능','uncertain':'불확실','disconnected':'모델 경로 없음','blocked':'닫힌 문 차단','overlap':'공간 겹침'}

def draw_route_3d(view,painter):
    if not getattr(view,'show_routes',True):return
    path=getattr(view,'route_path',[])
    if len(path)<2:return
    points=np.asarray(path,float)+[.5,.16,.5];projected=view.project(points)
    painter.setPen(QPen(QColor('#ffda65'),3))
    for i in range(1,len(path)):
        if view.single_layer and not(min(path[i-1][1],path[i][1])<=view.layer<=max(path[i-1][1],path[i][1])):continue
        if view.cut and min(path[i-1][1],path[i][1])>view.layer:continue
        painter.drawLine(QPointF(*projected[i-1]),QPointF(*projected[i]))
    for point,label,color in ((projected[0],'S','#72e3a2'),(projected[-1],'G','#ff8f86')):
        painter.setPen(QPen(QColor(color),3));painter.drawEllipse(QPointF(*point),5,5);painter.drawText(QPointF(point[0]+6,point[1]-6),label)
    painter.drawText(16,85,'노란색: 통행 모델 경로 · 실제 게임 이동/장치 작동은 별도 확인')

def draw_route_2d(plan,painter):
    if not getattr(plan.manager.owner.view,'show_routes',True):return
    path=getattr(plan.manager.owner.view,'route_path',[]);painter.setPen(QPen(QColor('#ffda65'),3))
    for a,b in zip(path,path[1:]):
        if min(a[1],b[1])<=plan.layer<=max(a[1],b[1]):painter.drawLine(QPointF(plan.ox+(a[0]+.5)*plan.scale,plan.oz+(a[2]+.5)*plan.scale),QPointF(plan.ox+(b[0]+.5)*plan.scale,plan.oz+(b[2]+.5)*plan.scale))

class NetworkView(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.nav=None;self.snapshot=None;self.equipment=None;self.analysis_doc=None
        lay=QVBoxLayout(self);row=QHBoxLayout();self.source=QComboBox();self.target=QComboBox();row.addWidget(QLabel('출발 공간'));row.addWidget(self.source,1);row.addWidget(QLabel('도착 공간'));row.addWidget(self.target,1);lay.addLayout(row)
        row=QHBoxLayout();self.local=QCheckBox('현재 선택 범위만 분석');row.addWidget(self.local)
        for text,fn in [('연결 분석',self.analyze),('경로 찾기',self.find),('경로 지우기',self.clear_route),('선택 공간으로 카메라 이동',self.focus)]:
            b=QPushButton(text);b.clicked.connect(fn);row.addWidget(b)
        lay.addLayout(row);self.status=QLabel('확인된 통행 모델 / 불확실 / 공간 겹침을 구분합니다. 엘리베이터는 작동 미확인 후보입니다.');self.status.setWordWrap(True);lay.addWidget(self.status)
        split=QSplitter();self.connections=QListWidget();self.connections.currentRowChanged.connect(self.describe);split.addWidget(self.connections);self.details=QPlainTextEdit();self.details.setReadOnly(True);split.addWidget(self.details);lay.addWidget(split,1)
        row=QHBoxLayout();b=QPushButton('모드 설비 분석');b.clicked.connect(self.scan_equipment);row.addWidget(b);self.filter=QLineEdit();self.filter.setPlaceholderText('설비 검색: 블록 ID / 종류 / 공간 이름');self.filter.textChanged.connect(self.equipment_filter_changed);row.addWidget(self.filter,1);lay.addLayout(row)
        self.page=QSpinBox();self.page.setPrefix('설비 페이지 ');self.page.setRange(1,100000);self.page.valueChanged.connect(self.show_equipment);lay.addWidget(self.page)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['종류','블록 상태 ID','X / Y / Z','소속 / 인접 공간','작동']);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.itemSelectionChanged.connect(self.equipment_selected);lay.addWidget(self.table,1)
        self.source.currentIndexChanged.connect(self.list_connections)
    def refresh_spaces(self):
        d=self.owner.doc
        if d is None:return
        ids=(self.source.currentData(),self.target.currentData());self.source.blockSignals(True);self.target.blockSignals(True);self.source.clear();self.target.clear()
        for s in sorted(d.spaces,key=lambda s:(bbox(s)[0][1],s['name'])):
            lo,hi=bbox(s);label=f"{s['name']} · Y={lo[1]}~{hi[1]}";self.source.addItem(label,s['id']);self.target.addItem(label,s['id'])
        for combo,sid in zip((self.source,self.target),ids):
            at=combo.findData(sid)
            if at>=0:combo.setCurrentIndex(at)
        if ids[1] is None and self.target.count()>1:self.target.setCurrentIndex(1)
        self.source.blockSignals(False);self.target.blockSignals(False)
        if self.analysis_doc is not d:self.reset()
        elif self.snapshot!=self.signature():self.invalidate()
        self.list_connections()
    def signature(self):
        from .space_model import token
        d=self.owner.doc
        block_events=tuple((h['action'],h.get('time')) for h in d.history if h.get('blocks',0)>0 and h['action'] in ('approve','undo','redo'))
        return (id(d),block_events,tuple(token(s) for s in d.spaces),json.dumps(d.analysis_state.get('equipment_rules',{}),sort_keys=True))
    def reset(self):
        self.nav=None;self.equipment=None;self.snapshot=None;self.analysis_doc=None;self.connections.clear();self.table.setRowCount(0);self.details.clear();self.clear_route()
    def invalidate(self):
        self.nav=None;self.equipment=None;self.snapshot=None;self.connections.clear();self.table.setRowCount(0);self.clear_route();self.status.setText('프로젝트/공간이 바뀌었습니다. 연결과 설비를 다시 분석하세요.')
    def analyze(self):
        if hasattr(self,'pages'):
            self.pages.setCurrentIndex(0);self.owner.integrated_ui.tasks.show(self.owner.integrated_ui.tasks.connection_results)
        d=self.owner.doc
        if self.owner.operation is not None:return
        if not d.spaces:self.status.setText('공간 관리에서 공간을 먼저 만들거나 가져오세요.');return
        signature=self.signature();bounds=d.selection if self.local.isChecked() else None
        def work(progress,cancel):
            nav=Navigation(d,bounds,progress,cancel);equipment=inventory(d,progress,cancel);return nav,equipment
        def done(result):
            if self.owner.doc is not d or signature!=self.signature():self.status.setText('분석 중 문서가 변경되어 폐기했습니다.');return
            self.nav,self.equipment=result;self.snapshot=signature;self.analysis_doc=d;self.list_connections();self.show_equipment();self.status.setText(f"통행 셀 {self.nav.observations['walkable_cells']:,} · 성분 {self.nav.components:,} · 겹침은 연결과 별도 표시 · 작동 미확인 설비 {self.equipment['total']:,}")
        self.owner.run_operation(work,done,'문·계단·통행 연결 분석')
    def pair(self,left,right):
        result=self.nav.pair(left,right)
        if result['status'] in ('uncertain','disconnected') and self.equipment:
            links=[e for e in self.equipment['elevator_candidates'] if set(e['spaces'])=={left,right}]
            if links:result=dict(status='uncertain',reason='Create 엘리베이터 층 연결 후보. 작동 정보가 없어 통행 경로로 사용하지 않습니다.',elevator_evidence=links)
        return result
    def list_connections(self,*_):
        self.connections.clear();self.rows=[]
        if self.nav is None:return
        if self.snapshot!=self.signature():self.invalidate();return
        left=self.source.currentData()
        if left not in self.nav.members:return
        for s in self.owner.doc.spaces:
            if s['id']==left:continue
            result=self.pair(left,s['id']);self.rows.append((s,result));self.connections.addItem(f"{s['name']} · {STATUS[result['status']]} · {result['reason']}")
    def describe(self,row):
        if 0<=row<len(getattr(self,'rows',[])):
            s,r=self.rows[row];self.target.setCurrentIndex(self.target.findData(s['id']));self.details.setPlainText(json.dumps(r,ensure_ascii=False,indent=2))
    def find(self):
        if hasattr(self,'pages'):
            self.pages.setCurrentIndex(1);self.owner.integrated_ui.tasks.show(self.owner.integrated_ui.tasks.connection_results)
        if self.owner.operation is not None:return
        if self.nav is None or self.snapshot!=self.signature():self.status.setText('현재 설계로 연결 분석을 먼저 실행하세요.');return
        left,right=self.source.currentData(),self.target.currentData()
        if left==right:self.status.setText('다른 출발·도착 공간을 선택하세요.');return
        pair=self.pair(left,right)
        if pair['status']!='connected':self.clear_route();self.details.setPlainText(json.dumps(pair,ensure_ascii=False,indent=2));self.status.setText(STATUS[pair['status']]+' · '+pair['reason']);return
        nav=self.nav;signature=self.signature()
        def done(result):
            if signature!=self.signature():self.clear_route();return
            self.details.setPlainText(json.dumps({k:v for k,v in result.items() if k!='path'},ensure_ascii=False,indent=2));path=result.get('path',[])
            # Compress collinear segments only; every underlying step was validated.
            compressed=[]
            for point in path:
                if len(compressed)>1 and np.array_equal(np.sign(np.array(point)-compressed[-1]),np.sign(np.array(compressed[-1])-compressed[-2])):compressed[-1]=point
                else:compressed.append(point)
            self.owner.view.route_path=compressed
            if path:
                points=np.asarray(path);lo=tuple(int(max(0,a-2)) for a in points.min(axis=0));hi=tuple(int(min(dim-1,b+2)) for dim,b in zip(self.owner.doc.schematic.size,points.max(axis=0)))
                self.owner.view.focus_bounds=Bounds(lo,hi);self.owner.view.zoom=1.;self.owner.view.pan[:]=0;self.owner.view.schedule_render()
            self.owner.view.update();self.owner.space_manager.plan.update();self.status.setText(f"{STATUS[result['status']]} · 경로 {result.get('steps',0):,}칸 · 공간 2D 편집 또는 분할 화면에서 확인")
        self.owner.run_operation(lambda p,c:nav.route(left,right,c),done,'방 사이 경로 탐색')
    def clear_route(self):
        self.owner.view.route_path=[];self.owner.view.update()
        if hasattr(self.owner,'space_manager'):self.owner.space_manager.plan.update()
    def focus(self):
        sid=self.source.currentData()
        if sid:self.owner.space_manager.select_id(sid);self.owner.view.focus_selection();self.owner.tabs.setCurrentWidget(self.owner.split_tab if self.owner.split_mode.isChecked() else self.owner.view)
    def scan_equipment(self):
        if hasattr(self,'pages'):
            self.pages.setCurrentIndex(2);self.owner.integrated_ui.tasks.show(self.table)
        d=self.owner.doc
        if self.owner.operation is not None:return
        signature=self.signature()
        def done(result):
            if signature==self.signature():self.equipment=result;self.analysis_doc=d;self.snapshot=signature;self.show_equipment();getattr(self,'equipment_details',self.details).setPlainText(json.dumps({k:v for k,v in result.items() if k not in ('records','position_runs')},ensure_ascii=False,indent=2));
            if hasattr(self.owner,'workbench'):self.owner.workbench.rebuild_tree(True)
        self.owner.run_operation(lambda p,c:inventory(d,p,c),done,'모드 설비 분석')
    def equipment_filter_changed(self,*_):
        self.page.blockSignals(True);self.page.setValue(1);self.page.blockSignals(False);self.show_equipment()
    def show_equipment(self,*_):
        self.table.setRowCount(0);self.visible_records=[]
        if not self.equipment:return
        names={s['id']:s['name'] for s in self.owner.doc.spaces};query=self.filter.text().casefold()
        for r in search_records(self.owner.doc,self.equipment,query,(self.page.value()-1)*2000) if 'position_runs' in self.equipment else self.equipment['records']:
            owners=', '.join(names.get(i,i) for i in r['spaces']);near=', '.join(names.get(i,i) for i in r['adjacent_spaces']);text=owners+(' / 경계 인접 '+near if near else '')
            values=[r['category'],r['block'],str(r['position']),text or '미지정','미확인']
            if 'position_runs' not in self.equipment and query and query not in ' '.join(values).casefold():continue
            if len(self.visible_records)>=2000:break
            row=len(self.visible_records);self.visible_records.append(r);self.table.insertRow(row)
            for col,v in enumerate(values):
                item=QTableWidgetItem(v);item.setToolTip(v);self.table.setItem(row,col,item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1,min(360,self.table.columnWidth(1)));self.table.setColumnWidth(3,min(220,self.table.columnWidth(3)))
        self.status.setText(f"설비/미분류 후보 {self.equipment['total']:,}개 · 미분류 {self.equipment.get('unclassified',0):,} · 페이지당 2,000행 · 전체 좌표 RLE 보존 · 작동 미확인")
        diagnostic_event('equipment_display',trace_id=self.equipment.get('trace_id'),page=self.page.value(),query=query,shown=len(self.visible_records),found=self.equipment['total'],omission='페이지/검색/장면 표시 한도; 전체 RLE 유지')
    def equipment_selected(self):
        row=self.table.currentRow()
        if 0<=row<len(getattr(self,'visible_records',[])):
            r=self.visible_records[row];getattr(self,'equipment_details',self.details).setPlainText(json.dumps(r,ensure_ascii=False,indent=2));xyz=r['position'];self.owner.layer.setValue(xyz[1]);self.owner.view.focus_bounds=Bounds(tuple(max(0,p-3) for p in xyz),tuple(min(dim-1,p+3) for dim,p in zip(self.owner.doc.schematic.size,xyz)));self.owner.view.zoom=1.;self.owner.view.pan[:]=0;self.owner.view.schedule_render()
