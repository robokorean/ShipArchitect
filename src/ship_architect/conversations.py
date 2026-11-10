"""Project-scoped SQLite chat journal; independent from block-edit transactions.
Exact diff arrays are compressed without pickle; portable project/chat exports include them.
"""
import base64,io,json,sqlite3,threading,uuid
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

def now():return datetime.now(timezone.utc).isoformat()

class ConversationStore:
    def __init__(self,path):
        if str(path)!=':memory:':Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(str(path),timeout=1,check_same_thread=False)
        self.connection.execute('PRAGMA journal_mode=WAL');self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS chats(project TEXT,trace TEXT,updated TEXT,payload TEXT,diff BLOB,PRIMARY KEY(project,trace))')
        self.connection.commit();self.lock=threading.RLock();self.generation=0;self.session=uuid.uuid4().hex

    def close(self):
        with self.lock:
            if not getattr(self,'closed',False):self.connection.close();self.closed=True

    def __del__(self):
        try:self.close()
        except Exception:pass

    def start(self,project,trace,prompt,selection,**fields):
        entry=dict(trace_id=trace,project_id=project,created=now(),updated=now(),prompt=prompt,selection=selection,
                   status='running',steps=[],responses=[],session=self.session,**fields)
        self._put(entry);self.patch(project,trace,status='running');return entry

    def _put(self,entry,diff=None):
        for key in ('project_id','trace_id','created','updated','prompt','status'):
            if type(entry.get(key)) is not str:raise ValueError('잘못된 대화 필드: '+key)
        if len(entry['prompt'])>4000 or len(entry['trace_id'])>64 or len(entry['project_id'])>128:raise ValueError('대화 필드 길이 제한 초과')
        if type(entry.get('selection')) is not dict or any(len(entry['selection'].get(k,[]))!=3 for k in ('lo','hi')):raise ValueError('대화 구역 형식 오류')
        text=json.dumps(entry,ensure_ascii=False,allow_nan=False)
        if len(text.encode())>1_000_000:raise ValueError('대화 항목 크기 제한 초과')
        with self.lock,self.connection:
            self.connection.execute('INSERT INTO chats VALUES(?,?,?,?,?) ON CONFLICT(project,trace) DO UPDATE SET updated=excluded.updated,payload=excluded.payload,diff=COALESCE(excluded.diff,chats.diff)',
                                    (entry['project_id'],entry['trace_id'],entry['updated'],text,diff))
            self.generation+=1

    def get(self,project,trace):
        with self.lock:
            row=self.connection.execute('SELECT payload FROM chats WHERE project=? AND trace=?',(project,trace)).fetchone()
        return json.loads(row[0]) if row else None

    def patch(self,project,trace,**fields):
        with self.lock:
            entry=self.get(project,trace)
            if not entry:return
            if 'response' in fields:entry['responses'].append(fields.pop('response'))
            if 'status' in fields:entry['steps'].append({'time':now(),'status':fields['status'],'detail':fields.get('error',fields.get('reason',''))})
            entry.update(fields);entry['updated']=now();self._put(entry)

    def changes(self,project,trace,proposal,palette,size):
        stream=io.BytesIO();np.savez_compressed(stream,indices=proposal.indices,before=proposal.before,after=proposal.after if proposal.after is not None else np.full(len(proposal.indices),proposal.target,np.int32))
        with self.lock:
            entry=self.get(project,trace)
            if not entry:return
            entry.update(diff_format='npz-base64-v1',diff_palette={str(i):s for i,s in palette.items()},size_xyz=list(size),updated=now())
            self._put(entry,stream.getvalue())

    def entries(self,project,query='',limit=500):
        with self.lock:
            rows=self.connection.execute('SELECT payload FROM chats WHERE project=? AND payload LIKE ? ORDER BY updated DESC LIMIT ?',
                    (project,'%'+query+'%',limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def bundle(self,project):
        with self.lock:
            rows=self.connection.execute('SELECT payload,diff FROM chats WHERE project=? ORDER BY updated',(project,)).fetchall()
        entries=[]
        for payload,diff in rows:
            entry=json.loads(payload)
            if diff:entry['change_map_npz_base64']=base64.b64encode(diff).decode('ascii')
            entries.append(entry)
        result={'format':'ship-architect-chat','version':1,'project_id':project,'entries':entries}
        if len(json.dumps(result).encode())>64_000_000:raise ValueError('대화 내보내기/프로젝트 포함 크기는 64MB 이하여야 합니다.')
        return result

    def restore(self,bundle):
        if not isinstance(bundle,dict) or bundle.get('format')!='ship-architect-chat' or bundle.get('version')!=1:raise ValueError('대화 형식 오류')
        project=bundle['project_id']
        if not isinstance(bundle.get('entries'),list) or len(bundle['entries'])>10000:raise ValueError('대화 수 제한 초과')
        for item in bundle['entries']:
            item=dict(item)
            if item.get('project_id')!=project:raise ValueError('대화 프로젝트 ID 불일치')
            blob=item.pop('change_map_npz_base64',None);old=self.get(project,item['trace_id'])
            if old and old['updated']>item['updated']:continue
            data=base64.b64decode(blob,validate=True) if blob else None
            if data and len(data)>8_000_000:raise ValueError('변경 맵 크기 제한 초과')
            self._put(item,data)

    def recover(self,project):
        for entry in self.entries(project,limit=10000):
            if entry.get('session')!=self.session:
                if entry['status']=='running':self.patch(project,entry['trace_id'],status='interrupted',reason='이전 실행에서 완료되지 않은 요청')
                elif entry['status']=='preview':self.patch(project,entry['trace_id'],status='preview_expired',reason='이전 미승인 제안 · 다시 생성 후 검토해야 합니다.')

    def export(self,project,path,kind):
        from .schematic import atomic_write
        if Path(path).suffix.lower()!='.'+kind:raise ValueError('대화 내보내기는 .txt 또는 .json 확장자를 사용하세요.')
        bundle=self.bundle(project)
        if kind=='json':text=json.dumps(bundle,ensure_ascii=False,indent=2)
        elif kind=='txt':
            lines=['Ship Architect 대화 기록 · 프로젝트 '+project]
            for e in bundle['entries']:
                lines += ['',e['created']+' | '+e['trace_id']+' | '+e['status'],'사용자: '+e['prompt'],'선택 구역: '+json.dumps(e['selection'],ensure_ascii=False)]
                for response in e.get('responses',[]):lines += ['AI 제안(미실행 원문): '+response]
                lines += ['Python 검증/실제 결과: '+json.dumps(e.get('actual',{}),ensure_ascii=False), '상태 이력: '+json.dumps(e.get('steps',[]),ensure_ascii=False)]
                if e.get('error'):lines += ['오류: '+e['error']]
            text='\n'.join(lines)+'\n정확한 좌표별 변경 맵은 JSON 내보내기에 포함됩니다.\n'
        else:raise ValueError('TXT/JSON만 지원합니다.')
        atomic_write(Path(path),text.encode('utf-8'));return Path(path)
