"""Review classifications and display ownership derived from recognition evidence."""
from collections import defaultdict
from dataclasses import dataclass

LABELS = {'major':'주요 공간','support':'보조 공간','movement':'이동 공간',
          'attachment':'부속 조각','residual':'활용 제한 잔여','external':'외부 공기',
          'uncertain':'미확정'}
VISIBLE_CLASSES = frozenset(('major','support','movement'))
EXTERNAL = {'exterior_air','exterior_or_merged','sky_exposed','exposed_deck'}
MANAGEMENT_LIMIT = 3584


@dataclass(frozen=True)
class Classification:
    kind: str
    volume: int
    reason: str
    owner: str | None = None


def _base(c, facilities):
    size = c.get('volume', c.get('area',0))
    checks = c.get('checks', {})
    ctype = c.get('space_class')
    facts = c.get('space_facts',{})
    proof = c.get('structure_evidence',{})
    reviewed = c.get('decision') in ('review','auto','registered')
    height = c['hi'][1] - c['lo'][1] + 1
    width = min(c['hi'][0] - c['lo'][0] + 1, c['hi'][2] - c['lo'][2] + 1)
    contacts=c.get('boundary_contacts',{})
    roof = max(c.get('ceiling_ratio',0) or 0,contacts.get('roof',0)/max(1,contacts.get('top_boundary',0)))
    wall = c.get('wall_ratio',0) or 0
    tall = checks.get('two_cell_clearance',height >= 2)
    floor = checks.get('usable_floor',False)
    wide = checks.get('wide_floor_patch',width >= 3)
    paired = 'floor_y' in proof and 'roof_y' in proof
    enclosed = (checks.get('original_connected_component') or facts.get('enclosure')=='observed_enclosed'
                or roof >= .5 and wall >= .35)
    if ctype in EXTERNAL and not (reviewed and floor and facilities):
        return Classification('external',size,'구조 분석의 외부 노출·공기 판정')
    if facilities and (reviewed or tall and (floor or facts.get('membership') in ('estimated','inside'))):
        elevator = any('elevator' in item or 'lift' in item for item in facilities)
        if elevator or c.get('kind')=='corridor':
            return Classification('movement',size,'장치·계단·개구의 인접 근거 · 통행 검증 전')
        if not (reviewed and size>=1500 and wide and tall and floor):
            return Classification('support',size,'내부 구조와 설비 인접 근거')
    if reviewed and size>=1500 and wide and tall and floor and (paired or enclosed or roof>=.60):
        return Classification('major',size,'높이·넓이 및 바닥과 천장·벽 지지 근거')
    if reviewed and c.get('kind')=='corridor' and tall and floor:
        return Classification('movement',size,'높이와 바닥이 있는 좁은 구역 · 통행 검증 전')
    if reviewed and tall and floor and (paired or enclosed or wide):
        return Classification('support',size,'높이 및 독립 바닥·경계 근거')
    if not tall or facts.get('usability')=='restricted':
        return Classification('residual',size,'높이 제한 · 활용 검토 필요')
    return Classification('uncertain',size,'독립 용도 또는 경계 근거가 부족함')


def classify(result, overrides=None, registered=()):
    candidates=result.get('candidates',[])
    facilities=defaultdict(set)
    for feature in result.get('semantic_features',[]):
        for key in feature.get('spaces',[]):
            facilities[key].add(str(feature.get('block','')).split('[',1)[0])
    for detail in result.get('structure_review',{}).get('spaces',[]):
        entries=detail.get('equipment_groups_adjacent',[])
        if entries:
            facilities[detail['id']].add('장치')
    classes={'candidate:'+c['id']:_base(c,facilities[c['id']]) for c in candidates}
    spaces={'space:'+s['id'] for s in registered}
    registered_ids={c['id']:'space:'+sid for c in candidates
                    if (sid:=c.get('registered_space_id') or c.get('existing_space_id')) and 'space:'+sid in spaces}
    for c in candidates:
        key='candidate:'+c['id']
        item=classes[key]
        fragment=c.get('fragment',{})
        parent=fragment.get('suggested_owner_id')
        if item.kind in VISIBLE_CLASSES or not parent:
            continue
        parent_key='candidate:'+parent
        proposed=classes.get(parent_key)
        if proposed is None or proposed.kind not in VISIBLE_CLASSES or proposed.volume <= item.volume:
            continue
        faces=fragment.get('shared_faces',0)
        total=fragment.get('adjacent_faces',0)
        contacts=[x.get('unit_faces',0) for x in c.get('adjacent_spaces',[]) if x.get('id')!=parent]
        if faces >= 4 and faces*3>=total*2 and faces>max(contacts, default=0):
            classes[key]=Classification('attachment',item.volume,'공유 면 우세 · 형상 독립 유지',
                                        registered_ids.get(parent,parent_key))
    overrides=overrides or {}
    available=set(classes)|spaces
    for child,parent in overrides.items():
        if child not in classes:
            continue
        item=classes[child]
        if parent is None or parent not in available:
            if item.owner is not None:
                classes[child]=Classification('uncertain',item.volume,'수동으로 소속 해제됨')
        else:
            classes[child]=Classification('attachment',item.volume,'사용자가 지정한 소속',parent)
    # A one-level display tree avoids automatic cascades and cyclic user input.
    for key,item in list(classes.items()):
        if item.owner in classes and classes[item.owner].owner is not None:
            classes[key]=Classification('uncertain',item.volume,'상위 소속을 확인해야 함')
    return classes


def validate_memberships(value):
    if not isinstance(value,dict) or len(value)>MANAGEMENT_LIMIT:
        raise ValueError('공간 소속 설정 한도를 초과했습니다.')
    for child,parent in value.items():
        if not isinstance(child,str) or not child.startswith('candidate:') or len(child)>160:
            raise ValueError('부속 후보 ID가 올바르지 않습니다.')
        if parent is not None and (not isinstance(parent,str) or len(parent)>160 or
                not parent.startswith(('candidate:','space:')) or child==parent):
            raise ValueError('상위 공간 ID가 올바르지 않습니다.')
    for parent in value.values():
        if parent in value and value[parent] is not None:
            raise ValueError('부속 후보를 다른 부속 후보의 상위 공간으로 지정할 수 없습니다.')


def prune_memberships(state, removed=(), replacement=None):
    from .facilities import prune
    prune(state, removed, replacement)
    mapping=state.get('space_memberships')
    if not mapping:
        return
    removed=set(removed)
    for key in list(mapping):
        if key in removed:
            del mapping[key]
        elif mapping[key] in removed:
            mapping[key]=replacement
