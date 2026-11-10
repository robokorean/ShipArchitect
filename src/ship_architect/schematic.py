"""Sponge v2/v3 IO. Preserve the entire typed NBT tree; change only block arrays."""
from __future__ import annotations
import copy
import gzip
import hashlib
import io
import os
from pathlib import Path
import tempfile
from .chunks import allocate, decode_into, encode_batch, BATCH, checkpoint
import numpy as np
import nbtlib as nbt

MAX_VOLUME = 536_870_912  # disk budget, not a dense RAM allocation
MAX_BYTES = 512 * 1024 * 1024
AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}


def decode_varints(data, count):
    result = np.empty(count, dtype=np.int32)
    value = shift = pos = 0
    for b in data:
        b = int(b) & 255
        value |= (b & 127) << shift
        if shift == 28 and b & 248:
            raise ValueError('팔레트 VarInt 범위를 초과했습니다.')
        if b & 128:
            shift += 7
            if shift > 28:
                raise ValueError('잘못된 VarInt 길이입니다.')
            continue
        if pos >= count:
            raise ValueError('블록 데이터가 크기보다 많습니다.')
        result[pos] = value
        pos += 1
        value = shift = 0
    if shift or pos != count:
        raise ValueError(f'블록 데이터 손상: {pos}/{count}개, 미완성 VarInt={bool(shift)}')
    return result


def encode_varints(values):
    out = bytearray()
    for n in values:
        n = int(n)
        if not 0 <= n <= 0x7fffffff:
            raise ValueError('잘못된 팔레트 인덱스')
        while n > 127:
            out.append((n & 127) | 128)
            n >>= 7
        out.append(n)
    return nbt.ByteArray(np.frombuffer(bytes(out), dtype=np.int8).copy())


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class LimitedReader:
    def __init__(self,stream,progress=None,cancel=None):
        self.stream=stream
        self.progress=progress
        self.cancel=cancel
        self.used=0
        self.next_report=1024**2

    def read(self,n=-1):
        checkpoint(self.cancel)
        if n<0 or n>MAX_BYTES-self.used:
            raise ValueError('NBT 읽기 예산 512 MiB 초과')
        data=self.stream.read(n)
        if len(data)!=n:raise ValueError('NBT 파일이 잘렸습니다.')
        self.used+=len(data)
        if self.progress and self.used>=self.next_report:
            self.progress(15,f'NBT 읽기 {self.used/1024**2:.1f} MiB')
            self.next_report=self.used+1024**2
        return data


class Schematic:
    def __init__(self, tree, blocks, palette, storage=None, counts=None):
        self.storage = storage
        self.counts = counts
        self._entities_cache = None
        self._entity_chunks = {}
        self.tree = tree
        self.blocks = blocks  # shape Y,Z,X, coordinates in UI are X,Y,Z
        self.palette = dict(palette)
        self.safe_mod_states=set()
        self.mod_registry={}
        self.root = tree.get('Schematic', tree)
        self.version = int(self.root['Version'])

    def __del__(self):
        try:
            if self.storage is not None:
                self.blocks._mmap.close()
                self.storage.cleanup()
        except Exception:
            pass

    @property
    def size(self):
        h, l, w = self.blocks.shape
        return w, h, l

    @classmethod
    def from_bytes(cls, raw, progress=None, cancel=None):
        checkpoint(cancel)
        if progress: progress(5, "NBT 읽기 / 압축 해제")
        if len(raw) > MAX_BYTES:
            raise ValueError('입력 파일 제한은 512 MiB입니다.')
        source=io.BytesIO(raw)
        stream=gzip.GzipFile(fileobj=source) if raw[:2]==b'\x1f\x8b' else source
        tree=nbt.File.parse(LimitedReader(stream,progress,cancel))
        stream.close()
        root = tree.get('Schematic', tree)
        version = int(root.get('Version', 0))
        if version not in (2, 3):
            raise ValueError('Sponge .schem v2/v3만 지원합니다. 구형 .schematic/.litematic은 변환하세요.')
        w, h, l = [int(root[k]) & 65535 for k in ('Width', 'Height', 'Length')]
        if min(w, h, l) < 1 or w * h * l > MAX_VOLUME:
            raise ValueError('유효하지 않은 크기 또는 디스크 블록 저장소 예산 초과입니다.')
        container = root['Blocks'] if version == 3 else root
        palette = {int(v): str(k) for k, v in container['Palette'].items()}
        if len(palette) != len(container['Palette']) or not palette or min(palette) < 0:
            raise ValueError('팔레트 인덱스가 중복되거나 유효하지 않습니다.')
        if progress: progress(25, '디스크 블록 저장소 준비')
        blocks, storage = allocate((h,l,w))
        data_key = 'Data' if version == 3 else 'BlockData'
        counts = decode_into(container[data_key], blocks, palette, progress, cancel)
        # The original encoded Data is reproducible from blocks; keep all other typed tags.
        container[data_key] = nbt.ByteArray([])
        checkpoint(cancel)
        return cls(tree, blocks, palette, storage, counts)

    @classmethod
    def empty(cls, w, h, l, data_version=3955):
        if min(w, h, l) < 1 or max(w, h, l) > 32767 or w * h * l > MAX_VOLUME:
            raise ValueError('양수 크기, 축당 32767 이하, 디스크 예산 2 GiB 이내로 지정하세요.')
        palette = {0: 'minecraft:air', 1: 'minecraft:white_concrete', 2: 'minecraft:stone',
                   3: 'minecraft:polished_andesite', 4: 'minecraft:glass', 5: 'minecraft:oak_planks',
                   6: 'minecraft:gray_concrete', 7: 'minecraft:lime_wool'}
        root = nbt.Compound({'Version': nbt.Int(3), 'DataVersion': nbt.Int(data_version),
            'Width': nbt.Short(w), 'Height': nbt.Short(h), 'Length': nbt.Short(l),
            'Offset': nbt.IntArray([0, 0, 0]), 'Metadata': nbt.Compound({'Name': nbt.String('Ship Architect')}),
            'Blocks': nbt.Compound({'Palette': nbt.Compound(), 'Data': nbt.ByteArray([]),
                                  'BlockEntities': nbt.List[nbt.Compound]([])}),
            'Entities': nbt.List[nbt.Compound]([])})
        blocks, storage = allocate((h,l,w))
        return cls(nbt.File({'Schematic': root}), blocks, palette, storage, None)

    def to_bytes(self, cancel=None):
        tree = copy.deepcopy(self.tree)
        root = tree.get('Schematic', tree)
        container = root['Blocks'] if self.version == 3 else root
        container['Palette'] = nbt.Compound({state: nbt.Int(i) for i, state in self.palette.items()})
        container['Data' if self.version == 3 else 'BlockData'] = StreamingBlocks(self.blocks, cancel)
        if self.version == 2:
            root['PaletteMax'] = nbt.Int(max(self.palette) + 1)
        stream = io.BytesIO()
        with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as compressed:
            tree.write(compressed)
        return stream.getvalue()

    def metadata_digest(self):
        tree=copy.deepcopy(self.tree)
        root=tree.get('Schematic',tree)
        container=root['Blocks'] if self.version==3 else root
        container['Data' if self.version==3 else 'BlockData']=nbt.ByteArray([])
        container['Palette']=nbt.Compound({self.palette[i]:nbt.Int(i) for i in sorted(self.palette)})
        if self.version==2:root['PaletteMax']=nbt.Int(max(self.palette)+1)
        stream=io.BytesIO();tree.write(stream)
        return hashlib.sha256(stream.getvalue()).hexdigest()

    def solid(self, blocks=None):
        blocks = self.blocks if blocks is None else blocks
        air_ids = [i for i, state in self.palette.items() if state.split('[')[0] in AIR]
        return ~np.isin(blocks, air_ids)

    def entity_positions(self):
        if self._entities_cache is not None:
            return self._entities_cache
        container = self.root['Blocks'] if self.version == 3 else self.root
        positions=set()
        unsafe=False
        for entity in container.get('BlockEntities', []):
            try:
                pos=entity.get('Pos')
                if pos is None or len(pos)!=3:
                    raise ValueError('unknown position')
                xyz=tuple(map(int,pos))
                if not all(0<=v<dim for v,dim in zip(xyz,self.size)):
                    raise ValueError('outside schematic')
                positions.add(xyz)
            except (TypeError,ValueError,AttributeError):
                unsafe=True
        self._entities_cache=positions,unsafe
        self._entity_chunks={}
        for xyz in positions:
            key=tuple(v//32 for v in xyz)
            self._entity_chunks.setdefault(key,[]).append(xyz)
        return self._entities_cache

    def is_decorative(self,state):
        from .model import decorative
        from .block_registry import valid_vanilla_state
        return (decorative(state) and valid_vanilla_state(state)) or state in self.safe_mod_states

    def protected(self, blocks=None, origin=(0,0,0), _unsafe_ids=None):
        from .model import decorative
        blocks=self.blocks if blocks is None else blocks
        ids=[i for i,state in self.palette.items() if not self.is_decorative(state)] if _unsafe_ids is None else _unsafe_ids
        mask=np.isin(blocks,ids)
        positions,unsafe=self.entity_positions()
        if unsafe:
            mask[:]=True
        else:
            x0,y0,z0=origin
            h,l,w=blocks.shape
            for cy in range(y0//32,(y0+h-1)//32+1):
                for cz in range(z0//32,(z0+l-1)//32+1):
                    for cx in range(x0//32,(x0+w-1)//32+1):
                        for x,y,z in self._entity_chunks.get((cx,cy,cz),()):
                            if x0<=x<x0+w and y0<=y<y0+h and z0<=z<z0+l:mask[y-y0,z-z0,x-x0]=True
        return mask

    def protected_at(self, xyz):
        from .model import decorative
        x,y,z=xyz
        positions,unsafe=self.entity_positions()
        return unsafe or xyz in positions or not self.is_decorative(self.palette[int(self.blocks[y,z,x])])


class StreamingBlocks(nbt.ByteArray):
    """NBT ByteArray writer that never materializes full uncompressed block data."""
    def __new__(cls, blocks, cancel=None):
        obj=super().__new__(cls,[])
        obj.blocks=blocks
        obj.cancel=cancel
        return obj

    def write(self, fileobj, byteorder='big'):
        import struct
        flat=self.blocks.reshape(-1)
        total=len(flat)
        for start in range(0,len(flat),BATCH):
            checkpoint(self.cancel)
            a=flat[start:start+BATCH]
            for shift in (7,14,21,28):
                total += int(np.count_nonzero(a >= 1<<shift))
        if total>0x7fffffff:
            raise ValueError('NBT 블록 데이터 길이 초과')
        fileobj.write(struct.pack('>i' if byteorder=='big' else '<i',total))
        for start in range(0,len(flat),BATCH):
            checkpoint(self.cancel)
            fileobj.write(encode_batch(flat[start:start+BATCH]))


def load_file(path, backup_dir=None, progress=None, cancel=None):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('입력 파일이 512 MiB보다 큽니다.')
    raw = path.read_bytes()
    schematic = Schematic.from_bytes(raw, progress, cancel)
    if backup_dir:
        digest = hashlib.sha256(raw).hexdigest()
        backup = Path(backup_dir) / (digest + '.schem')
        if not backup.exists():
            atomic_write(backup, raw)
    if progress: progress(90, "원본 백업 완료")
    return schematic, raw
