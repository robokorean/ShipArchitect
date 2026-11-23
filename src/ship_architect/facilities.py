"""User-approved logical facilities over stable space references, independent of geometry."""
from copy import deepcopy
from uuid import uuid4
from collections import defaultdict

from .analysis_state import commit


def validate_facilities(value):
    if not isinstance(value, dict) or len(value) > 1024:
        raise ValueError('시설 그룹은 최대 1,024개입니다.')
    for key, facility in value.items():
        if not isinstance(key, str) or not key or len(key) > 64 or not isinstance(facility, dict):
            raise ValueError('시설 ID 형식 오류')
        for field in ('name', 'purpose'):
            if not isinstance(facility.get(field), str) or not facility[field].strip() or len(facility[field]) > 200:
                raise ValueError('시설 이름과 용도는 1~200자입니다.')
        members = facility.get('members')
        if not isinstance(members, list) or not members or len(members) > 4096 or any(
            not isinstance(ref, str) or len(ref) > 160 or not ref.startswith(('space:', 'candidate:'))
            for ref in members
        ) or len(set(members)) != len(members):
            raise ValueError('시설의 공간 참조 형식 오류')
        if facility.get('approved') is not True:
            raise ValueError('사용자가 확인한 시설만 저장할 수 있습니다.')


def space_records(doc):
    return {'space:' + s['id']: s for s in doc.spaces} | {
        'candidate:' + c['id']: c for c in doc.space_candidates
    }


def describe(doc, facility):
    records = space_records(doc)
    present = [ref for ref in facility['members'] if ref in records]
    missing = sorted(set(facility['members']) - set(present))
    runs = [r for ref in present for r in records[ref].get('runs', records[ref].get('volume_runs', []))]
    rows = defaultdict(list)
    for y,z,a,b in runs:
        rows[y,z].append((a,b))
    volume = 0
    for spans in rows.values():
        end = -1
        for left,right in sorted(spans):
            volume += max(0,right-max(left,end+1)+1)
            end = max(end,right)
    return dict(members=present, missing=missing,
                physical_cells=volume,
                purpose=facility['purpose'], approved=True)


def save_facility(doc, members, name, purpose='미지정', facility_id=None):
    members = sorted(set(members))
    known = space_records(doc)
    retained = doc.analysis_state.get('facilities',{}).get(facility_id,{}).get('members',[])
    if not members or any(ref not in known and ref not in retained for ref in members):
        raise ValueError('현재 문서의 공간 또는 후보를 선택하세요.')
    state = deepcopy(doc.analysis_state)
    groups = state.setdefault('facilities', {})
    if facility_id is not None and facility_id not in groups:
        raise ValueError('시설을 다시 선택하세요.')
    key = facility_id or uuid4().hex
    groups[key] = dict(name=name.strip(), purpose=purpose.strip(), members=members, approved=True)
    validate_facilities(groups)
    commit(doc, state, action='facility_update' if facility_id else 'facility_create')
    return key


def dissolve(doc, facility_id):
    state = deepcopy(doc.analysis_state)
    if facility_id not in state.get('facilities', {}):
        raise ValueError('시설을 다시 선택하세요.')
    del state['facilities'][facility_id]
    commit(doc, state, action='facility_dissolve')


def prune(state, removed, replacement=None):
    """Explicit deletion/merge follows members. Reanalysis retains unresolved references."""
    if 'facilities' not in state:
        return
    removed = set(removed)
    for facility in state.get('facilities', {}).values():
        refs = set(facility['members'])
        if refs & removed:
            refs -= removed
            if replacement:
                refs.add(replacement)
            facility['members'] = sorted(refs)
    state['facilities'] = {k:v for k,v in state.get('facilities', {}).items() if v['members']}


def inherit_split(state, original, additional):
    for facility in state.get('facilities',{}).values():
        if original in facility['members']:
            facility['members'] = sorted(set(facility['members']) | {additional})
