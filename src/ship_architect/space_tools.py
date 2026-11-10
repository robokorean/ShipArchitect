"""Scene context actions and view controls. Deletion changes space metadata only."""
import copy
from PySide6.QtCore import Qt,QObject,QEvent
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import (QWidget,QFormLayout,QSlider,QCheckBox,QComboBox,QMenu,QMessageBox,QInputDialog,QPushButton)
from .space_display import DEFAULTS,visible
from .analysis_state import commit
from .diagnostics import event

class SpaceTools(QObject):
    def __init__(self,owner):
        super().__init__(owner);self.w=owner;self.b=owner.workbench;view=owner.view
        self.panel=QWidget();form=QFormLayout(self.panel);self.normal=QSlider(Qt.Orientation.Horizontal);self.normal.setRange(0,255);self.candidate=QSlider(Qt.Orientation.Horizontal);self.candidate.setRange(0,255);self.geometry=QComboBox();self.geometry.addItems(['실제 복셀 메시','보조 경계 상자']);self.only=QCheckBox('선택 공간 오버레이만');self.show_external=QCheckBox('외부·큰 미분리 영역 표시')
        form.addRow('일반 공간 불투명도 배율',self.normal);form.addRow('인식 후보 투명도',self.candidate);form.addRow('공간 표시',self.geometry);form.addRow(self.only);form.addRow(self.show_external)
        self.normal.setToolTip('0=숨김, 일반 공간은 80=기존 개별 알파, 255=강하게 표시. 인식 후보는 알파값을 직접 적용합니다.');self.candidate.setToolTip(self.normal.toolTip())
        for widget in (self.normal,self.candidate):widget.valueChanged.connect(self.update)
        self.geometry.currentIndexChanged.connect(self.update);self.only.toggled.connect(self.update);self.show_external.toggled.connect(self.update)
        self.b.view_panel.layout().insertWidget(0,self.panel);self.b.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.b.tree.customContextMenuRequested.connect(self.context);self.b.tree.installEventFilter(self)
        self.attach();owner.space_tools=self
    def attach(self):
        cfg=copy.deepcopy(DEFAULTS);cfg.update(self.w.doc.view_settings.get('space_presentation',{}));self.w.view.space_display=cfg
        for widget in (self.normal,self.candidate,self.geometry,self.only,self.show_external):widget.blockSignals(True)
        self.normal.setValue(cfg['normal_alpha']);self.candidate.setValue(cfg['candidate_alpha']);self.geometry.setCurrentIndex(cfg['geometry']=='bounds');self.only.setChecked(cfg['only_selected']);self.show_external.setChecked(cfg['external'])
        for widget in (self.normal,self.candidate,self.geometry,self.only,self.show_external):widget.blockSignals(False)
    def refresh(self):
        w=self.w;w.view.update();w.space_manager.plan.update();self.b.axis_plan.refresh();w.space_manager.refresh();self.b.rebuild_tree(True);self.b.timer.start()
    def update(self,*_):
        self.w.view.space_display.update(normal_alpha=self.normal.value(),candidate_alpha=self.candidate.value(),geometry='bounds' if self.geometry.currentIndex() else 'mesh',only_selected=self.only.isChecked(),external=self.show_external.isChecked());self.refresh()
    def ids(self):return {str(i.data(0,Qt.ItemDataRole.UserRole)).split(':',1)[1] for i in self.b.tree.selectedItems() if str(i.data(0,Qt.ItemDataRole.UserRole)).startswith('space:')}
    def visibility(self,ids,on):
        v=self.w.view.space_display.setdefault('visibility',{})
        for sid in ids:v[sid]=bool(on)
        self.refresh()
    def delete(self,exclude=False,confirm=True,ids=None):
        w=self.w;d=w.doc;ids=self.ids() if ids is None else set(ids)
        if not ids:return
        if w.operation is not None:w.statusBar().showMessage('현재 분석이 끝난 뒤 공간을 삭제하세요.');return
        if confirm and QMessageBox.question(w,'공간 관리 데이터 삭제',f'선택 공간 {len(ids)}개를 삭제합니다. 실제 블록/NBT는 유지됩니다.\n'+('동일 형상 인식 제외도 등록합니다.' if exclude else '재분석 시 다시 등록될 수 있습니다.'),QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        if w.proposal is not None:w.reject()
        state=copy.deepcopy(d.analysis_state)
        for s in d.spaces:
            if s['id'] not in ids:continue
            fp=s.get('recognition',{}).get('source_fingerprint') or s.get('recognition',{}).get('fingerprint')
            if not fp:
                from .spatial3d import fingerprint
                fp=fingerprint(s['runs'])
            if exclude:state.setdefault('decisions',{})[fp]={'action':'exclude'}
            elif state.setdefault('decisions',{}).get(fp,{}).get('action')=='registered':state['decisions'].pop(fp,None)
        for c in state.get('last_result',{}).get('candidates',[]):
            if c.get('existing_space_id') in ids or c.get('fingerprint') in {s.get('recognition',{}).get('fingerprint') for s in d.spaces if s['id'] in ids}:
                c['decision']='exclude' if exclude else 'review';c['confirmed']=False
        commit(d,state,[s for s in d.spaces if s['id'] not in ids],'space_delete_exclude' if exclude else 'space_delete');w.space_view.bulk.attach();w.space_manager.refresh();w.refresh_history();self.b.rebuild_tree(True);w.view.update();self.b.axis_plan.refresh();event('space_metadata_deleted',count=len(ids),exclude=exclude,blocks_changed=0)
    def rename(self):
        ids=self.ids();d=self.w.doc
        if len(ids)!=1:return
        sid=next(iter(ids));sp=next(s for s in d.spaces if s['id']==sid);name,ok=QInputDialog.getText(self.w,'공간 이름 변경','새 이름',text=sp['name'])
        if ok and name.strip():
            spaces=copy.deepcopy(d.spaces);next(s for s in spaces if s['id']==sid)['name']=name.strip()[:200];self.w.space_manager.changed(spaces,'space_rename',sid)
    def context(self,pos):
        item=self.b.tree.itemAt(pos)
        if item is None:return
        if not item.isSelected():self.b.tree.clearSelection();item.setSelected(True);self.b.tree.setCurrentItem(item)
        if not self.ids():return
        menu=QMenu(self.b.tree)
        for label,fn in [('공간 삭제 (재분석 허용)',self.delete),('삭제하고 인식 제외 등록',lambda:self.delete(True)),('이름 변경',self.rename),('숨기기',lambda:self.visibility(self.ids(),False)),('다시 표시',lambda:self.visibility(self.ids(),True)),('선택 공간만 보기',self.isolate_selected)]:
            action=menu.addAction(label);action.triggered.connect(fn)
        menu.exec(self.b.tree.viewport().mapToGlobal(pos))
    def isolate_selected(self):
        self.only.setChecked(True);self.b.isolate()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Delete and self.ids():self.delete();return True
        return False
