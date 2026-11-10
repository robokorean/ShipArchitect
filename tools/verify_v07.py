"""Read-only user-file regression, real Diagnostic1 response replay and Qt/spatial checks.
python tools/verify_v07.py --before ship_f.schem --after design_approved4.schem --diagnostic Diagnostic1.zip --output verification_v07
No new Ollama inference is claimed by this replay tool.
"""
import os,sys,json,hashlib,tempfile,zipfile,uuid,argparse
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from ship_architect.schematic import Schematic,load_file
from ship_architect.model import Document,Bounds,Proposal
from ship_architect.ai_design import generate_design,PlanError
from ship_architect.conversations import ConversationStore
from ship_architect.spaces import recognize
from ship_architect.safety import review
from ship_architect.app import MainWindow,STYLE
from compare_schematics import compare
from verify_schem import pump_until


def verify(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True)
 baseline_hash=hashlib.sha256(args.before.read_bytes()).hexdigest();target_hash=hashlib.sha256(args.after.read_bytes()).hexdigest()
 comparison=compare(args.before,args.after,out)
 assert comparison['changed']==86479 and comparison['after_materials']=={'minecraft:blue_concrete':86479} and comparison['safety']['hull_risk']
 with zipfile.ZipFile(args.diagnostic) as z:
  rows=[json.loads(l) for l in z.read('logs/events.jsonl').decode().splitlines()]
 kinds={kind:sum(r['kind']==kind for r in rows) for kind in sorted({r['kind'] for r in rows})}
 requests={r['trace_id']:r for r in rows if r['kind']=='ai_request'}
 raw=next(r['raw'] for r in rows if r['kind']=='ai_raw_response' and r['trace_id']=='6395ae2c22a549ac')
 diagnostic_summary={'sha256':hashlib.sha256(args.diagnostic.read_bytes()).hexdigest(),'events':len(rows),'kinds':kinds,
  'confirmed_errors':['exclusive ZIP creation collision','zone creation absent from old Undo command stack','AI completed narrative vs actual 0/11820 changed','greedy Korean material suffix and missing aliases'],
  'work_ids':sorted(requests)}
 (out/'diagnostic_analysis.json').write_text(json.dumps(diagnostic_summary,ensure_ascii=False,indent=2),encoding='utf-8')
 sc,raw_source=load_file(args.before);doc=Document(sc,raw_source,args.before);before=sc.blocks.copy();nbt=sc.metadata_digest();request=requests['6395ae2c22a549ac'];b=Bounds(tuple(request['selection']['lo']),tuple(request['selection']['hi']));doc.selection=b
 class Replay:
  def chat(self,*a,**k):return raw,{'replay':True}
 with tempfile.TemporaryDirectory(prefix='ship-v07-check-') as temporary:
  temp=Path(temporary);store=ConversationStore(temp/'chat.sqlite3');doc.chat_store=store;trace='6395ae2c22a549ac'
  store.start(doc.project_id,trace,request['prompt'],request['selection'],model='Recorded real Qwen3 response replay')
  try:
   generate_design(Replay(),'qwen3:4b',doc,b,request['prompt'],allow_existing=True,bindings=request['bindings'],trace_id=trace,record=lambda **f:doc.chat_update(trace,**f))
  except PlanError as exc:
   replay_error=str(exc);doc.chat_update(trace,status='error',error=replay_error,actual={'changed':0})
  else:raise AssertionError('Old fill-to-shell response should be rejected')
  assert np.array_equal(sc.blocks,before) and sc.metadata_digest()==nbt
  project=temp/'state.shipproj';doc.save_project(project);restored=Document.open_project(project);fresh=ConversationStore(temp/'fresh.sqlite3');fresh.restore(restored.chat_seed)
  assert fresh.get(restored.project_id,trace)['status']=='error'
  fresh.export(restored.project_id,temp/'chat.json','json');fresh.export(restored.project_id,temp/'chat.txt','txt')
  blue=doc.add_material('minecraft:blue_concrete');doc.selection=Bounds((0,0,0),tuple(v-1 for v in sc.size))
  with np.load(out/'changes.npz',allow_pickle=False) as diff:ii=diff['indices'].copy()
  p=Proposal(doc.revision,doc.selection,'ai_plan',blue,ii,sc.blocks.reshape(-1)[ii].copy(),0,0,after=np.full(len(ii),blue,np.int32),origin='ai',plan={'summary':'기존 두 파일의 외곽 변경 재현 (새 AI 추론 아님)','operations':[],'notes':[]},trace_id=uuid.uuid4().hex[:16])
  check=review(doc,p);assert check['hull_risk']
  try:doc.approve(p,approve_mass=True)
  except ValueError as exc:blocked=str(exc)
  else:raise AssertionError('Historical 86479 shell was not blocked')
  assert np.array_equal(sc.blocks,before)
  spatial=recognize(doc,doc.selection)
  assert doc.revision==p.revision and np.array_equal(sc.blocks,before)
  for candidate in spatial['candidates']:Bounds(tuple(candidate['lo']),tuple(candidate['hi'])).validate(sc.size)
  app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE)
  os.environ['SHIP_ARCHITECT_DATA_DIR']=str(temp/'ui-data');w=MainWindow();w.resize(1420,1000);errors=[];w.error=errors.append
  w.set_document(doc,'실제 파일 차이 재현 · 86,479개 외곽 변경 차단');w.ai_panel.chat_store.restore(store.bundle(doc.project_id));w.show();w._preview_ready(p)
  pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive(),60)
  assert not w.approve_button.isEnabled() and w.tabs.currentWidget() is w.review_view
  w.grab().save(str(out/'hull_blocked.png'));w.reject();pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive(),60)
  w.space_view.ready(spatial,doc)
  if spatial['candidates']:
   w.space_view.list.setCurrentRow(0);w.space_view.select();w.zone_clip.setChecked(True)
   first=spatial['candidates'][0];w.layer.setValue(min(sc.size[1]-1,first['floor_y']+4));w.clip.setChecked(True)
   pump_until(lambda:not w.view.workers_running() and not w.view.render_timer.isActive(),60);w.grab().save(str(out/'space_candidates.png'))
  w.ai_panel.chat_view.refresh(True);w.tabs.setCurrentWidget(w.ai_panel.chat_view);app.processEvents();w.grab().save(str(out/'chat_history.png'))
  doc.dirty=False;w.close();app.processEvents();assert not errors,errors
  # Wait for close cancellation of any automatic connection probe / view worker.
  pump_until(lambda:not w.isVisible(),30)
  summary={'diagnostic_replay':'recorded real response (not new inference)','replay_error':replay_error,'replay_changed':0,'historical_changed':len(ii),'blocked_even_with_mass_approval':blocked,
   'project_chat_portability':'passed','chat_txt_json_export':'passed','original_blocks':'unchanged','NBT':'identical','gui_disabled_approval':'passed',
   'spaces':{'candidates':len(spatial['candidates']),'floor_levels':sorted({c['floor_y'] for c in spatial['candidates']}),'kinds':{k:sum(c['kind']==k for c in spatial['candidates']) for k in ('room','corridor','open_area')},'limits':spatial['limits'],'semantic_accuracy':'requires user review; not independently ground-truthed'},'platform':sys.platform,'errors':errors}
  (out/'verification_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
 assert hashlib.sha256(args.before.read_bytes()).hexdigest()==baseline_hash and hashlib.sha256(args.after.read_bytes()).hexdigest()==target_hash

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--before',type=Path,required=True);p.add_argument('--after',type=Path,required=True);p.add_argument('--diagnostic',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('verification_v07'));verify(p.parse_args())
