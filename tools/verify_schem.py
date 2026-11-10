"""Run on a real schematic: python tools/verify_schem.py PATH --output DIR.
Never edits input. Output is a report and screenshots, not a copy of private NBT.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import nbtlib as nbt
from PySide6.QtCore import QTimer,Qt,QPoint,QEventLoop
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ship_architect.app import MainWindow,STYLE
from ship_architect.schematic import load_file,decode_varints
from ship_architect.model import Document,Bounds
from ship_architect.mesh import build_scene,MeshCache


def serialized(tree):
    stream=io.BytesIO();tree.write(stream);return stream.getvalue()


def pump_until(predicate,seconds):
    # Run the genuine Qt event loop. Repeated QTest.qWait can starve Python
    # worker threads on some PySide builds and distort throughput measurements.
    loop=QEventLoop()
    poll=QTimer(loop);poll.setInterval(10)
    poll.timeout.connect(lambda:loop.quit() if predicate() else None)
    deadline=QTimer(loop);deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start();deadline.start(int(seconds*1000))
    loop.exec()
    poll.stop();deadline.stop()
    assert predicate(),'Qt operation timed out'


def verify(path,output):
    output.mkdir(parents=True,exist_ok=True)
    before_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    stages=[]
    start=time.perf_counter()
    sc,raw=load_file(path,progress=lambda n,s:stages.append((n,s)))
    loaded=time.perf_counter()
    original=nbt.load(path)
    root=original.get('Schematic',original)
    container=root['Blocks'] if int(root['Version'])==3 else root
    # Independent legacy scalar decoder vs bounded vector decoder: every block.
    independent=decode_varints(container['Data' if int(root['Version'])==3 else 'BlockData'],sc.blocks.size)
    assert np.array_equal(independent,sc.blocks.reshape(-1))
    del independent
    doc=Document(sc,raw,str(path))
    stats=doc.analyze(doc.selection)
    cache=MeshCache()
    mstart=time.perf_counter();scene=build_scene(sc,cache=cache);mesh_seconds=time.perf_counter()-mstart
    repeat=build_scene(sc,cache=cache)
    assert repeat.stats['cache_hits']==repeat.stats['chunks']
    with tempfile.TemporaryDirectory(prefix='shiparchitect-verify-') as tmp:
        export=Path(tmp)/'export.schem'
        doc.export(export,approved=True)
        check=nbt.load(export)
        assert serialized(original)==serialized(check),'Typed NBT tree including complete Data must match'
        project=Path(tmp)/'test.shipproj'
        doc.save_project(project)
        reopened=Document.open_project(project)
        assert np.array_equal(reopened.schematic.blocks,sc.blocks)
        assert reopened.original==raw
        # A one-block COW preview must not write to approved data or its disk store.
        air_ids=[i for i,s in sc.palette.items() if s=='minecraft:air']
        flat=sc.blocks.reshape(-1)
        chosen=None
        for base in range(0,len(flat),262144):
            candidates=np.flatnonzero(np.isin(flat[base:base+262144],air_ids))
            for local in candidates[:32]:
                index=base+int(local)
                y,rem=divmod(index,sc.size[0]*sc.size[2]);z,x=divmod(rem,sc.size[0])
                if not sc.protected_at((x,y,z)):
                    chosen=(x,y,z);break
            if chosen:break
        target=next(i for i,s in sc.palette.items() if s=='minecraft:white_concrete')
        p=doc.propose(Bounds(chosen,chosen),target,'fill')
        snapshot=doc.preview(p)
        x,y,z=chosen
        assert snapshot[y,z,x]==target and sc.blocks[y,z,x] in air_ids
        del snapshot
        doc.approve(p)
        import gzip
        edited=nbt.File.parse(io.BytesIO(gzip.decompress(sc.to_bytes())))
        expected=copy.deepcopy(original)
        expected_root=expected.get('Schematic',expected)
        edited_root=edited.get('Schematic',edited)
        expected_container=expected_root['Blocks'] if sc.version==3 else expected_root
        edited_container=edited_root['Blocks'] if sc.version==3 else edited_root
        data_key='Data' if sc.version==3 else 'BlockData'
        expected_container[data_key]=edited_container[data_key]
        assert serialized(expected)==serialized(edited),'Only block Data may change after an approved edit'
        doc.undo()
        assert np.array_equal(reopened.schematic.blocks,sc.blocks)
    total_io=time.perf_counter()-start
    app=QApplication.instance() or QApplication([])
    app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE)
    w=MainWindow();w.resize(1380,900)
    errors=[];w.error=errors.append
    w.show();app.processEvents()
    ticks=[]
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(time.perf_counter()));timer.start()
    ui_start=time.perf_counter()
    w.load_path(path)
    pump_until(lambda: not w.loading and not w.view.workers_running() and not w.view.render_timer.isActive() and w.view.pick_buffer is not None,90)
    assert not errors,errors
    assert np.any(w.view.pick_buffer>=0),'No rendered blocks in final frame'
    assert w.view.render_stats['chunks']>0
    ui_seconds=time.perf_counter()-ui_start
    full_render_stats=dict(w.view.render_stats)
    app.processEvents();w.grab().save(str(output/'real_ship_3d.png'))
    # Warm camera rotation uses the same chunk meshes; only view projection changes.
    warm_start=time.perf_counter()
    QTest.mousePress(w.view,Qt.MouseButton.LeftButton,pos=QPoint(250,250))
    QTest.mouseMove(w.view,QPoint(290,250))
    QTest.mouseRelease(w.view,Qt.MouseButton.LeftButton,pos=QPoint(290,250))
    pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive() and w.view.pick_buffer is not None,30)
    warm_camera_seconds=time.perf_counter()-warm_start
    # Rapid layer changes coalesce/cancel old chunk work; last request must win.
    w.single_layer.setChecked(True)
    for y in (16,24,32,40,48):w.layer.setValue(min(y,sc.size[1]-1))
    last_y=w.layer.value()
    pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive() and w.view.pick_buffer is not None,45)
    assert all(last_y<=float(q[:,1].min())<=float(q[:,1].max())<=last_y+1 for q,n,i in w.view.mesh)
    w.grab().save(str(output/'real_ship_layer.png'))
    timer.stop()
    gaps=np.diff(ticks)*1000
    heartbeat={'ticks':len(ticks),'max_gap_ms':round(float(gaps.max()),2) if len(gaps) else None,
               'p95_gap_ms':round(float(np.percentile(gaps,95)),2) if len(gaps) else None}
    w.doc.dirty=False;w.close();app.processEvents()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before_hash
    try:
        import resource
        peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)
    except ImportError:peak_rss_mib=None
    result={'input':path.name,'sha256':before_hash,'compressed_bytes':path.stat().st_size,
        'dimensions_xyz':sc.size,'volume_including_air':stats['volume'],'placed_blocks':stats['solid'],
        'air_cells':stats['empty'],'protected_blocks':stats['protected'],'palette_states':len(sc.palette),
        'block_entities':len(container.get('BlockEntities',[])),'storage':type(sc.blocks).__name__,'block_store_bytes':sc.blocks.nbytes,'process_peak_rss_mib_linux':peak_rss_mib,
        'load_seconds':round(loaded-start,4),'mesh_seconds':round(mesh_seconds,4),'mesh_patches':len(scene),
        'mesh_stats':scene.stats,'warm_cache_hits':repeat.stats['cache_hits'],'io_verify_total_seconds':round(total_io,4),
        'ui_load_and_render_seconds':round(ui_seconds,4),'full_render':full_render_stats,'warm_camera_seconds':round(warm_camera_seconds,4),'heartbeat':heartbeat,'last_layer':last_y,
        'scalar_vs_chunk_decoder':'all blocks equal','export_typed_nbt':'entire serialized NBT identical',
        'edited_non_block_nbt':'identical','project_roundtrip':'passed','preview_approve_undo':'passed','source_unchanged':True,'errors':errors,
        'progress_events':len(stages),'platform':sys.platform}
    (output/'real_schem_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('path',type=Path);parser.add_argument('--output',type=Path,default=Path('verification'))
    args=parser.parse_args();print(json.dumps(verify(args.path.resolve(),args.output.resolve()),ensure_ascii=False,indent=2))
