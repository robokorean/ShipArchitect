"""Readable explorer details and an explicit metadata-only batch edit."""
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                               QLabel, QLineEdit, QSpinBox, QVBoxLayout)

from .space_model import bbox


def format_record(record, title, space_names, reasons):
    result = [title]
    runs = record.get('runs', record.get('volume_runs'))
    if runs:
        low, high = (record['lo'],record['hi']) if 'lo' in record and 'hi' in record else bbox({'runs':runs})
        volume = record.get('volume')
        if volume is None:
            volume = sum(b-a+1 for _,_,a,b in runs)
        result.extend((f'층  Y {low[1]} ~ {high[1]}',f'범위  {tuple(low)} → {tuple(high)}',f'부피  {volume:,}셀'))
    if record.get('purpose'):
        result.extend((f"용도  {record['purpose']}",f"색상  {record.get('color','기본')}   불투명도  {record.get('opacity',80)}/255"))
    if record.get('position') is not None:
        result.append(f"좌표  {tuple(record['position'])}")
    if record.get('device_kind'):
        result.append('장치 구분  ' + record['device_kind'])
    if record.get('candidate_spaces') or record.get('adjacent_candidates'):
        result.append('분석 후보  ' + ', '.join(record.get('candidate_spaces',[]) + record.get('adjacent_candidates',[])))
    if record.get('category'):
        result.append(f"설비 종류  {record['category']}")
    state = {'auto':'등록 가능','review':'검토 필요','exclude':'제외','registered':'등록됨'}.get(record.get('decision'),record.get('status'))
    if state:
        result.append('상태  ' + state)
    proof = record.get('structure_evidence',{})
    if 'floor_y' in proof and 'roof_y' in proof:
        result.append(f"구조  관측 바닥 Y={proof['floor_y']} · 천장 Y={proof['roof_y']}")
    if record.get('reasons'):
        result.append('근거  ' + ' · '.join(reasons.get(reason, reason) for reason in record['reasons'][:3]))
    for title, field in (('소속','spaces'), ('인접','adjacent_spaces')):
        ids=record.get(field,[])
        if ids and all(isinstance(item,str) for item in ids):
            result.append(title + '  ' + ', '.join(space_names.get(item,item) for item in ids[:8]))
    fragment=record.get('fragment',{})
    if fragment.get('suggested_owner_id'):
        result.append(f"조각 근거  공유면 {fragment.get('shared_faces',0)}개")
    return '\n'.join(result)


def apply_fields(spaces, ids, changes):
    supported={'name','purpose','color','opacity'}
    if not set(changes) <= supported:
        raise ValueError('이 기능은 등록 공간의 표시 속성만 편집합니다.')
    return [dict(space,**changes) if space['id'] in ids else space for space in spaces]


def edit_batch(b):
    ids = {k[6:] for k in b.keys_for_scope('group') if k.startswith('space:')}
    if len(ids) < 2:
        return
    owner=b.owner
    dialog=QDialog(owner)
    dialog.setWindowTitle('확정 공간 일괄 속성')
    layout=QVBoxLayout(dialog)
    layout.addWidget(QLabel(f'확정 공간 {len(ids):,}개 · 체크한 항목을 한 번에 변경합니다.'))
    form=QFormLayout()
    layout.addLayout(form)
    fields={}
    for key,title in (('name','이름'),('purpose','용도'),('color','색상 (#RRGGBB)'),('opacity','불투명도')):
        enabled=QCheckBox(title)
        editor=QSpinBox() if key=='opacity' else QLineEdit()
        if key=='opacity':
            editor.setRange(0,255)
            editor.setValue(80)
        fields[key]=(enabled,editor)
        form.addRow(enabled,editor)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec()!=QDialog.DialogCode.Accepted:
        return
    changes={k:(w.value() if k=='opacity' else w.text().strip()) for k,(selected,w) in fields.items() if selected.isChecked()}
    if changes:
        owner.space_manager.changed(apply_fields(owner.doc.spaces,ids,changes),'space_batch_attributes',owner.doc.active_space_id)
