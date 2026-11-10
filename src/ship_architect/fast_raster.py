"""Compiled opaque parallelogram rasterization. Called only from render workers.
No Python objects or GIL inside the pixel loop. Chunks/batches allow cancellation.
"""
import math
import numpy as np
from numba import njit


@njit(cache=True,nogil=True)
def raster_batch(screens,depths,colors,face_ids,rgba,picks,zbuffer):
    height,width=picks.shape
    for n in range(len(face_ids)):
        screen=screens[n]
        x0=max(0,int(math.floor(min(screen[0,0],screen[1,0],screen[2,0],screen[3,0]))))
        y0=max(0,int(math.floor(min(screen[0,1],screen[1,1],screen[2,1],screen[3,1]))))
        x1=min(width,int(math.ceil(max(screen[0,0],screen[1,0],screen[2,0],screen[3,0]))))
        y1=min(height,int(math.ceil(max(screen[0,1],screen[1,1],screen[2,1],screen[3,1]))))
        if x1<=x0 or y1<=y0:continue
        ax=screen[1,0]-screen[0,0];ay=screen[1,1]-screen[0,1]
        bx=screen[3,0]-screen[0,0];by=screen[3,1]-screen[0,1]
        determinant=ax*by-bx*ay
        if abs(determinant)<1e-8:continue
        iu0=by/determinant;iu1=-bx/determinant
        iv0=-ay/determinant;iv1=ax/determinant
        du=depths[n,1]-depths[n,0];dv=depths[n,3]-depths[n,0]
        for y in range(y0,y1):
            dy=y+.5-screen[0,1]
            for x in range(x0,x1):
                dx=x+.5-screen[0,0]
                u=iu0*dx+iu1*dy;v=iv0*dx+iv1*dy
                if u< -1e-7 or u>1+1e-7 or v< -1e-7 or v>1+1e-7:continue
                depth=depths[n,0]+u*du+v*dv
                if depth>zbuffer[y,x]:
                    zbuffer[y,x]=depth
                    picks[y,x]=face_ids[n]
                    rgba[y,x,0]=colors[n,0];rgba[y,x,1]=colors[n,1];rgba[y,x,2]=colors[n,2];rgba[y,x,3]=255


def buffers(width,height):
    return (np.zeros((height,width,4),np.uint8),np.full((height,width),-1,np.int32),
            np.full((height,width),-np.inf,np.float64))
