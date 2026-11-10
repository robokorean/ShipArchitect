"""Synthetic roof/interior/L-space and equipment evidence fixture, not a game registry."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import nbtlib as nbt
from ship_architect.model import Document
from ship_architect.schematic import Schematic
from ship_architect.spaces import recognize
from ship_architect.space_model import from_candidate

def build():
 sc=Schematic.empty(22,18,20);a=sc.blocks;a[0,2:18,2:20]=1;a[8,2:18,2:20]=1
 a[1:8,2,2:20]=1;a[1:8,17,2:20]=1;a[1:8,2:18,2]=1;a[1:8,2:18,19]=1;a[1:8,10:17,12:19]=1
 a[9:15,4,4:12]=1;a[9:15,11,4:12]=1;a[9:15,4:12,4]=1;a[9:15,4:12,11]=1;a[15,4:12,4:12]=1
 for i,state in enumerate(['create:gearbox[axis=y]','computercraft:computer_advanced[facing=north]','unknown:fixture_controller[facing=east]'],2):sc.palette[i]=state
 a[1,4,4]=2;a[1,4,6]=3;a[1,4,8]=4
 sc.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([6,1,4]),'Label':nbt.String('NBT 보존 시험'),'ComputerId':nbt.Int(15)}))
 d=Document(sc);r=recognize(d,d.selection);d.space_candidates=r['candidates'];d.space_revision=d.revision;d.spaces=[from_candidate(d,c) for c in r['candidates'] if c['kind']!='open_area'];d.active_space_id=d.spaces[0]['id'] if d.spaces else None
 return d,r
if __name__=='__main__':
 out=Path(__file__).resolve().parents[1]/'examples';d,r=build();d.save_project(out/'v092_stability.shipproj')
 (out/'v092_stability_expected.json').write_text(json.dumps({'description':'합성 시험: 게임에서 unknown 모드 블록 사용 가능함을 뜻하지 않음','interior_floors':[c['floor_y'] for c in r['candidates'] if c['kind']!='open_area'],'roof_outdoor_floors':[c['floor_y'] for c in r['candidates'] if c.get('exclude_from_bulk')],'confirmed_exact_shapes':len(d.spaces),'equipment_ids':['create:gearbox[axis=y]','computercraft:computer_advanced[facing=north]','unknown:fixture_controller[facing=east]'],'equipment_counts':{'Create':1,'ComputerCraft':1,'미분류 모드 블록':1},'NBT':'ComputerId=15, Label=NBT 보존 시험; 수정/숨김/저장 후 유지','manual_boundary':'기존 공간은 재인식으로 덮어쓰지 않음'},ensure_ascii=False,indent=2),encoding='utf8')
