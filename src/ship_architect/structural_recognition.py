"""Generate and partition empty voxels using observed local structure."""
import numpy as np
from numba import njit

from .chunks import checkpoint
from .spatial3d import next_seed
from .structure_review import adjacent

ENGINES = {'legacy':'0.9.3.4.1', 'structural':'0.9.3.7-structural', 'hybrid37':'0.9.3.7-hybrid', 'hybrid':'0.9.3.8-hybrid','integrated':'0.9.3.9-integrated'}
ENGINE_NAMES = {'legacy':'Legacy · v0.9.3.6', 'structural':'Structural · 구조', 'hybrid':'Hybrid · 0.9.3.8', 'hybrid37':'Hybrid · 0.9.3.7 비교용', 'integrated':'통합 · 0.9.3.9'}


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


def domain_from_structure(air, hull, raw, geometry, engine, cancel, *, graph=None):
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
    if engine in ('hybrid','hybrid37','integrated'):
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
               'parameters':{'wall_sightline_max':64,'aperture_growth':2 if engine in ('hybrid','hybrid37','integrated') else 0,
                             'floor_step_seed_tolerance':2}}
    if graph is not None:
        # Tall walls on three sides can support a bounded unfinished room.
        # Require evidence at standing and head height to reject parapets.
        tall = adjacent(sides,0,1)>=3
        roof_gap = air & raw & on_deck & (roof<0) & (sides>=3) & tall
        roof_gap &= (np.arange(air.shape[0])[:,None,None]-floor)<=5
        # Evidence at the floor is required as well; avoid floating pockets
        # composed only of isolated strips higher in a room.
        for y in range(1,air.shape[0]):
            roof_gap[y] &= (floor[y]==y-1) | roof_gap[y-1]
        domain |= roof_gap
        graph['unfinished']=roof_gap
        graph['deck_ids'],graph['deck_records']=deck_anchors(deck,anchor,cancel)
        metrics['unfinished_missing_roof_cells']=int(roof_gap.sum())
        metrics['unassigned_exterior_air_cells']=int((raw&~domain).sum())
        metrics['unassigned_missing_vertical_support_cells']=int((raw&~domain&((floor<0)|(roof<0))).sum())
        metrics['unassigned_insufficient_lateral_support_cells']=int((raw&~domain&paired).sum())
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


def label_domain(domain,anchor,edges,queue,progress,cancel,*,physical=None):
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
    seed_samples=[]
    local_bits=edge_bits if physical is None else (edges|physical).ravel()
    def fill(allowed, seeding, minimum, blocked):
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
                head,tail=walk(mask,flat,queue,head,tail,width,depth,height,anchors,blocked,seeding)
            if tail<minimum:
                flat[queue[:tail]]=-1
                label-=1
            elif seeding:
                seed_cells+=tail
                if physical is not None and len(seed_samples)<256:
                    seed_samples.append({'label':label,'local_xyz':[int(seed%width),
                        int(seed//(width*depth)),int(seed//width%depth)],'local_floor_y':int(anchors[seed]),
                        'core_cells':tail})
    fill(anchored,True,18,local_bits)
    flat[flat<0]=0
    def grow(blocked):
        seeds=np.flatnonzero(flat)
        tail=len(seeds)
        queue[:tail]=seeds
        head=0
        initial=tail
        while head<tail:
            checkpoint(cancel)
            head,tail=walk(domain.ravel(),flat,queue,head,tail,width,depth,height,anchors,blocked,False)
            if progress:progress(35+int(14*head/max(1,domain.size)),'물리 지지 경계를 반영해 빈 셀 확장')
        return tail-initial
    del core,anchored
    within_support=grow(local_bits)
    anchored_labels=label
    if physical is not None:
        # Preserve unseeded sections bounded by a supported plate rather than
        # letting an earlier seed claim all such sections through a weak edge.
        fill(domain,False,1,local_bits)
    else:
        fill(domain,False,1,edge_bits)
    counts={'anchored_labels':anchored_labels,'anchored_core_cells':seed_cells,
            'growth_cells':within_support,'components_without_seed':label-anchored_labels}
    if physical is not None:
        counts['seed_samples']=seed_samples
        counts['seed_samples_omitted']=anchored_labels-len(seed_samples)
    return labels,label,counts


def reserve_verified_partitions(reference, domain, air, bounds, cancel, *, narrow_seams=False):
    """Retain previously repaired, strongly supported narrow-seam candidates.

    Keep the original voxel runs only when strong physical floor/roof evidence
    agrees with the new domain. Open-air linkage remains measured separately.
    """
    reserved = []
    offset_x,offset_y,offset_z = bounds.lo
    for c in reference['candidates']:
        checkpoint(cancel)
        profile = c['enclosure_profile']
        contacts=c.get('boundary_contacts',{})
        repaired='wide_partition_repaired' in c['reasons']
        confirmed_geometry=(narrow_seams and 'large_merge_reanalysed' in c['reasons']
                            and 0<contacts.get('air',0)<=.05*contacts.get('hull',0))
        if (c['decision'] != 'review' or c['structure_class'] != 'open_interior'
                or not (repaired or confirmed_geometry)
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


@njit(cache=True,nogil=True)
def plate_labels(mask, labels, queue, last):
    depth,width=mask.shape
    for z in range(depth):
        for x in range(width):
            if not mask[z,x] or labels[z,x]:
                continue
            last+=1
            labels[z,x]=last
            queue[0]=z*width+x
            head,tail=0,1
            while head<tail:
                p=queue[head]
                head+=1
                yy,xx=p//width,p%width
                for dz,dx in ((-1,0),(1,0),(0,-1),(0,1)):
                    nz,nx=yy+dz,xx+dx
                    if 0<=nz<depth and 0<=nx<width and mask[nz,nx] and not labels[nz,nx]:
                        labels[nz,nx]=last
                        queue[tail]=nz*width+nx
                        tail+=1
    return last


def deck_anchors(deck, anchor, cancel):
    """Build four-connected physical plate IDs per layer, then project from feet."""
    plates=np.zeros(deck.shape,np.int32)
    queue=np.empty(plates.shape[1]*plates.shape[2],np.int32)
    last=0
    records=[]
    for y in np.flatnonzero(deck.any(axis=(1,2))):
        checkpoint(cancel)
        start=last
        last=plate_labels(deck[y],plates[y],queue,last)
        ids,counts=np.unique(plates[y][deck[y]],return_counts=True)
        records.extend((int(i),int(y),int(n)) for i,n in zip(ids,counts) if start<i)
    result=np.zeros_like(plates)
    for y in range(len(anchor)):
        checkpoint(cancel)
        z,x=np.nonzero(anchor[y]>=0)
        result[y,z,x]=plates[anchor[y,z,x],z,x]
    return result,records


def support_edges(domain, anchor, deck_ids, records, bounds, cancel):
    """Separate a step flanked by substantial plates; preserve physical air links.

    Exact faces are restricted to adjacent supported voxels. The graph does
    not extend a plane across the sky, a shaft, or an unsupported air column.
    """
    result=np.zeros(domain.shape,np.uint8)
    size=np.zeros(max((r[0] for r in records),default=0)+1,dtype=np.int32)
    for ident,_,area in records:
        size[ident]=area
    strong=size[deck_ids]>=24
    count=0
    samples=[]
    for axis in (1,2):
        checkpoint(cancel)
        left=[slice(None)]*3
        right=left.copy()
        left[axis]=slice(None,-1)
        right[axis]=slice(1,None)
        l,r=tuple(left),tuple(right)
        # Different substantial plates at different heights signal an actual
        # floor edge. Small steps are left passable for stairs and details.
        blocked=domain[l]&domain[r]&strong[l]&strong[r]&(deck_ids[l]!=deck_ids[r])
        blocked &= np.abs(anchor[l]-anchor[r])>2
        result[l] |= blocked.astype(np.uint8) << (axis*2+1)
        result[r] |= blocked.astype(np.uint8) << (axis*2)
        count+=int(blocked.sum())
        for y,z,x in np.argwhere(blocked)[:96]:
            pos=(int(y),int(z),int(x))
            target=list(pos)
            target[axis]+=1
            samples.append({'axis':'YZX'[axis], 'xyz':[int(x)+bounds.lo[0],int(y)+bounds.lo[1],int(z)+bounds.lo[2]],
                            'plates':[int(deck_ids[pos]),int(deck_ids[tuple(target)])],
                            'floor_y':[int(anchor[pos])+bounds.lo[1],int(anchor[tuple(target)])+bounds.lo[1]],
                            'type':'virtual_ownership_boundary','confidence':'review'})
    return result,{'physical_plate_count':len(records),'supported_boundary_faces':count,
                   'boundary_samples':samples,'samples_omitted':max(0,count-len(samples)),
                   'largest_plates':[{'id':i,'y':y+bounds.lo[1],'area':area}
                                     for i,y,area in sorted(records,key=lambda r:(-r[2],r[0]))[:64]]}


def refine_large(labels, domain, raw, deck, manual, bounds, queue, cancel):
    """Test local vertical apertures along real decks for large joined volumes.

    Keep a speculative seam only when the local cut disconnects meaningful
    bodies. The remaining open boundary is still an air link in diagnostics.
    """
    height,depth,width=domain.shape
    samples=[]
    maximum=int(labels.max())
    physical=np.zeros(domain.shape,np.uint8)
    anchors=np.full(domain.size,-1,np.int16)
    flat_queue=queue
    for ident,volume in enumerate(np.bincount(labels.ravel())):
        if not ident or volume<40000:
            continue
        checkpoint(cancel)
        local=labels==ident
        if not (local&raw).any():
            continue
        layers=local.sum(axis=(1,2))
        cumulative=np.cumsum(layers)
        options=[]
        for y in range(2,height-2):
            below,above=int(cumulative[y-1]),int(volume-cumulative[y])
            if min(below,above)<max(1200,.07*volume):
                continue
            if min(layers[y-1],layers[y+1])<100:
                continue
            plate=deck[y]&domain[y-1]&domain[y+1]&(local[y-1]|local[y+1])
            contact=int(plate.sum())
            opening=local[y]&local[y+1]
            n=int(opening.sum())
            if contact<300 or not n or contact/(contact+n)<.55:
                continue
            # Grow only along the footprint immediately attached to plate
            # edges. Never extrapolate the closure across the whole floor.
            footprint=plate.copy()
            for _ in range(3):
                halo=footprint.copy()
                halo[1:]|=footprint[:-1]
                halo[:-1]|=footprint[1:]
                halo[:,1:]|=footprint[:,:-1]
                halo[:,:-1]|=footprint[:,1:]
                footprint=halo
            seam=opening&footprint
            fraction=contact/(contact+n)
            if fraction>=.85 and seam.any():
                # A narrow aperture may hug the whole edge of one deck.
                # Recover only connected opening patches that touch the collar.
                patch_ids=np.zeros(opening.shape,np.int32)
                plate_labels(opening,patch_ids,queue,0)
                ids=np.unique(patch_ids[seam])
                seam=np.isin(patch_ids,ids) & opening
            coverage=int(seam.sum())
            if coverage:
                options.append((coverage/n, contact/(contact+n), y, seam, contact, n))
        options.sort(key=lambda o:(-o[0],-o[1],o[2]))
        result={'source_label':ident,'source_cells':int(volume),'outcome':'unresolved_review','tested':[]}
        for _,fraction,y,seam,contact,opening in options[:6]:
            checkpoint(cancel)
            physical[y] |= seam.astype(np.uint8)<<1
            physical[y+1] |= seam.astype(np.uint8)
            gates=(manual|physical).ravel()
            renumber=np.zeros(labels.shape,np.int32)
            flat=renumber.ravel()
            mask=local.ravel()
            number=0
            start=0
            while True:
                checkpoint(cancel)
                seed=next_seed(mask,flat,start)
                if seed<0:
                    break
                number+=1
                flat[seed]=number
                flat_queue[0]=seed
                head,tail=0,1
                while head<tail:
                    checkpoint(cancel)
                    head,tail=walk(mask,flat,flat_queue,head,tail,width,depth,height,anchors,gates,False)
                start=seed+1
            areas=np.bincount(flat[mask])[1:]
            significant=np.count_nonzero(areas>=max(1000,int(.08*volume)))>=2
            physical[y]=0
            physical[y+1]=0
            record={'y':y+1+bounds.lo[1],'deck_contacts':contact,'vertical_air_faces':opening,
                    'plate_fraction':round(fraction,4),'local_virtual_faces':int(seam.sum()),
                    'piece_cells':sorted(map(int,areas),reverse=True)[:12]}
            result['tested'].append(record)
            if not significant:
                continue
            # Apply only this tested seam. Physical navigation remains separate.
            record['virtual_samples_xyz']=[[int(x)+bounds.lo[0],y+1+bounds.lo[1],int(z)+bounds.lo[2]]
                for z,x in np.argwhere(seam)[::max(1,int(seam.sum())//32)][:32]]
            record['confidence']='review'
            result['outcome']='partitioned_review'
            result['new_labels']=[int(i) for i in range(maximum+1,maximum+number+1)]
            labels[local]=flat[mask]+maximum
            maximum+=number
            break
        samples.append(result)
    return maximum,samples


def legacy_volume(reference, shape, bounds, air, cancel):
    """Decode discovery runs into a compact source label field."""
    sources=np.zeros(shape,np.int32)
    stats={}
    x0,y0,z0=bounds.lo
    for ident,candidate in enumerate(reference['candidates'],1):
        checkpoint(cancel)
        stats[ident]=candidate
        for y,z,a,b in candidate['volume_runs']:
            row=np.s_[y-y0,z-z0,a-x0:b-x0+1]
            region=sources[row]
            region[(region==0)&air[row]]=ident
    return sources,stats


def label_remaining(mask, edges, queue, cancel, source=None):
    """Exact connected parts with shared discovery source and manual boundaries."""
    if source is not None:
        edges=edges.copy()
        for axis in range(3):
            checkpoint(cancel)
            low=[slice(None)]*3
            high=low.copy()
            low[axis]=slice(None,-1)
            high[axis]=slice(1,None)
            low,high=tuple(low),tuple(high)
            border=mask[low]&mask[high]&(source[low]!=source[high])
            edges[low] |= border.astype(np.uint8) << (axis*2+1)
            edges[high] |= border.astype(np.uint8) << (axis*2)
    h,d,w=mask.shape
    labels=np.zeros(mask.shape,np.int32)
    flat=labels.ravel()
    allowed=mask.ravel()
    blocked=edges.ravel()
    empty=np.full(mask.size,-1,np.int16)
    ident=0
    start=0
    while True:
        checkpoint(cancel)
        seed=next_seed(allowed,flat,start)
        if seed<0:
            return labels,ident
        start=seed+1
        ident+=1
        flat[seed]=ident
        queue[0]=seed
        head,tail=0,1
        while head<tail:
            checkpoint(cancel)
            head,tail=walk(allowed,flat,queue,head,tail,w,d,h,empty,blocked,False)


def supported_bands(labels, maximum, domain, hull, geometry, edges, queue, bounds, skip, cancel):
    """Extract local connected volumes between measured floor/roof pairs.

    Extraction is restricted to large unverified labels and unclaimed empty
    cells. A vertical opening stays in the residual unless it actually has
    the same observed floor and roof. No full horizontal cap is introduced.
    """
    floor,roof,deck=column_support(hull,geometry[4],geometry[5])
    sizes=np.bincount(labels.ravel())
    evidence={}
    for parent in np.flatnonzero(sizes>=40000):
        if not parent or parent in skip:
            continue
        checkpoint(cancel)
        original=labels==parent
        height=np.flatnonzero(original.any(axis=(1,2)))
        if len(height)<10:
            continue
        paired=original&deck&(roof-floor>=4)&(roof-floor<=28)
        keys,counts=np.unique(np.stack([floor[paired],roof[paired]],axis=1),axis=0,return_counts=True)
        options=sorted(zip(keys.tolist(),counts.tolist()),key=lambda kv:(-kv[1],kv[0]))
        for (bottom,top),volume in options[:5]:
            if volume<max(1300,.10*sizes[parent]) or len(height)<top-bottom+3:
                continue
            section=np.s_[bottom+1:top]
            available=domain[section]&((labels[section]==parent)|(labels[section]==0))
            matching=available&(floor[section]==bottom)&(roof[section]==top)
            groups,count=label_remaining(matching,edges[section],queue,cancel)
            parent_values=groups[labels[section]==parent]
            shared=np.bincount(parent_values,minlength=count+1)
            areas=np.bincount(groups.ravel(),minlength=count+1)
            for part in np.flatnonzero(shared>=max(950,int(.085*sizes[parent]))):
                if not part or shared[part]<areas[part]*.30:
                    continue
                selected=groups==part
                footprint=selected.any(axis=0)
                if int(footprint.sum())<180:
                    continue
                # The actual deck footprint must support most of this part.
                contacts=int((footprint&geometry[5][bottom]).sum())
                if contacts<.52*footprint.sum():
                    continue
                maximum+=1
                labels[section][selected]=maximum
                evidence[maximum]={
                    'source_label':int(parent),'floor_y':bottom+bounds.lo[1],
                    'roof_y':top+bounds.lo[1],'floor_plate_columns':contacts,
                    'paired_columns':int(footprint.sum()),'source_cells':int(shared[part]),
                    'cells':int(areas[part]),'boundary':'virtual_at_change_in_observed_pair',
                    'confidence':'review',
                }
    # Extraction may disconnect the remaining air. Preserve each connected
    # residual rather than calling it a single room across the band.
    for parent in sorted({e['source_label'] for e in evidence.values()}):
        checkpoint(cancel)
        remainder=labels==parent
        parts,count=label_remaining(remainder,edges,queue,cancel)
        if count>1:
            labels[remainder]=parts[remainder]+maximum
            maximum+=count
    return maximum,evidence
