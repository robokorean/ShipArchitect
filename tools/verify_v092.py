"""Real-file Qt/offscreen stability and evidence verification; no Windows/live AI claim."""
import os,sys,json,time,hashlib,tempfile,zipfile,collections,argparse,faulthandler
faulthandler.enable()
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from PySide6.QtCore import Qt,QPointF,QEvent,QEventLoop,QTimer
from PySide6.QtGui import QFont,QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ship_architect.app import MainWindow,STYLE
from ship_architect.model import Document
from ship_architect.schematic import load_file
from ship_architect.space_model import from_candidate
from ship_architect.spaces import recognize
from ship_architect.equipment import inventory,category
from ship_architect.diagnostics import report
from verify_schem import pump_until

def ready(w):return not w.loading and not w.view.workers_running() and not w.view.render_timer.isActive() and w.operation is None and w.analysis_worker is None

def qt_wait(ms):
 loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()

def main(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True);source_hash=hashlib.sha256(args.project.read_bytes()).hexdigest();d=Document.open_project(args.project);blocks=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();shapes=[s['runs'] for s in d.spaces];timing={}
 with zipfile.ZipFile(args.diagnostic) as z:legacy=[json.loads(l) for l in z.read('logs/events.jsonl').decode().splitlines()]
 with tempfile.TemporaryDirectory() as tmp:
  os.environ['SHIP_ARCHITECT_DATA_DIR']=str(Path(tmp)/'data');app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE);w=MainWindow();w.resize(1720,1040);errors=[];w.error=errors.append;w.ai_panel.timer.stop();w.show();t=time.perf_counter();w.set_document(d,args.project.name);pump_until(lambda:ready(w),180);timing['initial_scene_seconds']=round(time.perf_counter()-t,3)
  v=w.view;b=w.workbench;ui=w.integrated_ui;b.open_tab(v);QTest.qWait(40);faces=len(v.mesh);scene=v.mesh;old=v.interaction_renders;observed=[];paints=[]
  QTest.mousePress(v,Qt.MouseButton.LeftButton,pos=v.rect().center())
  for i in range(60):
   event=QMouseEvent(QEvent.Type.MouseMove,QPointF(60+3*i,180+i),QPointF(60+3*i,180+i),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);v.mouseMoveEvent(event);qt_wait(40);assert v.frame is not None and v.mesh is scene
   if v.render_stats.get('interaction'):observed.append(v.render_stats['seconds']);assert v.render_stats['scene_faces']==faces
   t=time.perf_counter();v.repaint();paints.append(1000*(time.perf_counter()-t))
  assert v.interaction_renders-old>=3 and v.pick_buffer is None
  w.grab().save(str(out/'rotation_full_ship.png'));moving_frames=v.interaction_renders-old;t=time.perf_counter();QTest.mouseRelease(v,Qt.MouseButton.LeftButton,pos=v.rect().center());pump_until(lambda:ready(w),180);timing['release_precise_seconds']=round(time.perf_counter()-t,3);assert v.pick_buffer is not None
  serial=v.mesh_serial
  for i in range(8):
   w.space_manager.overlays.setChecked(i%2==0);w.space_manager.blocks.setChecked(i%2==0);assert v.frame is not None
  w.space_manager.overlays.setChecked(True);w.space_manager.blocks.setChecked(True);pump_until(lambda:ready(w),180);assert v.mesh_serial==serial
  t=time.perf_counter();w.network_view.scan_equipment();pump_until(lambda:ready(w),180);timing['equipment_scan_seconds']=round(time.perf_counter()-t,3);inv=w.network_view.equipment;assert inv
  # Counts reconcile against the actual array, independent of the representative position list.
  actual={d.schematic.palette[int(i)]:int(n) for i,n in zip(*np.unique(d.schematic.blocks,return_counts=True))}
  assert all(actual[state]==count for state,count in inv['by_block'].items())
  assert sum(b-a+1 for runs in inv['position_runs'].values() for _,_,a,b in runs)==inv['total']
  w.space_manager.overlays.setChecked(False);w.space_manager.overlays.setChecked(True);w.space_manager.refresh();assert w.network_view.equipment is inv
  ui.open_analysis();QTest.qWait(50);assert w.dockWidgetArea(ui.analysis_dock)==Qt.DockWidgetArea.RightDockWidgetArea;w.grab().save(str(out/'analysis_right_tabs.png'));ui.open_properties(0);b.open_tab(v)
  t=time.perf_counter();results=[];w.run_operation(lambda p,c:recognize(d,d.selection,p,c),results.append,'실제 파일 공간 후보·제외 분석');pump_until(lambda:ready(w),180);timing['recognition_seconds']=round(time.perf_counter()-t,3);recognition=results[0]
  assert [s['runs'] for s in d.spaces]==shapes and np.array_equal(blocks,d.schematic.blocks)
  w.space_view.ready(recognition,d);QTest.qWait(30);w.grab().save(str(out/'candidates_exact_volume.png'))
  candidates=[c for c in recognition['candidates'] if c.get('volume_runs') and c['kind']!='open_area']
  if not candidates:candidates=[c for c in recognition['candidates'] if c.get('volume_runs')]
  assert candidates,'actual file must have at least one exact-volume candidate for confirmation test'
  c=candidates[0];row=next(i for i,q in enumerate(d.space_candidates) if q['id']==c['id']);w.space_view.list.setCurrentRow(row);t=time.perf_counter();w.space_view.confirm();pump_until(lambda:ready(w),180);timing['confirm_seconds']=round(time.perf_counter()-t,3)
  confirmed=d.active_space();assert confirmed['runs']==c['volume_runs'] and [s['runs'] for s in d.spaces[:len(shapes)]]==shapes
  w.space_view.overlay.setChecked(False);b.open_tab(v);QTest.qWait(30);w.grab().save(str(out/'confirmed_exact_volume.png'))
  b.cut_axis.setCurrentText('X');b.cut_position.setValue(d.selection.hi[0]-1);pump_until(lambda:ready(w),180);assert np.array_equal(blocks,d.schematic.blocks) and meta==d.schematic.metadata_digest()
  project=Path(tmp)/'verified.shipproj';w.workbench.save_display();d.save_project(project);r=Document.open_project(project);assert [s['runs'] for s in r.spaces]==[s['runs'] for s in d.spaces]
  export=Path(tmp)/'verified.schem';d.export(export,approved=True);sc,_=load_file(export);assert np.array_equal(sc.blocks,blocks) and sc.metadata_digest()==meta
  diagnostic=report(out/'sample_diagnostic.zip',document=d,ui_state=ui.ui_state())
  with zipfile.ZipFile(diagnostic) as z:
   names=z.namelist();assert 'spaces_confirmed.jsonl' in names and any('equipment-' in n for n in names) and any('spaces-' in n for n in names)
  d.dirty=False;t=time.perf_counter();w.load_path(args.schem);pump_until(lambda:ready(w),180);timing['schem_gui_load_seconds']=round(time.perf_counter()-t,3);loaded_source,_=load_file(args.schem);assert np.array_equal(w.doc.schematic.blocks,loaded_source.blocks)
  w.doc.dirty=False;w.close();pump_until(lambda:not w.isVisible(),120);assert not errors,errors
 summary={'source_sha256':source_hash,'size_xyz':list(d.schematic.size),'volume':int(blocks.size),'placed_blocks':sum(int(n) for i,n in zip(*np.unique(blocks,return_counts=True)) if d.schematic.palette[int(i)].split('[')[0] not in ('minecraft:air','minecraft:cave_air','minecraft:void_air')),'original_spaces':len(shapes),'scene_faces':faces,'interaction_model':'same full scene, reduced pixel resolution, no face sampling','interaction_frames':moving_frames,'paint_median_ms':round(float(np.median(paints)),3),'interaction_render_median_seconds':round(float(np.median(observed)),4),'overlay_toggle_mesh_rebuilds':v.mesh_serial-serial if w.doc is d else 0,'equipment':{k:inv[k] for k in ('total','classified','unclassified','by_category','record_count','positions_truncated')},'equipment_counts_and_all_RLE_reconciled':True,'recognized_candidates':len(recognition['candidates']),'excluded':recognition['exclusion_counts'],'confirmed_candidate_exact':True,'manual_spaces_preserved':True,'project_shapes_roundtrip':True,'schem_blocks_NBT_identical':True,'GUI_errors':errors,'timings':timing,'diagnostic_file':diagnostic.name,'legacy_diagnostic_events':len(legacy),'Windows_direct':False,'physical_dual_monitor':False,'GPU':False,'live_Ollama':False,'peak_rss_mib_linux':round(__import__('resource').getrusage(__import__('resource').RUSAGE_SELF).ru_maxrss/1024,2)}
 (out/'verification_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2));assert hashlib.sha256(args.project.read_bytes()).hexdigest()==source_hash
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--schem',type=Path,required=True);p.add_argument('--diagnostic',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('docs/v092_verification'));main(p.parse_args())
