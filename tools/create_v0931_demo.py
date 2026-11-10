"""Independent exterior/hangar/door/irregular-volume fixture. No real mod registry claim."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import nbtlib as nbt
from ship_architect.model import Document
from ship_architect.schematic import Schematic

def build():
 s=Schematic.empty(64,22,46);a=s.blocks;groups={}
 # Closed L room with an internal pillar and a connected upper tower.
 a[2:10,3:24,3:28]=1
 cells={(x,y,z) for y in range(3,9) for z in range(4,23) for x in range(4,27) if x<13 or z<11}
 cells-={(x,y,z) for x in (8,9) for y in range(3,9) for z in (6,7)}
 a[9:19,4:12,4:13]=1
 cells|={(x,y,z) for y in range(9,18) for z in range(5,11) for x in range(5,12)}
 groups['L+pillar+upper']=cells
 # Closed C room, exact concave shape.
 a[2:10,3:25,32:61]=1
 groups['C']={(x,y,z) for y in range(3,9) for z in range(4,24) for x in range(33,60) if x<40 or z<10 or z>=18}
 # Hangar roof/floor/side walls/back; large front opening at z=42.
 a[2,28:43,3:28]=1;a[14,28:43,3:28]=1;a[3:14,28:43,3]=1;a[3:14,28:43,27]=1;a[3:14,28,3:28]=1
 groups['open_hangar']={(x,y,z) for x in range(4,27) for y in range(3,14) for z in range(29,43)}
 # Two rooms connected by an empty 1x2 doorway; semantic open door adjacent.
 a[2:10,28:43,32:61]=1
 left={(x,y,z) for x in range(33,46) for y in range(3,9) for z in range(29,42)}
 right={(x,y,z) for x in range(47,60) for y in range(3,9) for z in range(29,42)}
 groups['door_rooms_union']=left|right|{(46,3,35),(46,4,35)}
 a[16:19,3:25,32:61]=1;groups['roof_slit']={(x,17,z) for x in range(34,59) for z in range(5,23)}
 for cells in groups.values():
  for x,y,z in cells:a[y,z,x]=0
 s.palette[2]='minecraft:oak_door[facing=east,half=lower,hinge=left,open=true,powered=false]';s.palette[3]='minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]';s.palette[4]='computercraft:computer_advanced[facing=north]'
 a[3,34,46]=2;a[3,30,34]=3;groups['door_rooms_union'].discard((34,3,30));a[2,28,32]=4
 s.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([32,2,28]),'ComputerId':nbt.Int(15),'Label':nbt.String('v0931 NBT preservation')}))
 return Document(s),groups
if __name__=='__main__':
 from ship_architect.exterior_recognition import recognize_exterior
 from ship_architect.spatial3d import fingerprint
 d,groups=build();out=Path(__file__).resolve().parents[1]/'examples';d.save_project(out/'v0931_exterior_demo.shipproj')
 expected={'independent_fixture':True,'expected':{'L+pillar+upper':'exact cells; one room; pillar excluded; multi-height retained','C':'exact C cells; one room','open_hangar':'candidate despite exterior air; virtual boundary; review','door_rooms_union':'at least two room labels; passage connectivity independent','roof_slit':'excluded, no headroom'},'volumes':{k:len(v) for k,v in groups.items()}}
 (out/'v0931_exterior_expected.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2));r=recognize_exterior(d,d.selection);print([(c['volume'],c['decision'],c['lo'],c['hi'],c['reasons']) for c in r['candidates']])
