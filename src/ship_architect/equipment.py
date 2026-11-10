"""Auditable ID inventory. Unclassified mod blocks are candidates, never functional claims."""
from collections import Counter,defaultdict
import numpy as np
from .space_model import bbox
from .chunks import regions,slices,checkpoint
from .diagnostics import evidence,event

def category(state):
    name=state.split('[')[0];namespace,_,path=name.partition(':')
    if namespace in ('computercraft','cc_tweaked','cc','advancedperipherals','computercraft_tweaked') or namespace.startswith('computercraft_'):return 'ComputerCraft'
    if any(k in path for k in ('thruster','propeller','propulsion','rocket_engine','ion_engine','thrust','ship_engine','engine_controller')):return '추진 설비 후보'
    if any(k in path for k in ('generator','battery','alternator','capacitor','dynamo','reactor','energy_cell','creative_motor','electric_motor','power_cell')):return '전력 설비 후보'
    if (namespace=='create' or namespace.startswith('create')) and 'elevator' in path:return 'Create 엘리베이터 후보'
    if namespace=='create' or namespace.startswith(('create_','createaddition','createconnected','createnewage','createpropulsion')):return 'Create'
    return None

def classification(doc,state):
    rules=getattr(doc,'analysis_state',{}).get('equipment_rules',{});base=state.split('[')[0]
    if state in rules:return rules[state]
    if base in rules:return rules[base]
    # Structural/decorative mod blocks remain discoverable, but are not called functioning devices.
    name=base.split(':')[-1]
    if not base.startswith('minecraft:') and name.endswith(('_casing','_girder','_bracket','_concrete','_planks','_wool','_scaffolding')):return '장식용 블록 후보'
    return category(state) or ('미분류 BlockEntity' if state.startswith('minecraft:') else '미분류 모드 블록')

def owner_index(doc):
    rows=defaultdict(list)
    for s in doc.spaces:
        for y,z,a,b in s['runs']:rows[y,z].append((a,b,s['id']))
    return rows

def ownership(xyz,rows,size):
    x,y,z=xyz;owners={sid for a,b,sid in rows.get((y,z),()) if a<=x<=b};near=set()
    for X,Y,Z in ((x-1,y,z),(x+1,y,z),(x,y-1,z),(x,y+1,z),(x,y,z-1),(x,y,z+1)):
        if all(0<=v<n for v,n in zip((X,Y,Z),size)):
            near.update(sid for a,b,sid in rows.get((Y,Z),()) if a<=X<=b)
    return sorted(owners),sorted(near-owners)

def record(doc,state,xyz,rows=None):
    rows=owner_index(doc) if rows is None else rows;owners,near=ownership(xyz,rows,doc.schematic.size)
    return dict(block=state,category=classification(doc,state),position=list(xyz),spaces=owners,adjacent_spaces=near,block_entity=tuple(xyz) in doc.schematic.entity_positions()[0],operation='미확인',membership_reason='정확한 셀 포함' if owners else '셀 밖: 6면 인접 근거만 있음' if near else '정확한 셀 및 6면 인접 근거 없음',identification='블록 ID 규칙 분류; 미분류는 설비 여부 미확인; 실제 작동 추정 안 함')

def inventory(doc,progress=None,cancel=None):
    sc=doc.schematic;entities,unsafe=sc.entity_positions();ids={i:classification(doc,s) for i,s in sc.palette.items() if category(s) or not s.startswith('minecraft:') or s in doc.analysis_state.get('equipment_rules',{}) or s.split('[')[0] in doc.analysis_state.get('equipment_rules',{})}
    # Vanilla BlockEntities are also discoverable even when no name rule exists.
    for xyz in entities:
        x,y,z=xyz;i=int(sc.blocks[y,z,x]);ids.setdefault(i,classification(doc,sc.palette[i]))
    allcounts=Counter();position_runs=defaultdict(list);boxes=list(regions(sc.blocks.shape));rows=owner_index(doc)
    for n,(lo,hi) in enumerate(boxes):
        checkpoint(cancel);a=sc.blocks[slices(lo,hi)];present,num=np.unique(a,return_counts=True);allcounts.update({int(i):int(v) for i,v in zip(present,num)})
        for Y,Z in np.ndindex(a.shape[:2]):
            row=a[Y,Z];edges=np.r_[0,np.flatnonzero(row[1:]!=row[:-1])+1,len(row)]
            for left,right in zip(edges[:-1],edges[1:]):
                i=int(row[left])
                if i in ids:position_runs[sc.palette[i]].append([int(Y+lo[1]),int(Z+lo[2]),int(left+lo[0]),int(right-1+lo[0])])
        if progress and n%16==0:progress(int(100*(n+1)/max(1,len(boxes))),'블록 발견·분류·소속·미분류 증거 수집')
    state_categories={sc.palette[i]:cat for i,cat in ids.items()};records=[];elevators=[];counts={sc.palette[i]:int(n) for i,n in allcounts.items() if i in ids};limit=20000;quota=max(1,limit//max(1,len(position_runs)))
    for state,runs in sorted(position_runs.items()):
        emitted=0
        for y,z,a,b in runs:
            for x in range(a,b+1):
                checkpoint(cancel)
                if emitted>=quota or len(records)>=limit:break
                r=record(doc,state,(x,y,z),rows);r['category']=state_categories[state];records.append(r);emitted+=1
                if r['category']=='Create 엘리베이터 후보':elevators.append(r)
            if emitted>=quota or len(records)>=limit:break
    links=[];columns=defaultdict(list)
    for r in elevators:columns[tuple(r['position'][::2])].append(r)
    for group in columns.values():
        for i,left in enumerate(group):
            for right in group[i+1:]:
                if len(links)>=4096:break
                if abs(left['position'][1]-right['position'][1])<2:continue
                for first in set(left['spaces']+left['adjacent_spaces']):
                    for second in set(right['spaces']+right['adjacent_spaces']):
                        if first!=second and len(links)<4096:links.append(dict(spaces=[first,second],status='uncertain',reason='같은 X/Z 승강기 블록 후보; 제어·플랫폼·작동 미검증',positions=[left['position'],right['position']]))
    total=sum(counts.values());unknown=sum(v for s,v in counts.items() if classification(doc,s).startswith('미분류'))
    summary=dict(total=total,classified=total-unknown,unclassified=unknown,record_count=len(records),by_category=dict(Counter()),palette_searched=len(sc.palette),position_storage='all coordinates in exact RLE; records are bounded representative UI subset',operation='모든 설비 작동 미확인')
    cats=Counter()
    for i,cat in ids.items():cats[cat]+=allcounts.get(i,0)
    summary['by_category']=dict(cats)
    def details():
        for i,state in sc.palette.items():yield dict(stage='discovery_classification',block=state,palette_id=i,found=allcounts.get(i,0),category=ids.get(i),user_rule=(state in doc.analysis_state.get('equipment_rules',{}) or state.split('[')[0] in doc.analysis_state.get('equipment_rules',{})),reason='미사용 팔레트' if not allcounts.get(i,0) else '규칙 분류' if category(state) else '미분류·보존' if i in ids else '바닐라 일반 블록: 설비 검색 대상 제외')
        for state,runs in position_runs.items():
            for run in runs:yield dict(stage='all_positions',block=state,run=run)
        for r in records:yield dict(stage='membership_sample',**r)
    trace=evidence('equipment',summary,details())
    return dict(**summary,by_block=counts,records=records,position_runs=dict(position_runs),positions_truncated=len(records)<total,elevator_candidates=links,unknown_block_entity_positions=unsafe,trace_id=trace)

def search_records(doc,result,query='',offset=0,limit=2000):
    """All ID/type coordinates remain accessible beyond representative-record limits."""
    query=query.casefold();rows=owner_index(doc);names={s['id']:s['name'] for s in doc.spaces};out=[];matched=0;name_query=any(query in name.casefold() for name in names.values());coordinate_query=any(c.isdigit() for c in query)
    for state,runs in result.get('position_runs',{}).items():
        cat=classification(doc,state);direct=not query or query in (state+' '+cat).casefold()
        if not direct and not name_query and not coordinate_query:continue
        for y,z,a,b in runs:
            for x in range(a,b+1):
                r=None
                if not direct:
                    r=record(doc,state,(x,y,z),rows);text=' '.join(names.get(s,s) for s in r['spaces']+r['adjacent_spaces'])+str([x,y,z])
                    if query not in text.casefold():continue
                matched+=1
                if matched<=offset:continue
                out.append(r or record(doc,state,(x,y,z),rows))
                if len(out)>=limit:return out
    return out
