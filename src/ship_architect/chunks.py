"""Bounded-memory block storage and voxel iteration. Public coordinates XYZ, arrays YZX."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import shutil
import tempfile
import numpy as np

CHUNK_SIZE = 32
BATCH = 262_144
MAX_DISK_BYTES = 2 * 1024**3


class Cancelled(Exception):
    pass


def checkpoint(cancel=None):
    if cancel and cancel():
        raise Cancelled('작업을 취소했습니다.')


def allocate(shape):
    volume = int(np.prod(shape))
    required = volume * 4
    if required > MAX_DISK_BYTES:
        raise ValueError('작업 저장소 2 GiB 예산 초과입니다. 설계를 구역으로 나눠 주세요.')
    if volume <= 1_000_000:
        return np.zeros(shape, dtype=np.int32), None
    owner = tempfile.TemporaryDirectory(prefix='shiparchitect-',ignore_cleanup_errors=True)
    if shutil.disk_usage(owner.name).free < required + 256*1024**2:
        owner.cleanup()
        raise ValueError('임시 블록 저장소를 만들 디스크 공간이 부족합니다.')
    array = np.memmap(Path(owner.name)/'blocks.bin', dtype=np.int32, mode='w+', shape=shape)
    return array, owner


def regions(shape, bounds=None, size=CHUNK_SIZE):
    h, l, w = shape
    lo, hi = ((0,0,0),(w-1,h-1,l-1)) if bounds is None else (bounds.lo,bounds.hi)
    x0,y0,z0 = lo
    x1,y1,z1 = (v+1 for v in hi)
    for y in range(y0//size*size, y1, size):
        for z in range(z0//size*size, z1, size):
            for x in range(x0//size*size, x1, size):
                yield (max(x,x0),max(y,y0),max(z,z0)), (min(x+size,x1),min(y+size,y1),min(z+size,z1))


def slices(lo, hi):
    x,y,z=lo
    X,Y,Z=hi
    return slice(y,Y),slice(z,Z),slice(x,X)


def count_states(blocks, cancel=None):
    counts=Counter()
    flat=blocks.reshape(-1)
    for start in range(0, len(flat), BATCH):
        checkpoint(cancel)
        ids, ns=np.unique(flat[start:start+BATCH],return_counts=True)
        counts.update({int(i):int(n) for i,n in zip(ids,ns)})
    return counts


def encode_batch(values):
    values=np.asarray(values, dtype=np.int32).reshape(-1)
    if np.any(values < 0):
        raise ValueError('잘못된 팔레트 인덱스')
    lengths=np.ones(len(values), dtype=np.int8)
    for shift in (7,14,21,28):
        lengths += values >= (1 << shift)
    offsets=np.cumsum(lengths,dtype=np.int64)-lengths
    out=np.empty(int(lengths.sum()),dtype=np.uint8)
    for k in range(5):
        mask=lengths > k
        byte=((values[mask].astype(np.uint32) >> (7*k)) & 127).astype(np.uint8)
        byte |= np.where(lengths[mask] > k+1,128,0).astype(np.uint8)
        out[offsets[mask]+k]=byte
    return out.tobytes()


def decode_into(data, output, palette, progress=None, cancel=None):
    """Decode bounded encoded-byte batches directly into disk-backed blocks."""
    raw=np.asarray(data,dtype=np.int8).view(np.uint8)
    flat=output.reshape(-1)
    offset=position=0
    counts=Counter()
    while offset < len(raw):
        checkpoint(cancel)
        part=raw[offset:offset+BATCH]
        ends=np.flatnonzero(part < 128)
        if not len(ends):
            raise ValueError('잘못된 VarInt 길이입니다.')
        starts=np.r_[0,ends[:-1]+1]
        lengths=ends-starts+1
        if np.any(lengths>5) or np.any((lengths==5)&((part[ends]&248)!=0)):
            raise ValueError('VarInt 범위 또는 길이 오류')
        values=np.zeros(len(ends),dtype=np.int32)
        for k in range(5):
            mask=lengths>k
            values[mask] |= (part[starts[mask]+k]&127).astype(np.int32) << (7*k)
        if position+len(values)>len(flat):
            raise ValueError('블록 데이터가 크기보다 많습니다.')
        ids,ns=np.unique(values,return_counts=True)
        if not set(map(int,ids)) <= palette.keys():
            raise ValueError('팔레트에 없는 블록 인덱스입니다.')
        counts.update({int(i):int(n) for i,n in zip(ids,ns)})
        flat[position:position+len(values)]=values
        position += len(values)
        offset += int(ends[-1])+1
        if progress:
            progress(25+int(55*offset/max(1,len(raw))),f'블록 해독 {position:,}/{len(flat):,}칸')
    if position != len(flat):
        raise ValueError(f'블록 데이터 손상: {position}/{len(flat)}개')
    if isinstance(output,np.memmap):
        output.flush()
    return counts
