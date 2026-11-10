import io
import gzip
import numpy as np
import nbtlib as nbt
import pytest
from ship_architect.schematic import Schematic,load_file
from ship_architect.model import Document,Bounds,Proposal
from ship_architect.mesh import build_scene,MeshCache
from ship_architect.protection import build_exterior


def imported_box(tmp_path,door=False):
    s=Schematic.empty(12,10,14)
    a=s.blocks
    a[2:8,3:11,2:10]=1
    a[4:6,5:9,4:8]=0
    if door:a[4,3:5,5]=0
    path=tmp_path/'box.schem';path.write_bytes(s.to_bytes())
    loaded,raw=load_file(path)
    return Document(loaded,raw,path)


def test_exterior_distinguishes_sealed_interior_and_open_door(tmp_path):
    d=imported_box(tmp_path);d.prepare_protection()
    # Outermost original blocks are protected, enclosed inner faces are editable.
    assert d.protection_at((2,4,5))
    assert not d.protection_at((3,4,6))  # inner solid wall of a sealed cavity
    assert d.exterior.mask((0,0,0),d.schematic.blocks.shape)[4,5,2]
    assert not d.exterior.mask((0,0,0),d.schematic.blocks.shape)[4,6,3]
    opened=imported_box(tmp_path,door=True);opened.prepare_protection()
    assert opened.exterior.mask((0,0,0),opened.schematic.blocks.shape)[4,6,3]


def test_exterior_requires_explicit_proposal_and_second_approval(tmp_path):
    d=imported_box(tmp_path)
    outer=Bounds((2,4,6),(2,4,6));inner=Bounds((3,4,6),(3,4,6))
    blocked=d.propose(outer,2,'replace',source_id=1)
    assert not len(blocked.indices) and blocked.exterior_blocked==1
    assert len(d.propose(inner,2,'replace',source_id=1).indices)==1
    p=d.propose(outer,2,'replace',source_id=1,allow_exterior=True)
    assert p.exterior_affected==1
    with pytest.raises(ValueError,match='외피'):d.approve(p)
    d.approve(p,approve_exterior=True)
    assert d.schematic.blocks[4,6,2]==2
    d.undo();assert d.schematic.blocks[4,6,2]==1
    d.redo();assert d.schematic.blocks[4,6,2]==2


def test_explicit_add_delete_and_source_filtered_replace():
    d=Document(Schematic.empty(8,6,10))
    d.schematic.blocks[1,1,1:4]=[1,2,1]
    b=Bounds((0,1,0),(4,1,3))
    p=d.propose(b,3,'replace',source_id=1)
    assert len(p.indices)==2 and p.source_id==1
    preview=d.preview(p)
    assert d.schematic.blocks[1,1,1]==1 and preview[1,1,1]==3
    assert preview[1,1,2]==2 and preview[1,0,0]==0
    d.approve(p)
    add=d.propose(b,3,'add',replace=True)
    assert len(add.indices)==b.volume-3
    d.approve(add)
    erase=d.propose(b,1,'erase');d.approve(erase)
    assert not np.any(d.schematic.blocks[b.slices])
    d.undo();assert np.all(d.schematic.blocks[b.slices]!=0)
    d.redo();assert not np.any(d.schematic.blocks[b.slices])


def test_protected_zone_blocks_add_and_legacy_redo():
    d=Document(Schematic.empty(8,6,10));b=Bounds((1,1,1),(3,3,3))
    d.approve(d.propose(b,1,'fill'));d.undo()
    d.add_zone('설비 여유 공간',b,protected=True)
    p=d.propose(b,2,'fill',allow_exterior=True)
    assert not len(p.indices) and p.locked_blocked==b.volume
    assert not d.redo_stack  # adding a zone branches the unified command history
    d.undo();assert not d.zones
    d.redo();assert d.zones[0]['protected']


def test_protection_rechecked_against_forged_proposal_and_new_nbt():
    from dataclasses import replace
    d=Document(Schematic.empty(5,5,5));b=Bounds((1,1,1),(1,1,1))
    p=d.propose(b,1,'add')
    d.schematic.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_normal'),'Pos':nbt.IntArray([1,1,1])}))
    with pytest.raises(ValueError,match='BlockEntity'):d.approve(p)
    assert d.schematic.blocks[1,1,1]==0
    with pytest.raises(ValueError,match='중복'):
        d.approve(replace(p,indices=np.repeat(p.indices,2),before=np.repeat(p.before,2)))


def test_catalogue_material_export_and_project_reopen(tmp_path):
    d=Document(Schematic.empty(8,6,10))
    target=d.add_material('minecraft:blue_concrete')
    assert d.add_material('minecraft:blue_concrete')==target
    with pytest.raises(ValueError):d.add_material('computercraft:computer_normal')
    d.approve(d.propose(Bounds((1,1,1),(2,2,2)),target,'add'))
    path=tmp_path/'design.schem';report=d.export(path,approved=True)
    assert report['nbt_verified'] and report['sha256']
    sc,_=load_file(path);assert sc.palette[target]=='minecraft:blue_concrete' and sc.blocks[1,1,1]==target
    d.add_zone('protected',Bounds((0,0,0),(0,1,0)),protected=True)
    project=tmp_path/'design.shipproj';d.save_project(project)
    restored=Document.open_project(project)
    assert restored.zones[0]['protected']
    restored.undo();assert not restored.zones  # latest command is zone creation
    restored.undo();assert restored.schematic.blocks[1,1,1]==0
    restored.redo();assert restored.schematic.blocks[1,1,1]==target
    restored.redo();assert restored.zones[0]['protected']


def test_incremental_cache_invalidates_modified_chunk_and_halo_only():
    s=Schematic.empty(128,32,64);s.blocks[0]=1
    cache=MeshCache();initial=build_scene(s,cache=cache)
    # One changed voxel at the border: includes the neighbor to remove shared faces.
    index=np.array([31],np.int64)
    next_cache=cache.fork_invalidated(index,s.size)
    s.blocks[0,0,31]=2
    scene=build_scene(s,cache=next_cache)
    assert 0<scene.stats['cache_hits']<initial.stats['chunks']
    clean=build_scene(s)
    def signatures(mesh):
        return sorted((tuple(q.reshape(-1)),tuple(n),i) for q,n,i in mesh)
    assert signatures(scene)==signatures(clean)


def test_floor_ceiling_walls_and_erase_preserve_block_entity_nbt(tmp_path):
    s=Schematic.empty(12,10,14)
    s.palette[128]='computercraft:computer_advanced[facing=north]'
    s.blocks[2,3,2]=128
    entity=nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([2,2,3]),'Data':nbt.Compound({'ComputerId':nbt.Int(15),'Payload':nbt.ByteArray([-128,0,127])})})
    s.root['Blocks']['BlockEntities'].append(entity)
    d=Document(s);b=Bounds((1,1,1),(10,8,12))
    for mode in ('floor','ceiling','walls'):
        d.approve(d.propose(b,1,mode))
    d.approve(d.propose(b,2,'replace',source_id=1))
    d.approve(d.propose(b,1,'erase'))
    assert d.schematic.blocks[2,3,2]==128
    out=tmp_path/'protected.schem';d.export(out,approved=True)
    check=nbt.load(out)
    assert check['Schematic']['Blocks']['BlockEntities'][0]==entity


def test_undo_cannot_remove_a_newly_reserved_block_entity():
    d=Document(Schematic.empty(5,5,5));b=Bounds((1,1,1),(1,1,1))
    d.approve(d.propose(b,1,'add'))
    d.schematic.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('test:reserved'),'Pos':nbt.IntArray([1,1,1])}))
    with pytest.raises(ValueError,match='BlockEntity'):d.undo()
    assert d.schematic.blocks[1,1,1]==1 and len(d.undo_stack)==1
