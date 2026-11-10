"""Standalone v0.6.1 tests; run without Qt or NBT optional dependencies."""
import json
import zipfile
import numpy as np
import pytest
from ship_architect.safety import audit, require_safe
from ship_architect import diagnostics as diagnostics


def test_bounds_never_cross_selected_region():
    shape=(40,20,50)
    bad=np.array([(10*shape[2]+10)*shape[0]+25])
    with pytest.raises(ValueError,match='선택 구역 밖'):
        audit(bad,size_xyz=shape,bounds_lo=(0,0,0),bounds_hi=(20,19,49))


def test_hull_envelope_is_blocked():
    w,h,l=40,20,50
    yy,zz,xx=np.indices((h,l,w))
    boundary=(xx==0)|(xx==w-1)|(yy==0)|(yy==h-1)|(zz==0)|(zz==l-1)
    selection=np.flatnonzero(boundary.reshape(-1))
    check=audit(selection,size_xyz=(w,h,l),bounds_lo=(0,0,0),bounds_hi=(w-1,h-1,l-1))
    assert check['changed']>1_000 and check['hull_risk'] and len(check['document_faces'])==6
    with pytest.raises(ValueError,match='외곽'):
        require_safe(check,approve_mass=True)


def test_large_interior_change_requires_separate_approval():
    w,h,l=40,20,50
    yy,zz,xx=np.indices((h,l,w))
    interior=(xx>0)&(xx<w-1)&(yy>0)&(yy<h-1)&(zz>0)&(zz<l-1)
    selection=np.flatnonzero(interior.reshape(-1))[:6_000]
    check=audit(selection,size_xyz=(w,h,l),bounds_lo=(0,0,0),bounds_hi=(w-1,h-1,l-1))
    assert check['requires_confirmation'] and not check['hull_risk']
    with pytest.raises(ValueError,match='별도 승인'):
        require_safe(check)
    require_safe(check,approve_mass=True)


def test_small_work_has_exact_bbox():
    w,h,l=12,10,14
    positions=np.array([(2*l+3)*w+4, (3*l+5)*w+7],dtype=np.int64)
    result=audit(positions,size_xyz=(w,h,l),bounds_lo=(2,1,2),bounds_hi=(8,5,8))
    assert result['bbox']=={'lo':[4,2,3], 'hi':[7,3,5]}
    assert not result['hull_risk'] and not result['requires_confirmation']


def test_diagnostic_zip_keeps_evidence_but_not_schematic(tmp_path):
    diagnostics.setup(tmp_path/'log')
    trace='aabbccdd99ff0011'
    diagnostics.event('ai_request',trace_id=trace,critical=True,prompt='바닥에 유리 추가',token='MYTOKEN')
    diagnostics.event('ai_raw_response',trace_id=trace,raw='{"hello":"world"}')
    diagnostics.save_change_map(trace,np.array([1,2,3]),np.array([0,0,0]),np.array([1,1,1]))
    dest=tmp_path/'report.zip'
    diagnostics.report(dest)
    with zipfile.ZipFile(dest) as z:
        contents=set(z.namelist())
        assert 'report.json' in contents and 'logs/events.jsonl' in contents
        assert f'diffs/{trace}.npz' in contents
        assert not any(p.endswith(('.schem','.shipproj')) for p in contents)
        events=[json.loads(x) for x in z.read('logs/events.jsonl').decode().splitlines()]
        assert any(e['kind']=='ai_request' and e['prompt']=='바닥에 유리 추가' and e['token']=='[REDACTED]' for e in events)
    second=diagnostics.report(dest)
    assert second!=dest and second.name=='report_1.zip' and second.is_file()
    with zipfile.ZipFile(second) as z:assert z.testzip() is None
