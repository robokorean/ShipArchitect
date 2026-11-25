"""Prepare registration off-thread; publish one reversible metadata transaction."""
import copy,time
from .metadata_copy import snapshot
from .chunks import checkpoint
from .analysis_state import register,commit


def prepare(doc, ids, progress=lambda *_:None, cancel=None):
    start=time.perf_counter();metrics={};ids=frozenset(ids)
    checkpoint(cancel);progress(1,f'후보 등록 준비 · 선택 {len(ids):,}개')
    chosen=[c for c in doc.space_candidates if c['id'] in ids]
    if not chosen:raise ValueError('등록할 후보를 선택하세요.')
    if doc.analysis_state.get('session_side')=='old':raise ValueError('저장 비교는 읽기 전용입니다. 신규 결과로 전환하세요.')
    if any(c.get('source_block_epoch')!=doc.block_epoch for c in chosen):raise ValueError('블록 변경 이후 후보가 오래되었습니다. 재분석하세요.')
    result=doc.analysis_state.get('last_result')
    if not result:raise ValueError('3D 분석을 먼저 실행하세요.')
    revision=doc.revision;references=(doc.spaces,doc.analysis_state,doc.zones)
    pending={c['id'] for c in chosen if c.get('decision')!='registered'}
    metrics['collect_seconds']=time.perf_counter()-start
    t=time.perf_counter()
    spaces,result,added=register(doc,result,False,ids,progress=progress,cancel=cancel)
    metrics['registration_seconds']=time.perf_counter()-t
    checkpoint(cancel);progress(60,'등록 판단·분석 상태 준비')
    t=time.perf_counter()
    state=snapshot({k:v for k,v in doc.analysis_state.items() if k!='last_result'})
    state['last_result']=result;index={c['id']:c for c in result['candidates']}
    for cid in ids:
        c=index.get(cid)
        if c and c['decision']=='registered':
            state.setdefault('decisions',{})[c.get('source_fingerprint',c['fingerprint'])]={'action':'registered',**({'runs':snapshot(c['volume_runs'])} if c.get('user_modified') else {})}
    zones=snapshot(doc.zones)
    for c in chosen:
        checkpoint(cancel)
        q=index.get(c['id'])
        if c['id'] in pending and added and q and q['decision']=='registered':
            zones.append({'name':'확인 공간 '+c['id'],'lo':c['lo'],'hi':c['hi'],'space_footprint':c['footprint'],'space_kind':c['kind'],'space_volume_runs':snapshot(c['volume_runs']),'space_method':c.get('method'),'registered_space_id':q.get('registered_space_id')})
    metrics['decisions_zones_seconds']=time.perf_counter()-t
    checkpoint(cancel);progress(72,'전체 결과 검증·실행 취소 이력 준비')
    # Only read existing arrays/metadata; isolate every mutable history/cache target.
    shadow=copy.copy(doc);shadow.undo_stack=[];shadow.redo_stack=[];shadow.history=[];shadow._analysis={}
    def log(*args,**kwargs):shadow.revision+=1;shadow.dirty=True
    shadow.log=log
    t=time.perf_counter();commit(shadow,state,spaces,'space_manual_batch',metrics=metrics,cancel=cancel)
    record=shadow.undo_stack[-1];record['zones_before']=snapshot(doc.zones);record['zones_after']=snapshot(zones)
    metrics['transaction_seconds']=time.perf_counter()-t
    checkpoint(cancel);progress(95,f'검증 완료 · 새 공간 {len(added):,}개 · 화면 반영 대기')
    checkpoint(cancel)
    metrics['prepare_seconds']=time.perf_counter()-start
    return dict(revision=revision,references=references,spaces=shadow.spaces,state=shadow.analysis_state,
                candidates=shadow.space_candidates,zones=zones,record=record,active=shadow.active_space_id,
                added=len(added),selected=len(ids),metrics=metrics)


def apply(doc, prepared):
    refs=prepared['references']
    if doc.revision!=prepared['revision'] or any(a is not b for a,b in zip(refs,(doc.spaces,doc.analysis_state,doc.zones))):
        raise ValueError('등록 준비 중 문서가 바뀌었습니다. 결과를 적용하지 않았습니다. 다시 시도하세요.')
    keys=('spaces','analysis_state','space_candidates','zones','active_space_id','space_revision',
          'undo_stack','redo_stack','history','_analysis','last_export','revision','dirty')
    old={k:getattr(doc,k) for k in keys}
    try:
        doc.spaces=prepared['spaces'];doc.analysis_state=prepared['state'];doc.space_candidates=prepared['candidates']
        doc.zones=prepared['zones'];doc.active_space_id=prepared['active']
        doc.undo_stack=doc.undo_stack+[prepared['record']];doc.redo_stack=[];doc.history=list(doc.history);doc._analysis={}
        doc.log('space_manual_batch',space_id=doc.active_space_id,blocks=0)
        doc.space_revision=doc.revision
    except Exception:
        for k,value in old.items():setattr(doc,k,value)
        raise
