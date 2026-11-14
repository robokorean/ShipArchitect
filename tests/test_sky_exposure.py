import pytest

from ship_architect.analysis_state import register
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.sky_exposure import classify_sky, sky_fraction, top_solid
from ship_architect.space_display import visible
from ship_architect.spatial3d import recognize_3d


@pytest.mark.parametrize('engine',[recognize_exterior,recognize_3d])
def test_roofed_compartment_is_not_excluded_by_sky_check(engine):
    ship=Schematic.empty(12,12,12)
    ship.blocks[1:9,1:11,1:11]=1
    ship.blocks[2:8,2:10,2:10]=0
    d=Document(ship)
    result=engine(d,d.selection)
    assert any(c['lo'][1]==2 and c.get('sky_open_column_fraction')==0 for c in result['candidates'])
    scope=Bounds((0,0,0),(11,6,11))
    assert sky_fraction([[6,3,3,6]],top_solid(ship,scope),scope)==0


def test_sky_exposed_roof_air_stays_hidden_and_cannot_register():
    ship=Schematic.empty(12,12,12)
    ship.blocks[5,2:10,2:10]=1
    d=Document(ship)
    rr=[[y,z,2,9] for y in range(6,9) for z in range(2,10)]
    c={'id':'roof','volume_runs':rr,'decision':'review','reasons':[]}
    classify_sky(c,sky_fraction(rr,top_solid(ship,d.selection),d.selection))
    assert c['sky_open_column_fraction']==1 and c['decision']=='exclude'
    from types import SimpleNamespace
    assert not visible(SimpleNamespace(doc=d),c,True)
    with pytest.raises(ValueError,match='등록하지 않습니다'):
        register(d,{'block_epoch':0,'candidates':[c]},False)
    assert (ship.blocks[5,2:10,2:10]==1).all()
