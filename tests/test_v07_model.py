import json
from dataclasses import replace
import numpy as np
import nbtlib as nbt
import pytest
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.safety import audit,review,require_safe
from ship_architect.ai_design import build_context,compile_plan,generate_design,PlanError
from ship_architect.block_registry import BlockRegistry,request_bindings
from test_ai_design import plan,operation,server


def test_zone_undo_redo_is_ordered_and_keeps_blocks(tmp_path):
 d=Document(Schematic.empty(12,10,12));b=Bounds((2,1,2),(7,5,7));d.approve(d.propose(b,1,'floor'));state=d.schematic.blocks.copy()
 d.add_zone('reserve',b,True);d.undo();assert not d.zones and np.array_equal(d.schematic.blocks,state)
 d.undo();assert not np.any(d.schematic.blocks)
 d.redo();assert np.array_equal(d.schematic.blocks,state)
 d.redo();assert d.zones[0]['protected']
 d.delete_zone(0);d.undo();assert d.zones[0]['protected'];d.redo();assert not d.zones
 p=tmp_path/'history.shipproj';d.save_project(p);r=Document.open_project(p);r.undo();assert r.zones[0]['protected']


@pytest.mark.parametrize('wrong_write',[False,True])
def test_approval_failure_restores_exact_data_and_no_history(monkeypatch,wrong_write):
 d=Document(Schematic.empty(8,6,8));p=d.propose(d.selection,1,'floor');before=d.schematic.blocks.copy();original=d.original;revision=d.revision
 def broken(array,ii,desired):
  array[ii[:5]]=desired[:5]
  if not wrong_write:raise OSError('injected disk/write failure')
 monkeypatch.setattr(d,'_write_blocks',broken)
 with pytest.raises((OSError,ValueError)):d.approve(p)
 assert np.array_equal(d.schematic.blocks,before) and d.revision==revision and not d.undo_stack and d.original==original


def test_current_selection_is_revalidated_at_commit():
 d=Document(Schematic.empty(12,10,12));p=d.propose(Bounds((1,1,1),(9,4,9)),1,'floor');d.selection=Bounds((2,1,2),(3,4,3))
 with pytest.raises(ValueError,match='선택 구역 밖'):d.approve(p)
 assert not np.any(d.schematic.blocks)


def test_new_block_entity_still_blocks_even_after_zone_undo():
 d=Document(Schematic.empty(8,6,8));p=d.propose(d.selection,1,'floor');d.approve(p);d.add_zone('lock',d.selection,True);d.undo()
 d.schematic.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_normal'),'Pos':nbt.IntArray([0,0,0])}))
 with pytest.raises(ValueError,match='BlockEntity'):d.undo()
 assert d.schematic.blocks[0,0,0]==1


def test_offset_ship_enclosure_detected_not_only_document_faces():
 size=(100,50,100);y,z,x=np.indices((30,60,60));mask=(x==0)|(x==59)|(z==0)|(z==59)|(y==0)|(y==29)
 yy,zz,xx=np.nonzero(mask);ii=((yy+10)*100+(zz+20))*100+xx+20
 c=audit(ii,size_xyz=size,bounds_lo=(0,0,0),bounds_hi=(99,49,99),reference_bounds={'lo':[23,12,23],'hi':[75,36,75]},origin='ai')
 assert not c['document_faces'] and c['hull_risk']
 with pytest.raises(ValueError):require_safe(c,approve_mass=True)


def test_destructive_manual_mass_requires_separate_confirmation():
 size=(40,12,40);ii=np.arange(2100,dtype=np.int64);c=audit(ii,size_xyz=size,bounds_lo=(0,0,0),bounds_hi=(39,11,39),origin='manual',before=np.ones(2100,np.int32),after=np.zeros(2100,np.int32),palette={0:'minecraft:air',1:'minecraft:stone'})
 assert c['removed']==2100 and c['requires_confirmation']
 with pytest.raises(ValueError):require_safe(c)
 require_safe(c,approve_mass=True)


@pytest.mark.parametrize('name',['석재블럭','석재 블록','돌 블럭'])
def test_diagnostic_alias_regressions(name):assert BlockRegistry().resolve(name)=='minecraft:stone'


def test_random_blocks_are_instruction_not_nonexistent_material():
 b,s=request_bindings('선택구역 랜덤블럭으로 채워줘',BlockRegistry());assert not b and not s


def test_fill_request_cannot_be_silently_turned_into_shell(server):
 client,h=server;d=Document(Schematic.empty(6,5,6));h.responses=[json.dumps(plan(operation(material='minecraft:stone',hi=(5,4,5))))]*2
 with pytest.raises(PlanError,match='채우기 요청'):generate_design(client,'qwen3:4b',d,d.selection,'선택구역 돌로 채워줘')
 assert not np.any(d.schematic.blocks)


def test_fill_reports_exact_zero_when_protection_excludes_all(server):
 client,h=server;d=Document(Schematic.empty(6,5,6));d.add_zone('all',d.selection,True)
 h.responses=[json.dumps(plan(operation('fill','minecraft:stone',hi=(5,4,5))))]
 result=generate_design(client,'qwen3:4b',d,d.selection,'선택구역 돌로 채워줘')
 assert result['actual']['changed']==0 and result['proposal'].protected==180 and not np.any(d.schematic.blocks)


def test_old_project_zone_addition_is_safely_migrated(tmp_path):
 import zipfile,io
 d=Document(Schematic.empty(8,6,8));d.approve(d.propose(d.selection,1,'floor'));d.add_zone('old lock',d.selection,True)
 path=tmp_path/'new.shipproj';d.save_project(path)
 with zipfile.ZipFile(path) as z:files={n:z.read(n) for n in z.namelist()}
 payload=json.loads(files['project.json']);payload['version']=5;payload['undo']=[r for r in payload['undo'] if r.get('command')!='zone'];files['project.json']=json.dumps(payload).encode()
 old=tmp_path/'old.shipproj'
 with zipfile.ZipFile(old,'w') as z:
  for key,value in files.items():z.writestr(key,value)
 r=Document.open_project(old);r.undo();assert not r.zones and np.any(r.schematic.blocks)
 r.undo();assert not np.any(r.schematic.blocks)


@pytest.mark.parametrize('role_bindings',[None,{'floor':'minecraft:stone','walls':'minecraft:stone'}])
def test_fill_material_is_pinned_even_with_old_role_bindings(server,role_bindings):
 client,h=server;d=Document(Schematic.empty(6,5,6));h.responses=[json.dumps(plan(operation('fill','minecraft:white_concrete',hi=(5,4,5))))]*2
 with pytest.raises(PlanError,match='요청 재료 불일치'):generate_design(client,'qwen3:4b',d,d.selection,'선택구역 돌로 채워줘',bindings=role_bindings)
 assert not np.any(d.schematic.blocks)
