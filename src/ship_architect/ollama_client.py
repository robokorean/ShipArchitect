"""Local-only Ollama HTTP client. No shell, remote redirects, model pulls or tools.
Generation streams progress and supports cancelling even during model prefill.
"""
from __future__ import annotations
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit
from .chunks import Cancelled,checkpoint


class OllamaError(Exception):
    pass


def endpoint(value):
    parsed=urlsplit(value.strip())
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise OllamaError('Ollama 주소는 http://127.0.0.1:11434 같은 로컬 HTTP 주소만 지원합니다.')
    try:port=parsed.port or 11434
    except ValueError as e:raise OllamaError('잘못된 포트입니다.') from e
    return parsed.hostname,port


class OllamaClient:
    def __init__(self,url='http://127.0.0.1:11434'):
        self.host,self.port=endpoint(url)
        self.url=url

    def _request(self,path,payload=None,timeout=3,cancel=None,progress=None):
        checkpoint(cancel)
        connection=http.client.HTTPConnection(self.host,self.port,timeout=min(3,timeout))
        stop=threading.Event();reason=[];active=[]
        deadline=time.monotonic()+timeout
        def watch():
            while not stop.wait(.05):
                if (cancel and cancel()) or time.monotonic()>deadline:
                    reason.append('cancel' if cancel and cancel() else 'timeout')
                    if active:
                        try:active[0].shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                    return
        watcher=threading.Thread(target=watch,daemon=True)
        watcher.start()
        try:
            connection.connect();active.append(connection.sock)
            checkpoint(cancel)
            connection.sock.settimeout(timeout)
            body=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
            connection.request('POST' if body is not None else 'GET',path,body,{'Content-Type':'application/json','Connection':'close'})
            response=connection.getresponse()
            if response.status!=200:
                error=response.read(16384).decode('utf-8','replace')
                raise OllamaError(f'Ollama HTTP {response.status}: {error[:500]}')
            if payload is None:
                data=response.read(1024*1024+1)
                if len(data)>1024*1024:raise OllamaError('Ollama 상태 응답이 너무 큽니다.')
                return json.loads(data)
            text=[];received=0;done=False;stats={}
            while True:
                checkpoint(cancel)
                line=response.readline(65537)
                if len(line)>65536:raise OllamaError('Ollama 응답 줄이 너무 깁니다.')
                if not line:break
                received+=len(line)
                if received>2*1024*1024:raise OllamaError('Ollama 응답 크기 제한 초과')
                chunk=json.loads(line)
                if not isinstance(chunk,dict):raise OllamaError('잘못된 Ollama 응답입니다.')
                if chunk.get('error'):raise OllamaError(str(chunk['error'])[:500])
                message=chunk.get('message',{})
                if not isinstance(message,dict):raise OllamaError('잘못된 Ollama 메시지입니다.')
                fragment=message.get('content','')
                if not isinstance(fragment,str):raise OllamaError('잘못된 Ollama 메시지입니다.')
                text.append(fragment)
                if progress:progress(min(95,len(''.join(text))//30),f'AI 설계 생성 중 · {sum(map(len,text)):,}자')
                if chunk.get('done'):
                    done=True
                    stats={k:chunk[k] for k in ('total_duration','load_duration','eval_count','prompt_eval_count','done_reason') if k in chunk}
                    break
            if not done:raise OllamaError('AI 응답이 완료되기 전에 연결이 끊겼습니다.')
            return ''.join(text),stats
        except (OSError,ValueError,http.client.HTTPException) as e:
            if reason:
                if reason[0]=='cancel':raise Cancelled('AI 요청을 취소했습니다.') from e
                raise OllamaError('AI 요청 시간 초과. 수동 편집은 계속 사용할 수 있습니다.') from e
            checkpoint(cancel)
            if isinstance(e,TimeoutError) or time.monotonic()>=deadline:raise OllamaError('AI 요청 시간 초과') from e
            raise OllamaError(f'Ollama 통신 실패: {e}') from e
        finally:
            stop.set();connection.close();watcher.join(.2)
            if reason and reason[0]=='cancel':raise Cancelled('AI 요청을 취소했습니다.')
            if reason and reason[0]=='timeout':raise OllamaError('AI 요청 시간 초과')

    def models(self,cancel=None):
        result=self._request('/api/tags',timeout=2,cancel=cancel)
        if not isinstance(result,dict) or not isinstance(result.get('models',[]),list):raise OllamaError('잘못된 Ollama 모델 목록입니다.')
        names=[]
        for row in result.get('models',[]):
            if not isinstance(row,dict):continue
            name=row.get('name',row.get('model',''))
            if isinstance(name,str) and 0<len(name)<=128:names.append(name)
        return sorted(set(names))

    def chat(self,model,messages,schema,timeout=180,cancel=None,progress=None):
        if not isinstance(model,str) or not model.strip() or len(model)>128:raise OllamaError('모델 이름을 확인하세요.')
        payload={'model':model,'messages':messages,'format':schema,'stream':True,'think':False,
            'keep_alive':'5m','options':{'temperature':0.15,'num_ctx':8192,'num_predict':4096}}
        return self._request('/api/chat',payload,timeout,cancel,progress)
