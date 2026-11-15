import numpy as np

from ship_architect.exterior_recognition import recognize_exterior
from ship_architect.model import Bounds, Document
from ship_architect.schematic import Schematic
from ship_architect.structural_recognition import deck_anchors, support_edges, label_domain, refine_large
from test_ai_gui import ready, wait_for, window
from test_v0937_engines import hull_with_partial_deck, voxel_mask


def test_plate_components_and_supported_growth_edges():
    shape=(8,12,30)
    deck=np.zeros(shape,bool)
    deck[1,2:10,1:14]=True
    deck[1,2:10,18:28]=True
    deck[4,2:10,14:18]=True
    domain=np.zeros(shape,bool)
    domain[5:8,2:10,1:28]=True
    anchor=np.full(shape,-1,dtype=np.int16)
    anchor[domain]=1
    anchor[5:8,2:10,14:18]=4
    ids,plates=deck_anchors(deck,anchor,None)
    assert len(plates)==3 and ids[5,5,2]!=ids[5,5,23]
    bounds=Bounds((0,0,0),(29,7,11))
    edges,trace=support_edges(domain,anchor,ids,plates,bounds,None)
    assert trace['supported_boundary_faces']==48
    for sample in trace['boundary_samples']:
        assert sample['type']=='virtual_ownership_boundary'
    labels,_,_=label_domain(domain,anchor,np.zeros_like(edges),np.empty(domain.size,np.int32),None,None,physical=edges)
    assert labels[5,5,2]!=labels[5,5,15]
    assert np.array_equal(labels>0,domain)


def test_large_deck_refinement_keeps_domain_and_broad_openings():
    height,depth,width=33,42,44
    shape=(height,depth,width)
    for aperture,expect_split in ((slice(20,23),True),(slice(4,40),False)):
        domain=np.ones(shape,bool)
        domain[16]=False
        domain[16,:,aperture]=True
        deck=np.zeros(shape,bool)
        deck[16]=~domain[16]
        labels=domain.astype(np.int32)
        n,trace=refine_large(labels,domain,domain,deck,np.zeros(shape,np.uint8),
            Bounds((0,0,0),(width-1,height-1,depth-1)),np.empty(domain.size,np.int32),None)
        assert (n>1)==expect_split
        assert (trace[0]['outcome']=='partitioned_review')==expect_split
        assert np.array_equal(labels>0,domain)


def test_tall_roofless_room_is_reviewable_parapet_is_not():
    doc=hull_with_partial_deck()
    b=doc.schematic.blocks
    b[15]=0
    b[8]=0
    d=recognize_exterior(doc,doc.selection,engine='hybrid')
    assert d['structural_generation']['unfinished_missing_roof_cells']>0
    assert any(c['structure_class']=='unfinished_interior_review' and c['decision']=='review' for c in d['candidates'])
    assert not (voxel_mask(doc,d)&doc.schematic.solid()).any()
    b[4:]=0
    exposed=recognize_exterior(doc,doc.selection,engine='hybrid')
    assert exposed['structural_generation']['unfinished_missing_roof_cells']==0


def test_engine_choice_shared_and_retained_across_document_changes(window):
    w=window
    panel=w.structure_review
    tools=w.space_view.bulk.recognition
    panel.engine.setCurrentIndex(panel.engine.findData('hybrid37'))
    assert tools.mode.currentData()=='hybrid37'
    assert tools.preferences.value('analysis/engine')=='hybrid37'
    w.set_document(hull_with_partial_deck())
    wait_for(lambda:ready(w),seconds=30)
    tools.mode.setCurrentIndex(tools.mode.findData('structural'))
    assert panel.engine.currentData()=='structural'
    w.space_view.bulk.scan()
    wait_for(lambda:ready(w),seconds=30)
    assert w.doc.analysis_state['last_result']['engine_id']=='structural'
