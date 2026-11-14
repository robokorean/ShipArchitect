"""Provenance, independent analysis, and deck segmentation regression tests."""
from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import Qt

from ship_architect import __version__
from ship_architect.analysis_sessions import compare_candidates, provenance, matches_document
from ship_architect.analysis_state import commit, register
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.exterior_structure import split_deck_complexes
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space
from test_ai_gui import window, ready, wait_for


def test_comparison_uses_intersections_for_merge_split_and_disjoint():
    a=[dict(id='a',runs=[[0,0,0,4]]),dict(id='b',runs=[[0,0,5,7]]),
       dict(id='gone',runs=[[1,0,0,2]])]
    b=[dict(id='different',runs=[[0,0,0,7]]),dict(id='new',runs=[[2,0,0,1]])]
    rows=compare_candidates(a,b)
    assert [x['status'] for x in rows]==['merge','new','old_only']
    assert rows[0]['intersections']==[5,3]
    assert compare_candidates(b,a)[0]['status']=='split'
    assert compare_candidates(a[:1],[dict(a[0],id='renamed')])[0]['status']=='same'


def test_deck_partition_keeps_every_air_cell_and_requires_both_faces():
    shape=(22,120,130)
    labels=np.ones(shape,dtype=np.int32)
    hull=np.zeros(shape,bool)
    hull[11,:,:]=True
    hull[11,10:30,10:30]=False
    labels[hull]=0
    before=labels.copy()
    scope=Bounds((0,0,0),(129,21,119))
    info=split_deck_complexes(labels,hull,before>0,scope,None)[1]
    assert [x['cut_y'] for x in info['cuts']]==[12]
    assert info['cuts'][0]['air_faces']==400
    assert sum(p['cells'] for p in info['parts'])==int((before>0).sum())
    assert np.array_equal(labels>0,before>0)
    assert len(set(np.unique(labels))-{0})==2
    # Volume alone does not split open, uninterrupted air.
    open_air=np.ones(shape,dtype=np.int32)
    untouched=split_deck_complexes(open_air,np.zeros(shape,bool),open_air>0,scope,None)
    assert untouched[1]['parts']==[] and np.all(open_air==1)


def room_document():
    ship=Schematic.empty(20,12,20)
    ship.blocks[1:8,1:10,1:10]=1
    ship.blocks[2:7,2:9,2:9]=0
    ship.blocks[1:8,11:19,11:19]=1
    ship.blocks[2:7,12:18,12:18]=0
    return Document(ship)


def test_clean_and_protected_analysis_with_restore_and_approval(window,tmp_path):
    d=room_document()
    old=recognize_exterior(d,d.selection)
    old['algorithm_version']='0.9.3.3'
    manual=new_space(old['candidates'][0]['volume_runs'],d.schematic.size,'Keep me')
    key=old['candidates'][1]['fingerprint']
    d.set_spaces([manual])
    commit(d,dict(d.analysis_state,last_result=old,decisions={key:{'action':'exclude'}}))
    window.set_document(d)
    wait_for(lambda:ready(window),seconds=30)
    sessions=window.space_view.bulk.sessions
    manual_before=deepcopy(d.spaces)
    blocks=d.schematic.blocks.copy()
    nbt=d.schematic.metadata_digest()
    sessions.scan(False)
    wait_for(lambda:ready(window) and d.analysis_state.get('session_side')=='new',seconds=30)
    fresh=d.analysis_state['last_result']
    assert fresh['provenance']['mode']=='fresh'
    assert fresh['algorithm_version']==__version__
    assert all(c['decision']=='auto' for c in fresh['candidates'])
    assert d.spaces==manual_before and d.analysis_state['session_reference']['algorithm_version']=='0.9.3.3'
    sessions.restore()
    assert d.analysis_state['last_result']['algorithm_version']=='0.9.3.3'
    assert not window.space_view.overlay.isChecked()
    sessions.swap()
    assert d.analysis_state['last_result']['algorithm_version']==__version__
    panel=window.space_view.bulk
    for row in range(panel.panel.list.count()):
        item=panel.panel.list.item(row)
        if item.data(Qt.ItemDataRole.UserRole)==fresh['candidates'][1]['id']:
            panel.panel.list.setCurrentRow(row)
            break
    panel.confirm()
    assert len(d.spaces)==2 and d.spaces[0]==manual
    assert d.spaces[1]['provenance']['origin']=='approved_candidate'
    assert np.array_equal(blocks,d.schematic.blocks) and nbt==d.schematic.metadata_digest()
    output=tmp_path/'fresh.shipproj'
    d.save_project(output)
    q=Document.open_project(output)
    assert q.spaces==d.spaces
    assert q.analysis_state['session_reference']['algorithm_version']=='0.9.3.3'
    assert matches_document(q,q.analysis_state['last_result']['provenance'])
    d.undo()
    assert d.spaces==manual_before
    d.redo()
    assert d.spaces==q.spaces
    # Restore the original exclusion decision for the protected scan.
    d.analysis_state['decisions'][key]={'action':'exclude'}
    sessions.scan(True)
    wait_for(lambda:ready(window) and d.analysis_state['last_result']['provenance']['mode']=='protected_reanalysis',seconds=30)
    assert any(c['fingerprint']==key and c['decision']=='exclude' for c in d.space_candidates)
    assert d.spaces==q.spaces


def test_stale_signature_blocks_registration_even_at_same_epoch():
    d=room_document()
    result=recognize_exterior(d,d.selection)
    result['provenance']=provenance(d,result['method'],'fresh')
    d.schematic.blocks[0,0,0]=1
    assert not matches_document(d,result['provenance'])
    with pytest.raises(ValueError,match='블록 상태'):
        register(d,result)
