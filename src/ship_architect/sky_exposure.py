"""Sky exposure using full schematic columns, including outside the scan selection."""
import numpy as np
from .chunks import checkpoint


def top_solid(schematic, bounds, cancel=None):
    x,y,z=bounds.lo
    X,Y,Z=bounds.hi
    top=np.full((Z-z+1,X-x+1),-1,dtype=np.int32)
    for yy in range(schematic.size[1]-1,y-1,-1):
        checkpoint(cancel)
        if (top>=0).all():
            break
        layer=schematic.solid(schematic.blocks[yy,z:Z+1,x:X+1])
        top[(top<0)&layer]=yy
    return top


def sky_fraction(runs, top, bounds):
    x0,_,z0=bounds.lo
    # Each column counts once, even if its candidate has several vertical spans.
    lowest=np.full(top.shape,np.iinfo(np.int32).max,dtype=np.int32)
    for y,z,a,b in runs:
        row=lowest[z-z0,a-x0:b-x0+1]
        np.minimum(row,y,out=row)
    present=lowest!=np.iinfo(np.int32).max
    return int((present&(lowest>top)).sum())/max(1,int(present.sum()))


def classify_sky(candidate, fraction):
    candidate['sky_open_column_fraction']=round(fraction,4)
    if fraction>=.5 and candidate.get('space_class')!='exterior_or_merged':
        candidate['decision']=candidate['base_decision']='exclude'
        candidate['space_class']=candidate['structure_class']='sky_exposed'
        candidate['kind']='open_area'
        candidate['reasons'].append('sky_exposed_not_enclosed')
