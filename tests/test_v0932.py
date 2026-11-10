import copy,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox,QInputDialog
from test_ai_gui import window,ready,wait_for
from test_ai_design import server
from test_v093_analysis import build
from create_v0931_demo import build as demo
from ship_architect.space_model import new_space,box_runs
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.space_display import visible,external,alpha
from ship_architect.space_classification import classify
from ship_architect.spatial3d import recognize_3d
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.space_geometry import boundary_quads,feature_lines
from ship_architect.equipment import classification,inventory

def select(w,ids):
 b=w.workbench;b.rebuild_tree(True);b.tree.clearSelection()
 for sid in ids:b.nodes['space:'+sid].setSelected(True)
 if ids:b.tree.setCurrentItem(b.nodes['space:'+ids[-1]],0,__import__('PySide6.QtCore',fromlist=['QItemSelectionModel']).QItemSelectionModel.SelectionFlag.NoUpdate)


def test_opacity_visibility_isolation_bounds_do_not_mutate_spaces_or_blocks(window,tmp_path):
 w=window;d,cells=build();r=recognize_3d(d,d.selection);d.spaces=[new_space(r['candidates'][0]['volume_runs'],d.schematic.size,'A'),new_space([[1,1,1,2]],d.schematic.size,'B')];w.set_document(d);wait_for(lambda:ready(w));spaces=copy.deepcopy(d.spaces);blocks=d.schematic.blocks.copy();revision=d.revision;meta=d.schematic.metadata_digest();t=w.space_tools;sid=d.spaces[0]['id'];w.space_manager.select_id(sid)
 t.normal.setValue(145);t.candidate.setValue(190);t.visibility({sid},False);assert not visible(w.view,d.spaces[0]);t.visibility({sid},True);t.only.setChecked(True);assert visible(w.view,d.spaces[0]) and not visible(w.view,d.spaces[1]);t.geometry.setCurrentIndex(1);wait_for(lambda:ready(w))
 assert d.spaces==spaces and np.array_equal(blocks,d.schematic.blocks) and meta==d.schematic.metadata_digest() and d.revision==revision
 w.workbench.save_display();path=tmp_path/'view.shipproj';d.save_project(path);q=Document.open_project(path);w.set_document(q);wait_for(lambda:ready(w));assert q.view_settings['space_presentation']['normal_alpha']==145 and w.space_tools.geometry.currentIndex()==1 and q.spaces==spaces


def test_tree_checks_share_2d3d_state_without_history_mutation(window):
 w=window;d,cells=build();d.spaces=[new_space([[2,3,3,5]],d.schematic.size)];w.set_document(d);wait_for(lambda:ready(w));before=copy.deepcopy(d.spaces);b=w.workbench;b.nodes['space:'+d.spaces[0]['id']].setCheckState(0,Qt.CheckState.Unchecked);wait_for(lambda:ready(w));assert not visible(w.view,d.spaces[0]) and d.spaces==before and not d.undo_stack
 w.space_manager.show_all(True);assert visible(w.view,d.spaces[0]);assert d.spaces==before


def test_Delete_key_multi_delete_confirm_undo_redo_preserves_NBT(window,monkeypatch):
 w=window;d,g=demo();r=recognize_exterior(d,d.selection);d.spaces=[new_space(c['volume_runs'],d.schematic.size,c['id']) for c in r['candidates'][:3]];w.set_document(d);wait_for(lambda:ready(w));before=copy.deepcopy(d.spaces);blocks=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();select(w,[s['id'] for s in d.spaces[:2]])
 monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No);QTest.keyClick(w.workbench.tree,Qt.Key.Key_Delete);assert d.spaces==before
 monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes);QTest.keyClick(w.workbench.tree,Qt.Key.Key_Delete);assert len(d.spaces)==1;w.undo();wait_for(lambda:ready(w));assert d.spaces==before;w.redo();wait_for(lambda:ready(w));assert len(d.spaces)==1 and np.array_equal(blocks,d.schematic.blocks) and d.schematic.metadata_digest()==meta


def test_normal_delete_can_regenerate_exclusion_delete_stays_excluded(window):
 w=window;d,cells=build();w.set_document(d);b=w.space_view.bulk;b.scan(True);wait_for(lambda:ready(w));sid=d.spaces[0]['id'];select(w,[sid]);w.space_tools.delete(confirm=False);assert not d.spaces;b.scan(True,False,True);wait_for(lambda:ready(w));assert len(d.spaces)==1
 select(w,[d.spaces[0]['id']]);w.space_tools.delete(True,False);b.scan(True,False,True);wait_for(lambda:ready(w));assert not d.spaces and d.analysis_state['decisions']


def test_candidate_delete_cleanup_and_undo_do_not_delete_confirmed_spaces(window):
 w=window;d,g=demo();w.set_document(d);b=w.space_view.bulk;b.recognition.mode.setCurrentIndex(1);b.scan(True);wait_for(lambda:ready(w));before=copy.deepcopy(d.spaces);count=len(d.space_candidates);w.space_view.list.setCurrentRow(0);b.remove_candidates();assert len(d.space_candidates)==count-1 and d.spaces==before;w.undo();wait_for(lambda:ready(w));assert len(d.space_candidates)==count;b.remove_candidates(True);assert not any(c['decision']=='exclude' for c in d.space_candidates) and d.spaces==before


def test_context_rename_is_metadata_only(window,monkeypatch):
 w=window;d,cells=build();d.spaces=[new_space([[2,3,3,5]],d.schematic.size)];w.set_document(d);wait_for(lambda:ready(w));select(w,[d.spaces[0]['id']]);monkeypatch.setattr(QInputDialog,'getText',lambda *a,**k:('함교',True));w.space_tools.rename();assert d.spaces[0]['name']=='함교';w.undo();assert d.spaces[0]['name']!='함교'


def test_exterior_giant_is_not_auto_or_manual_registered_and_default_hidden():
 from ship_architect.analysis_state import register
 import pytest
 d=Document(Schematic.empty(120,30,120));runs=box_runs(d.selection);c={'id':'outer','method':'exterior_voxel_mesh','volume_runs':runs,'volume':d.selection.volume,'lo':[0,0,0],'hi':[119,29,119],'fingerprint':'x','checks':{},'decision':'review','reasons':[]};classify([c],d);assert c['space_class']=='exterior_or_merged' and c['decision']=='exclude'
 class V:doc=d;space_display={}
 assert not visible(V,c,True)
 with pytest.raises(ValueError,match='외부'):register(d,{'block_epoch':0,'candidates':[c]},False)


def test_true_edges_remove_coplanar_strip_seams():
 runs=[[y,z,1,4] for y in range(1,5) for z in range(1,5)];q,n=boundary_quads(runs);lines=feature_lines(q,n)
 # Every retained segment is a cuboid ridge, never a line inside its planar face.
 for a,b in lines:
  fixed=[float(a[i]) for i in range(3) if a[i]==b[i]];assert len(fixed)==2 and all(v in (1.,5.) for v in fixed)


def test_unclassified_create_addon_detection_and_counts():
 s=Schematic.empty(8,8,8);s.palette[2]='createaddition:alternator[facing=north]';s.palette[3]='createpropulsion:thrust_controller';s.palette[4]='computercraft_tweaked:computer';s.blocks[1,1,1:4]=[2,3,4];d=Document(s);r=inventory(d);assert r['total']==3 and r['unclassified']==0 and classification(d,s.palette[3])=='추진 설비 후보'


def test_space_delete_discards_pending_preview_without_applying_blocks(window):
 w=window;d=Document(Schematic.empty(12,8,12));d.spaces=[new_space([[y,z,2,5] for y in range(1,5) for z in range(2,6)],d.schematic.size)];w.set_document(d);wait_for(lambda:ready(w));sid=d.spaces[0]['id'];w.space_manager.select_id(sid);w.make_preview();wait_for(lambda:ready(w));assert w.proposal is not None;select(w,[sid]);w.space_tools.delete(confirm=False);wait_for(lambda:ready(w));assert w.proposal is None and not d.schematic.blocks.any() and not d.spaces



def test_automatic_startup_probe_with_controlled_server(window,server):
 w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url);p.startup_probe.start(1);wait_for(lambda:'연결됨' in p.server_status.text() and not p.busy());assert '연결됨' in p.server_status.text()
