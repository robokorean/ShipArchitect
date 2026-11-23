"""Contextual facility management using the common explorer and exact space overlays."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout
from .facilities import describe, dissolve, save_facility


class FacilityTools:
    def __init__(self, workbench):
        self.b = workbench
        self.w = workbench.owner

    def edit(self, facility_id=None):
        if self.w.operation is not None:
            return
        old = self.w.doc.analysis_state.get('facilities', {}).get(facility_id, {})
        members = old.get('members') or sorted(k for k in self.b.keys_for_scope('group') if k.startswith(('space:', 'candidate:')))
        if not members:
            self.w.statusBar().showMessage('탐색기에서 공간 또는 후보를 선택하세요.')
            return
        dialog = QDialog(self.w)
        dialog.setWindowTitle('시설 그룹 편집' if old else '선택 공간을 시설로 묶기')
        layout = QVBoxLayout(dialog)
        label = QLabel(f'공간·후보 {len(members)}개를 하나의 시설로 관리합니다. 각 공간의 경계와 ID를 유지합니다.')
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        name = QLineEdit(old.get('name', '새 시설'))
        purpose = QLineEdit(old.get('purpose', '미지정'))
        name.setMaxLength(200)
        purpose.setMaxLength(200)
        form.addRow('시설 이름', name)
        form.addRow('사용자가 확인한 용도', purpose)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            key = save_facility(self.w.doc, members, name.text(), purpose.text(), facility_id)
            self.b.space_review.finish_edit()
            self.b.select_key('facility:' + key)
        except ValueError as exc:
            self.w.error(exc)

    def add_selection(self, facility_id):
        if self.w.operation is not None:
            return
        old = self.w.doc.analysis_state.get('facilities', {}).get(facility_id)
        if old is None:
            return
        chosen = {k for k in self.b.keys_for_scope('group') if k.startswith(('space:', 'candidate:'))}
        if not chosen:
            return
        members = set(old['members']) | chosen
        if QMessageBox.question(self.w, '시설에 공간 추가', f'{old["name"]}에 선택 공간을 추가해 {len(members)}개를 관리합니다.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        save_facility(self.w.doc, members, old['name'], old['purpose'], facility_id)
        self.b.space_review.finish_edit()

    def remove(self, facility_id):
        if self.w.operation is not None:
            return
        if QMessageBox.question(self.w, '시설 그룹 해제', '시설의 논리적 묶음을 해제합니다. 포함된 공간과 후보는 유지됩니다.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            dissolve(self.w.doc, facility_id)
            self.b.space_review.finish_edit()

    def summary(self, facility_id):
        facility = self.w.doc.analysis_state['facilities'][facility_id]
        info = describe(self.w.doc, facility)
        return (f'{facility["name"]}\n용도  {facility["purpose"]} · 사용자 확인\n'
                f'구성 공간  {len(info["members"])}개 · 겹침 제외 부피  {info["physical_cells"]:,}셀\n'
                f'재분석 후 찾을 수 없는 참조  {len(info["missing"])}개\n'
                '시설의 연결 모습은 물리적 공간 병합이나 통행 검증을 뜻하지 않습니다.')
