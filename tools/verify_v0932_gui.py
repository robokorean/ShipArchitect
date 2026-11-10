"""Actual input Qt scenario: extends v0.9.3.1 script with view-only deletion checks."""
from pathlib import Path
# Maintained as a separate entry; the shared verification source is read only by this development tool.
source=(Path(__file__).with_name('verify_v0931_gui.py')).read_text()
source=source.replace('v0.9.3.1','v0.9.3.2').replace("Path('docs/v0931_gui_verification')","Path('private_dev/v0932_gui')")
needle="  diagnostic=report(out/'sample_diagnostic.zip',document=d,ui_state=ui.ui_state())"
checks='''  original_spaces=copy.deepcopy(d.spaces);original_blocks=d.schematic.blocks.copy();epoch=d.block_epoch;before_revision=d.revision
  t=w.space_tools;t.normal.setValue(140);t.candidate.setValue(180);t.only.setChecked(True);t.geometry.setCurrentIndex(1);wait(60)
  assert d.spaces==original_spaces and d.revision==before_revision and np.array_equal(original_blocks,d.schematic.blocks)
  t.geometry.setCurrentIndex(0);t.only.setChecked(False);ids=[s['id'] for s in d.spaces[:2]]
  for sid in ids:t.visibility({sid},False)
  assert d.spaces==original_spaces;w.grab().save(str(out/'space_view_controls.png'))
  b.rebuild_tree(True);b.tree.clearSelection()
  for sid in ids:b.nodes['space:'+sid].setSelected(True)
  t.delete(confirm=False);assert len(d.spaces)==len(original_spaces)-2 and np.array_equal(original_blocks,d.schematic.blocks)
  w.undo();pump_until(lambda:ready(w),180);assert d.spaces==original_spaces and d.block_epoch==epoch and d.schematic.metadata_digest()==meta
  ui.show_view_controls();w.grab().save(str(out/'opacity_visibility_panel.png'))
'''
source=source.replace(needle,checks+needle)
exec(compile(source,str(Path(__file__)), 'exec'))
