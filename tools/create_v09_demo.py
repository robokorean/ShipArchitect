"""Deterministic small connection fixture; generates no user's ship data."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import nbtlib as nbt
from ship_architect.schematic import Schematic
from ship_architect.model import Document,Bounds
from ship_architect.space_model import new_space,box_runs

def build_demo(door_open=True,stair_facing='east'):
 sc=Schematic.empty(48,16,30)
 def add(state):
  for i,s in sc.palette.items():
   if s==state:return i
  i=max(sc.palette)+1;sc.palette[i]=state;return i
 stone=add('minecraft:stone');a=sc.blocks
 a[0,2:7,1:17]=stone;a[1:4,2:7,8]=stone
 a[1:4,2,6:11]=stone;a[1:4,6,6:11]=stone
 for y,half in ((1,'lower'),(2,'upper')):a[y,4,8]=add(f'minecraft:oak_door[facing=east,half={half},hinge=left,open={str(door_open).lower()},powered=false]')
 a[0,10:13,1:4]=stone
 for x,y in ((4,0),(5,1),(6,2),(7,3)):a[y,11,x]=add(f'minecraft:stone_stairs[facing={stair_facing},half=bottom,shape=straight,waterlogged=false]')
 a[4,10:13,8:13]=stone
 a[0,20:24,20:24]=stone;a[7,20:24,20:24]=stone
 a[1,21,19]=a[8,21,19]=add('create:elevator_contact[facing=east]')
 a[0,2:6,35:39]=stone
 for state,xyz in [('computercraft:computer_advanced[facing=north]',(40,1,21)),('vs_clockwork:propeller[facing=up]',(41,1,21)),('create:creative_motor[facing=east]',(42,1,21))]:a[xyz[1],xyz[2],xyz[0]]=add(state)
 sc.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([40,1,21]),'Label':nbt.String('V09 보존 확인')}))
 d=Document(sc);spaces=[]
 for sid,name,lo,hi in [('A','A 출발 방',(1,1,2),(5,3,6)),('B','B 문 건너 방',(11,1,2),(16,3,6)),('C','C 계단 하층',(1,1,10),(3,3,12)),('D','D 계단 상층',(9,5,10),(12,7,12)),('E','E 승강기 하층',(20,1,20),(23,3,23)),('F','F 승강기 상층',(20,8,20),(23,10,23)),('G','G 고립 방',(35,1,2),(38,3,5)),('H','H 복도',(6,1,3),(7,3,5)),('A_DUP','A 중복 공간',(1,1,2),(5,3,6))]:
  s=new_space(box_runs(Bounds(lo,hi)),sc.size,name,'통로' if sid=='H' else '테스트');s['id']=sid;s['color']={'A':'#64b5d7','B':'#5ad6b2','C':'#c49cdb','D':'#e6c17a'}.get(sid,'#b0bdcc');spaces.append(s)
 d.set_spaces(spaces,'space_fixture',active='A');d.selection=Bounds((0,0,0),(47,15,29));return d

EXPECTED={'A->B':{'open':'connected','closed':'blocked'},'C->D':{'east_stairs':'connected','west_stairs':'disconnected'},'A->G':'disconnected','A->A_DUP':'overlap','E->F':{'navigation':'disconnected','elevator_evidence':'uncertain'}}
def expected_pairs(variant='open'):
 # Independent semantic expectation matrix: a doorway joins the A/H side to B;
 # oriented stairs join C/D; lifts E/F remain uncertain, G is isolated, A_DUP overlaps A.
 ids=['A','B','C','D','E','F','G','H','A_DUP'];pairs=[]
 for i,left in enumerate(ids):
  for right in ids[i+1:]:
   pair={left,right};status='disconnected'
   if pair=={'A','A_DUP'}:status='overlap'
   elif pair<= {'A','B','H','A_DUP'}:status='blocked' if variant=='closed' and 'B' in pair else 'connected'
   elif pair=={'C','D'}:status='disconnected' if variant=='wrong_stairs' else 'connected'
   elif pair=={'E','F'}:status='uncertain'
   pairs.append({'from':left,'to':right,'ui_status':status,'nav_status':'disconnected' if pair=={'E','F'} else status})
 return pairs

if __name__=='__main__':
 folder=Path(__file__).resolve().parents[1]/'examples';folder.mkdir(exist_ok=True)
 for variant,opened,facing in [('open',True,'east'),('closed',False,'east'),('wrong_stairs',True,'west')]:
  d=build_demo(opened,facing);name='v09_connections' if variant=='open' else 'v09_connections_'+variant
  d.save_project(folder/(name+'.shipproj'))
  if variant=='open':(folder/(name+'.schem')).write_bytes(d.schematic.to_bytes())
 (folder/'v09_expected_connections.json').write_text(json.dumps({variant:expected_pairs(variant) for variant in ('open','closed','wrong_stairs')},ensure_ascii=False,indent=2),encoding='utf8')
 print('Saved 3 v09 fixtures with 9 spaces and 108 explicit pair expectations')
