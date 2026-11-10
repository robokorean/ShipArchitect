"""Deterministic indexed exterior mesh of exact RLE cells; never a bbox mesh."""
import hashlib
import numpy as np
from .space_geometry import boundary_quads
from .space_model import normalize

def indexed_mesh(runs):
    q,n=boundary_quads(runs)
    if len(q):
        wrong=np.einsum('ij,ij->i',np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]),n)<0
        q[wrong]=q[wrong][:,::-1]
    vertices,indices=np.unique(q.reshape(-1,3),axis=0,return_inverse=True);faces=indices.reshape(-1,4).astype(np.int32)
    edges=np.sort(np.concatenate([faces[:,[i,(i+1)%4]] for i in range(4)]),axis=1) if len(faces) else np.empty((0,2),np.int32)
    edges=np.unique(edges,axis=0)
    area=float(np.linalg.norm(np.cross(q[:,1]-q[:,0],q[:,3]-q[:,0]),axis=1).sum())
    q=q.astype(np.float64)
    volume=float((np.einsum('ij,ij->i',q[:,0],np.cross(q[:,1],q[:,2]))+np.einsum('ij,ij->i',q[:,0],np.cross(q[:,2],q[:,3]))).sum()/6)
    return {'vertices':vertices,'faces':faces,'edges':edges,'area':area,'volume':volume,'digest':hashlib.sha256(vertices.tobytes()+faces.tobytes()).hexdigest()}

def summary(mesh):
    return {'vertices':len(mesh['vertices']),'edges':len(mesh['edges']),'faces':len(mesh['faces']),'surface_area':mesh['area'],'enclosed_volume':mesh['volume'],'sha256':mesh['digest'],'geometry':'exposed voxel boundary; coplanar strips merged; no internal faces','topology':'indexed coordinates; coplanar T junctions may remain, not a CAD manifold guarantee'}

def obj_bytes(mesh):
    lines=['# Ship Architect exact voxel boundary; virtual openings remain analysis only']
    lines+=['v '+' '.join(f'{v:g}' for v in p) for p in mesh['vertices']]
    lines+=['f '+' '.join(str(int(v)+1) for v in f) for f in mesh['faces']]
    return ('\n'.join(lines)+'\n').encode('utf8')


def validate_boundary(runs,mesh,cancel=None):
    """Independently raster-check every quad against inside/outside voxel occupancy.

    One padded local mask and one face-coverage mask at a time. Finds intruding,
    duplicate and missing surface cells, retaining bounded mismatch coordinates.
    """
    from .space_model import bbox
    from .chunks import checkpoint
    lo,hi=bbox({'runs':runs});origin=np.array(lo)-1;dims=np.array(hi)-np.array(lo)+3;mask=np.zeros((dims[1],dims[2],dims[0]),bool)
    for y,z,a,b in runs:mask[y-origin[1],z-origin[2],a-origin[0]:b-origin[0]+1]=True
    vertices=mesh['vertices'];faces=mesh['faces'];quads=vertices[faces].astype(np.float64);cross=np.cross(quads[:,1]-quads[:,0],quads[:,2]-quads[:,0]);axes=np.argmax(np.abs(cross),axis=1);mismatches=[];bad=0;missing=0;duplicate=0;expected_area=0
    xyz_to_array=(2,0,1)
    def sample(a,reason):
        if len(mismatches)<256:mismatches.append({'position':[int(v) for v in a],'reason':reason})
    for axis,array_axis in enumerate(xyz_to_array):
        checkpoint(cancel);coverage=np.zeros(mask.shape,bool)
        for index in np.flatnonzero(axes==axis):
            checkpoint(cancel);q=quads[index];a=q.min(axis=0);b=q.max(axis=0);sign=1 if cross[index,axis]>0 else -1
            if not np.all(q==np.rint(q)) or np.count_nonzero(b-a)!=2:bad+=1;sample(a,'non-axis-aligned integer quad');continue
            a=a.astype(int);b=b.astype(int);p=int(a[axis]);inside=p-1 if sign>0 else p;outside=p if sign>0 else p-1
            def slab(position):
                r=[]
                for xyz in (1,2,0):r.append(int(position-origin[xyz]) if xyz==axis else slice(int(a[xyz]-origin[xyz]),int(b[xyz]-origin[xyz])))
                return tuple(r)
            # Bounds are checked before indexing, including outside-of-bbox quads.
            if np.any(a<origin) or np.any(b>origin+dims-1) or not 0<=inside-origin[axis]<dims[axis] or not 0<=outside-origin[axis]<dims[axis]:bad+=1;sample(a,'face outside voxel bounds');continue
            I,O,P=slab(inside),slab(outside),slab(p)
            if not mask[I].all() or mask[O].any():bad+=1;sample(a,'face does not separate inside voxel from empty exterior')
            if coverage[P].any():duplicate+=int(coverage[P].sum());sample(a,'duplicate/overlapping face')
            coverage[P]=True
        for sign in (-1,1):
            neighbour=np.roll(mask,-sign,axis=array_axis);expected=mask&~neighbour;expected_area+=int(expected.sum());covered=np.roll(coverage,-1,axis=array_axis) if sign>0 else coverage;absent=expected&~covered;n=int(absent.sum());missing+=n
            if n:
                y,z,x=np.unravel_index(int(absent.argmax()),absent.shape);sample([x+origin[0],y+origin[1],z+origin[2]],'missing outer face')
    return {'matches':bad==0 and missing==0 and duplicate==0,'invalid_faces':bad,'missing_unit_faces':missing,'duplicate_unit_faces':duplicate,'expected_unit_faces':expected_area,'mismatch_coordinates':mismatches,'mismatch_samples_truncated':bad+missing+duplicate>len(mismatches)}
