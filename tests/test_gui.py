import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_ai_gui import left_mouse,wait_for,ready
from ship_architect.app import MainWindow
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    w = MainWindow()
    def fail(error):
        raise AssertionError(str(error))
    w.error = fail
    w.resize(1280, 850)
    w.show()
    app.processEvents()
    yield w
    w.proposal = None
    w.doc.dirty = False
    w.close()
    app.processEvents()


def test_preview_approval_undo_redo_real_buttons(window, app):
    w = window
    w.set_document(Document(Schematic.empty(8, 6, 10)), 'GUI TEST')
    w.select_layer()
    w.material.setCurrentIndex(w.material.findData(1))
    QTest.mouseClick(w.preview_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert w.proposal is not None and len(w.proposal.indices) == 80
    assert np.count_nonzero(w.doc.schematic.blocks) == 0
    assert w.view.pending
    assert len(w.view.mesh) == 6
    QTest.mouseClick(w.approve_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert np.count_nonzero(w.doc.schematic.blocks) == 80
    assert not w.view.pending
    w.undo()
    assert not np.any(w.doc.schematic.blocks)
    w.redo()
    assert np.count_nonzero(w.doc.schematic.blocks) == 80


def test_layer_drag_selects_air(window, app):
    w = window
    w.tabs.setCurrentIndex(1)
    app.processEvents()
    w.plan.layout_map()
    def point(x, z):
        return QPoint(round(w.plan.ox+(x+.5)*w.plan.scale), round(w.plan.oz+(z+.5)*w.plan.scale))
    a, b = point(2, 3), point(8, 10)
    QTest.mousePress(w.plan, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(w.plan, b)
    QTest.mouseRelease(w.plan, Qt.MouseButton.LeftButton, pos=b)
    app.processEvents()
    assert w.doc.selection == Bounds((2, 0, 3), (8, 0, 10))


def test_3d_pick_rotation_zoom_and_clip(window, app):
    w = window
    d = Document(Schematic.empty(4, 4, 4))
    d.schematic.blocks[:] = 1
    w.set_document(d)
    wait_for(lambda: ready(w) and bool(w.view.hits), seconds=30)
    assert len(w.view.mesh) == 6 and w.view.hits
    polygon, quad, normal = w.view.hits[-1]
    point = polygon.boundingRect().center()
    xyz = w.view.pick(point)
    assert xyz is not None
    axis = int(np.argmax(np.abs(normal)))
    assert xyz[axis] == (3 if normal[axis] > 0 else 0)
    initial = w.view.yaw
    left_mouse(w.view,QEvent.Type.MouseButtonPress,QPoint(250,220))
    left_mouse(w.view,QEvent.Type.MouseMove,QPoint(310,250))
    left_mouse(w.view,QEvent.Type.MouseButtonRelease,QPoint(310,250))
    assert w.view.yaw != initial
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF
    previous_zoom = w.view.zoom
    wheel = QWheelEvent(QPointF(250, 220), QPointF(250, 220), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(w.view, wheel)
    assert w.view.zoom > previous_zoom
    w.layer.setValue(1)
    w.clip.setChecked(True)
    app.processEvents()
    assert max(float(q[:,1].max()) for q,n,i in w.view.mesh) == 2
    assert np.count_nonzero(d.schematic.blocks) == 64


def test_coordinates_and_named_zone(window, app):
    w = window
    for spins, values in zip(w.coords, [(7, 5, 12), (2, 1, 3)]):
        for s, v in zip(spins, values):
            s.setValue(v)
    w.coordinates_selected()
    assert w.doc.selection == Bounds((2, 1, 3), (7, 5, 12))
    w.zone_name.setText('격납고')
    w.add_zone()
    assert w.zone_list.count() == 1
    w.select_all()
    w.zone_selected(w.zone_list.item(0))
    assert w.doc.selection == Bounds((2, 1, 3), (7, 5, 12))
    w.zone_list.setCurrentRow(0)
    w.delete_zone()
    assert not w.doc.zones


def test_controls_invalidate_preview_and_cancel_restores(window, app):
    w = window
    w.select_layer()
    w.make_preview()
    assert w.proposal
    w.material.setCurrentIndex(w.material.findData(2))
    assert w.proposal is None and not w.view.pending
    w.make_preview()
    w.reject()
    assert not np.any(w.doc.schematic.blocks)
    assert not w.approve_button.isEnabled()


def test_async_import(window, app, tmp_path):
    path = tmp_path/'sample.schem'
    s = Schematic.empty(5, 4, 9)
    s.blocks[0] = 1
    path.write_bytes(s.to_bytes())
    window.load_path(path)
    for _ in range(200):
        app.processEvents()
        if not window.loader.isRunning():
            break
        QTest.qWait(10)
    app.processEvents()
    assert window.doc.schematic.size == (5, 4, 9)
    assert window.centralWidget().isEnabled()
    assert np.count_nonzero(window.doc.schematic.blocks) == 45


def test_corrupt_file_keeps_open_document(window, app, tmp_path):
    previous = window.doc
    errors = []
    window.error = errors.append
    path = tmp_path/'broken.schem'
    path.write_bytes(b'not a schematic')
    window.load_path(path)
    for _ in range(200):
        app.processEvents()
        if not window.loader.isRunning():
            break
        QTest.qWait(10)
    app.processEvents()
    assert errors and window.doc is previous
    assert window.toolbar.isEnabled()


def test_new_dialog_and_file_actions(window, app, monkeypatch, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox, QFileDialog
    from ship_architect import app as gui
    Base = gui.NewDialog
    class AutoDialog(Base):
        def __init__(self, parent):
            super().__init__(parent)
            for spin, value in zip(self.fields, [12, 8, 20, 3955]):
                spin.setValue(value)
            QTimer.singleShot(0, self.accept)
    monkeypatch.setattr(gui, 'NewDialog', AutoDialog)
    window.new_project()
    assert window.doc.schematic.size == (12, 8, 20)
    window.select_layer()
    window.make_preview()
    window.approve()
    project = tmp_path/'ui.shipproj'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(project), ''))
    assert window.save_project()
    restored = Document.open_project(project)
    assert np.count_nonzero(restored.schematic.blocks) == 240
    output = tmp_path/'ui.schem'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(output), ''))
    window.export()
    assert np.array_equal(Schematic.from_bytes(output.read_bytes()).blocks, restored.schematic.blocks)


def test_3d_shift_click_two_corners(window, app):
    d = Document(Schematic.empty(4, 4, 4))
    d.schematic.blocks[:] = 1
    window.set_document(d)
    wait_for(lambda: ready(window) and bool(window.view.hits), seconds=30)
    polygon, quad, normal = window.view.hits[-1]
    screen = window.view.project([quad.mean(axis=0)])[0]
    point = QPoint(round(screen[0]), round(screen[1]))
    expected = window.view.pick(point)
    QTest.mouseClick(window.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point)
    assert window.corner == expected
    QTest.mouseClick(window.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point)
    assert window.corner is None
    assert window.doc.selection == Bounds(expected, expected)


def test_camera_horizontal_direction_and_persisted_inversion(window,app):
    from PySide6.QtCore import QSettings
    settings=QSettings('ShipArchitect','ShipArchitect')
    old=settings.value('camera/invert_x',False,type=bool)
    w=window
    try:
        w.invert_x.setChecked(False)
        start=w.view.yaw
        QTest.mousePress(w.view,Qt.MouseButton.LeftButton,pos=QPoint(220,220))
        QTest.mouseMove(w.view,QPoint(280,220))
        QTest.mouseRelease(w.view,Qt.MouseButton.LeftButton,pos=QPoint(280,220))
        assert w.view.yaw<start
        w.invert_x.setChecked(True)
        start=w.view.yaw
        QTest.mousePress(w.view,Qt.MouseButton.LeftButton,pos=QPoint(220,220))
        QTest.mouseMove(w.view,QPoint(280,220))
        QTest.mouseRelease(w.view,Qt.MouseButton.LeftButton,pos=QPoint(280,220))
        assert w.view.yaw>start
        assert settings.value('camera/invert_x',False,type=bool)
    finally:
        w.invert_x.setChecked(old)
        settings.setValue('camera/invert_x',old)


def test_finished_but_undelivered_mesh_job_keeps_latest_request(window,app):
    w=window
    wait_for(lambda: ready(w), seconds=30)
    d=Document(Schematic.empty(64,40,48))
    d.schematic.blocks[:]=1
    w.set_document(d)
    first=w.view.mesh_worker
    assert first is not None
    # Finish worker without pumping queued result/finished slots (test only).
    assert first.wait(10000)
    w.view.cut=True;w.view.layer=2
    w.view.rebuild()
    assert w.view.mesh_worker is first  # must not overwrite the pending worker
    wait_for(lambda: ready(w), seconds=30)
    assert w.view.frame is not None
    assert max(float(q[:,1].max()) for q,n,i in w.view.mesh)==3


def test_header_distinguishes_volume_air_and_placed(window,app):
    d=Document(Schematic.empty(8,6,10));d.schematic.blocks[0]=1
    window.set_document(d)
    assert '480칸' in window.heading.text()
    assert '80블록' in window.heading.text()
    assert '공기 400' in window.analysis.text()


def test_cancel_large_load_retains_previous_document(window,app,tmp_path):
    from PySide6.QtCore import QTimer,QEventLoop
    previous=window.doc
    path=tmp_path/'large.schem'
    s=Schematic.empty(128,96,128);path.write_bytes(s.to_bytes())
    window.load_path(path)
    window.cancel_tasks()
    loop=QEventLoop();poll=QTimer(loop);poll.setInterval(10)
    poll.timeout.connect(lambda:loop.quit() if not window.loading else None)
    timeout=QTimer(loop);timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
    poll.start();timeout.start(10000);loop.exec();poll.stop();timeout.stop()
    assert not window.loading
    assert window.load_cancelled
    assert window.doc is previous
    assert window.toolbar.isEnabled()
