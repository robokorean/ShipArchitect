"""One-click analysis, contextual review, and approved metadata edits."""
from copy import deepcopy
import json

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout)

from .analysis_state import commit
from .space_display import alpha, hidden_reason
from .space_edits import combined, divided, volume
from .space_organization import LABELS, VISIBLE_CLASSES, prune_memberships, validate_memberships

DISPLAY_LABELS = {'exact':'정밀 표시 중','simplified':'간략 형상 표시 중',
                  'summary':'요약점 표시 · 선택하면 정밀 메시 요청',
                  'pending':'메시 준비 중','failed':'메시 생성 실패 · 진단 요약 참조',
                  'outside':'현재 화면 밖 · F로 이동','clip':'층 또는 단면 밖',
                  'covered':'외벽에 가림 · 단면·내부 관찰에서 외벽 너머 표시 사용',
                  'off_marker':'화면 밖 요약점 · F로 이동'}


class SpaceReview:
    def __init__(self, workbench):
        self.b=workbench
        self.w=workbench.owner
        self.note=QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        self.reveal=QPushButton('이 공간 임시 보기')
        self.reveal.clicked.connect(self.reveal_selected)
        self.reveal.hide()
        layout=self.b.editor_dock.widget().layout()
        layout.insertWidget(1,self.note)
        layout.insertWidget(2,self.reveal)
        self.w.view.space_review=self
        self.update_selection(self.b.selection_key)

    def compare_selected(self):
        from .space_boundaries import boundary_relation
        keys=sorted(k for k in self.b.keys_for_scope('group') if k.startswith(('space:','candidate:')))
        if len(keys)!=2:
            self.w.statusBar().showMessage('Ctrl로 공간 또는 후보 두 개를 선택하세요.')
            return
        report=boundary_relation(*(self.b.records[key] for key in keys))
        self.b.summary_properties.setPlainText(
            f"공간 경계 비교\n{keys[0]}\n{keys[1]}\n"
            f"겹침 {report['overlap_cells']:,}셀 · 공유 공기 경계 {report['shared_air_faces']:,}면\n"
            '각 공간의 복셀 ID와 경계를 유지합니다. 이동 가능성은 연결 분석에서 확인하세요.')
        self.b.inspector.setPlainText(json.dumps(report,ensure_ascii=False,indent=2))
        from .diagnostics import event
        event('space_boundary_comparison',**report)
        self.b.editor_dock.show()
        self.b.editor_dock.raise_()

    def analyze(self):
        if self.w.loading or self.w.operation is not None:
            return
        self.w.space_view.bulk.scan(False,False,True,engine_override='precision',organize=True)

    def state(self,key):
        record=self.b.records.get(key)
        if not record:
            return '현재 분석에서 후보를 찾을 수 없음'
        candidate=key.startswith('candidate:')
        view=self.w.view
        if getattr(view,'temporary_space',None)!=key:
            reason=hidden_reason(view,record,candidate)
            if reason:return reason
            if candidate and not self.w.space_view.overlay.isChecked():return '후보 레이어 꺼짐'
            if not candidate and not view.show_spaces:return '확정 공간 레이어 꺼짐'
            if not candidate and view.exact_space_only and record['id']!=view.doc.active_space_id:
                return '다른 확정 공간만 표시'
        if not alpha(view,record,candidate) and key!=getattr(view,'temporary_space',None):
            return '불투명도 0'
        state=view.space_renderer.display_states.get(key)
        return DISPLAY_LABELS.get(state,'현재 뷰에 아직 그려지지 않음')

    def update_selection(self,key):
        space=bool(key and key.startswith(('space:','candidate:')))
        self.note.setVisible(space)
        self.reveal.setVisible(space and key in self.b.records and not self.b.record_visible(key))
        if space:
            self.note.setText(self.state(key))
        self.b.space_properties_controls.setVisible(bool(key and key.startswith('space:')))

    def reveal_selected(self):
        key=self.b.selection_key
        if key in self.b.records and key.startswith(('candidate:','space:')):
            self.w.view.temporary_space=key
            self.b.refresh_selection_overlay()
            self.update_selection(key)
            self.w.statusBar().showMessage('선택한 공간만 임시 표시했습니다. 다른 항목을 선택하면 해제됩니다.',5000)

    def filter_dialog(self):
        view=self.w.view
        cfg=view.space_display
        dialog=QDialog(self.w)
        dialog.setWindowTitle('공간 표시 필터')
        layout=QVBoxLayout(dialog)
        form=QFormLayout()
        layout.addLayout(form)
        grouped=QCheckBox('주요 공간 우선 · 부속 및 잔여 그룹 정리')
        grouped.setChecked(cfg.get('organized',False))
        size=QSpinBox()
        size.setRange(0,1_000_000)
        size.setValue(cfg.get('min_volume',0))
        use=QComboBox()
        for key,title in {'all':'모든 활용 분류',**LABELS}.items():
            use.addItem(title,key)
        use.setCurrentIndex(max(0,use.findData(cfg.get('use_filter','all'))))
        form.addRow(grouped)
        form.addRow('후보 최소 부피 (셀)',size)
        form.addRow('후보 활용 분류',use)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            selected=use.currentData()
            cfg.update(organized=grouped.isChecked() or size.value()>0 or selected!='all',
                       min_volume=size.value(),use_filter=selected)
            self.b.rebuild_tree(True)
            view.update()
            self.b.axis_plan.refresh()
            self.b.save_display()

    def assign(self, children, parent):
        d=self.w.doc
        classes=self.w.view.space_classes
        if not children or any(key not in classes for key in children):
            raise ValueError('분류할 후보를 선택하세요.')
        if parent is not None and (parent in children or parent not in self.b.records or
                parent not in classes and not parent.startswith('space:') or
                parent in classes and classes[parent].kind not in VISIBLE_CLASSES):
            raise ValueError('상위 항목은 확정 공간 또는 독립 공간 후보여야 합니다.')
        state=deepcopy(d.analysis_state)
        mapping=state.setdefault('space_memberships',{})
        mapping.update(dict.fromkeys(children,parent))
        validate_memberships(mapping)
        commit(d,state,action='space_group_membership')
        self.w.view.space_display['organized']=True
        self.finish_edit()

    def change_membership(self):
        children={key for key in self.b.keys_for_scope('selected') if key.startswith('candidate:')}
        if not children or self.w.operation is not None:
            return
        choices={'독립 검토 항목으로 분리':None}
        for key,item in self.b.records.items():
            entry=self.w.view.space_classes.get(key)
            if key not in children and (key.startswith('space:') or entry and entry.kind in VISIBLE_CLASSES):
                choices[self.b.nodes[key].text(0)+' · '+key.split(':',1)[1]]=key
        choice,ok=QInputDialog.getItem(self.w,'공간 소속 변경','상위 공간',list(choices),0,False)
        if ok:
            try:self.assign(children,choices[choice])
            except ValueError as exc:self.w.error(exc)

    def _approve(self, title, text, shapes):
        view=self.w.view
        old=view.validation_spaces
        view.validation_spaces=[dict(s,color=c,opacity=145,_edit_preview=True) for s,c in shapes]
        view.update()
        self.b.open_tab(view)
        try:
            return QMessageBox.question(self.w,title,text,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,
                                        QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Yes
        finally:
            view.validation_spaces=old
            view.update()

    def merge(self):
        d=self.w.doc
        ids={key[6:] for key in self.b.keys_for_scope('selected') if key.startswith('space:')}
        if self.w.operation is not None:
            return
        try:
            result=combined(d,ids,d.active_space_id)
            if not self._approve('공간 합치기',f'{len(ids)}개를 빈 복셀 {volume(result["runs"]):,}셀로 합칩니다. 색 표시로 결과를 확인하세요.',[(result,'#efc46d')]):
                return
            removed=ids-{result['id']}
            state=deepcopy(d.analysis_state)
            prune_memberships(state,{'space:'+sid for sid in removed},'space:'+result['id'])
            for c in state.get('last_result',{}).get('candidates',[]):
                for field in ('existing_space_id','registered_space_id'):
                    if c.get(field) in removed:c[field]=result['id']
            spaces=[result if s['id']==result['id'] else s for s in d.spaces if s['id'] not in removed]
            self.w.reject()
            commit(d,state,spaces,'space_merge',active=result['id'])
            self.finish_edit()
        except ValueError as exc:
            self.w.error(exc)

    def split(self,bounds=None):
        d=self.w.doc
        space=d.active_space()
        if space is None or self.w.operation is not None:
            return
        try:
            from .model import Bounds
            from .space_model import bbox
            bounds=bounds or d.selection
            if self.w.space_manager.all_height.isChecked():
                lo,hi=bbox(space)
                bounds=Bounds((bounds.lo[0],lo[1],bounds.lo[2]),(bounds.hi[0],hi[1],bounds.hi[2]))
            first,second=divided(d,space,bounds)
            if not self._approve('공간 분할',f'파랑 {volume(first["runs"]):,}셀과 금색 {volume(second["runs"]):,}셀로 나눕니다.',
                                 [(first,'#6ab5e0'),(second,'#efc46d')]):
                return
            state=deepcopy(d.analysis_state)
            from .facilities import inherit_split
            inherit_split(state,'space:'+first['id'],'space:'+second['id'])
            result=[first if s['id']==space['id'] else s for s in d.spaces]+[second]
            self.w.reject()
            commit(d,state,result,'space_split',active=first['id'])
            self.finish_edit()
        except ValueError as exc:
            self.w.error(exc)

    def finish_edit(self):
        self.w.doc.save_empty_spaces=True
        self.w.space_view.bulk.attach()
        self.w.space_manager.refresh()
        self.w.space_manager.autosave.start()
        self.w.refresh_history()
        self.w.update_summary()
        self.b.rebuild_tree(True)
        self.b.axis_plan.refresh()
        self.w.view.update()
        self.b.save_display()
