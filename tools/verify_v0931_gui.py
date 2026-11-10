"""Read-only source inputs; real Qt offscreen verification of v0.9.3.1. No Windows claim."""
import os,sys,json,time,hashlib,tempfile,zipfile,copy,resource,argparse,faulthandler
from pathlib import Path
faulthandler.enable();os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from PySide6.QtCore import Qt,QPointF,QEvent,QEventLoop,QTimer
from PySide6.QtGui import QFont,QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ship_architect.app import MainWindow,STYLE
from ship_architect.model import Document
from ship_architect.schematic import load_file
from ship_architect.diagnostics import report
from verify_schem import pump_until

def ready(w):return not w.loading and not w.view.workers_running() and not w.view.render_timer.isActive() and w.operation is None and w.analysis_worker is None

def wait(ms):
 loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()

def main(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True);inputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.project,args.schem,args.diagnostic)}
 d=Document.open_project(args.project);blocks=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();shapes=copy.deepcopy(d.spaces);timing={}
 with zipfile.ZipFile(args.diagnostic) as z:legacy=[json.loads(l) for l in z.read('logs/events.jsonl').decode().splitlines()]
 with tempfile.TemporaryDirectory() as tmp:
  os.environ['SHIP_ARCHITECT_DATA_DIR']=str(Path(tmp)/'data');app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE)
  w=MainWindow();w.resize(1720,1040);errors=[];w.error=lambda e:errors.append(str(e));w.ai_panel.timer.stop();w.show();t=time.perf_counter();w.set_document(d,args.project.name);pump_until(lambda:ready(w),180);timing['initial_scene_seconds']=round(time.perf_counter()-t,3)
  v=w.view;b=w.workbench;ui=w.integrated_ui;tasks=ui.tasks;b.open_tab(v);wait(50);faces=len(v.mesh);scene=v.mesh;old=v.interaction_renders;observed=[];paints=[]
  QTest.mousePress(v,Qt.MouseButton.LeftButton,pos=v.rect().center())
  for i in range(70):
   ev=QMouseEvent(QEvent.Type.MouseMove,QPointF(60+3*i,180+i),QPointF(60+3*i,180+i),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);v.mouseMoveEvent(ev);wait(40);assert v.frame is not None and v.mesh is scene
   if v.render_stats.get('interaction'):observed.append(v.render_stats['seconds']);assert v.render_stats['scene_faces']==faces
   t=time.perf_counter();v.repaint();paints.append(1000*(time.perf_counter()-t))
  moving=v.interaction_renders-old;assert moving>=3 and v.pick_buffer is None
  w.grab().save(str(out/'rotation_full_ship.png'));QTest.mouseRelease(v,Qt.MouseButton.LeftButton,pos=v.rect().center());pump_until(lambda:ready(w),180);assert v.pick_buffer is not None
  serial=v.mesh_serial
  for i in range(8):
   w.space_manager.overlays.setChecked(i%2==0);w.space_manager.blocks.setChecked(i%2==0);assert v.frame is not None
  w.space_manager.overlays.setChecked(True);w.space_manager.blocks.setChecked(True);pump_until(lambda:ready(w),180);rebuilds=v.mesh_serial-serial;assert rebuilds==0
  t=time.perf_counter();w.space_view.bulk.recognition.compare();pump_until(lambda:ready(w),180);timing['two_methods_UI_comparison_seconds']=round(time.perf_counter()-t,3)
  recognition=copy.deepcopy(d.analysis_state['last_result']);assert recognition['scope']['lo']==[0,0,0] and recognition['scope']['hi']==[n-1 for n in d.schematic.size]
  assert d.spaces[:len(shapes)]==shapes and np.array_equal(blocks,d.schematic.blocks)
  tasks.show(tasks.space_results);w.grab().save(str(out/'whole_3d_review.png'))
  if w.space_view.list.count():
   w.space_view.list.setCurrentRow(0);w.space_view.bulk.compare();pump_until(lambda:ready(w),180);w.grab().save(str(out/'exact_air_validation.png'));w.space_view.bulk.clear_compare();w.space_view.bulk.recognition.overlay();w.grab().save(str(out/'recognition_overlay.png'));w.space_view.bulk.clear_compare()
  t=time.perf_counter();w.network_view.scan_equipment();pump_until(lambda:ready(w),180);timing['equipment_scan_seconds']=round(time.perf_counter()-t,3);inv=copy.deepcopy(w.network_view.equipment)
  actual={d.schematic.palette[int(i)]:int(n) for i,n in zip(*np.unique(d.schematic.blocks,return_counts=True))}
  assert all(actual[state]==count for state,count in inv['by_block'].items())
  assert sum(b-a+1 for runs in inv['position_runs'].values() for _,_,a,b in runs)==inv['total']
  # A real unclassified ID is reclassified by exact saved rule; undo restores metadata only.
  n=w.network_view;n.filter.setText('미분류');wait(30)
  if not n.visible_records:n.filter.clear();wait(30)
  n.table.selectRow(0);key=tasks.rule_key();state_before=copy.deepcopy(d.analysis_state);tasks.rule_type.setCurrentText('전력 설비 후보');tasks.save_rule();pump_until(lambda:ready(w),180);assert d.analysis_state['equipment_rules'][key]=='전력 설비 후보'
  rule_count=w.network_view.equipment['by_category'].get('전력 설비 후보',0);w.undo();pump_until(lambda:ready(w),180);assert d.analysis_state==state_before;n.filter.clear();n.scan_equipment();pump_until(lambda:ready(w),180)
  geometry=[]
  for width in (1280,1720):
   w.resize(width,1040);ui.open_analysis()
   for page in range(3):
    n.pages.setCurrentIndex(page);tasks.show(n.table if page==2 else tasks.connection_results);wait(80)
    central=w.centralWidget().geometry();panels=[b.scene_dock,ui.analysis_dock]
    assert w.view.isVisible() and all(not dock.geometry().intersects(central) for dock in panels)
    if page<2:assert n.source.width()>180 and n.target.width()>180
    else:assert n.table.viewport().width()>500
    geometry.append({'width':width,'page':page,'central_width':central.width(),'source_width':n.source.width(),'table_width':n.table.viewport().width(),'horizontal_scroll_max':n.table.horizontalScrollBar().maximum()})
    w.grab().save(str(out/f'layout_{width}_{page}.png'))
  tasks.result_dock.setFloating(True);wait(40);assert w.view.isVisible();b.save();tasks.result_dock.setFloating(False);b.restore();wait(40);assert tasks.result_dock.isFloating();tasks.result_dock.setFloating(False)
  b.cut_axis.setCurrentText('X');b.cut_position.setValue(d.selection.hi[0]-1);pump_until(lambda:ready(w),180)
  assert np.array_equal(blocks,d.schematic.blocks) and meta==d.schematic.metadata_digest()
  project=Path(tmp)/'verified.shipproj';b.save_display();d.save_project(project);r=Document.open_project(project);assert r.spaces==d.spaces and r.analysis_state==d.analysis_state
  export=Path(tmp)/'verified.schem';d.export(export,approved=True);sc,_=load_file(export);assert np.array_equal(sc.blocks,blocks) and sc.metadata_digest()==meta
  diagnostic=report(out/'sample_diagnostic.zip',document=d,ui_state=ui.ui_state())
  with zipfile.ZipFile(diagnostic) as z:
   names=z.namelist();assert 'analysis_summary.json' in names and 'analysis_rules.jsonl' in names and any('exterior_first-' in n for n in names) and 'recognition_comparison.json' in names
  d.dirty=False;t=time.perf_counter();w.load_path(args.schem);pump_until(lambda:ready(w),180);timing['schem_gui_load_seconds']=round(time.perf_counter()-t,3);source_sc,_=load_file(args.schem);assert np.array_equal(w.doc.schematic.blocks,source_sc.blocks)
  w.doc.dirty=False;w.close();pump_until(lambda:not w.isVisible(),120);assert not errors,errors
 summary={'input_sha256':inputs,'size_xyz':list(d.schematic.size),'volume':int(blocks.size),'placed_blocks':sum(int(count) for i,count in zip(*np.unique(blocks,return_counts=True)) if d.schematic.palette[int(i)].split('[')[0] not in ('minecraft:air','minecraft:cave_air','minecraft:void_air')),'original_spaces':len(shapes),'spaces_after_analysis':len(d.spaces),'scene_faces':faces,'interaction_model':'same full scene; lower pixel resolution only; no proxy geometry','interaction_frames':moving,'paint_median_ms':round(float(np.median(paints)),3),'interaction_render_median_seconds':round(float(np.median(observed)),4),'visibility_toggle_mesh_rebuilds':rebuilds,'recognition':{k:recognition.get(k) for k in ('method','scope','component_count','counts','limited','omitted_candidate_count','domain_unassigned_cells','retained_candidate_cells')},'candidate_reasons':dict(__import__('collections').Counter(reason for c in recognition['candidates'] for reason in c['reasons'])),'equipment':{k:inv[k] for k in ('total','classified','unclassified','by_category','record_count','positions_truncated')},'equipment_full_counts_RLE_reconciled':True,'user_rule_test':{'key':key,'result_category_count':rule_count,'undo_restored':True},'manual_spaces_unchanged':True,'project_shapes_rules_roundtrip':True,'schem_blocks_NBT_identical':True,'layout_checks':geometry,'GUI_errors':errors,'timings':timing,'diagnostic_file':diagnostic.name,'supplied_diagnostic_events':len(legacy),'Windows_direct':False,'physical_dual_monitor':False,'GPU':False,'live_Ollama':False,'peak_rss_mib_linux':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)}
 for p in (args.project,args.schem,args.diagnostic):assert hashlib.sha256(p.read_bytes()).hexdigest()==inputs[p.name]
 (out/'verification_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--schem',type=Path,required=True);p.add_argument('--diagnostic',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('docs/v0931_gui_verification'));main(p.parse_args())
