"""End-to-end editing in an empty part of a real file; never writes the input.
python tools/verify_editing.py PATH --output verification_editing
"""
import os,sys,time,json,io,copy,gzip,hashlib,argparse,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import nbtlib as nbt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from ship_architect.schematic import load_file,Schematic
from ship_architect.model import Document,Bounds
from ship_architect.mesh import build_scene,MeshCache
from ship_architect.app import MainWindow,STYLE
from verify_schem import pump_until,serialized


def verify(path,out):
    out.mkdir(parents=True,exist_ok=True)
    source_raw=path.read_bytes();source_hash=hashlib.sha256(source_raw).hexdigest()
    start=time.perf_counter();sc,raw=load_file(path);doc=Document(sc,raw,path)
    doc.prepare_protection();protection_seconds=time.perf_counter()-start
    baseline=doc.analyze(doc.selection)
    # A completely empty 16x12x16 box with no BlockEntity reservation.
    chosen=None
    w,h,l=sc.size
    for y in range(4,max(5,h-12),12):
        for z in range(0,max(1,l-16),16):
            for x in range(0,max(1,w-16),16):
                b=Bounds((x,y,z),(min(x+15,w-1),min(y+11,h-1),min(z+15,l-1)))
                a=sc.blocks[b.slices]
                if not np.any(sc.solid(a)) and not np.any(sc.protected(a,b.lo)):
                    chosen=b;break
            if chosen:break
        if chosen:break
    assert chosen is not None,'No suitable empty test region in this file'
    white=next((i for i,s in sc.palette.items() if s=='minecraft:white_concrete'),None)
    if white is None:white=doc.add_material('minecraft:white_concrete')
    gray=doc.add_material('minecraft:gray_concrete')
    cache=MeshCache();scene=build_scene(sc,cache=cache)
    edits=[]
    for mode in ('floor','walls','ceiling'):
        p=doc.propose(chosen,white,mode)
        assert p.exterior_affected==0 and len(p.indices)>0
        before_preview=sc.blocks[chosen.slices].copy()
        preview=doc.preview(p)
        assert np.array_equal(sc.blocks[chosen.slices],before_preview)
        del preview
        next_cache=cache.fork_invalidated(p.indices,sc.size)
        doc.approve(p)
        mark=time.perf_counter();updated=build_scene(sc,cache=next_cache);elapsed=time.perf_counter()-mark
        edits.append({'mode':mode,'blocks':len(p.indices),'mesh_seconds':round(elapsed,4),'cache_hits':updated.stats['cache_hits'],'total_chunks':updated.stats['chunks']})
        cache=next_cache
    replacement=doc.propose(chosen,gray,'replace',source_id=white)
    assert len(replacement.indices)>0
    doc.approve(replacement)
    approved=sc.blocks[chosen.slices].copy()
    doc.undo();assert np.any(sc.blocks[chosen.slices]!=approved)
    doc.redo();assert np.array_equal(sc.blocks[chosen.slices],approved)
    # Original exterior remains unchanged by every operation, including replacement.
    original=Schematic.from_bytes(raw)
    for begin in range(0,sc.blocks.size,262144):
        ii=np.arange(begin,min(begin+262144,sc.blocks.size))
        mask=doc.exterior.at(ii)
        assert np.array_equal(sc.blocks.reshape(-1)[ii[mask]],original.blocks.reshape(-1)[ii[mask]])
        original_values=original.blocks.reshape(-1)[ii]
        non_air=original.solid(original_values)
        assert np.array_equal(sc.blocks.reshape(-1)[ii][non_air],original_values[non_air])
    # Find a mod state that is actually used; targeted deletion/replacement must be blocked.
    mod_id=next(i for i,n in original.counts.items() if n and not original.palette[i].startswith('minecraft:'))
    blocked=doc.propose(doc.selection,white,'replace',source_id=mod_id,allow_exterior=True)
    assert not len(blocked.indices)
    # Deletion preview is cancelable; approved geometry must remain intact.
    erase=doc.propose(chosen,white,'erase');deleted=doc.preview(erase)
    assert not np.any(sc.solid(deleted[chosen.slices]));del deleted
    assert np.array_equal(sc.blocks[chosen.slices],approved)
    with tempfile.TemporaryDirectory(prefix='ship-editing-verify-') as temp:
        export=Path(temp)/'edited.schem';report=doc.export(export,approved=True)
        reread,export_raw=load_file(export)
        assert np.array_equal(reread.blocks,sc.blocks)
        tree0=nbt.File.parse(io.BytesIO(gzip.decompress(raw)))
        tree1=nbt.File.parse(io.BytesIO(gzip.decompress(export_raw)))
        root0,root1=tree0.get('Schematic',tree0),tree1.get('Schematic',tree1)
        c0=root0['Blocks'] if sc.version==3 else root0;c1=root1['Blocks'] if sc.version==3 else root1
        c0['Data' if sc.version==3 else 'BlockData']=c1['Data' if sc.version==3 else 'BlockData']
        c0['Palette']=c1['Palette']
        if sc.version==2:root0['PaletteMax']=root1['PaletteMax']
        assert serialized(tree0)==serialized(tree1),'NBT outside block Data/Palette changed'
        project=Path(temp)/'edited.shipproj';doc.save_project(project)
        restored=Document.open_project(project);restored.prepare_protection()
        assert np.array_equal(restored.schematic.blocks,sc.blocks)
        restored.undo();restored.redo();assert np.array_equal(restored.schematic.blocks,sc.blocks)
    app=QApplication.instance() or QApplication([])
    app.setFont(QFont('Noto Sans CJK KR',10));app.setStyle('Fusion');app.setStyleSheet(STYLE)
    win=MainWindow();win.resize(1380,900);errors=[];win.error=errors.append
    win.set_document(doc,path.name+' · 건축 편집 검증');win.show()
    win.set_selection(chosen);win.view.focus_selection();win.zone_clip.setChecked(True)
    # Cut below the new ceiling for a readable before/after floor replacement.
    win.layer.setValue(min(chosen.lo[1]+8,chosen.hi[1]));win.clip.setChecked(True)
    win.material.setCurrentIndex(win.material.findData(white))
    win.mode.setCurrentIndex(win.mode.findData('replace'))
    win.source_material.setCurrentIndex(win.source_material.findData(gray))
    win.make_preview()
    pump_until(lambda:win.operation is None and not win.view.workers_running() and not win.view.render_timer.isActive() and win.view.pick_buffer is not None,60)
    assert win.proposal and len(win.proposal.indices)>0
    assert np.any(win.view.pick_buffer>=0)
    win.grab().save(str(out/'editing_after.png'))
    win.compare.setCurrentIndex(1)
    pump_until(lambda:not win.view.workers_running() and not win.view.render_timer.isActive() and win.view.pick_buffer is not None,30)
    win.grab().save(str(out/'editing_before.png'))
    win.reject();pump_until(lambda:not win.view.workers_running() and not win.view.render_timer.isActive(),30)
    win.doc.dirty=False;win.close();app.processEvents()
    assert not errors,errors
    assert hashlib.sha256(path.read_bytes()).hexdigest()==source_hash
    result={'input':path.name,'sha256':source_hash,'volume':sc.blocks.size,'placed_before':baseline['solid'],
        'original_exterior_blocks':doc.exterior.count,'protection_prepare_seconds':round(protection_seconds,4),
        'region':{'lo':chosen.lo,'hi':chosen.hi},'edits':edits,'replace_blocks':len(replacement.indices),
        'blocked_mod_replace_count':blocked.protected,'undo_redo':'passed','erase_preview_cancel':'passed',
        'export_on_disk_reread':'passed','all_non_block_nbt':'identical','original_exterior':'unchanged',
        'all_original_non_air':'unchanged','source':'unchanged','project_restore_and_history':'passed','errors':errors,'platform':sys.platform}
    (out/'editing_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('path',type=Path);p.add_argument('--output',type=Path,default=Path('verification_editing'))
    a=p.parse_args();verify(a.path.resolve(),a.output.resolve())
