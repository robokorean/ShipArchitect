"""Reproducible synthetic chunk benchmark; real-file GUI test: verify_schem.py."""
import sys,time,json,platform
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from ship_architect.schematic import Schematic
from ship_architect.mesh import build_scene
from ship_architect.model import Document
from create_demo import make_demo
from create_large_demo import make_large


def measure(label,sc):
    start=time.perf_counter();raw=sc.to_bytes();write=time.perf_counter()
    loaded=Schematic.from_bytes(raw);read=time.perf_counter()
    scene=build_scene(loaded);meshed=time.perf_counter()
    d=Document(loaded,raw);a=d.analyze(d.selection)
    assert np.array_equal(loaded.blocks,sc.blocks)
    return {'case':label,'size':sc.size,'volume':a['volume'],'placed_blocks':a['solid'],'air':a['empty'],
        'storage':type(loaded.blocks).__name__,'compressed_bytes':len(raw),'patches':len(scene),'chunks':scene.stats,
        'write_seconds':round(write-start,4),'read_seconds':round(read-write,4),'mesh_seconds':round(meshed-read,4)}


if __name__=='__main__':
    print(json.dumps({'platform':platform.platform(),'python':platform.python_version(),
        'cases':[measure('synthetic carrier',make_demo().schematic),measure('synthetic 6.29M sparse workspace',make_large())]},indent=2))
