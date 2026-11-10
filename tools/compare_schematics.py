"""Read-only state-aware comparison. No schematic bytes are written or changed.
python tools/compare_schematics.py ship_f.schem design_approved4.schem --output comparison
"""
import argparse,copy,hashlib,io,json,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from ship_architect.schematic import load_file
from ship_architect.safety import audit


def metadata_digest(sc):
 tree=copy.deepcopy(sc.tree);root=tree.get('Schematic',tree);container=root['Blocks'] if sc.version==3 else root
 for key in ('Data','BlockData','Palette'):container.pop(key,None)
 root.pop('PaletteMax',None);stream=io.BytesIO();tree.write(stream);return hashlib.sha256(stream.getvalue()).hexdigest()


def compare(left,right,output):
 output.mkdir(parents=True,exist_ok=True);a,ar=load_file(left);b,br=load_file(right)
 if a.size!=b.size or list(a.root.get('Offset',[]))!=list(b.root.get('Offset',[])):raise ValueError('크기/Offset이 다릅니다. 먼저 같은 좌표계로 정렬하세요.')
 names=sorted(set(a.palette.values())|set(b.palette.values()));codes={s:i for i,s in enumerate(names)}
 def mapping(sc):
  ids=np.array(sorted(sc.palette));return ids,np.array([codes[sc.palette[int(i)]] for i in ids])
 ai,ac=mapping(a);bi,bc=mapping(b);indices=[];before=[];after=[];counts=Counter();total=0;low=np.array(a.size);high=np.zeros(3,np.int64)
 w,h,l=a.size
 for start in range(0,a.blocks.size,262144):
  aa=ac[np.searchsorted(ai,a.blocks.reshape(-1)[start:start+262144])];bb=bc[np.searchsorted(bi,b.blocks.reshape(-1)[start:start+262144])];mask=aa!=bb;ii=np.flatnonzero(mask)+start
  if not len(ii):continue
  counts.update({names[int(i)]:int(n) for i,n in zip(*np.unique(bb[mask],return_counts=True))});q=(ii%w,ii//(w*l),ii//w%l)
  low=np.minimum(low,[v.min() for v in q]);high=np.maximum(high,[v.max() for v in q]);room=max(0,200000-total)
  indices.append(ii[:room]);before.append(aa[mask][:room]);after.append(bb[mask][:room]);total+=len(ii)
 ii=np.concatenate(indices) if indices else np.empty(0,np.int64);av=np.concatenate(before) if before else np.empty(0,np.int32);bv=np.concatenate(after) if after else np.empty(0,np.int32)
 check=audit(ii,size_xyz=a.size,bounds_lo=(0,0,0),bounds_hi=tuple(v-1 for v in a.size),origin='ai',before=av,after=bv,palette=dict(enumerate(names)))
 report={'files':[left.name,right.name],'sha256':[hashlib.sha256(ar).hexdigest(),hashlib.sha256(br).hexdigest()],'size_xyz':a.size,'changed':total,'after_materials':dict(counts),
 'bbox':{'lo':low.tolist(),'hi':high.tolist()} if total else None,'positions_truncated':total>200000,'before_non_air':int(a.solid().sum()),'after_non_air':int(b.solid().sum()),'non_block_nbt_equal':metadata_digest(a)==metadata_digest(b),'safety':check,'state_names':names}
 (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');np.savez_compressed(output/'changes.npz',indices=ii,before=av,after=bv)
 print(json.dumps({k:v for k,v in report.items() if k not in ('safety','state_names')},ensure_ascii=False,indent=2));return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('--output',type=Path,default=Path('comparison'));a=p.parse_args();compare(a.before,a.after,a.output)
