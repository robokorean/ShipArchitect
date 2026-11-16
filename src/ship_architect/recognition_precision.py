"""Refine unresolved volumes using local observed plates, and classify residual air."""
from collections import Counter, defaultdict

import numpy as np

from .spatial3d import checkpoint
from .structural_recognition import column_support, label_remaining


def split_deck_bands(labels, maximum, hull, geometry, edges, queue, bounds, exterior, reserved, cancel):
    """Take connected, wholly supported bands out of oversized open labels.

    Free vertical air and bypasses remain in review. Previously extracted
    bands and verified smaller labels are outside the scope of this pass.
    """
    evidence = {}
    floor, roof, _ = column_support(hull, geometry[4], geometry[5])
    sizes = np.bincount(labels.ravel())
    for parent in np.flatnonzero(sizes > 50_000):
        if not parent or parent in reserved:
            continue
        checkpoint(cancel)
        original = labels == parent
        if not np.any(original & exterior):
            continue
        paired = original & (floor >= 0) & (roof > floor + 2) & (roof <= floor + 25)
        keys, counts = np.unique(np.column_stack((floor[paired], roof[paired])), axis=0, return_counts=True)
        for (bottom, top), count in sorted(zip(keys.tolist(), counts.tolist()), key=lambda row: -row[1])[:16]:
            checkpoint(cancel)
            if count < 900:
                break
            section = slice(bottom + 1, top)
            local = labels[section]
            mask = paired[section] & (local == parent) & (floor[section] == bottom) & (roof[section] == top)
            groups, _ = label_remaining(mask, edges[section], queue, cancel)
            for part, size in enumerate(np.bincount(groups.ravel())):
                if not part or size < 900:
                    continue
                cells = groups == part
                columns = np.any(cells, axis=0)
                floor_contacts = int(np.count_nonzero(columns & geometry[5][bottom]))
                roof_contacts = int(np.count_nonzero(columns & geometry[5][top]))
                area = int(columns.sum())
                if min(floor_contacts, roof_contacts) < max(120, .65 * area):
                    continue
                if sizes[parent] - int(size) < max(900, .06 * sizes[parent]):
                    continue
                maximum += 1
                local[cells] = maximum
                evidence[maximum] = {
                    'source_label': int(parent), 'cells': int(size), 'paired_columns': area,
                    'floor_y': int(bottom + bounds.lo[1]), 'roof_y': int(top + bounds.lo[1]),
                    'floor_plate_columns': floor_contacts, 'roof_plate_columns': roof_contacts,
                    'boundary': 'virtual_at_change_in_observed_pair', 'confidence': 'review',
                }
    for parent in sorted({record['source_label'] for record in evidence.values()}):
        remainder = labels == parent
        parts, count = label_remaining(remainder, edges, queue, cancel)
        if count > 1:
            labels[remainder] = parts[remainder] + maximum
            maximum += count
    return maximum, evidence


def annotate_fragments(candidates, pairs, by_label, cancel):
    """Retain exact residual cells with explicit geometric affiliation evidence."""
    records = {candidate['id']: candidate for candidate in candidates}
    neighbors = defaultdict(Counter)
    for (left, right), faces in pairs.items():
        if left in by_label and right in by_label:
            a, b = by_label[left], by_label[right]
            neighbors[a][b] += faces
            neighbors[b][a] += faces
    counts = Counter()
    for candidate in candidates:
        checkpoint(cancel)
        if candidate['decision'] == 'registered' or candidate.get('checks', {}).get('wide_floor_patch'):
            continue
        if candidate.get('checks', {}).get('two_cell_clearance') and candidate.get('checks', {}).get('usable_floor'):
            continue
        adjacent = neighbors[candidate['id']]
        supported = [(sid, faces) for sid, faces in adjacent.most_common()
                     if records[sid]['decision'] in ('review', 'auto')
                     and records[sid]['volume'] > candidate['volume']]
        owner, faces = supported[0] if supported else (None, 0)
        classification = 'residual_air'
        if owner and faces >= 3 and faces >= .7 * sum(adjacent.values()):
            classification = 'attached_fragment'
        elif candidate['space_class'] in ('exterior_air', 'sky_exposed', 'exposed_deck'):
            classification = 'exterior_air'
        candidate['fragment'] = {'kind': classification, 'adjacent_faces': sum(adjacent.values()),
                                 'suggested_owner_id': owner if classification == 'attached_fragment' else None,
                                 'shared_faces': faces}
        counts[classification] += 1
    return dict(counts)
