"""Keep active candidate references consistent without changing observed geometry."""
from collections import Counter


def prune_candidate_relations(result, removed_ids):
    """Remove references to explicitly removed candidates; retain other evidence.

    Operates on a transaction's private result copy. The caller has already
    removed the candidate rows. Raw observation layers and volume runs are
    deliberately untouched. A missing owner requires review, not reassignment.
    """
    removed = set(removed_ids)
    if not removed:
        return
    candidates = result.get('candidates', [])
    if 'connections' in result:
        result['connections'] = [row for row in result['connections']
                                 if not removed.intersection(row.get('spaces', []))]
    for feature in result.get('semantic_features', []):
        if 'spaces' in feature:
            feature['spaces'] = [sid for sid in feature['spaces'] if sid not in removed]
    for candidate in candidates:
        if 'adjacent_spaces' in candidate:
            candidate['adjacent_spaces'] = [row for row in candidate['adjacent_spaces']
                                            if row['id'] not in removed]
        fragment = candidate.get('fragment')
        if not fragment:
            continue
        if 'adjacent_spaces' in candidate:
            fragment['adjacent_faces'] = sum(row['unit_faces'] for row in candidate['adjacent_spaces'])
        if fragment.get('suggested_owner_id') in removed:
            fragment.update(kind='residual_air', suggested_owner_id=None, shared_faces=0)
    review = result.get('structure_review')
    if review:
        if 'spaces' in review:
            review['spaces'] = [row for row in review['spaces'] if row['id'] not in removed]
            if 'class_counts' in review:
                review['class_counts'] = dict(Counter(row['classification'] for row in review['spaces']))
        for row in review.get('spaces', []) + review.get('equipment_groups', []):
            if 'adjacent_space_ids' in row:
                row['adjacent_space_ids'] = [sid for sid in row['adjacent_space_ids'] if sid not in removed]
    # Active classification summaries follow the remaining candidate rows.
    if 'structure_counts' in result:
        result['structure_counts'] = dict(Counter(c['structure_class'] for c in candidates if 'structure_class' in c))
    structural = result.get('structural_generation', {})
    if 'fragment_counts' in structural:
        structural['fragment_counts'] = dict(Counter(c['fragment']['kind'] for c in candidates if c.get('fragment')))
