import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from test_ai_gui import window,ready,wait_for
from create_v0931_demo import build
from ship_architect.model import Document
from ship_architect.diagnostics import report

def test_engine_selection_auto_registers_exact_shapes_and_preserves_manual_UI(window,tmp_path):
 w=window;d,g=build();w.set_document(d);b=w.space_view.bulk;w.integrated_ui.open_properties(2);b.recognition.mode.setCurrentIndex(1)
 button=next(q for q in w.space_view.findChildren(QPushButton) if q.text()=='전체 3D 분석·자동 등록');QTest.mouseClick(button,Qt.MouseButton.LeftButton);wait_for(lambda:ready(w),45)
 assert d.analysis_state['last_result']['method']=='exterior_voxel_mesh' and len(d.spaces)==2 and w.view.isVisible()
 assert sorted(sum(B-a+1 for y,z,a,B in s['runs']) for s in d.spaces)==[1968,2280]
 p=tmp_path/'new.shipproj';d.save_project(p);q=Document.open_project(p);assert q.spaces==d.spaces
 w.undo();wait_for(lambda:ready(w));assert not d.spaces;w.redo();wait_for(lambda:ready(w));assert len(d.spaces)==2


def test_comparison_switch_overlay_and_project_restoration(window,tmp_path):
 w=window;d,g=build();w.set_document(d);b=w.space_view.bulk;t=b.recognition;t.compare();wait_for(lambda:ready(w),45)
 assert 'comparison' in d.analysis_state and not d.spaces and w.view.isVisible()
 b.group.setCurrentIndex(0);w.space_view.list.setCurrentRow(0);t.overlay();assert len(w.view.validation_spaces)==2 and 'IoU' in w.space_view.text.toPlainText()
 t.show('old');assert d.analysis_state['last_result']['method']=='3d_air_components';t.show('new');assert d.analysis_state['last_result']['method']=='exterior_voxel_mesh'
 p=tmp_path/'comparison.shipproj';d.save_project(p);q=Document.open_project(p);w.set_document(q);wait_for(lambda:ready(w));assert w.space_view.list.count()>0
 assert q.analysis_state['comparison']==d.analysis_state['comparison']
 import zipfile
 z=report(tmp_path/'diagnostic.zip',document=q)
 with zipfile.ZipFile(z) as archive:assert 'recognition_comparison.json' in archive.namelist() and any('exterior_first' in n for n in archive.namelist())


def test_experimental_failure_keeps_old_result_and_fallback_is_usable(window,monkeypatch):
 import ship_architect.recognition_ui as ui
 w=window;d,g=build();w.set_document(d);b=w.space_view.bulk;b.scan(False);wait_for(lambda:ready(w));before=copy.deepcopy(d.analysis_state);blocks=d.schematic.blocks.copy();errors=[];original=w.error;w.error=errors.append
 monkeypatch.setattr(ui,'recognize_exterior',lambda *a,**k:(_ for _ in ()).throw(ValueError('실험 인식 실패 시험')));b.recognition.mode.setCurrentIndex(1);b.scan(False);wait_for(lambda:ready(w));assert errors and d.analysis_state==before
 b.recognition.mode.setCurrentIndex(0);b.scan(False);wait_for(lambda:ready(w));assert d.analysis_state['last_result']['method']=='3d_air_components' and np.array_equal(blocks,d.schematic.blocks);w.error=original


def test_obj_export_connected_to_UI_without_modifying_schematic(window,tmp_path,monkeypatch):
 from ship_architect.recognition_ui import QFileDialog
 w=window;d,g=build();w.set_document(d);b=w.space_view.bulk;b.recognition.mode.setCurrentIndex(1);b.scan(False);wait_for(lambda:ready(w));w.space_view.list.setCurrentRow(0);before=d.schematic.blocks.copy();path=tmp_path/'hangar.obj';monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(path),'Wavefront OBJ (*.obj)'))
 b.recognition.export();wait_for(lambda:ready(w));assert path.exists() and '\nf ' in path.read_text() and np.array_equal(before,d.schematic.blocks)


def test_legacy_import_entry_preserves_new_review_gates_and_exact_shapes(window):
 w=window;d,g=build();w.set_document(d);b=w.space_view.bulk;b.recognition.mode.setCurrentIndex(1);b.scan(False);wait_for(lambda:ready(w));w.space_manager.import_candidates();wait_for(lambda:ready(w));assert len(d.spaces)==2
 # Explicitly approve the open hangar; legacy-zone import must not extrude/duplicate it.
 b.group.setCurrentIndex(2);w.space_view.list.setCurrentRow(0);b.confirm();wait_for(lambda:ready(w));before=copy.deepcopy(d.spaces);w.space_manager.import_legacy();assert d.spaces==before and all('space_volume_runs' in z for z in d.zones)
