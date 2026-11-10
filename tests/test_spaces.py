import numpy as np
import pytest
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic
from ship_architect.spaces import recognize
from ship_architect.chunks import Cancelled


def multilevel():
 s=Schematic.empty(24,14,22);a=s.blocks
 # Asymmetric L-shaped room, lower floor and partial upper floor.
 a[0,2:19,2:21]=1;a[7,2:12,2:21]=1;a[13,2:12,2:21]=1
 for y in range(1,13):
  a[y,2,2:21]=1;a[y,11,2:21]=1;a[y,2:12,2]=1;a[y,2:12,20]=1
 a[1:7,7:11,12:20]=1  # obstacle -> nonrectangular footprint
 return Document(s)


def test_multifloor_asymmetric_rooms_no_block_modification():
 d=multilevel();before=d.schematic.blocks.copy();r=recognize(d,d.selection)
 assert {1,8}.issubset({c['floor_y'] for c in r['candidates']})
 assert any(c['bbox_fill_ratio']<.95 and c['footprint'] for c in r['candidates'])
 assert any(c['wall_ratio']>.5 and c['ceiling_ratio']>.8 for c in r['candidates'])
 assert np.array_equal(before,d.schematic.blocks) and not d.history
 for c in r['candidates']:Bounds(tuple(c['lo']),tuple(c['hi'])).validate(d.schematic.size)


def test_corridor_candidate_and_open_walls():
 s=Schematic.empty(24,7,10);s.blocks[0,2:6,1:23]=1;s.blocks[5,2:6,1:23]=1;s.blocks[1:5,2,1:23]=1;s.blocks[1:5,5,1:23]=1
 d=Document(s);r=recognize(d,d.selection)
 assert any(c['kind']=='corridor' and c['warnings'] for c in r['candidates'])


def test_analysis_is_bounded_and_cancelable():
 d=Document(Schematic.empty(10,5,10))
 with pytest.raises(ValueError):recognize(d,Bounds((0,0,0),(9,0,9)))
 with pytest.raises(Cancelled):recognize(d,d.selection,cancel=lambda:True)


def test_exterior_ground_with_partial_roof_is_not_a_closed_room():
 s=Schematic.empty(24,8,24);s.blocks[0]=1;s.blocks[6,:12,:]=1
 # Islands create wall edges, but cannot turn the exterior into a room.
 s.blocks[1:5,4:6,4:6]=1;s.blocks[1:5,14:16,14:16]=1
 d=Document(s);r=recognize(d,d.selection)
 large=max(r['candidates'],key=lambda c:c['area']);assert large['kind']=='open_area'
