import sys,copy,json
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from create_v09_demo import build_demo,EXPECTED,expected_pairs
from ship_architect.navigation import Navigation,overlap_cells
from ship_architect.equipment import inventory
from ship_architect.spaces import shape_metrics
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space,box_runs,bbox,contains
from ship_architect.ai_design import build_context,compile_plan,PlanError
from test_ai_design import plan,operation

@pytest.mark.parametrize('opened,expected',[(True,'connected'),(False,'blocked')])
def test_door_states_and_actual_route(opened,expected):
 d=build_demo(door_open=opened);before=d.schematic.blocks.copy();nav=Navigation(d)
 assert nav.pair('A','B')['status']==expected
 route=nav.route('A','B');assert bool(route['path'])==opened
 if opened:
  assert any(p[0]==8 and p[2]==4 for p in route['path'])
  assert all(abs(a[1]-b[1])<=1 for a,b in zip(route['path'],route['path'][1:]))
 assert np.array_equal(before,d.schematic.blocks)

@pytest.mark.parametrize('facing,expected',[('east','connected'),('west','disconnected'),('north','disconnected')])
def test_stair_direction_and_headroom(facing,expected):
 d=build_demo(stair_facing=facing);nav=Navigation(d);assert nav.pair('C','D')['status']==expected
 assert bool(nav.route('C','D')['path'])==(expected=='connected')
 if expected=='connected':
  d.schematic.blocks[3,11,4]=1;assert Navigation(d).pair('C','D')['status']!='connected'


def test_isolated_overlap_elevator_uncertainty_and_equipment_preservation():
 d=build_demo();before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();nav=Navigation(d)
 assert nav.pair('A','G')['status']=='disconnected'
 assert nav.pair('A','A_DUP')['status']=='overlap' and not nav.route('A','A_DUP')['path']
 assert nav.pair('E','F')['status']=='disconnected'
 inv=inventory(d);assert any(set(e['spaces'])=={'E','F'} and e['status']=='uncertain' for e in inv['elevator_candidates'])
 assert inv['total']==5 and {'ComputerCraft','Create 엘리베이터 후보','추진 설비 후보','전력 설비 후보'}.issubset(set(inv['by_category']))
 assert all(r['operation']=='미확인' for r in inv['records'])
 assert np.array_equal(before,d.schematic.blocks) and meta==d.schematic.metadata_digest()


def test_analysis_bounds_and_memory_cap():
 d=build_demo();nav=Navigation(d,Bounds((0,0,0),(17,5,8)));assert nav.pair('A','D')['status']=='uncertain'
 huge=Document(Schematic.empty(300,100,300))
 with pytest.raises(ValueError,match='8백만'):Navigation(huge)


def test_unknown_floor_and_malformed_open_door_do_not_certify_connection():
 d=build_demo();d.schematic.palette[10000]='mystery:floor';d.schematic.blocks[0,2:7,7]=10000
 assert Navigation(d).pair('A','B')['status']=='uncertain'
 d=build_demo();d.schematic.blocks[2,4,8]=0
 assert Navigation(d).pair('A','B')['status']!='connected'


def test_l_t_corridor_width_and_small_room_classification():
 a=np.zeros((30,30),bool);a[2:25,2:5]=True;a[22:25,2:25]=True
 assert shape_metrics(a)['kind']=='corridor'
 a=np.zeros((30,30),bool);a[3:6,2:27]=True;a[3:27,13:16]=True
 assert shape_metrics(a)['kind']=='corridor'
 a=np.ones((8,8),bool);assert shape_metrics(a)['kind']=='room'


def test_zero_changes_diagnose_shape_protection_same_and_occupied():
 d=Document(Schematic.empty(12,8,12));s=new_space(box_runs(Bounds((3,1,3),(8,5,8))),d.schematic.size);d.set_spaces([s],active=s['id']);d.selection=Bounds((0,1,0),(9,5,9))
 ctx=build_context(d,d.selection);p=compile_plan(d,d.selection,plan(operation('floor','minecraft:stone',hi=(0,0,0))),ctx,d.revision)
 assert len(p.indices)==0 and p.reasons['operations'][0]['excluded']['outside_space']==1
 assert not d.approve(p) and not d.schematic.blocks.any()
 d.selection=Bounds(*bbox(s));d.add_zone('lock',d.selection,True);p=d.propose(d.selection,1,'fill');assert p.reasons['protected']>0
 d.undo();d.schematic.blocks[d.selection.slices]=1;p=d.propose(d.selection,1,'fill');assert p.reasons['same_material']>0
 glass=d.add_material('minecraft:glass');p=d.propose(d.selection,glass,'fill');assert p.reasons['occupied_not_allowed']>0


def test_manual_corrections_persist_and_recognition_does_not_overwrite(tmp_path):
 d=build_demo();old=copy.deepcopy(d.spaces);new=copy.deepcopy(old);new[0]['runs']=new[0]['runs'][:-1];d.set_spaces(new,'space_remove',active='A');assert d.spaces[0]['manual_modified']
 p=tmp_path/'manual.shipproj';d.save_project(p);r=Document.open_project(p);assert r.spaces==d.spaces;r.undo();assert r.spaces==old;r.redo();assert r.spaces==d.spaces


def test_open_door_axis_and_partial_scope_context():
 d=build_demo();sc=d.schematic
 for i,state in list(sc.palette.items()):
  if 'oak_door' in state:sc.palette[i]=state.replace('facing=east','facing=north')
 assert Navigation(d).pair('A','B')['status']=='disconnected'
 d.selection=Bounds((2,1,3),(4,2,4));ctx=build_context(d,d.selection)
 assert all(0<=r[0]<2 and 0<=r[1]<2 and 0<=r[2]<=r[3]<3 for r in ctx['exact_space']['runs'])
 assert ctx['exact_space']['cell_count']==12


def test_navigation_cancel_preserves_data():
 from ship_architect.chunks import Cancelled
 d=build_demo();before=d.schematic.blocks.copy()
 with pytest.raises(Cancelled):Navigation(d,cancel=lambda:True)
 assert np.array_equal(before,d.schematic.blocks)


@pytest.mark.parametrize('variant,pair',[(v,p) for v in ('open','closed','wrong_stairs') for p in expected_pairs(v)],ids=lambda p:p if isinstance(p,str) else p['from']+'-'+p['to'])
def test_explicit_all_space_pair_expectations(variant,pair):
 d=build_demo(door_open=variant!='closed',stair_facing='west' if variant=='wrong_stairs' else 'east');nav=Navigation(d)
 result=nav.pair(pair['from'],pair['to']);assert result['status']==pair['nav_status']
 if pair['ui_status']=='uncertain' and pair['nav_status']=='disconnected':
  assert any(set(e['spaces'])=={pair['from'],pair['to']} for e in inventory(d)['elevator_candidates'])


def test_legacy_space_without_provenance_restores_exact_metadata_on_undo(tmp_path):
 d=build_demo()
 for s in d.spaces:s.pop('origin',None);s.pop('manual_modified',None)
 before=copy.deepcopy(d.spaces);edited=copy.deepcopy(d.spaces);edited[0]['runs']=edited[0]['runs'][:-1];d.set_spaces(edited,'space_remove','A');assert d.spaces[0]['manual_modified']
 d.undo();assert d.spaces==before;d.redo();assert d.spaces[0]['manual_modified']
 p=tmp_path/'legacy.shipproj';d.save_project(p);r=Document.open_project(p);r.undo();assert r.spaces==before
