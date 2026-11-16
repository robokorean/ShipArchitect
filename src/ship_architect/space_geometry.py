"""Exact exposed boundary quads from sparse [Y,Z,X0,X1] cells, no bbox substitute."""
from collections import defaultdict
import numpy as np

def difference(a,b,other):
    parts=[(a,b)]
    for c,d in other:
        parts=[r for lo,hi in parts for r in ((lo,min(hi,c-1)),(max(lo,d+1),hi)) if r[0]<=r[1]]
    return parts

def boundary_quads(runs):
    rows=defaultdict(list)
    for y,z,a,b in runs:rows[y,z].append((a,b))
    faces=defaultdict(list)
    for y,z,a,b in runs:
        faces[0,-1,a,y,y+1].append((z,z+1));faces[0,1,b+1,y,y+1].append((z,z+1))
        for axis,sign,near,plane,v in ((1,-1,(y-1,z),y,z),(1,1,(y+1,z),y+1,z),(2,-1,(y,z-1),z,y),(2,1,(y,z+1),z+1,y)):
            for lo,hi in difference(a,b,rows.get(near,())):faces[axis,sign,plane,lo,hi+1].append((v,v+1))
    quads=[];normals=[]
    for (axis,sign,plane,a,b),spans in faces.items():
        spans=sorted(spans);merged=[]
        for c,d in spans:
            if merged and c<=merged[-1][1]:merged[-1][1]=max(d,merged[-1][1])
            else:merged.append([c,d])
        uv=[i for i in range(3) if i!=axis]
        for c,d in merged:
            if len(quads)>=200000:raise ValueError('공간 경계 200000면 예산 초과')
            q=np.zeros((4,3),np.float32);q[:,axis]=plane;q[:,uv[0]]=[a,b,b,a];q[:,uv[1]]=[c,c,d,d];n=[0,0,0];n[axis]=sign;quads.append(q);normals.append(n)
    return np.asarray(quads,np.float32).reshape(-1,4,3),np.asarray(normals,np.int8).reshape(-1,3)

def draw_volumes(view, painter, candidates=False, spaces_override=None, selectable=True, highlight=False):
    from .space_render import draw_volumes as draw
    draw(view, painter, candidates, spaces_override, selectable, highlight)


def feature_lines(quads,normals):
    """Cancel coplanar strip seams, retaining true corners even at T junctions."""
    from collections import defaultdict,Counter
    lines=defaultdict(list)
    for q,normal in zip(quads,normals):
        for i in range(4):
            a=q[i].astype(int);b=q[(i+1)%4].astype(int);axis=int(np.argmax(np.abs(b-a)));fixed=tuple(int(a[k]) for k in range(3) if k!=axis);lo,hi=sorted((int(a[axis]),int(b[axis])));lines[axis,fixed].append((lo,hi,tuple(normal)))
    out=[]
    for (axis,fixed),spans in lines.items():
        events=defaultdict(Counter)
        for lo,hi,n in spans:events[lo][n]+=1;events[hi][n]-=1
        active=Counter();previous=None
        for pos,change in sorted(events.items()):
            if previous is not None and pos>previous and len([n for n,v in active.items() if v])>1:
                a=[];j=0
                for k in range(3):
                    if k==axis:a.append(previous)
                    else:a.append(fixed[j]);j+=1
                b=list(a);b[axis]=pos;out.append([a,b])
            active.update(change);previous=pos
    return np.asarray(out,np.float32).reshape(-1,2,3)
