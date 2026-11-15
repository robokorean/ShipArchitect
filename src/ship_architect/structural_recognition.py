"""Generate and partition empty voxels using observed local structure."""
import numpy as np
from numba import njit

from .chunks import checkpoint
from .spatial3d import next_seed
from .structure_review import adjacent

ENGINES = {'legacy':'0.9.3.4.1', 'structural':'0.9.3.7-structural', 'hybrid':'0.9.3.7-hybrid'}
ENGINE_NAMES = {'legacy':'Legacy · v0.9.3.6', 'structural':'Structural · 구조', 'hybrid':'Hybrid · 구조+공기'}


@njit(cache=True, nogil=True)
def column_support(hull, structure, deck):
    """Nearest physical blockers stop a sightline even when unsuitable as decks."""
    height, depth, width = hull.shape
    floor = np.full(hull.shape, -1, np.int16)
    roof = np.full(hull.shape, -1, np.int16)
    on_deck = np.zeros(hull.shape, np.bool_)
    for z in range(depth):
        for x in range(width):
            last = -1
            for y in range(height):
                if hull[y,z,x]:
                    last = y if structure[y,z,x] else -1
                elif last >= 0:
                    floor[y,z,x] = last
                    on_deck[y,z,x] = deck[last,z,x]
            last = -1
            for y in range(height-1,-1,-1):
                if hull[y,z,x]:
                    last = y if structure[y,z,x] else -1
                elif last >= 0:
                    roof[y,z,x] = last
    return floor, roof, on_deck


@njit(cache=True, nogil=True)
def wall_distances(wall, hull, axis):
    """Length of clear sightline to a vertical structure in both directions."""
    result = np.zeros(wall.shape,np.uint8)
    height,depth,width = wall.shape
    for y in range(height):
        for z in range(depth):
            for x in range(width):
                if hull[y,z,x]:
                    continue
                if (x if axis==2 else z)==0:
                    continue
                zz,xx = (z,x-1) if axis==2 else (z-1,x)
                length = result[y,zz,xx]
                if wall[y,zz,xx] or 0<length<64:
                    result[y,z,x] = length+1
    return result


def domain_from_structure(air, hull, raw, geometry, engine, cancel):
    _,_,_,_,structure,deck,_,wall = geometry
    floor,roof,on_deck = column_support(hull,structure,deck)
    checkpoint(cancel)
    pairs = np.zeros(air.shape,bool)
    sides = np.zeros(air.shape,np.uint8)
    for axis in (1,2):
        left = wall_distances(wall,hull,axis)>0
        right = np.flip(wall_distances(np.flip(wall,axis),np.flip(hull,axis),axis),axis)>0
        pairs |= left & right
        sides += left
        sides += right
        checkpoint(cancel)
    paired = air & (floor>=0) & (roof>=0) & (roof-floor>=3)
    # A narrow supported bay can be bounded by one side and a continuous deck.
    # A freestanding roof on pillars alone does not seed a room.
    supported = paired & (pairs | (on_deck & (sides>=2)))
    enclosed = air & ~raw
    # Enclosed physical air stays reviewable even with an unusual substrate.
    domain = supported | enclosed
    supplemental = np.zeros(air.shape,bool)
    if engine=='hybrid':
        # Floor gaps and shafts are retained only near an actually supported
        # room and between opposed vertical supports. Limit growth at open ends.
        nearby = supported
        eligible = air & raw & pairs & (roof>=0)
        for _ in range(2):
            fringe = np.zeros(air.shape,bool)
            for axis in (1,2):
                for sign in (-1,1):
                    fringe |= adjacent(nearby,axis,sign)
            supplemental |= fringe & eligible & ~domain
            nearby = supported | supplemental
            checkpoint(cancel)
        domain |= supplemental
    anchor = np.where(on_deck,floor,-1).astype(np.int16)
    metrics = {'paired_floor_roof_cells':int(paired.sum()), 'supported_cells':int(supported.sum()),
               'observed_enclosed_cells':int(enclosed.sum()), 'supported_aperture_fringe_cells':int(supplemental.sum()),
               'unassigned_exterior_air_cells':int((raw&~domain).sum()),
               'parameters':{'wall_sightline_max':64,'aperture_growth':2 if engine=='hybrid' else 0,
                             'floor_step_seed_tolerance':2}}
    return domain,anchor,metrics


def manual_edges(shape,bounds,marks,air):
    """Set symmetric edge bits only where a user-marked plane crosses real air."""
    edges = np.zeros(shape,np.uint8)
    accepted=[]
    for mark in marks:
        axis = (2,0,1)[mark['axis']]
        lo = [max(a,b)-b for a,b in zip(mark['lo'],bounds.lo)]
        hi = [min(a,b)-x for a,b,x in zip(mark['hi'],bounds.hi,bounds.lo)]
        face = mark['lo'][mark['axis']]-bounds.lo[mark['axis']]
        if min(b-a for a,b in zip(lo,hi))<0 or not 0<face<shape[axis]:
            continue
        s = [slice(lo[i],hi[i]+1) for i in (1,2,0)]
        left,right = s.copy(),s.copy()
        left[axis],right[axis] = face-1,face
        left,right = tuple(left),tuple(right)
        clear = air[left]&air[right]
        edges[left] |= clear.astype(np.uint8) << (axis*2+1)
        edges[right] |= clear.astype(np.uint8) << (axis*2)
        accepted.append({'id':mark['id'],'axis':mark['axis'],'lo':mark['lo'],'hi':mark['hi'],'air_faces':int(clear.sum())})
    return edges,accepted


@njit(cache=True, nogil=True)
def walk(allowed, labels, queue, head, tail, w, depth, height, anchor, edges, seeding):
    plane = w*depth
    stop = head+65536
    while head<tail and head<stop:
        p = queue[head]
        head += 1
        x,z,y = p%w,p//w%depth,p//plane
        near = (p-plane if y else -1,p+plane if y+1<height else -1,
                p-w if z else -1,p+w if z+1<depth else -1,
                p-1 if x else -1,p+1 if x+1<w else -1)
        for direction,q in enumerate(near):
            if q<0 or not allowed[q] or labels[q] or edges[p] & (1<<direction):
                continue
            if seeding and anchor[p]>=0 and anchor[q]>=0 and abs(int(anchor[p])-int(anchor[q]))>2:
                continue
            labels[q] = labels[p]
            queue[tail] = q
            tail += 1
    return head,tail


def label_domain(domain,anchor,edges,queue,progress,cancel):
    height,depth,width=domain.shape
    anchors=anchor.ravel()
    edge_bits=edges.ravel()
    core=domain.copy()
    for axis in range(3):
        for sign in (-1,1):
            core &= adjacent(domain,axis,sign)
    # Locally anchored seeds cannot cross an unsupported gap by using that
    # gap's missing floor label as a bridge. Only growth fills such gaps.
    anchored=core & (anchor>=0)
    labels=np.zeros(domain.shape,np.int32)
    flat=labels.ravel()
    label=0
    seed_cells=0
    def fill(allowed, seeding, minimum):
        nonlocal label,seed_cells
        search_from=0
        mask=allowed.ravel()
        while True:
            checkpoint(cancel)
            seed=next_seed(mask,flat,search_from)
            if seed<0:
                break
            search_from=seed+1
            label+=1
            queue[0]=seed
            flat[seed]=label
            head,tail=0,1
            while head<tail:
                checkpoint(cancel)
                head,tail=walk(mask,flat,queue,head,tail,width,depth,height,anchors,edge_bits,seeding)
            if tail<minimum:
                flat[queue[:tail]]=-1
                label-=1
            elif seeding:
                seed_cells+=tail
    fill(anchored,True,18)
    flat[flat<0]=0
    # Unanchored cores are used only when there is no adjacent anchored seed.
    # First fill from structural seeds; then recover truly separate fragments.
    seeds=np.flatnonzero(flat)
    tail=len(seeds)
    queue[:tail]=seeds
    del seeds,core,anchored
    head=0
    while head<tail:
        checkpoint(cancel)
        head,tail=walk(domain.ravel(),flat,queue,head,tail,width,depth,height,anchors,edge_bits,False)
        if progress:progress(35+int(14*head/max(1,domain.size)),'구조 기준점에서 원본 빈 셀 확장')
    anchored_labels=label
    fill(domain,False,1)
    return labels,label,{'anchored_labels':anchored_labels,'anchored_core_cells':seed_cells,
                         'components_without_seed':label-anchored_labels}


def reserve_verified_partitions(reference, domain, air, bounds, cancel):
    """Retain previously repaired, strongly supported narrow-seam candidates.

    Keep the original voxel runs only when strong physical floor/roof evidence
    agrees with the new domain. Open-air linkage remains measured separately.
    """
    reserved = []
    offset_x,offset_y,offset_z = bounds.lo
    for c in reference['candidates']:
        checkpoint(cancel)
        profile = c['enclosure_profile']
        if (c['decision'] != 'review' or c['structure_class'] != 'open_interior'
                or 'wide_partition_repaired' not in c['reasons']
                or min(profile['floor_fraction'],profile['roof_fraction'])<.97
                or profile['lateral_open_faces'] or c.get('sky_open_column_fraction',1)>.02
                or not c['validation']['mesh_volume_matches_voxels']
                or c['validation']['solid_intrusions']):
            continue
        rows = [(y-offset_y,z-offset_z,slice(a-offset_x,b-offset_x+1)) for y,z,a,b in c['volume_runs']]
        observed = sum(int(domain[row].sum()) for row in rows)
        if observed < .90*c['volume'] or any(not air[row].all() for row in rows):
            continue
        for row in rows:
            domain[row] = False
        reserved.append((c['id'],c['volume'],observed,rows))
    return reserved
