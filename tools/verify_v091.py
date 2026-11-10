"""Real-file view-only regression, held-drag measurement and dock/plane inspection.
No Windows/GPU/dual-monitor hardware or new Ollama inference is claimed.
"""
import os,sys,json,time,hashlib,tempfile,argparse,zipfile,collections
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPointF,QEvent
from PySide6.QtGui import QFont,QMouseEvent
from PySide6.QtTest import QTest
from ship_architect.app import MainWindow,STYLE
from ship_architect.model import Document
from ship_architect.schematic import load_file
from ship_architect.axis_plan import VIEWS
from verify_schem import pump_until

def ready(w):return not w.view.workers_running() and not w.view.render_timer.isActive() and w.operation is None and w.analysis_worker is None

def run(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True);schem_report={}
 if args.schem:
  started=time.perf_counter();source_sc,source_raw=load_file(args.schem);schem_report={'name':args.schem.name,'sha256':hashlib.sha256(source_raw).hexdigest(),'volume':int(source_sc.blocks.size),'read_seconds':round(time.perf_counter()-started,3)}
 sha=hashlib.sha256(args.project.read_bytes()).hexdigest();d=Document.open_project(args.project);before=hashlib.sha256(d.schematic.blocks.tobytes()).hexdigest();meta=d.schematic.metadata_digest();spaces=json.loads(json.dumps(d.spaces));timing={}
 with zipfile.ZipFile(args.diagnostic) as z:events=[json.loads(l) for l in z.read('logs/events.jsonl').decode().splitlines()]
 with tempfile.TemporaryDirectory(prefix='ship-v091-') as tmp:
  os.environ['SHIP_ARCHITECT_DATA_DIR']=str(Path(tmp)/'data');app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE);w=MainWindow();w.resize(1720,1040);errors=[];w.error=errors.append;w.ai_panel.timer.stop();w.show();start=time.perf_counter();w.set_document(d,args.project.name);pump_until(lambda:ready(w),120);timing['initial_scene_seconds']=round(time.perf_counter()-start,3)
  b=w.workbench;b.open_tab(w.view);QTest.qWait(100);v=w.view;old=v.interactive_frames;QTest.mousePress(v,Qt.MouseButton.LeftButton,pos=v.rect().center());paints=[];began=time.perf_counter()
  for i in range(60):
   e=QMouseEvent(QEvent.Type.MouseMove,QPointF(60+i*4,180+i),QPointF(60+i*4,180+i),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);v.mouseMoveEvent(e);t=time.perf_counter();v.repaint();paints.append(1000*(time.perf_counter()-t));QTest.qWait(8)
  held_frames=v.interactive_frames-old;assert v.start is not None and held_frames>=30 and v.pick_buffer is None
  w.grab().save(str(out/'held_drag_proxy.png'));QTest.mouseRelease(v,Qt.MouseButton.LeftButton,pos=v.rect().center());t=time.perf_counter();pump_until(lambda:ready(w),120);timing['precise_release_seconds']=round(time.perf_counter()-t,3);assert v.pick_buffer is not None
  w.network_view.analyze();pump_until(lambda:ready(w),120);b.rebuild_tree(True);app.processEvents();w.grab().save(str(out/'integrated_scene.png'))
  b.cut_axis.setCurrentText('X');b.cut_position.setValue(d.selection.hi[0]-5);pump_until(lambda:ready(w),120);w.grab().save(str(out/'section_x.png'))
  b.restore_visibility();pump_until(lambda:ready(w),120)
  for name in VIEWS:b.axis_plan.orientation.setCurrentText(name);b.axis_plan.refresh();assert b.axis_plan.image is not None
  b.open_tab(b.axis_plan);QTest.qWait(40);w.grab().save(str(out/'side_plane.png'))
  b.owner.space_manager.select_id(d.active_space_id);b.interior();pump_until(lambda:ready(w),120);w.grab().save(str(out/'interior_view.png'))
  b.restore_visibility();pump_until(lambda:ready(w),120);w.integrated_ui.categories.setCurrentIndex(1);w.split_mode.setChecked(True);b.open_tab(w.view);QTest.qWait(70);w.grab().save(str(out/'integrated_split.png'));w.split_mode.setChecked(False);b.detach_view('2D');QTest.qWait(50);assert b.floating['2D'].isFloating() and b.axis_plan.isVisible();b.attach_view('2D');b.save();b.restore()
  # Export while cut/hide state is active: the full data must still round trip.
  b.cut_axis.setCurrentText('Z');b.cut_position.setValue(d.selection.lo[2]);pump_until(lambda:ready(w),120);b.save_display();path=Path(tmp)/'view.shipproj';d.save_project(path);r=Document.open_project(path);assert r.view_settings['axis']=='Z' and np.array_equal(r.schematic.blocks,d.schematic.blocks)
  export=Path(tmp)/'full.schem';d.export(export,approved=True);sc,_=load_file(export);assert hashlib.sha256(sc.blocks.tobytes()).hexdigest()==before and sc.metadata_digest()==meta
  assert hashlib.sha256(d.schematic.blocks.tobytes()).hexdigest()==before and d.schematic.metadata_digest()==meta
  assert [s['runs'] for s in d.spaces]==[s['runs'] for s in spaces]
  if args.schem:
   d.dirty=False;started=time.perf_counter();w.load_path(args.schem);pump_until(lambda:not w.loading and ready(w),120);timing['schem_GUI_load_seconds']=round(time.perf_counter()-started,3);assert w.doc.schematic.size==d.schematic.size
  w.doc.dirty=False;w.close();pump_until(lambda:not w.isVisible(),120);assert not errors,errors
 report={'source':args.project.name,'source_sha256':sha,'schem_input':schem_report,'size_xyz':list(d.schematic.size),'volume':d.schematic.blocks.size,'spaces':len(d.spaces),'blocks_identical':True,'NBT_identical':True,'space_shapes_identical':True,'project_view_restore':True,'export_while_cut_is_full_data':True,'GUI_errors':errors,'ribbon_categories':list(w.integrated_ui.CATEGORIES),'consolidated_split_checked':True,'held_drag_frames':held_frames,'held_drag_events':60,'proxy_max_faces':1400,'paint_median_ms':round(float(np.median(paints)),3),'paint_p95_ms':round(float(np.percentile(paints,95)),3),'timings':timing,'rendering':'CPU NumPy/QPainter proxy during gesture; precise compiled CPU raster + pick buffer after gesture','diagnostic_events':len(events),'diagnostic_versions':dict(collections.Counter(e.get('version') for e in events)),'Windows_direct_test':False,'GPU_test':False,'physical_dual_monitor_test':False,'new_Ollama_inference':False}
 (out/'verification_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False,indent=2));assert hashlib.sha256(args.project.read_bytes()).hexdigest()==sha
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--diagnostic',type=Path,required=True);p.add_argument('--schem',type=Path);p.add_argument('--output',type=Path,default=Path('verification_v091'));run(p.parse_args())
