"""Reproducible >5-million-cell fixture, not the user's real ship."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import nbtlib as nbt
from ship_architect.schematic import Schematic


def make_large():
    s=Schematic.empty(256,96,256)
    s.blocks[0]=1
    s.blocks[48,16:240,16:240]=6
    s.blocks[1:49,16,16]=2
    s.blocks[1:49,239,239]=2
    s.palette[128]='create:shaft[axis=x]'
    s.blocks[1,30,30]=128
    s.root['Metadata']['Name']=nbt.String('Synthetic 6.29M cell fixture')
    s.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('test:protected_fixture'),
        'Pos':nbt.IntArray([30,1,30]),'Data':nbt.Compound({'Fixture':nbt.String('not a real device')})}))
    return s


if __name__=='__main__':
    path=Path(__file__).resolve().parents[1]/'examples'/'large_sparse_6m.schem'
    s=make_large();path.write_bytes(s.to_bytes());print(path)
