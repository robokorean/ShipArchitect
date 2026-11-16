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
                          ('신규 후보만','candidates'),('변경 후보만','changed'),('복원된 탐색 공간','recovered'),('미확정·제외 후보','uncertain'),('공간 조각·잔여 공기','fragments'),('모든 오버레이 숨김','none')):
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

    def bulk_delete(self, mode, confirm=True, exclude=False):
        w=self.w
        d=w.doc
        if w.loading or w.operation is not None:return
        if mode in ('selected','group','all','visible_tree'):
            keys=self.b.keys_for_scope(mode)
        elif mode=='visible':
            from .space_display import alpha
            keys={k for k,r in self.b.records.items() if self.b.record_visible(k)
                  and (not k.startswith(('space:','candidate:')) or alpha(w.view,r,k.startswith('candidate:'))>0)}
        else:
            keys=set()
        sid={s['id'] for s in d.spaces} if mode in ('spaces','all') else {k[6:] for k in keys if k.startswith('space:')}
        cid={c['id'] for c in d.space_candidates} if mode in ('candidates','all') else {k[10:] for k in keys if k.startswith('candidate:')}
        from .scene_registry import delete_items, PREFIXES
        scene={key for key in keys if key.startswith(PREFIXES)}
        if not (sid or cid or scene):return
        if confirm:
            scope={'selected':'선택 항목','group':'선택 그룹의 현재 검색 범위','visible_tree':'현재 검색 결과',
                   'all':'전체 프로젝트','visible':'현재 표시','spaces':'전체 확정 공간','candidates':'전체 분석 후보'}[mode]
            protected='\n확정 공간의 사용자 경계와 이름을 삭제합니다. 유지할 공간을 다시 확인하세요.' if sid else ''
            policy='재분석에서도 같은 형상·위치의 재등록을 막습니다.' if exclude else '재분석에서 다시 발견될 수 있습니다.'
            message=f'{scope}에서 확정 공간 {len(sid)}개, 후보 {len(cid)}개, 설비·이동 항목 {len(scene)}개를 삭제합니다.\n{policy}{protected}\n실제 블록·NBT 및 별도 분석 보관 파일은 그대로입니다.'
            if QMessageBox.question(w,'탐색기 데이터 삭제',message,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            if w.proposal is not None:w.reject()
            fallback={k:v for k,v in self.b.records.items() if k.startswith(PREFIXES)}
            before=self.b.settings()
            delete_items(d,sid,cid,scene,exclude,fallback)
            self.b.tree.clearSelection()
            self.b.restore_selected_keys=[]
            self.b.selection_key=None
            w.view.selected_candidate_id=None
            removed_ids=sid|cid
            after=copy.deepcopy(before)
            after['selected_keys']=[];after['selected']=None;after['selected_xyz']=None
            for key in removed_ids:after.get('space_presentation',{}).get('visibility',{}).pop(key,None)
            after['hidden_overlays']=[k for k in before['hidden_overlays'] if k not in scene]
            if mode=='all':
                after['space_presentation']['visibility']={}
                after['hidden_groups']=[]
                after['hidden_overlays']=[]
            d.undo_stack[-1]['scene_view_before']=copy.deepcopy(before)
            d.undo_stack[-1]['scene_view_after']=copy.deepcopy(after)
            self.b.restore_scene_view(after)
            w.space_manager.save_metadata()
            w.space_view.bulk.clear_compare()
            w.space_view.bulk.attach()
            self.refresh()
            w.refresh_zones()
            w.refresh_history()
            event('scene_metadata_deleted',spaces=len(sid),candidates=len(cid),scene=len(scene),exclude=exclude,blocks_changed=0)
        except (ValueError,OSError) as exc:
            w.error(exc)

    def restore_excluded(self):
        w=self.w
        state=copy.deepcopy(w.doc.analysis_state)
        if not state.get('scene_excluded') and not state.get('scene_excluded_shapes'):return
        if QMessageBox.question(w,'재분석 제외 해제','탐색기 삭제에서 저장한 영구 제외를 모두 해제합니다. 다음 분석부터 다시 발견할 수 있습니다.')!=QMessageBox.StandardButton.Yes:return
        state.pop('scene_excluded',None);state.pop('scene_excluded_shapes',None)
        commit(w.doc,state,action='scene_exclusion_reset')
        w.refresh_history()

    def deletion_menu(self, parent):
        menu=QMenu('탐색기 데이터 삭제',parent)
        for label,mode in (('선택 항목 삭제','selected'),('선택 그룹 · 현재 검색 범위 삭제','group'),
                           ('검색 결과 전체 삭제','visible_tree'),('모든 분석 후보 삭제','candidates'),
                           ('확정 공간 전체 삭제','spaces'),('현재 표시 항목 삭제','visible'),
                           ('전체 분석 데이터 초기화','all')):
            menu.addAction(label,lambda key=mode:self.bulk_delete(key))
        menu.addSeparator()
        menu.addAction('선택 삭제 및 재분석 제외',lambda:self.bulk_delete('selected',exclude=True))
        menu.addAction('그룹 삭제 및 재분석 제외',lambda:self.bulk_delete('group',exclude=True))
        menu.addAction('재분석 제외 모두 해제',self.restore_excluded)
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
        self.context_menu(str(item.data(0,Qt.ItemDataRole.UserRole)),self.b.tree.viewport().mapToGlobal(pos))

    def context_menu(self,key,global_position):
        if key in ('None','source','blocks','floors') or key.startswith('floor:'):
            return
        menu=QMenu(self.b.tree)
        if key in self.b.records:
            label='즐겨찾기에서 제거' if key in self.b.favorites else '즐겨찾기에 추가'
            menu.addAction(label,lambda:self.b.toggle_favorite(key))
        menu.addAction('선택 위치로 이동',self.b.focus)
        if self.candidate_ids() or key.startswith('candgroup:'):
            menu.addAction('후보 확정·등록',lambda:self.w.space_view.bulk.confirm(
                {k[10:] for k in self.b.keys_for_scope('group') if k.startswith('candidate:')}))
        if self.ids():
            menu.addAction('이름 변경',self.rename)
            menu.addAction('분류·속성',lambda:self.w.integrated_ui.open_properties(0))
            menu.addAction('선택 공간만 보기',self.isolate_selected)
        if key.startswith(('equip:','lift:','route:','connection:')):
            menu.addAction('이름 변경',self.rename_record)
            menu.addAction('설비·이동 항목 고립',self.b.isolate)
        if self.b.keys_for_scope('group'):
            menu.addAction('선택 숨기기',lambda:self.set_selection_visibility(False))
            menu.addAction('선택 다시 표시',lambda:self.set_selection_visibility(True))
        menu.addMenu(self.deletion_menu(menu))
        menu.exec(global_position)
    def rename_record(self):
        from .scene_registry import PREFIXES
        keys={key for key in self.b.keys_for_scope('selected') if key.startswith(PREFIXES)}
        if len(keys)!=1 or self.w.operation is not None:return
        key=next(iter(keys))
        item=self.b.records[key]
        value,accepted=QInputDialog.getText(self.w,'탐색기 이름 변경','표시 이름',text=item.get('name',item.get('label','')))
        if not accepted or not value.strip():return
        state=copy.deepcopy(self.w.doc.analysis_state)
        registry=state.setdefault('scene_records',{k:v for k,v in self.b.records.items() if k.startswith(PREFIXES)})
        registry[key]=dict(item,name=value.strip()[:200],manual=True)
        commit(self.w.doc,state,action='scene_item_rename')
        self.b.rebuild_tree(True)
        self.w.refresh_history()

    def set_selection_visibility(self, visible):
        keys=self.b.keys_for_scope('group')
        items={key.split(':',1)[1] for key in keys if key.startswith(('space:','candidate:'))}
        scene=keys-{key for key in keys if key.startswith(('space:','candidate:'))}
        if visible:self.b.hidden_overlays.difference_update(scene)
        else:self.b.hidden_overlays.update(scene)
        self.visibility(items,visible)

    def isolate_selected(self):
        self.only.setChecked(True);self.b.isolate()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Delete and self.b.keys_for_scope('group'):
            self.bulk_delete('group' if not self.b.keys_for_scope('selected') else 'selected');return True
        return False
