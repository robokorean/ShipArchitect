"""Exact sparse spaces. Runs are [Y,Z,X0,X1], inclusive; never bbox permission."""
import hashlib,json,uuid
from collections import defaultdict,Counter
import numpy as np
MAX_RUNS=200_000

def normalize(runs,size):
    if len(runs)>MAX_RUNS:raise ValueError('공간 경계가 너무 복잡합니다. 공간을 나눠 주세요.')
    rows=defaultdict(list);w,h,l=size
    for r in runs:
        if len(r)!=4 or any(type(n) is not int for n in r):raise ValueError('공간 셀 좌표는 정수 Y,Z,X0,X1이어야 합니다.')
        y,z,a,b=r
        if not(0<=y<h and 0<=z<l and 0<=a<=b<w):raise ValueError('공간 셀이 설계도 좌표 밖입니다.')
        rows[y,z].append((a,b))
    out=[]
    for (y,z),spans in sorted(rows.items()):
        for a,b in sorted(spans):
            if out and out[-1][:2]==[y,z] and a<=out[-1][3]+1:out[-1][3]=max(b,out[-1][3])
            else:out.append([y,z,a,b])
    return out

def new_space(runs,size,name='새 공간',purpose='미지정',**fields):
    r=normalize(runs,size)
    if not r:raise ValueError('공간 셀이 0개입니다.')
    return dict(id=uuid.uuid4().hex,origin='manual',manual_modified=False,name=name,purpose=purpose,color='#4fc7b0',opacity=80,visible=True,runs=r,**fields)

def validate_space(s,size):
    if not isinstance(s,dict) or not isinstance(s.get('id'),str) or not s['id']:raise ValueError('공간 ID 오류')
    q=dict(s);q['runs']=normalize(q['runs'],size)
    if not q['runs']:raise ValueError('빈 공간')
    for k in ('name','purpose','color'):
        if not isinstance(q.get(k),str) or len(q[k])>200:raise ValueError('공간 속성 오류')
    if len(q['color'])!=7 or q['color'][0]!='#':raise ValueError('공간 색상 오류')
    int(q['color'][1:],16)
    if type(q.get('opacity')) is not int or not 0<=q['opacity']<=255 or type(q.get('visible')) is not bool:raise ValueError('공간 표시 속성 오류')
    return q

def box_runs(b):
    if (b.hi[1]-b.lo[1]+1)*(b.hi[2]-b.lo[2]+1)>MAX_RUNS:raise ValueError('공간이 너무 큽니다. 층별로 나눠 주세요.')
    return [[y,z,b.lo[0],b.hi[0]] for y in range(b.lo[1],b.hi[1]+1) for z in range(b.lo[2],b.hi[2]+1)]

def bbox(space):
    r=np.asarray(space['runs'],np.int64)
    return (int(r[:,2].min()),int(r[:,0].min()),int(r[:,1].min())),(int(r[:,3].max()),int(r[:,0].max()),int(r[:,1].max()))

def token(space):
    return space['id']+':'+hashlib.sha256(json.dumps(space['runs'],separators=(',',':')).encode()).hexdigest()

def contains(space,indices,size):
    ii=np.asarray(indices,np.int64);w,h,l=size
    y=ii//(w*l);z=ii//w%l;x=ii%w;out=np.zeros(ii.shape,bool)
    # Row keys sorted once per call. Search only touched rows, never allocate a volume mask.
    rr=np.asarray(space['runs'],np.int64);keys=rr[:,0]*l+rr[:,1];ik=y*l+z
    order=np.argsort(ik);ordered=ik[order]
    for key in np.unique(ik):
        a,b=np.searchsorted(keys,[key,key+1]);lo,hi=np.searchsorted(ordered,[key,key+1]);where=order[lo:hi]
        for r in rr[a:b]:out[where]|=(x[where]>=r[2])&(x[where]<=r[3])
    return out

def mask(space,origin,shape):
    x,y,z=origin;H,L,W=shape;out=np.zeros(shape,bool)
    rr=np.asarray(space['runs'],np.int64)
    lo,hi=np.searchsorted(rr[:,0],[y,y+H]);rr=rr[lo:hi]
    rr=rr[(rr[:,1]>=z)&(rr[:,1]<z+L)&(rr[:,3]>=x)&(rr[:,2]<x+W)]
    for Y,Z,a,b in rr:out[Y-y,Z-z,max(0,a-x):min(W,b-x+1)]=True
    return out

def subtract(runs,cut,size):
    rows=defaultdict(list)
    for y,z,a,b in normalize(cut,size):rows[y,z].append((a,b))
    out=[]
    for y,z,a,b in runs:
        parts=[(a,b)]
        for c,d in rows[y,z]:
            parts=[v for lo,hi in parts for v in ((lo,min(hi,c-1)),(max(lo,d+1),hi)) if v[0]<=v[1]]
        out.extend([y,z,lo,hi] for lo,hi in parts)
    return normalize(out,size)

def candidate_runs(doc,c,cancel=None):
    from .chunks import checkpoint
    runs=[];sc=doc.schematic;y=c['floor_y']
    # Per column stop at the first solid block, preserving varying ceilings and obstacles.
    for z,a,b in c['footprint']:
        checkpoint(cancel)
        alive=np.ones(b-a+1,bool)
        for Y in range(y,c['hi'][1]+1):
            solid=sc.solid(sc.blocks[Y,z,a:b+1]);alive &= ~solid
            starts=np.flatnonzero(alive & ~np.r_[False,alive[:-1]])
            ends=np.flatnonzero(alive & ~np.r_[alive[1:],False])
            runs.extend([Y,int(z),int(a+lo),int(a+hi)] for lo,hi in zip(starts,ends))
            if len(runs)>MAX_RUNS:raise ValueError('공간 셀 한도 초과')
    return normalize(runs,sc.size)

def from_candidate(doc,c,cancel=None):
    from .chunks import checkpoint
    if c.get('footprint_truncated'):raise ValueError('후보 바닥 경계가 잘렸습니다. 작은 영역으로 재분석하세요. 상자 대체는 허용하지 않습니다.')
    if c.get('method') in ('3d_air_components','exterior_voxel_mesh'):
        if c.get('source_block_epoch')!=getattr(doc,'block_epoch',0):raise ValueError('3D 후보 이후 블록이 변경되었습니다.')
        runs=normalize(c['volume_runs'],doc.schematic.size)
        if any(doc.schematic.solid(doc.schematic.blocks[y,z,a:b+1]).any() for y,z,a,b in runs):raise ValueError('후보 내부 실제 블록 충돌')
    else:runs=candidate_runs(doc,c,cancel)
    sc=doc.schematic
    space=new_space(runs,sc.size,'확인 공간 '+c['id'],{'room':'방','corridor':'통로','open_area':'개방 공간'}.get(c['kind'],'미지정'));space['origin']='auto';space['recognition']=dict(c.get('shape',{}),candidate_id=c['id'],surface_role=c.get('surface_role','legacy'),evidence=c.get('decision_reason','legacy'))
    from .diagnostics import evidence
    evidence('space_confirmed',{'source_trace_id':c.get('source_trace_id'),'candidate_id':c['id'],'space_id':space['id'],'lo':c.get('lo',bbox(space)[0]),'hi':c['hi'],'cells':sum(b-a+1 for _,_,a,b in space['runs']),'runs':len(space['runs']),'geometry':'exact 3D cells, no bbox substitution'},({'space_id':space['id'],'run':r} for r in space['runs']))
    return space

def statistics(doc,space,cancel=None):
    counts=Counter();protected=0
    unsafe_ids=[i for i,state in doc.schematic.palette.items() if not doc.schematic.is_decorative(state)]
    indexed={'runs':np.asarray(space['runs'],np.int64)}
    from .chunks import checkpoint
    for y,z,a,b in space['runs']:
        checkpoint(cancel);v=doc.schematic.blocks[y:y+1,z:z+1,a:b+1]
        ids,n=np.unique(v,return_counts=True);counts.update({doc.schematic.palette[int(i)]:int(k) for i,k in zip(ids,n)})
        protected+=int((doc.schematic.protected(v,(a,y,z),_unsafe_ids=unsafe_ids)|doc.locked_mask((a,y,z),v.shape)).sum())
    devices=[{'position':list(pos),'block':doc.schematic.palette[int(doc.schematic.blocks[pos[1],pos[2],pos[0]])]} for pos in doc.schematic.entity_positions()[0] if contains(indexed,[pos[1]*doc.schematic.size[0]*doc.schematic.size[2]+pos[2]*doc.schematic.size[0]+pos[0]],doc.schematic.size)[0]]
    return dict(cells=sum(counts.values()),blocks=dict(counts),protected_devices_and_zones=protected,block_entities=devices)

def connections(doc,spaces,cancel=None):
    """Geometric contact evidence, not functional or navigability guarantees."""
    from .chunks import checkpoint
    rows=defaultdict(list)
    for s in spaces:
        for y,z,a,b in s['runs']:rows[y,z].append((a,b,s['id']))
    pairs={}
    for (y,z),spans in rows.items():
        checkpoint(cancel)
        for a,b,sid in spans:
            for dy,dz in ((0,0),(0,1),(1,0)):
                for c,d,tid in rows.get((y+dy,z+dz),[]):
                    if sid==tid:continue
                    overlap=min(b,d)-max(a,c)+1
                    touching=(dy or dz) and overlap>0 or (not dy and not dz and (b+1==c or d+1==a))
                    if touching:
                        key=tuple(sorted((sid,tid)));edge=pairs.setdefault(key,dict(spaces=list(key),horizontal_contacts=0,vertical_contacts=0,kind='接触候補'))
                        edge['vertical_contacts' if dy else 'horizontal_contacts']+=max(1,overlap)
    for e in pairs.values():e['kind']='다층 접촉 후보 (계단/개구부 수동 확인)' if e['vertical_contacts'] else '수평 연결 후보 (문/통로 수동 확인)'
    return list(pairs.values())

def analyze_connections(doc,spaces,cancel=None):
    from .chunks import regions,slices,checkpoint
    from .model import Bounds
    edges=connections(doc,spaces,cancel);features=[];seen=set();truncated=False
    indexed=[(sp,{'runs':np.asarray(sp['runs'],np.int64)},bbox(sp)) for sp in spaces]
    feature_ids={i:('계단' if state.split('[')[0].endswith('_stairs') else '문') for i,state in doc.schematic.palette.items() if state.split('[')[0].endswith(('_stairs','_door','_trapdoor'))}
    if not feature_ids:return dict(edges=edges,features=features,truncated=False)
    w,h,l=doc.schematic.size
    for s in spaces:
        low,high=bbox(s);b=Bounds(tuple(max(0,v-2) for v in low),tuple(min(dim-1,v+2) for dim,v in zip(doc.schematic.size,high)))
        # Align cache keys to world chunks across overlapping space boxes.
        low=tuple(v//32*32 for v in b.lo);high=tuple(min(dim-1,(v//32+1)*32-1) for dim,v in zip(doc.schematic.size,b.hi))
        for lo,hi in regions(doc.schematic.blocks.shape,Bounds(low,high)):
            checkpoint(cancel)
            if lo in seen:continue
            seen.add(lo);a=doc.schematic.blocks[slices(lo,hi)];positions=np.argwhere(np.isin(a,list(feature_ids)))
            for Y,Z,X in positions:
                xyz=(int(X+lo[0]),int(Y+lo[1]),int(Z+lo[2]));idx=xyz[1]*w*l+xyz[2]*w+xyz[0];material=int(doc.schematic.blocks.reshape(-1)[idx])
                neighbours=[]
                for dx,dy,dz in ((0,0,0),(-1,0,0),(1,0,0),(0,0,-1),(0,0,1),(0,-1,0),(0,1,0),(-1,1,0),(1,1,0),(0,1,-1),(0,1,1)):
                    x,y,z=xyz[0]+dx,xyz[1]+dy,xyz[2]+dz
                    if 0<=x<w and 0<=y<h and 0<=z<l:neighbours.append(y*w*l+z*w+x)
                checkpoint(cancel)
                nearby=[(sp,index) for sp,index,(low,high) in indexed if all(a-1<=v<=b+1 for a,v,b in zip(low,xyz,high))]
                ids=[sp['id'] for sp,index in nearby if np.any(contains(index,neighbours,doc.schematic.size))]
                if not ids and feature_ids[material]!='계단':continue
                features.append(dict(kind=feature_ids[material],position=list(xyz),block=doc.schematic.palette[material],spaces=ids))
                for i,left in enumerate(ids):
                    for right in ids[i+1:]:edges.append(dict(spaces=[left,right],kind=feature_ids[material]+' 블록 인접 후보',position=list(xyz)))
                if len(features)>=4096:truncated=True;return link_stair_flights(dict(edges=edges,features=features,truncated=truncated))
    return link_stair_flights(dict(edges=edges,features=features,truncated=truncated))


def plane_rectangles(space,y):
    """Merge identical X spans on consecutive Z rows for bounded overlay draw calls."""
    active={};out=[]
    for Y,z,a,b in space['runs']:
        if Y!=y:continue
        key=(a,b)
        if key in active and active[key][3]==z:
            active[key][3]=z+1
        else:
            if key in active:out.append(active.pop(key))
            active[key]=[a,z,b+1,z+1]
    out.extend(active.values())
    return out

def link_stair_flights(result):
    """Group geometrically touching stair blocks; clearance remains unverified."""
    stairs={tuple(f['position']):f for f in result['features'] if f['kind']=='계단'};visited=set()
    for pos in stairs:
        if pos in visited:continue
        stack=[pos];visited.add(pos);ids=set();count=0
        while stack:
            here=stack.pop();ids.update(stairs[here]['spaces']);count+=1
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for dz in (-1,0,1):
                        other=(here[0]+dx,here[1]+dy,here[2]+dz)
                        if other in stairs and other not in visited:visited.add(other);stack.append(other)
        ids=sorted(ids)
        for i,left in enumerate(ids):
            for right in ids[i+1:]:result['edges'].append(dict(spaces=[left,right],kind='연속 계단 블록 연결 후보 (여유 높이 미검증)',stair_blocks=count))
    return result
