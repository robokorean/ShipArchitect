import json,copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import pytest
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from test_ai_gui import window,wait_for,ready
from test_ai_design import server,plan,operation
from create_v09_demo import build_demo

def click(widget,label):
 button=next(b for b in widget.findChildren(QPushButton) if b.text()==label and b.isVisible());QTest.mouseClick(button,Qt.MouseButton.LeftButton)

def setup(window):
 w=window;w.set_document(build_demo());wait_for(lambda:ready(w));w.tabs.setCurrentWidget(w.network_view);return w,w.network_view

def test_buttons_route_split_equipment_and_layout(window):
 w,v=setup(window);click(v,'연결 분석');wait_for(lambda:ready(w),30);assert v.nav is not None and v.connections.count()==len(w.doc.spaces)-1
 v.pages.setCurrentIndex(1);v.source.setCurrentIndex(v.source.findData('A'));v.target.setCurrentIndex(v.target.findData('B'));click(v,'경로 찾기');wait_for(lambda:ready(w));assert w.view.route_path and '경로' in v.status.text()
 w.split_mode.setChecked(True);wait_for(lambda:ready(w));assert w.tabs.currentWidget() is w.split_tab and w.design_splitter.count()==2
 assert w.view.isVisible() and w.space_manager.plan.isVisible() and w.view.width()>100 and w.space_manager.plan.width()>100
 w.layer.setValue(1);assert w.space_manager.plan.layer==1
 w.design_splitter.setSizes([650,350]);w.main_splitter.setSizes([800,400]);w.save_layout();saved=json.loads((w.data_dir/'layout.json').read_text());assert saved['split']
 w.split_mode.setChecked(False);w.restore_layout() # Toggle saved layout was intentionally updated; direct restore the retained snapshot.
 (w.data_dir/'layout.json').write_text(json.dumps(saved));w.restore_layout();wait_for(lambda:ready(w));assert w.split_mode.isChecked()
 w.tabs.setCurrentWidget(v);click(v,'경로 지우기');assert not w.view.route_path
 v.source.setCurrentIndex(v.source.findData('E'));v.target.setCurrentIndex(v.target.findData('F'));click(v,'경로 찾기');assert not w.view.route_path and '불확실' in v.status.text()
 v.pages.setCurrentIndex(2);v.filter.setText('computercraft');assert v.table.rowCount()==1 and v.table.item(0,4).text()=='미확인'
 w.split_mode.setChecked(False)


def test_space_search_sort_groups_multiselect_and_manual_geometry(window):
 w,v=setup(window);m=w.space_manager;before=copy.deepcopy(w.doc.spaces);w.tabs.setCurrentWidget(m)
 m.search.setText('계단');assert m.list.count()==2
 m.sort.setCurrentText('층별 그룹');assert m.list.count()==4 # 2 unselectable headers + two spaces
 m.search.clear();m.sort.setCurrentText('이름순');m.select_id('A');assert w.doc.active_space_id=='A'
 m.search.setText('중복');assert m.list.count()==1;m.search.clear();m.sort.setCurrentText('기존 순서')
 for i in range(m.list.count()):
  if m.list.item(i).data(Qt.ItemDataRole.UserRole) in ('A','A_DUP'):m.list.item(i).setSelected(True)
 assert m.selected_ids()=={'A','A_DUP'}
 assert w.doc.spaces==before


def test_block_change_invalidates_route_but_visibility_does_not(window):
 w,v=setup(window);v.analyze();wait_for(lambda:ready(w),30);old=v.nav
 w.space_manager.show_all(False);wait_for(lambda:ready(w));assert v.nav is old
 w.doc.schematic.blocks[1,4,8]=1;w.doc.log('approve',blocks=1);w.space_manager.refresh();assert v.nav is None and not w.view.route_path


def test_zero_change_claim_cannot_be_completed_or_approved(window,server):
 w,v=setup(window);m=w.space_manager;m.select_id('A');client,h=server;p=w.ai_panel;p.url.setText(client.url)
 raw=plan(operation('floor','minecraft:stone',hi=(0,0,0)));raw['summary']='공사를 완료했습니다'
 # Same material at a floor cell is still zero and must be no_change.
 w.doc.schematic.blocks[1,2,1]=1;h.responses=[json.dumps(raw)];p.prompt.setPlainText('바닥을 돌로 바꾸어줘');p.generate();wait_for(lambda:p.job is None and ready(w))
 e=p.chat_store.get(w.doc.project_id,p.current_trace)
 assert e['status']=='no_change' and e['actual']['applied'] is False and e['actual']['operation_diagnostics']
 assert not w.approve_button.isEnabled() and '미완료' in w.summary.toPlainText()


def test_manual_floor_reference_saved_and_removed(window,tmp_path):
 from ship_architect.model import Document
 w,v=setup(window);m=w.space_manager;before=w.doc.schematic.blocks.copy();w.layer.setValue(6);click(m,'현재 Y를 층으로 등록') if m.isVisible() else m.add_floor()
 assert 6 in m.levels and 6 in w.doc.floor_starts
 p=tmp_path/'floors.shipproj';w.doc.save_project(p);r=Document.open_project(p);assert 6 in r.floor_starts
 m.remove_floor();assert 6 not in w.doc.floor_starts and (w.doc.schematic.blocks==before).all()
