"""Actual Qwen3 material identity check: JSON -> engine -> preview/mesh -> disk.
python tools/verify_ai_materials.py --url http://127.0.0.1:11434 --output verification_materials
"""
import argparse,json,sys,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from ship_architect.ollama_client import OllamaClient
from ship_architect.ai_design import generate_design
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.mesh import build_scene


def verify(args):
    args.output.mkdir(parents=True,exist_ok=True);client=OllamaClient(args.url);assert args.model in client.models()
    rows=[]
    for state in ('minecraft:oak_planks','minecraft:stone_bricks','minecraft:glass'):
        doc=Document(Schematic.empty(6,4,6));b=Bounds((1,1,1),(3,2,3));doc.selection=b
        start=time.perf_counter();result=generate_design(client,args.model,doc,b,f'선택 구역 전체에 {state} 재료로 바닥만 만들어 줘. 다른 재료나 벽, 천장, 기둥은 만들지 마.',timeout=args.timeout)
        p=result['proposal'];assert len(p.indices)==9
        # No block mutation before user approval. New requested palette entries are metadata only.
        assert not np.any(doc.schematic.solid())
        assert {doc.schematic.palette[int(i)] for i in p.after}=={state}
        assert {op['material'] for op in result['plan']['operations']}=={state}
        preview=doc.preview(p);assert np.all(preview[1,1:4,1:4]==next(i for i,s in doc.schematic.palette.items() if s==state))
        scene=build_scene(doc.schematic,blocks=preview)
        rendered={doc.schematic.palette[int(i)] for chunk in scene.chunks for i in chunk.materials}
        assert rendered=={state},rendered
        doc.approve(p);expected=doc.schematic.blocks.copy();doc.undo();assert not np.any(doc.schematic.solid());doc.redo();assert np.array_equal(doc.schematic.blocks,expected)
        path=args.output/(state.split(':')[1]+'.schem');doc.export(path,approved=True);reloaded,_=load_file(path)
        assert np.array_equal(reloaded.blocks,expected)
        counts=Counter(reloaded.palette[int(i)] for i in reloaded.blocks.reshape(-1));assert counts[state]==9
        assert set(counts)=={'minecraft:air',state} and counts.get('minecraft:stone',0)==0
        record={k:result[k] for k in ('plan','context','stats','attempts','validation_errors','raw_plan_json')}
        record.update(requested=state,seconds=round(time.perf_counter()-start,3),placed=counts[state],rendered_states=sorted(rendered),exported_palette=reloaded.palette,output=path.name,undo_redo='passed',source='REAL Ollama / Qwen3 (not mocked)')
        (args.output/(state.split(':')[1]+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        rows.append({key:record[key] for key in ('requested','seconds','placed','attempts','rendered_states','output')});print(rows[-1],flush=True)
    report={'model':args.model,'platform':sys.platform,'backend':'REAL Ollama / Qwen3 (not mocked)','cases':rows,'json_engine_preview_mesh_export_reread_identity':'passed','stone_fallback':'none'}
    (args.output/'materials_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:11434');p.add_argument('--model',default='qwen3:4b');p.add_argument('--timeout',type=int,default=600);p.add_argument('--output',type=Path,default=Path('verification_materials'));verify(p.parse_args())
