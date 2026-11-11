import json,gzip,zipfile,copy,time
import numpy as np
import pytest
from PySide6.QtCore import Qt,QEvent,QPointF
from PySide6.QtTest import QTest
from test_ai_gui import window,ready,wait_for,left_mouse
from test_v09_gui import build_demo
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.spaces import recognize
from ship_architect.space_model import new_space,normalize,from_candidate
from ship_architect.space_geometry import boundary_quads
from ship_architect.equipment import inventory,search_records,category,owner_index,ownership
from ship_architect.mesh import build_scene,MeshCache
from ship_architect.display import DisplayState
from ship_architect import diagnostics
from test_v091_workspace import move

@pytest.mark.parametrize('runs',[
 [[1,1,1,4],[1,2,1,1],[1,3,1,1]],
 [[1,1,1,3],[2,1,1,2],[3,1,2,2],[2,2,2,2]],
 [[1,1,1,1],[1,1,3,3],[3,1,1,3]],
])
def test_exact_3d_volume_boundary_not_bbox(runs):
 runs=normalize(runs,(8,8,8));cells={(x,y,z) for y,z,a,b in runs for x in range(a,b+1)};q,n=boundary_quads(runs)
 area=0;volume=0
 for quad,normal in zip(q,n):
  face_area=np.linalg.norm(np.cross(quad[1]-quad[0],quad[3]-quad[0]));area+=face_area;volume+=face_area*np.dot(quad.mean(axis=0),normal)/3
 expected=sum(tuple(np.array(c)+d) not in cells for c in cells for d in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)))
 assert area==expected and volume==pytest.approx(len(cells)) and any(abs(normal[1])==0 for normal in n)


def roof_room():
 s=Schematic.empty(12,12,12);s.blocks[0,1:11,1:11]=1;s.blocks[6,1:11,1:11]=1;s.blocks[1:6,1,1:11]=1;s.blocks[1:6,10,1:11]=1;s.blocks[1:6,1:11,1]=1;s.blocks[1:6,1:11,10]=1
 return Document(s)


def test_rooftop_is_not_room_and_candidate_confirmation_exact():
 d=roof_room();manual=new_space([[2,2,2,4],[3,2,2,3]],d.schematic.size);d.spaces=[manual];before=d.schematic.blocks.copy();r=recognize(d,d.selection)
 assert any(c['floor_y']==1 and c['kind']=='room' for c in r['candidates'])
 roofs=[c for c in r['candidates'] if c['floor_y']==7];assert roofs and all(c['kind']=='open_area' and c['exclude_from_bulk'] for c in roofs)
 for c in r['candidates']:
  confirmed=from_candidate(d,c);assert confirmed['runs']==c['volume_runs']
 assert d.spaces==[manual] and np.array_equal(before,d.schematic.blocks)


def test_equipment_discovery_unknown_and_after_20000_coordinates(tmp_path):
 sc=Schematic.empty(64,10,40);sc.palette[2]='mystery:controller[facing=east]';sc.palette[3]='computercraft:computer_advanced[facing=north]';unknown=2;cc=3;d=Document(sc);d.schematic.blocks[:]=unknown;d.schematic.blocks[9,39,63]=cc
 before=d.schematic.blocks.copy();meta=d.schematic.metadata_digest();inv=inventory(d)
 assert inv['total']==25600 and inv['unclassified']==25599 and sum(b-a+1 for runs in inv['position_runs'].values() for _,_,a,b in runs)==25600
 assert inv['positions_truncated'] and len(inv['records'])<=20000
 assert search_records(d,inv,'computer_advanced')[0]['position']==[63,9,39]
 tail=search_records(d,inv,'mystery',25000,10);assert len(tail)==10 and all(r['category']=='미분류 모드 블록' for r in tail)
 out=tmp_path/'devices.schem';d.export(out,approved=True);s,_=load_file(out);assert np.array_equal(s.blocks,before) and s.metadata_digest()==meta and s.palette[unknown]=='mystery:controller[facing=east]'


def test_equipment_exact_owner_not_bbox_and_known_namespaces():
 d=Document(Schematic.empty(8,8,8));d.spaces=[new_space([[1,1,1,5],[1,2,1,1],[1,3,1,1]],d.schematic.size)];index=owner_index(d)
 assert ownership((3,3,3),index,d.schematic.size)==([],[])
 assert ownership((1,1,1),index,d.schematic.size)[0]==[d.spaces[0]['id']]
 assert category('create_newage:generator')=='전력 설비 후보' and category('createaddition:capacitor')=='전력 설비 후보'
 assert category('advancedperipherals:chat_box')=='ComputerCraft'


def test_hidden_local_chunk_reuses_unaffected_meshes():
 d=Document(Schematic.empty(96,12,40));d.schematic.blocks[:]=1;cache=MeshCache();a=build_scene(d.schematic,cache=cache,display=DisplayState(dimensions=d.schematic.size));state=DisplayState(hidden=(1,),dimensions=d.schematic.size);b=build_scene(d.schematic,cache=cache,display=state)
 assert b.stats['cache_hits']>=b.stats['chunks']-2 and b.stats['cache_hits']>0


def test_full_mesh_frames_keep_geometry_and_overlay_toggles_do_not_clear_frame(window):
 w=window;w.set_document(build_demo());wait_for(lambda:ready(w));w.workbench.open_tab(w.view);v=w.view;scene=v.mesh;frame=v.frame;serial=v.mesh_serial
 w.space_manager.overlays.setChecked(False);w.space_manager.overlays.setChecked(True);v.rebuild();assert v.mesh is scene and v.frame is not None and v.mesh_serial==serial
 rendered=v.interaction_renders
 center=QPointF(v.rect().center())
 left_mouse(v,QEvent.Type.MouseButtonPress,center)
 for step in (1,2):
  position=center+QPointF(step*25,step*10)
  left_mouse(v,QEvent.Type.MouseMove,position)
  wait_for(lambda: v.interaction_renders>=rendered+step, seconds=30)
  assert v.frame is not None and v.mesh is scene
 assert v.render_stats['scene_faces']==len(scene) and v.pick(QPointF(100,100)) is None
 left_mouse(v,QEvent.Type.MouseButtonRelease,position)
 wait_for(lambda:ready(w),seconds=30)
 assert v.pick_buffer is not None


def test_analysis_tabs_share_right_area_and_preserve_data(window):
 w=window;ui=w.integrated_ui;before=w.doc.schematic.blocks.copy();ui.open_analysis();QTest.qWait(30)
 assert w.dockWidgetArea(ui.analysis_dock)==Qt.DockWidgetArea.RightDockWidgetArea and ui.analysis_dock in w.tabifiedDockWidgets(w.workbench.editor_dock)
 assert ui.scene_pages.count()==2 and ui.properties.count()==5
 ui.open_properties(0);ui.open_analysis();assert np.array_equal(before,w.doc.schematic.blocks)
 state=ui.ui_state();assert any(d['name']=='structure_analysis' for d in state['docks'])


def test_equipment_only_scan_survives_visibility_and_tree_updates(window):
 w=window;w.set_document(build_demo());w.network_view.scan_equipment();wait_for(lambda:ready(w));inv=w.network_view.equipment;assert inv is not None
 w.space_manager.overlays.setChecked(False);w.space_manager.overlays.setChecked(True);w.space_manager.refresh();assert w.network_view.equipment is inv
 w.workbench.rebuild_tree(True);assert any(r['category']=='ComputerCraft' for r in w.workbench.equipment)


def test_diagnostic_failed_excluded_unknown_geometry_and_auth_redaction(tmp_path):
 diagnostics.setup(tmp_path/'logs');d=roof_room();r=recognize(d,d.selection);d.spaces=[from_candidate(d,next(c for c in r['candidates'] if c['kind']=='room'))]
 with pytest.raises(ValueError):recognize(d,Bounds((0,0,0),(11,0,11)))
 inventory(d);diagnostics.event('credentials',payload='Bearer TOPSECRET http://bob:PASS@localhost',api_key='KEY')
 for i in range(300):d.spaces[0]['runs'].append([7,1,1,1])
 target=diagnostics.report(tmp_path/'diag.zip',document=d,ui_state={'panels':'right tabs','token':'NO'})
 with zipfile.ZipFile(target) as z:
  assert 'evidence_index.json' in z.namelist() and 'spaces_confirmed.jsonl' in z.namelist() and 'SUMMARY.txt' in z.namelist()
  evidence=[n for n in z.namelist() if n.startswith('evidence/')];assert any('spaces_failed-' in n for n in evidence) and any('equipment-' in n for n in evidence)
  contents='\n'.join(z.read(n).decode() for n in z.namelist() if n.endswith(('.json','.txt','.jsonl')));assert all(secret not in contents for secret in ('TOPSECRET','PASS@','KEY','"NO"'))
  assert len(z.read('spaces_confirmed.jsonl').splitlines())==1+len(d.spaces[0]['runs'])
 assert diagnostics.report(target)!=target


def test_async_full_scene_refresh_with_running_qt_event_loop(window):
 from PySide6.QtCore import QEventLoop,QTimer
 w=window;sc=Schematic.empty(64,32,64);sc.blocks[0,:,:]=1;sc.blocks[1:24,12:15,4:58]=1;w.set_document(Document(sc));wait_for(lambda:ready(w),30);w.workbench.open_tab(w.view);v=w.view;scene=v.mesh;start=v.interaction_renders
 QTest.mousePress(v,Qt.MouseButton.LeftButton,pos=v.rect().center())
 for i in range(24):
  move(v,50+4*i,140+i);loop=QEventLoop();QTimer.singleShot(20,loop.quit);loop.exec();assert v.frame is not None and v.mesh is scene
 assert v.interaction_renders>start+1 and v.render_stats['scene_faces']==len(scene)
 QTest.mouseRelease(v,Qt.MouseButton.LeftButton,pos=v.rect().center());wait_for(lambda:ready(w));assert v.pick_buffer is not None


def test_diagnostic_ui_export_is_background_and_contains_layout(window,tmp_path,monkeypatch):
 from PySide6.QtWidgets import QFileDialog,QMessageBox
 w=window;out=tmp_path/'ui_diag.zip';notices=[]
 monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(out),'ZIP (*.zip)'))
 monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
 monkeypatch.setattr(QMessageBox,'information',lambda *a,**k:notices.append(a))
 w.export_diagnostics();wait_for(lambda:ready(w));assert notices
 with zipfile.ZipFile(out) as z:assert json.loads(z.read('report.json'))['ui']['docks'] and 'spaces_confirmed.jsonl' in z.namelist()


def test_equipment_group_full_coordinates_visibility_and_persistence(window):
 from ship_architect.display import validate_settings
 w=window;sc=Schematic.empty(64,10,40);sc.palette[2]='unknown:machine';sc.blocks[:]=2;d=Document(sc);w.set_document(d);w.network_view.scan_equipment();wait_for(lambda:ready(w));b=w.workbench;b.rebuild_tree(True)
 nodes=b.tree.findItems('미분류 모드 블록',Qt.MatchFlag.MatchExactly|Qt.MatchFlag.MatchRecursive,0);assert nodes;nodes[0].setCheckState(0,Qt.CheckState.Unchecked);wait_for(lambda:ready(w))
 assert len(b.hidden_blocks)==25600 and 'equipgroup:미분류 모드 블록' in b.hidden_groups
 settings=b.settings();assert len(validate_settings(settings,sc.size)['hidden'])==25600 and d.schematic.blocks.min()==2
 b.restore_visibility();wait_for(lambda:ready(w));assert not b.hidden_blocks and np.all(sc.blocks==2)


def test_display_mask_unsorted_hidden_indices_is_exact():
 state=DisplayState(hidden=(7,1,4),dimensions=(8,2,2));mask=state.mask((0,0,0),(2,2,8));assert not mask[0,0,[1,4,7]].any() and mask.sum()==29


def test_tall_hangar_roof_above_32_cells_is_not_misclassified_outdoors():
 s=Schematic.empty(10,66,10);s.blocks[0,1:9,1:9]=1;s.blocks[60,1:9,1:9]=1;s.blocks[1:60,1,1:9]=1;s.blocks[1:60,8,1:9]=1;s.blocks[1:60,1:9,1]=1;s.blocks[1:60,1:9,8]=1;d=Document(s);result=recognize(d,d.selection)
 interior=next(c for c in result['candidates'] if c['floor_y']==1);assert interior['kind']=='room' and interior['ceiling_height_min']==59 and interior['hi'][1]==59
 assert result['limits']['ceiling_search']==128
