"""Independent, named recognition results scoped to a schematic's block signature."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from .analysis_sessions import block_signature, timestamp
from .analysis_state import validate
from .schematic import atomic_write

LIMIT = 48_000_000


def save(directory, doc, name):
    result = doc.analysis_state.get('last_result')
    if not result:
        raise ValueError('저장할 분석 결과가 없습니다.')
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError('분석 이름을 1~100자로 입력하세요.')
    path = Path(directory) / (uuid4().hex + '.analysis.json')
    meta = {
        'name': name, 'date': timestamp(), 'version': result.get('algorithm_version')
        or result.get('provenance', {}).get('algorithm_version', '확인 불가'),
        'count': len(result['candidates']), 'size': list(doc.schematic.size),
        'signature': result.get('provenance', {}).get('block_signature'),
        'saved_document_signature': block_signature(doc),
    }
    payload = json.dumps({'format':1,'meta':meta,'result':result},ensure_ascii=False).encode('utf8')
    if len(payload)>LIMIT:
        raise ValueError('분석 저장 크기 제한을 초과했습니다.')
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, payload)
    atomic_write(path.with_suffix('.index'), json.dumps(meta,ensure_ascii=False).encode('utf8'))
    return path


def entries(directory):
    for index in sorted(Path(directory).glob('*.analysis.index')):
        try:
            if index.stat().st_size > 8192:
                continue
            meta = json.loads(index.read_text(encoding='utf8'))
            path = index.with_suffix('.json')
            if path.is_file() and all(k in meta for k in ('date','name','version','count')):
                yield path, meta
        except (OSError, ValueError):
            continue


def load(path, doc):
    if path.stat().st_size > LIMIT:
        raise ValueError('분석 저장 크기 제한을 초과했습니다.')
    data = json.loads(path.read_text(encoding='utf8'))
    if data.get('format')!=1 or data['meta']['size']!=list(doc.schematic.size):
        raise ValueError('설계도 크기가 다르거나 지원하지 않는 분석 파일입니다.')
    meta=data['meta']
    signature=meta.get('signature')
    match=None if not signature else signature==block_signature(doc)
    result=validate({'last_result':data['result']},doc.schematic.size)['last_result']
    if match:
        result['block_epoch']=doc.block_epoch
        for candidate in result['candidates']:
            candidate['source_block_epoch']=doc.block_epoch
    result['archive_read_only']=match is not True
    return result, match
