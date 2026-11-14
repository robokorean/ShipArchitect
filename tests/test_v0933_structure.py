"""Independent geometry regressions for v0.9.3.3 (no private schematic data)."""

import numpy as np
import pytest

from ship_architect.analysis_state import commit, register
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.exterior_structure import repair_partitions
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space
from test_ai_gui import window


def cells(candidate):
    return {(x, y, z) for y, z, a, b in candidate['volume_runs'] for x in range(a, b+1)}


def test_ground_floor_not_used_but_open_lower_bay_and_upper_room_survive(tmp_path):
    ship = Schematic.empty(36, 20, 36)
    ship.palette[2] = 'minecraft:grass_block[snowy=false]'
    ship.blocks[0] = 2
    # Roof and sides of a bottom-open skirt do not establish a ship floor.
    ship.blocks[7, 3:15, 3:15] = 1
    ship.blocks[2:7, 3:15, 3] = 1
    ship.blocks[2:7, 3:15, 14] = 1
    # Floor, roof and two side walls of a genuine open lower bay.
    ship.blocks[2, 17:29, 3:15] = 1
    ship.blocks[8, 17:29, 3:15] = 1
    ship.blocks[3:8, 17:29, 3] = 1
    ship.blocks[3:8, 17:29, 14] = 1
    # An upper closed compartment with sky above it.
    ship.blocks[11:18, 5:13, 19:28] = 1
    ship.blocks[12:17, 6:12, 20:27] = 0
    doc = Document(ship)
    before = ship.blocks.copy()
    meta = ship.metadata_digest()
    result = recognize_exterior(doc, doc.selection)
    assert result['terrain']['ignored_cells'] == 36 * 36
    assert result['terrain']['components'][0]['lo'] == [0, 0, 0]
    assert not any(x < 15 and z < 15 and y < 7 for c in result['candidates'] for x,y,z in cells(c))
    assert any((8, 3, 23) in cells(c) and c['decision'] == 'review' for c in result['candidates'])
    assert any((23, 13, 9) in cells(c) and c['structure_class'] == 'closed_interior'
               for c in result['candidates'])
    assert not any(y >= 18 for c in result['candidates'] for x,y,z in cells(c))
    legacy_terrain = recognize_exterior(doc, doc.selection, ignore_terrain=False)
    assert legacy_terrain['terrain']['ignored_cells'] == 0
    assert any((8, 2, 8) in cells(c) for c in legacy_terrain['candidates'])
    path = tmp_path/'bay.schem'
    doc.export(path,approved=True)
    restored = Schematic.from_bytes(path.read_bytes())
    assert np.array_equal(before, restored.blocks) and restored.metadata_digest() == meta


@pytest.mark.parametrize('aperture', ['wide', 'door', 'stair', 'elevator', 'vertical'])
def test_partition_repair_uses_geometry_and_respects_passages(aperture):
    shape=(8,13,18)
    blocks=np.ones(shape,dtype=np.uint32)
    labels=np.zeros(shape,dtype=np.int32)
    labels[1:6,2:11,2:8]=1
    labels[1:6,2:11,9:16]=2
    if aperture=='vertical':
        labels[:]=0
        labels[1:4,2:11,2:16]=1
        labels[4:7,2:11,2:16]=2
    else:
        labels[1:6,2:11,8]=1
        if aperture!='wide':
            labels[1:6,2:11,8]=0
            labels[1:4,5:7,8]=1
    blocks[labels>0]=0
    palette={0:'minecraft:air',1:'minecraft:stone'}
    if aperture in ('stair','elevator'):
        labels[1:6,2:11,8]=1
        labels[1,5,8]=0
        blocks[labels>0]=0
        blocks[1,5,8]=2
        palette[2]='minecraft:oak_stairs' if aperture=='stair' else 'create:elevator_pulley'
    bounds=Bounds((0,0,0),(17,7,12))
    restored, decisions=repair_partitions(labels,blocks>0,blocks,palette,bounds,set(),set(),None)
    assert np.array_equal(restored>0,labels>0)
    assert (len(np.unique(restored))==2)==(aperture=='wide')
    if aperture=='wide':
        key=':'.join(decisions[0]['source_fingerprints'])
        rejected,_=repair_partitions(labels,blocks>0,blocks,palette,bounds,set(),{key},None)
        protected,_=repair_partitions(labels,blocks>0,blocks,palette,bounds,{key.split(':')[0]},set(),None)
        assert np.array_equal(rejected,labels) and np.array_equal(protected,labels)


def test_user_space_metadata_is_not_overwritten_by_registration(tmp_path):
    ship=Schematic.empty(14,10,14)
    ship.blocks[1:9,1:13,1:13]=1
    ship.blocks[2:8,2:12,2:12]=0
    doc=Document(ship)
    custom=[new_space([[y,z,2,5] for y in range(2,8) for z in range(2,12)],ship.size,'Manual A'),
            new_space([[y,z,6,11] for y in range(2,8) for z in range(2,12)],ship.size,'Manual B')]
    doc.set_spaces(custom,'space_split')
    state=dict(doc.analysis_state,partition_rejections=['a'*64+':'+'b'*64])
    commit(doc,state)
    path=tmp_path/'manual.shipproj'
    doc.save_project(path)
    restored=Document.open_project(path)
    result=recognize_exterior(restored,restored.selection)
    spaces,review,added=register(restored,result)
    assert spaces==custom and not added
    assert any('existing_space_overlap_protected' in c['reasons'] for c in review['candidates'])
    assert restored.analysis_state['partition_rejections']==state['partition_rejections']


def test_suspended_slab_is_not_an_upper_interior():
    ship=Schematic.empty(30,20,30)
    ship.blocks[3,4:26,4:26]=1
    ship.blocks[16,4:26,4:26]=1
    # Rails provide horizontal skyline votes at only a few heights.
    ship.blocks[4:8,4:26,4]=1
    ship.blocks[4:8,4:26,25]=1
    doc=Document(ship)
    result=recognize_exterior(doc,doc.selection)
    assert not any(c['decision']=='auto' for c in result['candidates'])
    assert all(not any(y>16 for _,y,_ in cells(c)) for c in result['candidates'])


def test_visual_review_and_json_export_through_qt(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    from test_ai_gui import ready, wait_for
    import json

    ship=Schematic.empty(16,10,16)
    ship.blocks[1:9,1:15,1:15]=1
    ship.blocks[2:8,2:14,2:14]=0
    doc=Document(ship)
    window.set_document(doc)
    panel=window.space_view.bulk
    panel.recognition.mode.setCurrentIndex(1)
    panel.scan(whole=True)
    wait_for(lambda:ready(window) and bool(doc.space_candidates), seconds=30)
    target=tmp_path/'recognition.json'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(target),'JSON'))
    panel.recognition.export_json()
    wait_for(lambda:ready(window) and target.is_file(), seconds=30)
    result=json.loads(target.read_text(encoding='utf-8'))
    from ship_architect import __version__
    assert result['algorithm_version']==__version__
    assert result['candidates'][0]['boundary_contacts']['roof'] == 144
    assert doc.schematic.blocks[2,2,2]==0
