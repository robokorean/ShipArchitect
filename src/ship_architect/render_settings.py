"""Validated, render-only quality controls and bounded paint statistics."""
from collections import deque
from time import perf_counter

import numpy as np

PROFILES = {
    'auto': ('자동', 10000, 5000, 24),
    'low': ('저사양', 4000, 2500, 12),
    'balanced': ('균형', 9000, 5000, 24),
    'high': ('고품질', 16000, 7500, 40),
    'ultra': ('최고품질', 28000, 11000, 64),
}


def validated(value):
    value = value if isinstance(value, dict) else {}
    mode = value.get('quality')
    if not isinstance(mode, str) or mode not in PROFILES:
        mode = 'auto'
    _, faces, selection, memory = PROFILES[mode]
    def bounded(key, default, minimum, maximum):
        n = value.get(key)
        return max(minimum, min(maximum, n)) if type(n) is int else default
    return {'quality': mode, 'fps': bounded('fps', 60, 15, 144),
            'faces': bounded('faces', faces, 1000, 32000),
            'selection_faces': bounded('selection_faces', selection, 1000, 16000),
            'cache_mib': bounded('cache_mib', memory, 8, 96),
            'lod': value.get('lod') is not False,
            'priority': 'confirmed' if value.get('priority') == 'confirmed' else 'detail',
            'renderer': 'opengl' if value.get('renderer') == 'opengl' else 'compatible'}


def backend(config):
    return 'QPainter + CPU' + (' · OpenGL 요청은 호환 경로로 실행' if config['renderer'] == 'opengl' else '')


class Performance:
    def __init__(self):
        self.frames = deque(maxlen=120)
        self.current = {}

    def begin(self):
        self.started = perf_counter()
        self.current = dict.fromkeys(('exact', 'simplified', 'summary', 'hidden', 'offscreen',
                                      'pending', 'failed', 'not_drawn', 'faces', 'visible_tiles',
                                      'projection_ms', 'draw_ms', 'lod_budget', 'selection_exact',
                                      'selection_expected'), 0)

    def end(self):
        self.frames.append((perf_counter() - self.started) * 1000)

    def snapshot(self):
        values = np.asarray(self.frames)
        return self.current | ({'paint_samples': len(values), 'paint_mean_ms': round(float(values.mean()), 2),
                                'paint_p50_ms': round(float(np.median(values)), 2),
                                'paint_p95_ms': round(float(np.percentile(values, 95)), 2),
                                'paint_throughput_fps': round(1000 / max(.01, float(values.mean())), 1)}
                               if len(values) else {})
