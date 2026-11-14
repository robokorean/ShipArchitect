from zipfile import ZipFile

import numpy as np
import pytest
from PySide6.QtCore import Qt

from ship_architect.analysis_sessions import provenance
from ship_architect.analysis_state import commit, validate
from ship_architect.chunks import Cancelled
from ship_architect.diagnostics import report
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.structure_review import LAYERS
from test_ai_gui import ready, wait_for, window


def example(kind):
    sc=Schematic.empty(22,14,20)
    sc.palette.update({1:'minecraft:stone',2:'minecraft:grass_block',3:'create:creative_motor'})
    b=sc.blocks
    b[2,3:17,3:19]=1
    b[10,3:17,3:19]=1
    b[2:11,3:17,[3,18]]=1
    b[2:11,[3,16],3:19]=1
    if kind in ('one_side','both','partial','platform','equipment'):
        b[3:10,4:16,3]=0
    if kind in ('both','partial'):
        b[3:10,4:16,18]=0
    if kind=='partial':
        b[6,4:16,4:14]=1
    if kind=='platform':
        b[6,7:10,7:11]=1
    if kind=='under':
        b[2,6:14,6:15]=0
        b[0]=2
    if kind=='deck':
        b[3:11]=0
    if kind=='equipment':
        b[4,8,8:14]=3
    if kind=='pillar':
        b[3:10,9,10]=1
    if kind=='concave':
        b[:,12:,12:]=0
    return Document(sc)


def run(doc, structure=True):
    return recognize_exterior(doc,doc.selection,structure_review=structure)


@pytest.mark.parametrize('kind',['closed','one_side','both','partial','platform','under','deck','equipment','pillar','concave'])
def test_evidence_is_separate_from_exact_candidates(kind):
    doc=example(kind)
    before=doc.schematic.blocks.copy()
    metadata=doc.schematic.metadata_digest()
    old=run(doc,False)
    new=run(doc)
    view=new['structure_review']
    assert new['candidates']==old['candidates']
    assert set(view['layers'])==set(LAYERS)
    assert view['layers']['hull']['count']
    for key in ('interior','external_air'):
        assert view['layers'][key]['rows_omitted']==0
        for y,z,a,b in view['layers'][key]['runs']:
            assert not doc.schematic.solid(before[y,z,a:b+1]).any()
    assert np.array_equal(before,doc.schematic.blocks)
    assert metadata==doc.schematic.metadata_digest()
    if kind=='closed':
        assert view['class_counts']['closed_interior']==1
        assert not view['spaces'][0]['external_connection']
    if kind in ('one_side','both'):
        assert any(x['opening_pattern']==('one_side' if kind=='one_side' else 'opposing_sides') and x['external_connection'] for x in view['spaces'])
    if kind=='partial':
        assert any(x['classification']=='partial_partition' and
                   any(q['y']==6 and q['through_air_cells'] for q in x['deck_sections']) for x in view['spaces'])
    if kind=='platform':
        assert not any(x['deck_sections'] for x in view['spaces'])
        assert view['layers']['supports']['count']
    if kind=='under':
        assert view['layers']['terrain']['count']==22*20
        assert any(x['lower_opening_candidate_contacts'] for x in view['spaces'])
    if kind=='deck':
        assert not view['spaces']
        assert view['layers']['decks']['count']
    if kind=='equipment':
        group,=view['equipment_groups']
        assert (group['category'],group['cells'],group['operation'])==('전력 설비 후보',6,'unknown')
        assert group['adjacent_space_ids']
    if kind=='concave':
        assert not any(y==6 and z==14 and a<=16<=b for y,z,a,b in view['layers']['interior']['runs'])


def test_review_cancellation_and_invalid_geometry():
    doc=example('both')
    seen=[]
    def progress(n, message):
        seen.append(n)
    with pytest.raises(Cancelled):
        recognize_exterior(doc,doc.selection,progress,lambda:bool(seen and seen[-1]==95))
    r=run(doc)
    r['structure_review']['layers']['decks']['faces'][0][0]=4
    with pytest.raises(ValueError,match='면 방향'):
        validate(dict(last_result=r),doc.schematic.size)


def test_structure_panel_visibility_manual_review_undo_and_project(window,tmp_path):
    w=window
    d=example('partial')
    r=run(d)
    r['provenance']=provenance(d,r['method'],'fresh')
    commit(d,dict(d.analysis_state,last_result=r))
    w.set_document(d)
    wait_for(lambda:ready(w),seconds=30)
    w.integrated_ui.open_properties(w.integrated_ui.tasks.structure_page)
    panel=w.structure_review
    panel.attach()
    before=d.schematic.blocks.copy()
    for key in LAYERS:
        item=panel.nodes[key]
        item.setCheckState(0,Qt.CheckState.Checked)
        assert key in panel.visible
        item.setCheckState(0,Qt.CheckState.Unchecked)
        assert key not in panel.visible
    panel.nodes['decks'].setCheckState(0,Qt.CheckState.Checked)
    clip=panel.clip()
    assert len(panel.quads_3d('decks',panel.review['layers']['decks'],clip))
    assert panel.overlay_image(0,2,1,2).pixelColor(8,8).alpha()>0
    assert panel.overlay_image(0,1,2,8).pixelColor(8,2).alpha()>0
    panel.nodes['hidden'].setCheckState(0,Qt.CheckState.Checked)
    assert w.view.space_display['external']
    panel.nodes['hidden'].setCheckState(0,Qt.CheckState.Unchecked)
    assert not w.view.space_display['external']
    panel.results.setCurrentRow(0)
    panel.hold()
    assert any(s.get('hold') for s in panel.review['spaces'])
    w.set_selection(Bounds((6,4,6),(10,8,11)))
    panel.mark()
    assert len(panel.marks)==1 and panel.nodes['manual'].checkState(0)==Qt.CheckState.Checked
    w.undo()
    assert not panel.marks
    w.redo()
    assert len(panel.marks)==1
    w.workbench.save_display()
    path=tmp_path/'review.shipproj'
    d.save_project(path)
    reopened=Document.open_project(path)
    assert reopened.analysis_state==d.analysis_state
    assert 'decks' in reopened.view_settings['structure_layers']
    assert np.array_equal(before,reopened.schematic.blocks)
    assert reopened.schematic.metadata_digest()==d.schematic.metadata_digest()
    out=report(tmp_path/'review.zip',document=d,ui_state=w.integrated_ui.ui_state())
    with ZipFile(out) as z:
        assert 'structure_review.jsonl' in z.namelist()
        assert b'"layer": "decks"' in z.read('structure_review.jsonl')
