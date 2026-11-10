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

def request_geometry(view,key,runs):
    from .workers import Task
    from .diagnostics import event
    from .chunks import checkpoint
    if getattr(view,'geometry_worker',None) is not None:return
    if key in getattr(view,'geometry_failed',set()):return
    identity=id(view.doc)
    def work(progress,cancel):
        checkpoint(cancel);result=boundary_quads(runs);checkpoint(cancel);return result
    job=Task(work,view);view.geometry_worker=job
    def accept(value):
        if id(view.doc)==identity:view.space_geometry_cache[key]=value
    def fail(error):
        if not hasattr(view,'geometry_failed'):view.geometry_failed=set()
        view.geometry_failed.add(key);event('space_mesh_display_failed',reason=str(error))
    def finished():view.geometry_worker=None;view.update()
    job.result.connect(accept);job.failed.connect(fail);job.finished.connect(finished);job.finished.connect(job.deleteLater);job.start()

def draw_volumes(view,painter,candidates=False,spaces_override=None,selectable=True):
    from PySide6.QtCore import Qt,QPointF
    from PySide6.QtGui import QColor,QPolygonF,QPen
    from .display import visible_space_runs
    from .space_display import visible as space_visible,alpha as space_alpha,config,color as space_color
    from .space_model import token
    from .diagnostics import event
    if not hasattr(view,'space_geometry_cache'):view.space_geometry_cache=OrderedDict()
    spaces=spaces_override if spaces_override is not None else getattr(view,'candidate_boxes',[])[:128] if candidates else view.doc.spaces
    if getattr(view,'interaction_active',False):spaces=[s for s in spaces if candidates or s['id']==view.doc.active_space_id]
    budget=2500 if getattr(view,'interaction_active',False) else 50000;warn=False
    for space in spaces:
        if spaces_override is None and not space_visible(view,space,candidates):continue
        if not candidates and spaces_override is None and view.exact_space_only and space['id']!=view.doc.active_space_id:continue
        runs=space.get('volume_runs') if candidates else space['runs']
        if not runs:continue
        clipped=visible_space_runs({'runs':runs},view.doc,view) if hasattr(view,'display_state') else runs
        if not clipped:continue
        if spaces_override is None and config(view).get('geometry')=='bounds':
            from .model import Bounds
            from .space_model import bbox
            painter.setBrush(Qt.BrushStyle.NoBrush);view.box(painter,Bounds(*bbox({'runs':clipped})));continue
        key=hashlib.sha256(json.dumps(clipped,separators=(',',':')).encode()).hexdigest()
        cache=view.space_geometry_cache
        if key not in cache:
            if getattr(view,'interaction_active',False):continue
            if len(clipped)>4000:
                request_geometry(view,key,clipped);continue
            try:q,n=boundary_quads(clipped)
            except ValueError as e:event('space_volume_display_excluded',reason=str(e),space_id=space['id']);warn=True;continue
            cache[key]=(q,n)
            while sum(q.nbytes+n.nbytes for q,n in cache.values())>32*1024**2 and len(cache)>1:cache.popitem(last=False)
        else:cache.move_to_end(key)
        if getattr(view,'interaction_active',False):
            lines=getattr(view,'space_edge_cache',{}).get(key)
            if lines is not None and len(lines)<=800:
                screen=view.project(lines.reshape(-1,3)).reshape(-1,2,2);painter.setPen(QPen(QColor('#ffce62'),1.3))
                for a,b in screen:painter.drawLine(QPointF(*a),QPointF(*b))
            continue
        all_quads,all_normals=cache[key]
        q,n=all_quads,all_normals;ii=np.flatnonzero(np.sum(n*view.camera(),axis=1)>1e-6)
        if len(ii)>budget:warn=True;continue
        budget-=len(ii);q=q[ii];pixels=view.project(q.reshape(-1,3)).reshape(-1,4,2);depth=np.mean(q@view.camera(),axis=1);order=np.argsort(depth)
        selected=candidates or space['id']==view.doc.active_space_id
        color='#ffc95f' if selected else space_color(space)
        c=QColor(color);c.setAlpha(space.get('opacity',60) if spaces_override is not None else space_alpha(view,space,candidates));painter.setBrush(c);painter.setBrush(c)
        edge=QColor(c);edge.setAlpha(110 if candidates else 135);painter.setPen(QPen(edge,.65,Qt.PenStyle.DashLine if candidates else Qt.PenStyle.SolidLine))
        if not selected:painter.setPen(Qt.PenStyle.NoPen)
        else:painter.setPen(Qt.PenStyle.NoPen)
        for i in order:
            screen=pixels[i]
            if screen[:,0].max()<0 or screen[:,1].max()<0 or screen[:,0].min()>view.width() or screen[:,1].min()>view.height():continue
            poly=QPolygonF([QPointF(*pt) for pt in screen]);painter.drawPolygon(poly)
            if not candidates and selectable:view.space_hits.append((space['id'],poly))
        if selected and spaces_override is None:
            if not hasattr(view,'space_edge_cache'):view.space_edge_cache=OrderedDict()
            if key not in view.space_edge_cache:
                if not getattr(view,'interaction_active',False):view.space_edge_cache[key]=feature_lines(all_quads,all_normals)
                while len(view.space_edge_cache)>12:view.space_edge_cache.popitem(last=False)
            edges=view.space_edge_cache.get(key,np.empty((0,2,3),np.float32))
            if len(edges)<=12000:
                screen=view.project(edges.reshape(-1,3)).reshape(-1,2,2);painter.setPen(QPen(QColor('#ffce62'),1.7))
                for a,b in screen:painter.drawLine(QPointF(*a),QPointF(*b))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if warn:
        painter.setPen(QColor('#ffb78b'));painter.drawText(16,65,'공간 부피 표시 예산 초과: 선택 공간/층으로 좁히세요. 정확한 셀과 저장 데이터는 유지합니다.')


def feature_lines(quads,normals):
    """Cancel coplanar strip seams, retaining true corners even at T junctions."""
    from collections import defaultdict,Counter
    lines=defaultdict(list)
    for q,normal in zip(quads,normals):
        for i in range(4):
            a=q[i].astype(int);b=q[(i+1)%4].astype(int);axis=int(np.argmax(np.abs(b-a)));fixed=tuple(int(a[k]) for k in range(3) if k!=axis);lo,hi=sorted((int(a[axis]),int(b[axis])));lines[axis,fixed].append((lo,hi,tuple(normal)))
    out=[]
    for (axis,fixed),spans in lines.items():
        events=defaultdict(Counter)
        for lo,hi,n in spans:events[lo][n]+=1;events[hi][n]-=1
        active=Counter();previous=None
        for pos,change in sorted(events.items()):
            if previous is not None and pos>previous and len([n for n,v in active.items() if v])>1:
                a=[];j=0
                for k in range(3):
                    if k==axis:a.append(previous)
                    else:a.append(fixed[j]);j+=1
                b=list(a);b[axis]=pos;out.append([a,b])
            active.update(change);previous=pos
    return np.asarray(out,np.float32).reshape(-1,2,3)
