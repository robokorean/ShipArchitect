"""UI-independent selection, edit approval, history and portable project model."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import uuid
from pathlib import Path
import zipfile
import numpy as np
from .schematic import Schematic, AIR, MAX_BYTES, atomic_write
from .chunks import regions, slices, count_states, checkpoint, Cancelled
from .safety import audit, require_safe, review
from .diagnostics import event


def decorative(state):
    base = state.split('[')[0]
    if base in AIR:
        return True
    if not base.startswith('minecraft:'):
        return False
    name = base.split(':')[1]
    return name in {'stone', 'cobblestone', 'andesite', 'polished_andesite', 'diorite',
                    'polished_diorite', 'granite', 'polished_granite', 'glass', 'dirt',
                    'grass_block', 'deepslate', 'cobbled_deepslate', 'stone_bricks', 'bricks',
                    'smooth_stone', 'terracotta', 'quartz_block', 'smooth_quartz', 'iron_block'} or name.endswith(
                    ('_concrete', '_wool', '_planks', '_stained_glass', '_terracotta', '_log', '_wood', '_stem', '_hyphae'))


@dataclass(frozen=True)
class Bounds:
    lo: tuple[int, int, int]
    hi: tuple[int, int, int]

    @classmethod
    def between(cls, a, b):
        return cls(tuple(min(x, y) for x, y in zip(a, b)), tuple(max(x, y) for x, y in zip(a, b)))

    def validate(self, size):
        if len(self.lo) != 3 or len(self.hi) != 3 or any(
            not isinstance(v, int) for v in (*self.lo, *self.hi)) or any(
            not 0 <= a <= b < s for a, b, s in zip(self.lo, self.hi, size)):
            raise ValueError('구역 좌표가 프로젝트 범위를 벗어났습니다.')

    @property
    def slices(self):
        x, y, z = self.lo
        X, Y, Z = self.hi
        return slice(y, Y+1), slice(z, Z+1), slice(x, X+1)

    @property
    def volume(self):
        return int(np.prod([b-a+1 for a, b in zip(self.lo, self.hi)]))


@dataclass(frozen=True)
class Proposal:
    revision: int
    bounds: Bounds
    mode: str
    target: int
    indices: np.ndarray
    before: np.ndarray
    protected: int
    occupied: int
    source_id: int | None = None
    exterior_blocked: int = 0
    exterior_affected: int = 0
    locked_blocked: int = 0
    allow_exterior: bool = False
    after: np.ndarray | None = None
    origin: str = 'manual'
    plan: dict | None = None
    trace_id: str | None = None
    space_token: str | None = None
    outside_space: int = 0
    reasons: dict | None = None


class Document:
    def __init__(self, schematic, original=None, source=None):
        self.schematic = schematic
        self.original = original if original is not None else schematic.to_bytes()
        self.source = str(Path(source).resolve()) if source else None
        if 'minecraft:air' not in schematic.palette.values():
            schematic.palette[max(schematic.palette) + 1] = 'minecraft:air'
        self.project_id=hashlib.sha256((str(self.source)+hashlib.sha256(self.original).hexdigest()).encode()).hexdigest()[:32] if source else uuid.uuid4().hex
        self.chat_store=None;self.chat_seed=None;self.chat_error=None;self.space_candidates=[];self.space_revision=None;self.spaces=[];self.active_space_id=None;self.floor_visibility={};self.floor_starts=[];self.view_settings={}
        self.analysis_state={'equipment_rules':{},'space_rules':[],'decisions':{}};self.block_epoch=0;self.spatial_dirty=None
        self.zones = []
        self.history = []
        self.undo_stack = []
        self.redo_stack = []
        self.revision = 0
        self.dirty = False
        self.exterior = None
        self._analysis = {}
        self.last_export = None
        self.ship_bounds=None
        self.integrity_error=None
        self.selection = Bounds((0, 0, 0), tuple(v-1 for v in schematic.size))

    def prepare_protection(self,progress=None,cancel=None):
        if self.source and self.exterior is None:
            from .protection import build_exterior
            baseline=Schematic.from_bytes(self.original,cancel=cancel)
            mins=np.array(baseline.size);maxs=np.zeros(3,dtype=int);found=False
            for lo,hi in regions(baseline.blocks.shape):
                checkpoint(cancel);a=baseline.blocks[slices(lo,hi)];yy,zz,xx=np.nonzero(baseline.solid(a))
                if len(xx):
                    found=True;mins=np.minimum(mins,[xx.min()+lo[0],yy.min()+lo[1],zz.min()+lo[2]]);maxs=np.maximum(maxs,[xx.max()+lo[0],yy.max()+lo[1],zz.max()+lo[2]])
            if found:self.ship_bounds={'lo':mins.tolist(),'hi':maxs.tolist()}
            if baseline.size != self.schematic.size:raise ValueError('원본과 현재 설계 크기가 달라 보호할 수 없습니다.')
            self.exterior=build_exterior(baseline,progress,cancel)
            self._analysis.clear()
        return self.exterior

    def locked_mask(self,lo,shape):
        x,y,z=lo;h,l,w=shape
        mask=np.zeros(shape,dtype=bool)
        for zone in self.zones:
            if not zone.get('protected'):continue
            a,b=zone['lo'],zone['hi']
            X,Y,Z=max(x,a[0]),max(y,a[1]),max(z,a[2])
            xx,yy,zz=min(x+w,b[0]+1),min(y+h,b[1]+1),min(z+l,b[2]+1)
            if X<xx and Y<yy and Z<zz:mask[Y-y:yy-y,Z-z:zz-z,X-x:xx-x]=True
        return mask

    def protection_at(self,xyz):
        reasons=[]
        if self.schematic.protected_at(xyz):reasons.append('장치/BlockEntity/보수적 보호')
        x,y,z=xyz;w,h,l=self.schematic.size
        if self.exterior is not None and self.exterior.at(np.array([(y*l+z)*w+x]))[0]:reasons.append('원본 외피')
        if any(v.get('protected') and all(a<=p<=b for a,p,b in zip(v['lo'],xyz,v['hi'])) for v in self.zones):reasons.append('잠금 구역')
        return reasons

    def analyze(self, bounds, cancel=None):
        bounds.validate(self.schematic.size)
        sc=self.schematic
        key=(self.revision,bounds)
        if key in self._analysis:return self._analysis[key]
        whole = bounds.lo==(0,0,0) and bounds.hi==tuple(v-1 for v in sc.size)
        if whole:
            if sc.counts is None:
                computed=count_states(sc.blocks,cancel)
                if key[0]!=self.revision:raise Cancelled('분석 중 설계가 변경되었습니다.')
                sc.counts=computed
            counts=dict(sc.counts)
            protected=sum(n for i,n in counts.items() if not sc.is_decorative(sc.palette[i]))
            positions,unsafe=sc.entity_positions()
            if unsafe:
                protected=bounds.volume
            else:
                protected += sum(sc.is_decorative(sc.palette[int(sc.blocks[y,z,x])]) for x,y,z in positions)
        else:
            counts=Counter()
            protected=0
            for lo,hi in regions(sc.blocks.shape,bounds):
                checkpoint(cancel)
                a=sc.blocks[slices(lo,hi)]
                ids,ns=np.unique(a,return_counts=True)
                counts.update({int(i):int(n) for i,n in zip(ids,ns)})
                protected += int(sc.protected(a,lo).sum())
        if self.exterior is not None or any(z.get('protected') for z in self.zones):
            protected=0
            for lo,hi in regions(sc.blocks.shape,bounds):
                checkpoint(cancel)
                a=sc.blocks[slices(lo,hi)]
                mask=sc.protected(a,lo)|self.locked_mask(lo,a.shape)
                if self.exterior is not None:mask |= self.exterior.mask(lo,a.shape)
                protected+=int(mask.sum())
        empty=sum(n for i,n in counts.items() if sc.palette[i].split('[')[0] in AIR)
        result={'volume':bounds.volume,'solid':bounds.volume-empty,'empty':empty,
            'protected':protected,'palette':{sc.palette[i]:n for i,n in counts.items()}}
        if len(self._analysis)>16:self._analysis.clear()
        self._analysis[key]=result
        return result

    def active_space(self):
        return next((s for s in self.spaces if s['id']==self.active_space_id),None)

    def scope_token(self):
        from .space_model import token
        if self.active_space_id is not None and self.active_space() is None:raise ValueError('선택 공간이 사라졌습니다. 공간을 다시 선택하세요.')
        return token(self.active_space()) if self.active_space() is not None else None

    def check_space(self,indices,expected):
        from .space_model import contains
        if self.scope_token()!=expected:raise ValueError('공간 선택/경계가 바뀌었습니다. 다시 미리보기하세요.')
        if self.active_space() is not None and not np.all(contains(self.active_space(),indices,self.schematic.size)):
            raise ValueError('정확한 공간 셀 밖의 변경을 차단했습니다. 경계 상자는 건축 허가 범위가 아닙니다.')

    def set_spaces(self,spaces,action='space_edit',active=None):
        from .space_model import validate_space
        new=[validate_space(s,self.schematic.size) for s in spaces]
        if len(new)>512 or len({s['id'] for s in new})!=len(new):raise ValueError('공간 수/ID 오류')
        if sum(len(s['runs']) for s in new)>500_000:raise ValueError('전체 공간 셀 구간은 500,000개 이하로 나눠 관리하세요.')
        old_spaces={s['id']:s for s in self.spaces}
        for s in new:
            if s['id'] in old_spaces and s['runs']!=old_spaces[s['id']]['runs']:s['manual_modified']=True
        before={s['id']:s for s in self.spaces};after={s['id']:s for s in new};old_active=self.active_space_id
        delta={}
        for sid in before.keys()|after.keys():
            a,b=before.get(sid),after.get(sid)
            if a==b:continue
            if a is None or b is None:delta[sid]={'before':a,'after':b,'whole':True}
            else:
                keys={k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}
                delta[sid]={'before':{k:a.get(k) for k in keys},'after':{k:b.get(k) for k in keys},'whole':False,'missing_before':[k for k in keys if k not in a],'missing_after':[k for k in keys if k not in b]}
        order_before=[s['id'] for s in self.spaces]
        delta=json.loads(json.dumps(delta))
        if active is not None and active not in {s['id'] for s in new}:raise ValueError('선택 공간 ID 오류')
        self.spaces=new;self.active_space_id=active
        self.undo_stack.append({'command':'spaces','delta':delta,'order_before':order_before,'order_after':[s['id'] for s in new],
                                'active_before':old_active,'active_after':active})
        self.redo_stack.clear();self.log(action,space_id=active,blocks=0)

    def _space_history(self,record,forward):
        if 'analysis_before' in record:
            expected=record['analysis_before' if forward else 'analysis_after']
            if self.analysis_state!=expected:raise ValueError('분석 규칙이 이력과 다릅니다.')
        if 'zones_before' in record and self.zones!=record['zones_before' if forward else 'zones_after']:raise ValueError('구역이 이력과 다릅니다.')
        from .space_model import validate_space
        previous='before' if forward else 'after';desired='after' if forward else 'before'
        if 'delta' in record:
            if [s['id'] for s in self.spaces]!=record['order_'+previous]:raise ValueError('공간 순서가 이력과 다릅니다.')
            current={s['id']:s for s in self.spaces}
            for sid,change in record['delta'].items():
                old=change[previous];new=change[desired]
                if change['whole']:
                    if current.get(sid)!=old:raise ValueError('공간 정보가 변경 이력과 다릅니다.')
                    if new is None:current.pop(sid,None)
                    else:current[sid]=new
                else:
                    value=dict(current[sid])
                    if any(value.get(k)!=v for k,v in old.items()):raise ValueError('공간 속성이 변경 이력과 다릅니다.')
                    value.update(new)
                    for key in change.get('missing_'+desired,[]):value.pop(key,None)
                    current[sid]=value
            result=[current[sid] for sid in record['order_'+desired]]
        else:
            expected=record['spaces_'+previous]
            if self.spaces!=expected:raise ValueError('공간 정보가 변경 이력과 다릅니다.')
            result=record['spaces_'+desired]
        self.spaces=[validate_space(s,self.schematic.size) for s in result]
        self.active_space_id=record.get('active_after' if forward else 'active_before')
        if self.active_space() is not None:
            from .space_model import bbox
            lo,hi=bbox(self.active_space());self.selection=Bounds(lo,hi)
        if 'analysis_before' in record:
            from .analysis_state import validate
            self.analysis_state=validate(record['analysis_after' if forward else 'analysis_before'],self.schematic.size)
            self.space_candidates=json.loads(json.dumps(self.analysis_state.get('last_result',{}).get('candidates',[])))
            self.space_revision=self.revision+1
        if 'zones_before' in record:self.zones=json.loads(json.dumps(record['zones_after' if forward else 'zones_before']))
        self.log('redo' if forward else 'undo',mode='spaces',blocks=0)

    def propose(self, bounds, target, mode='floor', replace=False, cancel=None, source_id=None, allow_exterior=False):
        bounds.validate(self.schematic.size)
        revision=self.revision
        if mode != 'erase' and (target not in self.schematic.palette or not self.schematic.is_decorative(self.schematic.palette[target])):
            raise ValueError('안전한 일반 장식 블록만 배치할 수 있습니다.')
        if mode!='erase':self.validate_material(self.schematic.palette[target])
        if mode not in {'floor','ceiling','walls','fill','add','replace','erase'}:
            raise ValueError('알 수 없는 편집 모드')
        self.prepare_protection(cancel=cancel)
        if mode!='erase' and self.schematic.palette[target].split('[')[0] in AIR:raise ValueError('공기를 배치하는 대신 삭제 도구를 사용하세요.')
        if source_id is not None and source_id not in self.schematic.palette:raise ValueError('교체 원본 재료가 없습니다.')
        if mode=='erase':
            target=next(i for i,state in self.schematic.palette.items() if state=='minecraft:air')
        pieces=[]
        before=[]
        total=protected=occupied=exterior_blocked=exterior_affected=locked_blocked=outside_space=same_material=occupied_skipped=0
        space=self.active_space();scope_token=self.scope_token()
        indexed_space={'runs':np.asarray(space['runs'],np.int64)} if space is not None else None
        w,h,l=self.schematic.size
        for lo,hi in regions(self.schematic.blocks.shape,bounds):
            checkpoint(cancel)
            x,y,z=lo
            X,Y,Z=hi
            if space is None and mode=='floor' and not y<=bounds.lo[1]<Y: continue
            if space is None and mode=='ceiling' and not y<=bounds.hi[1]<Y: continue
            a=self.schematic.blocks[slices(lo,hi)]
            yy,zz,xx=np.ogrid[y:Y,z:Z,x:X]
            choose=np.ones(a.shape,dtype=bool)
            if mode=='floor' and space is None: choose &= yy==bounds.lo[1]
            elif mode=='ceiling' and space is None: choose &= yy==bounds.hi[1]
            elif mode=='walls' and space is None:
                choose &= ((xx==bounds.lo[0])|(xx==bounds.hi[0])|(zz==bounds.lo[2])|(zz==bounds.hi[2]))
            if space is not None:
                from .space_model import mask
                inside=mask(indexed_space,lo,a.shape)
                if mode=='floor':choose &= (~mask(indexed_space,(x,y-1,z),a.shape)) | (yy==bounds.lo[1])
                elif mode=='ceiling':choose &= (~mask(indexed_space,(x,y+1,z),a.shape)) | (yy==bounds.hi[1])
                elif mode=='walls':
                    edge=~mask(indexed_space,(x-1,y,z),a.shape)|~mask(indexed_space,(x+1,y,z),a.shape)|~mask(indexed_space,(x,y,z-1),a.shape)|~mask(indexed_space,(x,y,z+1),a.shape)
                    choose &= edge | (xx==bounds.lo[0]) | (xx==bounds.hi[0]) | (zz==bounds.lo[2]) | (zz==bounds.hi[2])
                outside_space+=int((choose & ~inside & (a!=target)).sum())
                choose &= inside
            same_material+=int((choose & (a==target)).sum())
            choose &= a != target
            solid=self.schematic.solid(a)
            if mode=='replace':
                choose &= solid
                if source_id is not None:choose &= a==source_id
            protection=self.schematic.protected(a,lo)
            locked=self.locked_mask(lo,a.shape)
            locked_blocked+=int((choose & locked & ~protection).sum())
            protection |= locked
            if self.exterior is not None:
                outside=self.exterior.mask(lo,a.shape)
                affected=choose & outside & ~protection
                if mode=='add' or (mode=='fill' and not replace) or (mode in {'floor','walls','ceiling'} and not replace):affected &= ~solid
                if allow_exterior:exterior_affected+=int(affected.sum())
                else:
                    exterior_blocked+=int(affected.sum())
                    protection |= outside
            protected += int((choose & protection).sum())
            occupied += int((choose & solid & ~protection).sum())
            choose &= ~protection
            if mode=='add' or (mode=='fill' and not replace) or (not replace and mode in {'floor','ceiling','walls'}):
                occupied_skipped+=int((choose & solid).sum());choose &= ~solid
            iy,iz,ix=np.nonzero(choose)
            if len(ix):
                indices=(iy+y)*w*l+(iz+z)*w+ix+x
                pieces.append(indices)
                before.append(a[iy,iz,ix])
                total+=len(ix)
                if total>200_000:
                    raise ValueError('한 번의 변경은 200,000 블록 이하로 나눠 주세요.')
        indices=np.concatenate(pieces) if pieces else np.empty(0,dtype=np.int64)
        values=np.concatenate(before) if before else np.empty(0,dtype=np.int32)
        if revision!=self.revision:raise Cancelled('제안 계산 중 설계가 변경되었습니다.')
        indices.setflags(write=False);values.setflags(write=False)
        return Proposal(revision,bounds,mode,target,indices,values,protected,occupied,source_id,
            exterior_blocked,exterior_affected,locked_blocked,bool(allow_exterior),space_token=scope_token,outside_space=outside_space,reasons={'same_material':same_material,'occupied_not_allowed':occupied_skipped,'outside_space':outside_space,'protected':protected,'exterior':exterior_blocked,'locked':locked_blocked})

    def preview(self, proposal):
        if proposal.revision != self.revision:
            raise ValueError('오래된 미리보기입니다. 다시 생성하세요.')
        self.check_space(proposal.indices,proposal.space_token)
        blocks=self.schematic.blocks
        if isinstance(blocks,np.memmap):
            blocks.flush()
            a=np.memmap(blocks.filename,dtype=blocks.dtype,mode='c',shape=blocks.shape)
        else:
            a=blocks.copy()
        a.ravel()[proposal.indices] = proposal.target if proposal.after is None else proposal.after
        return a

    def _check_indices(self,indices,target,allow_exterior=False):
        sc=self.schematic
        sc._entities_cache=None  # commit checks the current typed NBT, not a stale lookup
        if target not in sc.palette or not sc.is_decorative(sc.palette[target]):raise ValueError('장치/모드 블록은 배치할 수 없습니다.')
        self.validate_material(sc.palette[target])
        ids=sc.blocks.reshape(-1)[indices]
        protected_ids=[i for i,s in sc.palette.items() if not sc.is_decorative(s)]
        if np.any(np.isin(ids,protected_ids)):raise ValueError('장치 보호 조건이 변경되었습니다.')
        positions,unsafe=sc.entity_positions()
        w,h,l=sc.size
        device_indices=np.array([(y*l+z)*w+x for x,y,z in positions],np.int64)
        if unsafe or np.any(np.isin(indices,device_indices)):raise ValueError('BlockEntity 보호 조건이 변경되었습니다.')
        x=indices%w;y=indices//(w*l);z=(indices//w)%l
        for zone in self.zones:
            if not zone.get('protected'):continue
            a,b=zone['lo'],zone['hi']
            if np.any((x>=a[0])&(x<=b[0])&(y>=a[1])&(y<=b[1])&(z>=a[2])&(z<=b[2])):raise ValueError('잠금 구역을 수정할 수 없습니다.')
        if self.exterior is not None and not allow_exterior and np.any(self.exterior.at(indices)):
            raise ValueError('기존 외피 변경에는 해당 제안의 별도 승인이 필요합니다.')

    def approve(self, proposal, approve_exterior=False, approve_mass=False):
        if proposal.revision!=self.revision:raise ValueError('오래된 제안입니다.')
        self.prepare_protection()
        indices=proposal.indices
        self.check_space(indices,proposal.space_token)
        if not len(indices):return None
        if len(indices)>200_000 or len(indices)!=len(proposal.before) or np.any(indices<0) or np.any(indices>=self.schematic.blocks.size):raise ValueError('잘못된 변경 인덱스입니다.')
        if len(np.unique(indices))!=len(indices):raise ValueError('중복 변경 인덱스입니다.')
        w,h,l=self.schematic.size
        x=indices%w;y=indices//(w*l);z=(indices//w)%l
        for values,a,b in zip((x,y,z),proposal.bounds.lo,proposal.bounds.hi):
            if np.any((values<a)|(values>b)):raise ValueError('승인 범위 밖 변경입니다.')
        audit(indices,size_xyz=self.schematic.size,bounds_lo=self.selection.lo,bounds_hi=self.selection.hi,origin='manual')
        check=review(self,proposal)
        event('approval_precheck', trace_id=proposal.trace_id, critical=True,
              scope={'lo':proposal.bounds.lo,'hi':proposal.bounds.hi}, change=check)
        try:
            require_safe(check, approve_mass=approve_mass)
        except ValueError as exc:
            event('approval_blocked', trace_id=proposal.trace_id, critical=True, reason=str(exc), change=check)
            raise
        if proposal.origin=='ai' and (proposal.allow_exterior or approve_exterior):raise ValueError('AI 계획은 원본 외피 보호를 해제할 수 없습니다.')
        allowed=proposal.allow_exterior and approve_exterior
        after=np.full(len(indices),proposal.target,dtype=np.int32) if proposal.after is None else np.asarray(proposal.after)
        if after.shape!=indices.shape or after.dtype.kind not in 'iu':raise ValueError('잘못된 변경 후 블록 배열입니다.')
        for target in np.unique(after):self._check_indices(indices[after==target],int(target),allowed)
        a=self.schematic.blocks.reshape(-1)
        if not np.array_equal(a[indices],proposal.before):raise ValueError('제안 이후 블록이 변경되었습니다.')
        record={'indices':indices.tolist(),'before':proposal.before.tolist(),'target':int(proposal.target),
            'mode':proposal.mode,'bounds':asdict(proposal.bounds),'allow_exterior':bool(allowed),'source_id':proposal.source_id,
            'after':after.tolist() if proposal.after is not None else None,'origin':proposal.origin,'plan':proposal.plan,'trace_id':proposal.trace_id,'space_token':proposal.space_token,'space_id':self.active_space_id}
        self._apply_verified(indices,proposal.before,after)
        check['applied']=True
        self.undo_stack.append(record);self.redo_stack.clear()
        counts=Counter(map(int,after))
        self.log('approve',trace_id=proposal.trace_id,space_id=self.active_space_id,space_token=proposal.space_token,mode=proposal.mode,blocks=len(indices),state=self.schematic.palette[proposal.target] if len(counts)==1 else f'재료 {len(counts)}종',
            origin=proposal.origin,materials={self.schematic.palette[i]:n for i,n in counts.items()},plan=proposal.plan,
            bounds={'lo':list(proposal.bounds.lo),'hi':list(proposal.bounds.hi)},source_id=proposal.source_id,exterior_changed=proposal.exterior_affected,
            protected_skipped=proposal.protected)
        self.chat_update(proposal.trace_id,status='approved',actual=check)
        event('approved', trace_id=proposal.trace_id, critical=True,
              changed=int(len(indices)), change=check, materials={self.schematic.palette[i]:n for i,n in counts.items()})
        return indices

    def chat_update(self,trace_id,**fields):
        if not trace_id or self.chat_store is None:return
        try:
            if fields.get('status') in ('approved','undone','redone'):
                actual=fields.get('actual') or (self.chat_store.get(self.project_id,trace_id) or {}).get('actual',{})
                fields['actual']={**actual,'applied':fields['status']!='undone'}
            self.chat_store.patch(self.project_id,trace_id,**fields)
        except Exception as e:self.chat_error=str(e);event('chat_save_error',error=str(e))

    def log(self, action, **details):
        self._analysis.clear()
        self.last_export=None
        self.history.append({'time': datetime.now(timezone.utc).isoformat(), 'action': action, **details})
        # The project history is separate from durable developer diagnostics.
        event('document_'+action,trace_id=details.get('trace_id'), details={k:v for k,v in details.items() if k!='plan'},
              plan_summary=(details.get('plan') or {}).get('summary') if isinstance(details.get('plan'),dict) else None)
        self.revision += 1
        if action in {'approve','undo','redo'}:
            self.schematic.counts=None
        self.dirty = True

    def _write_blocks(self,array,indices,values):
        array[indices]=values

    def _apply_verified(self,indices,expected,desired):
        if self.integrity_error:raise ValueError('데이터 무결성 오류: 원본/저장 프로젝트를 다시 여세요.')
        array=self.schematic.blocks.reshape(-1)
        if not np.array_equal(array[indices],expected):raise ValueError('변경 전 블록이 일치하지 않습니다.')
        backup=array[indices].copy()
        try:
            self._write_blocks(array,indices,desired)
            if not np.array_equal(array[indices],desired):raise ValueError('적용 후 블록 검증 실패')
            if isinstance(self.schematic.blocks,np.memmap):self.schematic.blocks.flush()
        except Exception:
            try:
                array[indices]=backup
                if isinstance(self.schematic.blocks,np.memmap):self.schematic.blocks.flush()
                if not np.array_equal(array[indices],backup):raise ValueError('복구 검증 실패')
            except Exception as recovery:
                self.integrity_error=str(recovery)
            event('change_rolled_back',critical=True,blocks=len(indices),integrity_error=self.integrity_error)
            raise
        if len(indices):
            w,h,l=self.schematic.size;ii=np.asarray(indices,np.int64);xyz=np.stack([ii%w,ii//(w*l),ii//w%l],axis=1);dirty=Bounds(tuple(map(int,xyz.min(axis=0))),tuple(map(int,xyz.max(axis=0))))
            old=self.spatial_dirty
            self.spatial_dirty=Bounds(tuple(min(a,b) for a,b in zip(old.lo,dirty.lo)),tuple(max(a,b) for a,b in zip(old.hi,dirty.hi))) if old else dirty
            self.block_epoch+=1


    def _zone_history(self,record,forward):
        expected=record['zones_before'] if forward else record['zones_after']
        if self.zones!=expected:raise ValueError('보호 구역이 이력과 달라 중단했습니다.')
        self.zones=json.loads(json.dumps(record['zones_after'] if forward else record['zones_before']))
        if 'spaces_before' in record:
            self.spaces=json.loads(json.dumps(record['spaces_after'] if forward else record['spaces_before']))
            self.active_space_id=record.get('active_after' if forward else 'active_before')
        self.log('redo' if forward else 'undo',mode='zone',blocks=0)

    def undo(self):
        if self.undo_stack and self.undo_stack[-1].get('command')=='spaces':
            record=self.undo_stack[-1];self._space_history(record,False)
            self.undo_stack.pop();self.redo_stack.append(record);return np.empty(0,np.int64)
        if self.undo_stack and self.undo_stack[-1].get('command')=='zone':
            record=self.undo_stack[-1];self._zone_history(record,False)
            self.undo_stack.pop();self.redo_stack.append(record);return np.empty(0,np.int64)
        if self.undo_stack:
            self.prepare_protection()
            record=self.undo_stack[-1]
            indices=np.asarray(record['indices'],np.int64)
            self._check_indices(indices,record['target'],record.get('allow_exterior',False))
            expected=record.get('after')
            if expected is None:expected=np.full(len(indices),record['target'],np.int32)
            if not np.array_equal(self.schematic.blocks.reshape(-1)[indices],expected):raise ValueError('블록이 이력과 달라 실행 취소를 중단했습니다.')
            if any(not self.schematic.is_decorative(self.schematic.palette[i]) for i in set(record['before'])):raise ValueError('보호 장치를 포함한 잘못된 이력입니다.')
            self._apply_verified(indices,expected,record['before'])
            self.undo_stack.pop()
            self.redo_stack.append(record)
            self.log('undo',trace_id=record.get('trace_id'), blocks=len(record['indices']),mode=record['mode'])
            self.chat_update(record.get('trace_id'),status='undone')
            return np.asarray(record['indices'],dtype=np.int64)

    def redo(self):
        if self.redo_stack and self.redo_stack[-1].get('command')=='spaces':
            record=self.redo_stack[-1];self._space_history(record,True)
            self.redo_stack.pop();self.undo_stack.append(record);return np.empty(0,np.int64)
        if self.redo_stack and self.redo_stack[-1].get('command')=='zone':
            record=self.redo_stack[-1];self._zone_history(record,True)
            self.redo_stack.pop();self.undo_stack.append(record);return np.empty(0,np.int64)
        self.prepare_protection()
        if self.redo_stack:
            record = self.redo_stack[-1]
            indices=np.asarray(record['indices'],np.int64)
            if record.get('space_token'):
                from .space_model import token,contains
                sp=next((s for s in self.spaces if s['id']==record.get('space_id')),None)
                if sp is None or token(sp)!=record['space_token'] or not np.all(contains(sp,indices,self.schematic.size)):raise ValueError('공간 경계가 바뀌어 다시 실행할 수 없습니다.')
            after=record.get('after')
            if after is None:after=np.full(len(indices),record['target'],np.int32)
            after=np.asarray(after,dtype=np.int32)
            for target in np.unique(after):self._check_indices(indices[after==target],int(target),record.get('allow_exterior',False))
            if not np.array_equal(self.schematic.blocks.reshape(-1)[indices],record['before']):raise ValueError('블록이 이력과 달라 다시 실행을 중단했습니다.')
            check=audit(indices,size_xyz=self.schematic.size,bounds_lo=record['bounds']['lo'],bounds_hi=record['bounds']['hi'],origin=record.get('origin','manual'),before=record['before'],after=after,palette=self.schematic.palette,reference_bounds=self.ship_bounds)
            require_safe(check,approve_mass=True)
            self._apply_verified(indices,record['before'],after)
            self.redo_stack.pop()
            self.undo_stack.append(record)
            self.log('redo',trace_id=record.get('trace_id'), blocks=len(record['indices']),mode=record['mode'])
            self.chat_update(record.get('trace_id'),status='redone')
            return np.asarray(record['indices'],dtype=np.int64)

    def registry(self):
        from .block_registry import BlockRegistry
        return BlockRegistry(self.schematic.palette,self.schematic.mod_registry)

    def validate_material(self,state):
        state=self.registry().resolve(state)
        if not decorative(state) and not self.registry().safe_mod(state):
            raise ValueError('배치 미지원/장치 보호 재료: '+state+' · 모드 장식은 검증된 목록과 안전 분류가 필요합니다.')
        return state

    def add_material(self,state):
        state=self.validate_material(state)
        if self.registry().safe_mod(state):self.schematic.safe_mod_states.add(state)
        for i,name in self.schematic.palette.items():
            if name==state:return i
        i=next(i for i in range(len(self.schematic.palette)+1) if i not in self.schematic.palette)
        self.schematic.palette[i]=state
        self.log('material_add',state=state)
        return i

    def import_mod_registry(self,data):
        from .block_registry import validate_mod_registry
        mods=validate_mod_registry(data)
        # Device / NBT protection always applies regardless of this classification.
        self.schematic.mod_registry=mods
        self.schematic.safe_mod_states={s for s in self.schematic.palette.values() if self.registry().safe_mod(s)}
        self.log('registry_import',blocks=len(mods))

    def add_zone(self, name, bounds, protected=False):
        bounds.validate(self.schematic.size)
        name = name.strip()
        if not name or any(z['name'] == name for z in self.zones):
            raise ValueError('비어 있지 않은 고유 구역 이름을 입력하세요.')
        before=json.loads(json.dumps(self.zones))
        self.zones.append({'name': name, 'lo': list(bounds.lo), 'hi': list(bounds.hi)})
        if protected:self.zones[-1]['protected']=True
        self.undo_stack.append({'command':'zone','zones_before':before,'zones_after':json.loads(json.dumps(self.zones))})
        self.redo_stack.clear()
        self.log('zone_add', name=name,protected=protected)

    def delete_zone(self,index):
        before=json.loads(json.dumps(self.zones));zone=self.zones.pop(index)
        self.undo_stack.append({'command':'zone','zones_before':before,'zones_after':json.loads(json.dumps(self.zones))})
        self.redo_stack.clear();self.log('zone_delete',name=zone['name'])

    def _check_destination(self, path):
        if self.source and Path(path).resolve() == Path(self.source).resolve():
            raise ValueError('원본 경로에는 저장할 수 없습니다. 다른 이름을 사용하세요.')
        if self.source and Path(path).exists() and Path(self.source).exists():
            if Path(path).samefile(self.source):
                raise ValueError('원본과 같은 파일입니다.')

    def save_project(self, path, cancel=None):
        if self.integrity_error:raise ValueError('복구 실패 데이터는 저장할 수 없습니다.')
        self._check_destination(path)
        payload = {'format': 'ship-architect', 'version': 7, 'project_id':self.project_id, 'source': self.source,
            'original_sha256': hashlib.sha256(self.original).hexdigest(),
            'mod_registry':self.schematic.mod_registry,'analysis_state':self.analysis_state,'block_epoch':self.block_epoch,'spatial_dirty':asdict(self.spatial_dirty) if self.spatial_dirty else None,
            'spaces':self.spaces,'active_space_id':self.active_space_id,'floor_visibility':self.floor_visibility,'floor_starts':self.floor_starts,'view_settings':self.view_settings,'zones': self.zones, 'selection': asdict(self.selection), 'history': self.history,
            'undo': self.undo_stack, 'redo': self.redo_stack, 'revision': self.revision}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
            if self.chat_store is not None:
                try:z.writestr('conversation.json',json.dumps(self.chat_store.bundle(self.project_id),ensure_ascii=False))
                except Exception as exc:
                    self.chat_error='설계는 저장되지만 대화 내장은 실패했습니다. 로컬 DB 기록을 확인하세요: '+str(exc)
                    z.writestr('conversation_warning.json',json.dumps({'project_id':self.project_id,'warning':self.chat_error},ensure_ascii=False))
                    event('chat_snapshot_error',error=str(exc),project_id=self.project_id)
            z.writestr('project.json', json.dumps(payload, ensure_ascii=False))
            z.writestr('original.schem', self.original)
            z.writestr('current.schem', self.schematic.to_bytes(cancel))
        path = Path(path)
        self._check_destination(path.with_suffix(path.suffix + '.bak'))
        if path.exists():
            atomic_write(path.with_suffix(path.suffix + '.bak'), path.read_bytes())
        checkpoint(cancel)
        atomic_write(path, stream.getvalue())
        self.dirty = False

    @classmethod
    def open_project(cls, path, progress=None, cancel=None):
        with zipfile.ZipFile(path) as z:
            if any(i.file_size > MAX_BYTES for i in z.infolist()) or sum(i.file_size for i in z.infolist()) > 3*MAX_BYTES:
                raise ValueError('프로젝트 크기 제한을 초과했습니다.')
            p = json.loads(z.read('project.json'))
            if p.get('format') != 'ship-architect' or p.get('version') not in (2,3,4,5,6,7):
                raise ValueError('지원하지 않는 프로젝트 버전입니다. v0.1 JSON은 원본 .schem을 다시 여세요.')
            original = z.read('original.schem')
            if hashlib.sha256(original).hexdigest() != p['original_sha256']:
                raise ValueError('원본 백업 해시가 일치하지 않습니다.')
            d = cls(Schematic.from_bytes(z.read('current.schem'),progress,cancel), original, p.get('source'))
            d.project_id=p.get('project_id',d.project_id)
            d.from_project=True
            if 'conversation_warning.json' in z.namelist():d.chat_error=json.loads(z.read('conversation_warning.json')).get('warning')
            if 'conversation.json' in z.namelist():
                if z.getinfo('conversation.json').file_size>64_000_000:raise ValueError('대화 포함 크기 제한 초과')
                d.chat_seed=json.loads(z.read('conversation.json'))
        if p.get('mod_registry'):
            from .block_registry import validate_mod_registry
            d.schematic.mod_registry=validate_mod_registry({'minecraft_version':'1.21.1','blocks':p['mod_registry']})
            d.schematic.safe_mod_states={s for s in d.schematic.palette.values() if d.registry().safe_mod(s)}
        from .space_model import validate_space
        d.spaces=[validate_space(s,d.schematic.size) for s in p.get('spaces',[])]
        if len(d.spaces)>512 or sum(len(s['runs']) for s in d.spaces)>500_000 or len({s['id'] for s in d.spaces})!=len(d.spaces):raise ValueError('공간 ID/수 오류')
        d.active_space_id=p.get('active_space_id');d.scope_token();d.floor_visibility=p.get('floor_visibility',{});d.floor_starts=p.get('floor_starts',[])
        if not isinstance(d.floor_starts,list) or any(type(y) is not int or not 0<=y<d.schematic.size[1] for y in d.floor_starts):raise ValueError('사용자 층 기준 오류')
        if not isinstance(d.floor_visibility,dict) or any(not str(k).isdigit() or not 0<=int(k)<d.schematic.size[1] or type(v) is not bool for k,v in d.floor_visibility.items()):raise ValueError('층 표시 설정 오류')
        from .analysis_state import validate as validate_analysis
        d.analysis_state=validate_analysis(p.get('analysis_state',{}),d.schematic.size);d.block_epoch=int(p.get('block_epoch',0));dirty=p.get('spatial_dirty')
        if dirty:
            d.spatial_dirty=Bounds(tuple(dirty['lo']),tuple(dirty['hi']));d.spatial_dirty.validate(d.schematic.size)
        d.space_candidates=d.analysis_state.get('last_result',{}).get('candidates',[]);d.space_revision=d.revision
        d.view_settings=p.get('view_settings',{})
        if not isinstance(d.view_settings,dict) or len(json.dumps(d.view_settings))>2_000_000:raise ValueError('표시 설정 크기/형식 오류')
        d.zones = p['zones']
        for zone in d.zones:
            Bounds(tuple(zone['lo']), tuple(zone['hi'])).validate(d.schematic.size)
        d.selection = Bounds(tuple(p['selection']['lo']), tuple(p['selection']['hi']))
        d.selection.validate(d.schematic.size)
        d.history, d.undo_stack, d.redo_stack = p['history'], p['undo'], p['redo']
        d.revision = int(p['revision']);d.space_revision=d.revision if d.space_candidates else None
        if p.get('version',0)<6 and not any(r.get('command')=='zone' for r in d.undo_stack):
            last=max((i for i,e in enumerate(d.history) if e['action'] in ('approve','undo','redo')),default=-1)
            tail=d.history[last+1:]
            # Old format has no deleted-zone geometry: never invent missing data.
            if not any(e['action']=='zone_delete' for e in tail):
                working=json.loads(json.dumps(d.zones));recovered=[]
                for e in reversed(tail):
                    if e['action']!='zone_add':continue
                    matches=[z for z in working if z['name']==e.get('name')]
                    if len(matches)!=1:continue
                    before=[z for z in working if z['name']!=e['name']]
                    recovered.append({'command':'zone','zones_before':before,'zones_after':working})
                    working=before
                if recovered:d.undo_stack.extend(reversed(recovered));d.redo_stack.clear()
        flat = d.schematic.blocks.ravel()
        for record in d.undo_stack + d.redo_stack:
            if record.get('command')=='spaces':
                if 'analysis_before' in record:
                    for key in ('analysis_before','analysis_after'):validate_analysis(record[key],d.schematic.size)
                if 'delta' in record:
                    for sid,change in record['delta'].items():
                        if change['whole']:
                            for key in ('before','after'):
                                if change[key] is not None:validate_space(change[key],d.schematic.size)
                        else:
                            for key in ('before','after'):
                                if 'runs' in change[key]:
                                    from .space_model import normalize
                                    normalize(change[key]['runs'],d.schematic.size)
                else:
                    for key in ('spaces_before','spaces_after'):
                        for sp in record[key]:validate_space(sp,d.schematic.size)
                continue
            if record.get('command')=='zone':
                for key in ('spaces_before','spaces_after'):
                    for sp in record.get(key,[]):validate_space(sp,d.schematic.size)
                for zones in (record['zones_before'],record['zones_after']):
                    for zone in zones:Bounds(tuple(zone['lo']),tuple(zone['hi'])).validate(d.schematic.size)
                continue
            indices, before = record['indices'], record['before']
            if len(indices) != len(before) or any(not isinstance(i, int) or not 0 <= i < len(flat) for i in indices):
                raise ValueError('변경 이력 인덱스 오류')
            after=record.get('after')
            if after is not None and (len(after)!=len(indices) or any(type(i) is not int or i not in d.schematic.palette for i in after)):raise ValueError('변경 후 이력 팔레트 오류')
            if len(indices)>200_000:raise ValueError('변경 이력 크기 제한 초과')
            if record['target'] not in d.schematic.palette or any(i not in d.schematic.palette for i in before):
                raise ValueError('변경 이력 팔레트 오류')
        return d

    def export(self, path, approved=False, cancel=None):
        if self.integrity_error:raise ValueError('복구 실패 데이터는 출력할 수 없습니다.')
        if not approved:
            raise ValueError('사용자 내보내기 승인이 필요합니다.')
        self._check_destination(path)
        data = self.schematic.to_bytes(cancel)
        # Validate our output before committing it.
        check = Schematic.from_bytes(data,cancel=cancel)
        from .chunks import BATCH
        a,b=check.blocks.reshape(-1),self.schematic.blocks.reshape(-1)
        for start in range(0,len(a),BATCH):
            checkpoint(cancel)
            if not np.array_equal(a[start:start+BATCH],b[start:start+BATCH]):
                raise ValueError('내보내기 검증 실패')
        if check.metadata_digest()!=self.schematic.metadata_digest():raise ValueError('NBT/팔레트 검증 실패')
        self._check_destination(str(path) + '.bak')
        if Path(path).exists():
            atomic_write(str(path) + '.bak', Path(path).read_bytes())
        checkpoint(cancel)
        atomic_write(path, data)
        from .schematic import load_file
        reread,stored=load_file(path,cancel=cancel)
        if hashlib.sha256(stored).digest()!=hashlib.sha256(data).digest():raise ValueError('저장 파일 해시 검증 실패')
        if reread.metadata_digest()!=self.schematic.metadata_digest():raise ValueError('저장 파일 NBT 검증 실패')
        self.last_export={'path':str(path),'sha256':hashlib.sha256(stored).hexdigest(),'blocks':int(self.schematic.blocks.size),'nbt_verified':True}
        event('schem_export_verified',verified=True,file_name=Path(path).name,
              sha256=self.last_export['sha256'],blocks=self.last_export['blocks'])
        return self.last_export
        return self.last_export
