"""Interior evidence, protected circulation, strict plan editing and the new front door."""
import copy,json
import numpy as np
import pytest
from ship_architect.model import Bounds,Document
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space
from ship_architect.ai_design import build_context,compile_plan,PlanError,generate_design
from ship_architect.interior_design import enrich_context,reserved_route_indices
from test_ai_gui import window,ready,wait_for
from test_ai_design import plan,operation,server


def interior():
    d=Document(Schematic.empty(16,9,16))
    d.selection=Bounds((2,2,2),(11,6,11))
    s=new_space([[y,z,2,11] for y in range(2,7) for z in range(2,12)],d.schematic.size,'작업실','정비')
    d.set_spaces([s],active=s['id'])
    d.analysis_state['facilities']={'hangar':dict(name='사용자 격납고',purpose='격납고',members=['space:'+s['id']],approved=True)}
    d.analysis_state['scene_records']={
        'equip:4,3,4':dict(position=[4,3,4],block='computercraft:computer_advanced[facing=north]',
                          category='ComputerCraft',device_kind='CC 컴퓨터',operation='미확인'),
        'route:uncertain':dict(positions=[[2,2,5],[11,2,5]],status='불확실',spaces=[s['id']])}
    return d,d.selection


def test_evidence_retains_uncertainty_and_does_not_change_metadata():
    d,b=interior()
    before=copy.deepcopy(d.analysis_state),copy.deepcopy(d.spaces),d.schematic.blocks.copy(),d.schematic.metadata_digest()
    ctx=enrich_context(d,b,build_context(d,b))
    facts=ctx['interior_design']
    assert facts['facilities'][0]['purpose']=='격납고'
    assert facts['equipment_samples'][0]['operation']=='unverified'
    assert facts['routes'][0]['game_collision_verified'] is False
    assert facts['route_corridor_reserved_cells']>0
    assert d.analysis_state==before[0] and d.spaces==before[1]
    assert np.array_equal(before[2],d.schematic.blocks) and d.schematic.metadata_digest()==before[3]


def test_uncertain_route_is_reserved_and_valid_interior_add_is_preview_only():
    d,b=interior()
    ctx=enrich_context(d,b,build_context(d,b))
    with pytest.raises(PlanError,match='이동 경로'):
        compile_plan(d,b,plan(operation('add','minecraft:glass',(0,0,3),(9,1,3))),ctx,d.revision)
    p=compile_plan(d,b,plan(operation('add','minecraft:glass',(0,0,0),(2,0,0))),ctx,d.revision)
    assert len(p.indices)==3 and not np.any(d.schematic.blocks)
    assert not np.intersect1d(p.indices,reserved_route_indices(d,b)).size
    d.approve(p)
    assert np.count_nonzero(d.schematic.blocks)==3
    d.undo()
    assert not np.any(d.schematic.blocks)


def test_existing_ship_needs_exact_confirmed_space():
    d,b=interior()
    d.source='existing.schem'
    d.active_space_id=None
    with pytest.raises(ValueError,match='확정 공간'):
        enrich_context(d,b,build_context(d,b))


def test_ollama_request_contains_real_evidence_and_exact_scope(server):
    client,h=server;d,b=interior()
    h.responses=[json.dumps(plan(operation('add','minecraft:glass',(0,0,0),(2,0,0))))]
    result=generate_design(client,'qwen3:4b',d,b,'유리 장식 구역을 설계해 줘',interior_mode=True)
    assert result['context']['interior_design']['equipment_counts']['CC 컴퓨터']==1
    assert len(result['proposal'].indices)==3 and not np.any(d.schematic.blocks)


def test_basic_flow_and_advanced_tools_share_original_commands(window):
    w=window;f=w.integrated_ui.workflow
    f.set_advanced(False)
    assert f.bar.isVisible() and not w.integrated_ui.ribbon_bar.isVisible()
    assert f.command('프로젝트','저장') is w.integrated_ui.buttons['프로젝트','저장'].defaultAction()
    assert f.next_step.text()
    f.goto(1)
    assert w.ai_panel.interior_mode.isChecked() and f.pages.currentIndex()==1
    f.set_advanced(True)
    assert w.integrated_ui.ribbon_bar.isVisible()
    f.set_advanced(False)
    assert not w.integrated_ui.tasks.result_dock.isVisible()


def test_edited_plan_is_revalidated_and_requires_explicit_approval(window,server):
    w=window;client,h=server;p=w.ai_panel
    d,b=interior();w.set_document(d);wait_for(lambda:ready(w))
    p.url.setText(client.url);p.interior_mode.setChecked(True)
    h.responses=[json.dumps(plan(operation('add','minecraft:glass',(0,0,0),(2,0,0))))]
    p.prompt.setPlainText('유리 장식을 설계해 줘');p.generate()
    wait_for(lambda:p.job is None and ready(w))
    assert w.proposal is not None and len(w.proposal.indices)==3
    edited=copy.deepcopy(p.last_plan)
    edited['operations'][0]['max']=[3,0,0]
    p.revalidate_edited_plan(json.dumps(edited))
    wait_for(lambda:p.job is None and ready(w))
    assert w.proposal is not None and len(w.proposal.indices)==4
    assert not np.any(d.schematic.blocks)
    w.reject()
    assert not np.any(d.schematic.blocks)
