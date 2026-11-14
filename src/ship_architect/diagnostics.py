"""Crash-tolerant, opt-in-export developer black box; no project/SCHEm bytes in reports.

Local diagnostic records CAN contain user prompts, AI responses, and plan JSON.
They are stored only on this computer until the user exports a ZIP explicitly.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import traceback
import uuid
import zipfile

APP_VERSION = '0.9.3.4'
MAX_LOG_BYTES = 5 * 1024 * 1024
ROTATIONS = 3
_lock = threading.RLock()
_log_dir: Path | None = None
_session_id = uuid.uuid4().hex[:12]
_secret_key = re.compile(r'(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)', re.I)
_inline_secret = re.compile(r'(?i)(\b(?:authorization|bearer|password|token|api[_-]?key|secret)\s*[:=]\s*)["\']?[^\s,;"\'}]+')
_win_path = re.compile(r'(?i)\b[A-Z]:\\Users\\[^\\\s"\']+(?:\\[^\s"\']+)*')
_unix_home = re.compile(r'/(?:home|Users)/[^/\s"\']+(?:/[^\s"\']+)*')


def _clean(value, name='', depth=0):
    if _secret_key.search(str(name)):
        return '[REDACTED]'
    if depth > 10:
        return '[MAX_DEPTH]'
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        result = re.sub(r'(?i)\bbearer\s+[^\s,;\"\']+', 'Bearer [REDACTED]',value[:100_000])
        result = re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@',r'\1[REDACTED]@',result)
        result = _inline_secret.sub(r'\1[REDACTED]', result)
        result = _win_path.sub('[LOCAL_PATH]', result)
        result = _unix_home.sub('[LOCAL_PATH]', result)
        return result
    if isinstance(value, (list, tuple)):
        return [_clean(x, depth=depth+1) for x in value[:256]]
    if isinstance(value, dict):
        return {str(k)[:100]: _clean(v, k, depth+1) for k, v in list(value.items())[:256]}
    return _clean(str(value), depth=depth+1)


def setup(log_dir):
    """Call once on startup; do not keep any open file descriptors."""
    global _log_dir
    with _lock:
        _log_dir = Path(log_dir)
        _log_dir.mkdir(parents=True, exist_ok=True)
    event('application_start', platform=platform.platform(), python=platform.python_version())


def _rotate(path):
    if path.exists() and path.stat().st_size >= MAX_LOG_BYTES:
        last = path.with_name(path.name + f'.{ROTATIONS}')
        if last.exists(): last.unlink()
        for n in range(ROTATIONS-1, 0, -1):
            src = path.with_name(path.name + f'.{n}')
            if src.exists(): src.replace(path.with_name(path.name + f'.{n+1}'))
        path.replace(path.with_name(path.name + '.1'))


def event(kind, *, trace_id=None, critical=False, **data):
    """Best-effort append-only JSONL. Logging must never prevent normal editing."""
    with _lock:
        if _log_dir is None:
            return
        payload = {'time': datetime.now(timezone.utc).isoformat(), 'version': APP_VERSION,
                   'session': _session_id, 'kind': kind}
        if trace_id: payload['trace_id'] = str(trace_id)
        payload.update({str(k): _clean(v, k) for k,v in data.items()})
        try:
            path = _log_dir / 'events.jsonl'
            _rotate(path)
            with path.open('a', encoding='utf-8') as log:
                log.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + '\n')
                log.flush()
                if critical: os.fsync(log.fileno())
        except (OSError, TypeError, ValueError):
            # Logging is diagnostic and may not interrupt an already safe operation.
            pass


def exception(stage, exc=None, *, trace_id=None):
    if exc is None:
        detail = traceback.format_exc()
    else:
        detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    event('exception', trace_id=trace_id, critical=True, stage=stage, traceback=detail)



def save_change_map(trace_id, indices, before, after):
    """Capture changed linear voxel indices and palette IDs (no NBT or file paths)."""
    if not trace_id or not re.fullmatch(r'[a-f0-9]{8,32}',str(trace_id)):
        return
    try:
        import numpy as np
        with _lock:
            if _log_dir is None:return
            directory=_log_dir/'diffs'
            directory.mkdir(parents=True,exist_ok=True)
            target=directory/(str(trace_id)+'.npz')
            staging=directory/(str(trace_id)+'.npz.tmp')
            with staging.open('wb') as fd:
                np.savez_compressed(fd,indices=np.asarray(indices,dtype=np.int64),
                    before=np.asarray(before,dtype=np.int32),after=np.asarray(after,dtype=np.int32))
                fd.flush();os.fsync(fd.fileno())
            staging.replace(target)
            for stale in sorted(directory.glob('*.npz'),key=lambda p:p.stat().st_mtime,reverse=True)[12:]:
                stale.unlink(missing_ok=True)
        event('change_map_saved',trace_id=trace_id,blocks=len(indices))
    except (OSError,ValueError,TypeError):
        event('change_map_failed',trace_id=trace_id)


def report(destination, *, document=None, ui_state=None):
    """Export a bounded ZIP. No schematic, project bytes, source code or block-entity NBT."""
    target = Path(destination)
    if target.suffix.lower() != '.zip': raise ValueError('진단 보고서는 .zip으로 저장해야 합니다.')
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = {'version':APP_VERSION, 'created_utc':datetime.now(timezone.utc).isoformat(),
            'session':_session_id, 'note':'로컬 AI 입력과 응답 기록을 포함할 수 있으므로 공유 전 확인하세요.',
            'excludes':['.schem', '.shipproj', 'original NBT', 'source files']}
    if document is not None:
        sc = document.schematic
        meta['document']={'size_xyz':list(sc.size), 'revision':int(document.revision),
                          'selection':{'lo':list(document.selection.lo),'hi':list(document.selection.hi)},
                          'history_length':len(document.history)}
    if ui_state is not None:meta['ui']=_clean(ui_state)
    with _lock:
        detail_paths=[] if _log_dir is None else sorted((_log_dir/'evidence').glob('*.jsonl.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
        paths = [] if _log_dir is None else [p for p in [_log_dir/'events.jsonl', *(_log_dir/f'events.jsonl.{n}' for n in range(1,ROTATIONS+1))] if p.is_file()]
        diff_paths = [] if _log_dir is None else sorted((_log_dir/'diffs').glob('*.npz')) if (_log_dir/'diffs').exists() else []
        # Exclusive-create, no silent overwrites. Each rotated log is capped by max size.
        requested=target
        for serial in range(10000):
            candidate=requested if serial==0 else requested.with_name(requested.stem+f'_{serial}'+requested.suffix)
            try:
                fd=candidate.open('xb');target=candidate;break
            except FileExistsError:continue
        else:raise OSError('진단 파일 이름을 확보하지 못했습니다.')
        with fd, zipfile.ZipFile(fd, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('report.json', json.dumps(meta, ensure_ascii=False, indent=2))
            z.writestr('SUMMARY.txt','Ship Architect '+APP_VERSION+' 진단\nreport.json: 문서/UI 요약\nevidence/*.jsonl.gz: 실패·제외·미분류를 포함한 작업별 상세(JSON Lines, gzip)\n각 상세의 마지막 줄에 기록 수·잘림 여부가 있습니다. 상한을 넘으면 일부 기록은 제외되며 포함/제외 목록을 확인하세요.\n모드 장치 작동은 추정하지 않습니다. 원본 설계도/NBT 및 인증정보는 포함하지 않습니다. AI 입력은 공유 전에 확인하세요.\n')
            budget=32*1024**2;included=[];omitted=[]
            for p in detail_paths:
                if p.stat().st_size>budget:omitted.append({'name':p.name,'reason':'32MiB export evidence budget'});continue
                z.write(p,arcname='evidence/'+p.name);budget-=p.stat().st_size;included.append(p.name)
            z.writestr('evidence_index.json',json.dumps({'included':included,'omitted':omitted,'budget_bytes':32*1024**2},ensure_ascii=False,indent=2))
            if document is not None:
                state=getattr(document,'analysis_state',{})
                z.writestr('analysis_summary.json',json.dumps({'block_epoch':getattr(document,'block_epoch',0),'equipment_rules':len(state.get('equipment_rules',{})),'space_rules':len(state.get('space_rules',[])),'decisions':len(state.get('decisions',{})),'last_result':{k:v for k,v in state.get('last_result',{}).items() if k not in ('candidates','shell_face_runs')}},ensure_ascii=False))
                if state.get('session_reference'):
                    reference=state['session_reference']
                    z.writestr('analysis_session_comparison.json',json.dumps(_clean({
                        'side':state.get('session_side'),
                        'reference_method':reference.get('method'),
                        'reference_algorithm_version':reference.get('algorithm_version'),
                        'reference_provenance':reference.get('provenance'),
                        'changes':state.get('session_changes',[]),
                    }),ensure_ascii=False))
                comparison=state.get('comparison')
                if comparison:
                    z.writestr('recognition_comparison.json',json.dumps(_clean({'scope':comparison['scope'],'matches':comparison['matches'],'old_only_ids':comparison.get('old_only_ids',[]),'new_only_ids':comparison.get('new_only_ids',[]),'old':{k:v for k,v in comparison['old'].items() if k not in ('candidates','shell_face_runs')},'new':{k:v for k,v in comparison['new'].items() if k not in ('candidates','shell_face_runs')} }),ensure_ascii=False,indent=2))
                with z.open('analysis_rules.jsonl','w') as fd:
                    for key,value in state.get('equipment_rules',{}).items():fd.write((json.dumps(_clean({'type':'equipment','id':key,'category':value}),ensure_ascii=False)+'\n').encode())
                    for rule in state.get('space_rules',[]):fd.write((json.dumps(_clean({'type':'space','rule':rule}),ensure_ascii=False)+'\n').encode())
                    for key,value in state.get('decisions',{}).items():
                        fd.write((json.dumps(_clean({'type':'decision','fingerprint':key,**{k:v for k,v in value.items() if k!='runs'}}),ensure_ascii=False)+'\n').encode())
                        for run in value.get('runs',[]):fd.write((json.dumps({'fingerprint':key,'run':run})+'\n').encode())
            if document is not None:
                # Full exact space geometry, streamed as separate sanitized rows; no NBT.
                with z.open('spaces_confirmed.jsonl','w') as fd:
                    for space in document.spaces:
                        fd.write((json.dumps(_clean({k:v for k,v in space.items() if k!='runs'}),ensure_ascii=False)+'\n').encode())
                        for run in space['runs']:fd.write((json.dumps({'space_id':space['id'],'run':run})+'\n').encode())
            for p in paths:
                # Re-sanitize old logs too: older versions may have weaker redaction.
                lines=[]
                for line in p.read_text(encoding='utf8',errors='replace').splitlines():
                    try:lines.append(json.dumps(_clean(json.loads(line)),ensure_ascii=False))
                    except ValueError:lines.append(json.dumps({'invalid_log_record':True,'reason':'malformed JSON omitted'}))
                z.writestr('logs/'+p.name,'\n'.join(lines)+'\n')
            for p in diff_paths:
                if p.stat().st_size<=10*1024*1024:
                    z.write(p, arcname='diffs/'+p.name)
    event('diagnostic_report_exported', count=len(paths), maps=len(diff_paths))
    return target


def hook_unhandled():
    prior_sys = sys.excepthook
    def handle(typ, val, tb):
        event('unhandled_exception', critical=True, traceback=''.join(traceback.format_exception(typ,val,tb)))
        prior_sys(typ,val,tb)
    sys.excepthook = handle
    if hasattr(threading, 'excepthook'):
        previous = threading.excepthook
        def thread_handle(args):
            event('thread_exception', critical=True, traceback=''.join(traceback.format_exception(args.exc_type,args.exc_value,args.exc_traceback)))
            previous(args)
        threading.excepthook = thread_handle

# Structured evidence is kept separately from the compact, rotated event stream.
# Records are individually sanitized, so coordinates/runs are not lost to list truncation.
def evidence(kind, summary, records=(), *, trace_id=None):
    import gzip
    trace_id=trace_id or uuid.uuid4().hex
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',kind):raise ValueError('diagnostic category')
    try:
        with _lock:
            if _log_dir is None:return trace_id
            directory=_log_dir/'evidence';directory.mkdir(exist_ok=True)
            path=directory/(kind+'-'+trace_id+'.jsonl.gz');staging=path.with_suffix('.tmp');count=0;size=0;truncated=False
            with gzip.open(staging,'wt',encoding='utf8') as f:
                f.write(json.dumps({'kind':kind,'trace_id':trace_id,'summary':_clean(summary)},ensure_ascii=False)+'\n')
                for record in records:
                    line=json.dumps(_clean(record),ensure_ascii=False,allow_nan=False)+'\n';size+=len(line.encode())
                    if size>24*1024**2:truncated=True;break
                    f.write(line);count+=1
                f.write(json.dumps({'records_written':count,'detail_truncated':truncated,'uncompressed_bytes':size})+'\n')
            staging.replace(path)
            for old in sorted(directory.glob(kind+'-*.jsonl.gz'),key=lambda x:x.stat().st_mtime,reverse=True)[3:]:old.unlink(missing_ok=True)
        event(kind+'_evidence',trace_id=trace_id,summary=summary,records=count,detail_truncated=truncated)
    except Exception as e:event('evidence_write_failed',trace_id=trace_id,stage=kind,error=str(e))
    return trace_id
