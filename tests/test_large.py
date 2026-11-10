"""Resource/geometry regressions. Real-file verification is tools/verify_schem.py."""
import numpy as np
import pytest
from ship_architect.schematic import Schematic
from ship_architect.model import Document,Bounds
from ship_architect.mesh import build_scene,MeshCache
from ship_architect.chunks import decode_into,encode_batch,Cancelled
from ship_architect.render import render_scene


def test_large_disk_backing_counts_and_cow(tmp_path):
    s=Schematic.empty(256,96,256)
    assert isinstance(s.blocks,np.memmap)
    s.blocks[0]=1
    s.palette[128]='create:shaft[axis=x]'
    s.blocks[1,10,10]=128
    raw=s.to_bytes()
    loaded=Schematic.from_bytes(raw)
    assert isinstance(loaded.blocks,np.memmap)
    d=Document(loaded,raw)
    stats=d.analyze(d.selection)
    assert stats['volume']==6_291_456
    assert stats['solid']==256*256+1
    assert stats['empty']==6_291_456-256*256-1
    b=Bounds((9,1,9),(11,1,11))
    p=d.propose(b,2,'fill')
    assert p.protected==1 and len(p.indices)==8
    preview=d.preview(p)
    assert isinstance(preview,np.memmap)
    assert loaded.blocks[1,9,9]==0 and preview[1,9,9]==2
    del preview
    assert loaded.blocks[1,9,9]==0
    d.approve(p)
    assert loaded.blocks[1,9,9]==2 and loaded.blocks[1,10,10]==128
    d.undo();assert loaded.blocks[1,9,9]==0
    d.redo();assert loaded.blocks[1,9,9]==2
    path=tmp_path/'large.shipproj'
    d.save_project(path)
    restored=Document.open_project(path)
    assert restored.schematic.blocks[1,9,9]==2
    restored.undo();assert restored.schematic.blocks[1,9,9]==0


def test_vector_decode_crosses_batch_and_multibyte_boundaries():
    values=np.resize(np.array([0,128,16384,127,0x7fffffff],np.int32),180_001)
    raw=np.frombuffer(encode_batch(values),dtype=np.int8)
    output=np.empty(values.shape,np.int32)
    counts=decode_into(raw,output,{int(i):str(i) for i in np.unique(values)})
    assert np.array_equal(values,output) and sum(counts.values())==len(values)


def area(scene):
    return sum(float(np.linalg.norm(np.cross(q[1]-q[0],q[3]-q[0]))) for q,n,i in scene)


def test_cross_chunk_halo_removes_internal_faces():
    s=Schematic.empty(64,32,32);s.blocks[:]=1
    scene=build_scene(s)
    assert len(scene.chunks)==2
    assert area(scene)==2*(64*32+64*32+32*32)
    assert not any(n[0]!=0 and np.all(q[:,0]==32) for q,n,i in scene)


def test_layer_cache_changes_boundary_faces_correctly():
    s=Schematic.empty(32,96,32);s.blocks[:]=1
    cache=MeshCache()
    first=build_scene(s,bounds=Bounds((0,0,0),(31,63,31)),cache=cache)
    second=build_scene(s,bounds=Bounds((0,0,0),(31,79,31)),cache=cache)
    assert second.stats['cache_hits']>=1
    assert not any(n[1]!=0 and np.all(q[:,1]==64) for q,n,i in second)
    assert area(second)==2*(32*32+32*80+32*80)
    assert max(float(q[:,1].max()) for q,n,i in first)==64


def test_mesh_cancel_and_cache_budget():
    s=Schematic.empty(32,32,32);s.blocks[0]=1
    with pytest.raises(Cancelled):build_scene(s,cancel=lambda:True)
    cache=MeshCache(budget=1)
    build_scene(s,cache=cache)
    assert cache.used<=1


def test_render_chunk_frustum_and_backface_culling():
    s=Schematic.empty(96,2,2)
    s.blocks[:, :, 0:2]=1;s.blocks[:,:,94:96]=1
    scene=build_scene(s)
    colors={(1,(sign if axis==0 else 0,sign if axis==1 else 0,sign if axis==2 else 0)):(200,200,200)
            for axis in range(3) for sign in (-1,1)}
    rgba,picks,stats=render_scene(scene,colors,np.array([0,0,0]),np.array([[1,0],[0,-1],[0,0]]),
        np.array([10,30]),5.,np.array([0,0,1]),80,60)
    assert stats['chunks']==1 and stats['faces']>0
    assert np.any(picks>=0)
    with pytest.raises(Cancelled):
        render_scene(scene,colors,np.zeros(3),np.array([[1,0],[0,-1],[0,0]]),np.array([10,30]),5.,np.array([0,0,1]),80,60,cancel=lambda:True)


def test_compiled_depth_and_pick_buffers_match_numpy_reference():
    from ship_architect.fast_raster import buffers,raster_batch
    from ship_architect.views import rasterize_quads
    screens=np.array([[[1,1],[10,2],[11,10],[2,9]],[[3,2],[9,2],[9,9],[3,9]]],np.float64)
    depths=np.array([[1,2,3,2],[8,8,8,8]],np.float64)
    colors=np.array([[200,0,0],[0,220,0]],np.uint8)
    face_ids=np.array([4,9],np.int32)
    rgba,picks,zbuffer=buffers(14,13)
    raster_batch(screens,depths,colors,face_ids,rgba,picks,zbuffer)
    ref,refp=rasterize_quads([(screens[i],depths[i],tuple(colors[i]),int(face_ids[i])) for i in range(2)],14,13)
    assert np.array_equal(rgba,ref) and np.array_equal(picks,refp)
