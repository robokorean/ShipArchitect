"""Experimental engine selection, same-scope comparison and precise mesh export."""
import copy,json
from PySide6.QtWidgets import QComboBox,QHBoxLayout,QPushButton,QFileDialog,QCheckBox,QMessageBox
from .analysis_state import commit,apply_rules
from .exterior_recognition import recognize_exterior
from .spatial3d import recognize_3d
from .recognition_compare import compare_methods
from .voxel_mesh import indexed_mesh,obj_bytes
from .schematic import atomic_write
from .model import Bounds

class RecognitionTools:
    def __init__(self,batch):
        self.batch=batch;self.w=batch.owner;p=batch.panel;row=QHBoxLayout();self.mode=QComboBox();self.mode.addItems(['기존 3D 인식 (v0.9.3)','외관 복셀·메시 인식 (시험)']);self.mode.setToolTip('실제 블록/NBT를 변경하지 않고 서로 다른 인식을 선택합니다.');row.addWidget(self.mode)
        for text,fn in [('두 방식 전체 비교',self.compare),('기존 비교 결과',lambda:self.show('old')),('신규 비교 결과',lambda:self.show('new')),('선택 경계 겹쳐 보기',self.overlay),('선택 메시 OBJ 내보내기',self.export)]:
            b=QPushButton(text);b.clicked.connect(fn);row.addWidget(b)
        p.layout().insertLayout(0,row)
        tools=QHBoxLayout()
        self.ignore_terrain=QCheckBox('주변 지형 제외')
        self.ignore_terrain.setChecked(True)
        self.ignore_terrain.setToolTip('넓고 얇은 외곽 지형만 분석 경계에서 제외합니다.')
        tools.addWidget(self.ignore_terrain)
        for name, callback in (
            ('병합 경계 보기',self.show_partition),
            ('병합 거부·재분석',self.reject_partition),
            ('분석 JSON 저장',self.export_json),
        ):
            button=QPushButton(name)
            button.clicked.connect(callback)
            tools.addWidget(button)
        tools.addStretch()
        p.layout().insertLayout(1,tools)

    def engine(self):
        if self.mode.currentIndex()!=1:
            return recognize_3d
        from functools import partial
        return partial(recognize_exterior,ignore_terrain=self.ignore_terrain.isChecked())

    def partition(self):
        chosen=self.batch.selected()
        result=self.w.doc.analysis_state.get('last_result',{})
        if result.get('block_epoch')!=self.w.doc.block_epoch or not chosen:
            return []
        key=chosen[0]['id']
        return [r for r in result.get('partition_decisions',[])
                if key in r.get('result_ids',[]) or key in r.get('source_ids',[])]

    def show_partition(self):
        records=self.partition()
        self.w.view.partition_faces=[sample for record in records for sample in record['samples']]
        self.w.view.update()
        self.w.space_manager.plan.update()
        self.batch.panel.text.setPlainText(
            '주황 면: 이전 가상 분리 경계 표본\n'+
            (json.dumps(records,ensure_ascii=False,indent=2) if records else '선택 후보의 분석 경계 기록이 없습니다.'))

    def reject_partition(self):
        w=self.w
        if w.operation is not None or w.loading:
            return
        records=[r for r in self.partition() if r['merged']]
        if not records:
            self.batch.panel.status.setText('복원된 병합 후보를 선택하세요.')
            return
        if QMessageBox.question(w,'공간 병합 거부','이 가상 경계를 복원하고 분석 범위를 다시 검사할까요?')!=QMessageBox.StandardButton.Yes:
            return
        d=w.doc
        state=copy.deepcopy(d.analysis_state)
        scope=state['last_result']['scope']
        keys={':'.join(r['source_fingerprints']) for r in records}
        state['partition_rejections']=sorted(set(state.get('partition_rejections',[]))|keys)
        commit(d,state,action='reject_partition')
        self.mode.setCurrentIndex(1)
        self.batch.scan(scope=Bounds(tuple(scope['lo']),tuple(scope['hi'])))

    def export_json(self):
        w=self.w
        result=w.doc.analysis_state.get('last_result')
        if not result or w.operation is not None:
            return
        path,_=QFileDialog.getSaveFileName(w,'정확한 공간 인식 결과 저장','recognition.json','JSON (*.json)')
        if not path:
            return
        def work(progress,cancel):
            from .chunks import checkpoint
            checkpoint(cancel)
            data=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf8')
            checkpoint(cancel)
            atomic_write(path,data)
        w.run_operation(work,lambda _:self.batch.panel.status.setText('공간 인식 JSON 저장 완료'),'공간 인식 JSON 내보내기')
    def compare(self):
        w=self.w;d=w.doc
        if w.operation is not None or w.loading:return
        b=Bounds((0,0,0),tuple(n-1 for n in d.schematic.size));revision=d.revision
        def done(result):
            if w.doc is not d or d.revision!=revision:return
            state=copy.deepcopy(d.analysis_state);state['comparison']=result;state['last_result']=apply_rules(result['new'],state,d.schematic.size);commit(d,state,action='recognition_comparison');self.mode.setCurrentIndex(1);self.batch.attach();w.refresh_history();w.integrated_ui.tasks.show(w.integrated_ui.tasks.space_results)
            self.batch.panel.status.setText(f"비교 완료 · 기존 {len(result['old']['candidates'])}개/{result['old']['elapsed_seconds']:.2f}초 · 신규 {len(result['new']['candidates'])}개/{result['new']['elapsed_seconds']:.2f}초. IoU는 정답률이 아닙니다. 미등록 비교 결과입니다.")
            self.batch.panel.text.setPlainText(json.dumps({k:v for k,v in result.items() if k not in ('old','new')},ensure_ascii=False,indent=2))
        ignore_terrain=self.ignore_terrain.isChecked()
        w.integrated_ui.tasks.show(w.integrated_ui.tasks.space_results);w.run_operation(lambda p,c:compare_methods(d,b,p,c,ignore_terrain=ignore_terrain),done,'기존/외관 기반 인식 비교')
    def show(self,which):
        try:
            d=self.w.doc;r=d.analysis_state.get('comparison')
            if not r:raise ValueError('두 방식 전체 비교를 먼저 실행하세요.')
            if r['block_epoch']!=d.block_epoch:raise ValueError('블록 변경 뒤 비교 결과가 오래되었습니다. 다시 비교하세요.')
            state=copy.deepcopy(d.analysis_state);state['last_result']=apply_rules(r[which],state,d.schematic.size);commit(d,state,action='recognition_comparison_show');self.mode.setCurrentIndex(0 if which=='old' else 1);self.batch.attach();self.w.refresh_history()
        except Exception as e:self.w.error(e)
    def overlay(self):
        try:
            d=self.w.doc;r=d.analysis_state.get('comparison');chosen=self.batch.selected()
            if not r or not chosen:raise ValueError('비교를 실행하고 후보를 선택하세요.')
            if r['block_epoch']!=d.block_epoch:raise ValueError('오래된 비교 결과입니다. 다시 비교하세요.')
            c=chosen[0];match=next((m for m in r['matches'] if c['id'] in (m['new_id'],m['old_id'])),None)
            if not match or not match['old_id']:raise ValueError('겹치는 기존 후보가 없습니다. 같은 방이라고 단정하지 않습니다.')
            spaces=[]
            for which,key,color in [('old','old_id','#f1ac4d'),('new','new_id','#36cfe2')]:
                candidate=next(q for q in r[which]['candidates'] if q['id']==match[key]);spaces.append({'id':'compare-'+candidate['id'],'runs':candidate['volume_runs'],'visible':True,'opacity':60,'color':color})
            self.w.view.validation_spaces=spaces;self.w.view.update();self.batch.panel.text.setPlainText('주황: 기존 / 청록: 신규 · 정확한 셀 경계 중첩\n'+json.dumps(match,ensure_ascii=False,indent=2))
        except Exception as e:self.w.error(e)
    def export(self):
        chosen=self.batch.selected()
        if not chosen:return
        c=copy.deepcopy(chosen[0]);d=self.w.doc;epoch=d.block_epoch
        path,_=QFileDialog.getSaveFileName(self.w,'선택 공간의 정확한 외곽 메시 저장',c['id']+'.obj','Wavefront OBJ (*.obj)')
        if not path:return
        def work(progress,cancel):
            from .chunks import checkpoint
            checkpoint(cancel);data=obj_bytes(indexed_mesh(c['volume_runs']));checkpoint(cancel)
            if d.block_epoch!=epoch:raise ValueError('블록이 변경되어 메시 내보내기를 중단했습니다.')
            atomic_write(path,data);return path
        self.w.run_operation(work,lambda p:self.batch.panel.status.setText('복셀 외곽 OBJ 저장: '+p),'선택 공간 메시 내보내기')
