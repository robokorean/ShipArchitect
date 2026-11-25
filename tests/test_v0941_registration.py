import copy,threading,json
from types import SimpleNamespace
import numpy as np
import pytest
from ship_architect.analysis_state import commit
from ship_architect.registration import prepare,apply
from ship_architect.metadata_copy import snapshot
from ship_architect.spatial3d import recognize_3d
from ship_architect.chunks import Cancelled
from test_v093_analysis import build
from test_ai_gui import window,ready,wait_for
from test_ai_design import server,plan,operation
from test_v094_facility import facility_document
from ship_architect.model import Bounds,Document
from ship_architect.ai_design import build_context


def candidate_document():
    d,_=build();r=recognize_3d(d,d.selection);commit(d,dict(d.analysis_state,last_result=r))
    return d,r['candidates'][0]['id']


def test_snapshot_preserves_tuple_aliases_but_isolates_source():
    rows=[[1,2,3,4]];source={'a':rows,'b':rows,'tuple':(1,{'k':rows})}
    copied=snapshot(source)
    assert copied==copy.deepcopy(source) and type(copied['tuple']) is tuple
    assert copied['a'] is copied['b'] and copied['a'] is not rows
    copied['a'][0][0]=99;assert rows[0][0]==1


def test_registration_one_atomic_history_and_saved_undo_redo(tmp_path):
    d,cid=candidate_document();before=copy.deepcopy((d.analysis_state,d.spaces,d.zones,d.undo_stack));blocks=d.schematic.blocks.copy();meta=d.schematic.metadata_digest()
    p=prepare(d,{cid});assert (d.analysis_state,d.spaces,d.zones,d.undo_stack)==before
    apply(d,p);assert len(d.spaces)==1 and len(d.undo_stack)==len(before[3])+1
    assert d.analysis_state['decisions'] and d.zones[-1]['registered_space_id']==d.spaces[0]['id']
    assert np.array_equal(blocks,d.schematic.blocks)
    after=copy.deepcopy((d.analysis_state,d.spaces,d.zones));d.undo();assert (d.analysis_state,d.spaces,d.zones)==before[:3]
    d.redo();assert (d.analysis_state,d.spaces,d.zones)==after
    path=tmp_path/'registration.shipproj';d.save_project(path);q=Document.open_project(path)
    q.undo();assert (q.analysis_state,q.spaces,q.zones)==before[:3]
    q.redo();assert (q.analysis_state,q.spaces,q.zones)==after and q.schematic.metadata_digest()==meta


def test_cancel_invalid_member_and_stale_publication_change_nothing():
    d,cid=candidate_document();before=copy.deepcopy((d.spaces,d.analysis_state,d.zones,d.undo_stack))
    with pytest.raises(Cancelled):prepare(d,{cid},cancel=lambda:True)
    bad=copy.deepcopy(d.analysis_state);c=copy.deepcopy(bad['last_result']['candidates'][0]);c['id']='bad';c['space_class']='exterior_air';bad['last_result']['candidates'].append(c);commit(d,bad)
    current=copy.deepcopy((d.spaces,d.analysis_state,d.zones,d.undo_stack))
    with pytest.raises(ValueError,match='외부'):prepare(d,{cid,'bad'})
    assert (d.spaces,d.analysis_state,d.zones,d.undo_stack)==current
    p=prepare(d,{cid});d.log('other_edit')
    with pytest.raises(ValueError,match='문서'):apply(d,p)
    assert (d.spaces,d.analysis_state,d.zones,d.undo_stack)==current


def test_publication_exception_rolls_back_history_and_metadata(monkeypatch):
    d,cid=candidate_document();p=prepare(d,{cid})
    old=copy.deepcopy((d.spaces,d.analysis_state,d.zones,d.undo_stack,d.redo_stack,d.history,d.revision))
    monkeypatch.setattr(d,'log',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('write failure')))
    with pytest.raises(RuntimeError):apply(d,p)
    assert (d.spaces,d.analysis_state,d.zones,d.undo_stack,d.redo_stack,d.history,d.revision)==old


def test_registration_does_not_share_history_or_candidate_geometry():
    d,cid=candidate_document();apply(d,prepare(d,{cid}))
    stored=copy.deepcopy(d.undo_stack[-1]['analysis_after'])
    d.space_candidates[0]['volume_runs'][0][2]+=1
    assert d.analysis_state==stored
    d.analysis_state['last_result']['candidates'][0]['reasons'].append('later')
    assert d.undo_stack[-1]['analysis_after']==stored


def test_bulk_button_returns_while_worker_reports_progress_and_cancel(window,monkeypatch):
    import ship_architect.registration as module
    from PySide6.QtCore import QTimer
    w=window;d,cid=candidate_document();w.set_document(d);wait_for(lambda:ready(w));before=copy.deepcopy(d.spaces)
    entered=threading.Event();original=module.register
    def hold(*args,**kw):
        entered.set();kw['progress'](20,'검증 대기')
        while not kw['cancel']():threading.Event().wait(.01)
        raise Cancelled()
    monkeypatch.setattr(module,'register',hold)
    ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    w.space_view.bulk.confirm({cid});assert w.operation is not None
    wait_for(lambda:entered.is_set() and len(ticks)>2)
    w.operation.requestInterruption();wait_for(lambda:ready(w));timer.stop()
    assert d.spaces==before and not d.zones


def test_partial_facility_gui_keeps_permission_and_relative_coordinates(window,server):
    w=window;client,h=server;d,fid,left,right=facility_document();d.active_design_facility=None
    w.set_document(d);wait_for(lambda:ready(w));w.workbench.select_key('facility:'+fid)
    p=w.ai_panel;p.use_selection.setChecked(True);p.interior_mode.setChecked(True);p.url.setText(client.url)
    w.picked((2,2,2),True);w.picked((3,2,2),True)
    assert d.active_design_facility==fid and d.selection==Bounds((2,2,2),(3,2,2))
    h.responses=[json.dumps(plan(operation('add','minecraft:glass',(0,0,0),(1,0,0))))]
    p.prompt.setPlainText('유리 장식');p.generate();wait_for(lambda:p.job is None and ready(w))
    assert w.proposal is not None and len(w.proposal.indices)==2
    assert p.last_result['context']['size_xyz']==[2,1,1]
    assert p.last_result['context']['exact_space']['context_cells']==2
    assert p.last_result['context']['exact_space']['space_total_cells']>2
    assert d.selection==Bounds((2,2,2),(3,2,2)) and not np.any(d.schematic.blocks)


def test_exact_user_korean_request_does_not_make_adverb_a_material():
    from ship_architect.block_registry import request_bindings,BlockRegistry,MaterialError
    prompt=('현재 선택한 공간의 비어 있는 위치에 유리 블록(minecraft:glass) 2개를 서로 떨어진 곳에 배치해 줘.\n'
            '기존 벽, 바닥, 천장, 통로, ComputerCraft 및 Create 장치는 절대 변경하지 마.\n'
            '안전한 위치에 배치할 수 없다면 억지로 건축하지 말고 이유를 알려줘.')
    bindings,states=request_bindings(prompt,BlockRegistry())
    assert states==['minecraft:glass'] and not bindings
    for adverb in ('억지로','함부로','강제로'):
        assert request_bindings(adverb+' 건축하지 마',BlockRegistry())==({},[])
    with pytest.raises(MaterialError):request_bindings('없는재료로 건축해 줘',BlockRegistry())
    with pytest.raises(MaterialError):request_bindings('벽은 없는재료로 만들어 줘',BlockRegistry())


def test_history_sharing_is_private_and_keeps_restores_independent():
    from ship_architect.metadata_copy import history_after
    old={'geometry':[[1,2,3,4]],'decision':'review'}
    new={'geometry':[[1,2,3,4]],'decision':'registered'}
    before=snapshot(old);after=history_after(old,new,before)
    assert after==new and after['geometry'] is before['geometry']
    assert after['geometry'] is not old['geometry'] and after['geometry'] is not new['geometry']
    new['geometry'][0][0]=99;assert before['geometry'][0][0]==after['geometry'][0][0]==1


def test_cancel_at_ready_boundary_still_leaves_document_unchanged():
    d,cid=candidate_document();old=copy.deepcopy((d.analysis_state,d.spaces,d.undo_stack))
    stop=[False]
    def progress(n,text):
        if n==95:stop[0]=True
    with pytest.raises(Cancelled):prepare(d,{cid},progress,cancel=lambda:stop[0])
    assert (d.analysis_state,d.spaces,d.undo_stack)==old


def test_actual_registration_button_starts_async_then_restores_undo(window):
    from PySide6.QtWidgets import QPushButton
    w=window;d,cid=candidate_document();w.set_document(d);wait_for(lambda:ready(w))
    w.space_view.list.setCurrentRow(0)
    button=next(b for b in w.space_view.findChildren(QPushButton) if b.text()=='선택 후보 등록')
    button.click();wait_for(lambda:ready(w))
    assert len(d.spaces)==1 and d.zones[-1]['registered_space_id']==d.spaces[0]['id']
    w.undo();wait_for(lambda:ready(w));assert not d.spaces and not d.zones


def test_thousand_candidate_batch_preserves_overlap_decisions_and_progress():
    from ship_architect.analysis_state import crop
    d,cid=candidate_document();r=copy.deepcopy(d.analysis_state['last_result'])
    c=r['candidates'][0];crop(c,Bounds((3,2,3),(4,3,4)),d.schematic.size)
    r['candidates']=[]
    for i in range(1000):
        item=copy.deepcopy(c);item['id']='stress-'+str(i);r['candidates'].append(item)
    commit(d,dict(d.analysis_state,last_result=r));events=[]
    p=prepare(d,{c['id'] for c in d.space_candidates},lambda n,text:events.append((n,text)))
    assert not d.spaces
    apply(d,p)
    assert len(d.spaces)==1
    assert sum(c['decision']=='registered' for c in d.space_candidates)==1
    assert sum(c['decision']=='review' for c in d.space_candidates)==999
    assert any('1,000/1,000' in text for _,text in events)
    d.undo();assert not d.spaces


def test_confirmed_candidates_send_summaries_not_full_mesh_or_run_payload():
    from ship_architect.ai_design import material_context
    d,cid=candidate_document();d.space_candidates[0]['confirmed']=True
    d.space_candidates[0]['mesh']={'unbounded_geometry':'x'*100000}
    ctx=material_context(d,d.selection,'minecraft:glass')
    assert len(json.dumps(ctx).encode())<48000
    assert len(ctx['space_candidates'])==1
    assert 'mesh' not in ctx['space_candidates'][0] and 'volume_runs' not in ctx['space_candidates'][0]
    assert ctx['space_candidates'][0]['id']==cid
