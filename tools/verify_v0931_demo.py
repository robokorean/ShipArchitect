"""Real Qt offscreen synthetic UI verification and independently labelled shapes."""
import os,sys,json,copy,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from ship_architect.app import MainWindow,STYLE
from ship_architect.voxel_mesh import indexed_mesh,obj_bytes,validate_boundary
from verify_v093 import ready,wait
from verify_schem import pump_until
from create_v0931_demo import build
root=Path(__file__).resolve().parents[1];out=root/'docs/v0931_verification';out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
 os.environ['SHIP_ARCHITECT_DATA_DIR']=tmp;app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyleSheet(STYLE);w=MainWindow();w.ai_panel.timer.stop();w.resize(1720,1040);w.show();d,groups=build();before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();w.set_document(d);pump_until(lambda:ready(w),60)
 w.integrated_ui.open_properties(2);b=w.space_view.bulk;b.recognition.mode.setCurrentIndex(1);b.scan(True,False,True);pump_until(lambda:ready(w),60);assert len(d.spaces)==2 and d.schematic.metadata_digest()==meta
 b.group.setCurrentIndex(2);w.space_view.list.setCurrentRow(0);w.space_manager.blocks.setChecked(False);pump_until(lambda:ready(w),60);wait(100);w.grab().save(str(out/'synthetic_exterior_spaces.png'))
 c=b.selected()[0];mesh=indexed_mesh(c['volume_runs']);(out/'synthetic_hangar.obj').write_bytes(obj_bytes(mesh));validation=validate_boundary(c['volume_runs'],mesh)
 exact={}
 for name in ('L+pillar+upper','C','open_hangar','roof_slit'):
  candidate=next(c for c in d.space_candidates if {(x,y,z) for y,z,a,B in c['volume_runs'] for x in range(a,B+1)}==groups[name]);exact[name]={'cells':len(groups[name]),'intersection_over_union':1.,'missed_cells':0,'solid_intrusions':candidate['validation']['solid_intrusions'],'decision':candidate['decision']}
 result={'independent_fixture_checks':exact,'confirmed_spaces':len(d.spaces),'new_auto_registered':sum(bool(s.get('auto_registered')) for s in d.spaces),'NBT_unchanged':d.schematic.metadata_digest()==meta,'blocks_unchanged':bool((before==d.schematic.blocks).all()),'hangar_mesh_validation':validation,'Qt_offscreen':True,'Windows_direct':False};(out/'synthetic_gui_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));d.dirty=False;w.close();pump_until(lambda:not w.isVisible(),60)
