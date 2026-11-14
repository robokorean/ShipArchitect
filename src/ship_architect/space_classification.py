"""Conservative distinction of normal, exterior envelope and unresolved merges."""
from .space_display import external

def classify(candidates,doc):
    for c in candidates:
        if c.get('space_class') in ('sky_exposed','exposed_deck','exterior_air'):
            continue
        if external(c,doc):
            c['space_class']='exterior_or_merged';c['decision']='exclude';c['base_decision']='exclude'
            if 'exterior_envelope_unresolved' not in c['reasons']:c['reasons'].append('exterior_envelope_unresolved')
        else:c['space_class']='interior' if c['decision']=='auto' else 'review_volume'
    return candidates
