"""Actual-input exterior/legacy comparison, source invariance and diagnostic evidence."""
import os,sys,time,json,gzip,hashlib,tempfile,zipfile,resource,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from ship_architect.model import Document,Bounds
from ship_architect.schematic import load_file
from ship_architect.recognition_compare import compare_methods
from ship_architect.analysis_state import commit,register
from ship_architect.diagnostics import setup,report

def main(args):
 out=args.output;out.mkdir(parents=True,exist_ok=True);hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.project,args.schem,args.diagnostic)};d=Document.open_project(args.project);before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();spaces=json.loads(json.dumps(d.spaces));bounds=Bounds((0,0,0),tuple(n-1 for n in d.schematic.size));last=[-1]
 def progress(n,s):
  if n//10!=last[0]:print(n,s,flush=True);last[0]=n//10
 with tempfile.TemporaryDirectory() as tmp:
  setup(Path(tmp)/'logs');comparison=compare_methods(d,bounds,progress);new=comparison['new'];old=comparison['old'];prepared,result,added=register(d,new);assert prepared[:len(spaces)]==spaces
  assert all(not d.schematic.solid(d.schematic.blocks[y,z,a:b+1]).any() for c in new['candidates'] for y,z,a,b in c['volume_runs'])
  state=dict(d.analysis_state,comparison=comparison,last_result=result);commit(d,state,prepared,'exterior_actual_verification');project=Path(tmp)/'verified.shipproj';d.save_project(project);q=Document.open_project(project);assert q.spaces==d.spaces and q.analysis_state==d.analysis_state
  d.export(Path(tmp)/'verified.schem',approved=True);s,_=load_file(Path(tmp)/'verified.schem');assert np.array_equal(s.blocks,before) and s.metadata_digest()==meta
  actual_sc,_=load_file(args.schem);assert actual_sc.size==d.schematic.size
  assert np.array_equal(before,d.schematic.blocks) and d.schematic.metadata_digest()==meta
  diagnostic=report(out/'actual_diagnostic.zip',document=d)
 with gzip.open(out/'comparison_exact.json.gz','wt',encoding='utf8') as f:json.dump(comparison,f,ensure_ascii=False,separators=(',',':'))
 with zipfile.ZipFile(args.diagnostic) as z:supplied=json.loads(z.read('report.json'));events=[json.loads(l) for l in z.read('logs/events.jsonl').decode().splitlines()]
 def compact(r):return {k:v for k,v in r.items() if k not in ('candidates','shell_face_runs','semantic_features','connections','virtual_boundary_sample')}
 summary={'inputs_sha256':hashes,'size_xyz':list(d.schematic.size),'volume':int(before.size),'non_air':int(d.schematic.solid(before).sum()),'original_spaces':len(spaces),'spaces_after':len(d.spaces),'new_registered':len(added),'old':compact(old),'new':compact(new),'matches':comparison['matches'],'old_candidate_volumes':[c['volume'] for c in old['candidates']],'new_candidate_volumes':[c['volume'] for c in new['candidates']],'source_blocks_NBT_identical':True,'original_manual_spaces_identical':True,'project_exact_shapes_comparison_roundtrip':True,'actual_schem_vs_project_block_differences':int(np.count_nonzero(actual_sc.blocks!=before)),'diagnostic':diagnostic.name,'supplied_diagnostic_version':supplied['version'],'supplied_events':len(events),'peak_rss_mib_linux':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),'accuracy_ground_truth_real_ship':None,'interpretation':'Same scope, exact occupancy comparison. Candidate count/IoU is not room accuracy. No manual room ground truth supplied.','Windows_direct':False,'live_Ollama':False}
 (out/'real_comparison_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps({k:summary[k] for k in ('new_registered','old_candidate_volumes','new_candidate_volumes','peak_rss_mib_linux')},ensure_ascii=False),flush=True)
 for p in (args.project,args.schem,args.diagnostic):assert hashlib.sha256(p.read_bytes()).hexdigest()==hashes[p.name]
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--schem',type=Path,required=True);p.add_argument('--diagnostic',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('docs/v0931_verification'));main(p.parse_args())
