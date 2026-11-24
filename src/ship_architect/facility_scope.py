"""Transient exact facility permissions. Physical spaces and candidates retain their identity."""
from collections import defaultdict
from .chunks import checkpoint
from .space_model import bbox

MAX_SCOPE_RUNS=200_000


def resolve(doc, facility_id, cancel=None):
    facility=doc.analysis_state.get('facilities',{}).get(facility_id)
    if not facility or facility.get('approved') is not True:
        raise ValueError('사용자가 확인한 시설을 선택하세요.')
    records={'space:'+s['id']:s for s in doc.spaces}
    records.update({'candidate:'+c['id']:c for c in doc.space_candidates})
    missing=[ref for ref in facility['members'] if ref not in records]
    if missing:
        raise ValueError(f'시설 구성 공간 {len(missing)}개를 찾을 수 없습니다. 시설 소속을 먼저 검토하세요.')
    cache_key=(facility_id,id(doc.analysis_state),id(doc.spaces),id(doc.space_candidates))
    cached=getattr(doc,'_facility_permission_cache',None)
    if cached is not None and cached[0]==cache_key:
        return cached[1]
    rows=defaultdict(list)
    pending=0
    for ref in facility['members']:
        checkpoint(cancel)
        item=records[ref]
        if ref.startswith('candidate:'):
            if item.get('decision') in ('exclude',) or item.get('space_class') in (
                'exterior_air','exterior_or_merged','sky_exposed','exposed_deck','unresolved_exterior_merge'):
                raise ValueError('시설에 외부·제외·미해결 후보가 있습니다. 경계를 검토한 뒤 설계하세요.')
            if item.get('checks',{}).get('complete_component') is False:
                raise ValueError('시설에 잘린 후보가 있습니다. 구성 공간을 검토하세요.')
            pending+=int(item.get('decision')!='registered')
        runs=item.get('runs',item.get('volume_runs',[]))
        if not runs:
            raise ValueError('시설 구성 공간에 실제 셀이 없습니다.')
        for y,z,a,b in runs:
            rows[y,z].append((a,b))
    union=[]
    for (y,z),spans in sorted(rows.items()):
        checkpoint(cancel)
        for a,b in sorted(spans):
            if union and union[-1][:2]==[y,z] and a<=union[-1][3]+1:
                union[-1][3]=max(b,union[-1][3])
            else:
                union.append([y,z,a,b])
        if len(union)>MAX_SCOPE_RUNS:
            raise ValueError('시설의 실제 경계가 너무 복잡합니다. 구성 공간 단위로 나누어 설계하세요.')
    result=dict(id='facility:'+facility_id,name=facility['name'],purpose=facility['purpose'],
                runs=union,color='#ffce62',opacity=105,visible=True,
                facility_id=facility_id,members=list(facility['members']),
                unconfirmed_members=pending,permission_source='user_approved_facility_exact_union')
    result['lo'],result['hi']=bbox(result)
    doc._facility_permission_cache=(cache_key,result)
    return result


def select(doc, facility_id):
    scope=resolve(doc,facility_id)
    doc.active_space_id=None
    doc.active_design_facility=facility_id
    return scope
