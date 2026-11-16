"""Persistent recognition metadata for scene entities, independent of block edits."""
from collections import Counter
import copy
import hashlib
import json

from .analysis_state import commit
from .candidate_relations import prune_candidate_relations
from .equipment import record, owner_index, ownership
from .spatial3d import checkpoint, fingerprint

PREFIXES = ('equip:', 'connection:', 'lift:', 'route:')
MAX_RECORDS = 24_000


def key_at(prefix, xyz):
    return prefix + ','.join(map(str, xyz))


def stable_key(prefix, values):
    return prefix + hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]


def validate_scene(state, size):
    def valid_point(point):
        return isinstance(point, (list, tuple)) and len(point) == 3 and all(
            type(p) is int and 0 <= p < n for p, n in zip(point, size))
    records = state.get('scene_records', {})
    exclusions = state.get('scene_excluded', [])
    if not isinstance(records, dict) or len(records) > MAX_RECORDS:
        raise ValueError('프로젝트 탐색기 등록 한도 초과')
    if not isinstance(exclusions, list) or len(exclusions) > MAX_RECORDS or any(
            not isinstance(key, str) or not key.startswith(PREFIXES) for key in exclusions):
        raise ValueError('탐색기 제외 기록 오류')
    excluded_shapes = state.get('scene_excluded_shapes', [])
    if not isinstance(excluded_shapes, list) or len(excluded_shapes) > 8192 or any(
        not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key)
        for key in excluded_shapes
    ):
        raise ValueError('공간 형상 제외 기록 오류')
    for key, rec in records.items():
        points = rec.get('positions', [rec.get('position')]) if isinstance(rec, dict) else []
        if not key.startswith(PREFIXES) or not points or len(points) > 100_000 or not all(map(valid_point, points)):
            raise ValueError('탐색기 좌표 오류')


def discover(doc, result, inventory, cancel, state=None):
    """Register located evidence. Traversability and operation remain unconfirmed."""
    rows = owner_index(doc)
    output = {}
    for item in inventory.get('records', []):
        key = key_at('equip:', item['position'])
        output[key] = item
    for feature in result.get('semantic_features', []):
        checkpoint(cancel)
        xyz = feature['position']
        block = feature['block']
        base = block.split('[')[0]
        if 'elevator' in base:
            prefix, label = 'lift:', '승강기'
        elif base.endswith('_stairs'):
            prefix, label = 'connection:', '계단'
        elif base.endswith(('_door', '_trapdoor', '_gate')):
            prefix, label = 'connection:', '출입구'
        else:
            continue
        key = key_at(prefix, xyz)
        if 'half=upper' in block and base.endswith('_door'):
            continue
        data = record(doc, block, xyz, rows)
        output[key] = dict(data, label=label, status='닫힘' if feature['state_evidence']=='open=false' else '통행 미확인')
    for item in inventory.get('elevator_candidates', [])[:1024]:
        key = stable_key('lift:', sorted(item['positions']))
        output[key] = dict(item, label='승강기 층 연결', status='통행 미확인')
    seen_routes = set()
    for item in result.get('connections', []):
        points = item.get('positions') or ([item['position']] if 'position' in item else [])
        if not points:
            continue
        key = stable_key('connection:', points)
        if key in seen_routes:
            continue
        if len(output) >= MAX_RECORDS:
            break
        seen_routes.add(key)
        output[key] = dict(item, positions=points, candidate_ids=item.get('spaces', []), spaces=[],
                           label='통로 · 경계 후보', status='통행 미확인')
    state = state if state is not None else doc.analysis_state
    previous = state.get('scene_records', {})
    excluded = set(state.get('scene_excluded', []))
    for key, item in previous.items():
        if key in output:
            output[key].update({k:item[k] for k in ('name','manual','label') if k in item and item.get('manual')})
        elif item.get('manual') or key.startswith('route:'):
            output[key] = item
    return {key: val for key, val in output.items() if key not in excluded}


def prune_relationships(records, space_ids, candidate_ids):
    for record in records.values():
        for field, ids in (('spaces', space_ids), ('adjacent_spaces', space_ids), ('candidate_ids', candidate_ids)):
            if field in record:
                record[field] = [sid for sid in record[field] if sid not in ids]


def delete_items(doc, space_ids, candidate_ids, scene_keys, exclude=False, fallback=None):
    """Delete all requested metadata in one existing document history command."""
    state = copy.deepcopy(doc.analysis_state)
    scene = state.setdefault('scene_records', copy.deepcopy(fallback or {}))
    for key in scene_keys:
        scene.pop(key, None)
    if exclude:
        state['scene_excluded'] = sorted(set(state.get('scene_excluded', [])) | scene_keys)
    result = state.get('last_result')
    removed = [s for s in doc.spaces if s['id'] in space_ids]
    shapes = {fp for s in removed for fp in (s.get('recognition', {}).get('source_fingerprint'),
              s.get('recognition', {}).get('fingerprint'), fingerprint(s['runs'])) if fp}
    decisions = state.get('decisions', {})
    for fp in shapes:
        if decisions.get(fp, {}).get('action') == 'registered':
            decisions.pop(fp)
    deleted_candidates = set(candidate_ids)
    if result:
        retained = []
        for candidate in result['candidates']:
            fp = candidate.get('source_fingerprint', candidate['fingerprint'])
            if candidate['id'] in candidate_ids:
                shapes.add(fp)
                continue
            if candidate.get('existing_space_id') in space_ids or candidate.get('registered_space_id') in space_ids or fp in shapes:
                for link in ('existing_space_id','registered_space_id'):
                    candidate.pop(link, None)
                candidate['confirmed'] = False
                candidate['decision'] = 'review'
                if exclude:
                    shapes.add(fp)
                    deleted_candidates.add(candidate['id'])
                    continue
            retained.append(candidate)
        result['candidates'] = retained
        result['counts'] = dict(Counter(c['decision'] for c in retained))
        prune_candidate_relations(result, deleted_candidates)
    if exclude:
        state['scene_excluded_shapes'] = sorted(set(state.get('scene_excluded_shapes', [])) | shapes)
    if candidate_ids:
        for key in ('session_reference','session_changes','session_side','comparison'):
            state.pop(key,None)
    prune_relationships(scene, space_ids, deleted_candidates)
    zones = [z for z in doc.zones if z.get('registered_space_id') not in space_ids]
    old_zones = copy.deepcopy(doc.zones)
    commit(doc, state, [s for s in doc.spaces if s['id'] not in space_ids], 'scene_metadata_delete')
    if zones != old_zones:
        doc.undo_stack[-1].update(zones_before=old_zones,zones_after=copy.deepcopy(zones))
        doc.zones = zones
    doc.save_empty_spaces = True


def bind_spaces(records, old_spaces, new_spaces, size):
    """Recompute physical ownership after geometry edits while retaining item IDs."""
    old = {s['id']:s['runs'] for s in old_spaces}
    new = {s['id']:s['runs'] for s in new_spaces}
    if old == new:
        return
    rows = {}
    for sid,runs in new.items():
        for y,z,a,b in runs:
            rows.setdefault((y,z),[]).append((a,b,sid))
    for key,item in records.items():
        if 'position' in item:
            inside,near=ownership(item['position'],rows,size)
            item['spaces'],item['adjacent_spaces']=inside,near
        else:
            previous=item.get('spaces',[])
            valid=[sid for sid in previous if sid in new and new[sid]==old.get(sid)]
            if len(previous)!=len(valid):
                item['spaces']=valid
                item['status']='공간 변경 · 연결 재검토'
