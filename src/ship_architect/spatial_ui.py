"""3D analysis batches, protected review decisions and exact comparison overlays."""
import copy,json
from collections import Counter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QComboBox,QCheckBox,QLabel,QAbstractItemView
from .model import Bounds
from .spatial3d import recognize_3d,partial_bounds,intersects
from .analysis_state import commit,apply_rules,register,crop,compare
from .diagnostics import event

REASONS={'exterior_envelope_unresolved':'외부 외형·큰 미분리 영역 (기본 숨김)','large_merge_reanalysed':'대형 연결 후보 재분할·검토','possible_merged_space':'여러 공간 합쳐짐 가능·미해결','virtual_exterior_opening':'외부 연결·가상 개구 경계','virtual_room_partition':'가상 방 분리·통행 별도 검토','source_collision_repaired':'원본 충돌 셀 제외·검토','mesh_volume_mismatch':'메시/복셀 부피 불일치','mesh_budget_exceeded':'정밀 메시 표시 예산 초과','narrow_opening_partition':'좁은 개구부 분할 추정·수동 검토','analysis_limit':'분석 한도 초과','scope_or_exterior_open':'선택 경계·외부와 연결','ceiling_slit_no_headroom':'천장 틈·머리 공간 부족','no_usable_floor':'사용 가능한 바닥 부족','narrow_wall_gap_or_corridor':'좁은 벽 틈·통로 구분 필요','uncertain_block_boundary':'불확실한 블록 경계','closed_supported_volume':'닫힌 부피·지지 바닥 확인','existing_space_overlap_protected':'기존 공간과 겹침·보호','user_boundary_crop':'사용자 경계 보정','protected_user_decision':'사용자 판단 보호','user_exclusion_rule':'사용자 제외 규칙'}

CLASS_NAMES = {
    'closed_interior':'폐쇄형 내부', 'open_interior':'개방형 내부 후보',
    'hangar_or_large_opening':'넓은 개구부 후보', 'exposed_deck':'노출 갑판 후보',
    'exterior_air':'외부 공기', 'unresolved_exterior_merge':'미해결 외부 연결', 'uncertain':'불확실',
}
REASONS.update(wide_partition_repaired='넓은 가상 경계 병합', unsupported_exterior='선체 바닥 지지 부족', exposed_deck='지붕이 없는 노출면')

class SpatialBatch:
    def __init__(self,panel):
        self.panel=panel;self.owner=panel.owner;self.result=None
        layout=panel.layout();row=QHBoxLayout()
        for text,callback in [('3D 분석·자동 등록',lambda:self.scan(True)),('전체 3D 분석·자동 등록',lambda:self.scan(True,False,True)),('변경 주변 재분석',lambda:self.scan(True,True)),('선택 후보 등록',self.confirm),('3D 빈칸 비교',self.compare)]:
            button=QPushButton(text);button.clicked.connect(callback);row.addWidget(button)
        layout.insertLayout(0,row);row=QHBoxLayout();self.group=QComboBox();self.group.addItems(['전체','자동 등록 가능','검토 필요','제외','등록됨']);self.group.currentIndexChanged.connect(self.refresh);row.addWidget(self.group);self.reason=QComboBox();self.reason.addItem('전체 원인',None);self.reason.currentIndexChanged.connect(self.refresh);row.addWidget(self.reason)
        for text,callback in [('선택 일괄 제외',lambda:self.decide('exclude')),('선택 검토로 복원',lambda:self.decide('review')),('같은 원인 제외 규칙',self.exclude_rule),('선택 부피로 후보 수정',self.trim),('같은 원인 자르기 규칙',self.crop_rule),('규칙 적용 취소',self.owner.undo),('비교 표시 해제',self.clear_compare),('선택 후보 삭제',lambda:self.remove_candidates(False)),('제외 후보 목록 정리',lambda:self.remove_candidates(True))]:
            b=QPushButton(text);b.clicked.connect(callback);row.addWidget(b)
        layout.insertLayout(1,row);panel.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        from .recognition_ui import RecognitionTools
        self.recognition=RecognitionTools(self)
    def selected(self):
        ids={i.data(Qt.ItemDataRole.UserRole) for i in self.panel.list.selectedItems()};return [c for c in self.owner.doc.space_candidates if c['id'] in ids]
    def scan(self,auto=False,partial=False,whole=False,scope=None):
        w=self.owner;d=w.doc
        if w.operation is not None or w.loading:return
        b=scope or (partial_bounds(d) if partial else Bounds((0,0,0),tuple(n-1 for n in d.schematic.size)) if whole else d.selection)
        if b is None:self.panel.status.setText('변경 블록이 없습니다. 전체/선택 분석을 먼저 실행하세요.');return
        state=copy.deepcopy(d.analysis_state);revision=d.revision;engine=self.recognition.engine() if self.recognition.mode.currentIndex()==1 else recognize_3d
        def work(progress,cancel):
            r=engine(d,b,progress,cancel,partial);r=apply_rules(r,state,d.schematic.size)
            if partial:
                retained=[c for c in state.get('last_result',{}).get('candidates',[]) if c.get('method')==r['method'] and (not intersects(Bounds(tuple(c['lo']),tuple(c['hi'])),b) or c.get('user_modified'))]
                seen={c['fingerprint'] for c in r['candidates']};copies=copy.deepcopy([c for c in retained if c['fingerprint'] not in seen])
                for c in copies:
                    if not intersects(Bounds(tuple(c['lo']),tuple(c['hi'])),b):c['source_block_epoch']=d.block_epoch
                r['candidates']+=copies
                r['candidates']=r['candidates'][:512]
            prepared=register(d,r,True) if auto else (d.spaces,r,[])
            return r,prepared
        def done(value):
            if w.doc is not d or d.revision!=revision:self.panel.status.setText('분석 중 문서가 바뀌어 결과를 폐기했습니다. 재시도하세요.');return
            r,(spaces,r,added)=value;r['counts']=dict(Counter(c['decision'] for c in r['candidates']));state['last_result']=r
            commit(d,state,spaces,'space_auto_batch' if auto else 'space_analysis');self.result=r;self.panel.result=r
            dirty=d.spatial_dirty
            if dirty is None or all(b.lo[i]<=dirty.lo[i]<=dirty.hi[i]<=b.hi[i] for i in range(3)):d.spatial_dirty=None
            self.refresh();w.space_manager.refresh();w.refresh_history();self.panel.status.setText(f'3D 완료 · 자동 등록 {len(added)}개 · '+str(dict(Counter(c['decision'] for c in r['candidates'])))+' · 블록 변경 없음')
            event('space_batch_committed',trace_id=r.get('trace_id'),registered=len(added),partial=partial,scope=r['scope'])
        if hasattr(w,'integrated_ui') and hasattr(w.integrated_ui,'tasks'):w.integrated_ui.tasks.show(w.integrated_ui.tasks.space_results)
        w.run_operation(work,done,'3D 빈 공간 연결성·구조 검증')
    def receive(self,r,doc):
        if self.owner.doc is not doc or doc.revision!=r['revision']:return
        state=copy.deepcopy(doc.analysis_state);state['last_result']=r;commit(doc,state,action='space_analysis');self.result=r;self.refresh()
    def attach(self):
        self.result=self.owner.doc.analysis_state.get('last_result');self.panel.result=self.result
        self.recognition.mode.setCurrentIndex(1 if self.result and self.result.get('method')=='exterior_voxel_mesh' else 0)
        if self.result:self.owner.doc.space_candidates=copy.deepcopy(self.result.get('candidates',[]));self.owner.doc.space_revision=self.owner.doc.revision
        self.refresh()
    def refresh(self,*_):
        from PySide6.QtWidgets import QListWidgetItem
        p=self.panel;d=self.owner.doc;old=p.current();selected={c['id'] for c in self.selected()};p.list.blockSignals(True);p.list.clear();old_reason=self.reason.currentData();self.reason.blockSignals(True);self.reason.clear();self.reason.addItem('전체 원인',None)
        for reason in sorted({r for c in d.space_candidates if c.get('method') in ('3d_air_components','exterior_voxel_mesh') for r in c.get('reasons',[])}):self.reason.addItem(REASONS.get(reason,reason),reason)
        self.reason.setCurrentIndex(max(0,self.reason.findData(old_reason)));self.reason.blockSignals(False);wanted_reason=self.reason.currentData();wanted={0:None,1:'auto',2:'review',3:'exclude',4:'registered'}[self.group.currentIndex()]
        for c in d.space_candidates:
            if c.get('method') not in ('3d_air_components','exterior_voxel_mesh'):continue
            if wanted and c['decision']!=wanted:continue
            if wanted_reason and wanted_reason not in c['reasons']:continue
            structure=CLASS_NAMES.get(c.get('structure_class'))
            label=f'{structure} · ' if structure else ''
            item=QListWidgetItem(f"{label}{dict(auto='자동 등록 가능',review='검토 필요',exclude='제외',registered='등록됨')[c['decision']]} · {c.get('volume',0):,}셀 · Y={c['lo'][1]}~{c['hi'][1]} · {', '.join(REASONS.get(r,r) for r in c['reasons'])}");item.setData(Qt.ItemDataRole.UserRole,c['id']);item.setToolTip(item.text());p.list.addItem(item)
            if c['id'] in selected:item.setSelected(True)
        p.list.blockSignals(False)
        if p.list.count() and not p.list.selectedItems():p.list.setCurrentRow(0)
        p.show_overlay()
    def record(self,action,transform):
        d=self.owner.doc;state=copy.deepcopy(d.analysis_state);r=copy.deepcopy(state.get('last_result') or self.result)
        if not r:raise ValueError('3D 분석을 먼저 실행하세요.')
        transform(state,r);r['counts']=dict(Counter(c['decision'] for c in r['candidates']));state['last_result']=r;commit(d,state,action=action);self.result=r;self.refresh();self.owner.space_manager.refresh();self.owner.refresh_history()
    def decide(self,action):
        try:
            chosen={c['id'] for c in self.selected()}
            if not chosen:raise ValueError('하단 검토 목록에서 후보를 선택하세요.')
            def transform(state,r):
                for c in r['candidates']:
                    if c['id'] in chosen:
                        key=c.get('source_fingerprint',c['fingerprint']);state.setdefault('decisions',{})[key]={'action':action};c['decision']='exclude' if action=='exclude' else 'review';c['user_modified']=True
            self.record('space_batch_decision',transform)
        except Exception as e:self.owner.error(e)
    def exclude_rule(self):
        try:
            chosen=self.selected()
            if not chosen:raise ValueError('같은 원인으로 묶을 후보를 선택하세요.')
            reasons={c['reasons'][0] for c in chosen}
            def transform(state,r):
                for reason in sorted(reasons):state.setdefault('space_rules',[]).append({'reason':reason,'action':'exclude'})
                updated=apply_rules(r,state,self.owner.doc.schematic.size);r.update(updated)
            self.record('space_exclusion_rule',transform)
        except Exception as e:self.owner.error(e)
    def trim(self):
        try:
            ids={c['id'] for c in self.selected()};b=self.owner.doc.selection
            if not ids:raise ValueError('수정할 후보를 선택하세요.')
            def transform(state,r):
                for c in r['candidates']:
                    if c['id'] not in ids:continue
                    key=c.get('source_fingerprint',c['fingerprint']);crop(c,b,self.owner.doc.schematic.size);state.setdefault('decisions',{})[key]={'action':'crop','runs':copy.deepcopy(c['volume_runs'])}
            self.record('space_candidate_crop',transform)
        except Exception as e:self.owner.error(e)
    def crop_rule(self):
        try:
            chosen=self.selected();b=self.owner.doc.selection
            if not chosen:raise ValueError('규칙을 적용할 후보를 선택하세요.')
            reasons={c['reasons'][0] for c in chosen}
            def transform(state,r):
                for reason in reasons:state.setdefault('space_rules',[]).append({'reason':reason,'action':'crop','lo':list(b.lo),'hi':list(b.hi)})
                r.update(apply_rules(r,state,self.owner.doc.schematic.size))
            self.record('space_crop_rule',transform)
        except Exception as e:self.owner.error(e)
    def confirm(self):
        try:
            w=self.owner;d=w.doc;chosen=self.selected();pending={c['id'] for c in chosen if c.get('decision')!='registered'}
            if not chosen:raise ValueError('등록할 후보를 선택하세요.')
            if any(c.get('source_block_epoch')!=d.block_epoch for c in chosen):raise ValueError('블록 변경 이후 후보가 오래되었습니다. 재분석하세요.')
            r=copy.deepcopy(d.analysis_state.get('last_result') or self.result);spaces,r,added=register(d,r,False,{c['id'] for c in chosen});state=copy.deepcopy(d.analysis_state);state['last_result']=r
            for c in r['candidates']:
                if c['id'] in {c['id'] for c in chosen} and c['decision']=='registered':state.setdefault('decisions',{})[c.get('source_fingerprint',c['fingerprint'])]={'action':'registered',**({'runs':copy.deepcopy(c['volume_runs'])} if c.get('user_modified') else {})}
            zones=copy.deepcopy(d.zones);commit(d,state,spaces,'space_manual_batch');record=d.undo_stack[-1];record['zones_before']=zones
            for c in chosen:
                if c['id'] in pending and added and any(q['id']==c['id'] and q['decision']=='registered' for q in r['candidates']):d.zones.append({'name':'확인 공간 '+c['id'],'lo':c['lo'],'hi':c['hi'],'space_footprint':c['footprint'],'space_kind':c['kind'],'space_volume_runs':copy.deepcopy(c['volume_runs']),'space_method':c.get('method')})
            record['zones_after']=copy.deepcopy(d.zones);self.result=r;self.refresh();w.space_manager.refresh();w.refresh_zones();w.refresh_history();self.panel.status.setText(f'정확한 3D 공간 {len(added)}개 등록. 기존 겹치는 공간은 보호합니다.')
        except Exception as e:self.owner.error(e)
    def clear_compare(self):
        self.owner.view.validation_spaces=[];self.owner.view.partition_faces=[];self.owner.view.update();self.owner.space_manager.plan.update()
    def compare(self):
        chosen=self.selected()
        if not chosen:return
        d=self.owner.doc;epoch=d.block_epoch
        def work(progress,cancel):return [(c,compare(d,c['volume_runs'])) for c in chosen]
        def done(values):
            if self.owner.doc is not d or d.block_epoch!=epoch:return
            spaces=[];bad=0;good=0
            for c,r in values:
                for name,color in (('valid_runs','#42d694'),('conflict_runs','#f16d6d')):
                    if r[name]:spaces.append({'id':'validation-'+c['id']+name,'runs':r[name],'visible':True,'opacity':65,'color':color})
                bad+=r['conflict_cells'];good+=r['valid_cells']
            self.owner.view.validation_spaces=spaces;self.owner.view.update();self.panel.status.setText(f'초록 실제 빈칸 {good:,} / 빨강 실제 블록 충돌 {bad:,}. 표시만 변경합니다.')
        self.owner.run_operation(work,done,'후보와 실제 빈 공간 비교')

    def remove_candidates(self,excluded=False,ids=None):
        try:
            ids={c['id'] for c in self.selected()} if ids is None else set(ids)
            def transform(state,r):
                r['candidates']=[c for c in r['candidates'] if not(c.get('decision')=='exclude' if excluded else c['id'] in ids)]
            self.record('candidate_list_cleanup' if excluded else 'candidate_list_delete',transform)
            self.owner.workbench.rebuild_tree(True)
            self.panel.status.setText('후보 목록만 정리했습니다. 확정 공간·실제 블록은 유지합니다. 일반 삭제는 재분석 시 다시 생성될 수 있습니다.')
        except Exception as e:self.owner.error(e)
