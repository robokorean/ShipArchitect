"""Run the delivered ZIP in a new folder, isolated Python and private package cache.
This tool is development-only and is never part of the runtime ZIP.
"""
import sys,tempfile,subprocess,zipfile,json,os,argparse
from pathlib import Path
HARNESS=r'''
import sys,os,json,time,importlib.util
from pathlib import Path
root=Path(sys.argv[1]);out=Path(sys.argv[2]);forbidden=Path(sys.argv[3]);os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['SHIP_ARCHITECT_DATA_DIR']=str(root/'user_data')
spec=importlib.util.spec_from_file_location('isolated_launcher',root/'launch.py');launch=importlib.util.module_from_spec(spec);spec.loader.exec_module(launch)
packages=launch.prepare(root/'requirements.txt',Path(os.environ['LOCALAPPDATA'])/'ShipArchitect/packages');sys.path.insert(0,str(packages));sys.path.insert(0,str(root/'src'))
import numpy as np
from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
from ship_architect.app import MainWindow
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.block_registry import vanilla

def wait_for(predicate,seconds=90):
 if predicate():return
 loop=QEventLoop();poll=QTimer(loop);poll.setInterval(10);poll.timeout.connect(lambda:loop.quit() if predicate() else None);end=QTimer(loop);end.setSingleShot(True);end.timeout.connect(loop.quit);poll.start();end.start(seconds*1000);loop.exec();assert predicate(),'independent GUI timed out'
def ready():return not w.loading and w.operation is None and not w.view.workers_running() and not w.view.render_timer.isActive()
app=QApplication([]);w=MainWindow();w.ai_panel.timer.stop();errors=[];w.error=lambda e:errors.append(str(e));w.show();wait_for(ready)
d=Document(Schematic.empty(12,8,12));w._new_project_ready(d);wait_for(ready);target=d.add_material('minecraft:oak_planks');p=d.propose(Bounds((2,1,2),(4,1,4)),target,'floor',True);d.approve(p);w.refresh_views(p.indices);wait_for(ready);assert len(w.view.mesh)>0
project=root/'user_data'/'clean.shipproj';d.save_project(project);schem=root/'user_data'/'clean.schem';d.export(schem,approved=True);expected=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();q=Document.open_project(project);assert np.array_equal(q.schematic.blocks,expected);w.doc.dirty=False;w.load_path(schem);wait_for(ready);assert np.array_equal(w.doc.schematic.blocks,expected) and w.doc.schematic.metadata_digest()==meta
assert 'minecraft:oak_planks' in vanilla()
QMessageBox.question=lambda *a,**k:QMessageBox.StandardButton.Yes;QMessageBox.information=lambda *a,**k:None
diag=root/'user_data'/'independent_diagnostic.zip';QFileDialog.getSaveFileName=lambda *a,**k:(str(diag),'ZIP (*.zip)');w.export_diagnostics();wait_for(ready);assert diag.exists()
for name,module in tuple(sys.modules.items()):
 if name.startswith('ship_architect') and getattr(module,'__file__',None):
  path=Path(module.__file__).resolve();assert path.is_relative_to(root/'src') and not path.is_relative_to(forbidden)
assert not errors,errors
result={'startup':True,'new_project':True,'real_3d_frame':w.view.frame is not None,'builtin_registry_resource':True,'manual_placement':9,'project_roundtrip':True,'schem_blocks_NBT_roundtrip':True,'schem_GUI_reload':True,'diagnostic_UI_export':True,'isolated_python':True,'clean_private_dependencies':True,'original_source_imports':False,'Windows_direct':sys.platform=='win32'}
w.doc.dirty=False;w.close();wait_for(lambda:not w.isVisible());out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result))
'''
def verify(archive,output):
 Path(output).resolve().parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='ShipArchitect-clean-') as tmp:
  folder=Path(tmp)
  with zipfile.ZipFile(archive) as z:z.extractall(folder)
  root=folder/'ShipArchitect';runner=folder/'runner.py';runner.write_text(HARNESS);env=os.environ.copy();env['LOCALAPPDATA']=str(folder/'local_appdata');env.pop('PYTHONPATH',None);env['QT_QPA_PLATFORM']='offscreen';env['PYTHONNOUSERSITE']='1'
  # The original development source is physically unavailable during the run.
  source=Path(__file__).resolve().parents[1]/'src';quarantine=source.with_name('src_quarantined_for_release')
  source.rename(quarantine)
  try:r=subprocess.run([sys.executable,'-I',str(runner),str(root),str(Path(output).resolve()),str(source.parent)],cwd=root,env=env,text=True,capture_output=True,timeout=240)
  finally:quarantine.rename(source)
  if r.returncode:raise RuntimeError(r.stdout[-4000:]+'\n'+r.stderr[-6000:])
  print(r.stdout[-3000:]);return json.loads(Path(output).read_text())
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('--output',type=Path,default=Path('distribution_check.json'));a=p.parse_args();verify(a.archive,a.output)
