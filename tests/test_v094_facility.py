from copy import deepcopy
import json
import numpy as np
import pytest
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space,bbox,contains
from ship_architect.facilities import save_facility
from ship_architect.facility_scope import select,resolve
from ship_architect.ai_design import build_context,compile_plan,PlanError
from ship_architect.interior_design import enrich_context
from test_ai_gui import window,ready,wait_for
from test_ai_design import operation,plan,server


def facility_document():
    d=Document(Schematic.empty(20,12,16))
    left=new_space([[y,z,2,5] for y in (2,3,4) for z in range(2,7)],d.schematic.size,'정비 구역')
    right=new_space([[y,z,9,12] for y in (2,3,4) for z in range(2,7)]+[[y,6,7,8] for y in (3,4)],d.schematic.size,'보조 구역')
    d.set_spaces([left,right])
    fid=save_facility(d,['space:'+left['id'],'space:'+right['id']],'통합 격납고','격납고')
    scope=select(d,fid);d.selection=Bounds(*bbox(scope))
    return d,fid,left,right


def test_union_preserves_gaps_height_and_all_original_ids():
    d,fid,left,right=facility_document()
    original=deepcopy(d.spaces),deepcopy(d.analysis_state),d.schematic.metadata_digest()
    scope=resolve(d,fid)
    ctx=enrich_context(d,d.selection,build_context(d,d.selection))
    assert ctx['interior_design']['scope']=='exact_facility_union'
    assert len(ctx['interior_design']['facility_members'])==2
    proposal=compile_plan(d,d.selection,plan(operation('fill','minecraft:glass',(0,0,0),(10,2,4))),ctx,d.revision)
    w,h,l=d.schematic.size
    assert contains(scope,proposal.indices,d.schematic.size).all()
    gap=(proposal.indices%w>=6)&(proposal.indices%w<=8)&((proposal.indices//w)%l<6)
    assert not gap.any() and proposal.outside_space>0
    assert not np.any(d.schematic.blocks)
    assert (d.spaces,d.analysis_state,d.schematic.metadata_digest())==original


def test_facility_approval_roundtrip_undo_redo_and_changed_target_block(tmp_path):
    d,fid,left,right=facility_document()
    original=deepcopy(d.spaces),d.schematic.metadata_digest()
    ctx=enrich_context(d,d.selection,build_context(d,d.selection))
    p=compile_plan(d,d.selection,plan(operation('floor','minecraft:glass',(0,0,0),(10,2,4))),ctx,d.revision)
    d.active_design_facility=None
    with pytest.raises(ValueError,match='선택'):
        d.approve(p)
    select(d,fid);d.approve(p)
    after=d.schematic.blocks.copy()
    assert d.undo_stack[-1]['facility_id']==fid
    target=tmp_path/'facility.shipproj';d.save_project(target)
    q=Document.open_project(target)
    assert q.spaces==original[0] and q.schematic.metadata_digest()==original[1]
    q.undo();assert not np.any(q.schematic.blocks)
    q.redo();assert np.array_equal(q.schematic.blocks,after)
    q.undo()
    del q.analysis_state['facilities'][fid]
    with pytest.raises(ValueError):q.redo()


def test_missing_and_exterior_members_are_reviewed_without_mutation():
    d,fid,left,right=facility_document()
    state=deepcopy(d.analysis_state)
    state['facilities'][fid]['members'].append('candidate:missing')
    d.analysis_state=state
    with pytest.raises(ValueError,match='찾을 수 없습니다'):resolve(d,fid)
    d.analysis_state=deepcopy(state)
    d.space_candidates=[dict(id='missing',volume_runs=[[2,1,2,3]],decision='exclude',space_class='exterior_air')]
    with pytest.raises(ValueError,match='외부'):resolve(d,fid)
    assert len(d.spaces)==2 and not np.any(d.schematic.blocks)


def test_facility_gui_one_request_reaches_both_rooms_and_switch_invalidates(window,server):
    w=window;client,h=server
    d,fid,left,right=facility_document()
    d.active_design_facility=None
    w.set_document(d);wait_for(lambda:ready(w))
    assert w.workbench.select_key('facility:'+fid)
    assert d.active_design_facility==fid
    panel=w.ai_panel;panel.url.setText(client.url);panel.interior_mode.setChecked(True)
    panel.prompt.setPlainText('두 정비 구역에 유리 장식을 하나씩 설계해 줘')
    h.responses=[json.dumps(plan(operation('add','minecraft:glass',(0,0,0),(0,0,0)),
                                operation('add','minecraft:glass',(7,0,0),(7,0,0))))]
    panel.generate();wait_for(lambda:panel.job is None and ready(w))
    assert w.proposal is not None and len(w.proposal.indices)==2
    assert all(contains(space,w.proposal.indices,d.schematic.size).any() for space in (left,right))
    assert not np.any(d.schematic.blocks)
    w.workbench.select_key('space:'+left['id'])
    assert d.active_design_facility is None and w.proposal is None


def test_facility_material_rebuild_and_chat_replay_keep_scope_and_routes(window,server):
    w=window;client,h=server
    d,fid,left,right=facility_document();d.active_design_facility=None
    d.analysis_state['scene_records']={'route:test':dict(label='미확정 경로',positions=[[3,2,5],[4,2,5]],status='불확실')}
    w.set_document(d);wait_for(lambda:ready(w));w.workbench.select_key('facility:'+fid)
    panel=w.ai_panel;panel.url.setText(client.url);panel.interior_mode.setChecked(True)
    h.responses=[json.dumps(plan(operation('add','minecraft:glass',(0,0,0),(0,0,0)),
                                operation('add','minecraft:glass',(7,0,0),(7,0,0))))]
    panel.prompt.setPlainText('두 공간에 유리 장식');panel.generate()
    wait_for(lambda:panel.job is None and ready(w))
    assert w.proposal is not None
    panel.material_editor.table.item(0,2).setText('minecraft:oak_planks')
    panel.material_editor.rebuild();wait_for(lambda:ready(w))
    assert w.proposal is not None and len(w.proposal.indices)==2
    assert panel.last_result['context']['interior_design']['scope']=='exact_facility_union'
    assert panel.last_result['context']['interior_design']['route_corridor_reserved_cells']>0
    panel.chat_view.refresh(True)
    latest=panel.chat_view.selected()
    assert latest['selection']['facility_id']==fid and latest['interior_mode'] is True
    w.workbench.select_key('space:'+left['id'])
    assert d.active_design_facility is None
    assert panel.chat_view.load()
    assert d.active_design_facility==fid and panel.interior_mode.isChecked()
    assert len(d.permission_space()['members'])==2 and w.proposal is None
    assert not np.any(d.schematic.blocks)
