import copy,json
from dataclasses import replace
import numpy as np
import nbtlib as nbt
import pytest
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.space_model import (normalize,new_space,box_runs,bbox,contains,subtract,from_candidate,statistics,analyze_connections,token,mask)
from ship_architect.ai_design import build_context,compile_plan,PlanError
from ship_architect.mesh import build_scene,MeshCache
from test_ai_design import plan,operation

def l_space():
 d=Document(Schematic.empty(14,10,14));runs=[[y,z,2,4 if z>=5 else 9] for y in range(1,6) for z in range(2,10)]
 s=new_space(runs,d.schematic.size,'L 격납고','격납고');d.set_spaces([s],active=s['id']);d.selection=Bounds(*bbox(s));return d,s


def test_normalize_and_difference_handles_l_t_asymmetric_multilevel():
 size=(20,12,20);runs=[[1,2,1,5],[1,2,4,7],[1,3,3,4],[7,2,2,8]]
 a=normalize(runs,size);assert a==[[1,2,1,7],[1,3,3,4],[7,2,2,8]]
 rest=subtract(a,[[1,2,3,4]],size);assert rest==[[1,2,1,2],[1,2,5,7],[1,3,3,4],[7,2,2,8]]
 assert normalize(rest+[[1,2,3,4]],size)==a
 for bad in ([[1,2,3,30]],[[True,2,3,4]],[[1,2,4,3]]):
  with pytest.raises(ValueError):normalize(bad,size)


def test_exact_l_shape_fill_preview_approve_undo_redo_project_schem(tmp_path):
 d,s=l_space();i=d.add_material('minecraft:oak_planks');p=d.propose(d.selection,i,'fill')
 assert len(p.indices)==5*(3*8+5*3) and p.outside_space>0
 assert contains(s,p.indices,d.schematic.size).all() and not d.schematic.blocks.any()
 preview=d.preview(p);d.approve(p);assert np.array_equal(preview,d.schematic.blocks)
 d.undo();assert not d.schematic.blocks.any();d.redo();assert np.array_equal(preview,d.schematic.blocks)
 out=tmp_path/'l.shipproj';d.save_project(out);r=Document.open_project(out)
 assert r.spaces==d.spaces and r.active_space_id==d.active_space_id
 r.undo();assert not r.schematic.blocks.any();r.redo()
 out=tmp_path/'l.schem';r.export(out,approved=True);sc,_=load_file(out)
 assert np.array_equal(sc.blocks,preview) and sc.palette[i]=='minecraft:oak_planks'


@pytest.mark.parametrize('mode',['floor','ceiling','walls','fill','erase','replace'])
def test_all_manual_modes_never_escape_exact_shape(mode):
 d,s=l_space()
 if mode in ('erase','replace'):d.schematic.blocks[:]=1
 p=d.propose(d.selection,1 if mode not in ('replace',) else d.add_material('minecraft:glass'),mode,replace=True)
 assert len(p.indices) and contains(s,p.indices,d.schematic.size).all()
 if mode=='walls':
  w,h,l=d.schematic.size
  assert (1*w*l+6*w+4) in p.indices # inner concave edge, absent from bbox walls


def test_forged_preview_commit_and_same_bbox_boundary_change_blocked():
 d,s=l_space();p=d.propose(d.selection,1,'fill');w,h,l=d.schematic.size;outside=2*w*l+8*w+8
 forged=replace(p,indices=np.array([outside]),before=np.array([0]))
 for fn in (d.preview,d.approve):
  with pytest.raises(ValueError,match='셀 밖'):fn(forged)
 changed=copy.deepcopy(d.spaces);changed[0]['runs']=subtract(s['runs'],[[2,3,3,3]],d.schematic.size)
 assert bbox(changed[0])==bbox(s);d.spaces=changed # even an unlogged edit must invalidate token
 with pytest.raises(ValueError,match='경계'):d.approve(p)
 assert not d.schematic.blocks.any()


def test_space_switch_with_identical_bbox_invalidates_proposal():
 d,s=l_space();p=d.propose(d.selection,1,'fill');other=new_space(s['runs'],d.schematic.size);d.spaces.append(other);d.active_space_id=other['id']
 with pytest.raises(ValueError):d.approve(p)


@pytest.mark.parametrize('state',['minecraft:oak_planks','minecraft:stone_bricks','minecraft:glass'])
def test_ai_exact_l_shape_material_pipeline(state,tmp_path):
 d,s=l_space();i=d.add_material(state);ctx=build_context(d,d.selection)
 assert ctx['exact_space']['purpose']=='격납고' and ctx['space_token']==token(s)
 p=compile_plan(d,d.selection,plan(operation('fill',state,hi=(7,4,7))),ctx,d.revision)
 assert p.space_token==token(s) and p.outside_space>0 and np.all(p.after==i)
 assert contains(s,p.indices,d.schematic.size).all();d.approve(p)
 out=tmp_path/(state.split(':')[1]+'.schem');d.export(out,approved=True);r,_=load_file(out)
 assert all(r.palette[int(v)]==state for v in r.blocks.reshape(-1)[p.indices])


def test_ai_context_stale_space_even_if_bounds_match():
 d,s=l_space();ctx=build_context(d,d.selection);d.spaces[0]['runs']=subtract(s['runs'],[[2,3,3,3]],d.schematic.size)
 with pytest.raises(PlanError,match='공간'):compile_plan(d,d.selection,plan(operation('fill','minecraft:stone',hi=(7,4,7))),ctx,d.revision)


def test_space_metadata_undo_redo_does_not_touch_blocks(tmp_path):
 d,s=l_space();before=d.schematic.blocks.copy();other=new_space([[6,2,2,6]],d.schematic.size,'upper')
 d.set_spaces([s,other],'space_split',s['id']);d.undo();assert d.spaces==[s];d.redo();assert len(d.spaces)==2
 f=tmp_path/'spaces.shipproj';d.save_project(f);r=Document.open_project(f);r.undo();assert r.spaces==[s]
 assert np.array_equal(r.schematic.blocks,before)


def test_exact_scope_protects_be_locked_mod_and_original_nbt():
 d,s=l_space();sc=d.schematic
 mod=max(sc.palette)+1;sc.palette[mod]='computercraft:computer_advanced';sc.blocks[2,3,3]=mod
 sc.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([3,2,3]),'Label':nbt.String('보존')}));sc._entities_cache=None
 d.add_zone('잠금',Bounds((2,1,2),(2,3,3)),True);meta=sc.metadata_digest()
 p=d.propose(d.selection,d.add_material('minecraft:glass'),'fill',True)
 idx=2*14*14+3*14+3;assert idx not in p.indices and p.protected>0
 d.approve(p);assert sc.blocks[2,3,3]==mod and sc.metadata_digest()==meta


def test_candidate_varying_ceiling_and_truncation_fail_closed():
 d=Document(Schematic.empty(12,10,12));d.schematic.blocks[4,2,3]=1
 c=dict(id='test',floor_y=1,hi=[5,7,5],footprint=[[2,2,4]],kind='room')
 s=from_candidate(d,c);w,h,l=d.schematic.size
 assert contains(s,[3*w*l+2*w+3],(w,h,l))[0]
 assert not contains(s,[5*w*l+2*w+3],(w,h,l))[0]
 c['footprint_truncated']=True
 with pytest.raises(ValueError,match='잘렸'):from_candidate(d,c)


def test_chunk_mask_cache_and_hidden_floors():
 d,s=l_space();d.schematic.blocks[:]=1;cache=MeshCache()
 full=build_scene(d.schematic,cache=cache);exact=build_scene(d.schematic,cache=cache,space=s,visible_y=[1,2])
 assert len(exact)>0 and exact.stats['cache_hits']==0
 assert all(np.all(q[:,1]>=1) and np.all(q[:,1]<=3) for q,n,m in exact)
 again=build_scene(d.schematic,cache=cache,space=s,visible_y=[1,2]);assert again.stats['cache_hits']>0
 assert len(full)>0


def test_connection_features_door_stair_and_statistics():
 d=Document(Schematic.empty(12,8,12));sc=d.schematic
 sc.palette[90]='minecraft:oak_door[facing=north,half=lower,hinge=left,open=false,powered=false]';sc.palette[91]='minecraft:stone_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]'
 sc.blocks[1,3,5]=90;sc.blocks[2,3,5]=91
 a=new_space([[1,3,2,4],[2,3,2,4]],sc.size,'방');b=new_space([[1,3,6,8],[2,3,6,8]],sc.size,'통로')
 result=analyze_connections(d,[a,b]);assert {'문','계단'}=={v['kind'] for v in result['features']}
 assert any(len(v['spaces'])==2 for v in result['features']) and result['edges']
 assert statistics(d,a)['cells']==6


def test_properties_history_delta_does_not_duplicate_space_runs():
 d,s=l_space();updated=copy.deepcopy(d.spaces);updated[0]['name']='변경';d.set_spaces(updated,active=s['id'])
 record=d.undo_stack[-1];assert record['delta'][s['id']]['before']=={'name':'L 격납고'} and record['delta'][s['id']]['after']=={'name':'변경'}
 assert 'runs' not in json.dumps(record);d.undo();assert d.spaces[0]['runs']==s['runs'] and d.spaces[0]['name']==s['name']


def test_disjoint_vertical_intervals_are_not_filled_by_a_bbox():
 d=Document(Schematic.empty(14,12,14));s=new_space([[1,2,2,5],[2,2,2,5],[8,2,2,5],[9,2,2,5]],d.schematic.size,'두 층');d.set_spaces([s],active=s['id']);d.selection=Bounds(*bbox(s))
 p=d.propose(d.selection,1,'fill');assert len(p.indices)==16 and p.outside_space==20
 d.approve(p);assert not d.schematic.blocks[3:8].any()


def test_stair_flight_connects_disjoint_floor_spaces_as_a_candidate():
 d=Document(Schematic.empty(12,9,10));d.schematic.palette[90]='minecraft:stone_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]'
 for x,y in ((3,1),(4,2),(5,3),(6,4)):d.schematic.blocks[y,3,x]=90
 a=new_space([[1,3,1,2]],d.schematic.size,'하층');b=new_space([[5,3,7,8]],d.schematic.size,'상층')
 graph=analyze_connections(d,[a,b]);assert any('연속 계단' in e['kind'] and set(e['spaces'])=={a['id'],b['id']} for e in graph['edges'])
