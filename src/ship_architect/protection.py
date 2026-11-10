"""Immutable original exterior: six-connected outside air, not a material heuristic.
Masks and BFS queue are disk backed. An open doorway connects its room to outside;
such surfaces are deliberately protected too. This is geometry, not device physics.
"""
from pathlib import Path
import shutil
import tempfile
import numpy as np
from numba import njit
from .chunks import BATCH,checkpoint


@njit(cache=True,nogil=True)
def seed_boundary(state,queue,w,h,l):
    tail=0
    for y in range(h):
        for z in range(l):
            for x in (0,w-1):
                i=(y*l+z)*w+x
                if state[i]==1:state[i]=2;queue[tail]=i;tail+=1
                elif state[i]==0:state[i]=3
    for y in range(h):
        for x in range(w):
            for z in (0,l-1):
                i=(y*l+z)*w+x
                if state[i]==1:state[i]=2;queue[tail]=i;tail+=1
                elif state[i]==0:state[i]=3
    for z in range(l):
        for x in range(w):
            for y in (0,h-1):
                i=(y*l+z)*w+x
                if state[i]==1:state[i]=2;queue[tail]=i;tail+=1
                elif state[i]==0:state[i]=3
    return tail


@njit(cache=True,nogil=True)
def flood_step(state,queue,head,tail,w,h,l,budget):
    stop=min(head+budget,len(queue))
    plane=w*l
    while head<tail and head<stop:
        i=int(queue[head]);head+=1
        x=i%w;z=(i//w)%l;y=i//plane
        for axis in range(6):
            j=-1
            if axis==0 and x>0:j=i-1
            elif axis==1 and x<w-1:j=i+1
            elif axis==2 and z>0:j=i-w
            elif axis==3 and z<l-1:j=i+w
            elif axis==4 and y>0:j=i-plane
            elif axis==5 and y<h-1:j=i+plane
            if j>=0:
                if state[j]==1:
                    state[j]=2;queue[tail]=j;tail+=1
                elif state[j]==0:state[j]=3
    return head,tail


class Exterior:
    def __init__(self,owner,state,shape,count):
        self.owner,self.state,self.shape,self.count=owner,state,shape,count

    def mask(self,lo,shape):
        x,y,z=lo;h,l,w=shape
        return self.state.reshape(self.shape)[y:y+h,z:z+l,x:x+w]==3

    def at(self,indices):return self.state[indices]==3

    def __del__(self):
        try:self.state._mmap.close();self.owner.cleanup()
        except Exception:pass


def build_exterior(schematic,progress=None,cancel=None):
    n=schematic.blocks.size
    owner=tempfile.TemporaryDirectory(prefix='ship-exterior-',ignore_cleanup_errors=True)
    if shutil.disk_usage(owner.name).free < n*5+256*1024**2:
        owner.cleanup();raise ValueError('원본 외피 보호 분석을 위한 임시 디스크 공간이 부족합니다.')
    state=np.memmap(Path(owner.name)/'exterior.bin',dtype=np.uint8,mode='w+',shape=(n,))
    queue=np.memmap(Path(owner.name)/'queue.bin',dtype=np.uint32,mode='w+',shape=(n,))
    flat=schematic.blocks.reshape(-1)
    try:
        for start in range(0,n,BATCH):
            checkpoint(cancel)
            state[start:start+BATCH]=~schematic.solid(flat[start:start+BATCH])
        if progress:progress(0,'기존 외피 분석 준비 (최초 연산 준비 포함)')
        w,h,l=schematic.size
        tail=seed_boundary(state,queue,w,h,l);head=0
        while head<tail:
            checkpoint(cancel)
            head,tail=flood_step(state,queue,head,tail,w,h,l,BATCH)
            if progress:progress(min(99,int(head*100/max(1,n))),f'외부 공기 연결 분석 {head:,}/{n:,}')
        count=0
        for start in range(0,n,BATCH):
            checkpoint(cancel);count+=int(np.count_nonzero(state[start:start+BATCH]==3))
        state.flush();queue._mmap.close();(Path(owner.name)/'queue.bin').unlink()
        if progress:progress(100,f'원본 외피 {count:,}블록 보호 준비 완료')
        return Exterior(owner,state,schematic.blocks.shape,count)
    except BaseException:
        queue._mmap.close();state._mmap.close();owner.cleanup();raise
