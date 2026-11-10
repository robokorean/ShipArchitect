import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
import pytest
from create_v0931_demo import build
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.recognition_compare import compare_methods,overlap
from ship_architect.voxel_mesh import indexed_mesh,obj_bytes
from ship_architect.analysis_state import commit,register,crop
from ship_architect.space_model import new_space,from_candidate
from ship_architect.chunks import Cancelled

def cells(c):return {(x,y,z) for y,z,a,b in c['volume_runs'] for x in range(a,b+1)}

@pytest.fixture(scope='module')
def fixture():
 d,g=build();return d,g,recognize_exterior(d,d.selection)

@pytest.mark.parametrize('name',['L+pillar+upper','C','open_hangar','roof_slit'])
def test_exact_shapes_and_no_filled_bbox(fixture,name):
 d,g,r=fixture;c=next(c for c in r['candidates'] if cells(c)==g[name]);assert c['volume']==len(g[name]) and c['validation']['mesh_volume_matches_voxels']
 if name=='roof_slit':assert c['decision']=='exclude'
 elif name=='open_hangar':assert c['decision']=='review' and c['virtual_boundary']['raw_exterior_cells']==len(g[name]) and c['virtual_boundary']['unit_faces']>0
 else:assert c['decision']=='auto'


def test_air_door_rooms_split_without_claiming_movement_connectivity(fixture):
 d,g,r=fixture;parts=[c for c in r['candidates'] if cells(c)&g['door_rooms_union']]
 assert len(parts)==2 and set.union(*(cells(c) for c in parts))==g['door_rooms_union']
 assert all(c['decision']=='review' for c in parts) and any(q['traversability']=='uncertain' for q in r['connections'])
 assert any('open=true' in q['block'] for q in r['semantic_features']) and any('_stairs' in q['block'] for q in r['semantic_features'])


def test_mesh_volume_exposed_area_and_obj_are_independent_of_bbox(fixture):
 d,g,r=fixture;c=next(c for c in r['candidates'] if cells(c)==g['L+pillar+upper']);m=indexed_mesh(c['volume_runs']);expected=g['L+pillar+upper']
 directions=[(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
 exposed=sum((x+dx,y+dy,z+dz) not in expected for x,y,z in expected for dx,dy,dz in directions)
 assert m['volume']==len(expected) and m['area']==exposed and len(m['vertices'])==len(np.unique(m['vertices'],axis=0))
 assert np.all(m['edges'][:,0]<m['edges'][:,1]) and b'\nf ' in obj_bytes(m)
 assert indexed_mesh(copy.deepcopy(c['volume_runs']))['digest']==m['digest']


def test_domain_full_coverage_source_and_NBT_unchanged(fixture):
 d,g,r=fixture;before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest()
 assert r['domain_unassigned_cells']==0 and not r['omitted_domain_cells'] and sum(c['volume'] for c in r['candidates'])==r['domain_cells']
 for c in r['candidates']:
  assert all(not d.schematic.solid(d.schematic.blocks[y,z,a:b+1]).any() for y,z,a,b in c['volume_runs'])
 assert r['shell_face_runs'] and not r['shell_runs_omitted'] and np.array_equal(before,d.schematic.blocks) and meta==d.schematic.metadata_digest()


def test_register_preserve_manual_and_roundtrip_indexed_mesh(tmp_path):
 d,g=build();r=recognize_exterior(d,d.selection);manual=next(c for c in r['candidates'] if cells(c)==g['C']);d.spaces=[new_space(manual['volume_runs'],d.schematic.size,'수정 공간')];before=copy.deepcopy(d.spaces);blocks=d.schematic.blocks.copy();meta=d.schematic.metadata_digest()
 spaces,result,added=register(d,r);state=copy.deepcopy(d.analysis_state);state['last_result']=result;commit(d,state,spaces);assert len(added)==1 and d.spaces[:1]==before
 p=tmp_path/'new.shipproj';d.save_project(p);q=Document.open_project(p);assert q.spaces==d.spaces and q.analysis_state==d.analysis_state
 assert indexed_mesh(q.spaces[-1]['runs'])['digest']==indexed_mesh(d.spaces[-1]['runs'])['digest'];q.undo();assert q.spaces==before;q.redo();assert q.spaces==d.spaces
 target=tmp_path/'new.schem';d.export(target,approved=True);s,_=load_file(target);assert np.array_equal(s.blocks,blocks) and meta==s.metadata_digest()


def test_exact_candidate_conversion_keeps_virtual_hangar_cells(fixture):
 d,g,r=fixture;c=next(c for c in r['candidates'] if cells(c)==g['open_hangar']);assert from_candidate(d,c)['runs']==c['volume_runs']


def test_comparison_IoU_is_exact_and_not_ground_truth(fixture):
 d,g,r=fixture;before=d.schematic.blocks.copy();c=compare_methods(d,d.selection);assert c['old']['method']=='3d_air_components' and c['new']['method']=='exterior_voxel_mesh' and c['matches']
 assert all(0<=m['IoU']<=1 for m in c['matches']) and np.array_equal(before,d.schematic.blocks)
 assert overlap([[1,1,1,3]],[[1,1,2,5]])==2
 state=copy.deepcopy(d.analysis_state);state['comparison']=c;state['last_result']=c['new'];commit(d,state);d.undo();assert 'comparison' not in d.analysis_state;d.redo();assert d.analysis_state['comparison']==c


def test_mesh_budget_or_cancel_never_writes_source(fixture,monkeypatch):
 import ship_architect.exterior_recognition as engine
 d,g=build();before=d.schematic.blocks.copy()
 with pytest.raises(Cancelled):recognize_exterior(d,d.selection,cancel=lambda:True)
 monkeypatch.setattr(engine,'indexed_mesh',lambda runs:(_ for _ in ()).throw(ValueError('test budget')));r=recognize_exterior(d,d.selection)
 assert not any(c['decision']=='auto' for c in r['candidates']) and r['mesh_failures'] and np.array_equal(before,d.schematic.blocks)


def test_stale_analysis_cannot_be_registered(fixture):
 d,g=build();r=recognize_exterior(d,d.selection);d.block_epoch+=1
 with pytest.raises(ValueError,match='오래'):register(d,r)


def test_empty_project_and_thin_selection_are_safe():
 d=Document(Schematic.empty(10,8,10));r=recognize_exterior(d,d.selection);assert r['candidates']==[] and r['exterior_air_cells']==800
 r=recognize_exterior(d,Bounds((1,2,1),(8,2,8)));assert not r['candidates']


def test_large_exterior_merge_is_reanalysed_and_virtual_splits_stay_review():
 s=Schematic.empty(116,34,34);a=s.blocks;a[2:32,2:32,2:114]=1
 a[3:31,3:31,3:56]=0;a[3:31,3:31,58:113]=0
 # 4x4 passage retains a one-cell core, removed by the second core pass.
 a[3:7,15:19,56:58]=0
 # Open exterior mouth: both rooms are outside-connected in the original air graph.
 a[3:15,2:4,100:110]=0
 d=Document(s);r=recognize_exterior(d,d.selection)
 assert r['merge_reanalyses'] and any(e['outcome']=='partitioned_review' for e in r['merge_reanalyses'])
 assert sum(c['volume'] for c in r['candidates'])==r['domain_cells']
 assert all(c['decision']!='auto' for c in r['candidates'] if c['validation']['reanalysis_index'] is not None)


def test_mesh_validation_is_required_for_auto_registration(fixture):
 d,g=build();r=recognize_exterior(d,d.selection)
 for c in r['candidates']:c['validation']['mesh_volume_matches_voxels']=False
 assert not register(d,r)[2]


def test_cropped_candidate_clears_stale_mesh_statistics(fixture):
 d,g,r=fixture;c=copy.deepcopy(next(c for c in r['candidates'] if c['decision']=='auto'));before=c['mesh'];crop(c,Bounds(tuple(c['lo']),tuple(min(v+2,n-1) for v,n in zip(c['lo'],d.schematic.size))),d.schematic.size)
 assert before and c['mesh'] is None and c['validation']['mesh_volume_matches_voxels'] is None and c['decision']=='review'
 assert indexed_mesh(c['volume_runs'])['volume']==c['volume']


def test_missing_intruding_duplicate_mesh_faces_produce_mismatch_coordinates(fixture):
 from ship_architect.voxel_mesh import validate_boundary
 d,g,r=fixture;c=next(c for c in r['candidates'] if c['decision']=='auto');m=indexed_mesh(c['volume_runs']);assert validate_boundary(c['volume_runs'],m)['matches']
 removed=dict(m,faces=m['faces'][1:]);v=validate_boundary(c['volume_runs'],removed);assert not v['matches'] and v['missing_unit_faces']>0 and v['mismatch_coordinates']
 duplicate=dict(m,faces=np.concatenate([m['faces'],m['faces'][:1]]));assert validate_boundary(c['volume_runs'],duplicate)['duplicate_unit_faces']>0
 moved=dict(m,vertices=m['vertices']+np.array([1,0,0]));v=validate_boundary(c['volume_runs'],moved);assert not v['matches'] and v['mismatch_coordinates']
