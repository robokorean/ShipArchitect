import copy,json
from collections import Counter
import numpy as np
import nbtlib as nbt
import pytest
from ship_architect.block_registry import BlockRegistry,MaterialError,request_bindings,vanilla
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.ai_design import material_context,compile_plan,generate_design,PlanError
from ship_architect.mesh import build_scene
from test_ai_design import operation,plan,server


@pytest.mark.parametrize('name,expected',[
 ('참나무 판자','minecraft:oak_planks'),('oak planks','minecraft:oak_planks'),('oak_planks','minecraft:oak_planks'),
 ('돌 벽돌','minecraft:stone_bricks'),('석재 벽돌','minecraft:stone_bricks'),('유리','minecraft:glass'),
 ('minecraft:oak_log[axis=x]','minecraft:oak_log[axis=x]'),('참나무 원목','minecraft:oak_log[axis=y]')])
def test_official_names_and_states(name,expected):assert BlockRegistry().resolve(name)==expected


@pytest.mark.parametrize('value',['minecraft:unobtainium','이름모를 광석','minecraft:glass[waterlogged=true]',
 'minecraft:oak_log[axis=up]','minecraft:oak_log[axis=x,axis=y]','missing:panel[facing=north]'])
def test_no_fallback_on_unknown_or_bad_state(value):
 with pytest.raises(MaterialError):BlockRegistry().resolve(value)


def test_official_registry_and_exact_existing_state_preserved():
 assert len(vanilla())==1060
 state='test:panel[waterlogged=false,facing=north]'
 r=BlockRegistry([state]);assert r.resolve(state)==state
 with pytest.raises(MaterialError):r.resolve('test:panel[facing=south,waterlogged=false]')
 assert '미확인' in r.provenance(state)
 existing='minecraft:oak_log[axis=x]';assert BlockRegistry([existing]).resolve('oak_log[axis=x]')==existing


def test_korean_roles_and_longest_alias_no_cross_binding():
 roles,states=request_bindings('바닥은 참나무 판자, 벽은 석재 벽돌, 천장은 유리, 기둥은 참나무 원목으로 만들어 줘',BlockRegistry())
 assert roles==dict(floor='minecraft:oak_planks',walls='minecraft:stone_bricks',ceiling='minecraft:glass',column='minecraft:oak_log[axis=y]')
 assert request_bindings('흰색 바닥과 유리 천장',BlockRegistry())[0]=={'ceiling':'minecraft:glass'}
 assert request_bindings('참나무 판자 바닥과 석재 벽돌 벽',BlockRegistry())[0]=={'floor':'minecraft:oak_planks','walls':'minecraft:stone_bricks'}


@pytest.mark.parametrize('state',['minecraft:oak_planks','minecraft:stone_bricks','minecraft:glass'])
def test_json_engine_preview_mesh_and_export_identity(tmp_path,state):
 d=Document(Schematic.empty(7,5,7));b=Bounds((1,1,1),(4,3,4));ctx=material_context(d,b,state+'로 바닥을 만들어 줘')
 # Values above 127 exercise varint palette indices; never assume global numeric IDs.
 old=next(i for i,s in d.schematic.palette.items() if s==state);d.schematic.palette[517]=d.schematic.palette.pop(old);d.schematic.blocks[d.schematic.blocks==old]=517
 p=compile_plan(d,b,plan(operation(material=state,hi=(3,2,3))),ctx,d.revision)
 assert set(p.after)=={517} and not np.any(d.schematic.blocks)
 view=d.preview(p);scene=build_scene(d.schematic,blocks=view)
 assert {d.schematic.palette[int(i)] for c in scene.chunks for i in c.materials}=={state}
 d.approve(p);out=tmp_path/'identity.schem';d.export(out,approved=True);sc,_=load_file(out)
 assert Counter(sc.palette[int(i)] for i in sc.blocks.ravel())[state]==16
 assert np.all(sc.blocks[1,1:5,1:5]==517)
 d.undo();assert not np.any(d.schematic.blocks)
 d.redo();assert np.array_equal(d.schematic.blocks,sc.blocks)


def test_wrong_stone_response_is_rejected_not_approved(server):
 client,h=server;d=Document(Schematic.empty(6,5,6))
 wrong=json.dumps(plan(operation(material='minecraft:stone',hi=(5,4,5))))
 h.responses=[wrong,wrong]
 with pytest.raises(PlanError,match='요청 재료 불일치'):generate_design(client,'qwen3:4b',d,d.selection,'minecraft:oak_planks로 바닥을 만들어 줘')
 assert len(h.requests)==2 and not np.any(d.schematic.blocks) and not d.undo_stack


def test_korean_ai_output_is_normalized_and_new_requested_material_registered(server):
 client,h=server;d=Document(Schematic.empty(6,5,6));h.responses=[json.dumps(plan(operation(material='석재 벽돌',hi=(5,4,5))))]
 r=generate_design(client,'qwen3:4b',d,d.selection,'석재 벽돌로 바닥을 만들어 줘')
 assert r['plan']['operations'][0]['material']=='minecraft:stone_bricks'
 assert {d.schematic.palette[int(i)] for i in r['proposal'].after}=={'minecraft:stone_bricks'}
 assert not np.any(d.schematic.blocks)


def test_mixed_roles_and_column_roundtrip(tmp_path):
 d=Document(Schematic.empty(10,9,10));b=Bounds((1,1,1),(8,7,8));prompt='바닥은 참나무 판자, 벽은 석재 벽돌, 천장은 유리, 기둥은 참나무 원목'
 ctx=material_context(d,b,prompt)
 p=plan(operation(material='minecraft:oak_planks',hi=(7,6,7)),operation('walls','minecraft:stone_bricks',hi=(7,6,7)),operation('ceiling','minecraft:glass',hi=(7,6,7)),operation('column','minecraft:oak_log[axis=y]',lo=(3,1,3),hi=(3,5,3)))
 compiled=compile_plan(d,b,p,ctx,d.revision);assert len(np.unique(compiled.after))==4
 d.approve(compiled);assert d.schematic.palette[int(d.schematic.blocks[4,4,4])]=='minecraft:oak_log[axis=y]'
 saved=tmp_path/'roles.shipproj';d.save_project(saved);restored=Document.open_project(saved)
 assert np.array_equal(restored.schematic.blocks,d.schematic.blocks)
 restored.undo();assert not np.any(restored.schematic.blocks)
 restored.redo();assert np.array_equal(restored.schematic.blocks,d.schematic.blocks)
 wrong=copy.deepcopy(p);wrong['operations'][1]['material']='minecraft:stone'
 with pytest.raises(PlanError,match='불일치'):compile_plan(d,b,wrong,ctx,d.revision)


def test_mod_registry_validation_exact_state_and_nbt_protection(tmp_path):
 d=Document(Schematic.empty(7,6,7));data={'minecraft_version':'1.21.1','blocks':{
 'decor:panel':{'properties':{'axis':['x','y','z']},'default':{'axis':'y'},'safe_full_cube':True,'has_block_entity':False,'functional':False},
 'computercraft:computer_normal':{'properties':{},'default':{},'safe_full_cube':True,'has_block_entity':False,'functional':False}}}
 d.import_mod_registry(data);state='decor:panel[axis=x]';i=d.add_material(state)
 with pytest.raises(ValueError):d.add_material('computercraft:computer_normal')
 with pytest.raises(ValueError):d.add_material('decor:panel[axis=west]')
 b=Bounds((1,1,1),(5,4,5));ctx=material_context(d,b,'바닥은 '+state)
 p=compile_plan(d,b,plan(operation(material=state,hi=(4,3,4))),ctx,d.revision);d.approve(p)
 d.undo();d.redo();assert d.schematic.palette[int(d.schematic.blocks[1,1,1])]==state
 path=tmp_path/'mod.shipproj';d.save_project(path);reopened=Document.open_project(path);reopened.undo();reopened.redo()
 assert np.array_equal(reopened.schematic.blocks,d.schematic.blocks)
 # A block entity reservation always wins over even an explicitly classified decoration.
 d.schematic.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('decor:controller'),'Pos':nbt.IntArray([1,1,1]),'Data':nbt.Int(42)}))
 with pytest.raises(ValueError,match='BlockEntity'):d.undo()
 out=tmp_path/'mod.schem';d.export(out,approved=True);loaded,_=load_file(out)
 assert loaded.palette[int(loaded.blocks[1,1,1])]==state and int(loaded.root['Blocks']['BlockEntities'][0]['Data'])==42


def test_unknown_request_does_not_call_model_or_register_anything(server):
 client,h=server;d=Document(Schematic.empty(6,5,6));palette=dict(d.schematic.palette)
 with pytest.raises(ValueError):generate_design(client,'qwen3:4b',d,d.selection,'바닥은 minecraft:oak_planks, 벽은 minecraft:made_up')
 assert not h.requests and d.schematic.palette==palette and not np.any(d.schematic.blocks)


@pytest.mark.parametrize('prompt',['바닥은 무지개석으로 만들어 줘','무지개석으로 바닥을 만들어 줘','바닥은 없는 재료, 벽은 유리','바닥을 무지개석으로 만들어 줘'])
def test_unknown_korean_material_prompts_do_not_fall_back(prompt,server):
 client,h=server;d=Document(Schematic.empty(6,5,6))
 with pytest.raises(MaterialError):generate_design(client,'qwen3:4b',d,d.selection,prompt)
 assert not h.requests and not np.any(d.schematic.blocks)


def test_refinement_preserves_unmodified_wall_binding():
 p='이전 방의 바닥 재료만 minecraft:gray_concrete로 바꾸고 벽과 유리 천장은 유지한 전체 계획을 다시 생성해 줘.'
 bindings,states=request_bindings(p,BlockRegistry())
 assert bindings.get('floor')=='minecraft:gray_concrete'
 assert 'walls' not in bindings  # do not recolor unchanged walls
 assert 'minecraft:gray_concrete' in states


def test_replace_source_is_not_mistaken_for_destination():
 b,states=request_bindings('바닥의 minecraft:stone을 minecraft:oak_planks로 교체해 줘',BlockRegistry())
 assert b=={'floor':'minecraft:oak_planks'} and states==['minecraft:oak_planks']


def test_generic_new_floor_is_not_an_unknown_material():
 bindings,states=request_bindings('바닥을 다시 새로 만들어 줘',BlockRegistry())
 assert not bindings and not states


def test_invalid_imported_vanilla_state_is_protected_not_reused():
 d=Document(Schematic.empty(4,4,4));d.schematic.palette[99]='minecraft:made_up_wool';d.schematic.blocks[1,1,1]=99
 b=Bounds((1,1,1),(1,1,1));assert d.protection_at((1,1,1))
 assert not len(d.propose(b,0,'erase').indices)
 with pytest.raises(ValueError):d.propose(d.selection,99,'add')


@pytest.mark.parametrize('phrase,state',[('Oak planks','minecraft:oak_planks'),('Stone bricks','minecraft:stone_bricks')])
def test_english_block_names_in_korean_request(phrase,state):
 bindings,states=request_bindings(phrase+'로 바닥을 만들어 줘',BlockRegistry())
 assert bindings=={'floor':state} and states==[state]
