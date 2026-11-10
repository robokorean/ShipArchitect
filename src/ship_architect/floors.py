"""Deck evidence and provenance without mutating existing project or undo snapshots."""
import numpy as np
from .navigation import movement_palette
from .space_model import bbox

def provenance(doc,space):
    origin=space.get('origin');modified=bool(space.get('manual_modified',False))
    if origin not in ('auto','manual'):
        origin='legacy'
        for h in doc.history:
            if h.get('space_id')!=space['id']:continue
            if h['action'] in ('space_create','space_merge'):origin='manual'
            if h['action'] in ('space_add','space_remove','space_split'):modified=True
    return origin,modified

def detect_decks(doc):
    sc=doc.schematic;w,h,l=sc.size;step=max(1,int(np.ceil(np.sqrt(w*l/65536))));stride=max(1,int(np.ceil(h/512)))
    passage,_,support,stairs,_,_=movement_palette(sc);rows=[]
    for y in range(1,h-1,stride):
        below=sc.blocks[y-1,::step,::step];feet=sc.blocks[y,::step,::step];head=sc.blocks[y+1,::step,::step]
        count=int(((support[below]!=0)&(stairs[below]==0)&(passage[feet]!=0)&(passage[head]!=0)).sum())
        if count:rows.append((y,count))
    sample_area=((w+step-1)//step)*((l+step-1)//step)
    reference=max((n for _,n in rows if n<sample_area*.5),default=max((n for _,n in rows),default=0))
    threshold=max(4,reference*.15);groups=[]
    for y,n in rows:
        if n<threshold:continue
        if groups and y-groups[-1][-1][0]<=2*stride:groups[-1].append((y,n))
        else:groups.append([(y,n)])
    levels={max(group,key=lambda p:p[1])[0] for group in groups}
    levels.update(getattr(doc,'floor_starts',[]))
    manual=[]
    for s in doc.spaces:
        origin,modified=provenance(doc,s)
        if origin=='manual' or modified:
            start=bbox(s)[0][1];levels.add(start);manual.append(start)
    return dict(levels=sorted(levels or {0}),walkable_surface_samples=rows,threshold=threshold,manual_starts=sorted(set(manual)),sample_xz_step=step,sample_y_step=stride,note='2칸 머리 공간과 알려진 평평한 지지면의 면적 정점. 계단 발판은 층으로 세지 않음. 사용자 기준 보존, 자동 층은 추정.')
