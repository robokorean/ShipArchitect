"""Real Ollama end-to-end test. No mock responses. Does not alter source files.
python tools/verify_ai.py --source path/to/ship.schem --output verification_ai
"""
import argparse,hashlib,json,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file
from ship_architect.ai_design import generate_design
from ship_architect.ollama_client import OllamaClient


def verify(args):
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    client=OllamaClient(args.url);models=client.models();assert args.model in models,models
    records=[]
    def request(doc,bounds,prompt,name,previous=None):
        baseline=doc.schematic.blocks.copy();revision=doc.revision;start=time.perf_counter()
        result=generate_design(client,args.model,doc,bounds,prompt,previous=previous,timeout=args.timeout,
            progress=lambda n,t:print(t,flush=True) if n==0 else None)
        p=result['proposal'];assert len(p.indices)>0,'AI proposed no changes: '+str(result['plan'])
        assert doc.revision==revision and np.array_equal(doc.schematic.blocks,baseline)
        preview=doc.preview(p);assert np.any(preview!=baseline)
        x,y,z=bounds.lo;X,Y,Z=bounds.hi
        expected='minecraft:gray_concrete' if name=='refined_room' else 'minecraft:white_concrete'
        floor_id=next(i for i,s in doc.schematic.palette.items() if s==expected)
        assert np.all(preview[y,z:Z+1,x:X+1]==floor_id),'AI did not cover the full selected floor'
        if name!='existing_ship':
            glass_id=next(i for i,s in doc.schematic.palette.items() if s=='minecraft:glass')
            assert np.all(preview[Y,z+1:Z,x+1:X]==glass_id),'AI did not cover the selected ceiling'
            white=next(i for i,s in doc.schematic.palette.items() if s=='minecraft:white_concrete')
            for face in (preview[y+1:Y,z,x:X+1],preview[y+1:Y,Z,x:X+1],preview[y+1:Y,z:Z+1,x],preview[y+1:Y,z:Z+1,X]):
                assert np.all(face==white),'AI did not create full height walls'
        del preview
        r={k:result[k] for k in ('plan','context','model','stats','attempts','validation_errors')}
        r.update(name=name,seconds=round(time.perf_counter()-start,3),changed_blocks=len(p.indices),
            materials={doc.schematic.palette[int(i)]:int(n) for i,n in zip(*np.unique(p.after,return_counts=True))},
            before_approval_unchanged=True)
        records.append(r);(out/(name+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    doc=Document(Schematic.empty(20,12,20));bounds=Bounds((2,1,2),(13,7,13));doc.selection=bounds
    first=request(doc,bounds,'선택 구역 전체에 minecraft:white_concrete 바닥과 벽, minecraft:glass 천장이 있는 빈 방을 설계해 줘. 원래 공기만 사용해.', 'blank_room')
    revised=request(doc,bounds,'이전 방의 바닥 재료만 minecraft:gray_concrete로 바꾸고 벽과 유리 천장은 유지한 전체 계획을 다시 생성해 줘.', 'refined_room',first['plan'])
    doc.approve(revised['proposal']);approved=doc.schematic.blocks.copy();doc.undo();assert not np.any(doc.schematic.blocks)
    doc.redo();assert np.array_equal(doc.schematic.blocks,approved)
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'ai_room.schem';report=doc.export(path,approved=True);loaded,_=load_file(path)
        assert np.array_equal(loaded.blocks,approved)
        project=Path(tmp)/'ai.shipproj';doc.save_project(project);restored=Document.open_project(project)
        restored.undo();restored.redo();assert np.array_equal(restored.schematic.blocks,approved)
    large=None
    if args.source:
        source_hash=hashlib.sha256(args.source.read_bytes()).hexdigest()
        sc,raw=load_file(args.source);doc=Document(sc,raw,args.source);doc.prepare_protection()
        baseline=sc.blocks.copy();nbt_before=sc.metadata_digest()
        w,h,l=sc.size;region=None
        for y in range(4,h-8,8):
            for z in range(0,l-12,12):
                for x in range(0,w-12,12):
                    b=Bounds((x,y,z),(x+11,y+6,z+11));a=sc.blocks[b.slices]
                    if not np.any(sc.solid(a)) and not np.any(sc.protected(a,b.lo)):
                        region=b;break
                if region:break
            if region:break
        assert region is not None
        doc.selection=region
        result=request(doc,region,'선택 구역의 최하단 Y=0 전체에 minecraft:white_concrete 바닥만 설계해 줘. 기존 함선과 설비는 유지하고 벽과 천장은 만들지 마.', 'existing_ship')
        doc.approve(result['proposal'])
        original_solid=sc.solid(baseline);assert np.array_equal(sc.blocks[original_solid],baseline[original_solid])
        assert sc.metadata_digest()==nbt_before
        with tempfile.TemporaryDirectory() as tmp:
            export=Path(tmp)/'ai_ship.schem';report=doc.export(export,approved=True);loaded,_=load_file(export)
            assert np.array_equal(loaded.blocks,sc.blocks)
            assert loaded.metadata_digest()==nbt_before
        assert hashlib.sha256(args.source.read_bytes()).hexdigest()==source_hash
        large={'file':args.source.name,'sha256':source_hash,'size_xyz':sc.size,'volume':sc.blocks.size,
            'placed_before':int(original_solid.sum()),'block_entities':len(sc.entity_positions()[0]),
            'region':{'lo':region.lo,'hi':region.hi},'original_non_air':'unchanged','typed_nbt':'identical',
            'source':'unchanged','disk_reread':'passed'}
    summary={'backend':'REAL Ollama / Qwen3 inference (not mocked)','model':args.model,'platform':sys.platform,
        'models':models,'requests':[{'name':r['name'],'seconds':r['seconds'],'changed_blocks':r['changed_blocks'],'attempts':r['attempts']} for r in records],
        'preview_no_commit':'passed','undo_redo_atomic':'passed','project_history_restore':'passed','export_disk_reread':'passed','large_source':large}
    (out/'ai_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:11434');parser.add_argument('--model',default='qwen3:4b')
    parser.add_argument('--timeout',type=int,default=600);parser.add_argument('--source',type=Path);parser.add_argument('--output',type=Path,default=Path('verification_ai'))
    verify(parser.parse_args())
