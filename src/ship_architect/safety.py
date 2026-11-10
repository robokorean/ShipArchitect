"""Pure, exact change review. GUI, engine and diagnostics share one policy."""
from collections import Counter
import numpy as np
MASS_CONFIRM_THRESHOLD=5_000
HULL_BLOCK_THRESHOLD=1_000

def audit(indices,*,size_xyz,bounds_lo,bounds_hi,origin='ai',before=None,after=None,palette=None,reference_bounds=None):
    w,h,l=map(int,size_xyz);ii=np.asarray(indices)
    if ii.ndim!=1 or ii.dtype.kind not in 'iu' or np.any(ii<0) or np.any(ii>=w*h*l):raise ValueError('잘못되거나 설계도 밖인 변경 좌표')
    if len(np.unique(ii))!=len(ii):raise ValueError('중복 변경 좌표')
    q=(ii%w,ii//(w*l),(ii//w)%l)
    for v,a,b in zip(q,bounds_lo,bounds_hi):
        if np.any((v<int(a))|(v>int(b))):raise ValueError('선택 구역 밖 변경을 차단했습니다.')
    bbox={'lo':[int(v.min()) for v in q],'hi':[int(v.max()) for v in q]} if len(ii) else None
    masks=[q[0]==0,q[0]==w-1,q[1]==0,q[1]==h-1,q[2]==0,q[2]==l-1]
    faces=[n for n,m in zip(('-X','+X','-Y','+Y','-Z','+Z'),masks) if m.any()]
    outer=int(np.logical_or.reduce(masks).sum()) if len(ii) else 0
    result=dict(changed=len(ii),bbox=bbox,document_faces=faces,outer_face_changed=outer,large_change=len(ii)>=MASS_CONFIRM_THRESHOLD,
                hull_risk=False,requires_confirmation=False,enclosure=False,added=0,removed=0,replaced=0,materials={},transitions={},layers=[],chunks=[],warnings=[])
    if before is not None and after is not None:
        a,b=np.asarray(before),np.asarray(after)
        if a.shape!=ii.shape or b.shape!=ii.shape:raise ValueError('변경 전후 배열 길이 오류')
        air={i for i,s in palette.items() if s.split('[')[0] in {'minecraft:air','minecraft:cave_air','minecraft:void_air'}}
        aa=np.isin(a,list(air));bb=np.isin(b,list(air))
        result.update(added=int((aa&~bb).sum()),removed=int((~aa&bb).sum()),replaced=int((~aa&~bb).sum()),
            materials={palette[int(i)]:int(n) for i,n in zip(*np.unique(b,return_counts=True))})
        pairs,ns=np.unique(np.stack([a,b],axis=1),axis=0,return_counts=True)
        result['transitions']={palette[int(x)]+' → '+palette[int(y)]:int(n) for (x,y),n in zip(pairs,ns)}
    if len(ii):
        low,high=bbox['lo'],bbox['hi'];span=[b-a+1 for a,b in zip(low,high)]
        own=[q[0]==low[0],q[0]==high[0],q[2]==low[2],q[2]==high[2]]
        side_counts=[int(m.sum()) for m in own]
        boundary_share=float(np.logical_or.reduce(own).mean())
        enclosure=span[0]>=12 and span[2]>=12 and span[1]>=4 and min(side_counts)>=40 and boundary_share>=.45
        reference=reference_bounds or {'lo':[0,0,0],'hi':[w-1,h-1,l-1]}
        rs=[b-a+1 for a,b in zip(reference['lo'],reference['hi'])]
        covers=all(span[j]>=.8*rs[j] for j in (0,2)) and span[1]>=.5*rs[1]
        old_world_shell=len(faces)>=4 and outer>=500 and outer*3>=len(ii) and span[1]>=4
        result['enclosure']=bool(enclosure)
        result['hull_risk']=bool(origin=='ai' and len(ii)>=HULL_BLOCK_THRESHOLD and (old_world_shell or (enclosure and covers)))
        yy,nn=np.unique(q[1],return_counts=True);result['layers']=[{'y':int(y),'count':int(n)} for y,n in zip(yy,nn)]
        cells=np.stack([q[0]//32,q[1]//32,q[2]//32],axis=1);cc,ns=np.unique(cells,axis=0,return_counts=True)
        result['chunks']=[{'xyz':[int(v) for v in c],'count':int(n)} for c,n in zip(cc,ns)]
    destructive=result['removed']+result['replaced']>=2_000
    result['requires_confirmation']=bool(not result['hull_risk'] and ((origin=='ai' and result['large_change']) or destructive or (result['enclosure'] and result['large_change'])))
    if result['hull_risk']:result['warnings'].append('함선/설계도 전체를 감싸는 외곽 건축 차단')
    if result['large_change']:result['warnings'].append('대량 변경: 별도 승인 필요')
    if destructive:result['warnings'].append('대량 삭제/교체: 원래 구조 손실 가능')
    if result['enclosure']:result['warnings'].append('여러 측면을 둘러싸는 형상 감지')
    return result

def review(doc,p):
    after=np.full(len(p.indices),p.target,np.int32) if p.after is None else np.asarray(p.after)
    result=audit(p.indices,size_xyz=doc.schematic.size,bounds_lo=p.bounds.lo,bounds_hi=p.bounds.hi,origin=p.origin,
                 before=p.before,after=after,palette=doc.schematic.palette,reference_bounds=getattr(doc,'ship_bounds',None))
    result.update(space_id=getattr(doc,'active_space_id',None),space_token=p.space_token,outside_space_skipped=p.outside_space,
                  protected_skipped=p.protected,occupied_skipped=p.occupied,exterior_skipped=p.exterior_blocked,locked_skipped=p.locked_blocked)
    result['operation_diagnostics']=p.reasons or {}
    result['applied']=False
    if not len(p.indices):result['warnings'].append('변경 0개: 공간 외부 제외·보호 제외·재료 동일·겹침 허용 여부를 확인하세요.')
    return result

def require_safe(change,*,approve_mass=False):
    if change['hull_risk']:raise ValueError('AI 외곽 건축을 차단했습니다. 함선 전체를 둘러싸는 계획은 작은 구역으로 나눠 다시 설계하세요.')
    if change['requires_confirmation'] and not approve_mass:raise ValueError('대량 변경/삭제/교체에 별도 승인이 필요합니다.')
