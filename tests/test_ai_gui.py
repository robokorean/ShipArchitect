import os,json,threading
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
import pytest
from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication
from ship_architect.app import MainWindow
from ship_architect.ai_design import generate_design
from ship_architect.model import Bounds
from test_ai_design import server,plan,operation


def wait_for(predicate,seconds=15):
    if predicate():return
    loop=QEventLoop();timer=QTimer(loop);timer.setInterval(10);timer.timeout.connect(lambda:loop.quit() if predicate() else None)
    end=QTimer(loop);end.setSingleShot(True);end.timeout.connect(loop.quit);timer.start();end.start(int(seconds*1000));loop.exec()
    assert predicate(),'AI GUI timeout'


def ready(w):return not w.view.workers_running() and not w.view.render_timer.isActive() and w.operation is None


@pytest.fixture
def window():
    app=QApplication.instance() or QApplication([]);w=MainWindow();w.show();w.ai_panel.timer.stop()
    old={key:w.ai_panel.settings.value('ai/'+key) for key in ('url','model','timeout')}
    errors=[];w.error=errors.append
    wait_for(lambda:ready(w) and not w.ai_panel.busy())
    w.set_selection(Bounds((2,1,2),(7,5,7)));wait_for(lambda:ready(w))
    yield w
    w.ai_panel.cancel();wait_for(lambda:not w.ai_panel.busy());w.reject();wait_for(lambda:ready(w))
    w.doc.dirty=False;w.close();app.processEvents()
    for key,value in old.items():
        if value is None:w.ai_panel.settings.remove('ai/'+key)
        else:w.ai_panel.settings.setValue('ai/'+key,value)
    assert not errors,errors


def test_ai_status_preview_manual_approval_and_refine(window,server):
    w=window;client,h=server;panel=w.ai_panel
    panel.url.setText(client.url);panel.probe();wait_for(lambda:panel.probe_job is None)
    assert '연결됨' in panel.server_status.text()
    h.responses=[json.dumps(plan(operation(),operation('ceiling','minecraft:glass')))]
    panel.prompt.setPlainText('흰색 바닥과 유리 천장');panel.generate()
    assert w.editor_panel.isEnabled() and w.preview_button.isEnabled()
    wait_for(lambda:panel.job is None and ready(w))
    assert w.proposal is not None,panel.request_status.text()
    assert w.proposal.origin=='ai' and not np.any(w.doc.schematic.blocks)
    assert len(np.unique(w.proposal.after))==2 and np.any(w.view.blocks)
    w.compare.setCurrentIndex(1);wait_for(lambda:ready(w));assert not np.any(w.view.blocks)
    w.compare.setCurrentIndex(0);wait_for(lambda:ready(w))
    h.responses=[json.dumps(plan(operation('floor','minecraft:gray_concrete'),operation('ceiling','minecraft:glass')))]
    panel.prompt.setPlainText('회색 바닥으로 수정');panel.generate(True);wait_for(lambda:panel.job is None and ready(w))
    assert not np.any(w.doc.schematic.blocks) and 'gray_concrete' in panel.plan_text.toPlainText()
    w.approve();wait_for(lambda:ready(w));assert len(w.doc.undo_stack)==1 and 'AI 설계' in w.history_view.toPlainText()
    final=w.doc.schematic.blocks.copy();w.undo();wait_for(lambda:ready(w));assert not np.any(w.doc.schematic.blocks)
    w.redo();wait_for(lambda:ready(w));assert np.array_equal(final,w.doc.schematic.blocks)


def test_offline_failure_keeps_manual_editor_usable(window):
    w=window;p=w.ai_panel;p.url.setText('http://127.0.0.1:1');p.probe();wait_for(lambda:p.probe_job is None)
    assert '실패' in p.server_status.text()
    p.prompt.setPlainText('방 만들기');p.generate();wait_for(lambda:p.job is None)
    assert '실패' in p.request_status.text() and w.editor_panel.isEnabled()
    w.make_preview();wait_for(lambda:ready(w));assert w.proposal is not None
    w.approve();wait_for(lambda:ready(w));assert np.any(w.doc.schematic.blocks)


def test_stale_response_after_selection_change_is_discarded(window,server):
    w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url);h.delay=.4;h.responses=[json.dumps(plan(operation()))]
    p.prompt.setPlainText('흰색 바닥');p.generate();w.set_selection(Bounds((1,1,1),(4,4,4)))
    wait_for(lambda:p.job is None and ready(w));assert w.proposal is None and not np.any(w.doc.schematic.blocks)
    assert '폐기' in p.request_status.text()


def test_cancellation_preserves_pending_manual_work(window,server):
    w=window;client,h=server;p=w.ai_panel;p.url.setText(client.url);h.delay=.7
    p.prompt.setPlainText('방');p.generate();p.cancel();wait_for(lambda:p.job is None)
    assert not np.any(w.doc.schematic.blocks) and p.generate_button.isEnabled()
    w.make_preview();wait_for(lambda:ready(w));assert w.proposal is not None
