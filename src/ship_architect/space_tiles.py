"""Render-only sparse voxel tiles. Every face is derived from the original cells."""
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import blake2s
from time import perf_counter

import numpy as np

from .chunks import checkpoint

TILE = 16
MAX_TILES = 4096
MAX_MESH_BYTES = 24 * 1024**2


@dataclass(frozen=True)
class Tile:
    lo: tuple
    hi: tuple
    quads: np.ndarray
    normals: np.ndarray

    @property
    def nbytes(self):
        return self.quads.nbytes + self.normals.nbytes


class ByteCache:
    def __init__(self, limit, entries):
        self.limit, self.entries = limit, entries
        self.items = OrderedDict()
        self.bytes = self.hits = self.misses = 0

    def get(self, key):
        if key not in self.items:
            self.misses += 1
            return None
        self.hits += 1
        self.items.move_to_end(key)
        return self.items[key][0]

    def put(self, key, value, size):
        previous = self.items.pop(key, None)
        if previous is not None:
            self.bytes -= previous[1]
        if size > self.limit:
            return
        self.items[key] = value, size
        self.bytes += size
        self.trim()

    def trim(self):
        while self.bytes > self.limit or len(self.items) > self.entries:
            _, (_, removed) = self.items.popitem(last=False)
            self.bytes -= removed


def _face_strips(faces, axis, sign, origin):
    """Join consecutive cells into spans in the last grid dimension."""
    order = [axis] + [i for i in range(3) if i != axis]
    planes = faces.transpose(order)
    edges = np.diff(planes.astype(np.int8), axis=2, prepend=0, append=0)
    starts = np.argwhere(edges == 1)
    ends = np.argwhere(edges == -1)
    if not len(starts):
        return np.empty((0, 4, 3), dtype=np.float32)
    width_end = ends[:, 2]
    indices = np.lexsort((starts[:, 1], width_end, starts[:, 2], starts[:, 0]))
    starts, width_end = starts[indices], width_end[indices]
    cut = np.r_[True, (np.diff(starts[:, 0]) != 0) | (np.diff(starts[:, 1]) != 1)
                | (np.diff(starts[:, 2]) != 0) | (np.diff(width_end) != 0)]
    first = np.flatnonzero(cut)
    last = np.r_[first[1:] - 1, len(starts) - 1]
    q = np.repeat(starts[first, None, :], 4, axis=1)
    q[:, :, 0] += sign > 0
    q[:, 2:, 1] = starts[last, 1, None] + 1
    q[:, 1:3, 2] = width_end[first, None]
    # The grid runs in Y/Z/X order. The camera consumes X/Y/Z coordinates.
    return (q[:, :, np.argsort(order)] + origin)[..., [2, 0, 1]].astype(np.float32)


def _halo(chunks, coord):
    arr = np.zeros((TILE + 2,) * 3, dtype=bool)
    arr[1:-1, 1:-1, 1:-1] = chunks[coord]
    for axis in range(3):
        for sign in (-1, 1):
            neighbor = list(coord)
            neighbor[axis] += sign
            other = chunks.get(tuple(neighbor))
            if other is None:
                continue
            dst, src = [slice(1, -1)] * 3, [slice(None)] * 3
            dst[axis] = 0 if sign < 0 else -1
            src[axis] = -1 if sign < 0 else 0
            arr[tuple(dst)] = other[tuple(src)]
    return arr


def _make_tile(coord, arr):
    center = arr[1:-1, 1:-1, 1:-1]
    origin = np.asarray(coord) * TILE
    quads, normals = [], []
    for axis in range(3):
        for sign in (-1, 1):
            near = [slice(1, -1)] * 3
            near[axis] = slice(0, -2) if sign < 0 else slice(2, None)
            q = _face_strips(center & ~arr[tuple(near)], axis, sign, origin)
            n = np.zeros((len(q), 3), dtype=np.int8)
            n[:, (1, 2, 0)[axis]] = sign
            quads.append(q)
            normals.append(n)
    q, n = np.concatenate(quads), np.concatenate(normals)
    lo = tuple(map(int, origin[[2, 0, 1]]))
    return Tile(lo, tuple(v + TILE for v in lo), q, n)


def build_tiles(runs, clip, cache, cancel=None, progress=None):
    """Use the six neighbors as a halo so internal tile boundaries stay invisible."""
    lo, hi, levels = clip
    clipped_at = perf_counter()
    chunks = {}
    for i, (y, z, x, end) in enumerate(runs):
        if i % 256 == 0:
            checkpoint(cancel)
        if not lo[1] <= y <= hi[1] or not lo[2] <= z <= hi[2] or levels is not None and y not in levels:
            continue
        x, end = max(x, lo[0]), min(end, hi[0])
        while x <= end:
            tile_x, tile_y, tile_z = x // TILE, y // TILE, z // TILE
            key = tile_y, tile_z, tile_x
            if key not in chunks:
                if len(chunks) >= MAX_TILES:
                    raise ValueError('공간 청크 자원 한도 초과 · 2D 단면이나 층 제한으로 확인하세요.')
                chunks[key] = np.zeros((TILE,) * 3, dtype=bool)
            right = min(end + 1, (tile_x + 1) * TILE)
            chunks[key][y % TILE, z % TILE, x % TILE:right - tile_x * TILE] = True
            x = right
    clip_ms = (perf_counter() - clipped_at) * 1000
    built_at = perf_counter()
    tiles, size = [], 0
    for index, coord in enumerate(sorted(chunks), 1):
        checkpoint(cancel)
        halo = _halo(chunks, coord)
        key = coord, blake2s(halo.tobytes(), digest_size=16).digest()
        tile = cache.get(key)
        if tile is None:
            tile = _make_tile(coord, halo)
            cache.put(key, tile, tile.nbytes + 128)
        size += tile.nbytes
        if size > MAX_MESH_BYTES:
            raise ValueError('공간 메시 메모리 한도 초과 · 2D 단면이나 층 제한으로 확인하세요.')
        if len(tile.quads):
            tiles.append(tile)
        if progress and index < len(chunks) and (index % max(8,len(chunks)//16) == 0):
            progress(tuple(tiles),index,len(chunks))
    return tiles, clip_ms, (perf_counter() - built_at) * 1000


def screen_boxes(view, lower, upper):
    """Project boxes by center and orthographic extent, without allocating corners."""
    lower, upper = np.asarray(lower), np.asarray(upper)
    xy = view.project((lower + upper) * .5)
    radius = (upper - lower) @ np.abs(view.basis) * view.scale * .5
    return xy - radius, xy + radius


def in_view(view, low, high):
    return ((high >= 0) & (low <= [view.width(), view.height()])).all(axis=-1)
