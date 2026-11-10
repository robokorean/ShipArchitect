import json
import numpy as np
from dataclasses import replace
from test_ai_gui import window,wait_for,ready
from test_ai_design import server,plan,operation
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.ai_design import build_context,compile_plan
from ship_architect.spaces import recognize


def test_chat_request_result_approval_cancel_error_and_reload(window,server,tmp_path):
 w=window;p=w.ai_panel;client,h=server;p.url.setText(client.url);h.responses=[json.dumps(plan(operation(material='minecraft:oak_planks')))]
 p.prompt.setPlainText('참나무 판자로 바닥');p.generate();wait_for(lambda:p.job is None and ready(w))
 trace=p.current_trace;row=p.chat_store.get(w.doc.project_id,trace)
 assert row['status']=='preview' and row['responses'] and row['actual']['changed']==36
 assert w.proposal.trace_id==trace
 p.chat_view.refresh(True);p.chat_view.copy()
 # Copying does not affect the active proposal; loading cancels it (separate test).
 w.approve();wait_for(lambda:ready(w));assert p.chat_store.get(w.doc.project_id,trace)['status']=='approved'
 w.undo();wait_for(lambda:ready(w));assert p.chat_store.get(w.doc.project_id,trace)['status']=='undone'
 # A new request is a distinct work ID linked to the original.
 h.responses=[json.dumps(plan(operation(material='minecraft:oak_planks')))];p.chat_view.rerun();wait_for(lambda:p.job is None and ready(w));new=p.current_trace
 assert new!=trace and p.chat_store.get(w.doc.project_id,new)['parent_trace']==trace
 w.reject();wait_for(lambda:ready(w));assert p.chat_store.get(w.doc.project_id,new)['status']=='cancelled'
 p.prompt.setPlainText('바닥은 minecraft:unknown');p.generate();assert p.chat_store.get(w.doc.project_id,p.current_trace)['status']=='error'
 path=tmp_path/'chat.shipproj';w.doc.save_project(path);restored=Document.open_project(path);identity=w.doc.project_id;w.set_document(restored);wait_for(lambda:ready(w))
 assert restored.project_id==identity and p.chat_store.get(identity,trace)['status']=='undone'
 p.chat_view.search.setText('unknown');assert p.chat_view.list.count()==1


def test_model_claim_is_separate_from_zero_actual_changes(window,server):
 w=window;p=w.ai_panel;client,h=server;p.url.setText(client.url);w.doc.add_zone('locked',w.doc.selection,True)
 raw=plan(operation('fill','minecraft:stone'));raw['summary']='83051개를 완성했습니다'
 h.responses=[json.dumps(raw)];p.prompt.setPlainText('선택구역 돌로 채워줘');p.generate();wait_for(lambda:p.job is None and ready(w))
 row=p.chat_store.get(w.doc.project_id,p.current_trace)
 assert row['status']=='no_change' and row['actual']['changed']==0 and not np.any(w.doc.schematic.blocks)
 p.chat_view.refresh(True);assert '83051' in p.chat_view.browser.toPlainText() and 'Python 실제 결과' in p.chat_view.browser.toPlainText()
 text=p.chat_view.browser.toPlainText();assert text.index('Python 실제 결과')<text.index('AI 응답')
 assert not w.approve_button.isEnabled()


def test_hull_is_visible_in_change_map_but_cannot_be_approved(window):
 w=window;doc=Document(Schematic.empty(40,20,50));w.set_document(doc);b=doc.selection;ctx=build_context(doc,b)
 layout=plan(operation('walls','minecraft:stone',hi=(39,19,49)),operation('floor','minecraft:stone',hi=(39,19,49)),operation('ceiling','minecraft:stone',hi=(39,19,49)))
 p=compile_plan(doc,b,layout,ctx,doc.revision);w._preview_ready(p);wait_for(lambda:ready(w))
 assert w.review_view.report['hull_risk'] and w.review_view.report['changed']>1000
 assert w.integrated_ui.tasks.bottom.currentWidget() is w.review_view and w.review_view.isVisible() and not w.approve_button.isEnabled() and not np.any(doc.schematic.blocks)


def test_space_candidates_overlay_confirm_and_zone_undo(window):
 w=window;s=Schematic.empty(14,8,14);s.blocks[0,2:12,2:12]=1;s.blocks[6,2:12,2:12]=1;s.blocks[1:6,2,2:12]=1;s.blocks[1:6,11,2:12]=1;s.blocks[1:6,2:12,2]=1;s.blocks[1:6,2:12,11]=1
 d=Document(s);w.set_document(d);before=s.blocks.copy();result=recognize(d,d.selection);w.space_view.ready(result,d)
 assert d.space_candidates and w.view.candidate_boxes
 w.space_view.list.setCurrentRow(0);w.space_view.select();wait_for(lambda:ready(w));assert w.doc.selection.lo[1]==1
 w.space_view.confirm();assert d.zones and d.zones[-1]['space_footprint'] and np.array_equal(before,s.blocks)
 w.undo();wait_for(lambda:ready(w));assert not d.zones and np.array_equal(before,s.blocks)


def test_close_large_document_does_not_restart_cancelled_analysis(window):
 w=window;d=Document(Schematic.empty(100,64,100));w.set_document(d);d.dirty=False;w.close()
 wait_for(lambda:not w.isVisible(),10)
 assert w.closing and w.analysis_worker is None


def test_loading_history_never_leaves_old_preview_approvable(window,server):
 w=window;p=w.ai_panel;client,h=server;p.url.setText(client.url);h.responses=[json.dumps(plan(operation(material='minecraft:oak_planks')))]
 p.prompt.setPlainText('참나무 판자로 바닥');p.generate();wait_for(lambda:p.job is None and ready(w));trace=p.current_trace;p.chat_view.refresh(True)
 assert w.proposal is not None and p.chat_view.load()
 assert w.proposal is None and not w.approve_button.isEnabled() and not np.any(w.doc.schematic.blocks)
 assert p.chat_store.get(w.doc.project_id,trace)['status']=='cancelled'
 assert p.last_plan and not p.material_editor.rebuild_button.isEnabled()
