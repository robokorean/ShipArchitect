"""Untrusted structured plans -> existing bounded/protected Python engine.
This module never executes model code, writes NBT, approves changes or adds palette states.
"""
from __future__ import annotations
from collections import Counter
import copy
import json
import time
import numpy as np
from .chunks import checkpoint,Cancelled
from .model import Bounds,Proposal,decorative
from .schematic import AIR
from .safety import audit, review, require_safe
from .diagnostics import event, save_change_map

MAX_OPS=16
MAX_CHANGED=200_000
MAX_VISITS=16_000_000
OPS=('floor','walls','ceiling','column','add','fill','replace','erase')


class PlanError(ValueError):
    pass


def plan_schema(materials):
    vector={'type':'array','items':{'type':'integer','minimum':0},'minItems':3,'maxItems':3}
    return {'type':'object','additionalProperties':False,'required':['version','summary','operations','notes'],
        'properties':{'version':{'type':'integer','enum':[1]},'summary':{'type':'string','maxLength':1000},
        'notes':{'type':'array','items':{'type':'string','maxLength':500},'maxItems':8},
        'operations':{'type':'array','maxItems':MAX_OPS,'items':{'type':'object','additionalProperties':False,
            'required':['kind','min','max','material','source','overwrite'],
            'properties':{'kind':{'type':'string','enum':list(OPS)},'min':vector,'max':vector,
                'material':{'type':'string','enum':sorted(set(materials)|{'minecraft:air'})},
                'source':{'anyOf':[{'type':'null'},{'type':'string','enum':list(materials)}]},
                'overwrite':{'type':'boolean'}}}}}}


def parse_plan(raw,context):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>65536:raise PlanError('AI 계획 크기 제한 초과')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise PlanError('중복 JSON 키')
            result[k]=v
        return result
    try:plan=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(PlanError('잘못된 JSON 숫자')))
    except (ValueError,RecursionError) as e:raise PlanError(f'JSON 설계 형식 오류: {e}') from e
    if type(plan) is not dict or set(plan)!={'version','summary','operations','notes'}:raise PlanError('허용되지 않은 계획 필드')
    if type(plan['version']) is not int or plan['version']!=1:raise PlanError('계획 버전 오류')
    if type(plan['summary']) is not str or not 1<=len(plan['summary'])<=1000:raise PlanError('계획 요약 오류')
    if type(plan['notes']) is not list or len(plan['notes'])>8 or any(type(n) is not str or len(n)>500 for n in plan['notes']):raise PlanError('계획 설명 오류')
    operations=plan['operations']
    if type(operations) is not list or len(operations)>MAX_OPS:raise PlanError('연산 수 제한 초과')
    palette=set(context['allowed_materials']);size=context['size_xyz'];visits=0
    from .block_registry import BlockRegistry,MaterialError
    registry=BlockRegistry(palette)
    for op in operations:
        if type(op) is not dict or set(op)!={'kind','min','max','material','source','overwrite'}:raise PlanError('허용되지 않은 건축 연산 필드')
        if type(op['kind']) is not str or type(op['material']) is not str or (op['source'] is not None and type(op['source']) is not str):raise PlanError('연산/재료는 문자열이어야 합니다.')
        if op['kind'] not in OPS:raise PlanError('지원하지 않는 연산')
        try:
            op['material']=registry.resolve(op['material'])
            if op['source'] is not None:op['source']=registry.resolve(op['source'])
        except MaterialError as e:raise PlanError(str(e)) from e
        for field in ('min','max'):
            v=op[field]
            if type(v) is not list or len(v)!=3 or any(type(x) is not int for x in v):raise PlanError('좌표는 정수 3개여야 합니다.')
        if any(not 0<=a<=b<n for a,b,n in zip(op['min'],op['max'],size)):raise PlanError('선택 구역을 벗어나거나 뒤집힌 좌표')
        if op['kind']=='column' and (op['min'][0]!=op['max'][0] or op['min'][2]!=op['max'][2]):raise PlanError('기둥은 X/Z 한 칸의 수직 열이어야 합니다.')
        if op['kind']=='column' and op['overwrite']:raise PlanError('기둥은 공기 칸에만 추가합니다.')
        if type(op['overwrite']) is not bool:raise PlanError('overwrite는 true/false여야 합니다.')
        if op['kind']=='erase':
            if op['material']!='minecraft:air' or not op['overwrite']:raise PlanError('삭제는 air/overwrite=true가 필요합니다.')
        elif op['material'] not in palette:raise PlanError('현재 허용 팔레트에 없는 재료')
        if op['kind']=='replace':
            if op['source'] not in palette or not op['overwrite']:raise PlanError('교체할 원본 장식 재료와 overwrite=true가 필요합니다.')
        elif op['source'] is not None:raise PlanError('교체 외 연산의 source는 null이어야 합니다.')
        if op['kind']=='add' and op['overwrite']:raise PlanError('추가 연산은 기존 블록을 교체하지 않습니다.')
        binding=context.get('material_constraints',{}).get(op['kind'])
        if binding and op['material']!=binding:raise PlanError(f"요청 재료 불일치: {op['kind']}는 {binding}, AI 선택은 {op['material']}")
        permitted=context.get('requested_targets',[])
        if permitted and op['kind']!='erase' and op['material'] not in permitted:raise PlanError('요청에 없는 재료 선택: '+op['material'])
        if context.get('intent')=='fill' and op['kind'] not in ('fill','add','replace'):raise PlanError('채우기 요청은 add/fill/replace를 사용해야 합니다. 외곽 벽/천장이나 삭제로 바꾸지 마세요.')
        visits+=int(np.prod([b-a+1 for a,b in zip(op['min'],op['max'])]))
    if visits>MAX_VISITS:raise PlanError('계획 검사 부피 제한 초과. 더 작은 구역을 선택하세요.')
    return plan


def build_context(doc,bounds,allow_existing=False,cancel=None):
    revision=doc.revision
    doc.prepare_protection(cancel=cancel)
    stats=doc.analyze(bounds,cancel)
    sc=doc.schematic;origin=bounds.lo
    size=tuple(b-a+1 for a,b in zip(bounds.lo,bounds.hi))
    all_safe=[state for i,state in sc.palette.items() if sc.is_decorative(state) and state.split('[')[0] not in AIR]
    all_safe.sort(key=lambda s:(-stats['palette'].get(s,0),s))
    materials=all_safe[:128]
    if not materials:raise PlanError('사용 가능한 장식 재료가 없습니다. 수동 재료 추가 후 다시 요청하세요.')
    # 4³ coarse occupancy/protection grid, plus vertical occupancy bins. No NBT or paths.
    edges=[np.unique(np.linspace(0,n,min(n,4)+1,dtype=int)) for n in size]
    grid=[]
    for iy in range(len(edges[1])-1):
        for iz in range(len(edges[2])-1):
            for ix in range(len(edges[0])-1):
                checkpoint(cancel)
                lo=tuple(origin[a]+int(edges[a][i]) for a,i in enumerate((ix,iy,iz)))
                hi=tuple(origin[a]+int(edges[a][i+1]) for a,i in enumerate((ix,iy,iz)))
                x,y,z=lo;X,Y,Z=hi;a=sc.blocks[y:Y,z:Z,x:X]
                protected=sc.protected(a,lo)|doc.locked_mask(lo,a.shape)
                if doc.exterior is not None:protected |= doc.exterior.mask(lo,a.shape)
                grid.append([*[int(v-o) for v,o in zip(lo,origin)],*[int(v-o-1) for v,o in zip(hi,origin)],int(round(100*sc.solid(a).mean())),int(round(100*protected.mean()))])
    layers=[]
    yedges=np.unique(np.linspace(0,size[1],min(size[1],16)+1,dtype=int))
    for lo,hi in zip(yedges[:-1],yedges[1:]):
        checkpoint(cancel)
        a=sc.blocks[origin[1]+lo:origin[1]+hi,origin[2]:bounds.hi[2]+1,origin[0]:bounds.hi[0]+1]
        layers.append([int(lo),int(hi-1),round(float(1-sc.solid(a).mean()),3)])
    zones=[]
    for zone in doc.zones:
        if not zone.get('protected'):continue
        lo=[max(a,b) for a,b in zip(bounds.lo,zone['lo'])];hi=[min(a,b) for a,b in zip(bounds.hi,zone['hi'])]
        if all(a<=b for a,b in zip(lo,hi)):
            zones.append({'min':[a-o for a,o in zip(lo,origin)],'max':[a-o for a,o in zip(hi,origin)]})
    box={'min':[0,0,0],'max':[n-1 for n in size]}
    wall_material='minecraft:white_concrete' if 'minecraft:white_concrete' in materials else materials[0]
    roof_material='minecraft:glass' if 'minecraft:glass' in materials else wall_material
    room_example=[{'kind':kind,**box,'material':roof_material if kind=='ceiling' else wall_material,'source':None,'overwrite':False} for kind in ('floor','walls','ceiling')]
    context={'coordinate_system':'selection-relative XYZ, inclusive integer min/max; Y is up',
        'size_xyz':list(size),'full_region_box':box,'room_shell_example_if_requested':room_example,'volume':bounds.volume,'placed_blocks':stats['solid'],'air_cells':stats['empty'],
        'protected_cells':stats['protected'],'existing_blocks':sorted(stats['palette'].items(),key=lambda p:-p[1])[:24],
        'allowed_materials':materials,'palette_truncated':len(all_safe)>128,
        'coarse_cells_columns':['block_min_x','block_min_y','block_min_z','block_max_x','block_max_y','block_max_z','occupied_percent','protected_percent'],'coarse_cells':grid,
        'layers_y_min_max_air_fraction':layers,'protected_zones':zones[:32],
        'protected_zones_truncated':len(zones)>32,'allow_existing':bool(allow_existing),
        'limits':{'max_operations':MAX_OPS,'max_changed_blocks':MAX_CHANGED,'exterior_override':False},
        'sampling_note':'Coarse cells are approximate. Python validates exact coordinates, collisions and protections.'}
    space=doc.active_space()
    context['space_token']=doc.scope_token()
    if space is not None:
        from .space_model import statistics
        exact=statistics(doc,space,cancel)
        runs=[[y-origin[1],z-origin[2],max(a,bounds.lo[0])-origin[0],min(b,bounds.hi[0])-origin[0]] for y,z,a,b in space['runs'] if bounds.lo[1]<=y<=bounds.hi[1] and bounds.lo[2]<=z<=bounds.hi[2] and a<=bounds.hi[0] and b>=bounds.lo[0]]
        if not runs:raise PlanError('현재 사각 선택과 정확한 공간 셀의 교집합이 0개입니다. 공간을 다시 선택하세요.')
        context['exact_space']={'id':space['id'],'name':space['name'],'purpose':space['purpose'],
            'run_columns':['relative_y','relative_z','relative_x0','relative_x1'],
            'runs':[runs[int(i)] for i in np.unique(np.linspace(0,len(runs)-1,min(512,len(runs)),dtype=int))],'runs_truncated':len(runs)>512,'run_count':len(runs),'cell_count':sum(b-a+1 for y,z,a,b in runs),'space_total_cells':exact['cells'],
            'existing_blocks':exact['blocks'],'valid_cell_example_xyz':[runs[0][2],runs[0][0],runs[0][1]],'floor_definition':'Lowest permitted cells per vertical column; existing physical floor below permission is protected and excluded. Do not edit below runs.', 'note':'BBox is display/search only. Python clips ALL operations to exact cells. Never fill gaps or build an enclosing bbox shell.'}
    if revision!=doc.revision:raise Cancelled('구역 분석 중 설계가 변경되었습니다.')
    if len(json.dumps(context,ensure_ascii=False).encode())>48000:raise PlanError('AI에 전달할 구역 정보가 너무 큽니다. 더 작은 구역을 선택하세요.')
    return context


def compile_plan(doc,bounds,plan,context,revision,cancel=None,trace_id=None):
    if revision!=doc.revision:raise Cancelled('AI 요청 이후 설계가 변경되었습니다.')
    if context.get('space_token')!=doc.scope_token():raise PlanError('공간 선택/경계가 달라졌습니다. 다시 요청하세요.')
    # Revalidate even when a caller passes a parsed dictionary.
    plan=parse_plan(json.dumps(plan,ensure_ascii=False),context)
    doc.prepare_protection(cancel=cancel)
    empty=np.empty(0,dtype=np.int64)
    dummy=Proposal(revision,bounds,'ai_plan',next(iter(doc.schematic.palette)),empty,np.empty(0,np.int32),0,0,space_token=doc.scope_token())
    overlay=doc.preview(dummy)
    shadow=copy.copy(doc);shadow.schematic=copy.copy(doc.schematic)
    # A shallow Schematic must not own/close the document's disk mapping.
    shadow.schematic.storage=None;shadow.schematic.blocks=overlay;shadow.schematic.counts=None
    palette={state:i for i,state in doc.schematic.palette.items()}
    touched=np.empty(0,np.int64)
    protected=occupied=outside=locked=outside_space=0;operation_diagnostics=[]
    try:
        for op in plan['operations']:
            checkpoint(cancel)
            if revision!=doc.revision:raise Cancelled('설계가 변경되어 계획을 폐기합니다.')
            lo=tuple(a+b for a,b in zip(bounds.lo,op['min']));hi=tuple(a+b for a,b in zip(bounds.lo,op['max']))
            region=Bounds(lo,hi);target=palette[op['material']]
            source=palette[op['source']] if op['source'] is not None else None
            try:p=shadow.propose(region,target,'add' if op['kind']=='column' else op['kind'],op['overwrite'],cancel,source,False)
            except ValueError as e:raise PlanError(str(e)) from e
            if not context['allow_existing'] and len(p.indices):
                base=doc.schematic.blocks.reshape(-1)[p.indices]
                if np.any(doc.schematic.solid(base)):raise PlanError('기존 장식 변경/삭제 허용이 꺼져 있습니다. 체크 후 다시 요청하세요.')
            operation_diagnostics.append({'kind':op['kind'],'requested_volume':region.volume,'changed_in_operation':len(p.indices),'excluded':p.reasons})
            touched=np.union1d(touched,p.indices)
            if len(touched)>MAX_CHANGED:raise PlanError('계획 전체 변경 수가 200,000개를 초과합니다.')
            overlay.reshape(-1)[p.indices]=p.target
            protected+=p.protected;occupied+=p.occupied;outside+=p.exterior_blocked;locked+=p.locked_blocked;outside_space+=p.outside_space
        before=doc.schematic.blocks.reshape(-1)[touched].copy();after=overlay.reshape(-1)[touched].copy()
        keep=before!=after;touched=touched[keep];before=before[keep];after=after[keep]
        if revision!=doc.revision:raise Cancelled('설계가 변경되어 계획을 폐기합니다.')
        for a in (touched,before,after):a.setflags(write=False)
        check=audit(touched, size_xyz=doc.schematic.size, bounds_lo=bounds.lo, bounds_hi=bounds.hi, origin='ai',before=before,after=after,palette=doc.schematic.palette,reference_bounds=doc.ship_bounds)
        event('ai_diff_prepared', trace_id=trace_id, critical=True,
              change=check, operation_diagnostics=operation_diagnostics, material_counts={doc.schematic.palette[int(i)]:int(n) for i,n in zip(*np.unique(after,return_counts=True))})
        save_change_map(trace_id,touched,before,after)
        # Whole-schematic envelope patterns are blocked even before user approval.
        if check['hull_risk']:
            event('ai_diff_blocked', trace_id=trace_id, critical=True, change=check)
            # Keep the exact blocked proposal available for the location map; commit still rejects it.
            pass
        target=int(after[0]) if len(after) else palette['minecraft:air']
        return Proposal(revision,bounds,'ai_plan',target,touched,before,protected,occupied,None,outside,0,locked,False,
            after,'ai',plan,trace_id,doc.scope_token(),outside_space,{'operations':operation_diagnostics,'net_reverted':int(len(keep)-int(keep.sum()))})
    finally:
        # overlay references survive only inside generated proposal arrays (copies).
        shadow.schematic.blocks=np.empty((0,0,0),np.int32)


SYSTEM='''You are Ship Architect, a Minecraft Java 1.21.1 architectural planner. Use a short Korean summary (one sentence) and at most 2 short notes. Output ONLY one JSON object matching the schema. No code, tools, shell, NBT, settings, permissions or prose outside JSON.
When exact_space is provided, design for its purpose inside its sparse cell shape only; relative Y,Z,X runs define membership. Treat omitted runs as unknown, never assume bbox gaps are usable. [0,0,0] is often OUTSIDE the shape. Use valid_cell_example_xyz and run coordinates, not a 1-cell origin placeholder. For broad requests cover the intended shape using small explicit operations; never claim applied or completed. Python may report zero changes. Use multiple small operations for L/T shaped spaces. Use full_region_box only as an absolute limit, not an instruction to enclose the whole ship. Design only what is requested. For intent=fill use add/fill over the requested volume, never floor/walls/ceiling. For rooms/interiors divide purpose-specific spaces, keep 2-block-wide / 3-block-high routes, use add for partitions and furniture/trim, erase for door openings and replace for glazing. Carve only safe approved existing decorations or blocks created in the same plan. Do not create a full-ship bounding-box shell. Coarse cells are large analysis boxes with inclusive BLOCK bounds, never a different coordinate system. Do not mistake the number of analysis cells for building size.
Coordinates are RELATIVE to the selected region: [X,Y,Z], Y up, inclusive min/max. Use only allowed_materials verbatim. material_constraints pins each construction role to an EXACT block state. requested_targets, when present, is the permitted destination list. NEVER substitute stone or example materials for a requested material. If you cannot comply return operations:[] with an explanation. Never use world coordinates. Consider size, occupancy, layers, existing blocks and protected zones.
Create a complete plan with at most 16 floor/walls/ceiling/column/add/fill/replace/erase operations. column is ONE vertical line (minX=maxX and minZ=maxZ), overwrite=false. floor=min Y plane, ceiling=max Y plane, walls=four VERTICAL sides over the entire minY..maxY height. For a full room, use the SAME full 3D box min/max for floor, walls AND ceiling, as shown in room_shell_example_if_requested. NEVER flatten the walls box to the floor Y plane. The example is only a hint when a room is requested; obey the actual request and materials. add only fills air; fill fills the box, replacing existing safe decoration only when overwrite=true and allow_existing=true. source is null except replace, which must name one exact existing decorative state. erase uses material minecraft:air. overwrite is false for add; true for replace/erase; floor/walls/ceiling should default false.
Python will preserve all original exterior, mod devices, CC computers, BlockEntities and locked zones. You cannot override protection. When allow_existing=false, change only cells that were initially air: erase/replace may carve blocks created earlier in this same plan, never existing buildings. If a requested material is not allowed or the request is unclear, return operations:[] and explain what is missing in summary/notes. No new palette states.
A revision request must return the COMPLETE revised plan, not a delta. The previous plan was a proposal, not necessarily approved. Do not claim you changed any blocks.''' 


def prepare_material_request(doc,prompt,bindings=None):
    from .block_registry import request_bindings
    if len(prompt)>4000:raise PlanError('한국어 요청은 4,000자 이하여야 합니다.')
    pinned,requested=request_bindings(prompt,doc.registry(),bindings)
    # Validate the entire set first. Only explicitly requested palette entries are added;
    # block data stays unchanged, and material additions are journaled.
    states=[doc.validate_material(s) for s in requested]
    if len(states)>32:raise PlanError('한 요청의 재료는 32종 이하로 나눠 주세요.')
    for state in states:doc.add_material(state)
    return pinned,states


def material_context(doc,bounds,prompt='',bindings=None,previous=None,allow_existing=False,cancel=None):
    pinned,requested=prepare_material_request(doc,prompt,bindings)
    context=build_context(doc,bounds,allow_existing,cancel)
    import re
    context['intent']='fill' if re.search('채워|채우',prompt) and not re.search('방|통로|천장|바닥|벽|창문|문을',prompt) else 'design'
    if doc.space_revision==doc.revision:context['space_candidates']=[{k:v for k,v in c.items() if k not in ('footprint','warnings')} for c in doc.space_candidates if c.get('confirmed') and all(a>=o and b<=e for a,b,o,e in zip(c['lo'],c['hi'],bounds.lo,bounds.hi))][:8]
    # Requested entries cannot disappear behind the 128 entry context cap.
    prioritized=list(dict.fromkeys(requested+context['allowed_materials']))[:128]
    context['allowed_materials']=prioritized
    # Whole-volume fill has no floor/wall role: honor its literal requested
    # material even when old role bindings remain in the UI.
    mixed=bool(re.search('섞|혼합|랜덤',prompt))
    if context['intent']=='fill' and not mixed:
        from .block_registry import prompt_mentions
        literals={state for _,_,state in prompt_mentions(prompt,doc.registry())}
        choice=next(iter(literals)) if len(literals)==1 else requested[0] if len(requested)==1 else None
        if choice:
            for kind in ('add','fill','replace'):pinned[kind]=choice
    context['material_constraints']=pinned
    old=[op['material'] for op in (previous or {}).get('operations',[]) if op['kind']!='erase']
    context['requested_targets']=sorted(set(requested+old)) if requested and not pinned and not mixed else []
    if requested:
        # Examples carry the actual requested materials, never alphabetical fallbacks.
        for op in context['room_shell_example_if_requested']:
            op['material']=pinned.get(op['kind'],requested[0])
    context['material_validation']={state:doc.registry().provenance(state) for state in requested}
    return context


def generate_design(client,model,doc,bounds,prompt,allow_existing=False,previous=None,timeout=180,cancel=None,progress=None,bindings=None,trace_id=None,record=None):
    if type(prompt) is not str or not 1<=len(prompt.strip())<=4000:raise PlanError('한국어 요청은 1~4,000자로 입력하세요.')
    if progress:progress(0,'선택 구역·재료·공간 분석 중')
    context=material_context(doc,bounds,prompt,bindings,previous,allow_existing,cancel)
    revision=doc.revision
    event('ai_context', trace_id=trace_id, selected={'lo':bounds.lo,'hi':bounds.hi}, context=context)
    schema=plan_schema(context['allowed_materials'])
    messages=[{'role':'system','content':SYSTEM+'\nJSON schema: '+json.dumps(schema,ensure_ascii=False)},
        {'role':'user','content':'Current region data:\n'+json.dumps(context,ensure_ascii=False)}]
    if previous:
        messages.append({'role':'assistant','content':json.dumps(previous,ensure_ascii=False)})
    messages.append({'role':'user','content':prompt})
    errors=[]
    deadline=time.monotonic()+timeout
    for attempt in range(2):
        checkpoint(cancel)
        remaining=deadline-time.monotonic()
        if remaining<=0:raise PlanError('AI 처리 시간 초과')
        raw,stats=client.chat(model,messages,schema,remaining,cancel,progress)
        if record:record(response=raw)
        event('ai_raw_response', trace_id=trace_id, critical=True, attempt=attempt+1, raw=raw, stats=stats)
        if revision!=doc.revision:raise Cancelled('AI 응답 대기 중 설계가 변경되었습니다.')
        try:
            plan=parse_plan(raw,context)
            proposal=compile_plan(doc,bounds,plan,context,revision,cancel,trace_id)
            return {'actual':review(doc,proposal),'plan':plan,'proposal':proposal,'context':context,'model':model,'stats':stats,'attempts':attempt+1,'validation_errors':errors,'raw_plan_json':raw}
        except PlanError as e:
            errors.append(str(e))
            event('ai_validation_error', trace_id=trace_id, critical=True, attempt=attempt+1, reason=str(e))
            if attempt:raise PlanError('AI 계획 검증 실패: '+str(e)) from e
            if progress:progress(0,'계획 검증 오류 · AI에 1회 수정 요청 중')
            messages += [{'role':'assistant','content':raw[:65536]},{'role':'user','content':'Python rejected the plan: '+str(e)+'. Return one complete corrected plan. Never relax protections or invent materials.'}]
    raise PlanError('AI 계획 생성 실패')
