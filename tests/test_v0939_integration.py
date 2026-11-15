import numpy as np
from PySide6.QtCore import Qt
from ship_architect.analysis_sessions import provenance
from ship_architect.analysis_state import commit,register
from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Bounds,Document
from ship_architect.schematic import Schematic
from ship_architect.structural_recognition import supported_bands
from ship_architect.structure_review import extract_geometry
from ship_architect.space_display import visible
from test_ai_gui import ready,wait_for,window
from test_v0937_engines import voxel_mask
import pytest


def test_integrated_union_keeps_one_voxel_high_recess_and_rejects_heads():
    d=Document(Schematic.empty(27,16,21))
    b=d.schematic.blocks
    b[2:12,2:19,2:25]=1
    b[3:10,3:18,3:20]=0
    b[3,4:16,20:24]=0
    base=recognize_exterior(d,d.selection,engine='legacy')
    hybrid=recognize_exterior(d,d.selection,engine='hybrid')
    integrated=recognize_exterior(d,d.selection,engine='integrated')
    observed=voxel_mask(d,integrated)
    expected=voxel_mask(d,base)|voxel_mask(d,hybrid)
    assert np.array_equal(observed,expected)
    assert not np.any(observed&d.schematic.solid())
    assert observed[3,6,22]
    assert integrated['structural_generation']['discovery']['union_cells']==expected.sum()


def test_large_room_extracts_only_observed_pair_with_shaft_retained():
    d=Document(Schematic.empty(69,30,72))
    b=d.schematic.blocks
    b[2,3:69,3:66]=1
    b[13,3:69,3:50]=1
    b[24,3:69,3:66]=1
    floor=d.schematic.solid()
    air=~floor
    domain=np.zeros_like(air)
    domain[3:24,3:69,3:66]=True
    domain &=air
    labels=domain.astype(np.int32)
    max_id,evidence=supported_bands(labels,1,domain,floor,extract_geometry(d,d.selection,floor,air),
        np.zeros_like(labels,dtype=np.uint8),np.empty(labels.size,np.int32),d.selection,set(),None)
    assert max_id>=3 and len(evidence)>=2
    assert np.array_equal(labels>0,domain)
    assert labels[9,15,12] != labels[18,15,12]
    # Where a real mid-deck is absent the entire shaft stays a single partition.
    assert labels[9,15,58] == labels[18,15,58]


def test_recovered_filter_and_project_compatibility(window,tmp_path):
    w=window
    d=Document(Schematic.empty(18,14,16))
    b=d.schematic.blocks
    b[2:12,2:13,2:16]=1
    b[3:10,3:12,3:14]=0
    r=recognize_exterior(d,d.selection,engine='integrated')
    r['provenance']=provenance(d,r['method'],'fresh',r)
    commit(d,dict(d.analysis_state,last_result=r))
    w.set_document(d)
    wait_for(lambda:ready(w),seconds=30)
    panel=w.structure_review
    panel.nodes['restored_air'].setCheckState(0,Qt.CheckState.Checked)
    assert 'restored_air' in panel.visible
    sample=dict(r['candidates'][0],decision='exclude',discovery={'legacy_only_cells':1})
    assert not visible(w.view,sample,True)
    w.space_tools.set_mode('recovered')
    assert visible(w.view,sample,True)
    destination=tmp_path/'integrated.shipproj'
    d.save_project(destination)
    restored=Document.open_project(destination)
    assert restored.analysis_state==d.analysis_state
    assert restored.schematic.metadata_digest()==d.schematic.metadata_digest()
    candidate=r['candidates'][0]
    candidate['space_facts']['usability']='restricted'
    with pytest.raises(ValueError,match='머리 공간'):
        register(d,r,False,{candidate['id']})
