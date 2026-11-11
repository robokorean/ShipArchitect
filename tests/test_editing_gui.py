import os,time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
import pytest
from PySide6.QtCore import Qt,QEvent,QPoint,QEventLoop,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox
from test_ai_gui import left_mouse,wait_for
from ship_architect.app import MainWindow
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic


def wait_ready(w,seconds=12):
    def ready():return not w.loading and w.operation is None and not w.view.workers_running() and not w.view.render_timer.isActive()
    if ready():return
    loop=QEventLoop();poll=QTimer(loop);poll.setInterval(10)
    poll.timeout.connect(lambda:loop.quit() if ready() else None)
    end=QTimer(loop);end.setSingleShot(True);end.timeout.connect(loop.quit)
    poll.start();end.start(int(seconds*1000));loop.exec();poll.stop();end.stop()
    assert ready(),'asynchronous editor operation timed out'


@pytest.fixture
def w():
    app=QApplication.instance() or QApplication([])
    window=MainWindow();window.resize(1380,900);window.show();QTest.qWait(35);app.processEvents()
    def fail(error):raise AssertionError(str(error))
    window.error=fail
    yield window
    window.reject();wait_ready(window);window.doc.dirty=False;window.close();app.processEvents()


def test_real_3d_drag_selects_air_and_preserves_camera(w):
    w.select_mode.setCurrentIndex(1)
    wait_for(lambda: w.view.pick_buffer is not None, seconds=30)
    yaw=w.view.yaw
    screen=w.view.project([[2.5,0,3.5],[8.5,0,10.5]])
    a,b=(QPoint(round(x),round(y)) for x,y in screen)
    left_mouse(w.view,QEvent.Type.MouseButtonPress,a)
    left_mouse(w.view,QEvent.Type.MouseMove,b)
    left_mouse(w.view,QEvent.Type.MouseButtonRelease,b)
    assert w.doc.selection==Bounds((2,0,3),(8,0,10))
    assert w.view.yaw==yaw
    assert 'X 8 / Y 0 / Z 10' in w.inspect.text()


def test_before_after_preview_approval_history_and_undo(w):
    b=Bounds((2,1,3),(6,4,8));w.set_selection(b)
    w.mode.setCurrentIndex(w.mode.findData('add'))
    QTest.mouseClick(w.preview_button,Qt.MouseButton.LeftButton)
    wait_ready(w)
    assert not np.any(w.doc.schematic.blocks)
    assert np.count_nonzero(w.view.blocks)==b.volume
    w.compare.setCurrentIndex(1);wait_ready(w)
    assert not np.any(w.view.blocks)
    assert w.view.preview_side=='변경 전'
    w.compare.setCurrentIndex(0);wait_ready(w)
    assert np.count_nonzero(w.view.blocks)==b.volume
    QTest.mouseClick(w.approve_button,Qt.MouseButton.LeftButton);wait_ready(w)
    assert np.count_nonzero(w.doc.schematic.blocks)==b.volume
    assert '승인' in w.history_view.toPlainText()
    w.undo();wait_ready(w);assert not np.any(w.doc.schematic.blocks)
    w.redo();wait_ready(w);assert np.count_nonzero(w.doc.schematic.blocks)==b.volume
    assert '다시 실행' in w.history_view.toPlainText()


def test_catalogue_source_filter_and_preview_cancellation(w):
    w.catalog.setCurrentText('minecraft:red_concrete');w.add_material()
    target=w.material.currentData()
    b=Bounds((1,1,1),(2,2,2));w.set_selection(b)
    w.mode.setCurrentIndex(w.mode.findData('add'));w.make_preview();w.approve();wait_ready(w)
    w.material.setCurrentIndex(w.material.findData(2))
    w.mode.setCurrentIndex(w.mode.findData('replace'))
    w.source_material.setCurrentIndex(w.source_material.findData(target))
    w.make_preview();wait_ready(w)
    assert len(w.proposal.indices)==8 and w.proposal.source_id==target
    w.reject();wait_ready(w)
    assert np.all(w.doc.schematic.blocks[b.slices]==target)


def test_exterior_confirmation_can_cancel_then_approve(w,tmp_path,monkeypatch):
    s=Schematic.empty(8,8,8);s.blocks[2:6,2:6,2:6]=1
    path=tmp_path/'original.schem';path.write_bytes(s.to_bytes())
    d=Document(s,path.read_bytes(),path);d.prepare_protection()
    w.set_document(d);w.set_selection(Bounds((2,3,3),(2,3,3)))
    w.material.setCurrentIndex(w.material.findData(2))
    w.mode.setCurrentIndex(w.mode.findData('replace'));w.allow_exterior.setChecked(True)
    w.make_preview();wait_ready(w)
    assert w.proposal.exterior_affected==1
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No)
    w.approve();assert s.blocks[3,3,2]==1 and w.proposal is not None
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    w.approve();wait_ready(w);assert s.blocks[3,3,2]==2


def test_late_preview_is_discarded_after_selection_change(w):
    old=w.doc.selection
    p=w.doc.propose(old,1,'floor')
    epoch=w.preview_epoch
    w.set_selection(Bounds((1,1,1),(2,2,2)))
    w._preview_ready(p,epoch)
    assert w.proposal is None and not np.any(w.doc.schematic.blocks)


def test_selection_focus_fits_complete_3d_bounds(w):
    b=Bounds((0,4,0),(15,15,15));w.set_selection(b);w.view.focus_selection()
    wait_ready(w)
    lo,hi=np.array(b.lo),np.array(b.hi)+1
    vertices=np.array([[hi[i] if n>>i&1 else lo[i] for i in range(3)] for n in range(8)])
    pixels=w.view.project(vertices)
    assert pixels[:,0].min()>=0 and pixels[:,0].max()<=w.view.width()
    assert pixels[:,1].min()>=0 and pixels[:,1].max()<=w.view.height()
