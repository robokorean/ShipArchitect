"""CPU rasterization jobs: chunk frustum/back-face culling and bounded pixels."""
import numpy as np
import time
from .chunks import checkpoint


def render_scene(scene, palette_rgb, center, basis, origin, scale, toward, width, height, factor=1., cancel=None):
    started=time.perf_counter()
    from .views import rasterize_quads
    rw,rh=max(1,int(width*factor)),max(1,int(height*factor))
    accelerated=len(scene)>1500
    if accelerated:
        from .fast_raster import buffers,raster_batch
        rgba,picks,zbuffer=buffers(rw,rh)
    palette=np.zeros((max((k[0] for k in palette_rgb),default=0)+1,3,2,3),np.uint8)
    for (material,normal),rgb in palette_rgb.items():
        axis=int(np.argmax(np.abs(normal)));palette[material,axis,int(normal[axis]>0)]=rgb
    faces=[]
    visible_chunks=visible_faces=offset=0
    for chunk in scene.chunks:
        checkpoint(cancel)
        corners=np.array([[chunk.hi[i] if n>>i&1 else chunk.lo[i] for i in range(3)] for n in range(8)])
        projected=(corners-center)@basis*scale+origin
        if projected[:,0].max()<0 or projected[:,0].min()>width or projected[:,1].max()<0 or projected[:,1].min()>height:
            offset+=len(chunk)
            continue
        visible_chunks+=1
        visible=np.flatnonzero(chunk.normals@toward>1e-6)
        if len(visible):
            quads=chunk.quads[visible]
            screens=((quads-center)@basis*scale+origin)*factor
            depths=quads@toward
            if accelerated:
                normals=chunk.normals[visible];axes=np.argmax(np.abs(normals),axis=1);signs=(normals[np.arange(len(axes)),axes]>0).astype(np.int8);rgbs=palette[chunk.materials[visible],axes,signs]
                face_ids=(visible+offset).astype(np.int32)
                for start in range(0,len(visible),256):
                    checkpoint(cancel)
                    end=start+256
                    raster_batch(screens[start:end],depths[start:end],rgbs[start:end],face_ids[start:end],rgba,picks,zbuffer)
            else:
                for n,i in enumerate(visible):
                    normal=tuple(int(v) for v in chunk.normals[i])
                    rgb=palette_rgb[(int(chunk.materials[i]),normal)]
                    faces.append((screens[n],depths[n],rgb,offset+int(i)))
            visible_faces+=len(visible)
        offset+=len(chunk)
    if not accelerated:rgba,picks=rasterize_quads(faces,rw,rh,cancel=cancel)
    return rgba,picks,{'chunks':visible_chunks,'faces':visible_faces,'pixels':rw*rh,'factor':factor,'engine':'compiled' if accelerated else 'numpy','seconds':round(time.perf_counter()-started,4)}
