"""Non-destructive exact-boundary editing of explicitly confirmed spaces."""
from collections import defaultdict
from copy import deepcopy

from .space_model import box_runs, new_space, normalize, subtract


def volume(runs):
    return sum(b-a+1 for _,_,a,b in runs)


def _connected(runs):
    rows=defaultdict(list)
    for i,(y,z,a,b) in enumerate(runs):
        rows[y,z].append((i,a,b))
    roots=list(range(len(runs)))

    def root(i):
        while roots[i]!=i:
            roots[i]=roots[roots[i]]
            i=roots[i]
        return i

    for (y,z), spans in rows.items():
        for nearby in (rows.get((y+1,z),()),rows.get((y,z+1),())):
            i=j=0
            while i<len(spans) and j<len(nearby):
                left,a,b=spans[i]
                right,c,d=nearby[j]
                if max(a,c)<=min(b,d):
                    roots[root(right)]=root(left)
                if b<=d:
                    i+=1
                else:
                    j+=1
    return all(root(i)==root(0) for i in range(1,len(roots)))


def _assert_air(doc, runs):
    sc=doc.schematic
    entities=defaultdict(list)
    for x,y,z in sc.entity_positions()[0]:
        entities[y,z].append(x)
    for y,z,a,b in runs:
        if sc.solid(sc.blocks[y,z,a:b+1]).any() or any(a<=x<=b for x in entities[y,z]):
            raise ValueError('선택 공간에 블록 또는 장치가 포함되어 있습니다. 빈 공간 경계를 확인하세요.')


def combined(doc, ids, keep_id=None):
    selected=[s for s in doc.spaces if s['id'] in ids]
    if len(selected)<2:
        raise ValueError('확정 공간을 두 개 이상 선택하세요.')
    survivor=next((s for s in selected if s['id']==keep_id),selected[0])
    runs=normalize([r for s in selected for r in s['runs']],doc.schematic.size)
    if not _connected(runs):
        raise ValueError('실제 빈 복셀 경계가 연결되지 않은 공간입니다. 벽 너머 공간은 따로 유지하세요.')
    _assert_air(doc,runs)
    result=deepcopy(survivor)
    result.update(runs=runs,manual_modified=True,merged_from=[s['id'] for s in selected if s!=survivor])
    return result


def divided(doc, space, bounds):
    size=doc.schematic.size
    rest=subtract(space['runs'],box_runs(bounds),size)
    part=subtract(space['runs'],rest,size)
    if not part or not rest:
        raise ValueError('선택 부피가 원래 공간의 일부와 겹쳐야 합니다.')
    _assert_air(doc,space['runs'])
    first=dict(space,runs=rest,manual_modified=True)
    second=new_space(part,size,space['name'][:196]+' 분리',space['purpose'])
    second.update(color=space['color'],opacity=space['opacity'],visible=space['visible'],split_from=space['id'])
    return first,second
