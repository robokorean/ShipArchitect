import json,io,base64
import numpy as np
import pytest
from ship_architect.conversations import ConversationStore
from ship_architect.model import Document,Bounds
from ship_architect.schematic import Schematic


def test_project_isolation_restart_search_export_and_diff(tmp_path):
 path=tmp_path/'chats.db';s=ConversationStore(path);d=Document(Schematic.empty(6,4,6));d.chat_store=s
 s.start(d.project_id,'12345678','유리 바닥',{'lo':[0,0,0],'hi':[5,3,5]},model='qwen3:4b')
 s.patch(d.project_id,'12345678',response='{"summary":"계획"}',status='preview');p=d.propose(d.selection,4,'floor');s.changes(d.project_id,'12345678',p,d.schematic.palette,d.schematic.size)
 other=Document(Schematic.empty(6,4,6));assert not s.entries(other.project_id)
 reopened=ConversationStore(path);assert reopened.entries(d.project_id,'유리')[0]['prompt']=='유리 바닥'
 for kind in ('txt','json'):
  output=tmp_path/('chat.'+kind);s.export(d.project_id,output,kind);assert '유리 바닥' in output.read_text(encoding='utf-8')
 data=json.loads((tmp_path/'chat.json').read_text(encoding='utf-8'));blob=data['entries'][0]['change_map_npz_base64']
 with np.load(io.BytesIO(base64.b64decode(blob)),allow_pickle=False) as arrays:assert len(arrays['indices'])==36 and set(arrays['after'])=={4}


def test_conversation_portable_in_project_and_approval_undo_status(tmp_path):
 s=ConversationStore(tmp_path/'a.db');d=Document(Schematic.empty(6,4,6));d.chat_store=s;s.start(d.project_id,'abcdef12','바닥',{'lo':[0,0,0],'hi':[5,3,5]})
 from dataclasses import replace
 p=replace(d.propose(d.selection,1,'floor'),trace_id='abcdef12');d.approve(p);assert s.get(d.project_id,'abcdef12')['status']=='approved'
 d.undo();assert s.get(d.project_id,'abcdef12')['status']=='undone';d.redo();assert s.get(d.project_id,'abcdef12')['status']=='redone'
 file=tmp_path/'chat.shipproj';d.save_project(file);r=Document.open_project(file);fresh=ConversationStore(tmp_path/'b.db');fresh.restore(r.chat_seed)
 assert r.project_id==d.project_id and fresh.get(r.project_id,'abcdef12')['status']=='redone'


def test_running_request_is_marked_interrupted_after_restart(tmp_path):
 path=tmp_path/'a.db';s=ConversationStore(path);s.start('project','trace','pending',{'lo':[0,0,0],'hi':[1,1,1]});new=ConversationStore(path);new.recover('project')
 assert new.get('project','trace')['status']=='interrupted'


def test_restore_rejects_cross_project_entries(tmp_path):
 s=ConversationStore(tmp_path/'a.db')
 with pytest.raises(ValueError):s.restore({'format':'ship-architect-chat','version':1,'project_id':'A','entries':[{'project_id':'B','trace_id':'trace'}]})


def test_chat_export_cannot_overwrite_schematic(tmp_path):
 s=ConversationStore(tmp_path/'a.db');path=tmp_path/'source.schem';path.write_bytes(b'original')
 with pytest.raises(ValueError):s.export('project',path,'json')
 assert path.read_bytes()==b'original'


def test_project_blocks_still_save_if_chat_snapshot_fails(tmp_path):
 d=Document(Schematic.empty(6,4,6));d.approve(d.propose(d.selection,1,'floor'))
 class Broken:
  def bundle(self,*args):raise OSError('DB unavailable')
 d.chat_store=Broken();path=tmp_path/'fallback.shipproj';d.save_project(path);r=Document.open_project(path)
 assert np.array_equal(r.schematic.blocks,d.schematic.blocks) and r.chat_error and r.chat_seed is None
