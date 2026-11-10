"""Bounded, cancellable 6-connected air volumes. Auto registration needs structural proofs."""
import hashlib,json,uuid
from collections import Counter,defaultdict
import numpy as np
from numba import njit
from .model import Bounds
from .space_model import normalize,bbox,contains
from .chunks import checkpoint
from .diagnostics import evidence
MAX_VOLUME=8_000_000

@njit(cache=True,nogil=True)
def next_seed(air,labels,start):
    for i in range(start,len(air)):
        if air[i] and labels[i]==0:return i
    return -1

@njit(cache=True,nogil=True)
def flood_step(air,labels,queue,head,tail,label,W,L,H):
    end=min(head+65536,tail+65536);plane=W*L;done=0
    while head<tail and done<65536:
        p=queue[head];head+=1;done+=1;x=p%W;z=p//W%L;y=p//plane
        for q in (p-1 if x>0 else -1,p+1 if x<W-1 else -1,p-W if z>0 else -1,p+W if z<L-1 else -1,p-plane if y>0 else -1,p+plane if y<H-1 else -1):
            if q>=0 and air[q] and labels[q]==0:labels[q]=label;queue[tail]=q;tail+=1
    return head,tail

def intersects(a,b):return all(x<=Y and X>=y for x,X,y,Y in zip(a.lo,a.hi,b.lo,b.hi))
def expanded(b,size,n=2):return Bounds(tuple(max(0,v-n) for v in b.lo),tuple(min(s-1,v+n) for s,v in zip(size,b.hi)))
def fingerprint(runs):return hashlib.sha256(json.dumps(runs,separators=(',',':')).encode()).hexdigest()

def partial_bounds(doc):
    dirty=getattr(doc,'spatial_dirty',None)
    if dirty is None:return None
    b=expanded(dirty,doc.schematic.size)
    previous=getattr(doc,'analysis_state',{}).get('last_result',{}).get('candidates',[])
    sources=[Bounds(*bbox(s)) for s in doc.spaces]
    sources += [Bounds(tuple(c['lo']),tuple(c['hi'])) for c in previous if c.get('decision')!='exclude']
    visited=set()
    while True:
        fresh=[(i,a) for i,a in enumerate(sources) if i not in visited and intersects(a,b)]
        if not fresh:break
        for i,a in fresh:
            visited.add(i);a=expanded(a,doc.schematic.size)
            b=Bounds(tuple(min(x,y) for x,y in zip(b.lo,a.lo)),tuple(max(x,y) for x,y in zip(b.hi,a.hi)))
    return b

def _recognize_3d(doc,bounds,progress=None,cancel=None,partial=False):
    bounds.validate(doc.schematic.size)
    if bounds.volume>MAX_VOLUME:raise ValueError('3D 연결성 분석은 한 번에 8백만 셀 이하로 나누세요.')
    sc=doc.schematic;epoch=getattr(doc,'block_epoch',0);revision=doc.revision;x0,y0,z0=bounds.lo;X,Y,Z=bounds.hi
    a=sc.blocks[bounds.slices];solid=sc.solid(a);air=np.ascontiguousarray(~solid);H,L,W=air.shape
    labels=np.zeros(air.shape,np.int32);flat=labels.ravel();queue=np.empty(air.size,np.int32);af=air.ravel();start=0;label=0;limited=False
    while True:
        checkpoint(cancel);seed=next_seed(af,flat,start)
        if seed<0:break
        if label>=10000:limited=True;break
        label+=1;flat[seed]=label;queue[0]=seed;head=0;tail=1
        while head<tail:
            checkpoint(cancel);head,tail=flood_step(af,flat,queue,head,tail,label,W,L,H)
            if progress:progress(min(65,int(65*(start+head)/max(1,air.size))),f'3D 빈 셀 연결성 · 성분 {label:,}')
        start=seed+1
    # Narrow openings can join rooms to the exterior. Erode by one 6-neighbour
    # layer to identify interior cores, then restore one real-air layer. These
    # are hypotheses, never structural proof for automatic registration.
    partitioned=set();core_count=0
    if not limited:
        boundary_ids=set(map(int,np.unique(np.concatenate((labels[0].ravel(),labels[-1].ravel(),labels[:,0].ravel(),labels[:,-1].ravel(),labels[:,:,0].ravel(),labels[:,:,-1].ravel())))))-{0}
        if boundary_ids:
            core=air.copy()
            for axis in range(3):
                for sign in (-1,1):
                    checkpoint(cancel);near=np.roll(air,sign,axis);edge=[slice(None)]*3;edge[axis]=0 if sign==1 else -1;near[tuple(edge)]=False;core &= near
            core &= np.isin(labels,list(boundary_ids));cf=core.ravel();cl=np.zeros(air.size,np.int32);start=0;cid=0;proposals=np.zeros(air.shape,np.int32)
            while True:
                checkpoint(cancel);seed=next_seed(cf,cl,start)
                if seed<0:break
                if cid>=10000:limited=True;break
                cid+=1;cl[seed]=cid;queue[0]=seed;head=0;tail=1
                while head<tail:
                    checkpoint(cancel);head,tail=flood_step(cf,cl,queue,head,tail,cid,W,L,H)
                start=seed+1
                if tail<8:continue
                points=queue[:tail];xs=points%W;ys=points//(W*L);zs=points//W%L
                if xs.min()<=1 or xs.max()>=W-2 or ys.min()<=1 or ys.max()>=H-2 or zs.min()<=1 or zs.max()>=L-2:continue
                label+=1;core_count+=1;partitioned.add(label);proposals.ravel()[points]=label
            if core_count:
                expanded_labels=proposals.copy()
                for axis in range(3):
                    for sign in (-1,1):
                        checkpoint(cancel);near=np.roll(proposals,sign,axis);edge=[slice(None)]*3;edge[axis]=0 if sign==1 else -1;near[tuple(edge)]=0
                        eligible=(proposals==0)&air&np.isin(labels,list(boundary_ids))&(near>0)
                        # Ambiguous seams remain unassigned, rather than merging rooms.
                        conflict=eligible&(expanded_labels>0)&(expanded_labels!=near);expanded_labels[conflict]=-1
                        take=eligible&(expanded_labels==0);expanded_labels[take]=near[take]
                labels[expanded_labels>0]=expanded_labels[expanded_labels>0]
            if progress:progress(65,'좁은 개구부 분할 후보 · 자동 확정 제외')
    runs=defaultdict(list);total_runs=0;omitted=Counter()
    for y in range(H):
        checkpoint(cancel)
        for z in range(L):
            row=labels[y,z];edges=np.r_[0,np.flatnonzero(row[1:]!=row[:-1])+1,W]
            for lo,hi in zip(edges[:-1],edges[1:]):
                k=int(row[lo])
                if not k:continue
                if total_runs>=500000:omitted[k]+=int(hi-lo);continue
                runs[k].append([y+y0,z+z0,int(lo)+x0,int(hi-1)+x0]);total_runs+=1
    # Structural support and clearance, not a confidence-only decision.
    feet=defaultdict(int);patches=defaultdict(int);headcells=Counter();ambiguous=set()
    known={i for i,s in sc.palette.items() if s.startswith('minecraft:') and not any(t in s for t in ('stairs','slab','fence','pane','door','trapdoor','water','lava','carpet','snow','leaves'))}
    for y in range(H):
        checkpoint(cancel)
        row=labels[y]
        if y+1<H:headcells.update({int(k):int(n) for k,n in zip(*np.unique(row[(row>0)&(labels[y+1]>0)&(row==labels[y+1])],return_counts=True))})
        if y+y0>0 and y+1<H:
            below=solid[y-1] if y>0 else sc.solid(sc.blocks[y0-1,z0:Z+1,x0:X+1])
            mask=(row>0)&below&(row==labels[y+1]);ids,ns=np.unique(row[mask],return_counts=True)
            for k,n in zip(ids,ns):feet[int(k)]+=int(n)
            square=mask[:-1,:-1]&mask[1:,:-1]&mask[:-1,1:]&mask[1:,1:]&(row[:-1,:-1]==row[1:,1:])
            for k in np.unique(row[:-1,:-1][square]):patches[int(k)]+=1
        # All neighbours that close an air component must have known full-cube geometry for auto.
        for axis in range(3):
            for sign in (-1,1):
                yy=y+sign if axis==0 else y
                if not 0<=yy<H:continue
                shifted=np.roll(a[yy],sign,axis=0 if axis==1 else 1) if axis else a[yy]
                near_solid=np.roll(solid[yy],sign,axis=0 if axis==1 else 1) if axis else solid[yy]
                where=(row>0)&near_solid&~np.isin(shifted,list(known))
                if axis==1:where[0 if sign==1 else -1]=False
                if axis==2:where[:,0 if sign==1 else -1]=False
                ambiguous.update(map(int,np.unique(row[where])))
    candidates=[]
    for number,(k,rr) in enumerate(runs.items()):
        checkpoint(cancel)
        if progress:progress(65+int(30*number/max(1,len(runs))),'방 구조 검증·틈/외부 분류')
        low,high=bbox({'runs':rr});volume=sum(b-a+1 for _,_,a,b in rr);touch=[i for i in range(3) if low[i]==bounds.lo[i] or high[i]==bounds.hi[i]]
        checks={'closed_within_scope':not touch,'two_cell_clearance':headcells[k]>0,'usable_floor':feet[k]>=9,'wide_floor_patch':patches[k]>0,'known_boundary_geometry':k not in ambiguous,'complete_component':not limited and k not in omitted}
        reasons=[]
        if not checks['complete_component']:reasons.append('analysis_limit')
        if touch:reasons.append('scope_or_exterior_open')
        if not checks['two_cell_clearance']:reasons.append('ceiling_slit_no_headroom')
        if not checks['usable_floor']:reasons.append('no_usable_floor')
        if not checks['wide_floor_patch']:reasons.append('narrow_wall_gap_or_corridor')
        if not checks['known_boundary_geometry']:reasons.append('uncertain_block_boundary')
        if k in partitioned:reasons.append('narrow_opening_partition');checks['original_connected_component']=False
        else:checks['original_connected_component']=True
        decision='auto' if all(checks.values()) else 'review'
        if not checks['complete_component'] or not checks['two_cell_clearance'] or feet[k]<3:decision='exclude'
        if len(touch)==3 and bounds.lo==(0,0,0) and bounds.hi==tuple(n-1 for n in sc.size):decision='exclude'
        fp=fingerprint(rr);kind='corridor' if not patches[k] and feet[k]>=9 else 'room' if decision=='auto' else 'open_area'
        # Footprint is UI/legacy-zone metadata only. The volume is always the exact 3D component.
        footprint=[[z,a,b] for y,z,a,b in rr if y==low[1]]
        candidates.append(dict(id='3D-'+fp[:12],fingerprint=fp,method='3d_air_components',source_block_epoch=epoch,decision=decision,base_decision=decision,reasons=reasons or ['closed_supported_volume'],checks=checks,kind=kind,floor_y=low[1],area=feet[k],lo=list(low),hi=list(high),volume=volume,volume_runs=rr,footprint=footprint,footprint_truncated=False,wall_ratio=1. if not touch else 0.,ceiling_ratio=1. if not touch else 0.,confirmed=False,shape={'method':'6-connected actual empty cells'},warnings=reasons))
    candidates.sort(key=lambda c:(c['decision']=='exclude',-c['volume']))
    omitted_candidates=max(0,len(candidates)-512)
    if len(candidates)>512:
        for c in candidates[512:]:c['volume_runs']=[]
        candidates=candidates[:512]
    summary={'method':'3d_air_components','revision':revision,'block_epoch':epoch,'scope':{'lo':list(bounds.lo),'hi':list(bounds.hi)},'partial':partial,'candidates':candidates,'component_count':label-core_count,'partition_candidate_count':core_count,'omitted_candidate_count':omitted_candidates,'omitted_run_cells':sum(omitted.values()),'limited':limited or bool(omitted) or bool(omitted_candidates),'counts':dict(Counter(c['decision'] for c in candidates)),'note':'실제 6방향 빈 셀 연결성. 열린 경계/미확인 블록은 자동 확정하지 않습니다.'}
    def details():
        for c in candidates:
            yield {'candidate':{k:v for k,v in c.items() if k not in ('volume_runs','footprint')}}
            for r in c['volume_runs']:yield {'candidate_id':c['id'],'run':r}
    summary['trace_id']=evidence('spatial3d',{'counts':summary['counts'],'components':label,'limited':summary['limited'],'scope':summary['scope']},details())
    if progress:progress(100,'3D 구조 검증 완료')
    return summary


def recognize_3d(doc,bounds,progress=None,cancel=None,partial=False):
    import traceback
    try:return _recognize_3d(doc,bounds,progress,cancel,partial)
    except Exception as e:
        evidence('spatial3d_failed',{'scope':{'lo':bounds.lo,'hi':bounds.hi},'reason':str(e),'type':type(e).__name__},[{'stage':'3d_connectivity','traceback':traceback.format_exc()}])
        raise
