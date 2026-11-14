"""Qt commands for the independent analysis archive."""
from copy import deepcopy

from PySide6.QtWidgets import QInputDialog, QMessageBox

from . import analysis_archive
from .analysis_state import commit


class AnalysisArchive:
    def __init__(self, window):
        self.window = window

    def directory(self):
        return self.window.data_dir/'recognition_archive'

    def save(self):
        w=self.window
        if w.loading or w.operation is not None:
            return
        name,ok=QInputDialog.getText(w,'분석 결과 저장','저장 이름')
        if ok:
            try:
                analysis_archive.save(self.directory(),w.doc,name)
                w.statusBar().showMessage('분석 결과를 별도 보관했습니다.')
            except (OSError, ValueError) as exc:
                w.error(exc)

    def restore(self):
        w=self.window
        if w.loading or w.operation is not None:
            return
        rows=sorted(analysis_archive.entries(self.directory()),key=lambda x:x[1]['date'],reverse=True)
        if not rows:
            w.statusBar().showMessage('별도로 저장한 분석 결과가 없습니다.')
            return
        labels=[f"{i+1}. {m['name']}  |  {m['date']}  |  {m['version']}  |  {m['count']}개"
                for i,(_,m) in enumerate(rows)]
        choice,ok=QInputDialog.getItem(w,'저장 분석 불러오기','이름 | 날짜(UTC) | 분석 버전 | 후보 수',labels,0,False)
        if not ok:
            return
        try:
            result,match=analysis_archive.load(rows[labels.index(choice)][0],w.doc)
            if match is not True and QMessageBox.question(
                    w,'블록 구조 확인','현재 블록 구조와 다르거나 일치를 확인할 수 없습니다. 읽기 전용 후보로 표시할까요?'
            )!=QMessageBox.StandardButton.Yes:
                return
            state=deepcopy(w.doc.analysis_state)
            for key in ('session_reference','session_changes','session_side'):
                state.pop(key,None)
            state['last_result']=result
            commit(w.doc,state,action='recognition_archive_restore')
            w.doc.analysis_display_origin='보관 분석 복원'
            w.space_view.overlay.setChecked(True)
            w.space_view.bulk.attach()
            w.space_tools.refresh()
            w.refresh_history()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            w.error(exc)
