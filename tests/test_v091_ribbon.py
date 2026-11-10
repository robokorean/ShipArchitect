"""Actual consolidated UI routes, ribbon activation, shared view state and dock persistence."""
import copy,json
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolBar
from PySide6.QtTest import QTest
from test_ai_gui import window,ready,wait_for
from test_v09_gui import build_demo
from ship_architect.display import overlay_rectangles,overlay_indices_visible
from ship_architect.model import Bounds


def category(w,name):
 ui=w.integrated_ui;index=ui.CATEGORIES.index(name);QTest.mouseClick(ui.categories,Qt.MouseButton.LeftButton,pos=ui.categories.tabRect(index).center());assert ui.pages.currentIndex()==index


def press(w,category_name,text):
 category(w,category_name);button=w.integrated_ui.buttons[(category_name,text)];assert button.isVisible();QTest.mouseClick(button,Qt.MouseButton.LeftButton)


def test_ribbon_categories_only_show_the_selected_tools(window):
 w=window;ui=w.integrated_ui
 assert [ui.categories.tabText(i) for i in range(6)]==['프로젝트','공간','설계','분석','보기','도구']
 for name in ui.CATEGORIES:
  category(w,name)
  for (page,text),button in ui.buttons.items():assert button.isVisible()==(page==name),(page,text)
 assert not w.toolbar.isVisible() and not any(b.isVisible() for b in w.workbench.modebars)
 category(w,'도구');assert ui.buttons[('도구','오류 진단 ZIP')].isVisible()
 category(w,'공간');assert not ui.buttons[('도구','오류 진단 ZIP')].isVisible()


def test_one_explorer_one_drawing_and_properties_no_duplicate_tabs(window):
 w=window;ui=w.integrated_ui;b=w.workbench;m=w.space_manager
 assert b.scene_dock.windowTitle()=='장면 탐색기' and b.editor_dock.windowTitle()=='속성 편집기'
 assert [w.tabs.tabText(i) for i in range(w.tabs.count())]==['3D 함선','공통 도면 편집기']
 assert m.list.isHidden() and m.search.isHidden() and m.sort.isHidden() and m.name.isHidden()
 assert w.tabs.indexOf(m)<0 and w.tabs.indexOf(m.plan)<0 and ui.plane_stack.indexOf(m.plan)==0
 w.tabs.setCurrentWidget(m);assert ui.properties.currentWidget() is m and b.editor_dock.isVisible()
 w.tabs.setCurrentWidget(m.plan);assert w.tabs.currentWidget() is b.axis_plan and m.plan.isVisible()
 w.tabs.setCurrentWidget(w.network_view);assert ui.analysis_dock.isVisible() and w.tabs.indexOf(w.network_view)<0


def test_ribbon_create_edit_merge_split_and_undo_preserve_blocks(window):
 w=window;d=w.doc;b=w.workbench;ui=w.integrated_ui;before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest()
 w.set_selection(Bounds((1,1,1),(3,3,3)));press(w,'공간','공간 추가');first=d.active_space_id
 w.set_selection(Bounds((4,1,1),(6,3,3)));press(w,'공간','공간 추가');second=d.active_space_id
 b.property_name.setText('격납고 A');b.property_purpose.setText('격납고');b.apply_properties();assert d.active_space()['name']=='격납고 A'
 b.rebuild_tree(True);b.tree.clearSelection();b.nodes['space:'+first].setSelected(True);b.nodes['space:'+second].setSelected(True);press(w,'공간','합치기');assert len(d.spaces)==1
 w.set_selection(Bounds((1,1,1),(3,3,3)));press(w,'공간','분리');assert len(d.spaces)==2
 w.undo();assert len(d.spaces)==1;w.redo();assert len(d.spaces)==2
 assert np.array_equal(before,d.schematic.blocks) and d.schematic.metadata_digest()==meta


def test_common_editor_orientation_projection_and_split_detach(window):
 w=window;ui=w.integrated_ui;a=w.workbench.axis_plan
 press(w,'보기','공통 도면');assert ui.plane_stack.currentWidget() is w.space_manager.plan
 a.orientation.setCurrentText('정면도');assert ui.plane_stack.currentWidget() is a.canvas
 a.orientation.setCurrentText('평면도 (위)');a.projection.setChecked(True);assert ui.plane_stack.currentWidget() is a.canvas
 a.projection.setChecked(False);a.slice.setValue(3);assert w.layer.value()==3 and w.space_manager.plan.layer==3
 w.layer.setValue(4);assert a.slice.value()==4
 press(w,'보기','2D/3D 동시 보기');wait_for(lambda:ready(w));assert w.view.isVisible() and w.space_manager.plan.isVisible() and w.design_splitter.orientation()==Qt.Orientation.Vertical
 press(w,'보기','2D 분리');assert a.isVisible() and w.workbench.floating['2D'].isFloating()
 w.workbench.attach_view('2D');assert a.parent() is w.design_splitter and w.design_splitter.count()==2
 w.split_mode.setChecked(False)


def test_shared_visibility_and_clipped_space_annotations_do_not_change_data(window):
 w=window;d=build_demo();w.set_document(d);b=w.workbench;before=d.schematic.blocks.copy();shapes=copy.deepcopy([s['runs'] for s in d.spaces]);meta=d.schematic.metadata_digest()
 s=d.spaces[0];y=s['runs'][0][0];b.cut_axis.setCurrentText('X');b.cut_position.setValue(5);b.cut_side.setCurrentText('below')
 rects=overlay_rectangles(s,y,d,w.view);assert all(r[2]<=6 for r in rects)
 w.space_manager.blocks.setChecked(False);assert np.any(overlay_indices_visible(np.array([y*d.schematic.size[0]*d.schematic.size[2]+3*d.schematic.size[0]+3]),d,w.view))
 press(w,'보기','표시 전체 복원');wait_for(lambda:ready(w))
 assert np.array_equal(before,d.schematic.blocks) and d.schematic.metadata_digest()==meta and shapes==[s['runs'] for s in d.spaces]


def test_ribbon_analysis_and_model_path_are_connected(window):
 w=window;d=build_demo();w.set_document(d);press(w,'분석','연결 분석');wait_for(lambda:ready(w),30);assert w.network_view.nav is not None and w.integrated_ui.analysis_dock.isVisible()
 b=w.workbench;b.rebuild_tree(True);b.tree.clearSelection();b.nodes['space:A'].setSelected(True);b.nodes['space:B'].setSelected(True);press(w,'분석','두 공간 경로');wait_for(lambda:ready(w));assert w.view.route_path
 press(w,'분석','경로 지우기');assert not w.view.route_path
 press(w,'분석','설비 분석');wait_for(lambda:ready(w));assert w.network_view.table.rowCount()>0


def test_ribbon_preferences_restore_and_group_recovery(window):
 w=window;ui=w.integrated_ui;category(w,'공간');key='공간 · 가져오기';action=next(a for a in w.workbench.toolmenu.actions() if a.text()==key);action.setChecked(False);ui.save();saved=json.loads(ui.path.read_text());assert saved['category']==1 and key in saved['hidden_groups']
 action.setChecked(True);ui.path.write_text(json.dumps(saved));ui.restore();assert ui.groups[key].isHidden()
 ui.restore_groups();assert not ui.groups[key].isHidden()
 w.workbench.save();assert (w.data_dir/'workspace091.json').exists()


def test_busy_ribbon_dispatch_and_disabled_approval_cannot_change_document(window):
 w=window;ui=w.integrated_ui;before=w.doc.schematic.blocks.copy();spaces=copy.deepcopy(w.doc.spaces);w.loading=True
 try:
  ui.buttons[('공간','공간 추가')].defaultAction().trigger();ui.buttons[('프로젝트','실행 취소')].defaultAction().trigger()
  assert w.doc.spaces==spaces and np.array_equal(before,w.doc.schematic.blocks)
 finally:w.loading=False
 ui.sync_actions();assert not ui.buttons[('설계','승인·적용')].isEnabled()


def test_ribbon_manual_preview_approval_export_undo(window,tmp_path):
 from ship_architect.model import Document
 from ship_architect.schematic import Schematic,load_file
 w=window;w.set_document(Document(Schematic.empty(8,6,10)));w.set_selection(Bounds((2,1,2),(4,1,4)));w.material.setCurrentIndex(w.material.findData(1))
 press(w,'설계','변경 미리보기');wait_for(lambda:ready(w));assert w.proposal is not None and len(w.proposal.indices)==9 and not w.doc.schematic.blocks.any()
 w.integrated_ui.sync_actions();press(w,'설계','승인·적용');wait_for(lambda:ready(w));assert np.count_nonzero(w.doc.schematic.blocks)==9
 out=tmp_path/'approved.schem';w.doc.export(out,approved=True);sc,_=load_file(out);assert np.array_equal(sc.blocks,w.doc.schematic.blocks)
 press(w,'프로젝트','실행 취소');wait_for(lambda:ready(w));assert not w.doc.schematic.blocks.any()
 press(w,'프로젝트','다시 실행');wait_for(lambda:ready(w));assert np.count_nonzero(w.doc.schematic.blocks)==9
