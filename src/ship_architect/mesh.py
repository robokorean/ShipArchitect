"""32³ chunk meshes with one-voxel halos, bounded LRU and cross-chunk face removal."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
import bisect
import hashlib
import threading
import numpy as np
from .chunks import regions, slices, checkpoint
from .model import Bounds


@dataclass
class MeshChunk:
    lo: tuple
    hi: tuple
    quads: np.ndarray
    normals: np.ndarray
    materials: np.ndarray

    def __len__(self): return len(self.materials)

    @property
    def nbytes(self): return self.quads.nbytes+self.normals.nbytes+self.materials.nbytes

    def face(self,index): return self.quads[index],self.normals[index],int(self.materials[index])


class MeshScene:
    def __init__(self,chunks=(),stats=None):
        self.chunks=list(chunks)
        self.ends=np.cumsum([len(c) for c in self.chunks]).tolist()
        self.stats=stats or {}

    def __len__(self): return self.ends[-1] if self.ends else 0

    def __getitem__(self,index):
        if index<0: index+=len(self)
        if not 0<=index<len(self): raise IndexError(index)
        n=bisect.bisect_right(self.ends,index)
        return self.chunks[n].face(index-(self.ends[n-1] if n else 0))

    def __iter__(self):
        for c in self.chunks:
            for i in range(len(c)): yield c.face(i)


class MeshCache:
    def __init__(self,budget=96*1024**2):
        self.budget=budget
        self.used=0
        self.entries=OrderedDict()
        self.lock=threading.RLock()

    def get(self,key):
        with self.lock:
            value=self.entries.get(key)
            if value is not None:self.entries.move_to_end(key)
            return value

    def put(self,key,value):
        with self.lock:
            if key in self.entries:self.used-=self.entries.pop(key).nbytes
            self.entries[key]=value;self.used+=value.nbytes
            while self.used>self.budget and self.entries:
                _,old=self.entries.popitem(last=False);self.used-=old.nbytes

    def fork_invalidated(self,indices,size):
        from .chunks import CHUNK_SIZE
        indices=np.asarray(indices,dtype=np.int64)
        w,h,l=size
        coords=np.stack([indices%w,indices//(w*l),(indices//w)%l],axis=1)//CHUNK_SIZE if len(indices) else np.empty((0,3),np.int64)
        dirty=set(map(tuple,np.unique(coords,axis=0)))
        for xyz in list(dirty):
            for axis in range(3):
                for sign in (-1,1):
                    other=list(xyz);other[axis]+=sign;dirty.add(tuple(other))
        result=MeshCache(self.budget)
        with self.lock:entries=list(self.entries.items())
        for key,value in entries:
            if tuple(v//CHUNK_SIZE for v in key[0]) not in dirty:result.put(key,value)
        return result


def build_scene(schematic,blocks=None,bounds=None,cache=None,progress=None,cancel=None,space=None,visible_y=None,display=None):
    from .views import surface_mesh
    blocks=schematic.blocks if blocks is None else blocks
    w,h,l=schematic.size
    bounds=bounds or Bounds((0,0,0),(w-1,h-1,l-1))
    bounds.validate(schematic.size)
    cache=cache if cache is not None else MeshCache()
    boxes=list(regions(blocks.shape,bounds))
    viewlo=np.array(bounds.lo)
    viewhi=np.array(bounds.hi)+1
    output=[]
    from .space_model import token,mask
    space_key=token(space) if space is not None else None
    if space is not None:space={'runs':np.asarray(space['runs'],np.int64)}
    display_key=display.key() if display is not None else None
    visible_key=tuple(visible_y) if visible_y is not None else None
    total_bytes=hits=empty=0
    for n,(lo,hi) in enumerate(boxes):
        checkpoint(cancel)
        flags=tuple(np.array(lo)==viewlo)+tuple(np.array(hi)==viewhi)
        local_display=None
        if display is not None:
            mlo=np.maximum(np.array(lo)-1,viewlo);mhi=np.minimum(np.array(hi)+1,viewhi);dims=mhi-mlo
            local_mask=display.mask(mlo,(int(dims[1]),int(dims[2]),int(dims[0])))
            local_display=hashlib.sha256(local_mask.tobytes()).digest()
        local_levels=tuple(y for y in visible_key if lo[1]-1<=y<=hi[1]) if visible_key is not None else None
        key=(lo,hi,flags,space_key,local_levels,local_display)
        chunk=cache.get(key)
        if chunk is not None:
            hits+=1
        else:
            a=blocks[slices(lo,hi)]
            solid=schematic.solid(a)
            if display is not None:solid &= display.mask(lo,a.shape)
            if space is not None:solid &= mask(space,lo,a.shape)
            if visible_y is not None:solid &= np.isin(np.arange(lo[1],hi[1]),visible_y)[:,None,None]
            if np.any(solid):
                qlo=np.maximum(np.array(lo)-1,viewlo)
                qhi=np.minimum(np.array(hi)+1,viewhi)
                xyz=np.array(hi)-lo
                halo=np.zeros((xyz[1]+2,xyz[2]+2,xyz[0]+2),dtype=bool)
                targetlo=qlo-(np.array(lo)-1)
                targethi=qhi-(np.array(lo)-1)
                neighbour=schematic.solid(blocks[slices(qlo,qhi)])
                if display is not None:neighbour &= display.mask(qlo,neighbour.shape)
                if space is not None:neighbour &= mask(space,qlo,neighbour.shape)
                if visible_y is not None:neighbour &= np.isin(np.arange(qlo[1],qhi[1]),visible_y)[:,None,None]
                halo[slices(targetlo,targethi)]=neighbour
                faces=surface_mesh(a,solid,limit=200_000,halo=halo.transpose(2,0,1))
                if faces:
                    quads=np.stack([v[0] for v in faces]).astype(np.float32)+np.array(lo,dtype=np.float32)
                    normals=np.stack([v[1] for v in faces]).astype(np.int8)
                    materials=np.array([v[2] for v in faces],dtype=np.int32)
                else:
                    quads=np.empty((0,4,3),np.float32);normals=np.empty((0,3),np.int8);materials=np.empty(0,np.int32)
            else:
                quads=np.empty((0,4,3),np.float32);normals=np.empty((0,3),np.int8);materials=np.empty(0,np.int32)
            chunk=MeshChunk(lo,hi,quads,normals,materials)
            cache.put(key,chunk)
        if len(chunk):
            output.append(chunk)
            total_bytes+=chunk.nbytes
        else: empty+=1
        if total_bytes>192*1024**2:
            raise ValueError('3D 메시 예산 192 MiB 초과. 데이터는 유지됩니다. Y층 또는 구역 한정으로 표시하세요.')
        if progress and (n%8==0 or n==len(boxes)-1):
            progress(int(100*(n+1)/max(1,len(boxes))),f'청크 메시 {n+1:,}/{len(boxes):,}')
    return MeshScene(output,{'chunks':len(boxes),'visible_chunks':len(output),'empty_or_enclosed':empty,'cache_hits':hits,'bytes':total_bytes})
