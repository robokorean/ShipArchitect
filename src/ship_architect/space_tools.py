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
        self.mode=QComboBox()
        for label,key in (('확정 공간 + 후보','both'),('확정 공간만','confirmed'),
                          ('신규 후보만','candidates'),('변경 후보만','changed'),('모든 오버레이 숨김','none')):
            self.mode.addItem(label,key)
        self.mode.currentIndexChanged.connect(self.update)
        form.addRow('표시 대상',self.mode)
        form.addRow('일반 공간 불투명도 배율',self.normal);form.addRow('인식 후보 투명도',self.candidate);form.addRow('공간 표시',self.geometry);form.addRow(self.only);form.addRow(self.show_external)
        self.normal.setToolTip('0=숨김, 일반 공간은 80=기존 개별 알파, 255=강하게 표시. 인식 후보는 알파값을 직접 적용합니다.');self.candidate.setToolTip(self.normal.toolTip())
        for widget in (self.normal,self.candidate):widget.valueChanged.connect(self.update)
        self.geometry.currentIndexChanged.connect(self.update);self.only.toggled.connect(self.update);self.show_external.toggled.connect(self.update)
        self.b.view_panel.layout().insertWidget(0,self.panel);self.b.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.b.tree.customContextMenuRequested.connect(self.context);self.b.tree.installEventFilter(self)
        self.attach();owner.space_tools=self
    def attach(self):
        cfg=copy.deepcopy(DEFAULTS);cfg.update(self.w.doc.view_settings.get('space_presentation',{}));self.w.view.space_display=cfg
        for widget in (self.normal,self.candidate,self.geometry,self.only,self.show_external,self.mode):widget.blockSignals(True)
        self.normal.setValue(cfg['normal_alpha']);self.candidate.setValue(cfg['candidate_alpha']);self.geometry.setCurrentIndex(cfg['geometry']=='bounds');self.only.setChecked(cfg['only_selected']);self.show_external.setChecked(cfg['external']);self.mode.setCurrentIndex(max(0,self.mode.findData(cfg['mode'])))
        for widget in (self.normal,self.candidate,self.geometry,self.only,self.show_external,self.mode):widget.blockSignals(False)
    def refresh(self):
        w=self.w;w.view.update();w.space_manager.plan.update();self.b.axis_plan.refresh();w.space_manager.refresh();self.b.rebuild_tree(True);self.b.timer.start()
    def update(self,*_):
        self.w.view.space_display.update(mode=self.mode.currentData(),normal_alpha=self.normal.value(),candidate_alpha=self.candidate.value(),geometry='bounds' if self.geometry.currentIndex() else 'mesh',only_selected=self.only.isChecked(),external=self.show_external.isChecked());self.refresh()
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
        d.save_empty_spaces=True
        w.space_manager.save_metadata()
    def rename(self):
        ids=self.ids();d=self.w.doc
        if len(ids)!=1:return
        sid=next(iter(ids));sp=next(s for s in d.spaces if s['id']==sid);name,ok=QInputDialog.getText(self.w,'공간 이름 변경','새 이름',text=sp['name'])
        if ok and name.strip():
            spaces=copy.deepcopy(d.spaces);next(s for s in spaces if s['id']==sid)['name']=name.strip()[:200];self.w.space_manager.changed(spaces,'space_rename',sid)
    def candidate_ids(self):
        return {key.split(':',1)[1] for item in self.b.tree.selectedItems()
                if (key:=str(item.data(0,Qt.ItemDataRole.UserRole))).startswith('candidate:')}

    def bulk_delete(self, mode, confirm=True):
        w=self.w
        d=w.doc
        if w.loading or w.operation is not None:
            return
        spaces=d.spaces
        candidates=d.space_candidates
        sid,cid=set(),set()
        if mode=='selected':
            sid,cid=self.ids(),self.candidate_ids()
        elif mode in ('spaces','all'):
            sid={s['id'] for s in spaces}
        if mode in ('candidates','all'):
            cid={c['id'] for c in candidates}
        if mode=='visible':
            from .space_display import alpha
            sid={s['id'] for s in spaces if w.view.show_spaces and visible(w.view,s) and alpha(w.view,s)>0
                 and (not w.view.exact_space_only or s['id']==d.active_space_id)}
            cid={c['id'] for c in w.view.candidate_boxes if visible(w.view,c,True) and alpha(w.view,c,True)>0}
        if not sid and not cid:
            return
        if confirm and QMessageBox.question(
                w,'공간 데이터 삭제',f'확정 공간 {len(sid)}개, 분석 후보 {len(cid)}개를 삭제합니다. 블록과 별도 보관 분석은 유지됩니다.',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No
        )!=QMessageBox.StandardButton.Yes:
            return
        if w.proposal is not None:w.reject()
        state=copy.deepcopy(d.analysis_state)
        if mode in ('all','candidates'):
            for key in ('last_result','session_reference','session_changes','session_side','comparison'):
                state.pop(key,None)
        elif cid:
            if state.get('last_result'):
                result=state['last_result']
                result['candidates']=[c for c in result['candidates'] if c['id'] not in cid]
                from collections import Counter
                result['counts']=dict(Counter(c['decision'] for c in result['candidates']))
            for key in ('session_reference','session_changes','session_side','comparison'):
                state.pop(key,None)
        for c in state.get('last_result',{}).get('candidates',[]):
            if c.get('existing_space_id') in sid:
                c.pop('existing_space_id',None)
                c['confirmed']=False
                c['decision']='review'
        removed=[s for s in spaces if s['id'] in sid]
        decisions=state.get('decisions',{})
        for space in removed:
            recognition=space.get('recognition',{})
            key=recognition.get('source_fingerprint',recognition.get('fingerprint'))
            if key in decisions and decisions[key]['action']=='registered':
                decisions.pop(key)
        commit(d,state,[s for s in spaces if s['id'] not in sid],'space_bulk_delete')
        d.save_empty_spaces=True
        w.space_manager.save_metadata()
        if mode=='all':
            w.view.space_display['visibility']={}
        w.space_view.bulk.clear_compare()
        w.space_view.bulk.attach()
        self.refresh()
        w.refresh_history()
        event('space_bulk_deleted',spaces=len(sid),candidates=len(cid),blocks_changed=0)

    def deletion_menu(self, parent):
        menu=QMenu('공간 데이터 삭제',parent)
        for label,mode in (('선택 항목 삭제','selected'),('모든 분석 후보 삭제','candidates'),
                           ('확정 공간 전체 삭제','spaces'),('표시 중인 공간·후보 삭제','visible'),
                           ('모든 공간·후보 초기화','all')):
            menu.addAction(label,lambda key=mode:self.bulk_delete(key))
        return menu

    def presentation_menu(self):
        from PySide6.QtGui import QCursor
        menu=QMenu(self.w)
        for index in range(self.mode.count()):
            key=self.mode.itemData(index)
            action=menu.addAction(self.mode.itemText(index),lambda mode=key:self.set_mode(mode))
            action.setCheckable(True)
            action.setChecked(key==self.mode.currentData())
        menu.addSeparator()
        menu.addAction('전체 다시 표시',self.show_all)
        menu.exec(QCursor.pos())

    def show_all(self):
        self.w.view.space_display['visibility']={}
        self.w.view.space_display['show_candidates']=True
        self.w.space_view.overlay.setChecked(True)
        self.w.space_manager.overlays.setChecked(True)
        self.only.setChecked(False)
        self.set_mode('both')
        self.refresh()

    def set_mode(self, key):
        self.mode.setCurrentIndex(self.mode.findData(key))

    def context(self,pos):
        item=self.b.tree.itemAt(pos)
        if item is None:return
        if not item.isSelected():
            self.b.tree.clearSelection();item.setSelected(True);self.b.tree.setCurrentItem(item)
        key=str(item.data(0,Qt.ItemDataRole.UserRole))
        if not key.startswith(('space','cand')):
            return
        menu=QMenu(self.b.tree)
        if self.ids():
            menu.addAction('이름 변경',self.rename)
            menu.addAction('삭제하고 인식 제외 등록',lambda:self.delete(True))
            menu.addAction('선택 공간만 보기',self.isolate_selected)
        if self.ids() or self.candidate_ids():
            menu.addAction('선택 숨기기',lambda:self.visibility(self.ids()|self.candidate_ids(),False))
            menu.addAction('선택 다시 표시',lambda:self.visibility(self.ids()|self.candidate_ids(),True))
        menu.addMenu(self.deletion_menu(menu))
        menu.exec(self.b.tree.viewport().mapToGlobal(pos))
    def isolate_selected(self):
        self.only.setChecked(True);self.b.isolate()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Delete and (self.ids() or self.candidate_ids()):
            self.bulk_delete('selected');return True
        return False
