

import numpy as np
import pytest

from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.structure_review import extract_geometry
from ship_architect.structural_recognition import domain_from_structure


def hull_with_partial_deck():
    sc=Schematic.empty(38,18,24)
    blocks=sc.blocks
    blocks[2,2:22,3:35]=1
    blocks[15,2:22,3:35]=1
    blocks[2:16,2,3:35]=1
    blocks[2:16,21,3:35]=1
    blocks[2:16,2:22,34]=1
    blocks[8,3:21,4:25]=1
    return Document(sc)


def voxel_mask(doc,result):
    voxels=np.zeros(doc.schematic.blocks.shape,bool)
    for candidate in result['candidates']:
        for y,z,a,b in candidate['volume_runs']:
            assert not voxels[y,z,a:b+1].any()
            voxels[y,z,a:b+1]=True
    return voxels


@pytest.mark.parametrize('engine',['structural','hybrid'])
def test_partial_deck_changes_partitions_preserving_open_air(engine):
    doc=hull_with_partial_deck()
    source=doc.schematic.blocks.copy()
    old=recognize_exterior(doc,doc.selection)
    new=recognize_exterior(doc,doc.selection,engine=engine)
    assert old['algorithm_version']=='0.9.3.4.1'
    assert new['engine_id']==engine
    assert new['structural_generation']['anchored_labels']>=2
    assert new['domain_unassigned_cells']==0
    assert not np.any(voxel_mask(doc,new)&(source!=0))
    assert np.array_equal(source,doc.schematic.blocks)
    # These interior voxels are supported by separate physical floors; the
    # aperture between levels remains an air-space connection.
    def owner(x,y,z):
        return next(c for c in new['candidates'] if any(
            yy==y and zz==z and a<=x<=b for yy,zz,a,b in c['volume_runs']))
    lower,upper=owner(12,5,10),owner(12,11,10)
    assert lower['id'] != upper['id']
    assert lower['exterior_connected'] and upper['exterior_connected']
    assert owner(27,8,10)
    assert any({lower['id'],upper['id']}==set(x['spaces']) for x in new['connections'])


def test_supported_domain_crosses_storage_chunk_boundary_and_excludes_canopy():
    doc=hull_with_partial_deck()
    sc=doc.schematic
    air=~sc.solid(sc.blocks)
    geometry=extract_geometry(doc,doc.selection,~air,air)
    domain,*_=domain_from_structure(air,~air,air,geometry,'hybrid',None)
    assert domain[5,11,31:34].all()
    assert not domain[16].any()
    assert not domain[:,0].any()
    sc.blocks[3:15,2:22,3:35]=0
    for z,x in ((2,3),(21,3),(2,34),(21,34)):
        sc.blocks[3:15,z,x]=1
    air=~sc.solid(sc.blocks)
    geometry=extract_geometry(doc,doc.selection,~air,air)
    canopy,*_=domain_from_structure(air,~air,air,geometry,'hybrid',None)
    assert not canopy[6,11,12]


def test_manual_separator_is_a_graph_edge_and_leaves_blocks_and_flood_untouched():
    doc=hull_with_partial_deck()
    bounds=doc.selection
    original=doc.schematic.blocks.copy()
    plain=recognize_exterior(doc,bounds,engine='hybrid')
    plain['structure_review']['manual_boundaries']=[
        {'id':'manual','axis':2,'lo':[0,0,12],'hi':[37,17,12]}]
    doc.analysis_state['last_result']=plain
    split=recognize_exterior(doc,bounds,engine='hybrid')
    assert split['structural_generation']['manual_boundaries_applied'][0]['air_faces']>0
    assert split['exterior_air_cells']==plain['exterior_air_cells']
    assert np.array_equal(voxel_mask(doc,plain),voxel_mask(doc,split))
    assert len(split['candidates'])>len(plain['candidates'])
    assert np.array_equal(original,doc.schematic.blocks)


def test_hybrid_reference_requires_independent_physical_evidence():
    from ship_architect.structural_recognition import reserve_verified_partitions
    domain=np.zeros((7,8,16),bool)
    air=np.ones_like(domain)
    bounds=Bounds((0,0,0),(15,6,7))
    candidate={'id':'checked','decision':'review','structure_class':'open_interior',
               'enclosure_profile':{'floor_fraction':1.,'roof_fraction':1.,'lateral_open_faces':0},
               'reasons':['wide_partition_repaired'],'sky_open_column_fraction':0.,
               'validation':{'mesh_volume_matches_voxels':True,'solid_intrusions':0},
               'volume':10,'volume_runs':[[2,3,2,11]]}
    assert not reserve_verified_partitions({'candidates':[candidate]},domain,air,bounds,None)
    domain[2,3,2:11]=True
    air[2,3,11]=False
    assert not reserve_verified_partitions({'candidates':[candidate]},domain,air,bounds,None)
    air[2,3,11]=True
    assert reserve_verified_partitions({'candidates':[candidate]},domain,air,bounds,None)[0][2]==9
    assert not domain.any()


@pytest.mark.parametrize('engine',['legacy','structural','hybrid'])
def test_closed_house_manual_ground_truth(engine):
    doc=Document(Schematic.empty(16,12,14))
    b=doc.schematic.blocks
    b[2:9,2:12,3:13]=1
    b[3:8,3:11,4:12]=0
    # L-shaped annex at the same floor, explicitly mapped from a second room.
    b[2:9,2:9,12:15]=1
    b[3:8,3:8,12:14]=0
    expected=np.zeros(b.shape,bool)
    expected[3:8,3:11,4:12]=True
    expected[3:8,3:8,12:14]=True
    r=recognize_exterior(doc,doc.selection,engine=engine)
    assert np.array_equal(voxel_mask(doc,r),expected)
    assert sum(c['volume'] for c in r['candidates'])==370


def test_structure_engine_selector_and_persisted_comparison(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from ship_architect.app import MainWindow
    from test_ai_gui import ready, wait_for
    app=QApplication.instance() or QApplication([])
    monkeypatch.setenv('SHIP_ARCHITECT_DATA_DIR',str(tmp_path))
    w=MainWindow()
    w.ai_panel.timer.stop()
    errors=[]
    w.error=lambda e:errors.append(str(e))
    doc=hull_with_partial_deck()
    try:
        w.set_document(doc)
        wait_for(lambda:ready(w),seconds=30)
        panel=w.structure_review
        panel.engine.setCurrentIndex(panel.engine.findData('structural'))
        panel.compare()
        wait_for(lambda:ready(w),seconds=45)
        assert not errors,errors
        state=w.doc.analysis_state
        assert state['last_result']['provenance']['engine_id']=='structural'
        assert state['session_reference']['engine_id']=='legacy'
        assert state['session_changes']
        p=tmp_path/'test.shipproj'
        doc.save_project(p)
        restored=Document.open_project(p)
        assert restored.analysis_state==state
    finally:
        w.doc.dirty=False
        w.close()
        app.processEvents()


@pytest.mark.parametrize('engine',['structural','hybrid'])
def test_narrow_door_separates_rooms_without_breaking_air_connectivity(engine):
    doc=hull_with_partial_deck()
    b=doc.schematic.blocks
    b[3:15,3:21,3]=1
    b[8,3:21,4:25]=0
    b[3:15,3:21,19]=1
    b[3:6,11,19]=0
    truth=b==0
    truth[:3]=False
    truth[15:]=False
    truth[:,:3]=False
    truth[:,21:]=False
    truth[:,:,:4]=False
    truth[:,:,34:]=False
    r=recognize_exterior(doc,doc.selection,engine=engine)
    assert len(r['candidates'])==2
    assert r['connections'] and r['connections'][0]['traversability']=='uncertain'
    assert np.array_equal(voxel_mask(doc,r),truth)
    assert all(not c['exterior_connected'] for c in r['candidates'])
