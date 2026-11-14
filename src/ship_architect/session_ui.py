"""Explicit restored-space, clean analysis and compare workflows."""
from collections import Counter
from copy import deepcopy
import json

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton

from .analysis_sessions import compare_candidates, matches_document, provenance
from .analysis_state import apply_rules, commit
from .model import Bounds
from types import SimpleNamespace
from .space_model import normalize, subtract


class AnalysisSessions:
    def __init__(self,batch):
        self.batch=batch
        self.w=batch.owner
        self.status=QLabel()
        self.status.setWordWrap(True)
        row=QHBoxLayout()
        for title, callback in (
            ('저장 공간 복원',self.restore),
            ('완전 신규 분석',lambda:self.scan(False)),
            ('공간 유지 재분석',lambda:self.scan(True)),
            ('저장·신규 결과 전환',self.swap),
            ('선택 차이 표시',self.overlay),
        ):
            button=QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.filter=QComboBox()
        for title,key in (('모든 후보','all'),('형상 변경','changed'),('형상 동일','same'),
                          ('검토 필요','uncertain'),('제외·외부','excluded')):
            self.filter.addItem(title,key)
        row.addWidget(self.filter)
        batch.panel.layout().insertLayout(0,row)
        batch.panel.layout().insertWidget(1,self.status)

    def describe(self):
        d=self.w.doc
        r=d.analysis_state.get('last_result',{})
        p=r.get('provenance',{})
        label=getattr(d,'analysis_display_origin','저장 결과')
        version=p.get('program_version','확인 불가')
        engine=r.get('algorithm_version',p.get('algorithm_version','확인 불가'))
        match=matches_document(d,p)
        block_status={True:'현재 블록과 일치',False:'블록 변경됨',None:'블록 일치 미확인'}[match]
        counts=Counter(x['status'] for x in d.analysis_state.get('session_changes',[]))
        reference=d.analysis_state.get('session_reference',{})
        different_scope=bool(reference.get('scope') and reference['scope']!=r.get('scope'))
        self.status.setText(f'{label} · 저장 공간 {len(d.spaces)}개 · 후보 {len(r.get("candidates",[]))}개'
                            f' · 생성 {version} / 알고리즘 {engine} · {block_status}'
                            +(f' · {dict(counts)}' if counts else '')
                            +(' · 비교 범위 다름' if different_scope else ''))

    def restore(self):
        w=self.w
        if w.loading or w.operation is not None:
            return
        state=w.doc.analysis_state
        if state.get('session_side')=='new' and state.get('session_reference',{}).get('method') not in (None,'saved_spaces'):
            self.swap()
        w.space_manager.overlays.setChecked(True)
        self.batch.panel.overlay.setChecked(False)
        self.batch.clear_compare()
        self.batch.panel.status.setText('저장된 확정 공간 표시 · 신규 분석 없음')
        self.describe()

    def scan(self,keep_rules):
        w=self.w
        if w.loading or w.operation is not None:
            return
        d=w.doc
        revision=d.revision
        bounds=Bounds((0,0,0),tuple(n-1 for n in d.schematic.size))
        ignore_terrain=self.batch.recognition.ignore_terrain.isChecked()
        state=deepcopy(d.analysis_state)
        prior=(state.get('session_reference') if state.get('session_side')=='new' else state.get('last_result')) or state.get('last_result')
        saved=[dict(s,volume_runs=s['runs']) for s in d.spaces]
        mode='protected_reanalysis' if keep_rules else 'fresh'
        def work(progress,cancel):
            from .exterior_recognition import recognize_exterior
            from .chunks import checkpoint
            # No saved candidate arrays, user decisions, or analysis caches enter the clean engine.
            scan_doc=d if keep_rules else SimpleNamespace(schematic=d.schematic,block_epoch=d.block_epoch,revision=d.revision,spaces=[],analysis_state={})
            result=recognize_exterior(scan_doc,bounds,progress,cancel,ignore_terrain=ignore_terrain)
            checkpoint(cancel)
            result['provenance']=provenance(d,result['method'],mode)
            if keep_rules:
                result=apply_rules(result,state,d.schematic.size)
            baseline=prior or {'method':'saved_spaces','candidates':saved}
            changes=compare_candidates(baseline['candidates'],result['candidates'],cancel)
            return result,baseline,changes

        def done(value):
            if w.doc is not d or d.revision!=revision:
                return
            result,baseline,changes=value
            # Preserve even an empty saved snapshot; never apply candidate geometry here.
            state['last_result']=result
            state['session_reference']=baseline
            state['session_changes']=changes
            state['session_side']='new'
            commit(d,state,action='recognition_'+mode)
            d.analysis_display_origin='완전 신규 분석' if not keep_rules else '공간 유지 재분석'
            self.batch.panel.overlay.setChecked(True)
            self.batch.attach()
            w.refresh_history()
            self.describe()
            self.batch.panel.text.setPlainText(json.dumps(changes,ensure_ascii=False,indent=2))

        w.integrated_ui.tasks.show(w.integrated_ui.tasks.space_results)
        w.run_operation(work,done,'현재 복셀에서 새 공간 계산')

    def swap(self):
        w=self.w
        if w.loading or w.operation is not None:
            return
        d=w.doc
        state=deepcopy(d.analysis_state)
        reference=state.get('session_reference')
        if not reference:
            return
        if reference.get('method')=='saved_spaces':
            w.space_manager.overlays.setChecked(True)
            self.batch.panel.overlay.setChecked(not self.batch.panel.overlay.isChecked())
            self.batch.panel.status.setText('확정 공간과 신규 후보는 별개입니다. 신규 후보 오버레이만 전환했습니다.')
            return
        self.batch.panel.overlay.setChecked(True)
        state['last_result'],state['session_reference']=reference,state['last_result']
        state['session_side']='old' if state.get('session_side')=='new' else 'new'
        commit(d,state,action='recognition_snapshot_view')
        d.analysis_display_origin='저장 분석 결과' if state['session_side']=='old' else '신규 분석 결과'
        self.batch.clear_compare()
        self.batch.attach()
        w.refresh_history()
        self.describe()

    def overlay(self):
        w=self.w
        d=w.doc
        state=d.analysis_state
        chosen=self.batch.selected()
        if not chosen or 'session_reference' not in state:
            return
        side=state.get('session_side','new')
        a,b=state['session_reference'],state['last_result']
        if side=='old':
            a,b=b,a
        key=chosen[0]['id']
        changes=[row for row in state.get('session_changes',[])
                 if (row['new_id']==key if side=='new' else key in row['old_ids'])]
        if not changes:
            return
        old_ids={i for row in changes for i in row['old_ids']}
        new_ids={row['new_id'] for row in changes}
        def runs(result, ids):
            return normalize([r for c in result['candidates'] if c['id'] in ids
                              for r in c.get('volume_runs',c.get('runs',[]))],d.schematic.size)
        try:
            old,new=runs(a,old_ids),runs(b,new_ids)
            if new_ids:
                all_new={c['id'] for c in b['candidates']}
                siblings={row['new_id'] for row in state['session_changes']
                          if old_ids.intersection(row['old_ids'])}
                # A pure split changes labels without adding or removing air.
                if siblings-{None} and siblings & all_new and all(row['status']=='split' for row in changes):
                    self.batch.panel.status.setText('분리된 하위 후보와 이전 전체 구역을 비교합니다.')
            diffs=[('old','#ff8467',subtract(old,new,d.schematic.size)),
                   ('new','#36d3c4',subtract(new,old,d.schematic.size))]
            w.view.validation_spaces=[{'id':f'session-{kind}-{key}','runs':r,'color':color,
                                      'visible':True,'opacity':135}
                                     for kind,color,r in diffs if r]
            w.view.update()
            w.space_manager.plan.update()
            w.workbench.axis_plan.refresh()
            self.batch.panel.text.setPlainText('주황: 기존에만 / 청록: 신규에만\n'
                                               +json.dumps(changes,ensure_ascii=False,indent=2))
        except ValueError as exc:
            w.error(exc)
