import copy,json,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import numpy as np
import nbtlib as nbt
import pytest
from ship_architect.ai_design import build_context,parse_plan,compile_plan,generate_design,PlanError,plan_schema
from ship_architect.ollama_client import OllamaClient,OllamaError,endpoint
from ship_architect.chunks import Cancelled
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic,load_file


def operation(kind='floor',material='minecraft:white_concrete',lo=(0,0,0),hi=(5,4,5),overwrite=False,source=None):
    return dict(kind=kind,material=material,min=list(lo),max=list(hi),overwrite=overwrite,source=source)


def plan(*ops):return dict(version=1,summary='한국어 방 설계',operations=list(ops),notes=[])


def setup():
    doc=Document(Schematic.empty(12,10,12));b=Bounds((2,1,2),(7,5,7));doc.selection=b
    return doc,b,build_context(doc,b)


@pytest.mark.parametrize('change',[
    lambda p:p.update(version=True),lambda p:p.update(code='exec(...)'),lambda p:p.update(notes=['x'*501]),
    lambda p:p['operations'][0].update(min=[True,0,0]),lambda p:p['operations'][0].update(min=[-1,0,0]),
    lambda p:p['operations'][0].update(max=[6,4,5]),lambda p:p['operations'][0].update(max=[0,0,-1]),
    lambda p:p['operations'][0].update(kind='python'),lambda p:p['operations'][0].update(material='create:shaft'),
    lambda p:p['operations'][0].update(material='minecraft:diamond_block'),lambda p:p['operations'][0].update(kind=[]),
    lambda p:p['operations'][0].update(kind='erase'),lambda p:p['operations'][0].update(source='minecraft:stone'),
    lambda p:p['operations'][0].update(kind='replace',source='minecraft:stone'),lambda p:p['operations'][0].update(overwrite='false'),
    lambda p:p.update(operations=[operation()]*17)])
def test_strict_untrusted_plans(change):
    d,b,ctx=setup();p=plan(operation());change(p)
    with pytest.raises(PlanError):parse_plan(json.dumps(p),ctx)
    assert not np.any(d.schematic.blocks)


@pytest.mark.parametrize('raw',['{"version":1,"version":1}','{"x":NaN}','```json\n{}\n```','[]','x'*65537])
def test_bad_json(raw):
    d,b,ctx=setup()
    with pytest.raises(PlanError):parse_plan(raw,ctx)


def test_multimaterial_preview_approval_atomic_history_roundtrip(tmp_path):
    d,b,ctx=setup()
    p=compile_plan(d,b,plan(operation(),operation('walls','minecraft:stone'),operation('ceiling','minecraft:glass')),ctx,d.revision)
    assert p.origin=='ai' and len(np.unique(p.after))==3
    assert not np.any(d.schematic.blocks)
    preview=d.preview(p);assert np.count_nonzero(preview)==len(p.indices)
    d.approve(p);assert np.array_equal(d.schematic.blocks,preview) and len(d.undo_stack)==1
    d.undo();assert not np.any(d.schematic.blocks)
    d.redo();assert np.array_equal(d.schematic.blocks,preview)
    project=tmp_path/'ai.shipproj';d.save_project(project);reopen=Document.open_project(project)
    assert reopen.undo_stack[0]['origin']=='ai' and reopen.undo_stack[0]['plan']['summary']=='한국어 방 설계'
    reopen.undo();assert not np.any(reopen.schematic.blocks)
    reopen.redo();assert np.array_equal(reopen.schematic.blocks,preview)
    export=tmp_path/'ai.schem';d.export(export,approved=True);sc,_=load_file(export)
    assert np.array_equal(sc.blocks,preview)


def test_original_nonair_requires_optin_and_same_plan_can_carve_door():
    d,b,ctx=setup();d.schematic.blocks[1,2,2]=2
    p=plan(operation('floor','minecraft:white_concrete',overwrite=True))
    with pytest.raises(PlanError,match='허용'):compile_plan(d,b,p,ctx,d.revision)
    ctx=build_context(d,b,True);compiled=compile_plan(d,b,p,ctx,d.revision);assert len(compiled.indices)==36
    d,b,ctx=setup()
    p=plan(operation('walls'),operation('erase','minecraft:air',lo=(2,0,0),hi=(2,2,0),overwrite=True))
    preview=d.preview(compile_plan(d,b,p,ctx,d.revision))
    assert not np.any(preview[1:4,2,4]) and preview[2,2,3]==1


def test_devices_nbt_locks_and_original_exterior_protected(tmp_path):
    sc=Schematic.empty(12,10,12);sc.palette[128]='computercraft:computer_advanced[facing=north]'
    sc.blocks[2,3,3]=128;sc.blocks[2,3,4]=2
    entity=nbt.Compound({'Id':nbt.String('computercraft:computer_advanced'),'Pos':nbt.IntArray([3,2,3]),'Secret':nbt.String('NEVER_SEND_THIS')})
    sc.root['Blocks']['BlockEntities'].append(entity)
    # Imported isolated decorative block is part of original exterior.
    path=tmp_path/'source.schem';path.write_bytes(sc.to_bytes());loaded,raw=load_file(path);d=Document(loaded,raw,path)
    b=Bounds((2,1,2),(7,5,7));d.add_zone('locked',Bounds((5,1,2),(5,5,7)),protected=True)
    ctx=build_context(d,b,True)
    assert 'NEVER_SEND_THIS' not in json.dumps(ctx) and str(path) not in json.dumps(ctx)
    p=plan(operation('add','minecraft:white_concrete'))
    compiled=compile_plan(d,b,p,ctx,d.revision);d.approve(compiled)
    assert loaded.blocks[2,3,3]==128 and loaded.blocks[2,3,4]==2
    assert not np.any(loaded.blocks[1:6,2:8,5])
    assert loaded.root['Blocks']['BlockEntities'][0]==entity
    erase=compile_plan(d,b,plan(operation('erase','minecraft:air',overwrite=True)),ctx,d.revision)
    d.approve(erase);assert loaded.blocks[2,3,3]==128 and loaded.blocks[2,3,4]==2


def test_stale_generation_and_global_budget():
    d,b,ctx=setup();revision=d.revision;d.log('selection_test')
    with pytest.raises(Cancelled):compile_plan(d,b,plan(operation()),ctx,revision)
    ctx['size_xyz']=[10000,10000,10000]
    with pytest.raises(PlanError,match='부피'):parse_plan(json.dumps(plan(operation(hi=(999,999,999)))),ctx)


class LocalHandler(BaseHTTPRequestHandler):
    responses=[];delay=0;requests=[]
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'{"models":[{"name":"qwen3:4b"}]}')
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.requests.append(payload)
        time.sleep(self.delay);self.send_response(200);self.end_headers()
        value=self.responses.pop(0) if self.responses else '{}'
        try:
            self.wfile.write((json.dumps({'message':{'thinking':'ignore','content':value},'done':True})+'\n').encode());self.wfile.flush()
        except (OSError,BrokenPipeError):pass


@pytest.fixture
def server():
    handler=type('IsolatedHandler',(LocalHandler,),{'responses':[],'requests':[],'delay':0})
    srv=ThreadingHTTPServer(('127.0.0.1',0),handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    yield OllamaClient('http://127.0.0.1:'+str(srv.server_port)),handler
    srv.shutdown();srv.server_close();thread.join()


def test_streaming_schema_and_single_repair(server):
    client,h=server;d,b,ctx=setup();h.responses=['invalid',json.dumps(plan(operation()))]
    assert client.models()==['qwen3:4b']
    result=generate_design(client,'qwen3:4b',d,b,'바닥을 설계해 줘',timeout=3)
    assert result['attempts']==2 and len(result['validation_errors'])==1 and len(result['proposal'].indices)==36
    assert not np.any(d.schematic.blocks)
    request=h.requests[0];assert request['format']==plan_schema(ctx['allowed_materials']) and request['think'] is False
    assert request['stream'] is True and 'tools' not in request


def test_refinement_sends_complete_previous_plan(server):
    client,h=server;d,b,ctx=setup();previous=plan(operation());h.responses=[json.dumps(previous)]
    generate_design(client,'qwen3:4b',d,b,'바닥은 유지해 줘',previous=previous)
    messages=h.requests[0]['messages'];assert any(m['role']=='assistant' and json.loads(m['content'])==previous for m in messages)


def test_timeout_cancel_and_offline_do_not_mutate_document(server):
    client,h=server;d,b,ctx=setup();h.delay=.8
    start=time.monotonic()
    with pytest.raises(OllamaError,match='시간'):client.chat('qwen3:4b',[],{},timeout=.15)
    assert time.monotonic()-start<.7
    cancel=threading.Event();timer=threading.Timer(.15,cancel.set);timer.start()
    with pytest.raises(Cancelled):client.chat('qwen3:4b',[],{},timeout=2,cancel=cancel.is_set)
    timer.join();assert not np.any(d.schematic.blocks)
    with pytest.raises(OllamaError):OllamaClient('http://127.0.0.1:1').models()
    d.approve(d.propose(b,1,'floor'));assert np.count_nonzero(d.schematic.blocks)==36


@pytest.mark.parametrize('url',['https://localhost:11434','http://example.com','http://localhost:11434/api','http://u:p@localhost','http://localhost:abc'])
def test_only_local_endpoint(url):
    with pytest.raises((OllamaError,ValueError)):endpoint(url)


def test_changed_budget_and_empty_plan_are_safe():
    d=Document(Schematic.empty(501,1,501));b=d.selection;ctx=build_context(d,b)
    p=plan(operation(hi=(500,0,500)))
    with pytest.raises(PlanError,match='200,000'):compile_plan(d,b,p,ctx,d.revision)
    empty=compile_plan(d,b,plan(),ctx,d.revision);assert len(empty.indices)==0 and d.approve(empty) is None
    assert not np.any(d.schematic.blocks)


def test_all_targets_rechecked_atomically_on_ai_approval():
    d,b,ctx=setup();p=compile_plan(d,b,plan(operation(),operation('ceiling','minecraft:glass')),ctx,d.revision)
    # An NBT reservation appears after generation, approval must change zero blocks.
    i=int(p.indices[-1]);w,h,l=d.schematic.size;x=i%w;y=i//(w*l);z=(i//w)%l
    d.schematic.root['Blocks']['BlockEntities'].append(nbt.Compound({'Id':nbt.String('test:reserved'),'Pos':nbt.IntArray([x,y,z])}))
    with pytest.raises(ValueError,match='BlockEntity'):d.approve(p)
    assert not np.any(d.schematic.blocks) and not d.undo_stack


def test_ai_cannot_request_exterior_override():
    from dataclasses import replace
    d,b,ctx=setup();p=compile_plan(d,b,plan(operation()),ctx,d.revision)
    with pytest.raises(ValueError,match='AI'):d.approve(replace(p,allow_exterior=True),approve_exterior=True)
    assert not np.any(d.schematic.blocks)
