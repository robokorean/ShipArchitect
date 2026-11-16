from copy import deepcopy

import pytest
from PySide6.QtCore import Qt

from ship_architect.analysis_sessions import block_signature
from ship_architect.analysis_state import commit
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.space_display import hidden_reason
from ship_architect.space_edits import combined, divided, volume
from ship_architect.space_model import new_space
from ship_architect.space_organization import classify, validate_memberships
from ship_architect.spatial3d import fingerprint
from test_ai_gui import ready, wait_for, window
from test_v0934_sessions import room_document


def candidate(key, runs, lo, hi, **fields):
    data={'id':key,'volume_runs':runs,'volume':volume(runs),'lo':lo,'hi':hi,
          'decision':'review','base_decision':'review','kind':'room','source_block_epoch':0,
          'method':'exterior_voxel_mesh','reasons':[],
          'fingerprint':fingerprint(runs),
          'checks':{'usable_floor':True,'two_cell_clearance':True,'wide_floor_patch':True},
          'boundary_contacts':{'roof':1,'top_boundary':1}}
    return data | fields


def recognize_fixture():
    a=candidate('room',[[y,z,2,7] for y in range(2,4) for z in range(3,7)],[2,2,3],[7,3,6])
    b=candidate('piece',[[2,2,2,4]],[2,2,2],[4,2,2],decision='exclude',
                checks={'two_cell_clearance':False},
                fragment={'suggested_owner_id':'room','shared_faces':8,'adjacent_faces':10})
    lift=candidate('lift',[[2,8,2,2],[3,8,2,2]],[2,2,8],[2,3,8],kind='corridor',
                   checks={'two_cell_clearance':True,'usable_floor':False})
    result={'method':'exterior_voxel_mesh','candidates':[a,b,lift],
            'semantic_features':[{'block':'create:elevator_pulley','spaces':['lift']}]}
    return result


def test_classification_preserves_small_lift_and_groups_only_clear_dominant_contact():
    r=recognize_fixture()
    before=deepcopy(r)
    prof=classify(r)
    assert prof['candidate:room'].kind=='support'
    assert prof['candidate:piece'].owner=='candidate:room'
    assert prof['candidate:lift'].kind=='movement'
    assert r==before
    r['candidates'][1]['adjacent_spaces']=[{'id':'lift','unit_faces':8}]
    assert classify(r)['candidate:piece'].owner is None


def test_manual_membership_validations_and_no_cycles():
    r=recognize_fixture()
    direct={'candidate:piece':'candidate:lift'}
    validate_memberships(direct)
    assert classify(r,direct)['candidate:piece'].owner=='candidate:lift'
    assert classify(r,{'candidate:piece':None})['candidate:piece'].owner is None
    with pytest.raises(ValueError):
        validate_memberships({'candidate:piece':'candidate:lift','candidate:lift':'candidate:piece'})


def test_wall_or_device_prevents_merge_and_split_preserves_exact_union():
    d=Document(Schematic.empty(12,8,12))
    a=new_space([[2,3,2,3],[2,4,2,3]],d.schematic.size,'A')
    b=new_space([[2,3,5,7],[2,4,5,5]],d.schematic.size,'B')
    d.set_spaces([a,b])
    with pytest.raises(ValueError,match='연결되지'):
        combined(d,{a['id'],b['id']})
    b['runs'][0][2]=4
    d.set_spaces([a,b])
    merged=combined(d,{a['id'],b['id']},a['id'])
    assert merged['id']==a['id']
    left,right=divided(d,merged,Bounds((2,2,3),(3,2,4)))
    assert volume(left['runs'])+volume(right['runs'])==volume(merged['runs'])
    d.schematic.blocks[2,3,4]=1
    with pytest.raises(ValueError,match='장치'):
        combined(d,{a['id'],b['id']})


def test_group_tree_hidden_reveal_search_and_metadata_undo(window,tmp_path):
    w=window
    d=Document(Schematic.empty(12,8,12))
    r=recognize_fixture()
    commit(d,dict(d.analysis_state,last_result=r))
    w.set_document(d)
    wait_for(lambda:ready(w))
    b=w.workbench
    cfg=w.view.space_display
    cfg['organized']=True
    b.rebuild_tree(True)
    key='candidate:piece'
    assert b.nodes[key].parent().data(0,Qt.ItemDataRole.UserRole)=='attachments:candidate:room'
    assert not b.nodes[key].parent().isExpanded()
    b.search.setText('piece')
    assert key in b.keys_for_scope('visible_tree')
    assert b.select_key(key)
    assert '기본 숨김' in b.space_review.state(key)
    b.space_review.reveal_selected()
    assert b.record_visible(key) and cfg.get('visibility',{})=={}
    b.search.clear()
    b.select_key('candidate:room')
    assert not b.record_visible(key)
    cfg['min_volume']=99999
    assert hidden_reason(w.view,b.records['candidate:lift'],True)=='최소 부피 필터'
    cfg['min_volume']=0
    before=block_signature(d),d.schematic.metadata_digest()
    b.space_review.assign({key},'candidate:lift')
    assert w.view.space_classes[key].owner=='candidate:lift'
    w.undo()
    assert w.view.space_classes[key].owner=='candidate:room'
    w.redo()
    assert w.view.space_classes[key].owner=='candidate:lift'
    path=tmp_path/'membership.shipproj'
    d.save_project(path)
    assert Document.open_project(path).analysis_state['space_memberships']=={key:'candidate:lift'}
    assert (block_signature(d),d.schematic.metadata_digest())==before


def test_one_click_uses_safe_precision_without_auto_confirm(window):
    w=window
    d=room_document()
    w.set_document(d)
    wait_for(lambda:ready(w))
    w.integrated_ui.tasks.quick_analysis_button.click()
    wait_for(lambda:ready(w),seconds=35)
    assert d.analysis_state['last_result']['engine_id']=='precision'
    assert d.space_candidates and w.view.space_display['organized']
    assert not d.spaces
    assert '공간 분석 완료' in w.statusBar().currentMessage()
