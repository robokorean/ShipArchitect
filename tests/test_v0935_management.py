from copy import deepcopy
import json
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from ship_architect import analysis_archive
from ship_architect.analysis_state import commit, register
from ship_architect.analysis_sessions import provenance
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Document
from ship_architect.schematic import Schematic
from ship_architect.space_display import candidate_spaces, visible
from ship_architect.space_model import new_space
from test_ai_gui import ready, wait_for, window
from test_v0934_sessions import room_document


def populate(w):
    d=room_document()
    r=recognize_exterior(d,d.selection)
    r['provenance']=provenance(d,r['method'],'fresh')
    d.set_spaces([new_space([[2,2,2,3]],d.schematic.size,'Manual')])
    commit(d,dict(d.analysis_state,last_result=r))
    w.set_document(d)
    wait_for(lambda:ready(w),seconds=30)
    return d


def test_candidates_display_is_independent_of_selection_and_group(window):
    d=populate(window)
    w=window
    assert len(candidate_spaces(w.view))==2
    w.space_view.list.setCurrentRow(1)
    assert len(candidate_spaces(w.view))==2
    key='candgroup:자동 등록 가능'
    w.workbench.nodes[key].setCheckState(0,Qt.CheckState.Unchecked)
    assert not candidate_spaces(w.view)
    w.workbench.rebuild_tree(True)
    w.workbench.nodes[key].setCheckState(0,Qt.CheckState.Checked)
    assert len(candidate_spaces(w.view))==2
    w.space_tools.set_mode('confirmed')
    assert not candidate_spaces(w.view) and visible(w.view,d.spaces[0])
    w.space_tools.set_mode('candidates')
    assert len(candidate_spaces(w.view))==2 and not visible(w.view,d.spaces[0])
    w.space_tools.set_mode('none')
    assert not candidate_spaces(w.view) and not visible(w.view,d.spaces[0])


def test_raw_open_and_manual_legacy_restore_does_not_resurrect_deleted_spaces(window,tmp_path):
    w=window
    ship=Schematic.empty(12,8,12)
    f=tmp_path/'raw.schem'
    f.write_bytes(ship.to_bytes())
    def open_raw():
        return Document(Schematic.from_bytes(f.read_bytes()),f.read_bytes(),f)
    d=open_raw()
    w.set_document(d)
    legacy=w.space_manager.legacy_path()
    legacy.parent.mkdir(parents=True,exist_ok=True)
    legacy.write_text(json.dumps({'project_id':d.project_id,'revision':4,'size':list(ship.size),
        'geometry_key':w.space_manager.geometry_key(),'spaces':[new_space([[2,3,2,5]],ship.size)]}),encoding='utf8')
    d=open_raw()
    w.set_document(d)
    assert not d.spaces and legacy.is_file()
    w.space_view.bulk.sessions.restore()
    assert len(d.spaces)==1
    w.space_tools.bulk_delete('all',confirm=False)
    assert not d.spaces
    w.set_document(open_raw())
    w.space_view.bulk.sessions.restore()
    assert not w.doc.spaces


def test_archive_and_bulk_delete_undo_leave_blocks_nbt_and_saved_runs(window,tmp_path,monkeypatch):
    w=window
    d=populate(w)
    blocks=d.schematic.blocks.copy()
    nbt=d.schematic.metadata_digest()
    spaces=deepcopy(d.spaces)
    first=analysis_archive.save(tmp_path,d,'first')
    second=analysis_archive.save(tmp_path,d,'second')
    assert len(list(analysis_archive.entries(tmp_path)))==2
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No)
    w.space_tools.bulk_delete('all')
    assert d.spaces==spaces and len(d.space_candidates)==2
    w.space_tools.bulk_delete('all',confirm=False)
    assert not d.spaces and not d.space_candidates
    assert first.is_file() and second.is_file()
    w.undo()
    assert d.spaces==spaces and len(d.space_candidates)==2
    w.redo()
    assert not d.spaces and not d.space_candidates
    assert np.array_equal(blocks,d.schematic.blocks) and d.schematic.metadata_digest()==nbt
    result,match=analysis_archive.load(first,d)
    assert match is True and len(result['candidates'])==2
    d.schematic.blocks[0,0,0]=1
    result,match=analysis_archive.load(first,d)
    assert not match and result['archive_read_only']
    with pytest.raises(ValueError,match='블록 일치'):
        register(d,result,False)
