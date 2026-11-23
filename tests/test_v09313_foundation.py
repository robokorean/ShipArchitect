"""Facility metadata, candidate routes, display invalidation and device evidence regressions."""
from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import Qt

from ship_architect.analysis_state import commit, validate
from ship_architect.axis_plan import PlaneMasks, sampled_space_mask
from ship_architect.equipment import device_kind, inventory
from ship_architect.facilities import describe, dissolve, save_facility
from ship_architect.navigation import Navigation, analysis_spaces
from ship_architect.scene_registry import delete_items
from ship_architect.space_boundaries import boundary_relation
from ship_architect.space_model import box_runs, new_space
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from test_ai_gui import ready, wait_for, window
from test_v09310_precision import scene_document
from test_v09312_review import candidate, recognize_fixture


def test_facility_is_logical_union_and_roundtrips_without_editing_spaces(tmp_path):
    d, spaces, _ = scene_document()
    result=recognize_fixture()
    commit(d,dict(d.analysis_state,last_result=result))
    original=deepcopy(d.spaces),d.schematic.blocks.copy(),d.schematic.metadata_digest()
    refs={'space:'+spaces[0]['id'],'candidate:room','candidate:piece'}
    fid=save_facility(d,refs,'큰 격납고','격납고')
    assert set(describe(d,d.analysis_state['facilities'][fid])['members'])==refs
    assert d.spaces==original[0]
    d.undo()
    assert not d.analysis_state.get('facilities')
    d.redo()
    target=tmp_path/'logical.shipproj'
    d.save_project(target)
    q=Document.open_project(target)
    assert q.analysis_state['facilities']==d.analysis_state['facilities']
    assert q.spaces==original[0] and np.array_equal(q.schematic.blocks,original[1])
    assert q.schematic.metadata_digest()==original[2]
    dissolve(d,fid)
    assert d.spaces==original[0] and not d.analysis_state['facilities']
    d.undo()
    assert fid in d.analysis_state['facilities']


def test_facility_delete_prunes_only_deleted_members_and_undo_restores():
    d, spaces, _=scene_document()
    refs={'space:'+s['id'] for s in spaces}
    fid=save_facility(d,refs,'Engine room')
    delete_items(d,{spaces[0]['id']},set(),set())
    assert d.analysis_state['facilities'][fid]['members']==['space:'+spaces[1]['id']]
    d.undo()
    assert set(d.analysis_state['facilities'][fid]['members'])==refs
    d.redo()
    delete_items(d,{spaces[1]['id']},set(),set())
    assert not d.analysis_state['facilities']


def test_reanalysis_retains_unresolved_facility_references_for_review():
    d=Document(Schematic.empty(12,8,12))
    commit(d,dict(d.analysis_state,last_result=recognize_fixture()))
    fid=save_facility(d,['candidate:room','candidate:piece'],'Facility')
    altered=deepcopy(d.analysis_state)
    altered['last_result']['candidates']=[]
    commit(d,altered)
    detail=describe(d,d.analysis_state['facilities'][fid])
    assert detail['missing']==['candidate:piece','candidate:room']
    assert not detail['members'] and detail['physical_cells']==0
    save_facility(d,detail['missing'],'Rename','검토',fid)
    assert d.analysis_state['facilities'][fid]['name']=='Rename'
    with pytest.raises(ValueError):
        validate(dict(d.analysis_state,facilities={'bad':{'name':'test','purpose':'test','members':['candidate:x'],'approved':False}}),d.schematic.size)


def test_touching_candidates_share_air_boundary_but_do_not_certify_traversal():
    first={'id':'one','runs':[[2,3,1,3],[3,3,1,3]]}
    second={'id':'two','volume_runs':[[2,3,4,6],[3,3,4,6]]}
    report=boundary_relation(first,second)
    assert report['shared_air_faces']==2 and report['overlap_cells']==0
    assert report['traversability']=='unverified'
    second['volume_runs'][0][2]=3
    assert boundary_relation(first,second)['overlap_cells']==1


def test_candidate_navigation_does_not_require_confirming_rooms():
    d=Document(Schematic.empty(12,7,10))
    d.schematic.blocks[1,1:8,1:11]=1
    a=candidate('one',[[y,z,2,4] for y in (2,3) for z in range(2,7)],[2,2,2],[4,3,6])
    b=candidate('two',[[y,z,5,9] for y in (2,3) for z in range(2,7)],[5,2,2],[9,3,6])
    commit(d,dict(d.analysis_state,last_result={'method':'exterior_voxel_mesh','candidates':[a,b]}))
    nav=Navigation(d,spaces=analysis_spaces(d))
    route=nav.route('candidate:one','candidate:two')
    assert route['path'] and route['status']=='connected'
    assert route['game_collision_verified'] is False and not d.spaces
    d.schematic.blocks[2:4,:,5]=1
    assert Navigation(d,spaces=analysis_spaces(d)).pair('candidate:one','candidate:two')['status']!='connected'


@pytest.mark.parametrize('state,expected',[
    ('computercraft:computer_advanced[facing=north]','CC 컴퓨터'),
    ('computercraft:wireless_modem_advanced','CC 무선 모뎀'),
    ('computercraft:cable','CC 케이블'),('create:shaft[axis=x]','회전축 후보'),
    ('create:elevator_pulley[axis=x]','승강기 장치 후보'),
    ('unknownmod:unidentified_machine','기타 장치·블록 후보'),
])
def test_device_id_detail_without_runtime_claim(state,expected):
    assert device_kind(state)==expected


def test_inventory_discovers_candidate_relationships_and_exact_adjacent_devices():
    d=Document(Schematic.empty(12,8,12))
    ids={'create:shaft[axis=x]':2,'create:gearbox[axis=x]':3,'unknown:panel':4}
    for state,idx in ids.items():d.schematic.palette[idx]=state
    d.schematic.blocks[2,3,3:6]=[2,3,4]
    commit(d,dict(d.analysis_state,last_result=recognize_fixture()))
    original=d.schematic.blocks.copy(),d.schematic.metadata_digest()
    result=inventory(d)
    assert result['total']==3 and result['unclassified']==1
    assert all(row['operation']=='미확인' for row in result['records'])
    shaft=next(row for row in result['records'] if 'shaft' in row['block'])
    assert shaft['candidate_spaces']==['room'] and not shaft['spaces']
    assert len(result['assembly_candidates'])==1
    assert result['assembly_candidates'][0]['status']=='uncertain'
    assert np.array_equal(original[0],d.schematic.blocks) and original[1]==d.schematic.metadata_digest()


def test_facility_explorer_selects_and_hides_members_without_data_change(window):
    w=window
    d,spaces,_=scene_document()
    refs={'space:'+s['id'] for s in spaces}
    fid=save_facility(d,refs,'중앙 격납고','격납고')
    w.set_document(d)
    wait_for(lambda:ready(w))
    b=w.workbench
    assert b.select_key('facility:'+fid)
    assert b.keys_for_scope('group')==refs and w.view.scene_selection==refs
    assert '중앙 격납고' in b.summary_properties.toPlainText()
    before=deepcopy(d.spaces),d.schematic.blocks.copy()
    b.nodes['facility:'+fid].setCheckState(0,Qt.CheckState.Unchecked)
    wait_for(lambda:all(not b.record_visible(key) for key in refs))
    assert d.spaces==before[0] and np.array_equal(d.schematic.blocks,before[1])


def test_plane_mask_cache_obeys_changed_clip_and_keeps_original_mask(window):
    w=window
    d,spaces,_=scene_document()
    w.set_document(d)
    wait_for(lambda:ready(w))
    cache=PlaneMasks()
    runs=spaces[0]['runs']
    args=(runs,w.view,1,2,1,(14,14))
    before=cache.get(*args).copy()
    np.testing.assert_array_equal(before,sampled_space_mask(spaces[0],1,2,1,(14,14)))
    cache.get(*args)
    assert cache.cache.hits==1
    w.view.single_layer=True
    w.view.layer=4
    assert not cache.get(*args).any()
    assert cache.cache.bytes<=cache.cache.limit
    np.testing.assert_array_equal(before,sampled_space_mask(spaces[0],1,2,1,(14,14)))


def test_picture_cache_reuses_hits_and_invalidates_camera(window):
    w=window
    d,spaces,_=scene_document()
    w.set_document(d)
    wait_for(lambda:ready(w))
    b=w.workbench
    b.select_key('space:'+spaces[0]['id'])
    w.view.grab()
    wait_for(lambda:ready(w))
    w.view.grab()
    hits=[(key,depth) for key,poly,depth in w.view.scene_hits]
    before=w.view.space_renderer.pictures.hits
    shot=w.view.grab().toImage()
    assert w.view.space_renderer.pictures.hits>before
    assert [(key,depth) for key,poly,depth in w.view.scene_hits]==hits
    assert shot==w.view.grab().toImage()
    entries=len(w.view.space_renderer.pictures.items)
    w.view.yaw+=0.2
    w.view.schedule_render()
    wait_for(lambda:ready(w))
    w.view.grab()
    assert len(w.view.space_renderer.pictures.items)>entries


def test_facility_dialog_approval_and_split_keep_membership(window,monkeypatch):
    from PySide6.QtWidgets import QDialog,QLineEdit
    w=window
    d=Document(Schematic.empty(12,8,12))
    first=new_space(box_runs(Bounds((2,2,2),(7,4,6))),d.schematic.size,'Large')
    second=new_space([[2,8,2,7]],d.schematic.size,'Annex')
    d.set_spaces([first,second])
    w.set_document(d)
    wait_for(lambda:ready(w))
    b=w.workbench
    b.select_key('space:'+first['id'])
    b.select_key('space:'+second['id'],True)
    def accept(dialog):
        dialog.findChildren(QLineEdit)[0].setText('Confirmed facility')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept)
    b.facilities.edit()
    fid=next(iter(d.analysis_state['facilities']))
    assert set(d.analysis_state['facilities'][fid]['members'])=={'space:'+first['id'],'space:'+second['id']}
    b.select_key('space:'+first['id'])
    monkeypatch.setattr(b.space_review,'_approve',lambda *args:True)
    b.space_review.split(Bounds((2,2,2),(3,4,6)))
    assert len(d.spaces)==3 and len(d.analysis_state['facilities'][fid]['members'])==3
    w.undo()
    assert len(d.spaces)==2 and len(d.analysis_state['facilities'][fid]['members'])==2


def test_explorer_candidate_pair_runs_route_and_persists_uncertainty(window):
    w=window
    d=Document(Schematic.empty(12,7,10))
    d.schematic.blocks[1,1:8,1:11]=1
    candidates=[candidate('left',[[y,z,2,4] for y in (2,3) for z in range(2,7)],[2,2,2],[4,3,6]),
                candidate('right',[[y,z,5,9] for y in (2,3) for z in range(2,7)],[5,2,2],[9,3,6])]
    commit(d,dict(d.analysis_state,last_result={'method':'exterior_voxel_mesh','candidates':candidates}))
    w.set_document(d)
    wait_for(lambda:ready(w))
    b=w.workbench
    b.select_key('candidate:left')
    b.select_key('candidate:right',True)
    assert b.navigation_selection()==['candidate:left','candidate:right']
    b.route_selected()
    wait_for(lambda:w.network_view.nav is not None and ready(w))
    wait_for(lambda:bool(w.view.route_path) and ready(w))
    record=next(v for k,v in d.analysis_state['scene_records'].items() if k.startswith('route:'))
    assert set(record['candidate_ids'])=={'left','right'}
    assert record['spaces']==[] and '미검증' in record['status'] and not d.spaces
