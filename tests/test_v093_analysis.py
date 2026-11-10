import copy,json
import numpy as np
import pytest
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.spatial3d import recognize_3d,partial_bounds,intersects
from ship_architect.analysis_state import commit,register,apply_rules,crop,compare
from ship_architect.space_model import new_space,from_candidate
from ship_architect.equipment import inventory,classification
from ship_architect.chunks import Cancelled


def volume(c):return {(x,y,z) for y,z,a,b in c['volume_runs'] for x in range(a,b+1)}

def build(shape='L'):
 s=Schematic.empty(22,12,20);s.blocks[:]=1
 cells={(x,y,z) for y in range(2,7) for z in range(3,14) for x in range(3,16) if x<8 or z<7}
 if shape=='varying':cells={p for p in cells if p[1]<4 or p[0]<8};cells|={(x,y,z) for y in range(7,10) for x in range(3,8) for z in range(3,7)}
 for x,y,z in cells:s.blocks[y,z,x]=0
 return Document(s),cells


@pytest.mark.parametrize('shape',['L','varying'])
def test_exact_6_connected_volume_not_vertical_footprint(shape):
 d,cells=build(shape);before=d.schematic.blocks.copy();r=recognize_3d(d,d.selection);assert len(r['candidates'])==1;c=r['candidates'][0]
 assert c['decision']=='auto' and volume(c)==cells and all(c['checks'].values())
 assert from_candidate(d,c)['runs']==c['volume_runs'] and np.array_equal(before,d.schematic.blocks)


def test_floorless_void_ceiling_gap_and_narrow_slit_never_auto():
 s=Schematic.empty(24,12,20);s.blocks[:]=1;s.blocks[8,2:9,2:9]=0;s.blocks[2:6,2:15,15]=0;s.blocks[0:4,15:18,2:6]=0
 d=Document(s);r=recognize_3d(d,d.selection)
 assert r['candidates'] and all(c['decision']!='auto' for c in r['candidates'])
 assert any('ceiling_slit_no_headroom' in c['reasons'] for c in r['candidates'])
 assert any('narrow_wall_gap_or_corridor' in c['reasons'] for c in r['candidates'])


def test_exterior_leak_and_unknown_boundary_are_review_or_excluded():
 d,cells=build();d.schematic.blocks[2,3,:4]=0;r=recognize_3d(d,d.selection);assert all(c['decision']!='auto' for c in r['candidates'])
 d,cells=build();d.schematic.palette[2]='unknown:wall';d.schematic.blocks[2,3,2]=2;r=recognize_3d(d,d.selection);assert any('uncertain_block_boundary' in c['reasons'] for c in r['candidates']) and not any(c['decision']=='auto' for c in r['candidates'])


def test_auto_register_bulk_preserves_manual_cells_and_undo_roundtrip(tmp_path):
 d,cells=build();before=d.schematic.blocks.copy();r=recognize_3d(d,d.selection);spaces,r,added=register(d,r);state=copy.deepcopy(d.analysis_state);state['last_result']=r;commit(d,state,spaces,'space_auto_batch')
 assert len(added)==1 and volume(r['candidates'][0])==cells and d.spaces[0]['auto_registered']
 p=tmp_path/'review.shipproj';d.save_project(p);restored=Document.open_project(p);assert restored.analysis_state==d.analysis_state and restored.spaces==d.spaces
 restored.undo();assert not restored.spaces and 'last_result' not in restored.analysis_state;restored.redo();assert restored.spaces==d.spaces
 existing=copy.deepcopy(d.spaces);r=recognize_3d(d,d.selection);spaces,r,added=register(d,r);assert not added and spaces==existing and r['candidates'][0]['decision']=='registered'
 assert np.array_equal(before,d.schematic.blocks)


def test_confidence_or_empty_checks_cannot_auto_register_and_stale_epoch_rejected():
 d,cells=build();r=recognize_3d(d,d.selection);r['candidates'][0]['checks']={};assert register(d,r)[2]==[]
 r=recognize_3d(d,d.selection);d.block_epoch+=1
 with pytest.raises(ValueError,match='오래'):register(d,r)


def test_reusable_exclusion_and_crop_user_choices_survive_reanalysis_and_undo(tmp_path):
 d,cells=build();r=recognize_3d(d,d.selection);state=copy.deepcopy(d.analysis_state);state['space_rules']=[{'reason':'closed_supported_volume','action':'exclude'}];modified=apply_rules(r,state,d.schematic.size);assert modified['candidates'][0]['decision']=='exclude'
 c=copy.deepcopy(r['candidates'][0]);fp=c['fingerprint'];crop(c,Bounds((3,2,3),(6,5,6)),d.schematic.size);state['decisions'][fp]={'action':'crop','runs':c['volume_runs']};out=apply_rules(r,state,d.schematic.size);assert out['candidates'][0]['user_modified'] and out['candidates'][0]['volume_runs']==c['volume_runs']
 assert out['candidates'][0]['volume']==64 and all(3<=a<=b<=6 and 3<=z<=6 for z,a,b in out['candidates'][0]['footprint'])
 state['last_result']=out;commit(d,state);d.undo();assert not d.analysis_state['space_rules'];d.redo();assert d.analysis_state['space_rules']==state['space_rules']
 p=tmp_path/'rules.shipproj';d.save_project(p);q=Document.open_project(p);assert apply_rules(r,q.analysis_state,q.schematic.size)['candidates'][0]['volume_runs']==c['volume_runs']


def test_partial_scope_uses_dirty_and_whole_affected_room_but_not_far_room():
 d,cells=build();d.spaces=[new_space([[y,z,a,b] for y,z,a,b in recognize_3d(d,d.selection)['candidates'][0]['volume_runs']],d.schematic.size)]
 d.spatial_dirty=Bounds((4,2,4),(4,2,4));b=partial_bounds(d);assert b.lo[0]<=3 and b.hi[0]>=15 and b.volume<d.selection.volume
 r=recognize_3d(d,b,partial=True);assert r['partial'] and volume(r['candidates'][0])==cells


def test_real_block_changes_track_partial_reanalysis_and_preserve_nbt(tmp_path):
 d=Document(Schematic.empty(20,12,20));before=d.schematic.metadata_digest();b=Bounds((4,2,4),(6,2,6));p=d.propose(b,1,'fill',True);d.approve(p);assert d.block_epoch==1 and d.spatial_dirty==b
 path=tmp_path/'dirty.shipproj';d.save_project(path);r=Document.open_project(path);assert r.spatial_dirty==b and r.block_epoch==1
 d.undo();assert d.block_epoch==2 and d.schematic.metadata_digest()==before;d.redo();assert d.block_epoch==3


def test_equipment_saved_rule_applies_all_states_without_granting_edit_permission(tmp_path):
 s=Schematic.empty(8,8,8);s.palette[2]='unknown:machine[facing=north]';s.palette[3]='unknown:machine[facing=south]';s.blocks[1,1,1]=2;s.blocks[1,1,2]=3;d=Document(s);before=s.blocks.copy();meta=s.metadata_digest();state=copy.deepcopy(d.analysis_state);state['equipment_rules']['unknown:machine']='전력 설비 후보';commit(d,state,action='equipment_rule')
 r=inventory(d);assert r['by_category']['전력 설비 후보']==2 and all(x['operation']=='미확인' for x in r['records'])
 path=tmp_path/'devices.shipproj';d.save_project(path);q=Document.open_project(path);assert classification(q,'unknown:machine[facing=south]')=='전력 설비 후보';q.undo();assert classification(q,'unknown:machine[facing=south]')=='미분류 모드 블록';q.redo();assert classification(q,'unknown:machine[facing=north]')=='전력 설비 후보'
 assert s.is_decorative('unknown:machine[facing=north]') is False and np.array_equal(s.blocks,before) and s.metadata_digest()==meta


def test_compare_reports_actual_solid_conflicts_and_does_not_modify_geometry():
 d,cells=build();c=recognize_3d(d,d.selection)['candidates'][0];d.schematic.blocks[2,3,3]=1;r=compare(d,c['volume_runs']);assert r['conflict_cells']==1 and r['valid_cells']==len(cells)-1


def test_analysis_cancel_and_limits_fail_closed():
 d,cells=build()
 with pytest.raises(Cancelled):recognize_3d(d,d.selection,cancel=lambda:True)
 huge=Document(Schematic.empty(201,201,201))
 with pytest.raises(ValueError):recognize_3d(huge,huge.selection)


def test_narrow_door_connected_to_outside_produces_review_core_not_auto():
 s=Schematic.empty(26,14,24)
 # Hollow room with one one-wide/two-high door into the actual exterior.
 s.blocks[2:10,4:19,4:21]=1;s.blocks[3:9,5:18,5:20]=0;s.blocks[3:5,4,11]=0
 d=Document(s);r=recognize_3d(d,d.selection);parts=[c for c in r['candidates'] if 'narrow_opening_partition' in c['reasons']]
 assert parts and all(c['decision']=='review' and not c['checks']['original_connected_component'] for c in parts)
 assert all(not d.schematic.solid(d.schematic.blocks[y,z,a:b+1]).any() for c in parts for y,z,a,b in c['volume_runs'])
 assert not register(d,r)[2]


def test_selected_open_scope_is_review_instead_of_assumed_entire_exterior():
 d=Document(Schematic.empty(14,10,14));d.schematic.blocks[1,2:12,2:12]=1;b=Bounds((3,2,3),(10,7,10));r=recognize_3d(d,b)
 assert r['candidates'][0]['decision']=='review' and 'scope_or_exterior_open' in r['candidates'][0]['reasons']


def test_packaged_demo_expectations_are_actual_cells_and_protected_manual_geometry():
 import sys
 from pathlib import Path
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
 from create_v093_demo import build as build_demo
 d,expected=build_demo();before=copy.deepcopy(d.spaces);r=recognize_3d(d,d.selection);by_fp={c['fingerprint']:c for c in r['candidates']}
 assert all(by_fp[e['fingerprint']]['decision']==e['decision'] and by_fp[e['fingerprint']]['volume']==e['volume'] for e in expected)
 spaces,r,added=register(d,r);assert len(added)==2 and spaces[:1]==before
 assert {c['decision'] for c in r['candidates']}=={'review','exclude','registered'}
