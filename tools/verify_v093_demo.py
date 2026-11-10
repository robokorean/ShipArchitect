"""Qt verification of packaged synthetic expectations; source fixture unchanged."""
import os,sys,json,copy,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from ship_architect.app import MainWindow,STYLE
from ship_architect.model import Document
from verify_v093 import ready,wait
from verify_schem import pump_until
root=Path(__file__).resolve().parents[1];out=root/'docs/v093_verification'
with tempfile.TemporaryDirectory() as tmp:
 os.environ['SHIP_ARCHITECT_DATA_DIR']=tmp;app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK KR',10));app.setStyleSheet(STYLE);w=MainWindow();w.ai_panel.timer.stop();w.resize(1720,1040);w.show();d=Document.open_project(root/'examples/v093_3d_validation.shipproj');manual=copy.deepcopy(d.spaces);meta=d.schematic.metadata_digest();w.set_document(d);pump_until(lambda:ready(w),60)
 w.integrated_ui.open_properties(2);w.space_view.bulk.scan(True,False,True);pump_until(lambda:ready(w),60);assert len(d.spaces)==3 and d.spaces[:1]==manual and d.schematic.metadata_digest()==meta
 w.space_view.bulk.group.setCurrentIndex(4);w.space_view.list.setCurrentRow(0);w.view.focus_bounds=d.selection;w.view.reset_camera();pump_until(lambda:ready(w),60);wait(100);w.grab().save(str(out/'synthetic_automatic_rooms.png'))
 result={'confirmed_spaces':len(d.spaces),'new_auto_registered':sum(bool(s.get('auto_registered')) for s in d.spaces),'manual_preserved':d.spaces[:1]==manual,'volumes':sorted(sum(b-a+1 for _,_,a,b in s['runs']) for s in d.spaces),'NBT_unchanged':d.schematic.metadata_digest()==meta,'Qt_offscreen':True,'Windows_direct':False};(out/'synthetic_gui_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result);d.dirty=False;w.close();pump_until(lambda:not w.isVisible(),60)
