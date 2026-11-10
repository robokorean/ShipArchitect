"""Build the reviewed v0.9.3 source distribution, preserving the baseline archive."""
import ast,hashlib,json,sys,zipfile,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASELINE='5f97f9df4af0ca18dfd45ff9d6dd53487cf91bf930ec11fbe993b6bed6918ac8'
def included(p):
 return p.is_file() and not any(x.startswith('.') or x=='__pycache__' for x in p.relative_to(ROOT).parts) and p.suffix not in ('.pyc','.pyo','.nbc','.nbi','.bak','.tmp') and not p.name.startswith('core.') and p.name not in ('core','startup.log')
def build(output):
 assert hashlib.sha256((ROOT/'legacy/ShipArchitect_v0.9.2.zip').read_bytes()).hexdigest()==BASELINE
 files=sorted(p for p in ROOT.rglob('*') if included(p));python_files=[p for p in files if p.suffix=='.py']
 for p in python_files:compile(ast.parse(p.read_text(encoding='utf-8-sig')),str(p),'exec')
 check={'version':'0.9.3','all_python_AST_compiled':len(python_files),'original_v092_sha256':BASELINE,'original_v092_identical':True,'excluded':'caches, dot directories, temporary files, backups, crash dumps, startup logs','required':['START_WINDOWS.cmd','launch.py','requirements.txt','V093_TEST_GUIDE.md','docs/TEST_REPORT_V093.md','examples/v093_3d_validation.shipproj','src/ship_architect/spatial3d.py']}
 (ROOT/'docs/PACKAGE_CHECK_V093.json').write_text(json.dumps(check,ensure_ascii=False,indent=2)+'\n')
 manifest=ROOT/'docs/PACKAGE_MANIFEST.sha256';files=sorted(p for p in ROOT.rglob('*') if included(p) and p!=manifest)
 manifest.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(ROOT).as_posix()+'\n' for p in files))
 files.append(manifest);output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for p in sorted(files):z.write(p,'ShipArchitect/'+p.relative_to(ROOT).as_posix())
 with zipfile.ZipFile(output) as z:
  assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
  for p in check['required']:assert 'ShipArchitect/'+p in z.namelist()
  assert hashlib.sha256(z.read('ShipArchitect/legacy/ShipArchitect_v0.9.2.zip')).hexdigest()==BASELINE
  # Smoke import and fixture load from the delivered archive, not the working tree.
  with tempfile.TemporaryDirectory() as tmp:
   z.extractall(tmp);folder=Path(tmp)/'ShipArchitect'
   code="import sys;sys.path.insert(0,'src');from ship_architect import __version__;from ship_architect.model import Document;assert __version__=='0.9.3';d=Document.open_project('examples/v093_3d_validation.shipproj');assert len(d.spaces)==1;print(__version__,d.schematic.size)"
   smoke=subprocess.run([sys.executable,'-c',code],cwd=folder,capture_output=True,text=True,check=True)
 result={'file':str(output),'size_bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(files),'CRC':'passed','AST_files':len(python_files),'baseline_identical':True,'extracted_import_fixture_smoke':smoke.stdout.strip()};print(json.dumps(result,ensure_ascii=False,indent=2));return result
if __name__=='__main__':build(sys.argv[1] if len(sys.argv)>1 else ROOT.parent.parent/'ShipArchitect_v0.9.3.zip')
