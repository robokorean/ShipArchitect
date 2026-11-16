"""Display presets independent of space registration, history and block edits."""
from copy import deepcopy

LAYERS = {'equipment', 'connections', 'routes'}
PRESETS = {
    'exterior': ('외형 설계', 'none', LAYERS, 100),
    'interior': ('내부 공간 설계', 'confirmed', LAYERS, 40),
    'equipment': ('설비 배치', 'none', {'connections', 'routes'}, 82),
    'movement': ('이동 경로 검토', 'none', {'equipment'}, 70),
    'review': ('공간 인식 검증', 'both', LAYERS, 85),
}


def validate_presets(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for name, entry in list(value.items())[:12]:
        if not isinstance(name,str) or not name or not isinstance(entry,dict):
            continue
        mode = entry.get('mode')
        opacity = entry.get('opacity')
        groups = entry.get('groups')
        if mode in ('both','confirmed','candidates','changed','none','recovered','uncertain','fragments') and type(opacity) is int and isinstance(groups,list):
            out[name[:40]] = {'mode':mode,'opacity':max(15,min(100,opacity)),
                              'groups':sorted(LAYERS.intersection(x for x in groups if isinstance(x,str))),
                              'external':entry.get('external') is True,
                              'candidate_enabled':entry.get('candidate_enabled') is not False,
                              'overlay_enabled':entry.get('overlay_enabled') is not False}
    return out


class ViewPresets:
    def __init__(self, workbench):
        self.b = workbench
        self.current = ''
        self.previous = None
        self.custom = {}

    def snapshot(self):
        b = self.b
        return {'mode':b.owner.view.space_display.get('mode','both'),
                'opacity':getattr(b.owner.view,'hull_opacity',100),
                'groups':sorted(b.hidden_groups & LAYERS),
                'external':bool(b.owner.view.space_display.get('external',False)),
                'candidate_enabled':bool(b.owner.view.space_display.get('show_candidates',True)),
                'overlay_enabled':b.owner.space_view.overlay.isChecked()}

    def restore_saved(self, settings):
        self.custom = validate_presets(settings.get('display_presets'))
        self.previous = validate_presets({'temp':settings.get('preset_previous')}).get('temp')
        self.current = settings.get('display_preset', '')
        self.b.owner.view.hull_opacity = settings.get('hull_opacity',100)

    def set(self,key):
        label, mode, groups, opacity = PRESETS[key]
        if not self.current:
            self.previous = self.snapshot()
        self.apply(label, {'mode':mode,'groups':groups,'opacity':opacity,'external':False})

    def apply(self,name,settings):
        from PySide6.QtCore import QSignalBlocker
        w = self.b.owner
        mode = settings['mode']
        w.view.space_display.update(mode=mode, external=settings['external'])
        w.view.space_display['show_candidates']=settings.get('candidate_enabled',True)
        overlay=settings.get('overlay_enabled', mode in ('both','candidates','uncertain','fragments'))
        with QSignalBlocker(w.space_view.overlay):
            w.space_view.overlay.setChecked(overlay)
        w.space_view.show_overlay()
        w.view.hull_opacity=settings['opacity']
        self.b.hidden_groups.difference_update(LAYERS)
        self.b.hidden_groups.update(settings['groups'])
        w.view.show_routes='routes' not in self.b.hidden_groups and (getattr(w.view,'route_key',None) or 'route:current') not in self.b.hidden_overlays
        self.current=name
        with QSignalBlocker(w.space_tools.mode), QSignalBlocker(w.space_tools.show_external):
            w.space_tools.mode.setCurrentIndex(w.space_tools.mode.findData(mode))
            w.space_tools.show_external.setChecked(settings['external'])
        self.b.rebuild_tree(True)
        w.space_manager.plan.refresh()
        self.b.axis_plan.refresh()
        self.b.refresh_selection_overlay()
        self.b.save_display()
        w.statusBar().showMessage(f'표시 프리셋 · {name or "사용자 표시"}',4000)

    def restore_previous(self):
        if self.previous is not None:
            previous, self.previous = self.previous, None
            self.apply('',previous)

    def menu(self):
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        menu=QMenu(self.b.owner)
        for key,(label,*_) in PRESETS.items():
            item=menu.addAction(label, lambda k=key:self.set(k))
            item.setCheckable(True)
            item.setChecked(self.current==label)
        for name, data in self.custom.items():
            def load(entry=data, title=name):
                if not self.current:self.previous=self.snapshot()
                self.apply(title,entry)
            menu.addAction(name, load)
        menu.addSeparator()
        menu.addAction('이전 사용자 표시 복원',self.restore_previous).setEnabled(self.previous is not None)
        menu.addAction('현재 표시를 사용자 프리셋으로 저장',self.save_custom)
        menu.exec(QCursor.pos())

    def save_custom(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok=QInputDialog.getText(self.b.owner,'표시 프리셋 저장','프리셋 이름')
        name=name.strip()[:40]
        if ok and name:
            if name not in self.custom and len(self.custom) >= 12:
                self.b.owner.statusBar().showMessage('사용자 프리셋 12개 한도 · 기존 이름을 사용하세요.',5000)
                return
            self.custom[name]=deepcopy(self.snapshot())
            self.b.save_display()
