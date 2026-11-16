from collections import Counter

import numpy as np
from PySide6.QtCore import Qt

from ship_architect.analysis_sessions import block_signature
from ship_architect.explorer_properties import apply_fields
from ship_architect.render_settings import backend, validated
from ship_architect.space_geometry import boundary_quads
from ship_architect.space_tiles import ByteCache, build_tiles, in_view, screen_boxes
from test_ai_gui import ready, wait_for, window
from test_v09310_precision import scene_document


def _unit_faces(quads, normals):
    result=Counter()
    for q,n in zip(quads,normals):
        axis=int(np.flatnonzero(n)[0])
        u,v=[d for d in range(3) if d!=axis]
        lo,hi=q.min(axis=0).astype(int),q.max(axis=0).astype(int)
        for a in range(lo[u],hi[u]):
            for b in range(lo[v],hi[v]):
                result[axis,int(n[axis]),lo[axis],a,b]+=1
    return result


def test_halo_tiles_preserve_exact_surfaces_across_xyz_boundaries():
    runs=[[y,z,12,20] for y in range(13,21) for z in range(13,21) if (y,z)!=(15,15)]
    runs += [[18,22,17,18],[19,22,18,19]]
    old=_unit_faces(*boundary_quads(runs))
    tiles,_,_=build_tiles(runs,((0,0,0),(30,30,30),None),ByteCache(1_000_000,256))
    measured=Counter()
    for tile in tiles:
        measured.update(_unit_faces(tile.quads,tile.normals))
    assert measured==old and max(measured.values())==1 and len(tiles)>1


def test_tile_cache_reuses_only_unchanged_geometry_and_has_hard_limit():
    cache=ByteCache(2**20,256)
    shape=[[y,z,0,40] for y in range(6) for z in range(4)]
    bounds=((0,0,0),(50,8,8),None)
    build_tiles(shape,bounds,cache)
    build_tiles(shape,bounds,cache)
    assert cache.hits==3
    build_tiles(shape+[[0,1,41,42]],bounds,cache)
    assert cache.hits==5
    cache.limit=1
    cache.trim()
    assert cache.bytes==0


def test_layer_clipping_generates_no_spurious_halo_faces():
    runs=[[y,z,14,18] for y in range(20) for z in range(2)]
    clip=((15,15,0),(17,17,3),frozenset({15,17}))
    tiles,_,_=build_tiles(runs,clip,ByteCache(1_000_000,64))
    mesh=Counter()
    for tile in tiles:
        mesh.update(_unit_faces(tile.quads,tile.normals))
    assert mesh==_unit_faces(*boundary_quads([[y,z,15,17] for y in (15,17) for z in range(2)]))


def test_viewport_projection_rejects_offscreen_before_budget():
    class View:
        basis=np.eye(3)[:,:2]
        scale=1
        def project(self,q):return q[:,:2]
        def width(self):return 40
        def height(self):return 20
    v=View()
    lo,hi=screen_boxes(v,[[-4,2,1],[50,0,1],[39,15,0]],[[3,5,4],[60,6,9],[50,25,2]])
    assert in_view(v,lo,hi).tolist()==[True,False,True]


def test_graphics_settings_clamp_resources_and_report_real_backend():
    cfg=validated({'quality':[],'renderer':'opengl','fps':999,'faces':0,'cache_mib':999})
    assert cfg['fps']==144 and cfg['faces']==1000 and cfg['cache_mib']==96
    assert 'QPainter + CPU' in backend(cfg)
    assert '호환' in backend(cfg)


def test_presets_keep_selection_metadata_and_restore_visibility(window):
    w=window
    doc, spaces, _ = scene_document()
    w.set_document(doc)
    wait_for(lambda:ready(w))
    b=w.workbench
    keys={'space:'+entry['id'] for entry in spaces}
    for key in keys:b.nodes[key].setSelected(True)
    before=b.presets.snapshot()
    payload=block_signature(doc),doc.schematic.metadata_digest(),doc.spaces,doc.selection,doc.revision
    for name in ('exterior','interior','equipment','movement','review'):
        b.presets.set(name)
        assert keys==b.keys_for_scope('selected')
    b.presets.restore_previous()
    assert b.presets.snapshot()==before
    assert payload==(block_signature(doc),doc.schematic.metadata_digest(),doc.spaces,doc.selection,doc.revision)


def test_favorite_and_batch_edit_preserve_ids_and_undo(window,tmp_path):
    w=window
    d, spaces, _ = scene_document()
    w.set_document(d)
    wait_for(lambda:ready(w))
    b=w.workbench
    key='space:'+spaces[0]['id']
    b.toggle_favorite(key)
    b.kind_filter.setCurrentIndex(b.kind_filter.findData('favorites'))
    assert b.keys_for_scope('visible_tree')=={key}
    b.kind_filter.setCurrentIndex(0)
    ids={x['id'] for x in spaces}
    changed=apply_fields(d.spaces,ids,{'purpose':'검토용','opacity':115,'color':'#44aa99'})
    w.space_manager.changed(changed,'space_batch_attributes',d.active_space_id)
    assert [r['runs'] for r in d.spaces]==[s['runs'] for s in spaces]
    assert all(x['purpose']=='검토용' for x in d.spaces)
    w.undo()
    assert d.spaces==list(spaces)
    b.save_display()
    target=tmp_path/'settings.shipproj'
    d.save_project(target)
    assert key in type(d).open_project(target).view_settings['favorites']
