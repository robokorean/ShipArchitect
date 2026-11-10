"""Build the reviewed v0.9.3.1 source distribution, preserving the baseline archive."""
import ast,hashlib,json,sys,zipfile,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASELINE='4671e0bb8e8e565c3f7ea26208b019e2d0fb08f086430838b38c297a12d43528'
def included(p):
 return p.is_file() and not any(x.startswith('.') or x=='__pycache__' for x in p.relative_to(ROOT).parts) and p.suffix not in ('.pyc','.pyo','.nbc','.nbi','.bak','.tmp') and not p.name.startswith('core.') and p.name not in ('core','startup.log')
def build(output):
 assert hashlib.sha256((ROOT/'legacy/ShipArchitect_v0.9.3.zip').read_bytes()).hexdigest()==BASELINE
 files=sorted(p for p in ROOT.rglob('*') if included(p));python_files=[p for p in files if p.suffix=='.py']
 for p in python_files:compile(ast.parse(p.read_text(encoding='utf-8-sig')),str(p),'exec')
 check={'version':'0.9.3.1','all_python_AST_compiled':len(python_files),'original_v093_sha256':BASELINE,'original_v093_identical':True,'excluded':'caches, dot directories, temporary files, backups, crash dumps, startup logs','required':['START_WINDOWS.cmd','launch.py','requirements.txt','V0931_TEST_GUIDE.md','docs/TEST_REPORT_V0931.md','docs/RECOGNITION_COMPARISON_V0931.md','examples/v0931_exterior_demo.shipproj','src/ship_architect/exterior_recognition.py']}
 (ROOT/'docs/PACKAGE_CHECK_V0931.json').write_text(json.dumps(check,ensure_ascii=False,indent=2)+'\n')
 manifest=ROOT/'docs/PACKAGE_MANIFEST.sha256';files=sorted(p for p in ROOT.rglob('*') if included(p) and p!=manifest)
 manifest.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(ROOT).as_posix()+'\n' for p in files))
 files.append(manifest);output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for p in sorted(files):z.write(p,'ShipArchitect/'+p.relative_to(ROOT).as_posix())
 with zipfile.ZipFile(output) as z:
  assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
  for p in check['required']:assert 'ShipArchitect/'+p in z.namelist()
  assert hashlib.sha256(z.read('ShipArchitect/legacy/ShipArchitect_v0.9.3.zip')).hexdigest()==BASELINE
  # Smoke import and fixture load from the delivered archive, not the working tree.
  with tempfile.TemporaryDirectory() as tmp:
   z.extractall(tmp);folder=Path(tmp)/'ShipArchitect'
   code="import sys;sys.path.insert(0,'src');from ship_architect import __version__;from ship_architect.model import Document;assert __version__=='0.9.3.1';d=Document.open_project('examples/v0931_exterior_demo.shipproj');assert len(d.spaces)==0;print(__version__,d.schematic.size)"
   smoke=subprocess.run([sys.executable,'-c',code],cwd=folder,capture_output=True,text=True,check=True)
 result={'file':str(output),'size_bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(files),'CRC':'passed','AST_files':len(python_files),'baseline_identical':True,'extracted_import_fixture_smoke':smoke.stdout.strip()};print(json.dumps(result,ensure_ascii=False,indent=2));return result
if __name__=='__main__':build(sys.argv[1] if len(sys.argv)>1 else ROOT.parent.parent/'ShipArchitect_v0.9.3.1.zip')
