import copy,json
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from test_ai_gui import window,ready,wait_for
from test_v093_analysis import build
from ship_architect.model import Bounds,Document
from ship_architect.schematic import Schematic

def test_auto_registration_is_connected_to_ui_and_restores_on_undo(window,tmp_path):
 w=window;d,cells=build('varying');w.set_document(d);before=d.schematic.blocks.copy();bulk=w.space_view.bulk;bulk.scan(True);wait_for(lambda:ready(w),30)
 assert len(d.spaces)==1 and d.spaces[0]['auto_registered'] and d.analysis_state['last_result']['candidates'][0]['decision']=='registered'
 assert w.integrated_ui.tasks.bottom.currentWidget() is w.integrated_ui.tasks.space_results and w.view.isVisible()
 path=tmp_path/'auto.shipproj';d.save_project(path);r=Document.open_project(path);assert r.analysis_state==d.analysis_state and r.spaces==d.spaces
 w.undo();wait_for(lambda:ready(w));assert not d.spaces;w.redo();wait_for(lambda:ready(w));assert len(d.spaces)==1 and np.array_equal(before,d.schematic.blocks)


def test_review_crop_rule_and_undo_are_real_metadata_edits(window):
 w=window;d,cells=build();w.set_document(d);b=w.space_view.bulk;b.scan(False);wait_for(lambda:ready(w),30);original=copy.deepcopy(d.space_candidates[0]['volume_runs'])
 w.space_view.list.setCurrentRow(0);w.set_selection(Bounds((3,2,3),(6,5,6)));b.trim();assert d.space_candidates[0]['volume_runs']!=original and d.analysis_state['decisions']
 w.undo();wait_for(lambda:ready(w));assert d.space_candidates[0]['volume_runs']==original
 w.space_view.list.setCurrentRow(0);b.exclude_rule();assert d.space_candidates[0]['decision']=='exclude' and d.analysis_state['space_rules'];w.undo();wait_for(lambda:ready(w));assert not d.analysis_state['space_rules']


def test_compare_view_reports_conflict_and_does_not_change_any_block(window):
 w=window;d,cells=build();w.set_document(d);b=w.space_view.bulk;b.scan(False);wait_for(lambda:ready(w),30);d.schematic.blocks[2,3,3]=1;before=d.schematic.blocks.copy();w.space_view.list.setCurrentRow(0);b.compare();wait_for(lambda:ready(w));assert any(s['color']=='#f16d6d' for s in w.view.validation_spaces) and np.array_equal(before,d.schematic.blocks)


def test_layout_keeps_ship_when_analysis_chat_ai_and_results_open(window):
 w=window;ui=w.integrated_ui;tasks=ui.tasks;w.resize(1400,1000);w.workbench.open_tab(w.view)
 ui.open_analysis();QTest.qWait(40);assert w.view.isVisible() and w.workbench.scene_dock.isVisible()
 w.tabs.setCurrentWidget(w.ai_panel.chat_view);assert w.view.isVisible() and tasks.bottom.currentWidget() is w.ai_panel.chat_view
 w.ai_panel.show_panel();assert ui.properties.currentWidget().widget() is w.ai_panel and w.view.isVisible()
 tasks.toggle.setChecked(False);assert not tasks.result_dock.isVisible();tasks.toggle.setChecked(True);assert tasks.result_dock.isVisible()
 ui.open_analysis();w.network_view.pages.setCurrentIndex(0);QTest.qWait(30)
 assert w.network_view.source.width()>180 and w.network_view.target.width()>180
 for dock in (w.workbench.scene_dock,ui.analysis_dock):
  assert not dock.geometry().intersects(w.centralWidget().geometry()),(dock.geometry(),w.centralWidget().geometry())
 tasks.show(w.network_view.table);QTest.qWait(30);assert w.network_view.table.viewport().width()>500


def test_user_equipment_rule_from_real_selection_reclassifies_all_and_undo(window):
 w=window;sc=Schematic.empty(8,8,8);sc.palette[2]='unknown:engine[facing=north]';sc.palette[3]='unknown:engine[facing=south]';sc.blocks[1,1,1]=2;sc.blocks[1,1,2]=3;d=Document(sc);w.set_document(d);n=w.network_view;n.scan_equipment();wait_for(lambda:ready(w));n.table.selectRow(0)
 tasks=w.integrated_ui.tasks;tasks.rule_type.setCurrentText('추진 설비 후보');tasks.save_rule();wait_for(lambda:ready(w));assert n.equipment['by_category']['추진 설비 후보']==2 and d.analysis_state['equipment_rules']['unknown:engine']=='추진 설비 후보'
 before=sc.blocks.copy();w.undo();wait_for(lambda:ready(w));n.scan_equipment();wait_for(lambda:ready(w));assert n.equipment['unclassified']==2 and np.array_equal(before,sc.blocks)


def test_partial_reanalysis_ui_uses_changed_region(window):
 w=window;d,cells=build();w.set_document(d);b=w.space_view.bulk;b.scan(False);wait_for(lambda:ready(w),30)
 p=d.propose(Bounds((4,2,4),(4,2,4)),1,'fill',True);d.approve(p);w.refresh_views(p.indices);b.scan(False,True);wait_for(lambda:ready(w),30)
 assert d.analysis_state['last_result']['partial'] and d.spatial_dirty is None


def test_central_view_and_rule_results_survive_floating_result_dock(window):
 w=window;tasks=w.integrated_ui.tasks;tasks.result_dock.setFloating(True);tasks.show(tasks.diag);QTest.qWait(20);assert tasks.result_dock.isFloating() and w.view.isVisible();w.workbench.save();tasks.result_dock.setFloating(False);w.workbench.restore();assert tasks.result_dock.isFloating();tasks.result_dock.setFloating(False)


def test_analysis_cancel_discards_results_and_real_retry_succeeds(window,monkeypatch):
 import time
 import ship_architect.spatial_ui as spatial
 from ship_architect.chunks import checkpoint
 w=window;d,cells=build();w.set_document(d);wait_for(lambda:ready(w));original=spatial.recognize_3d;before=copy.deepcopy(d.analysis_state)
 def pending(doc,bounds,progress,cancel,partial=False):
  while True:checkpoint(cancel);time.sleep(.005)
 monkeypatch.setattr(spatial,'recognize_3d',pending);w.space_view.bulk.scan(True);assert w.operation is not None
 w.cancel_tasks();wait_for(lambda:ready(w));assert d.analysis_state==before and not d.spaces
 monkeypatch.setattr(spatial,'recognize_3d',original);w.space_view.bulk.scan(True);wait_for(lambda:ready(w));assert len(d.spaces)==1


def test_review_groups_and_bulk_decisions_do_not_override_manual_geometry(window):
 from pathlib import Path
 import sys
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
 from create_v093_demo import build as demo
 w=window;d,_=demo();w.set_document(d);shapes=copy.deepcopy(d.spaces);b=w.space_view.bulk;b.scan(False,False,True);wait_for(lambda:ready(w))
 b.group.setCurrentIndex(2);assert w.space_view.list.count()==2
 w.space_view.list.selectAll();b.decide('exclude');assert w.space_view.list.count()==0 and d.spaces==shapes
 w.undo();wait_for(lambda:ready(w));assert w.space_view.list.count()==2 and d.spaces==shapes
