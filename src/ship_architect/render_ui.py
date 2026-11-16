"""Graphics controls and live, paint-only diagnostics."""
import json

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLabel, QSpinBox, QVBoxLayout)

from .render_settings import PROFILES, backend, validated


class RenderControls:
    def __init__(self, workbench):
        self.b = workbench
        self.owner = workbench.owner
        self.store = QSettings('ShipArchitect', 'ShipArchitect')
        try:
            saved = json.loads(self.store.value('render/quality_v311', '{}', type=str))
        except (TypeError, ValueError):
            saved = {}
        self.owner.view.render_quality = validated(saved)
        self.label = QLabel()
        self.owner.statusBar().addPermanentWidget(self.label)
        self.timer = QTimer(self.owner)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def snapshot(self):
        view = self.owner.view
        stats = view.space_renderer.stats()
        block = view.render_stats
        return {'backend': backend(view.render_quality), 'policy': view.render_quality,
                'space_overlay': stats, 'block_raster': {k:v for k,v in block.items() if k != 'camera'},
                'memory_scope': '공간 타일 및 메시 캐시만 · VRAM 미측정'}

    def refresh(self):
        view = self.owner.view
        cfg = view.render_quality
        stats = view.space_renderer.stats()
        if not stats.get('paint_samples'):
            self.label.setText(backend(cfg))
            return
        active = stats['selection_expected']
        progress = ''
        if active:
            progress = f" · 선택 정밀 {stats['selection_exact']}/{active}"
        if stats['failed']:
            progress += ' · 표시 실패, 도구에서 원인 확인'
        elif stats['pending']:
            done,total=stats['active_tile_progress']
            progress += f' · 청크 {done}/{total}' if total else f" · 정밀화 {stats['pending']}"
        self.label.setText(f"{PROFILES[cfg['quality']][0]} · 정밀 {stats['exact']} / 간략 {stats['simplified']} / 요약 {stats['summary']}" + progress)
        self.label.setToolTip(json.dumps(self.snapshot(), ensure_ascii=False, indent=2))
        review=getattr(self.b,'space_review',None)
        if review is not None and review.note.isVisible():
            review.note.setText(review.state(self.b.selection_key))
        ui=getattr(self.owner,'integrated_ui',None)
        if ui is not None and hasattr(ui,'ui_summary') and ui.ui_summary.isVisible():
            ui.ui_summary.setPlainText(json.dumps(ui.ui_state(),ensure_ascii=False,indent=2))

    def dialog(self):
        view = self.owner.view
        current = view.render_quality
        dialog = QDialog(self.owner)
        dialog.setWindowTitle('그래픽 설정 및 표시 성능')
        box = QVBoxLayout(dialog)
        form = QFormLayout()
        box.addLayout(form)
        controls = {}

        def choice(key, title, options):
            combo = QComboBox()
            for label, value in options:
                combo.addItem(label, value)
            combo.setCurrentIndex(combo.findData(current[key]))
            controls[key] = combo
            form.addRow(title, combo)
            return combo

        profile = choice('quality', '품질', [(v[0], k) for k, v in PROFILES.items()])
        choice('renderer', '렌더러', [('호환 · QPainter + CPU','compatible'),
                                 ('OpenGL 요청 · 호환 경로 사용','opengl')])
        choice('priority', '주변 표시 우선순위', [('투영 크기','detail'), ('확정 공간','confirmed')])
        for key, label, minimum, maximum in (
            ('fps','목표 FPS (조작 갱신 간격)',15,144),
            ('faces','주변 면 예산',1000,32000),
            ('selection_faces','선택 면 예산',1000,16000),
            ('cache_mib','캐시 상한 (MiB)',8,96),
        ):
            spin = QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setValue(current[key])
            controls[key] = spin
            form.addRow(label, spin)
        lod = QCheckBox('줌·화면 크기에 따라 주변 상세 조절')
        lod.setChecked(current['lod'])
        form.addRow(lod)

        def defaults():
            for key, value in validated({'quality':profile.currentData()}).items():
                if key in ('faces','selection_faces','cache_mib'):
                    controls[key].setValue(value)
        profile.currentIndexChanged.connect(defaults)
        stats = view.space_renderer.stats()
        info = QLabel(backend(current) + '\n' +
                      f"최근 페인트 평균 {stats.get('paint_mean_ms',0)}ms · p95 {stats.get('paint_p95_ms',0)}ms\n"
                      f"메시 {stats['mesh_ms']}ms · 클립 {stats['clip_ms']}ms · 투영 {stats.get('projection_ms',0):.1f}ms\n"
                      f"그리기 {stats.get('draw_ms',0):.1f}ms · 캐시 {stats['cache_bytes']/1048576:.1f} MiB\n"
                      f"캐시 hit/miss {stats['cache_hits']}/{stats['cache_misses']} · 대기/취소 {stats['queued']}/{stats['cancelled']}\n"
                      f"화면 밖 {stats.get('offscreen',0)} · 숨김 {stats.get('hidden',0)} · 미표시 {stats.get('not_drawn',0)} · 실패 {stats.get('failed',0)}\n" + stats['failure'])
        info.setWordWrap(True)
        box.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings={key:widget.currentData() if isinstance(widget,QComboBox) else widget.value() for key,widget in controls.items()}
            view.render_quality = validated(dict(settings, lod=lod.isChecked()))
            view.space_renderer.failed.clear()
            self.store.setValue('render/quality_v311',json.dumps(view.render_quality))
            view.schedule_render()
            self.refresh()
