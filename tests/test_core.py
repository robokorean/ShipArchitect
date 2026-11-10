import gzip
import os
import io
import hashlib
import json
import zipfile
from pathlib import Path
import numpy as np
import nbtlib as nbt
import pytest
from ship_architect.schematic import Schematic, decode_varints, encode_varints, load_file
from ship_architect.model import Document, Bounds
from ship_architect.views import surface_mesh


@pytest.fixture
def doc():
    return Document(Schematic.empty(8, 6, 10))


def test_varint_signed_bytes_and_multibyte():
    a = np.array([0, 1, 127, 128, 255, 16384, 2**28, 2**31-1], dtype=np.int32)
    assert np.array_equal(decode_varints(encode_varints(a), len(a)), a)


@pytest.mark.parametrize('raw,count', [(b'\x80', 1), (b'\x80'*5, 1), (b'\x01\x01', 1), (b'', 1), (b'\xff\xff\xff\xff\x08', 1)])
def test_bad_varint(raw, count):
    with pytest.raises(ValueError):
        decode_varints(raw, count)


@pytest.mark.parametrize('version,wrapped', [(2, False), (2, True), (3, False), (3, True)])
def test_schematic_roundtrip_preserves_nbt(version, wrapped):
    s = Schematic.empty(7, 3, 11)
    s.blocks[2, 9, 5] = 3
    s.palette[128] = 'create:shaft[axis=x]'
    s.blocks[1, 4, 2] = 128
    s.root['Mystery'] = nbt.Compound({'nested': nbt.List[nbt.Long]([2**40]), 'blob': nbt.ByteArray([-128, 127])})
    entity = nbt.Compound({'Id': nbt.String('create:shaft'), 'Pos': nbt.IntArray([2, 1, 4]),
                           'Data': nbt.Compound({'Speed': nbt.Float(128.5)})})
    s.root['Blocks']['BlockEntities'].append(entity)
    s.root['Entities'] = nbt.List[nbt.Compound]([nbt.Compound({'Id': nbt.String('minecraft:pig'),
        'Pos': nbt.List[nbt.Double]([1., 2., 3.])})])
    s.root['Biomes'] = nbt.Compound({'Palette': nbt.Compound({'minecraft:plains': nbt.Int(0)}), 'Data': nbt.ByteArray([0])})
    s.root['Offset'] = nbt.IntArray([-100, 22, 50])
    if version == 2:
        s.root['Version'] = nbt.Int(2)
        s.version = 2
        b = s.root.pop('Blocks')
        s.root['Palette'], s.root['BlockData'] = b['Palette'], b['Data']
        s.root['BlockEntities'] = b['BlockEntities']
    if not wrapped:
        s.tree = nbt.File(s.root, root_name='Schematic')
        s.root = s.tree
    raw = s.to_bytes()
    loaded = Schematic.from_bytes(raw)
    assert loaded.size == (7, 3, 11)
    assert np.array_equal(loaded.blocks, s.blocks)
    assert loaded.palette == s.palette
    for key in ['Mystery', 'Entities', 'Biomes', 'Offset']:
        assert loaded.root[key] == s.root[key]
    container = loaded.root['Blocks'] if version == 3 else loaded.root
    assert container['BlockEntities'][0] == entity
    assert loaded.protected()[1, 4, 2]
    # Raw, uncompressed NBT is also accepted for imports.
    assert np.array_equal(Schematic.from_bytes(gzip.decompress(raw)).blocks, s.blocks)


def test_dimension_limits():
    with pytest.raises(ValueError):
        Schematic.empty(1000, 1000, 1000)
    with pytest.raises(ValueError):
        Schematic.empty(0, 3, 5)


def test_unknown_palette_rejected():
    s = Schematic.empty(2, 2, 2)
    s.blocks[0, 0, 0] = 900
    with pytest.raises(ValueError, match='팔레트'):
        Schematic.from_bytes(s.to_bytes())


def test_import_exact_backup(tmp_path):
    raw = Schematic.empty(3, 2, 5).to_bytes()
    p = tmp_path/'source.schem'
    p.write_bytes(raw)
    _, original = load_file(p, tmp_path/'backups')
    assert original == raw == p.read_bytes()
    assert (tmp_path/'backups'/(hashlib.sha256(raw).hexdigest()+'.schem')).read_bytes() == raw


def test_inclusive_and_reversed_bounds(doc):
    b = Bounds.between((6, 4, 8), (2, 1, 3))
    assert b.volume == 5*4*6
    assert doc.analyze(b)['empty'] == b.volume
    with pytest.raises(ValueError):
        Bounds((-1, 0, 0), (2, 1, 1)).validate(doc.schematic.size)


@pytest.mark.parametrize('mode,count', [('floor', 80), ('ceiling', 80), ('walls', (2*8+2*10-4)*6), ('fill', 480)])
def test_geometry_shapes(doc, mode, count):
    p = doc.propose(doc.selection, 1, mode)
    assert len(p.indices) == count
    assert np.count_nonzero(doc.schematic.blocks) == 0
    assert np.count_nonzero(doc.preview(p)) == count
    doc.approve(p)
    assert np.count_nonzero(doc.schematic.blocks) == count
    assert doc.history[-1]['action'] == 'approve'


def test_collisions_and_protection(doc):
    s = doc.schematic
    s.palette[9] = 'computercraft:computer_advanced[facing=north]'
    s.blocks[0, 0, 0] = 9
    s.blocks[0, 0, 1] = 2
    s.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id': nbt.String('minecraft:chest'), 'Pos': nbt.IntArray([2, 0, 0])}))
    p = doc.propose(doc.selection, 1, 'floor')
    assert p.protected == 2 and p.occupied == 1 and len(p.indices) == 77
    doc.approve(p)
    assert s.blocks[0, 0, 0] == 9 and s.blocks[0, 0, 1] == 2 and s.blocks[0, 0, 2] == 0
    p = doc.propose(doc.selection, 3, 'floor', True)
    assert p.protected == 2
    doc.approve(p)
    assert s.blocks[0, 0, 0] == 9 and s.blocks[0, 0, 1] == 3
    assert s.blocks[0, 0, 2] == 0
    with pytest.raises(ValueError):
        doc.propose(doc.selection, 9)


def test_erase_protects_mod_and_devices(doc):
    doc.schematic.blocks[:] = 1
    doc.schematic.palette[9] = 'create:shaft[axis=x]'
    doc.schematic.blocks[0, 0, 0] = 9
    p = doc.propose(doc.selection, 9, 'erase')
    assert p.target == 0 and p.protected == 1
    doc.approve(p)
    assert np.count_nonzero(doc.schematic.blocks) == 1


def test_undo_redo_and_stale_proposal(doc):
    p = doc.propose(doc.selection, 1)
    doc.approve(p)
    with pytest.raises(ValueError):
        doc.approve(p)
    doc.undo()
    assert not np.any(doc.schematic.blocks)
    doc.redo()
    assert np.count_nonzero(doc.schematic.blocks) == 80
    doc.undo()
    doc.approve(doc.propose(doc.selection, 2))
    assert not doc.redo_stack


def test_portable_project_zones_history_and_original(doc, tmp_path):
    original = doc.original
    doc.add_zone('격납고', Bounds((1, 0, 2), (6, 4, 8)))
    doc.selection = Bounds((1, 1, 2), (6, 3, 8))
    doc.approve(doc.propose(doc.selection, 1))
    path = tmp_path/'설계.shipproj'
    doc.save_project(path)
    loaded = Document.open_project(path)
    assert loaded.zones == doc.zones
    assert loaded.history == doc.history
    assert loaded.original == original
    assert loaded.selection == doc.selection
    assert np.array_equal(loaded.schematic.blocks, doc.schematic.blocks)
    loaded.undo()
    assert not np.any(loaded.schematic.blocks)
    loaded.redo()
    loaded.save_project(path)
    assert Path(str(path)+'.bak').exists()


def test_export_approval_original_guard_and_reload(doc, tmp_path):
    source = tmp_path/'source.schem'
    source.write_bytes(doc.original)
    doc.source = str(source)
    export = tmp_path/'approved.schem'
    with pytest.raises(ValueError):
        doc.export(export)
    with pytest.raises(ValueError):
        doc.export(source, approved=True)
    doc.approve(doc.propose(doc.selection, 1))
    doc.export(export, approved=True)
    assert np.array_equal(load_file(export)[0].blocks, doc.schematic.blocks)
    assert source.read_bytes() == doc.original
    if os.name != 'nt':  # Creating symlinks on Windows may require developer mode.
        alias = tmp_path/'alias.schem'
        alias.symlink_to(source)
        with pytest.raises(ValueError):
            doc.export(alias, approved=True)


def test_project_hash_tamper(doc, tmp_path):
    path = tmp_path/'p.shipproj'
    doc.save_project(path)
    with zipfile.ZipFile(path) as z:
        entries = {name:z.read(name) for name in z.namelist()}
    entries['original.schem'] += b'bad'
    with zipfile.ZipFile(path, 'w') as z:
        for name, raw in entries.items():
            z.writestr(name, raw)
    with pytest.raises(ValueError, match='해시'):
        Document.open_project(path)


def test_solid_cuboid_greedy_surface():
    blocks = np.ones((16, 64, 40), dtype=np.int32)
    mesh = surface_mesh(blocks, np.ones_like(blocks, dtype=bool))
    assert len(mesh) == 6
    assert set(tuple(n) for _, n, _ in mesh) == {(-1.,0.,0.),(1.,0.,0.),(0.,-1.,0.),(0.,1.,0.),(0.,0.,-1.),(0.,0.,1.)}


def test_mesh_hole_and_limit():
    a = np.ones((3, 3, 3), dtype=np.int32)
    a[1, 1, 1] = 0
    assert len(surface_mesh(a, a > 0)) == 12
    with pytest.raises(ValueError, match='60,000'):
        surface_mesh(a, a > 0, limit=3)


def test_depth_buffer_occlusion_and_face_ids_are_order_independent():
    from ship_architect.views import rasterize_quads
    square = np.array([[1,1],[9,1],[9,9],[1,9]], dtype=float)
    front = (square, np.array([5.,5.,5.,5.]), (255,0,0), 7)
    back = (square, np.array([1.,1.,1.,1.]), (0,255,0), 9)
    # Draw the far face after the near face. Sorting or draw order must not matter.
    rgba, picks = rasterize_quads([front, back], 12, 12)
    assert tuple(rgba[5,5]) == (255,0,0,255)
    assert picks[5,5] == 7 and picks[0,0] == -1
    other, ids = rasterize_quads([back, front], 12, 12)
    assert np.array_equal(rgba, other) and np.array_equal(picks, ids)


def test_unknown_block_entity_position_locks_edits(doc):
    doc.schematic.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id': nbt.String('mod:unknown')}))
    proposal = doc.propose(doc.selection, 1, 'fill', True)
    assert len(proposal.indices) == 0 and proposal.protected == doc.schematic.blocks.size
