"""Read-only real project, input schematic, diagnostic replay and Qt verification."""
import os,sys,json,hashlib,zipfile,time,tempfile,collections,argparse,platform
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from ship_architect.model import Document
from ship_architect.schematic import load_file
from ship_architect.navigation import Navigation
from ship_architect.equipment import inventory
from ship_architect.ai_design import build_context,compile_plan,parse_plan,PlanError
from ship_architect.floors import detect_decks,provenance
from ship_architect.space_model import contains
from ship_architect.app import MainWindow,STYLE
from create_v09_demo import build_demo,EXPECTED
from verify_schem import pump_until

def coordinate_replay(rows):
 contexts={r['trace_id']:r['context'] for r in rows if r['kind']=='ai_context'};result=[]
 for event in rows:
  if event['kind']!='ai_raw_response' or event.get('trace_id') not in ('b11f9f1e28c74d59','9a0faff7242a46da'):continue
  try:parse_plan(event['raw'],contexts[event['trace_id']])
  except PlanError as exc:
   result.append({'trace_id':event['trace_id'],'attempt':event.get('attempt'),'error':str(exc),'applied':False})
  else:raise AssertionError('Recorded invalid coordinates were accepted')
 return result

def verify(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True);timing={}
 def timed(name,fn):
  started=time.perf_counter();r=fn();timing[name]=round(time.perf_counter()-started,4);return r
 files=[args.project,args.ship,args.diagnostic];hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
 d=timed('load_project',lambda:Document.open_project(args.project));sc,raw=timed('load_schem',lambda:load_file(args.ship));before=d.schematic.blocks.copy();spaces=json.loads(json.dumps(d.spaces));meta=d.schematic.metadata_digest()
 nav=timed('navigation',lambda:Navigation(d));equipment=timed('equipment',lambda:inventory(d));floors=timed('floors',lambda:detect_decks(d))
 pairs=collections.Counter();examples=[]
 for i,left in enumerate(d.spaces):
  for right in d.spaces[i+1:]:
   pair=nav.pair(left['id'],right['id']);pairs[pair['status']]+=1
   if len(examples)<8 and pair['status'] in ('overlap','connected'):examples.append({'spaces':[left['name'],right['name']],**pair})
 with zipfile.ZipFile(args.diagnostic) as z:rows=[json.loads(l) for l in z.read('logs/events.jsonl').decode().splitlines()]
 coordinate_errors=coordinate_replay(rows)
 v08=[r for r in rows if r.get('version')=='0.8.0'];replays=[]
 ctx=timed('exact_context',lambda:build_context(d,d.selection,True))
 for event in v08:
  if event['kind']!='ai_raw_response':continue
  plan=parse_plan(event['raw'],ctx);p=compile_plan(d,d.selection,plan,ctx,d.revision,trace_id=event['trace_id']);assert len(p.indices)==0 and p.outside_space>0
  replays.append({'trace_id':event['trace_id'],'claim':plan['summary'],'changed':len(p.indices),'reasons':p.reasons,'status':'no_change / 미완료'})
 assert len(replays)==3
 assert np.array_equal(before,d.schematic.blocks) and d.spaces==spaces and d.schematic.metadata_digest()==meta
 with tempfile.TemporaryDirectory(prefix='ship-v09-verify-') as tmp:
  tmp=Path(tmp);project=tmp/'roundtrip.shipproj';timed('project_save',lambda:d.save_project(project));restored=timed('project_reopen',lambda:Document.open_project(project));assert restored.spaces==spaces and np.array_equal(restored.schematic.blocks,before)
  export=tmp/'roundtrip.schem';timed('schem_export',lambda:d.export(export,approved=True));rsc,_=load_file(export);assert np.array_equal(rsc.blocks,before) and rsc.metadata_digest()==meta
  # Reversible small edit within exact space, with original equipment/exterior protection.
  target=d.add_material('minecraft:glass');p=timed('exact_manual_proposal',lambda:d.propose(d.selection,target,'floor'))
  assert len(p.indices)>0 and contains(d.active_space(),p.indices,d.schematic.size).all()
  d.approve(p,approve_mass=True);d.undo();assert np.array_equal(d.schematic.blocks,before)
  app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE);os.environ['SHIP_ARCHITECT_DATA_DIR']=str(tmp/'data');w=MainWindow();w.resize(1600,1050);w.show();errors=[];w.error=errors.append
  w.set_document(d,'v0.9 실제 design.shipproj');v=w.network_view;v.nav=nav;v.equipment=equipment;v.analysis_doc=d;v.snapshot=v.signature();v.list_connections();v.show_equipment();w.tabs.setCurrentWidget(v)
  pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive(),60);app.processEvents();w.grab().save(str(out/'actual_connections.png'))
  demo=build_demo();w.set_document(demo,'v0.9 연결 시험 프로젝트');v=w.network_view;v.nav=Navigation(demo);v.equipment=inventory(demo);v.analysis_doc=demo;v.snapshot=v.signature();v.source.setCurrentIndex(v.source.findData('A'));v.target.setCurrentIndex(v.target.findData('B'));v.list_connections();v.find();pump_until(lambda:w.operation is None,30)
  w.layer.setValue(1);w.split_mode.setChecked(True);pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive(),60)
  from PySide6.QtTest import QTest
  QTest.qWait(100);assert w.view.isVisible() and w.space_manager.plan.isVisible() and w.view.width()>100 and w.space_manager.plan.width()>100
  app.processEvents();w.grab().save(str(out/'demo_split_route.png'));assert w.view.route_path
  w.split_mode.setChecked(False);demo.dirty=False;w.close();pump_until(lambda:not w.isVisible(),30);assert not errors,errors
 assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}==hashes
 result={'platform':platform.platform(),'python':platform.python_version(),'sha256':hashes,'size_xyz':list(d.schematic.size),'volume':int(d.schematic.blocks.size),'schem_non_air':int(sc.solid(sc.blocks).sum()),'project_non_air':int(d.schematic.solid(before).sum()),'spaces':len(spaces),'pair_status_counts':dict(pairs),'pair_examples':examples,'navigation_observations':nav.observations,'equipment':{k:v for k,v in equipment.items() if k!='records'},'floor_evidence':floors,'provenance':dict(collections.Counter(provenance(d,s)[0] for s in spaces)),
 'diagnostic_events':len(rows),'diagnostic_versions':dict(collections.Counter(r.get('version') for r in rows)),'embedded_chat_entries':len((d.chat_seed or {}).get('entries',[])),'recorded_real_AI_replays':replays,'recorded_coordinate_errors':coordinate_errors,'new_Ollama_inference':False,'exact_edit_changed':len(p.indices),'project_schem_roundtrip':True,'all_inputs_unchanged':True,'NBT_preserved':True,'manual_spaces_preserved':True,'GUI_errors':errors,'timings_seconds':timing}
 (out/'verification_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in result.items() if k not in ('equipment','pair_examples','floor_evidence')},ensure_ascii=False,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--ship',type=Path,required=True);p.add_argument('--diagnostic',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('verification_v09'));verify(p.parse_args())
