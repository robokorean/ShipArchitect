"""Exact exposed boundary quads from sparse [Y,Z,X0,X1] cells, no bbox substitute."""
from collections import defaultdict,OrderedDict
import hashlib,json
import numpy as np

def difference(a,b,other):
    parts=[(a,b)]
    for c,d in other:
        parts=[r for lo,hi in parts for r in ((lo,min(hi,c-1)),(max(lo,d+1),hi)) if r[0]<=r[1]]
    return parts

def boundary_quads(runs):
    rows=defaultdict(list)
    for y,z,a,b in runs:rows[y,z].append((a,b))
    faces=defaultdict(list)
    for y,z,a,b in runs:
        faces[0,-1,a,y,y+1].append((z,z+1));faces[0,1,b+1,y,y+1].append((z,z+1))
        for axis,sign,near,plane,v in ((1,-1,(y-1,z),y,z),(1,1,(y+1,z),y+1,z),(2,-1,(y,z-1),z,y),(2,1,(y,z+1),z+1,y)):
            for lo,hi in difference(a,b,rows.get(near,())):faces[axis,sign,plane,lo,hi+1].append((v,v+1))
    quads=[];normals=[]
    for (axis,sign,plane,a,b),spans in faces.items():
        spans=sorted(spans);merged=[]
        for c,d in spans:
            if merged and c<=merged[-1][1]:merged[-1][1]=max(d,merged[-1][1])
            else:merged.append([c,d])
        uv=[i for i in range(3) if i!=axis]
        for c,d in merged:
            
            if len(quads)>=200000:raise ValueError('공간 경계 200000면 예산 초과')
            q=np.zeros((4,3),np.float32);q[:,axis]=plane;q[:,uv[0]]=[a,b,b,a];q[:,uv[1]]=[c,c,d,d];n=[0,0,0];n[axis]=sign;quads.append(q);normals.append(n)
    return np.asarray(quads,np.float32).reshape(-1,4,3),np.asarray(normals,np.int8).reshape(-1,3)

def draw_volumes(view,painter,candidates=False,spaces_override=None,selectable=True):
    from PySide6.QtCore import Qt,QPointF
    from PySide6.QtGui import QColor,QPolygonF,QPen
    from .display import visible_space_runs
    from .space_model import token
    from .diagnostics import event
    if not hasattr(view,'space_geometry_cache'):view.space_geometry_cache=OrderedDict()
    spaces=spaces_override if spaces_override is not None else getattr(view,'candidate_boxes',[])[:128] if candidates else view.doc.spaces
    budget=50000;warn=False
    for space in spaces:
        if not candidates and (not space['visible'] or spaces_override is None and view.exact_space_only and space['id']!=view.doc.active_space_id):continue
        runs=space.get('volume_runs') if candidates else space['runs']
        if not runs:continue
        clipped=visible_space_runs({'runs':runs},view.doc,view) if hasattr(view,'display_state') else runs
        key=hashlib.sha256(json.dumps(clipped,separators=(',',':')).encode()).hexdigest()
        cache=view.space_geometry_cache
        if key not in cache:
            try:q,n=boundary_quads(clipped)
            except ValueError as e:event('space_volume_display_excluded',reason=str(e),space_id=space['id']);warn=True;continue
            cache[key]=(q,n)
            while sum(q.nbytes+n.nbytes for q,n in cache.values())>32*1024**2 and len(cache)>1:cache.popitem(last=False)
        else:cache.move_to_end(key)
        q,n=cache[key];ii=np.flatnonzero(n@view.camera()>1e-6)
        if len(ii)>budget:warn=True;continue
        budget-=len(ii);q=q[ii];pixels=view.project(q.reshape(-1,3)).reshape(-1,4,2);depth=np.mean(q@view.camera(),axis=1);order=np.argsort(depth)
        c=QColor('#9acbdc' if candidates else space['color']);c.setAlpha(28 if candidates else space['opacity']);painter.setBrush(c)
        edge=QColor(c);edge.setAlpha(110 if candidates else 135);painter.setPen(QPen(edge,.65,Qt.PenStyle.DashLine if candidates else Qt.PenStyle.SolidLine))
        if space.get('method',space.get('recognition',{}).get('method'))=='exterior_voxel_mesh':painter.setPen(Qt.PenStyle.NoPen)
        for i in order:
            screen=pixels[i]
            if screen[:,0].max()<0 or screen[:,1].max()<0 or screen[:,0].min()>view.width() or screen[:,1].min()>view.height():continue
            poly=QPolygonF([QPointF(*pt) for pt in screen]);painter.drawPolygon(poly)
            if not candidates and selectable:view.space_hits.append((space['id'],poly))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if warn:
        painter.setPen(QColor('#ffb78b'));painter.drawText(16,65,'공간 부피 표시 예산 초과: 선택 공간/층으로 좁히세요. 정확한 셀과 저장 데이터는 유지합니다.')
