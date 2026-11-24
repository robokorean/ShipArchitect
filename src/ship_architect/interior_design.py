"""Bounded, read-only evidence for approved interior planning."""
from collections import Counter
import json
import numpy as np
from .chunks import checkpoint
from .model import Bounds

MAX_ROUTE_CELLS = 200_000


def relevant_routes(doc, bounds):
    result = []
    for key, item in doc.analysis_state.get('scene_records', {}).items():
        if not key.startswith('route:'):
            continue
        points = item.get('positions', [])
        if not points:
            continue
        low = np.min(points, axis=0)
        high = np.max(points, axis=0)
        if any(b < lo or a > hi for a,b,lo,hi in zip(low,high,bounds.lo,bounds.hi)):
            continue
        result.append((key,item))
    return result


def reserved_route_indices(doc, bounds, cancel=None):
    """Keep existing route corridors available even when operation remains unverified."""
    w,h,l = doc.schematic.size
    cells = set()
    for key,item in relevant_routes(doc,bounds):
        points = item['positions']
        for first,last in zip(points,points[1:]):
            checkpoint(cancel)
            delta = np.asarray(last,dtype=np.int64) - first
            length = int(np.abs(delta).max())
            if length > 100_000:
                raise ValueError('경로 길이가 분석 한도를 초과합니다. 경로를 다시 검토하세요.')
            if not length:
                continue
            for step in range(length+1):
                if step % 256 == 0:
                    checkpoint(cancel)
                point = np.rint(np.asarray(first)+delta*(step/length)).astype(int)
                for dx in (-1,0,1):
                    for dz in (-1,0,1):
                        for dy in (-1,0,1,2):
                            x,y,z = map(int,point+(dx,dy,dz))
                            if all(a<=v<=b for a,v,b in zip(bounds.lo,(x,y,z),bounds.hi)):
                                cells.add((y*l+z)*w+x)
                if len(cells)>MAX_ROUTE_CELLS:
                    raise ValueError('보존할 통로가 너무 큽니다. 선택 영역을 줄이세요.')
    return np.fromiter(sorted(cells),dtype=np.int64)


def enrich_context(doc, bounds, context, cancel=None):
    space = doc.permission_space()
    if space is None and doc.source is not None:
        raise ValueError('기존 함선의 내부 설계는 확정 공간 또는 사용자가 확인한 시설을 선택한 뒤 시작하세요.')
    member = 'space:'+space['id'] if space is not None and 'facility_id' not in space else None
    facilities = []
    for key,item in doc.analysis_state.get('facilities',{}).items():
        if (member and member in item['members']) or (space is not None and space.get('facility_id')==key):
            facilities.append(dict(id=key,name=item['name'],purpose=item['purpose'],
                                   user_approved=item.get('approved') is True,
                                   member_count=len(item['members'])))
    equipment = []
    counts = Counter()
    for key,item in doc.analysis_state.get('scene_records',{}).items():
        if not key.startswith('equip:') or 'position' not in item:
            continue
        point = item['position']
        if not all(a<=v<=b for a,v,b in zip(bounds.lo,point,bounds.hi)):
            continue
        counts[item.get('device_kind',item.get('category','미분류'))] += 1
        if len(equipment)<96:
            x,y,z=point
            actual=doc.schematic.palette[int(doc.schematic.blocks[y,z,x])]
            equipment.append(dict(block=item['block'],actual_block=actual,
                                  evidence_status='current_block_match' if actual==item['block'] else 'stale_record_review',position=[v-a for v,a in zip(point,bounds.lo)],
                                  kind=item.get('device_kind',item.get('category','미분류')),
                                  operation='unverified',membership=item.get('membership_reason','unverified')))
    routes = []
    for key,item in relevant_routes(doc,bounds)[:32]:
        points = item['positions']
        routes.append(dict(id=key,status=item.get('status','unverified'),
                           game_collision_verified=False,device_operation_verified=False,
                           points=[[v-a for v,a in zip(point,bounds.lo)] for point in points[:64]],
                           points_truncated=len(points)>64))
    reserved = reserved_route_indices(doc,bounds,cancel)
    context['interior_design'] = {
        'scope':'exact_facility_union' if space and space.get('facility_id') else 'exact_registered_space' if space else 'new_project_selection',
        'facility_members':space.get('members',[]) if space else [],
        'unconfirmed_members':space.get('unconfirmed_members',0) if space else 0,
        'facilities':facilities[:16], 'equipment_counts':dict(counts),
        'equipment_samples':equipment, 'equipment_total_in_stored_records':sum(counts.values()),
        'equipment_samples_truncated':sum(counts.values())>len(equipment),
        'inventory_note':'Stored analysis records only. Missing records do not prove there are no devices.',
        'routes':routes,'route_corridor_reserved_cells':len(reserved),
        'reserved_corridor':'Preserve existing route center plus one cell on both X/Z sides, supporting floor and 3 cells high. Treat uncertain routes as reserved until user review.',
        'uncertainty':'Facility purpose requires user confirmation. All mod operation and game collision remain unverified.',
        'design_goal':'Propose purpose-specific partitions, furniture, windows and material choices inside the permitted cells. Keep routes and protected devices. Never auto-confirm facility use.'
    }
    if len(json.dumps(context,ensure_ascii=False).encode())>48_000:
        raise ValueError('설계 근거가 너무 큽니다. 더 작은 공간을 선택해 다시 요청하세요.')
    return context


def protect_routes(doc, bounds, indices, cancel=None):
    reserved = reserved_route_indices(doc,bounds,cancel)
    conflict = np.intersect1d(indices,reserved,assume_unique=True)
    if len(conflict):
        w,h,l = doc.schematic.size
        i = int(conflict[0])
        xyz = (i%w,i//(w*l),i//w%l)
        raise ValueError(f'기존 이동 경로의 보존 구간 {len(conflict)}칸과 충돌합니다. 첫 좌표 {xyz}. 통로를 비우도록 계획을 수정하세요.')
