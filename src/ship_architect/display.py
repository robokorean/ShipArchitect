"""Immutable, view-only visibility snapshots. No schematic writes or permission grants."""
from dataclasses import dataclass,field
import numpy as np
from .space_model import bbox

@dataclass(frozen=True)
class DisplayState:
    axis: str='none'
    position: int=0
    side: str='below'
    hide_exterior: bool=False
    hide_roof: bool=False
    occluders: bool=False
    hidden: tuple=()
    isolate: tuple|None=None
    exterior: object=field(default=None,compare=False,repr=False)
    dimensions: tuple=(1,1,1)
    roof_y: int|None=None

    def key(self):return (self.axis,self.position,self.side,self.hide_exterior,self.hide_roof,self.occluders,self.hidden,self.isolate,id(self.exterior),self.roof_y)
    def clip_bounds(self,lo,hi,doc,toward):
        lo=list(lo);hi=list(hi)
        if self.axis in ('X','Y','Z'):
            i='XYZ'.index(self.axis)
            if self.side in ('below','slice'):hi[i]=min(hi[i],self.position)
            if self.side in ('above','slice'):lo[i]=max(lo[i],self.position)
        if self.isolate is not None:
            low,high=self.isolate;lo=[max(a,b) for a,b in zip(lo,low)];hi=[min(a,b) for a,b in zip(hi,high)]
        if self.hide_roof and self.roof_y is not None:hi[1]=min(hi[1],self.roof_y)
        if self.occluders and doc.active_space() is not None:
            low,high=bbox(doc.active_space());i=max((0,2),key=lambda i:abs(toward[i]))
            if toward[i]>=0:hi[i]=min(hi[i],high[i])
            else:lo[i]=max(lo[i],low[i])
        return lo,hi
    def mask(self,origin,shape):
        x,y,z=origin;H,L,W=shape;yy,zz,xx=np.ogrid[y:y+H,z:z+L,x:x+W];out=np.ones(shape,bool)
        if self.axis in ('X','Y','Z'):
            a={'X':xx,'Y':yy,'Z':zz}[self.axis]
            if self.side in ('below','slice'):out &= a<=self.position
            if self.side in ('above','slice'):out &= a>=self.position
        if self.hide_roof and self.roof_y is not None:out &= yy<=self.roof_y
        if self.isolate is not None:
            for a,lo,hi in zip((xx,yy,zz),*self.isolate):out &= (a>=lo)&(a<=hi)
        if self.hide_exterior and self.exterior is not None:out &= ~self.exterior.mask(origin,shape)
        w,h,l=self.dimensions
        if self.hidden:
            ids=np.sort(np.asarray(self.hidden,np.int64));lo,hi=np.searchsorted(ids,[y*w*l,(y+H)*w*l]);ids=ids[lo:hi];X=ids%w;Y=ids//(w*l);Z=ids//w%l;valid=(X>=x)&(X<x+W)&(Z>=z)&(Z<z+L)
            out[Y[valid]-y,Z[valid]-z,X[valid]-x]=False
        return out
    def contains_xyz(self,xyz,doc,view=None):
        lo,hi=self.clip_bounds([0,0,0],[n-1 for n in self.dimensions],doc,view.camera() if view else [0,0,1])
        if any(v<a or v>b for a,v,b in zip(lo,xyz,hi)):return False
        if view is not None:
            if not view.show_blocks:return False
            if view.zone_only and any(v<a or v>b for a,v,b in zip(doc.selection.lo,xyz,doc.selection.hi)):return False
            if view.ghost_floors and view.floor_range and not view.floor_range[0]<=xyz[1]<=view.floor_range[1]:return False
            if view.cut and xyz[1]>view.layer or view.single_layer and xyz[1]!=view.layer:return False
            if view.visible_y is not None and xyz[1] not in view.visible_y:return False
            if view.exact_space_only and doc.active_space() is not None:
                from .space_model import contains
                w,h,l=self.dimensions
                if not contains(doc.active_space(),[xyz[1]*w*l+xyz[2]*w+xyz[0]],self.dimensions)[0]:return False
        return bool(self.mask(xyz,(1,1,1))[0,0,0])
    def visible_indices(self,indices,doc,view):
        w,h,l=self.dimensions;indices=np.asarray(indices,np.int64);result=np.ones(len(indices),bool)
        lo,hi=self.clip_bounds([0,0,0],[w-1,h-1,l-1],doc,view.camera());x=indices%w;y=indices//(w*l);z=indices//w%l
        for a,low,high in zip((x,y,z),lo,hi):result &= (a>=low)&(a<=high)
        if view.zone_only:
            for a,low,high in zip((x,y,z),doc.selection.lo,doc.selection.hi):result &= (a>=low)&(a<=high)
        if view.ghost_floors and view.floor_range:result &= (y>=view.floor_range[0])&(y<=view.floor_range[1])
        if view.cut:result &= y<=view.layer
        if view.single_layer:result &= y==view.layer
        if view.visible_y is not None:result &= np.isin(y,view.visible_y)
        if self.hidden:result &= ~np.isin(indices,self.hidden)
        if self.hide_exterior and self.exterior is not None:result &= ~self.exterior.at(indices)
        if view.exact_space_only and doc.active_space() is not None:
            from .space_model import contains
            result &= contains(doc.active_space(),indices,self.dimensions)
        if not view.show_blocks:result[:]=False
        return result


def validate_settings(value,size):
    """Discard malformed view metadata without granting access or changing block data."""
    if not isinstance(value,dict):return {}
    out={}
    for key,allowed in {'axis':('none','X','Y','Z'),'side':('below','above','slice'),'plane_orientation':('평면도 (위)','정면도','후면도','좌측면도','우측면도'),'selected_kind':('block','space','equipment','connection','path')}.items():
        if value.get(key) in allowed:out[key]=value[key]
    for key in ('position','layer','plane_slice'):
        if type(value.get(key)) is int:out[key]=max(0,min(max(size)-1,value[key]))
    for key in ('exterior','roof','occluders','plane_projection','show_blocks','show_spaces','only_space','ghost_floors','legacy_cut','single_layer','zone_clip'):
        if type(value.get(key)) is bool:out[key]=value[key]
    if isinstance(value.get('hidden'),list):out['hidden']=[i for i in value['hidden'][:min(1000000,int(np.prod(size)))] if type(i) is int and 0<=i<int(np.prod(size))]
    for key in ('hidden_overlays','hidden_groups','expanded','selected_keys'):
        if isinstance(value.get(key),list):out[key]=[s for s in value[key][:25000] if isinstance(s,str) and len(s)<=1024]
    if isinstance(value.get('selected'),str) and len(value['selected'])<=1024:out['selected']=value['selected']
    xyz=value.get('selected_xyz')
    if isinstance(xyz,(list,tuple)) and len(xyz)==3 and all(type(v) is int and 0<=v<n for v,n in zip(xyz,size)):out['selected_xyz']=xyz
    isolation=value.get('isolation')
    if isinstance(isolation,(list,tuple)) and len(isolation)==2:
        from .model import Bounds
        try:Bounds(tuple(isolation[0]),tuple(isolation[1])).validate(size);out['isolation']=isolation
        except (TypeError,ValueError):pass
    cfg=value.get('space_presentation')
    if isinstance(cfg,dict):
        p={}
        for k in ('normal_alpha','candidate_alpha'):
            if type(cfg.get(k)) is int:p[k]=max(0,min(255,cfg[k]))
        if cfg.get('mode') in ('both','confirmed','candidates','changed','none'):p['mode']=cfg['mode']
        if cfg.get('geometry') in ('mesh','bounds'):p['geometry']=cfg['geometry']
        for k in ('external','only_selected','show_candidates'):
            if type(cfg.get(k)) is bool:p[k]=cfg[k]
        p['visibility']={str(k):v for k,v in list(cfg.get('visibility',{}).items())[:4096] if isinstance(k,str) and type(v) is bool} if isinstance(cfg.get('visibility'),dict) else {}
        out['space_presentation']=p
    return out


def overlay_indices_visible(indices,doc,view):
    """Shared annotation mask; hiding block geometry does not hide space annotations."""
    w,h,l=doc.schematic.size;ii=np.asarray(indices,np.int64);x=ii%w;y=ii//(w*l);z=ii//w%l
    lo,hi=view.display_state.clip_bounds([0,0,0],[w-1,h-1,l-1],doc,view.camera());out=np.ones(ii.shape,bool)
    for a,low,high in zip((x,y,z),lo,hi):out &= (a>=low)&(a<=high)
    if view.cut:out &= y<=view.layer
    if view.single_layer:out &= y==view.layer
    if view.visible_y is not None:out &= np.isin(y,view.visible_y)
    if view.zone_only:
        for a,low,high in zip((x,y,z),doc.selection.lo,doc.selection.hi):out &= (a>=low)&(a<=high)
    if view.ghost_floors and view.floor_range:out &= (y>=view.floor_range[0])&(y<=view.floor_range[1])
    return out


def overlay_rectangles(space,y,doc,view):
    from .space_model import plane_rectangles
    w,h,l=doc.schematic.size;lo,hi=view.display_state.clip_bounds([0,0,0],[w-1,h-1,l-1],doc,view.camera())
    if not lo[1]<=y<=hi[1] or view.cut and y>view.layer or view.single_layer and y!=view.layer:return []
    if view.visible_y is not None and y not in view.visible_y:return []
    if view.ghost_floors and view.floor_range and not view.floor_range[0]<=y<=view.floor_range[1]:return []
    if view.zone_only:
        lo=[max(a,b) for a,b in zip(lo,doc.selection.lo)];hi=[min(a,b) for a,b in zip(hi,doc.selection.hi)]
        if not lo[1]<=y<=hi[1]:return []
    out=[]
    for a,z,b,Z in plane_rectangles(space,y):
        a=max(a,lo[0]);z=max(z,lo[2]);b=min(b,hi[0]+1);Z=min(Z,hi[2]+1)
        if a<b and z<Z:out.append((a,z,b,Z))
    return out


def visible_space_runs(space,doc,view):
    w,h,l=doc.schematic.size;lo,hi=view.display_state.clip_bounds([0,0,0],[w-1,h-1,l-1],doc,view.camera())
    if view.zone_only:
        lo=[max(a,b) for a,b in zip(lo,doc.selection.lo)];hi=[min(a,b) for a,b in zip(hi,doc.selection.hi)]
    if view.cut:hi[1]=min(hi[1],view.layer)
    if view.single_layer:lo[1]=max(lo[1],view.layer);hi[1]=min(hi[1],view.layer)
    if view.ghost_floors and view.floor_range:lo[1]=max(lo[1],view.floor_range[0]);hi[1]=min(hi[1],view.floor_range[1])
    levels=set(view.visible_y) if view.visible_y is not None else None
    out=[]
    for y,z,a,b in space['runs']:
        if not lo[1]<=y<=hi[1] or not lo[2]<=z<=hi[2] or levels is not None and y not in levels:continue
        a=max(a,lo[0]);b=min(b,hi[0])
        if a<=b:out.append([y,z,a,b])
    return out
