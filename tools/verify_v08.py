"""Real schematic + exact-space regression. No live Ollama inference is simulated as real."""
import os,sys,json,time,hashlib,tempfile,argparse,copy,platform
try:import resource
except ImportError:resource=None
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.spaces import recognize
from ship_architect.space_model import from_candidate,bbox,contains,statistics,subtract,new_space,box_runs,analyze_connections
from ship_architect.ai_design import build_context,compile_plan
from ship_architect.app import MainWindow,STYLE
from ship_architect.ollama_client import OllamaClient
from verify_schem import pump_until

def run(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True);timing={}
 def timed(name,fn):
  start=time.perf_counter();r=fn();timing[name]=round(time.perf_counter()-start,4);return r
 before_hash=hashlib.sha256(args.ship.read_bytes()).hexdigest();sc,raw=timed('load',lambda:load_file(args.ship));d=Document(sc,raw,args.ship);meta=sc.metadata_digest();blocks=sc.blocks.copy()
 report=timed('recognition',lambda:recognize(d,d.selection));spaces=timed('128_candidate_exact_conversion',lambda:[from_candidate(d,c) for c in report['candidates']])
 assert len(spaces)==128
 selected=next(s for s in spaces if 40<sum(b-a+1 for _,_,a,b in s['runs'])<1800 and min(bbox(s)[0])>0)
 selected.update(name='실제 함선 검증 공간',purpose='내부 공간');d.set_spaces([selected],active=selected['id']);d.selection=Bounds(*bbox(selected));stats=statistics(d,selected)
 glass=d.add_material('minecraft:glass');ctx=timed('AI_context',lambda:build_context(d,d.selection))
 dimensions=ctx['size_xyz'];layout={'version':1,'summary':'선택 공간에만 유리 배치 (결정적 JSON 시험, 새 AI 추론 아님)','operations':[{'kind':'fill','min':[0,0,0],'max':[n-1 for n in dimensions],'material':'minecraft:glass','source':None,'overwrite':False}],'notes':[]}
 p=timed('compile_exact_AI_JSON',lambda:compile_plan(d,d.selection,layout,ctx,d.revision));assert len(p.indices)>0 and contains(selected,p.indices,sc.size).all()
 preview=d.preview(p);d.approve(p,approve_mass=True);assert np.array_equal(preview,sc.blocks);assert meta==sc.metadata_digest()
 # Verify unchanged cells in chunks, including all external/mod/BE positions.
 keep=np.ones(sc.blocks.size,bool);keep[p.indices]=False;assert np.array_equal(sc.blocks.ravel()[keep],blocks.ravel()[keep])
 with tempfile.TemporaryDirectory(prefix='ship-v08-') as tmp:
  tmp=Path(tmp);project=tmp/'verified.shipproj';timed('save_project',lambda:d.save_project(project));r=timed('reopen_project',lambda:Document.open_project(project));assert r.spaces==d.spaces and np.array_equal(r.schematic.blocks,sc.blocks)
  export=tmp/'verified.schem';timed('schem_export',lambda:d.export(export,approved=True));rsc,_=timed('schem_reload',lambda:load_file(export));assert np.array_equal(rsc.blocks,sc.blocks) and rsc.metadata_digest()==meta
  d.undo();assert np.array_equal(sc.blocks,blocks);d.redo();assert np.array_equal(sc.blocks,preview)
  app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE);os.environ['SHIP_ARCHITECT_DATA_DIR']=str(tmp/'ui-data');w=MainWindow();w.resize(1600,1060);w.show();errors=[];w.error=errors.append
  w.set_document(d,'v0.8 실제 함선 · 정확한 공간');m=w.space_manager;m.select_id(selected['id']);w.layer.setValue(d.selection.lo[1]);w.view.focus_selection();w.zone_clip.setChecked(True)
  pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive(),60)
  w.tabs.setCurrentWidget(m);app.processEvents();w.grab().save(str(out/'real_ship_space_manager.png'))
  w.tabs.setCurrentWidget(m.plan);m.plan.refresh();app.processEvents();w.grab().save(str(out/'real_ship_plan.png'))
  w.tabs.setCurrentWidget(w.view);w.view.update();app.processEvents();w.grab().save(str(out/'real_ship_overlay.png'))
  assert not errors,errors;d.dirty=False;w.close();pump_until(lambda:not w.isVisible(),30)
 assert hashlib.sha256(args.ship.read_bytes()).hexdigest()==before_hash
 try:OllamaClient('http://127.0.0.1:11434').models();live='endpoint available; no inference executed'
 except Exception as exc:live='not tested / endpoint unavailable: '+str(exc)
 summary={'platform':platform.platform(),'python':platform.python_version(),'source':args.ship.name,'sha256':before_hash,'size_xyz':list(sc.size),'volume':sc.blocks.size,'original_non_air':int(np.count_nonzero(sc.solid(blocks))),
 'recognized_candidates':len(spaces),'exact_candidate_runs_total':sum(len(s['runs']) for s in spaces),'selected_space':{k:v for k,v in selected.items() if k!='runs'},'selected_cells':stats['cells'],'changed':len(p.indices),'outside_shape_skipped':p.outside_space,
 'all_changes_inside_exact_shape':True,'all_other_cells_unchanged':True,'NBT_metadata_preserved':True,'project_roundtrip':True,'schem_roundtrip':True,'undo_redo':True,'gui_errors':[],'timings_seconds':timing,
 'process_peak_RSS_MiB':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2) if resource else None,'peak_RSS_note':'Linux process high water mark includes Qt, JIT, baseline copy and verification arrays. Not an app-only memory benchmark.',
 'AI_test':'deterministic structured JSON compilation, not new Qwen3 inference','Ollama_live':live,'semantic_space_accuracy':'manual review still required'}
 (out/'verification_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--ship',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('verification_v08'));run(p.parse_args())
