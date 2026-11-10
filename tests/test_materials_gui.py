import json
import numpy as np
from test_ai_gui import window,wait_for,ready
from test_ai_design import server,plan,operation
from ship_architect.schematic import load_file


def test_new_requested_palette_entry_preview_approval_and_save(window,server,tmp_path):
 w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url)
 h.responses=[json.dumps(plan(operation(material='minecraft:stone_bricks')))]
 p.prompt.setPlainText('석재 벽돌로 바닥을 만들어 줘');p.generate()
 wait_for(lambda:p.job is None and ready(w))
 assert w.proposal is not None,p.request_status.text()
 assert {w.doc.schematic.palette[int(i)] for i in w.proposal.after}=={'minecraft:stone_bricks'}
 assert 'minecraft:stone_bricks' in p.material_editor.counts.toPlainText()
 assert not np.any(w.doc.schematic.blocks)
 w.approve();wait_for(lambda:ready(w));path=tmp_path/'ui.schem';w.doc.export(path,approved=True);sc,_=load_file(path)
 assert {sc.palette[int(i)] for i in np.unique(sc.blocks) if i}=={'minecraft:stone_bricks'}


def test_edit_material_invalidates_stale_approval_and_recompiles(window,server,tmp_path):
 w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url);h.responses=[json.dumps(plan(operation(material='minecraft:oak_planks')))]
 p.prompt.setPlainText('참나무 판자로 바닥을 만들어 줘');p.generate();wait_for(lambda:p.job is None and ready(w))
 assert w.proposal is not None,p.request_status.text()
 editor=p.material_editor;editor.table.item(0,2).setText('석재 벽돌')
 assert w.proposal is None and not w.approve_button.isEnabled()
 editor.rebuild();wait_for(lambda:ready(w))
 assert w.proposal is not None,p.request_status.text()
 assert {w.doc.schematic.palette[int(i)] for i in w.proposal.after}=={'minecraft:stone_bricks'}
 assert not np.any(w.doc.schematic.blocks)
 w.approve();wait_for(lambda:ready(w));before=w.doc.schematic.blocks.copy()
 editor.table.item(0,2).setText('minecraft:not_a_block');editor.rebuild();wait_for(lambda:ready(w))
 assert w.proposal is None and not w.approve_button.isEnabled()
 assert np.array_equal(w.doc.schematic.blocks,before) and '실패' in p.request_status.text()


def test_ui_role_override_and_unknown_name_are_not_stone_fallback(window,server):
 w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url)
 p.material_editor.roles['floor'].setCurrentText('참나무 판자');p.prompt.setPlainText('작은 바닥 설계')
 h.responses=[json.dumps(plan(operation(material='minecraft:stone')))]*2
 p.generate();wait_for(lambda:p.job is None and ready(w))
 assert w.proposal is None and '불일치' in p.request_status.text() and not np.any(w.doc.schematic.blocks)
 p.material_editor.roles['floor'].setCurrentText('없는 블록');p.generate()
 assert p.job is None and '재료 해석 실패' in p.request_status.text() and len(h.requests)==2


def test_common_preview_cannot_replace_ai_material_with_manual_stone(window,server,tmp_path):
 w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url)
 w.material.setCurrentIndex(w.material.findData(2))  # manual material is stone
 h.responses=[json.dumps(plan(operation(material='minecraft:oak_planks')))]
 p.prompt.setPlainText('minecraft:oak_planks로 바닥');p.generate();wait_for(lambda:p.job is None and ready(w))
 assert w.proposal is not None,p.request_status.text()
 assert not w.preview_button.isEnabled()
 before=w.proposal;w.make_preview();assert w.proposal is before
 assert {w.doc.schematic.palette[int(i)] for i in w.proposal.after}=={'minecraft:oak_planks'}
 w.approve();wait_for(lambda:ready(w));path=tmp_path/'regression.schem';w.doc.export(path,approved=True);s,_=load_file(path)
 assert {s.palette[int(i)] for i in np.unique(s.blocks) if i}=={'minecraft:oak_planks'}
 assert w.preview_button.isEnabled()  # manual editor remains available


def test_new_document_restores_manual_preview_after_ai(window,server):
 from ship_architect.model import Document
 from ship_architect.schematic import Schematic
 w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url);h.responses=[json.dumps(plan(operation(material='minecraft:oak_planks')))]
 p.prompt.setPlainText('참나무 판자로 바닥');p.generate();wait_for(lambda:p.job is None and ready(w));assert not w.preview_button.isEnabled()
 w.set_document(Document(Schematic.empty(6,5,6)));wait_for(lambda:ready(w))
 assert w.proposal is None and w.preview_button.isEnabled()
 w.make_preview();wait_for(lambda:ready(w));assert w.proposal.origin=='manual'
