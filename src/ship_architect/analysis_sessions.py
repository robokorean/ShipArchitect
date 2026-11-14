"""Versioned analysis snapshots and exact-cell comparisons."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json

from . import __version__
from .chunks import checkpoint


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def block_signature(doc):
    """Fingerprint current coordinates and palette, independent of file path and block epoch."""
    sc = doc.schematic
    digest = hashlib.sha256(json.dumps([sc.size, sorted(sc.palette.items())]).encode())
    for layer in sc.blocks:
        digest.update(layer.astype('<u4', copy=False).tobytes())
    return digest.hexdigest()


def provenance(doc, method, mode):
    from .exterior_structure import ALGORITHM_VERSION
    return {
        'program_version': __version__, 'algorithm_version': ('0.9.3' if method=='3d_air_components' else ALGORITHM_VERSION),
        'algorithm': method, 'mode': mode, 'created_at': timestamp(),
        'original_sha256': hashlib.sha256(doc.original).hexdigest(),
        'block_signature': block_signature(doc), 'block_epoch': doc.block_epoch,
    }


def matches_document(doc, record):
    signature = record.get('block_signature')
    return None if not signature else signature == block_signature(doc)


def _rows(items):
    rows = defaultdict(list)
    for index, item in enumerate(items):
        for y, z, a, b in item.get('volume_runs', item.get('runs', [])):
            rows[y,z].append((a,b,index))
    for row in rows.values():
        row.sort()
    return rows


def compare_candidates(old, new, cancel=None):
    """Join intervals by row. Multiple intersections expose split and merge candidates."""
    old_rows, new_rows = _rows(old), _rows(new)
    intersections = Counter()
    for key, current in new_rows.items():
        checkpoint(cancel)
        prior = old_rows.get(key, ())
        for a,b,j in current:
            for x,y,i in prior:
                if x>b:
                    break
                if y>=a:
                    intersections[i,j] += min(b,y)-max(a,x)+1
    by_old, by_new = defaultdict(list), defaultdict(list)
    for (i,j),n in intersections.items():
        by_old[i].append((j,n))
        by_new[j].append((i,n))
    def volume(item):
        return sum(b-a+1 for _,_,a,b in item.get('volume_runs',item.get('runs',[])))
    rows = []
    for j,item in enumerate(new):
        related = sorted(by_new[j],key=lambda p:(-p[1],p[0]))
        n = volume(item)
        status = 'new'
        if related:
            i,common = related[0]
            status = ('same' if len(related)==1 and len(by_old[i])==1 and common==n==volume(old[i])
                      else 'merge_and_split' if len(related)>1 and any(len(by_old[k])>1 for k,_ in related)
                      else 'merge' if len(related)>1 else 'split' if len(by_old[i])>1 else 'changed')
        if len(related)==1:
            intersection=related[0][1]
            union=n+volume(old[related[0][0]])-intersection
        else:
            intersection=union=None
        rows.append({'new_id':item['id'],'old_ids':[old[i]['id'] for i,_ in related],
                     'intersections':[v for _,v in related],'status':status,
                     'new_cells':n,'intersection_cells':intersection,'union_cells':union,'old_cells':[volume(old[i]) for i,_ in related],
                     'decision_changed':bool(related and item.get('decision')!=old[related[0][0]].get('decision'))})
    rows.extend({'new_id':None,'old_ids':[item['id']],'intersections':[],
                 'status':'old_only','new_cells':0,'old_cells':[volume(item)]}
                for i,item in enumerate(old) if i not in by_old)
    return rows
