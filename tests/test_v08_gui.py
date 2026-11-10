import copy,json
import numpy as np
from PySide6.QtCore import Qt,QPointF,QPoint
from PySide6.QtTest import QTest
from ship_architect.model import Document,Bounds
from ship_architect.space_model import bbox,contains,subtract,new_space
from ship_architect.schematic import Schematic
from test_ai_gui import window,wait_for,ready
from test_ai_design import server,plan,operation
from test_v08_spaces import l_space


def test_ui_create_merge_split_properties_visibility_and_undo(window):
 w=window;m=w.space_manager;m.create();wait_for(lambda:ready(w));first=w.doc.active_space_id
 w.set_selection(Bounds((8,1,4),(10,5,9)));m.create();wait_for(lambda:ready(w));assert m.list.count()==2
 for i in range(m.list.count()):m.list.item(i).setSelected(True)
 m.merge();wait_for(lambda:ready(w));assert len(w.doc.spaces)==1
 space=w.doc.active_space();before=copy.deepcopy(space['runs'])
 m.edit_bounds(Bounds((2,1,2),(3,5,7)),'split');wait_for(lambda:ready(w));assert len(w.doc.spaces)==2
 m.name.setText('함교');m.purpose.setText('항해');m.opacity.setValue(110);m.space_color='#123456';m.properties();assert w.doc.active_space()['name']=='함교'
 assert w.doc.active_space()['opacity']==110 and w.doc.active_space()['color']=='#123456'
 m.show_all(False);assert not any(s['visible'] for s in w.doc.spaces)
 m.show_all(True);assert all(s['visible'] for s in w.doc.spaces)
 for _ in range(4):w.undo();wait_for(lambda:ready(w))
 assert len(w.doc.spaces)==1 and w.doc.spaces[0]['runs']==before
 assert not w.doc.schematic.blocks.any()


def test_real_2d_mouse_drag_removes_exact_cells_and_3d_overlay_click(window):
 w=window;d,s=l_space();w.set_document(d);m=w.space_manager;m.select_id(s['id']);wait_for(lambda:ready(w))
 w.tabs.setCurrentWidget(m.plan);m.plan.layer=2;m.plan.refresh();m.edit_mode.setCurrentIndex(m.edit_mode.findData('remove'));m.all_height.setChecked(True)
 QTest.qWait(30);m.plan.layout_map()
 def pt(x,z):return QPoint(round(m.plan.ox+(x+.5)*m.plan.scale),round(m.plan.oz+(z+.5)*m.plan.scale))
 QTest.mousePress(m.plan,Qt.MouseButton.LeftButton,pos=pt(2,2));QTest.mouseMove(m.plan,pt(3,3));QTest.mouseRelease(m.plan,Qt.MouseButton.LeftButton,pos=pt(3,3));wait_for(lambda:ready(w))
 assert not contains(d.active_space(),[2*14*14+2*14+2],d.schematic.size)[0]
 assert m.plan.blocks is w.doc.schematic.blocks and d.active_space_id==s['id']
 # Exact planar polygon hit must select the room, independently of block hit buffers.
 d.active_space_id=None;w.tabs.setCurrentWidget(w.view);w.view.update();QTest.qWait(30)
 assert w.view.space_hits
 sid,poly=w.view.space_hits[-1];center=poly.boundingRect().center();QTest.mouseClick(w.view,Qt.MouseButton.LeftButton,pos=center.toPoint())
 assert d.active_space_id==s['id']


def test_ai_scope_ui_preview_zero_reason_and_per_space_undo(window,server):
 w=window;d,s=l_space();w.set_document(d);m=w.space_manager;m.select_id(s['id']);p=w.ai_panel;client,h=server;p.url.setText(client.url)
 h.responses=[json.dumps(plan(operation('fill','minecraft:glass',hi=(7,4,7))))];p.prompt.setPlainText('공간을 유리로 채워줘');p.generate();wait_for(lambda:p.job is None and ready(w))
 assert w.proposal is not None and w.proposal.outside_space>0 and contains(s,w.proposal.indices,d.schematic.size).all()
 trace=p.current_trace;row=p.chat_store.get(d.project_id,trace);assert row['selection']['space_id']==s['id'] and row['space_id']==s['id']
 assert '공간 셀 밖' in w.summary.toPlainText() and not d.schematic.blocks.any()
 w.tabs.setCurrentWidget(m.plan);w.layer.setValue(2);w.compare.setCurrentIndex(0);assert m.plan.blocks is w.preview_blocks
 w.approve();wait_for(lambda:ready(w));assert d.history[-1]['space_id']==s['id'];m.undo_space();wait_for(lambda:ready(w));assert not d.schematic.blocks.any()
 d.add_zone('protected',d.selection,True);h.responses=[json.dumps(plan(operation('fill','minecraft:glass',hi=(7,4,7))))];p.generate();wait_for(lambda:p.job is None and ready(w))
 assert '변경 0개' in w.summary.toPlainText() and not w.approve_button.isEnabled()


def test_metadata_autosave_recovers_latest_project_settings(window,tmp_path):
 w=window;d,s=l_space();w.set_document(d);m=w.space_manager;path=tmp_path/'state.shipproj';d.save_project(path)
 m.name.setText('자동 저장 방');m.properties();m.save_metadata();assert m.path().exists()
 restored=Document.open_project(path);w.set_document(restored);wait_for(lambda:ready(w));assert restored.spaces[0]['name']=='자동 저장 방'
 w.undo();wait_for(lambda:ready(w));assert restored.spaces[0]['name']=='L 격납고'
 # A project with newer / different block history must not receive an old sidecar.
 m.save_metadata();restored.active_space_id=None;p=restored.propose(Bounds((1,0,1),(1,0,1)),1,'add');restored.selection=p.bounds;restored.approve(p);restored.save_project(path)
 reopened=Document.open_project(path);w.set_document(reopened);assert reopened.schematic.blocks[0,1,1]==1


def test_display_independence_exact_mesh_ghost_floor_and_layer_sync(window):
 w=window;d,s=l_space();upper=new_space([[7,z,2,4] for z in range(2,6)],d.schematic.size,'상층');d.set_spaces(d.spaces+[upper],active=s['id']);d.schematic.blocks[0,2:10,2:10]=1;d.schematic.blocks[6,2:6,2:5]=1
 w.set_document(d);m=w.space_manager;m.detect_floors();assert 1 in m.levels and 7 in m.levels
 m.blocks.setChecked(False);assert not w.view.show_blocks and w.view.show_spaces
 m.overlays.setChecked(False);assert not w.view.show_spaces
 m.blocks.setChecked(True);m.overlays.setChecked(True);m.only.setChecked(True);wait_for(lambda:ready(w));assert w.view.exact_space_only
 m.only.setChecked(False);m.ghost.setChecked(True);m.floor.setCurrentIndex(m.levels.index(1));m.select_floor();wait_for(lambda:ready(w));assert hasattr(w.view.mesh,'context_scene')
 m.floor_on.setChecked(False);wait_for(lambda:ready(w));assert 1 not in w.view.visible_y
 m.floor_on.setChecked(True);w.layer.setValue(7);wait_for(lambda:ready(w));assert m.plan.layer==7 and m.floor.currentData()==7 and w.view.floor_range[0]==7


def test_v07_footprint_import_and_deleted_space_history_never_falls_back_to_bbox(window):
 w=window;m=w.space_manager;d=w.doc;b=Bounds((2,1,2),(7,5,7));d.add_zone('구버전 방',b);d.zones[-1].update(space_footprint=[[2,2,7],[3,2,4]],space_kind='room');m.import_legacy()
 assert len(d.spaces)==1 and sum(r[3]-r[2]+1 for r in d.spaces[0]['runs'])==45
 store=w.ai_panel.chat_store;trace='space-history';sid=d.active_space_id;store.start(d.project_id,trace,'유리 바닥',{'lo':list(b.lo),'hi':list(b.hi),'space_id':sid});view=w.ai_panel.chat_view;view.refresh(True)
 d.set_spaces([],'space_delete');assert not view.load() and '삭제' in view.status.text()
 assert not d.schematic.blocks.any()


def test_2d_before_after_tracks_shared_preview_toggle(window,server):
 w=window;d,s=l_space();w.set_document(d);client,h=server;p=w.ai_panel;p.url.setText(client.url);h.responses=[json.dumps(plan(operation('fill','minecraft:glass',hi=(7,4,7))))];p.prompt.setPlainText('유리로 채워줘');p.generate();wait_for(lambda:p.job is None and ready(w))
 w.compare.setCurrentIndex(1);wait_for(lambda:ready(w));assert w.space_manager.plan.blocks is d.schematic.blocks and not w.space_manager.plan.blocks.any()
 w.compare.setCurrentIndex(0);wait_for(lambda:ready(w));assert w.space_manager.plan.blocks is w.preview_blocks and w.space_manager.plan.blocks.any()
