"""Same-scope exact RLE overlap comparison, not a labelled ground-truth accuracy score."""
import time,copy
from collections import defaultdict
from .chunks import checkpoint
from .spatial3d import recognize_3d
from .exterior_recognition import recognize_exterior
from .diagnostics import evidence

def overlap(a,b):
    rows=defaultdict(list)
    for y,z,lo,hi in a:rows[y,z].append((lo,hi))
    return sum(max(0,min(B,hi)-max(A,lo)+1) for y,z,lo,hi in b for A,B in rows.get((y,z),()))

def compare_results(old,new,cancel=None):
    matches=[]
    # Exact interval intersection; different bbox shapes cannot inflate IoU.
    for n in new['candidates']:
        checkpoint(cancel);options=[]
        for o in old['candidates']:
            if any(n['lo'][i]>o['hi'][i] or o['lo'][i]>n['hi'][i] for i in range(3)):continue
            common=overlap(o['volume_runs'],n['volume_runs']);union=o['volume']+n['volume']-common
            row={'old_id':o['id'],'new_id':n['id'],'intersection_cells':common,'IoU':common/max(1,union),'old_volume':o['volume'],'new_volume':n['volume'],'old_lo':o['lo'],'old_hi':o['hi'],'new_lo':n['lo'],'new_hi':n['hi']}
            if common:options.append(row)
        options.sort(key=lambda item:-item['IoU'])
        best=dict(options[0]) if options else {'new_id':n['id'],'old_id':None,'intersection_cells':0,'IoU':0.}
        best['overlapping_old_ids']=[row['old_id'] for row in options]
        best['alternatives']=options[1:9]
        best['new_only_cells']=n['volume']-sum(row['intersection_cells'] for row in options)
        best['solid_intrusions']=n.get('validation',{}).get('solid_intrusions')
        matches.append(best)
    return matches

def compare_methods(doc,bounds,progress=None,cancel=None,*,ignore_terrain=True):
    epoch=doc.block_epoch;revision=doc.revision;t=time.perf_counter();old=recognize_3d(doc,bounds,(lambda n,s:progress(n//2,'기존: '+s)) if progress else None,cancel);old['elapsed_seconds']=round(time.perf_counter()-t,6)
    new=recognize_exterior(doc,bounds,(lambda n,s:progress(50+n//2,'신규: '+s)) if progress else None,cancel,ignore_terrain=ignore_terrain)
    if doc.block_epoch!=epoch or doc.revision!=revision:raise ValueError('비교 도중 문서가 변경되었습니다. 다시 실행하세요.')
    matches=compare_results(old,new,cancel);report={'scope':copy.deepcopy(old['scope']),'block_epoch':epoch,'revision':revision,'old':old,'new':new,'matches':matches,'interpretation':'IoU compares algorithm outputs, not ground truth; room identity/accuracy is not implied'}
    common={key for match in matches for key in match['overlapping_old_ids']}
    report['old_only_ids']=[c['id'] for c in old['candidates'] if c['id'] not in common]
    report['new_only_ids']=[m['new_id'] for m in matches if m['old_id'] is None]
    report['trace_id']=evidence('recognition_compare',{'scope':report['scope'],'old_candidates':len(old['candidates']),'new_candidates':len(new['candidates']),'old_seconds':old['elapsed_seconds'],'new_seconds':new['elapsed_seconds']},matches)
    return report
