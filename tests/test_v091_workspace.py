import copy,json,time
from pathlib import Path
import numpy as np
import pytest
from PySide6.QtCore import Qt,QPointF,QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from ship_architect.app import MainWindow
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.display import DisplayState
from ship_architect.axis_plan import VIEWS,sampled_space_mask
from ship_architect.mesh import build_scene
from test_ai_gui import window,ready,wait_for
from test_ai_design import server
from test_v09_gui import setup
from create_v09_demo import build_demo


def move(view,x,y):
 event=QMouseEvent(QEvent.Type.MouseMove,QPointF(x,y),QPointF(x,y),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
 view.mouseMoveEvent(event)


def test_continuous_drag_paints_before_release_and_finishes_precise(window):
 w=window;w.set_document(build_demo());wait_for(lambda:ready(w));w.workbench.open_tab(w.view);QTest.qWait(40);v=w.view;start=v.interactive_frames
 QTest.mousePress(v,Qt.MouseButton.LeftButton,pos=v.rect().center());old=v.yaw
 for i in range(30):move(v,70+i*5,140+i);QTest.qWait(12)
 assert v.start is not None and v.interaction_active and v.interactive_frames>start+3 and v.yaw!=old
 assert v.pick(QPointF(100,100)) is None
 QTest.mouseRelease(v,Qt.MouseButton.LeftButton,pos=v.rect().center());wait_for(lambda:ready(w));assert v.pick_buffer is not None and not v.interaction_active


@pytest.mark.parametrize('axis',list('XYZ'))
@pytest.mark.parametrize('side',['below','above','slice'])
def test_display_masks_and_meshes_do_not_write(axis,side):
 d=Document(Schematic.empty(12,9,10));d.schematic.blocks[:]=1;original=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();state=DisplayState(axis,4,side,dimensions=d.schematic.size)
 scene=build_scene(d.schematic,display=state)
 assert len(scene)>0 and np.array_equal(original,d.schematic.blocks) and meta==d.schematic.metadata_digest()
 for q,n,m in scene:
  a=q[:,'XYZ'.index(axis)]
  if side=='below':assert a.max()<=5
  if side=='above':assert a.min()>=4
  if side=='slice':assert a.min()>=4 and a.max()<=5


def test_view_hide_isolate_restore_roundtrip_keeps_blocks_nbt_and_history(window,tmp_path):
 w=window;d=build_demo();w.set_document(d);b=w.workbench;before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();block_history=[h for h in d.history if h.get('blocks',0)]
 b.cut_axis.setCurrentText('X');b.cut_position.setValue(8);b.block_selected((8,1,4));b.hide_selected();assert not b.snapshot().contains_xyz((8,1,4),d,w.view)
 b.isolate();wait_for(lambda:ready(w));b.restore_visibility();wait_for(lambda:ready(w))
 assert np.array_equal(before,d.schematic.blocks) and meta==d.schematic.metadata_digest() and block_history==[h for h in d.history if h.get('blocks',0)]
 path=tmp_path/'display.shipproj';b.save_display();d.save_project(path);r=Document.open_project(path);assert np.array_equal(r.schematic.blocks,before) and r.schematic.metadata_digest()==meta
 out=tmp_path/'view.schem';d.export(out,approved=True);sc,_=load_file(out);assert np.array_equal(sc.blocks,before) and sc.metadata_digest()==meta


def test_hidden_blocks_not_pickable_and_hidden_proposal_requires_restore(window):
 w=window;d=build_demo();w.set_document(d);b=w.workbench;b.block_selected((8,1,4));b.hide_selected();wait_for(lambda:ready(w))
 assert not b.snapshot().contains_xyz((8,1,4),d,w.view)
 # The proposal is valid for the engine; view-only hidden cells require explicit restore in UI.
 d.active_space_id=None;bounds=Bounds((1,0,2),(1,0,2));d.selection=bounds;p=d.propose(bounds,d.add_material('minecraft:glass'),'replace',True,allow_exterior=True)
 b.cut_axis.setCurrentText('Y');b.cut_position.setValue(1);b.cut_side.setCurrentText('above')
 with pytest.raises(ValueError,match='표시 전체 복원'):b.guard(p)
 b.restore_visibility();wait_for(lambda:ready(w));assert b.guard(p)


def test_dock_detach_reattach_and_layout_preserve_shared_objects(window):
 w=window;b=w.workbench;d=w.doc;view=w.view;plan=w.space_manager.plan
 for kind,widget in [('3D',view),('2D',plan)]:
  b.detach_view(kind);QTest.qWait(30);assert b.floating[kind].isFloating() and widget.isVisible();assert w.doc is d
  b.attach_view(kind);assert w.tabs.indexOf(w.workbench.axis_plan if kind=='2D' else widget)>=0 and not b.floating[kind].isVisible()
 b.scene_dock.setFloating(True);b.scene_dock.resize(380,500);b.save();b.scene_dock.setFloating(False);b.restore();assert b.scene_dock.isFloating()
 b.reset_docks();assert not b.scene_dock.isFloating() and w.view is view and w.space_manager.plan is plan


def test_tree_selection_sync_space_floor_and_equipment(window):
 w,v=setup(window);b=w.workbench;w.space_manager.select_id('D');assert w.layer.value()==5 and b.tree.currentItem().data(0,Qt.ItemDataRole.UserRole)=='space:D'
 v.analyze();wait_for(lambda:ready(w),30);b.rebuild_tree(True);record=next(r for r in b.equipment if r['category']=='ComputerCraft');xyz=tuple(record['position']);b.block_selected(xyz)
 assert b.tree.currentItem().data(0,Qt.ItemDataRole.UserRole)=='equip:'+','.join(map(str,xyz)) and 'computercraft' in b.inspector.toPlainText()
 b.selected_xyz=None;b.tree.clearSelection();b.nodes['space:A'].setSelected(True);b.nodes['space:A_DUP'].setSelected(True)
 assert len([i for i in b.tree.selectedItems() if str(i.data(0,Qt.ItemDataRole.UserRole)).startswith('space:')])==2


@pytest.mark.parametrize('name',list(VIEWS))
def test_all_axis_planes_coordinate_roundtrip_and_masks(window,name):
 w=window;d=build_demo();w.set_document(d);plane=w.workbench.axis_plan;plane.orientation.setCurrentText(name);plane.slice.setValue(1);plane.refresh();canvas=plane.canvas;canvas.resize(600,450)
 u,v,f,flip,size=canvas.mapping();xyz=[3,3,3];xyz[f]=1
 assert canvas.coord(canvas.point(xyz))==tuple(xyz)
 assert plane.image is not None
 s=d.spaces[0];mask=sampled_space_mask(s,f,1,1,(size[v],size[u]));assert mask.dtype==bool


def test_current_original_exterior_view_is_readonly(window):
 w=window;d=build_demo();w.set_document(d);b=w.workbench;before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();b.exterior.setChecked(True);wait_for(lambda:ready(w),30)
 assert b.snapshot().hide_exterior and np.array_equal(d.schematic.blocks,before) and meta==d.schematic.metadata_digest()
 b.restore_visibility();wait_for(lambda:ready(w))


def test_masked_cube_cannot_be_picked_through_empty_plane(window):
 w=window;sc=Schematic.empty(8,8,8);sc.blocks[1,3,3]=1;d=Document(sc);w.set_document(d);w.layer.setValue(1);w.space_manager.overlays.setChecked(False);wait_for(lambda:ready(w));v=w.view;w.workbench.open_tab(v);QTest.qWait(20)
 point=QPointF(*v.project([[3.5,1,3.5]])[0]);assert v.pick(point)==(3,1,3)
 w.workbench.block_selected((3,1,3));w.workbench.hide_selected();wait_for(lambda:ready(w));assert v.pick(point) is None


def test_isolation_follows_space_selection_and_right_properties_edit(window):
 w=window;w.set_document(build_demo());b=w.workbench;w.space_manager.select_id('A');b.isolate();w.space_manager.select_id('D');wait_for(lambda:ready(w))
 assert b.isolation==__import__('ship_architect.space_model',fromlist=['bbox']).bbox(w.doc.active_space()) and w.layer.value()==5
 before=w.doc.schematic.blocks.copy();b.property_name.setText('새 함교');b.property_purpose.setText('항법');b.property_alpha.setValue(120);b.apply_properties()
 assert w.doc.active_space()['name']=='새 함교' and w.doc.active_space()['opacity']==120 and np.array_equal(before,w.doc.schematic.blocks)


def test_plane_overlay_matches_exact_shape_in_all_directions():
 from test_v08_spaces import l_space
 from ship_architect.space_model import contains
 d,s=l_space();w,h,l=d.schematic.size
 for f in range(3):
  u,v=([2,1] if f==0 else [0,2] if f==1 else [0,1]);index=3;uu,vv=np.meshgrid(np.arange(d.schematic.size[u]),np.arange(d.schematic.size[v]));coords=[np.zeros(uu.shape,np.int64) for _ in range(3)];coords[u]=uu;coords[v]=vv;coords[f][:]=index
  ii=coords[1]*w*l+coords[2]*w+coords[0];expected=contains(s,ii.ravel(),d.schematic.size).reshape(uu.shape)
  assert np.array_equal(sampled_space_mask(s,f,index,1,uu.shape),expected)


@pytest.mark.parametrize('name',list(VIEWS))
def test_frontmost_projection_picks_exact_nonhidden_depth(window,name):
 w=window;sc=Schematic.empty(9,8,10);u,v,f,flip=VIEWS[name];a=[3,3,3];b=[3,3,3];a[f]=1;b[f]=5;sc.blocks[a[1],a[2],a[0]]=1;sc.blocks[b[1],b[2],b[0]]=2;d=Document(sc);before=sc.blocks.copy();w.set_document(d);plane=w.workbench.axis_plan;plane.orientation.setCurrentText(name);plane.projection.setChecked(True);plane.refresh()
 expected=tuple(b if name in ('평면도 (위)','후면도','우측면도') else a);assert plane.ray_pick((3,3,3))==expected
 x,y,z=expected;w.workbench.hidden_blocks.add(y*9*10+z*9+x);w.workbench.apply();wait_for(lambda:ready(w));other=tuple(a if expected==tuple(b) else b);assert plane.ray_pick((3,3,3))==other and np.array_equal(before,sc.blocks)


def test_front_plane_boundary_drag_changes_exact_cells_only(window):
 from PySide6.QtCore import QPoint
 w=window;d=build_demo();w.set_document(d);w.space_manager.select_id('A');plane=w.workbench.axis_plan;plane.orientation.setCurrentText('정면도');plane.slice.setValue(3);w.workbench.open_tab(plane);QTest.qWait(30);w.space_manager.all_height.setChecked(False);w.space_manager.edit_mode.setCurrentIndex(w.space_manager.edit_mode.findData('remove'));before=d.schematic.blocks.copy();canvas=plane.canvas
 p=canvas.point((1,1,3)).toPoint();q=canvas.point((2,2,3)).toPoint();QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=p);QTest.mouseMove(canvas,q);QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=q);wait_for(lambda:ready(w))
 from ship_architect.space_model import contains
 w0,h,l=d.schematic.size;indices=np.array([y*w0*l+3*w0+x for y in (1,2) for x in (1,2)])
 assert not contains(d.active_space(),indices,d.schematic.size).any() and contains(d.active_space(),[1*w0*l+2*w0+1],d.schematic.size)[0] and np.array_equal(before,d.schematic.blocks)


def test_display_settings_restore_and_invalid_metadata_is_ignored(window,tmp_path):
 from ship_architect.display import validate_settings
 w=window;d=build_demo();w.set_document(d);b=w.workbench;b.cut_axis.setCurrentText('Y');b.cut_position.setValue(5);b.axis_plan.orientation.setCurrentText('후면도');w.space_manager.overlays.setChecked(False);b.block_selected((40,1,21));b.save_display()
 path=tmp_path/'visibility.shipproj';d.save_project(path);r=Document.open_project(path);w.set_document(r);wait_for(lambda:ready(w));assert b.cut_axis.currentText()=='Y' and b.cut_position.value()==5 and b.axis_plan.orientation.currentText()=='후면도' and not w.view.show_spaces
 assert validate_settings({'hidden':[True,-1,999999],'hidden_overlays':[[]],'isolation':'broken','position':'bad'},(4,4,4))=={'hidden':[],'hidden_overlays':[]}


def test_all_blocks_off_disables_picking_without_destroying_scene(window):
 w=window;d=build_demo();w.set_document(d);wait_for(lambda:ready(w));scene=w.view.mesh;w.space_manager.blocks.setChecked(False)
 assert w.view.pick(QPointF(10,10)) is None and w.view.mesh is scene and d.schematic.blocks.any()
 w.workbench.restore_visibility();wait_for(lambda:ready(w))


def test_two_tree_spaces_route_in_connection_mode(window):
 w,v=setup(window);v.analyze();wait_for(lambda:ready(w),30);b=w.workbench;b.rebuild_tree(True);b.mode.setCurrentText('연결·경로');b.tree.clearSelection();b.tree.setCurrentItem(b.nodes['space:A']);b.nodes['space:A'].setSelected(True)
 from PySide6.QtCore import QItemSelectionModel
 b.tree.setCurrentItem(b.nodes['space:B'],0,QItemSelectionModel.SelectionFlag.NoUpdate);b.nodes['space:B'].setSelected(True);wait_for(lambda:ready(w))
 assert w.view.route_path and v.source.currentData() in ('A','B') and v.target.currentData() in ('A','B')


def test_multiple_selection_and_fold_state_restore(window,tmp_path):
 w=window;d=build_demo();w.set_document(d);b=w.workbench;b.tree.clearSelection();b.nodes['space:A'].setSelected(True);b.nodes['space:B'].setSelected(True);b.tree.collapseAll();b.save_display();path=tmp_path/'selected.shipproj';d.save_project(path)
 restored=Document.open_project(path);w.set_document(restored);wait_for(lambda:ready(w));keys={i.data(0,Qt.ItemDataRole.UserRole) for i in b.tree.selectedItems()}
 assert {'space:A','space:B'}.issubset(keys) and b.expanded_keys()==[]


def test_ai_hidden_proposal_is_blocked_in_chat_and_not_presented(window,server):
 from test_v08_spaces import l_space
 from test_ai_design import plan,operation
 w=window;d,s=l_space();w.set_document(d);b=w.workbench;b.cut_axis.setCurrentText('Y');b.cut_position.setValue(2);client,h=server;p=w.ai_panel;p.url.setText(client.url);h.responses=[json.dumps(plan(operation('fill','minecraft:glass',hi=(7,4,7))))];reported=[];w.error=reported.append;p.prompt.setPlainText('유리로 채워줘');p.generate();wait_for(lambda:p.job is None and ready(w))
 entry=p.chat_store.get(d.project_id,p.current_trace)
 assert entry['status']=='blocked' and entry['actual']['applied'] is False and w.proposal is None and reported and not d.schematic.blocks.any()
