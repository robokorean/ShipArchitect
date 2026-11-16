from copy import deepcopy

import numpy as np
from PySide6.QtCore import Qt

from ship_architect.analysis_state import apply_rules, commit
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Document
from ship_architect.scene_registry import delete_items, discover
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space
from test_ai_gui import ready, wait_for, window
from test_v0934_sessions import room_document


def cells(result):
    return {(x, y, z) for c in result['candidates'] for y, z, a, b in c['volume_runs'] for x in range(a, b + 1)}


def test_precision_retains_discovery_and_normal_rooms():
    d = room_document()
    old = recognize_exterior(d, d.selection, engine='integrated')
    new = recognize_exterior(d, d.selection, engine='precision')
    assert old['domain_cells'] == new['domain_cells']
    assert cells(old) == cells(new)
    assert new['algorithm_version'] == '0.9.3.10-precision'
    assert all(c['validation']['mesh_volume_matches_voxels'] for c in new['candidates'])


def scene_document():
    sc = Schematic.empty(14, 8, 14)
    sc.palette[2] = 'computercraft:computer_advanced'
    sc.blocks[2, 3, 7] = 2
    d = Document(sc)
    one = new_space([[2, 3, 3, 6], [2, 4, 3, 3]], sc.size, 'Held')
    two = new_space([[2, 7, 3, 6]], sc.size, 'Other')
    d.set_spaces([one, two], active=one['id'])
    equipment = {'position': [7, 2, 3], 'block': sc.palette[2], 'category': 'ComputerCraft',
                 'spaces': [], 'adjacent_spaces': [one['id']]}
    passage = {'positions': [[3, 2, 3], [3, 2, 7]], 'spaces': [one['id'], two['id']],
               'label': '통로', 'status': '통행 미확인'}
    return d, (one, two), {'equip:7,2,3': equipment, 'route:test-passage': passage}


def test_metadata_delete_cleans_relations_and_roundtrips_undo_redo(tmp_path):
    d, (one, two), items = scene_document()
    original = d.schematic.blocks.copy()
    nbt = d.schematic.metadata_digest()
    commit(d, dict(d.analysis_state, scene_records=items))
    delete_items(d, {one['id']}, set(), {'equip:7,2,3'}, True)
    assert d.spaces == [two]
    assert d.analysis_state['scene_records']['route:test-passage']['spaces'] == [two['id']]
    assert d.analysis_state['scene_excluded'] == ['equip:7,2,3']
    path = tmp_path / 'scene.shipproj'
    d.save_project(path)
    reopened = Document.open_project(path)
    reopened.undo()
    assert reopened.analysis_state['scene_records'] == items
    assert reopened.spaces == [one, two]
    reopened.redo()
    assert reopened.spaces == [two] and 'equip:7,2,3' not in reopened.analysis_state['scene_records']
    assert np.array_equal(reopened.schematic.blocks, original)
    assert reopened.schematic.metadata_digest() == nbt


def test_policy_controls_reappearance_for_scene_and_voxel_candidates():
    d = room_document()
    result = recognize_exterior(d, d.selection, engine='precision')
    candidate = result['candidates'][0]
    item = {'position': [1, 1, 1], 'block': 'computercraft:computer_advanced', 'category': 'ComputerCraft'}
    for exclude in (False, True):
        commit(d, dict(d.analysis_state, last_result=deepcopy(result), scene_records={'equip:1,1,1': item}))
        delete_items(d, set(), {candidate['id']}, {'equip:1,1,1'}, exclude)
        again = apply_rules(result, d.analysis_state, d.schematic.size)
        assert (candidate['id'] not in {c['id'] for c in again['candidates']}) == exclude
        assets = discover(d, {}, {'records': [item]}, None)
        assert ('equip:1,1,1' not in assets) == exclude


def test_explorer_uses_exact_shape_without_moving_camera(window):
    w = window
    d, (one, two), items = scene_document()
    commit(d, dict(d.analysis_state, scene_records=items))
    w.set_document(d)
    wait_for(lambda: ready(w))
    b = w.workbench
    camera = (w.view.zoom, w.view.focus_bounds, w.view.pan.copy())
    assert b.select_key('space:' + one['id'])
    assert b.select_key('equip:7,2,3', extend=True)
    assert w.view.scene_selection == {'space:' + one['id'], 'equip:7,2,3'}
    assert (w.view.zoom, w.view.focus_bounds) == camera[:2] and np.array_equal(w.view.pan, camera[2])
    assert 'space:' + one['id'] not in b.keys_at((5, 2, 4))
    assert 'space:' + one['id'] in b.keys_at((3, 2, 4))
    assert b.pick_scene(b.keys_at((3, 2, 6)))
    assert b.selection_key == 'route:test-passage'
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    plan=w.space_manager.plan
    w.integrated_ui.open_drawing()
    w.layer.setValue(2)
    plan.layout_map()
    pos=QPoint(round(plan.ox+3.5*plan.scale),round(plan.oz+4.5*plan.scale))
    QTest.mouseClick(plan,Qt.MouseButton.LeftButton,pos=pos)
    assert b.selection_key=='space:'+one['id']
    b.tree.clearSelection()
    b.nodes['space:' + one['id']].setCheckState(2, Qt.CheckState.Checked)
    b.nodes['space:' + two['id']].setCheckState(2, Qt.CheckState.Checked)
    assert len(b.keys_for_scope('selected')) == 2
    b.search.setText('Held')
    assert b.keys_for_scope('selected') == {'space:' + one['id']}
    w.space_tools.bulk_delete('selected', confirm=False)
    assert [s['id'] for s in d.spaces] == [two['id']]
    assert not w.view.scene_selection
    b.search.clear()
    w.undo()
    assert b.selection_key in ('space:' + one['id'], 'space:' + two['id'])
    assert len(d.spaces) == 2
    w.redo()
    assert d.spaces == [two]


def test_precision_scan_populates_inventory_in_same_transaction(window):
    w = window
    d = room_document()
    x, y, z = d.selection.lo
    d.schematic.palette[11] = 'computercraft:computer_normal'
    d.schematic.blocks[y, z, x] = 11
    w.set_document(d)
    w.structure_review.engine.setCurrentIndex(w.structure_review.engine.findData('precision'))
    w.structure_review.scan()
    wait_for(lambda: ready(w), seconds=30)
    assert 'equip:0,0,0' in d.analysis_state['scene_records']
    assert w.workbench.records['equip:0,0,0']['category'] == 'ComputerCraft'
    assert len(d.undo_stack) == 1
    w.undo()
    assert not d.analysis_state.get('scene_records')
