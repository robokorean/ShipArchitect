"""Bounded floor-first space candidates. No game simulation or automatic edits.
Per-floor 4-connected footprints retain asymmetry; ceilings/walls refine each candidate.
"""
import numpy as np
from numba import njit
from .chunks import checkpoint,Cancelled
from .diagnostics import evidence,event
from collections import Counter
MAX_ANALYSIS_VOLUME=8_000_000

@njit(cache=True)
def components(solid,y):
    depth,width=solid.shape[1:];walk=(~solid[y])&(~solid[y+1])&solid[y-1]
    labels=np.zeros((depth,width),np.int32);queue=np.empty(depth*width,np.int32)
    stats=np.zeros((depth*width+1,10),np.int64);label=0
    for z in range(depth):
        for x in range(width):
            if not walk[z,x] or labels[z,x]:continue
            label+=1;head=0;tail=1;queue[0]=z*width+x;labels[z,x]=label
            count=walls=opens=roofs=height_sum=0;minx=maxx=x;minz=maxz=z;minheight=129;maxheight=2
            while head<tail:
                p=queue[head];head+=1;zz=p//width;xx=p%width;count+=1
                minx=min(minx,xx);maxx=max(maxx,xx);minz=min(minz,zz);maxz=max(maxz,zz)
                for h in range(y+2,min(solid.shape[0],y+129)):
                    if solid[h,zz,xx]:
                        roofs+=1;height_sum+=h-y;minheight=min(minheight,h-y);maxheight=max(maxheight,h-y);break
                for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx=xx+dx;nz=zz+dz
                    if nx<0 or nx>=width or nz<0 or nz>=depth:opens+=1;continue
                    if walk[nz,nx]:
                        if not labels[nz,nx]:labels[nz,nx]=label;queue[tail]=nz*width+nx;tail+=1
                    elif solid[y,nz,nx] or solid[y+1,nz,nx]:walls+=1
                    else:opens+=1
            stats[label]=np.array((count,minx,maxx,minz,maxz,walls,opens,roofs,minheight,maxheight))
    return labels,stats[:label+1]


@njit(cache=True)
def widths(mask):
    H,W=mask.shape;out=np.zeros(mask.shape,np.int32)
    for z in range(H):
        x=0
        while x<W:
            if not mask[z,x]:x+=1;continue
            start=x
            while x<W and mask[z,x]:x+=1
            for X in range(start,x):out[z,X]=x-start
    for x in range(W):
        z=0
        while z<H:
            if not mask[z,x]:z+=1;continue
            start=z
            while z<H and mask[z,x]:z+=1
            for Z in range(start,z):out[Z,x]=min(out[Z,x],z-start)
    return out

def shape_metrics(mask):
    values=widths(mask)[mask];area=int(mask.sum())
    median=float(np.median(values)) if len(values) else 0;narrow=float((values<=4).mean()) if len(values) else 0
    # Long bent/T corridors stay narrow even when their bounding box is wide.
    kind='corridor' if median<=4 and narrow>=.65 and area/max(1,median*median)>=3 else 'room'
    return dict(kind=kind,width_median=median,width_p10=float(np.percentile(values,10)) if len(values) else 0,narrow_fraction=round(narrow,3),shape_method='연속 X/Z 폭의 최솟값 분포; L/T 형태 허용')

def _recognize(doc,bounds,progress=None,cancel=None):
    bounds.validate(doc.schematic.size);revision=doc.revision
    if bounds.volume>MAX_ANALYSIS_VOLUME:raise ValueError('공간 인식은 한 번에 8,000,000칸 이하 구역으로 나눠 주세요.')
    if (bounds.hi[0]-bounds.lo[0]+1)*(bounds.hi[2]-bounds.lo[2]+1)>262144:raise ValueError('공간 인식은 한 층 262,144칸 이하로 나눠 주세요.')
    if bounds.hi[1]-bounds.lo[1]<2:raise ValueError('바닥과 머리 공간을 확인할 수 있도록 높이 3칸 이상을 선택하세요.')
    x0,y0,z0=bounds.lo;X,Y,Z=bounds.hi;sy=max(0,y0-1)
    solid=doc.schematic.solid(doc.schematic.blocks[sy:Y+1,z0:Z+1,x0:X+1]);candidates=[];excluded=[];exclusion_counts=Counter()
    start=max(1,y0-sy);end=solid.shape[0]-1
    for y in range(start,end):
        checkpoint(cancel)
        if progress:progress(int(100*(y-start)/max(1,end-start)),f'바닥·벽·천장 분석 · Y={y+sy}')
        labels,stats=components(solid,y)
        ids=np.flatnonzero(stats[:,0]>=9);ids=sorted(ids,key=lambda i:-int(stats[i,0]));kept=set(ids[:8])
        for label in range(1,len(stats)):
            if label not in kept:
                reason='면적 9셀 미만' if stats[label,0]<9 else '층별 8개 후보 상한';exclusion_counts[reason]+=1
                if len(excluded)<4096:excluded.append({'Y':y+sy,'label':label,'area':int(stats[label,0]),'bbox_xz':[int(stats[label,1])+x0,int(stats[label,2])+x0,int(stats[label,3])+z0,int(stats[label,4])+z0],'reason':reason})
        ids=ids[:8]
        for label in ids:
            count,minx,maxx,minz,maxz,walls,opens,roofs,minheight,maxheight=map(int,stats[label])
            wall=walls/max(1,walls+opens);roof=roofs/count;wx=maxx-minx+1;wz=maxz-minz+1
            shape=shape_metrics(labels[minz:maxz+1,minx:maxx+1]==label);kind=shape['kind']
            boundary_sides=int(minx==0)+int(maxx==solid.shape[2]-1)+int(minz==0)+int(maxz==solid.shape[1]-1)
            if (wall<.15 and roof<.3) or (boundary_sides>=3 and roof<.9):kind='open_area'
            surface_role='interior_candidate'
            if roof<.5 or wall<.35:
                kind='open_area';surface_role='roof_or_outdoor' if roof<.3 else 'open_structure'
            rows=[]
            for z in range(minz,maxz+1):
                xx=np.flatnonzero(labels[z]==label)
                if not len(xx):continue
                runs=np.split(xx,np.flatnonzero(np.diff(xx)>1)+1)
                for run in runs:rows.append([z+z0,int(run[0])+x0,int(run[-1])+x0])
            warnings=[]
            if surface_role=='roof_or_outdoor':warnings.append('천장 근거 부족: 지붕 윗면/외부 바닥 가능성. 방으로 확정하지 않으며 일괄 가져오기에서 제외합니다.')
            if opens:warnings.append('열린 면/출입구 또는 선택 범위 경계가 있습니다.')
            if roof<1:warnings.append('천장 일부/전체가 열려 있거나 128칸 탐색 범위 밖입니다.')
            if count<wx*wz:warnings.append('비정형 공간: 표시 상자는 바닥 footprint보다 넓습니다.')
            candidates.append(dict(floor_block_y=y+sy-1,wall_boundary_cells=walls,open_boundary_cells=opens,ceiling_columns=roofs,walkable_evidence='지지 바닥+2칸 머리 공간; 장치 작동/방 사이 통행은 별도 분석',surface_role=surface_role,exclude_from_bulk=surface_role=='roof_or_outdoor',decision_reason='바닥 지지+머리 공간; 벽/천장 비율로 내부·개방·외부 구분',id=f'Y{y+sy}-{label}',origin='auto',manual_modified=False,shape=shape,kind=kind,floor_y=y+sy,area=count,
                lo=[minx+x0,y+sy,minz+z0],hi=[maxx+x0,min(Y,y+sy+maxheight-1),maxz+z0],
                wall_ratio=round(wall,3),ceiling_ratio=round(roof,3),ceiling_height_min=minheight if roofs else None,ceiling_height_max=maxheight if roofs else None,
                bbox_fill_ratio=round(count/(wx*wz),3),footprint=rows[:4096],footprint_truncated=len(rows)>4096,warnings=warnings,
                confidence=round((wall+roof)/2,3),confirmed=False))
        if revision!=doc.revision:raise Cancelled('공간 인식 중 문서가 바뀌었습니다.')
    if progress:progress(100,'공간 후보 분석 완료 · 사용자 확인 필요')
    retained=sorted(candidates,key=lambda c:(c['kind']=='open_area',-c['area']))[:128]
    from .space_model import candidate_runs
    volume_runs=0
    for c in retained:
        checkpoint(cancel)
        if c.get('footprint_truncated'):c['volume_error']='footprint truncated';continue
        runs=candidate_runs(doc,c,cancel)
        if volume_runs+len(runs)>500000:c['volume_error']='candidate preview 500000 runs budget';continue
        c['volume_runs']=runs;volume_runs+=len(runs)
    exclusion_counts['전체 128개 후보 상한']=max(0,len(candidates)-128)
    def details():
        for c in candidates:
            yield {'stage':'candidate','candidate':{k:v for k,v in c.items() if k not in ('footprint','volume_runs')},'retained':c in retained}
            for row in c['footprint']:yield {'stage':'footprint','candidate_id':c['id'],'row':row}
            for run in c.get('volume_runs',[]):yield {'stage':'candidate_volume','candidate_id':c['id'],'run':run}
        for q in excluded:yield dict(stage='excluded',**q)
    trace=evidence('spaces',{'scope':{'lo':bounds.lo,'hi':bounds.hi},'candidates':len(retained),'exclusions':dict(exclusion_counts),'excluded_details':len(excluded),'excluded_details_truncated':sum(exclusion_counts.values())>len(excluded),'manual_boundaries':'never overwritten by recognition'},details())
    return {'trace_id':trace,'excluded':excluded,'exclusion_counts':dict(exclusion_counts),'revision':revision,'scope':{'lo':bounds.lo,'hi':bounds.hi},'candidates':retained,
            'limits':{'max_volume':MAX_ANALYSIS_VOLUME,'max_candidates':128,'max_per_floor':8,'ceiling_search':128},'note':'폭 분포와 형태로 방/통로 후보를 구분합니다. 높이 차이는 별도 발판으로 유지합니다. 문 상태·계단 방향·실제 통행은 연결·경로 탭에서 검사하세요.'}


def recognize(doc,bounds,progress=None,cancel=None):
    import uuid,traceback
    trace=uuid.uuid4().hex
    try:
        result=_recognize(doc,bounds,progress,cancel)
        for candidate in result['candidates']:candidate['source_trace_id']=result['trace_id']
        return result
    except Exception as e:
        evidence('spaces_failed',{'scope':{'lo':bounds.lo,'hi':bounds.hi},'reason':str(e),'error_type':type(e).__name__},[{'traceback':traceback.format_exc(),'stage':'recognize'}],trace_id=trace)
        raise
