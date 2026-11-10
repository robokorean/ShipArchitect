"""Reproducible test fixture, NOT a recreation of the user's ship."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import nbtlib as nbt
from ship_architect.schematic import Schematic
from ship_architect.model import Document, Bounds


def make_demo():
    s = Schematic.empty(34, 16, 60)
    a = s.blocks
    # Shaped carrier hull: bow in -Z, stern in +Z.
    for z in range(3, 57):
        half = min(13, 4+(z-3)//2, 8+(57-z)//3)
        left, right = 17-half, 17+half
        a[1, z, left:right] = 6
        a[2:6, z, left] = 1
        a[2:6, z, right-1] = 1
        a[5, z, left:right] = 1
        if z % 6 in (0, 1, 2):
            a[5, z, 16:18] = 7
    a[2:5, 3, 13:21] = 1
    a[2:5, 56, 9:25] = 1
    # Hangar volume and partial upper deck.
    a[6:10, 20:45, 5] = 6
    a[6:10, 20:45, 28] = 6
    a[9, 20:45, 5:29] = 3
    a[10:13, 30:42, 23:28] = 1
    a[12, 30:42, 23:28] = 4
    a[13, 30:42, 23:28] = 6
    a[14, 34:38, 25] = 2
    # Stored mod devices with typed NBT must survive editing/export.
    s.palette[20] = 'create:shaft[axis=x]'
    s.palette[21] = 'computercraft:computer_advanced[facing=north]'
    a[6, 35, 8:15] = 20
    a[6, 32, 24] = 21
    s.root['Blocks']['BlockEntities'].append(nbt.Compound({
        'Id': nbt.String('computercraft:computer_advanced'), 'Pos': nbt.IntArray([24, 6, 32]),
        'Data': nbt.Compound({'ComputerId': nbt.Int(15), 'Label': nbt.String('DEMO ADMIN')})}))
    s.root['Metadata']['Name'] = nbt.String('Demo carrier - synthetic fixture')
    doc = Document(s)
    doc.add_zone('중앙 격납고', Bounds((6, 6, 20), (27, 8, 44)))
    doc.add_zone('비행 갑판', Bounds((5, 5, 12), (28, 5, 50)))
    doc.selection = Bounds((6, 6, 20), (27, 8, 44))
    doc.dirty = False
    return doc


if __name__ == '__main__':
    output = Path(__file__).resolve().parents[1]/'examples'
    output.mkdir(exist_ok=True)
    d = make_demo()
    (output/'demo_ship.schem').write_bytes(d.schematic.to_bytes())
    d.save_project(output/'demo_ship.shipproj')
    print('Generated demo_ship.schem and demo_ship.shipproj')
