"""Experimental exterior-first voxel envelope, conservative room partition and validation.

Only compact masks/labels are allocated. Original block storage is read in chunks.
Virtual boundaries are labels/interfaces, never edits to blocks or collision data.
"""
import time,traceback
from collections import Counter,defaultdict
import numpy as np
from numba import njit
from .chunks import regions,slices,checkpoint
from .spatial3d import next_seed,flood_step,fingerprint,MAX_VOLUME
from .space_model import bbox
from .diagnostics import evidence
from .voxel_mesh import indexed_mesh,summary,validate_boundary
from .exterior_structure import ALGORITHM_VERSION, terrain_mask, repair_partitions, exposure_class, split_deck_complexes
METHOD='exterior_voxel_mesh'

@njit(cache=True,nogil=True)
def grow_step(allowed,labels,queue,head,tail,W,L,H):
    done=0;plane=W*L
    while head<tail and done<65536:
        p=queue[head];head+=1;done+=1;x=p%W;z=p//W%L;y=p//plane
        for q in (p-1 if x>0 else -1,p+1 if x<W-1 else -1,p-W if z>0 else -1,p+W if z<L-1 else -1,p-plane if y>0 else -1,p+plane if y<H-1 else -1):
            if q>=0 and allowed[q] and labels[q]==0:labels[q]=labels[p];queue[tail]=q;tail+=1
    return head,tail

def shift(a,axis,sign,fill=0):
    b=np.roll(a,sign,axis);edge=[slice(None)]*a.ndim;edge[axis]=0 if sign==1 else -1;b[tuple(edge)]=fill;return b

def components(allowed,queue,cancel,progress=None):
    H,L,W=allowed.shape;af=allowed.ravel();labels=np.zeros(allowed.shape,np.int32);flat=labels.ravel();start=0;k=0
    while True:
        checkpoint(cancel);seed=next_seed(af,flat,start)
        if seed<0:break
        if k>=10000:raise ValueError('연결 성분 10000개 한도. 분석 범위를 나누세요.')
        k+=1;flat[seed]=k;queue[0]=seed;head=0;tail=1
        while head<tail:
            checkpoint(cancel);head,tail=flood_step(af,flat,queue,head,tail,k,W,L,H)
        start=seed+1
    return labels,k

def scan(doc,bounds,progress=None,cancel=None,partial=False,*,ignore_terrain=True,repair=True):
    started=time.perf_counter();bounds.validate(doc.schematic.size)
    if bounds.volume>MAX_VOLUME:raise ValueError('외관 기반 분석도 한 번에 800만 셀 이하로 선택하세요.')
    sc=doc.schematic;x0,y0,z0=bounds.lo;X,Y,Z=bounds.hi;H,L,W=Y-y0+1,Z-z0+1,X-x0+1
    solid=np.zeros((H,L,W),bool);epoch=doc.block_epoch;revision=doc.revision
    boxes=list(regions(sc.blocks.shape,bounds))
    for i,(lo,hi) in enumerate(boxes):
        checkpoint(cancel);a,b,c=lo;A,B,C=hi;solid[b-y0:B-y0,c-z0:C-z0,a-x0:A-x0]=sc.solid(sc.blocks[slices(lo,hi)])
        if progress and i%16==0:progress(int(10*i/max(1,len(boxes))),'원본 점유 상태 청크 분석')
    air=~solid;queue=np.empty(air.size,np.int32)
    source_blocks=sc.blocks[y0:Y+1,z0:Z+1,x0:X+1]
    terrain=np.zeros_like(solid)
    terrain_info={'ignored_cells':0,'components':[],'rule':'disabled'}
    if ignore_terrain:
        if progress:progress(10,'함선과 바닥 지형 분리')
        terrain,terrain_info=terrain_mask(source_blocks,sc.palette,bounds,sc.size,queue,components,cancel)
    hull=solid&~terrain
    # Exterior can reach through the ignored soil mask. Candidate cells still use physical air.
    passable=~hull
    # One exterior flood with every boundary air cell as a seed.
    exterior=np.zeros(air.shape,np.int32);seed=np.zeros(air.shape,bool)
    seed[0]=seed[-1]=True;seed[:,0]=seed[:,-1]=True;seed[:,:,0]=seed[:,:,-1]=True;seed &= passable
    points=np.flatnonzero(seed.ravel());tail=len(points);queue[:tail]=points;exterior.ravel()[points]=1;head=0
    while head<tail:
        checkpoint(cancel);head,tail=flood_step(passable.ravel(),exterior.ravel(),queue,head,tail,1,W,L,H)
        if progress:progress(10+int(15*head/max(1,air.size)),'외부 공기·외벽 접근성 탐색')
    raw_exterior=(exterior>0)&air;del exterior,seed,points,passable
    # Exterior-facing occupied unit faces: auditable shell, not inferred hull edits.
    shell_faces=Counter();shell_runs=[];shell_runs_omitted=0
    for axis in range(3):
        for sign in (-1,1):
            exposed=hull&shift(raw_exterior|terrain,axis,sign,True);shell_faces[f'{axis}:{sign}']=int(exposed.sum())
            for y in range(H):
                checkpoint(cancel)
                for z in range(L):
                    row=exposed[y,z];edges=np.r_[0,np.flatnonzero(row[1:]!=row[:-1])+1,W]
                    for a,b in zip(edges[:-1],edges[1:]):
                        if not row[a]:continue
                        if len(shell_runs)<50000:shell_runs.append([axis,-sign,y+y0,z+z0,int(a)+x0,int(b-1)+x0])
                        else:shell_runs_omitted+=1
    # Directional silhouettes. Floor+ceiling and at least one horizontal pair
    # permit an open hangar hypothesis. Three pairs alone are not proof of a room.
    votes=np.zeros(air.shape,np.uint8);vertical=None
    for axis in range(3):
        checkpoint(cancel);before=np.maximum.accumulate(hull,axis=axis);after=np.flip(np.maximum.accumulate(np.flip(hull,axis=axis),axis=axis),axis=axis);inside=before&after&air
        votes+=inside
        if axis==0:vertical=inside.copy()
    domain=air&((~raw_exterior)|(vertical&(votes>=2)))
    del before,after,inside
    if progress:progress(30,'외형 투영·가상 개구 경계 후보')
    # Interior cores separate narrow doorways. Multi-source fill covers all
    # domain cells without merging labels; label interfaces are virtual portals.
    core=domain.copy()
    for axis in range(3):
        for sign in (-1,1):core &= shift(domain,axis,sign)
    labels,k=components(core,queue,cancel);del core
    points=np.flatnonzero(labels.ravel());tail=len(points);queue[:tail]=points;head=0;del points
    while head<tail:
        checkpoint(cancel);head,tail=grow_step(domain.ravel(),labels.ravel(),queue,head,tail,W,L,H)
        if progress:progress(35+int(15*head/max(1,air.size)),'방 코어·개구부 분리 및 빈 복셀 복원')
    # Thin components with no eroded core remain reviewable rather than disappearing.
    missing=domain&(labels==0);extra,n=components(missing,queue,cancel);labels[missing]=extra[missing]+k;k+=n;del extra,missing,queue
    # Reanalyse unusually large exterior-connected merges with a wider neck test.
    # This only refines labels. Every virtual seam remains explicitly uncertain.
    reanalyses=[];refined_labels={}
    ids,sizes=np.unique(labels[labels>0],return_counts=True)
    for ident,volume0 in zip(ids,sizes):
        if volume0<50000:continue
        checkpoint(cancel);mask=labels==ident
        if not (mask&raw_exterior).any():continue
        spans=[]
        for axis in range(3):
            project=mask.any(axis=tuple(i for i in range(3) if i!=axis));pp=np.flatnonzero(project);spans.append(slice(int(pp[0]),int(pp[-1])+1))
        region=tuple(spans);local=mask[region];core2=local.copy()
        for repeat in range(2):
            previous=core2.copy()
            for axis in range(3):
                for sign in (-1,1):core2 &= shift(previous,axis,sign)
        queue2=np.empty(local.size,np.int32);seeds,n=components(core2,queue2,cancel)
        keys,ns=np.unique(seeds[seeds>0],return_counts=True);kept=keys[ns>=27]
        entry={'source_label':int(ident),'source_volume':int(volume0),'method':'second pass, two-cell 6-neighbour core','core_pieces':len(kept),'outcome':'unresolved_review'}
        if len(kept)>=2:
            remap=np.zeros(n+1,np.int32)
            for number,key in enumerate(kept,1):remap[key]=number
            seeds=remap[seeds];points=np.flatnonzero(seeds.ravel());tail=len(points);queue2[:tail]=points;head=0;HH,LL,WW=local.shape
            while head<tail:
                checkpoint(cancel);head,tail=grow_step(local.ravel(),seeds.ravel(),queue2,head,tail,WW,LL,HH)
            if not (local&(seeds==0)).any():
                target=labels[region]
                for number in range(1,len(kept)+1):
                    k+=1;target[seeds==number]=k;refined_labels[k]=len(reanalyses)
                entry['outcome']='partitioned_review';entry['new_labels']=list(range(k-len(kept)+1,k+1))
        reanalyses.append(entry)
        del mask,local,core2,queue2,seeds,previous
        if progress:progress(49,'대형 외부 연결 후보 재분석·가상 경계 검토')
    partition_decisions=[]
    if repair:
        if progress:progress(50,'공유 공기면과 바닥·천장으로 인위적 분할 확인')
        state=getattr(doc,'analysis_state',{})
        protected=set(state.get('decisions',{}))
        protected.update(s.get('recognition',{}).get('source_fingerprint') for s in doc.spaces)
        labels,partition_decisions=repair_partitions(
            labels,hull,source_blocks,sc.palette,bounds,protected,
            set(state.get('partition_rejections',[])),cancel)
    if progress:progress(50,'실제 갑판 접촉과 수직 개구부 검사')
    deck_complexes=split_deck_complexes(labels,hull,raw_exterior,bounds,cancel)
    deck_parts={p['label']:source for source,rec in deck_complexes.items() for p in rec['parts']}
    k+=len(deck_parts)
    if deck_parts:
        for decision in partition_decisions:
            if any(label in deck_complexes for label in decision['result_labels']):
                decision['deck_subdivision']=True
    # Sparse exact cells, counters and virtual boundaries; no per-voxel Python objects.
    runs=defaultdict(list);run_count=0;counts=Counter();raw_counts=Counter();feet=Counter();heads=Counter();patches=Counter();virtual=Counter();pairs=Counter();interfaces=[];unknown=set();surface_samples=[]
    known={i for i,s in sc.palette.items() if s.startswith('minecraft:') and not any(t in s for t in ('stairs','slab','fence','pane','door','trapdoor','water','lava','carpet','snow','leaves'))}
    for y in range(H):
        checkpoint(cancel);row=labels[y];ids,num=np.unique(row[row>0],return_counts=True);counts.update(dict(zip(map(int,ids),map(int,num))));ids,num=np.unique(row[raw_exterior[y]&(row>0)],return_counts=True);raw_counts.update(dict(zip(map(int,ids),map(int,num))))
        if y+1<H:
            ids,num=np.unique(row[(row>0)&(row==labels[y+1])],return_counts=True);heads.update(dict(zip(map(int,ids),map(int,num))))
            below=hull[y-1] if y>0 else sc.solid(sc.blocks[y0-1,z0:Z+1,x0:X+1]) if y0>0 else np.zeros((L,W),bool)
            mask=(row>0)&below&(row==labels[y+1]);ids,num=np.unique(row[mask],return_counts=True);feet.update(dict(zip(map(int,ids),map(int,num))))
            sq=mask[:-1,:-1]&mask[1:,:-1]&mask[:-1,1:]&mask[1:,1:]&(row[:-1,:-1]==row[1:,1:]);patches.update(map(int,np.unique(row[:-1,:-1][sq])))
        for z in range(L):
            rr=row[z];edges=np.r_[0,np.flatnonzero(rr[1:]!=rr[:-1])+1,W]
            for a,b in zip(edges[:-1],edges[1:]):
                ident=int(rr[a])
                if ident:
                    if run_count>=500000:raise ValueError('외관 후보 전체 50만 runs 한도. 범위를 나누세요.')
                    runs[ident].append([y+y0,z+z0,int(a)+x0,int(b-1)+x0]);run_count+=1
        if progress:progress(50+int(15*y/max(1,H)),'정확한 3D 셀·바닥·머리 공간 검증')
    faces=defaultdict(Counter)
    openings=defaultdict(list)
    def add_faces(field,mask):
        ids,num=np.unique(labels[mask],return_counts=True)
        for ident,n in zip(ids,num):
            faces[int(ident)][field]+=int(n)

    # Compare actual hull contact, other candidates and exterior boundaries separately.
    for axis in range(3):
        for sign in (-1,1):
            checkpoint(cancel);near=shift(labels,axis,sign);boundary=(labels>0)&(near!=labels);air_near=shift(air,axis,sign,True);cut=boundary&air_near
            ids,num=np.unique(labels[cut],return_counts=True);virtual.update(dict(zip(map(int,ids),map(int,num))))
            near_hull=shift(hull,axis,sign)
            near_terrain=shift(terrain,axis,sign)
            add_faces('hull',boundary&near_hull)
            add_faces('terrain',boundary&near_terrain)
            add_faces('air',cut)
            outside=cut&(near==0)
            if axis==0:
                add_faces('roof' if sign==-1 else 'floor',boundary&near_hull)
                add_faces('top_boundary' if sign==-1 else 'bottom_boundary',boundary)
            else:
                add_faces('open_side',outside)
                add_faces('other_label_side',cut&(near>0))
            points=np.argwhere(outside)
            if len(points):
                keys=labels[tuple(points.T)]
                order=np.argsort(keys,kind='stable')
                keys,points=keys[order],points[order]
                changes=np.r_[0,np.flatnonzero(keys[1:]!=keys[:-1])+1,len(keys)]
                for start,end in zip(changes[:-1],changes[1:]):
                    group=points[start:end][:,[2,0,1]]+np.asarray(bounds.lo)
                    openings[int(keys[start])].append({
                        'axis_yzx':axis,'sign':-sign,'lo':group.min(0).tolist(),
                        'hi':group.max(0).tolist(),'unit_faces':int(end-start),
                        'sample_xyz':group[::max(1,(len(group)+15)//16)].tolist(),
                        'reason':'outer silhouette or selection limit; virtual, possibly nonplanar',
                    })
            if sign==1:
                between=cut&(near>0);left=labels[between];right=near[between]
                if len(left):
                    ps,ns=np.unique(np.sort(np.stack([left,right],axis=1),axis=1),axis=0,return_counts=True)
                    for pair,count in zip(ps,ns):pairs[tuple(map(int,pair))]+=int(count)
            for y in range(H):
                checkpoint(cancel);yy=y-sign if axis==0 else y
                if not 0<=yy<H:continue
                ids_plane=sc.blocks[yy+y0,z0:Z+1,x0:X+1]
                if axis:ids_plane=shift(ids_plane,axis-1,sign,-1)
                uncertain=boundary[y]&~air_near[y]&~np.isin(ids_plane,list(known))
                unknown.update(map(int,np.unique(labels[y][uncertain])));unknown.discard(0)
                if len(interfaces)<256:
                    for z,x in np.argwhere(cut[y])[:256-len(interfaces)]:interfaces.append({'position':[int(x+x0),int(y+y0),int(z+z0)],'axis_yzx':axis,'sign':-sign,'candidate_label':int(labels[y,z,x]),'neighbour_label':int(near[y,z,x]),'reason':'virtual label boundary; no block written'})
    candidates=[];mesh_failures=[]
    # Always keep the largest useful volumes if UI count limits are reached.
    ordered=sorted(runs,key=lambda i:-counts[i]);omitted=max(0,len(ordered)-512)
    for number,ident in enumerate(ordered[:512]):
        checkpoint(cancel);rr=runs[ident];lo,hi=bbox({'runs':rr});volume=counts[ident];touch=any(lo[i]==bounds.lo[i] or hi[i]==bounds.hi[i] for i in range(3));checks={'closed_within_scope':not touch,'two_cell_clearance':heads[ident]>0,'usable_floor':feet[ident]>=9,'wide_floor_patch':patches[ident]>0,'known_boundary_geometry':ident not in unknown,'complete_component':True,'original_connected_component':raw_counts[ident]==0 and virtual[ident]==0}
        reasons=[]
        if raw_counts[ident]:reasons.append('virtual_exterior_opening')
        if any(q['merged'] and q['result_labels'][0]==ident for q in partition_decisions):reasons.append('wide_partition_repaired')
        if virtual[ident]:reasons.append('virtual_room_partition')
        if ident in refined_labels:reasons.append('large_merge_reanalysed')
        if volume>50000 and raw_counts[ident]:reasons.append('possible_merged_space')
        if touch:reasons.append('scope_or_exterior_open')
        if not checks['two_cell_clearance']:reasons.append('ceiling_slit_no_headroom')
        if not checks['usable_floor']:reasons.append('no_usable_floor')
        if not checks['wide_floor_patch']:reasons.append('narrow_wall_gap_or_corridor')
        if not checks['known_boundary_geometry']:reasons.append('uncertain_block_boundary')
        # One repair pass removes occupied cells if a concurrent mutation is observed.
        repaired=[];collisions=[];collision_count=0
        for y,z,a,b in rr:
            alive=~sc.solid(sc.blocks[y,z,a:b+1]);edges=np.r_[0,np.flatnonzero(alive[1:]!=alive[:-1])+1,len(alive)]
            for a1,b1 in zip(edges[:-1],edges[1:]):
                if alive[a1]:repaired.append([y,z,int(a+a1),int(a+b1-1)])
                else:
                    collision_count+=int(b1-a1)
                    if len(collisions)<256:collisions.append([int(a+a1),y,z])
        if collisions:rr=repaired;volume=sum(b-a+1 for _,_,a,b in rr);reasons.append('source_collision_repaired');checks['original_connected_component']=False
        if not rr:continue
        lo,hi=bbox({'runs':rr})
        mesh_info=None;mesh_ok=False;boundary_check=None
        try:
            mesh=indexed_mesh(rr);mesh_info=summary(mesh);boundary_check=validate_boundary(rr,mesh,cancel);mesh_ok=abs(mesh['volume']-volume)<.01 and boundary_check['matches']
            if not mesh_ok:reasons.append('mesh_volume_mismatch')
        except ValueError as e:mesh_failures.append({'label':ident,'reason':str(e)});reasons.append('mesh_budget_exceeded')
        auto=all(checks.values()) and mesh_ok and not collisions
        decision='auto' if auto else 'review'
        if not checks['two_cell_clearance'] or feet[ident]<3:decision='exclude'
        fp=fingerprint(rr);c=dict(id='EXT-'+fp[:12],fingerprint=fp,method=METHOD,source_block_epoch=epoch,decision=decision,base_decision=decision,reasons=reasons or ['closed_supported_volume'],checks=checks,kind='corridor' if not patches[ident] else 'room',floor_y=lo[1],area=feet[ident],lo=list(lo),hi=list(hi),volume=volume,volume_runs=rr,footprint=[[z,a,b] for y,z,a,b in rr if y==lo[1]],footprint_truncated=False,wall_ratio=1. if not virtual[ident] else 0.,ceiling_ratio=1. if not raw_counts[ident] else 0.,confirmed=False,shape={'method':'exterior flood + directional envelope + core partition; exact empty voxels'},mesh=mesh_info,validation={'solid_intrusions':collision_count,'repaired_sample_coordinates':collisions,'mesh_volume_matches_voxels':mesh_ok,'mesh_boundary':boundary_check,'domain_missing_cells':0,'semantic_room_split':'uncertain where virtual interfaces exist','reanalysis_index':refined_labels.get(ident)},virtual_boundary={'raw_exterior_cells':raw_counts[ident],'unit_faces':virtual[ident],'label':ident},warnings=reasons)
        f=faces[ident]
        c['boundary_contacts']={key:f[key] for key in (
            'hull','terrain','air','roof','floor','top_boundary','bottom_boundary','open_side','other_label_side')}
        c['opening_groups']=openings[ident]
        # Preserve a useful distinction without inferring equipment or room purpose.
        c['enclosure_profile']={
            'roof_fraction':round(f['roof']/max(1,f['top_boundary']),4),
            'floor_fraction':round(f['floor']/max(1,f['bottom_boundary']),4),
            'external_air_fraction':round(raw_counts[ident]/max(1,volume),4),
            'lateral_open_faces':f['open_side'],
        }
        c['structure_class']=exposure_class(c,f)
        if c['structure_class'] in ('exterior_air','exposed_deck'):
            c['reasons'].append('unsupported_exterior' if c['structure_class']=='exterior_air' else 'exposed_deck')
            if c['structure_class']=='exterior_air':
                c['decision']=c['base_decision']='exclude'
        if ident in deck_parts:
            c['decision']=c['base_decision']='exclude'
            c['deck_complex_source']=deck_parts[ident]
            c['reasons'].append('unresolved_deck_group')
        elif c['structure_class']=='uncertain' and raw_counts[ident] and hi[1]-lo[1]>=12:
            horizontal=max(1,(hi[0]-lo[0]+1)*(hi[2]-lo[2]+1))
            if f['floor']/max(1,f['bottom_boundary'])<.4 and volume/horizontal>=4:
                c['structure_class']='vertical_connection_review'
                c['reasons'].append('vertical_air_requires_facility_review')
        candidates.append(c)
        if progress:progress(65+int(30*(number+1)/max(1,min(512,len(ordered)))),'복셀 외곽 메시·원본 대조')
    by_label={c['virtual_boundary']['label']:c['id'] for c in candidates};portals=[]
    for (a,b),area in pairs.items():
        if a in by_label and b in by_label:portals.append({'spaces':[by_label[a],by_label[b]],'unit_faces':area,'kind':'virtual_partition','traversability':'uncertain','reason':'space segmentation is independent of movement connectivity'})
    neighbours=defaultdict(list)
    for portal in portals:
        a,b=portal['spaces']
        neighbours[a].append({'id':b,'unit_faces':portal['unit_faces']})
        neighbours[b].append({'id':a,'unit_faces':portal['unit_faces']})
    for candidate in candidates:
        candidate['adjacent_spaces']=neighbours[candidate['id']]
    for item in partition_decisions:
        item['result_ids']=[by_label.get(i) for i in item['result_labels']]
    # Door/steps are semantic evidence; their actual game collision is not guessed.
    devices=[]
    wanted={i:s for i,s in sc.palette.items() if any(t in s.split('[')[0] for t in ('_door','_trapdoor','_stairs','elevator'))}
    for lo1,hi1 in boxes:
        checkpoint(cancel);a,b,c=lo1;arr=sc.blocks[slices(lo1,hi1)]
        for yy,zz,xx in np.argwhere(np.isin(arr,list(wanted))):
            if len(devices)>=2048:break
            pos=[int(xx+a),int(yy+b),int(zz+c)];state=wanted[int(arr[yy,zz,xx])];neighbours=set()
            for axis in range(3):
                for delta in (-1,1):
                    x,y,z=pos;P=[y-y0,z-z0,x-x0];P[axis]+=delta
                    if all(0<=v<n for v,n in zip(P,labels.shape)):
                        ident=int(labels[tuple(P)])
                        if ident in by_label:neighbours.add(by_label[ident])
            devices.append({'position':pos,'block':state,'spaces':sorted(neighbours),'state_evidence':'open=true' if 'open=true' in state else 'open=false' if 'open=false' in state else 'direction/state only','traversability':'uncertain','reason':'occupancy/semantic evidence; no runtime collision simulation'})
    from .space_classification import classify
    classify(candidates,doc)
    for c in candidates:
        if 'deck_complex_source' in c:
            c['space_class']='exterior_or_merged'
            c['structure_class']='unresolved_deck_group'
        elif c['space_class']=='exterior_or_merged':
            c['structure_class']='unresolved_exterior_merge'
        elif c['structure_class'] in ('exterior_air','exposed_deck'):
            c['space_class']=c['structure_class']
    for record in deck_complexes.values():
        for part in record['parts']:
            part['candidate_id']=by_label.get(part['label'])
    for decision in partition_decisions:
        decision['source_ids']=['EXT-'+f[:12] for f in decision.get('source_fingerprints',[])]
    result={'method':METHOD,'algorithm_version':ALGORITHM_VERSION,'settings':{'ignore_terrain':ignore_terrain,'repair_partitions':repair},'terrain':terrain_info,'partition_decisions':partition_decisions,'deck_complexes':list(deck_complexes.values()),'structure_counts':dict(Counter(c['structure_class'] for c in candidates)),'revision':revision,'block_epoch':epoch,'scope':{'lo':list(bounds.lo),'hi':list(bounds.hi)},'partial':partial,'candidates':candidates,'counts':dict(Counter(c['decision'] for c in candidates)),'component_count':k,'limited':bool(omitted),'omitted_candidate_count':omitted,'domain_cells':int(domain.sum()),'retained_candidate_cells':sum(c['volume'] for c in candidates),'omitted_domain_cells':int(domain.sum())-sum(c['volume'] for c in candidates),'domain_unassigned_cells':int((domain&(labels==0)).sum()),'exterior_air_cells':int(raw_exterior.sum()),'interior_raw_cells':int((air&~raw_exterior).sum()),'outside_envelope_air_cells':int((air&~domain).sum()),'shell_unit_faces':dict(shell_faces),'shell_face_runs':shell_runs,'shell_runs_omitted':shell_runs_omitted,'virtual_boundary_sample':interfaces,'virtual_boundary_samples_truncated':sum(virtual.values())>len(interfaces),'connections':portals,'semantic_features':devices,'semantic_features_limit':2048,'mesh_failures':mesh_failures,'merge_reanalyses':reanalyses,'elapsed_seconds':round(time.perf_counter()-started,6),'note':'시험적 외관 투영+복셀 인식. 가상 개구 경계와 방 분할은 검토 대상이며 원본을 변경하지 않습니다.'}
    def details():
        for c in candidates:
            yield {'candidate':{k:v for k,v in c.items() if k not in ('volume_runs','footprint')}}
            for r in c['volume_runs']:yield {'candidate_id':c['id'],'run':r}
        for row in shell_runs:yield {'exterior_shell_face_run':row}
        for row in interfaces:yield {'virtual_boundary':row}
        for row in portals:yield {'segmentation_connection':row}
        for row in devices:yield {'semantic_feature':row}
        for row in partition_decisions:yield {'partition_decision':row}
    result['trace_id']=evidence('exterior_first', {k:v for k,v in result.items() if k not in ('candidates','semantic_features','virtual_boundary_sample','connections','shell_face_runs','partition_decisions')},details())
    if progress:progress(100,'외관 기반 인식·검증 완료')
    return result

def recognize_exterior(doc,bounds,progress=None,cancel=None,partial=False,**options):
    try:return scan(doc,bounds,progress,cancel,partial,**options)
    except Exception as e:
        evidence('exterior_failed',{'scope':{'lo':bounds.lo,'hi':bounds.hi},'reason':str(e),'type':type(e).__name__},[{'traceback':traceback.format_exc()}]);raise
