"""Reproducible exact-3D spaces, false positives, manual boundary and CC NBT fixture."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import nbtlib as nbt
from ship_architect.model import Document
from ship_architect.schematic import Schematic
from ship_architect.space_model import new_space
from ship_architect.spatial3d import recognize_3d,fingerprint

def build():
 s=Schematic.empty(58,18,36);s.blocks[:]=1
 groups={}
 groups['L자 실내']={(x,y,z) for x in range(3,16) for y in range(2,7) for z in range(3,15) if x<8 or z<8}
 groups['높이 변화·다층 연결']={(x,y,z) for x in range(25,39) for z in range(3,16) for y in range(2,11) if y<5 or x<30}
 groups['천장 한 칸 틈']={(x,12,z) for x in range(3,13) for z in range(21,29)}
 groups['좁은 벽 틈·통로']={(18,y,z) for y in range(2,9) for z in range(20,32)}
 groups['미확인 모드 경계']={(x,y,z) for x in range(28,39) for y in range(2,7) for z in range(23,32)}
 groups['바닥 없는 외부 연결']={(x,y,z) for x in range(44,52) for y in range(0,7) for z in range(4,15)}
 groups['사용자 보정 공간']={(x,y,z) for x in range(45,53) for y in range(11,15) for z in range(3,9)}
 for cells in groups.values():
  for x,y,z in cells:s.blocks[y,z,x]=0
 s.palette[2]='unknown:fixture_wall';s.blocks[3,24,27]=2
 s.palette[3]='computercraft:computer_advanced[facing=north]';s.blocks[1,1,1]=3
 s.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([1,1,1]),'ComputerId':nbt.Int(15),'Label':nbt.String('v093 보존 시험')}))
 decisions={'L자 실내':'auto','높이 변화·다층 연결':'auto','천장 한 칸 틈':'exclude','좁은 벽 틈·통로':'review','미확인 모드 경계':'review','바닥 없는 외부 연결':'exclude','사용자 보정 공간':'auto'}
 d=Document(s);r=recognize_3d(d,d.selection);expected=[]
 for name,cells in groups.items():
  c=next(c for c in r['candidates'] if {(x,y,z) for y,z,a,b in c['volume_runs'] for x in range(a,b+1)}==cells)
  assert c['decision']==decisions[name],(name,c['decision'])
  expected.append({'name':name,'decision':decisions[name],'volume':len(cells),'fingerprint':c['fingerprint'],'lo':c['lo'],'hi':c['hi'],'reasons':c['reasons']})
  if name=='사용자 보정 공간':
   sp=new_space(c['volume_runs'],s.size,name);sp['manual_modified']=True;d.spaces=[sp]
 return d,expected
if __name__=='__main__':
 out=Path(__file__).resolve().parents[1]/'examples';d,expected=build();d.save_project(out/'v093_3d_validation.shipproj');(out/'v093_3d_validation_expected.json').write_text(json.dumps({'description':'합성 시험 ID는 게임 등록 블록이 아님. 실제 3D 셀·판정 기대값','spaces':expected,'expected_auto_new_registrations':2,'protected_manual_spaces':1,'NBT':'ComputerId=15, Label=v093 보존 시험'},ensure_ascii=False,indent=2),encoding='utf8')
