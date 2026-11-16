from copy import deepcopy
from ship_architect.candidate_relations import prune_candidate_relations


def sample():
    return {'candidates': [
        {'id':'fragment','volume_runs':[[1,1,1,2]],'structure_class':'uncertain',
         'adjacent_spaces':[{'id':'owner','unit_faces':8},{'id':'other','unit_faces':2}],
         'fragment':{'kind':'attached_fragment','suggested_owner_id':'owner','shared_faces':8,'adjacent_faces':10}},
        {'id':'other','volume_runs':[[1,2,1,2]],'structure_class':'open_interior'}],
        'connections':[{'spaces':['owner','fragment']},{'spaces':['other','fragment']}],
        'semantic_features':[{'position':[1,1,1],'spaces':['owner','other']}],
        'structure_review':{'spaces':[{'id':'owner','classification':'open_interior'},
            {'id':'fragment','classification':'uncertain','adjacent_space_ids':['owner','other']},
            {'id':'other','classification':'open_interior'}],
            'equipment_groups':[{'category':'CC','adjacent_space_ids':['owner','other']}],
            'class_counts':{'open_interior':2,'uncertain':1},'layers':{'observed':{'runs':[[1,1,1,2]]}}},
        'structure_counts':{'uncertain':1,'open_interior':2},
        'structural_generation':{'fragment_counts':{'attached_fragment':1},'paired_floor_roof_cells':20}}


def test_owner_deletion_keeps_fragment_geometry_and_unrelated_links():
    result=sample(); original=deepcopy(result)
    prune_candidate_relations(result, {'owner'})
    fragment=result['candidates'][0]
    assert fragment['fragment']=={'kind':'residual_air','suggested_owner_id':None,'shared_faces':0,'adjacent_faces':2}
    assert fragment['volume_runs']==original['candidates'][0]['volume_runs']
    assert fragment['adjacent_spaces']==[{'id':'other','unit_faces':2}]
    assert result['connections']==[{'spaces':['other','fragment']}]
    assert result['semantic_features'][0]['spaces']==['other']
    review=result['structure_review']
    assert [row['id'] for row in review['spaces']]==['fragment','other']
    assert review['spaces'][0]['adjacent_space_ids']==['other']
    assert review['equipment_groups'][0]['adjacent_space_ids']==['other']
    assert review['class_counts']=={'open_interior':1,'uncertain':1}
    assert review['layers']==original['structure_review']['layers']
    assert result['structural_generation']=={'fragment_counts':{'residual_air':1},'paired_floor_roof_cells':20}
    assert result['structure_counts']=={'open_interior':1,'uncertain':1}


def test_empty_removal_is_noop_and_repeated_cleanup_is_idempotent():
    result=sample(); original=deepcopy(result)
    prune_candidate_relations(result,set())
    assert result==original
    prune_candidate_relations(result,{'owner'}); once=deepcopy(result)
    prune_candidate_relations(result,{'owner'})
    assert result==once


def test_legacy_result_without_precision_fields():
    result={'candidates':[{'id':'other'}],'connections':[{'spaces':['owner','other']}]}
    prune_candidate_relations(result,{'owner'})
    assert result=={'candidates':[{'id':'other'}],'connections':[]}
