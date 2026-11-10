"""Conservative two-cell walking model, independent from AI build permissions.
Only known cubic support, validated open doors and straight bottom stairs can certify
model connectivity. Mod collision shapes and elevator operation remain uncertain.
"""
from collections import Counter,deque
import numpy as np
from numba import njit
from .model import Bounds,decorative
from .schematic import AIR
from .space_model import bbox
from .chunks import checkpoint
DIRECTIONS=((1,0),(-1,0),(0,1),(0,-1))
FACING={'east':1,'west':2,'south':3,'north':4}
MAX_VOLUME=8_000_000

def state_parts(state):
    name=state.split('[')[0];props={}
    if '[' in state:
        for part in state.split('[',1)[1].rstrip(']').split(','):
            if '=' in part:k,v=part.split('=',1);props[k]=v
    return name,props

def movement_palette(sc):
    from .block_registry import valid_vanilla_state
    n=max(sc.palette)+1;passage=np.zeros(n,np.uint8);potential=np.zeros(n,np.uint8);support=np.zeros(n,np.uint8);stairs=np.zeros(n,np.uint8);unknown=np.zeros(n,np.uint8);reasons=Counter()
    for i,state in sc.palette.items():
        name,p=state_parts(state);vanilla=name.startswith('minecraft:') and valid_vanilla_state(state)
        if name in AIR:passage[i]=potential[i]=3
        elif vanilla and name.endswith('_door'):
            axis=1 if p.get('facing') in ('east','west') else 2 if p.get('facing') in ('north','south') else 0
            potential[i]=axis or 3
            if p.get('open')=='true' and axis:passage[i]=axis;reasons['open_door_states']+=1
            elif p.get('open')=='false':reasons['closed_door_states']+=1
            else:unknown[i]=1
        elif vanilla and name.endswith('_stairs'):
            if p.get('half')=='bottom' and p.get('shape')=='straight' and p.get('waterlogged','false')=='false' and p.get('facing') in FACING:
                stairs[i]=FACING[p['facing']];support[i]=1
            else:unknown[i]=1
        elif vanilla and (decorative(state) or name in ('minecraft:grass_block','minecraft:dirt','minecraft:bedrock','minecraft:deepslate','minecraft:cobbled_deepslate','minecraft:coarse_dirt','minecraft:rooted_dirt','minecraft:netherrack','minecraft:end_stone')):
            # Log / wood and simple full cube decorations in the existing allow-list.
            support[i]=1
        else:
            unknown[i]=1
            if name.endswith(('_door','_trapdoor')):potential[i]=3
    return passage,potential,support,stairs,unknown,dict(reasons)

@njit(cache=True)
def can_step(y,z,x,Y,Z,X,bits,stairs):
    H,L,W=bits.shape
    if not(0<=Y<H-1 and 0<=Z<L and 0<=X<W):return False
    dx=X-x;dz=Z-z;dy=Y-y
    axis=1 if dx else 2
    if (bits[y,z,x]&axis)==0 or (bits[y+1,z,x]&axis)==0 or (bits[Y,Z,X]&axis)==0 or (bits[Y+1,Z,X]&axis)==0:return False
    if dy==0:return True
    # No jumping, flying or inferred lift movement. One-level rises need oriented stairs.
    if dy>0:
        direction=1 if dx==1 else 2 if dx==-1 else 3 if dz==1 else 4
        if not(stairs[y,z,x]==direction or stairs[Y,Z,X]==direction):return False
        return y+2<H and (bits[y+2,z,x]&axis)!=0
    direction=2 if dx==1 else 1 if dx==-1 else 4 if dz==1 else 3
    if not(stairs[Y,Z,X]==direction or stairs[y,z,x]==direction):return False
    return Y+2<H and (bits[Y+2,Z,X]&axis)!=0

@njit(cache=True)
def label_walk(walk,bits,stairs):
    H,L,W=walk.shape;labels=np.zeros(walk.shape,np.int32);queue=np.empty(walk.size,np.int32);label=0
    for y in range(H-1):
        for z in range(L):
            for x in range(W):
                if not walk[y,z,x] or labels[y,z,x]:continue
                label+=1;head=0;tail=1;queue[0]=(y*L+z)*W+x;labels[y,z,x]=label
                while head<tail:
                    idx=queue[head];head+=1;yy=idx//(L*W);zz=idx//W%L;xx=idx%W
                    for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
                        for dy in (0,1,-1):
                            Y=yy+dy;Z=zz+dz;X=xx+dx
                            if 0<=Y<H-1 and 0<=Z<L and 0<=X<W and walk[Y,Z,X] and not labels[Y,Z,X] and can_step(yy,zz,xx,Y,Z,X,bits,stairs):
                                labels[Y,Z,X]=label;queue[tail]=(Y*L+Z)*W+X;tail+=1
    return labels,label

@njit(cache=True)
def trace_route(walk,bits,stairs,start,goal,budget):
    H,L,W=walk.shape;parent=np.full(walk.size,-2,np.int32);queue=np.empty(min(walk.size,budget),np.int32)
    queue[0]=start;parent[start]=-1;head=0;tail=1;found=-1
    while head<tail:
        idx=queue[head];head+=1
        if idx==goal:found=idx;break
        y=idx//(L*W);z=idx//W%L;x=idx%W
        for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
            for dy in (0,1,-1):
                Y=y+dy;Z=z+dz;X=x+dx
                if 0<=Y<H-1 and 0<=Z<L and 0<=X<W and walk[Y,Z,X] and can_step(y,z,x,Y,Z,X,bits,stairs):
                    j=(Y*L+Z)*W+X
                    if parent[j]==-2:
                        if tail>=len(queue):return np.empty(0,np.int32),True
                        parent[j]=idx;queue[tail]=j;tail+=1
    if found<0:return np.empty(0,np.int32),False
    count=1;p=found
    while parent[p]>=0:p=parent[p];count+=1
    out=np.empty(count,np.int32);p=found
    for i in range(count-1,-1,-1):out[i]=p;p=parent[p]
    return out,False

def space_labels(space,labels,origin):
    x0,y0,z0=origin;H,L,W=labels.shape;counts=Counter();representatives={}
    for y,z,a,b in space['runs']:
        Y=y-y0;Z=z-z0;lo=max(0,a-x0);hi=min(W,b-x0+1)
        if not (0<=Y<H and 0<=Z<L and lo<hi):continue
        row=labels[Y,Z,lo:hi];ids,ns=np.unique(row,return_counts=True)
        for label,n in zip(ids,ns):
            if label:
                label=int(label);counts[label]+=int(n)
                if label not in representatives:representatives[label]=(Y*L+Z)*W+lo+int(np.flatnonzero(row==label)[0])
    return dict(counts),representatives

def overlap_cells(a,b):
    rows={}
    for y,z,x,X in a['runs']:rows.setdefault((y,z),[]).append((x,X))
    count=0
    for y,z,x,X in b['runs']:
        for lo,hi in rows.get((y,z),[]):count+=max(0,min(X,hi)-max(x,lo)+1)
    return count

class Navigation:
    def __init__(self,doc,bounds=None,progress=None,cancel=None):
        self.revision=doc.revision;self.spaces=doc.spaces;self.size=doc.schematic.size
        w,h,l=self.size;bounds=bounds or Bounds((0,0,0),(w-1,h-1,l-1));bounds.validate(self.size)
        if bounds.volume>MAX_VOLUME:raise ValueError('통행 분석은 8백만 셀 이하로 범위를 나누세요. 현재 선택 범위 분석을 사용하세요.')
        self.bounds=bounds;self.origin=bounds.lo;sc=doc.schematic;x,y,z=bounds.lo;X,Y,Z=bounds.hi
        # Include floor and head halos; endpoints remain limited to requested analysis bounds.
        sy=max(0,y-1);ey=min(h,Y+2);self.origin=(x,sy,z)
        a=sc.blocks[sy:ey,z:Z+1,x:X+1];H,L,W=a.shape
        if a.size>MAX_VOLUME+2*262144:raise ValueError('통행 분석 halo 크기 초과')
        checkpoint(cancel)
        passage,potential,support,stair,unknown,self.observations=movement_palette(sc)
        self.bits=passage[a];self.potential_bits=potential[a];self.stairs=np.zeros(a.shape,np.uint8);self.stairs[1:]=stair[a[:-1]]
        # A half-door must agree with its upper/lower half. Missing/malformed halves are uncertain.
        for i,state in sc.palette.items():
            name,p=state_parts(state)
            if name.startswith('minecraft:') and name.endswith('_door') and passage[i]:
                positions=np.argwhere(a==i)
                for yy,zz,xx in positions:
                    other_y=int(yy)+(1 if p.get('half')=='lower' else -1)
                    valid=False
                    if 0<=other_y<H:
                        other_name,other=state_parts(sc.palette[int(a[other_y,zz,xx])])
                        valid=other_name==name and other.get('half')!=p.get('half') and all(other.get(k)==p.get(k) for k in ('facing','open','hinge'))
                    if not valid:self.bits[yy,zz,xx]=0
        self.walk=np.zeros(a.shape,bool);self.potential_walk=np.zeros(a.shape,bool)
        if H>=3:
            self.walk[1:-1]=(self.bits[1:-1]!=0)&(self.bits[2:]!=0)&(support[a[:-2]]!=0)
            self.potential_walk[1:-1]=(self.potential_bits[1:-1]!=0)&(self.potential_bits[2:]!=0)&((support[a[:-2]]|unknown[a[:-2]])!=0)
        for array in (self.walk,self.potential_walk):
            array[:max(0,y-sy)]=False;array[min(H,Y-sy+1):]=False
        if progress:progress(20,'2칸 높이·바닥·문·계단 방향 검사')
        checkpoint(cancel);self.labels,self.components=label_walk(self.walk,self.bits,self.stairs)
        if progress:progress(55,'확실한 통행과 불확실 후보 분리')
        checkpoint(cancel);self.possible_labels,_=label_walk(self.potential_walk,self.potential_bits,self.stairs)
        closed_ids=[i for i,state in sc.palette.items() if state_parts(state)[0].startswith('minecraft:') and state_parts(state)[0].endswith('_door') and state_parts(state)[1].get('open')=='false']
        closed=np.isin(a,closed_ids);uncertain_nodes=np.zeros(a.shape,bool);closed_nodes=np.zeros(a.shape,bool)
        if H>=3:
            uncertain_nodes[1:-1]=self.potential_walk[1:-1] & ((unknown[a[:-2]]!=0) | ((self.bits[1:-1]==0)&(self.potential_bits[1:-1]!=0)&~closed[1:-1]) | ((self.bits[2:]==0)&(self.potential_bits[2:]!=0)&~closed[2:]))
            closed_nodes[1:-1]=self.potential_walk[1:-1] & (closed[1:-1]|closed[2:])
        self.uncertain_components=set(map(int,np.unique(self.possible_labels[uncertain_nodes])))-{0}
        self.closed_components=set(map(int,np.unique(self.possible_labels[closed_nodes])))-{0}
        self.members={};self.possible={};self.points={};self.coverage={}
        for s in self.spaces:
            checkpoint(cancel);counts,points=space_labels(s,self.labels,self.origin);self.members[s['id']]=set(counts);self.points[s['id']]=points
            possible,_=space_labels(s,self.possible_labels,self.origin);self.possible[s['id']]=set(possible)
            low,high=bbox(s);self.coverage[s['id']]=all(a<=c and d<=b for a,b,c,d in zip(bounds.lo,bounds.hi,low,high))
        self.observations.update(walkable_cells=int(self.walk.sum()),possible_cells=int(self.potential_walk.sum()),components=self.components,unknown_collision_blocks=int(unknown[a].sum()))
        if progress:progress(100,'연결 분석 완료 · 실제 장치 작동은 미판정')
        checkpoint(cancel)
        if doc.revision!=self.revision:raise ValueError('분석 중 프로젝트가 변경되었습니다.')

    def pair(self,left,right):
        a=next(s for s in self.spaces if s['id']==left);b=next(s for s in self.spaces if s['id']==right)
        overlap=overlap_cells(a,b);common=self.members[left]&self.members[right]
        if overlap:return dict(status='overlap',reason='공간 셀이 겹칩니다. 이동 연결과 구분합니다.',overlap_cells=overlap,components=sorted(common))
        if common:return dict(status='connected',reason='알려진 충돌 모양·문 상태·계단 방향의 2칸 통행 모델에서 경로 확인',components=sorted(common))
        if not self.coverage[left] or not self.coverage[right]:return dict(status='uncertain',reason='분석 범위에 공간 전체가 포함되지 않습니다.')
        possible=self.possible[left]&self.possible[right]
        if possible:
            if not possible&self.uncertain_components and possible&self.closed_components:return dict(status='blocked',reason='닫힌 문이 경로를 차단합니다. 현재 블록 상태에서는 통과할 수 없습니다.')
            return dict(status='uncertain',reason='닫힌/불완전한 문 또는 미확인 바닥·충돌 모양을 지나야 하는 후보 경로입니다.')
        if not self.members[left] or not self.members[right]:return dict(status='uncertain',reason='공간 안에 확인된 2칸 높이와 바닥을 갖춘 출발/도착 셀이 없습니다.')
        return dict(status='disconnected',reason='이 분석 범위의 통행 모델에서 경로 없음: 벽·낙차·계단 방향·머리 공간을 확인하세요.')

    def route(self,left,right,cancel=None):
        pair=self.pair(left,right)
        if pair['status']!='connected':return dict(**pair,path=[])
        component=pair['components'][0];start=self.points[left][component];goal=self.points[right][component]
        checkpoint(cancel);indices,limited=trace_route(self.walk,self.bits,self.stairs,start,goal,2_000_000);checkpoint(cancel)
        if limited:return dict(status='uncertain',reason='경로 탐색 2백만 노드 예산 초과. 더 작은 범위로 분석하세요.',path=[])
        H,L,W=self.walk.shape;x,y,z=self.origin
        path=[[int(i%W+x),int(i//(W*L)+y),int(i//W%L+z)] for i in indices]
        if not path:return dict(status='uncertain',reason='라벨과 경로 검색 불일치. 다시 분석하세요.',path=[])
        return dict(**pair,path=path,steps=len(path)-1)
