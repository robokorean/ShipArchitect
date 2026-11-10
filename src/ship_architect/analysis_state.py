"""Validated project rules, immutable review choices and reversible analysis metadata."""
import copy,json,re
from .model import Bounds
from .space_model import normalize,new_space,bbox
from .spatial3d import fingerprint,intersects
METHODS=('3d_air_components','exterior_voxel_mesh')
EQUIPMENT_TYPES=('Create','ComputerCraft','추진 설비 후보','전력 설비 후보','장식용 블록','미분류 모드 블록')

def validate(value,size):
    if not isinstance(value,dict) or len(json.dumps(value))>48_000_000:raise ValueError('분석 설정 형식/48MB 한도 오류')
    state=copy.deepcopy(value);rules=state.setdefault('equipment_rules',{})
    if not isinstance(rules,dict) or len(rules)>4096:raise ValueError('설비 규칙 수 오류')
    for key,val in rules.items():
        if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9_.-]+:[a-z0-9_./-]+(?:\[[a-z0-9_=,.-]+\])?',key) or val not in EQUIPMENT_TYPES:raise ValueError('정확한 블록 ID와 지원 분류가 필요합니다.')
    for rule in state.setdefault('space_rules',[]):
        if rule.get('action') not in ('exclude','crop') or not isinstance(rule.get('reason'),str):raise ValueError('공간 규칙 오류')
        if rule['action']=='crop':Bounds(tuple(rule['lo']),tuple(rule['hi'])).validate(size)
    if len(state['space_rules'])>256:raise ValueError('공간 규칙 최대 256개')
    if not isinstance(state.setdefault('decisions',{}),dict) or len(state['decisions'])>2048:raise ValueError('검토 판단 수 오류')
    for entry in state['decisions'].values():
        if entry.get('action') not in ('exclude','review','crop','registered'):raise ValueError('검토 판단 오류')
        if 'runs' in entry:entry['runs']=normalize(entry['runs'],size)
    last=state.get('last_result')
    if last is not None:
        if last.get('method') not in METHODS or len(last.get('candidates',[]))>512:raise ValueError('3D 분석 복원 오류')
        count=0
        for c in last.get('candidates',[]):
            c['volume_runs']=normalize(c.get('volume_runs',[]),size);count+=len(c['volume_runs'])
            Bounds(tuple(c['lo']),tuple(c['hi'])).validate(size)
            if c.get('decision') not in ('auto','review','exclude','registered'):raise ValueError('후보 판정 오류')
        if count>500000:raise ValueError('후보 전체 50만 runs 한도')
    comparison=state.get('comparison')
    if comparison is not None:
        if not isinstance(comparison,dict):raise ValueError('인식 비교 형식 오류')
        for key in ('old','new'):
            validate({'last_result':comparison[key]},size)
        if len(comparison.get('matches',[]))>512:raise ValueError('비교 쌍 한도 초과')
    return state

def commit(doc,state,spaces=None,action='analysis_settings'):
    state=validate(state,doc.schematic.size);before=copy.deepcopy(doc.analysis_state)
    doc.set_spaces(doc.spaces if spaces is None else spaces,action,doc.active_space_id if doc.active_space_id in {s['id'] for s in (doc.spaces if spaces is None else spaces)} else None)
    record=doc.undo_stack[-1];record['analysis_before']=before;record['analysis_after']=copy.deepcopy(state);doc.analysis_state=state
    doc.space_candidates=copy.deepcopy(state.get('last_result',{}).get('candidates',[]));doc.space_revision=doc.revision

def refresh_shape(c):
    if c.get('method')=='exterior_voxel_mesh':
        c['mesh']=None;c.setdefault('validation',{})['mesh_volume_matches_voxels']=None;c['validation']['geometry_changed']='Rebuild indexed mesh from exact cells on display/export'
    lo,hi=bbox({'runs':c['volume_runs']});c['lo']=list(lo);c['hi']=list(hi);c['floor_y']=lo[1]
    c['footprint']=[[z,a,b] for y,z,a,b in c['volume_runs'] if y==lo[1]];c['area']=sum(b-a+1 for z,a,b in c['footprint']);c['volume']=sum(b-a+1 for _,_,a,b in c['volume_runs'])

def crop(c,b,size):
    runs=[]
    for y,z,a,B in c['volume_runs']:
        if b.lo[1]<=y<=b.hi[1] and b.lo[2]<=z<=b.hi[2]:
            a=max(a,b.lo[0]);B=min(B,b.hi[0])
            if a<=B:runs.append([y,z,a,B])
    if not runs:raise ValueError('현재 3D 선택과 후보의 실제 셀이 겹치지 않습니다.')
    c['volume_runs']=normalize(runs,size);lo,hi=bbox({'runs':c['volume_runs']});c['lo']=list(lo);c['hi']=list(hi);c['fingerprint']=fingerprint(c['volume_runs']);c['decision']='review';c['user_modified']=True;c['reasons']=['user_boundary_crop'];c['volume']=sum(b-a+1 for _,_,a,b in runs);refresh_shape(c)

def apply_rules(result,state,size):
    result=copy.deepcopy(result)
    for c in result['candidates']:
        original=c['fingerprint'];c['source_fingerprint']=original
        decision=state.get('decisions',{}).get(original)
        if decision:
            c['decision']='registered' if decision['action']=='registered' else 'exclude' if decision['action']=='exclude' else 'review';c['user_modified']=True
            if 'runs' in decision:
                c['volume_runs']=copy.deepcopy(decision['runs']);lo,hi=bbox({'runs':c['volume_runs']});c['lo']=list(lo);c['hi']=list(hi);c['fingerprint']=fingerprint(c['volume_runs']);c['volume']=sum(b-a+1 for _,_,a,b in c['volume_runs']);refresh_shape(c)
            c['reasons']=c['reasons']+['protected_user_decision'];continue
        for rule in state.get('space_rules',[]):
            if rule['reason'] not in c['reasons']:continue
            if rule['action']=='exclude':c['decision']='exclude';c['reasons'].append('user_exclusion_rule')
            else:
                b=Bounds(tuple(rule['lo']),tuple(rule['hi']))
                if intersects(Bounds(tuple(c['lo']),tuple(c['hi'])),b):crop(c,b,size)
    return result

def register(doc,result,automatic=True,ids=None):
    if result.get('block_epoch')!=doc.block_epoch:raise ValueError('블록 변경 뒤 분석 결과가 오래되었습니다.')
    spaces=copy.deepcopy(doc.spaces);added=[];candidates=copy.deepcopy(result['candidates']);rows={};existing={fingerprint(s['runs']):s['id'] for s in spaces}
    for s in spaces:
        for y,z,a,b in s['runs']:rows.setdefault((y,z),[]).append((a,b))
    from .space_model import contains
    w,h,l=doc.schematic.size
    for c in candidates:
        if ids is not None and c['id'] not in ids:continue
        if c.get('decision')=='registered':continue
        if automatic and c['decision']!='auto':continue
        if automatic and c.get('method')=='exterior_voxel_mesh' and c.get('validation',{}).get('mesh_volume_matches_voxels') is not True:continue
        required=('closed_within_scope','two_cell_clearance','usable_floor','wide_floor_patch','known_boundary_geometry','complete_component','original_connected_component')
        if automatic and (c.get('method') not in METHODS or any(c.get('checks',{}).get(k) is not True for k in required) or c.get('user_modified')):continue
        if c.get('source_block_epoch')!=doc.block_epoch:raise ValueError('후보가 오래되었습니다. 재분석하세요.')
        if c.get('checks',{}).get('complete_component') is not True:raise ValueError('잘린 3D 후보는 등록할 수 없습니다. 범위를 나눠 재분석하세요.')
        if not c['volume_runs']:continue
        if c['fingerprint'] in existing:c['decision']='registered';c['existing_space_id']=existing[c['fingerprint']];continue
        # Exact interval overlap; a large bounding box is never sufficient to overwrite a user space.
        overlap=False
        for y,z,a,b in c['volume_runs']:
            if any(a<=B and b>=A for A,B in rows.get((y,z),())):overlap=True;break
        if overlap:c['decision']='review';c['reasons'].append('existing_space_overlap_protected');continue
        # Revalidate actual cells at the current block epoch before any registration.
        if any(np_solid(doc,y,z,a,b) for y,z,a,b in c['volume_runs']):raise ValueError('후보에 실제 블록이 생겼습니다. 재분석하세요.')
        s=new_space(c['volume_runs'],doc.schematic.size,'3D 공간 '+str(len(spaces)+1),'미지정');s.update(origin='auto' if automatic else 'manual',auto_registered=automatic,recognition={'fingerprint':c['fingerprint'],'method':c.get('method','3d_air_components'),'checks':c['checks'],'mesh':c.get('mesh'),'virtual_boundary':c.get('virtual_boundary')})
        spaces.append(s);added.append(s['id']);
        for y,z,a,b in s['runs']:rows.setdefault((y,z),[]).append((a,b))
        c['decision']='registered';c['confirmed']=True
    new=copy.deepcopy(result);new['candidates']=candidates
    return spaces,new,added

def np_solid(doc,y,z,a,b):return bool(doc.schematic.solid(doc.schematic.blocks[y,z,a:b+1]).any())

def compare(doc,runs):
    import numpy as np
    good=[];bad=[]
    for y,z,a,b in runs:
        values=doc.schematic.solid(doc.schematic.blocks[y,z,a:b+1]);edges=np.r_[0,np.flatnonzero(values[1:]!=values[:-1])+1,len(values)]
        for lo,hi in zip(edges[:-1],edges[1:]):(bad if values[lo] else good).append([y,z,int(a+lo),int(a+hi-1)])
    return {'valid_runs':good,'conflict_runs':bad,'valid_cells':sum(b-a+1 for _,_,a,b in good),'conflict_cells':sum(b-a+1 for _,_,a,b in bad)}
