"""Read-only structure evidence extracted from original occupancy in Y/Z/X order.

Surface runs encode the owner cell and its oriented face. Air runs encode exact
volumes. Neither is used as a permission mask or a replacement room partition.
"""
from collections import Counter
from time import perf_counter

import numpy as np
from numba import njit

from .chunks import checkpoint
from .equipment import classification

VERSION = '0.9.3.6'
# id: label, color, what the source establishes
LAYERS = {
    'hull': ('외벽·외형', '#54c9e2', '외부와 맞닿는 실제 선체 면'),
    'decks': ('갑판', '#ffd166', '넓고 연속된 수평 표면 · 용도 추정'),
    'walls': ('내부 벽', '#81a1ef', '내부 추정 공기와 맞닿는 수직 면'),
    'ceilings': ('천장', '#b39ddb', '내부 추정 공기 바로 위의 실제 면'),
    'supports': ('기둥·보·발판', '#e7a876', '선형 또는 작은 수평 부재 후보'),
    'equipment': ('설비', '#ee93cf', '블록 ID 및 분류 규칙 · 작동 미확인'),
    'openings': ('실제 개구부', '#8beaab', '공기로 이어지는 관측 면 · 통행 미확인'),
    'virtual': ('가상 경계', '#ff896d', '외형 또는 라벨에서 추정한 분석면'),
    'external_air': ('실제 외부 연결 공기', '#64b4f6', '지형 제외 시 일반 공기가 지형 마스크를 통해 연결될 수 있음'),
    'interior': ('내부 추정·미구획', '#99db86', '원본에서 비어 있는 외형 추정 셀'),
    'terrain': ('분석 제외 지형', '#aa967f', '지형 마스크 · 원본 보존'),
}


@njit(cache=True, nogil=True)
def streak(mask, axis):
    """Length of the occupied segment through each cell in one direction."""
    out = np.zeros(mask.shape, np.uint16)
    height, depth, width = mask.shape
    for y in range(height):
        for z in range(depth):
            for x in range(width):
                if mask[y, z, x]:
                    n = 1
                    if axis == 0 and y: n += out[y-1, z, x]
                    if axis == 1 and z: n += out[y, z-1, x]
                    if axis == 2 and x: n += out[y, z, x-1]
                    out[y, z, x] = n
    for y in range(height-1, -1, -1):
        for z in range(depth-1, -1, -1):
            for x in range(width-1, -1, -1):
                if not mask[y, z, x]:
                    continue
                if axis == 0 and y+1 < height and mask[y+1,z,x]: out[y,z,x] = out[y+1,z,x]
                if axis == 1 and z+1 < depth and mask[y,z+1,x]: out[y,z,x] = out[y,z+1,x]
                if axis == 2 and x+1 < width and mask[y,z,x+1]: out[y,z,x] = out[y,z,x+1]
    return out


def adjacent(array, axis, sign, fill=False):
    out = np.roll(array, -sign, axis)
    edge = [slice(None)] * 3
    edge[axis] = -1 if sign == 1 else 0
    out[tuple(edge)] = fill
    return out


def runs(mask, offset, cancel=None):
    result = []
    x0, y0, z0 = offset
    for y, plane in enumerate(mask):
        checkpoint(cancel)
        ends = np.argwhere(np.diff(plane, axis=1, prepend=False, append=False))
        for ((z, a), (_, b)) in ends.reshape(-1, 2, 2):
            result.append([y+y0, int(z)+z0, int(a)+x0, int(b)-1+x0])
    return result


def cell_count(rows):
    return sum(b-a+1 for _, _, a, b in rows)


def analyse(doc, bounds, hull, terrain, air, raw, domain, labels, candidates, progress, cancel):
    started = perf_counter()
    checkpoint(cancel)
    offset = bounds.lo
    sc = doc.schematic
    x0,y0,z0 = offset
    x1,y1,z1 = bounds.hi
    source = sc.blocks[y0:y1+1,z0:z1+1,x0:x1+1]
    categories = {i: classification(doc, state) for i,state in sc.palette.items()}
    equipment_ids = [i for i,name in categories.items() if name in (
        'Create', 'Create 엘리베이터 후보', 'ComputerCraft', '추진 설비 후보', '전력 설비 후보')]
    equipment = hull & np.isin(source, equipment_ids)
    structure = hull & ~equipment
    sy, sz, sx = (streak(structure, axis) for axis in range(3))
    # Report measured geometry. Thickness/continuity are evidence, not a declaration
    # that the horizontal structure is a complete deck or seals a room.
    above = adjacent(air, 0, 1)
    top = structure & above
    deck = top & (sx >= 6) & (sz >= 6)
    narrow = structure & (((sx <= 2) & (sz <= 2) & (sy >= 3))
                          | ((sy <= 2) & ((sx >= 4) ^ (sz >= 4))))
    platform = top & ~deck & (sx >= 2) & (sz >= 2)
    wall = structure & (sy >= 2) & ((sx >= 3) | (sz >= 3))
    del sx, sz, sy
    data = {key: {'faces': [], 'runs': [], 'count': 0, 'rows_omitted': 0} for key in LAYERS}

    def add(key, mask, face=None, count=True):
        layer = data[key]
        if count:layer['count'] += int(mask.sum())
        rows = runs(mask, offset, cancel)
        limit = 150_000
        target = layer['runs'] if face is None else layer['faces']
        slots = max(0, limit-len(target))
        layer['rows_omitted'] += max(0,len(rows)-slots)
        if face is None:
            target.extend(rows[:slots])
        else:
            target.extend([*face, *row] for row in rows[:slots])

    add('decks', deck, (1, 1))
    add('supports', narrow|platform)
    add('equipment', equipment)
    equipment_groups = []
    equipment_by_label = {}
    for name in sorted({categories[i] for i in equipment_ids}):
        matches = equipment & np.isin(source, [i for i in equipment_ids if categories[i]==name])
        if matches.any():
            points=np.nonzero(matches)
            neighbouring_ids=set()
            for axis in range(3):
                for sign in (-1,1):
                    shifted=points[axis]+sign
                    valid=(shifted>=0)&(shifted<labels.shape[axis])
                    index=[p[valid] for p in points]
                    index[axis]=shifted[valid]
                    neighbouring_ids.update(int(i) for i in np.unique(labels[tuple(index)]) if i)
            for ident in neighbouring_ids:equipment_by_label.setdefault(ident,[]).append(name)
            equipment_groups.append({'category':name,'cells':int(matches.sum()),'operation':'unknown',
                'adjacent_space_ids':[c['id'] for c in candidates if c['virtual_boundary']['label'] in neighbouring_ids]})
    add('external_air', raw)
    add('interior', domain)
    add('terrain', terrain)
    # Actual exterior faces and interior faces remain independent. A thin shell
    # can legitimately appear in both. Selection edges are recorded as scope.
    opening_profiles = {}
    # A downward-facing hole can lose its entire column in the legacy floor+
    # ceiling projection. Recognise it from its horizontal contrast with
    # neighbouring supported air, while keeping the existing room volume.
    horizontal_pairs=[]
    for axis in (1,2):
        near=np.maximum.accumulate(hull,axis=axis)
        far=np.flip(np.maximum.accumulate(np.flip(hull,axis=axis),axis=axis),axis=axis)
        horizontal_pairs.append(near&far)
    under_roof=np.flip(np.maximum.accumulate(np.flip(hull,axis=0),axis=0),axis=0)
    lower_gap=air&raw&under_roof&horizontal_pairs[0]&horizontal_pairs[1]&~domain
    del horizontal_pairs,under_roof
    lower_by_label=Counter()
    for axis in (1,2):
        for sign in (-1,1):
            neighbor=adjacent(labels,axis,sign)
            contact=lower_gap&(neighbor>0)
            nums=np.bincount(neighbor[contact])
            for k in np.flatnonzero(nums):lower_by_label[int(k)]+=int(nums[k])
    # The lowest gap plane with supported air around it describes where a
    # missing floor may connect downward. Its orientation is inferred.
    base_neighbor=np.zeros_like(lower_gap)
    for axis in (1,2):
        for sign in (-1,1):base_neighbor |= adjacent(hull,axis,sign)
    lower_mouth=lower_gap & base_neighbor & adjacent(raw,0,-1) & adjacent(lower_gap,0,1) & ~adjacent(lower_gap,0,-1)
    add('virtual',lower_mouth,(1,-1))

    directions = [(0,1),(1,2),(2,0)]
    for axis, xyz in directions:
        for sign in (-1,1):
            checkpoint(cancel)
            face = (xyz, sign)
            neighbour_domain = adjacent(domain, axis, sign)
            add('external_air',raw & ~adjacent(raw,axis,sign),face,count=False)
            add('interior',domain & ~neighbour_domain,face,count=False)
            add('hull', hull & adjacent(raw|terrain, axis, sign, True), face)
            if axis:
                add('walls', wall & neighbour_domain, face)
            elif sign == -1:
                add('ceilings', structure & neighbour_domain, face)
            # A fitted interface must have two real empty cells. A cut at the
            # edge of the analysed scope is not counted as a physical opening.
            neighbour_label = adjacent(labels, axis, sign)
            between = domain & adjacent(air, axis, sign) & ~neighbour_domain
            mouth = between & adjacent(raw, axis, sign)
            add('openings', mouth, face)
            add('virtual', between | (domain & (neighbour_label>0) & (labels<neighbour_label)), face)
            nums = np.bincount(labels[mouth])
            opening_profiles[f'{"XYZ"[xyz]}{"+" if sign>0 else "-"}'] = nums

    fragments = []
    for candidate in candidates:
        checkpoint(cancel)
        ident = candidate['virtual_boundary']['label']
        openings = {k:int(v[ident]) for k,v in opening_profiles.items() if len(v)>ident and v[ident]}
        item = {
            'id': candidate['id'], 'lo':candidate['lo'], 'hi':candidate['hi'],
            'volume':candidate['volume'], 'old_class':candidate['structure_class'],
            'external_connection': bool(candidate['virtual_boundary']['raw_exterior_cells']),
            'air_connected_cells':candidate['virtual_boundary']['raw_exterior_cells'],
            'hull_membership':'observed_enclosed' if not candidate['virtual_boundary']['raw_exterior_cells'] else 'unresolved',
            'partition_status':'undetermined',
            'contacts':candidate['boundary_contacts'], 'opening_faces':openings,
            'confidence':'review', 'purpose':None, 'deck_sections':[],
            'equipment_groups_adjacent':equipment_by_label.get(ident,[]),
            'opening_pattern':('opposing_sides' if any(axis+'-' in openings and axis+'+' in openings for axis in 'XZ')
                               else 'one_side' if sum(d[0] in 'XZ' for d in openings)==1
                               else 'multiple_sides' if any(d[0] in 'XZ' for d in openings) else 'no_lateral_interface'),
            'lower_opening_observed':'Y-' in openings,
            'lower_opening_candidate_contacts':lower_by_label[ident],
            'upper_opening_observed':'Y+' in openings,
            'adjacent_space_ids':[x['id'] for x in candidate['adjacent_spaces']],
            'reasons': list(candidate['reasons']),
        }
        cls = item['old_class']
        if cls=='closed_interior':
            item.update(classification='closed_interior', confidence='observed_enclosure')
        elif cls in ('open_interior','hangar_or_large_opening'):
            item['classification']='open_interior'
        elif cls in ('sky_exposed','exposed_deck'):
            item['classification']='exposed_facility'
        elif cls=='exterior_air':
            item['classification']='exterior_air'
        else:
            item['classification']='uncertain'
        # Inspect per-deck continuity against both vertically adjacent air profiles.
        # Candidate labels may already differ on opposite sides of a partial deck.
        lo,hi=candidate['lo'],candidate['hi']
        if item['volume']>=100:
            a,b=lo[0]-x0,hi[0]-x0+1
            c,d=lo[2]-z0,hi[2]-z0+1
            region=np.s_[c:d,a:b]
            below_label=max(1,lo[1]-y0-1)
            above_label=min(len(labels)-1,hi[1]-y0+2)
            for y in range(below_label,above_label):
                checkpoint(cancel)
                slab=deck[y][region]
                if int(slab.sum())<24:
                    continue
                lower=domain[y-1][region];upper=domain[y+1][region]
                local=(labels[y-1][region]==ident)|(labels[y][region]==ident)|(labels[y+1][region]==ident)
                paired=lower & upper & local
                through=air[y][region]
                section=paired & (slab | through)
                paired_deck=section&slab
                opening=section&~slab
                deck_cells=int(paired_deck.sum()); opening_cells=int(opening.sum())
                if deck_cells<24 or opening_cells<4:
                    continue
                # Use actual occupied contiguous plate cells, paired air above and
                # below, and adjacent through-holes. Do not split candidate IDs.
                touches=False
                for axis in (0,1):
                    left=[slice(None)]*2;right=left.copy()
                    left[axis]=slice(1,None);right[axis]=slice(None,-1)
                    l,r=tuple(left),tuple(right)
                    touches|=bool((paired_deck[l]&opening[r]).any() or (opening[l]&paired_deck[r]).any())
                if not touches:
                    continue
                points=np.argwhere(section)
                item['deck_sections'].append({
                    'y':y+y0, 'deck_cells':deck_cells, 'through_air_cells':opening_cells,
                    'coverage':round(deck_cells/(deck_cells+opening_cells),4),
                    'air_samples_relative_y':[-1,1],
                    'lo':[int(points[:,1].min())+a+x0,y+y0,int(points[:,0].min())+c+z0],
                    'hi':[int(points[:,1].max())+a+x0,y+y0,int(points[:,0].max())+c+z0],
                    'basis':'long occupied runs in both horizontal axes, air above/below and adjacent vertical aperture',
                })
        if item['deck_sections'] and item['classification'] in ('open_interior','uncertain'):
            item['classification']='partial_partition'
        elif (item['classification']=='uncertain' and lower_by_label[ident]>=12 and item['contacts']['roof']>0):
            item['classification']='open_interior'
            item['reasons'].append('roofed lower aperture with lateral candidate contacts; virtual extent')
        elif (item['classification']=='uncertain' and not item['external_connection']
              and item['contacts']['hull']>0):
            item['classification']='unpartitioned_interior'
        elif item['classification']=='uncertain' and not item['deck_sections']:
            item['reasons'].append('no paired deck and through-opening established; segmentation unresolved')
        if item['classification'] in ('open_interior','partial_partition') and cls not in ('unresolved_deck_group','unresolved_exterior_merge'):
            item['hull_membership']='estimated'
        if item['classification'] in ('exposed_facility','exterior_air'):
            item['hull_membership']='outside_or_exposed'
        item['partition_status']={
            'closed_interior':'observed_closed','partial_partition':'partial',
            'unpartitioned_interior':'unpartitioned','open_interior':'open',
        }.get(item['classification'],'undetermined')
        if cls in ('unresolved_deck_group','unresolved_exterior_merge'):
            item['reasons'].append('legacy broad complex retained; local deck evidence does not establish full interior membership or room division')
        fragments.append(item)

    checkpoint(cancel)
    if progress: progress(98,'구조 요소·관측/가상 경계 검증 완료')
    return {'version':VERSION,'scope':{'lo':list(bounds.lo),'hi':list(bounds.hi)},'layers':data,
            'spaces':fragments, 'equipment_groups':equipment_groups,
            'class_counts':dict(Counter(x['classification'] for x in fragments)),
            'settings':{'air_model':'original air flood with optional terrain mask',
                        'opening_model':'air-to-air interface inferred from pre-partition envelope; passage not verified',
                        'scope_edges':'exposed if outside analysis; opening faces require observed air on both sides',
                        'face_order':'XYZ axis, direction, Y, Z, X start, X end'},
            'elapsed_seconds':round(perf_counter()-started,5)}


def validate_review(review,size):
    """Reject malformed on-disk geometry before the display cache consumes it."""
    from .model import Bounds
    if not isinstance(review,dict) or review.get('version') != VERSION:
        raise ValueError('구조 검증 버전을 확인할 수 없습니다.')
    scope=review.get('scope',{})
    if not isinstance(scope,dict):raise ValueError('구조 검증 범위 형식 오류')
    bounds=Bounds(tuple(scope.get('lo',[])),tuple(scope.get('hi',[])))
    bounds.validate(size)
    layers=review.get('layers')
    if not isinstance(layers,dict) or set(layers)!=set(LAYERS):
        raise ValueError('구조 검증 레이어 목록 오류')
    for layer in layers.values():
        if not isinstance(layer,dict):raise ValueError('구조 검증 레이어 형식 오류')
        for field in ('count','rows_omitted'):
            if type(layer.get(field)) is not int or layer[field]<0:raise ValueError('구조 검증 집계 오류')
        for field,n in (('runs',4),('faces',6)):
            rows=layer.get(field)
            if not isinstance(rows,list) or len(rows)>150_000:raise ValueError('구조 검증 구간 수 오류')
            if rows:
                arr=np.asarray(rows)
                if arr.shape!=(len(rows),n) or arr.dtype.kind not in 'iu':raise ValueError('구조 검증 구간 형식 오류')
                y,z,a,b=arr[:,-4:].T
                if not ((y>=bounds.lo[1])&(y<=bounds.hi[1])&(z>=bounds.lo[2])&(z<=bounds.hi[2])
                        &(a>=bounds.lo[0])&(b>=a)&(b<=bounds.hi[0])).all():
                    raise ValueError('구조 검증 구간 범위 오류')
                if n==6 and not ((arr[:,0]>=0)&(arr[:,0]<3)&(np.abs(arr[:,1])==1)).all():
                    raise ValueError('구조 검증 면 방향 오류')
    for key,limit in (('spaces',512),('manual_boundaries',256)):
        records=review.get(key,[])
        if not isinstance(records,list) or len(records)>limit:raise ValueError('구조 검증 항목 수 오류')
        ids=set()
        for record in records:
            if not isinstance(record,dict) or not isinstance(record.get('id'),str) or record['id'] in ids:
                raise ValueError('구조 검증 항목 ID 오류')
            ids.add(record['id'])
            low,high=tuple(record.get('lo',[])),tuple(record.get('hi',[]))
            Bounds(low,high).validate(size)
            if key=='manual_boundaries':
                axis=record.get('axis')
                if type(axis) is not int or not 0<=axis<3 or low[axis]!=high[axis]:
                    raise ValueError('수동 경계 방향 오류')
            elif record.get('classification') not in ('closed_interior','open_interior','partial_partition',
                'unpartitioned_interior','exposed_facility','exterior_air','uncertain'):
                raise ValueError('구조 검증 공간 판정 오류')
