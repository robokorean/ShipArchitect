"""View-only space presentation; shared by tree, plans and 3D, never voxel edits."""
from .space_model import bbox
DEFAULTS={'normal_alpha':80,'candidate_alpha':110,'geometry':'mesh','external':False,'only_selected':False,'show_candidates':True,'visibility':{}}

def config(view):return getattr(view,'space_display',DEFAULTS)
def external(space,doc):
    if space.get('space_class') in ('exterior_or_merged','exterior_air'):return True
    runs=space.get('volume_runs',space.get('runs',[]))
    if not runs:return False
    volume=space['volume'] if 'volume' in space else sum(b-a+1 for _,_,a,b in runs)
    if volume<=100000:return False
    lo,hi=bbox({'runs':runs});size=doc.schematic.size
    broad=(hi[0]-lo[0]+1)>=.8*size[0] and (hi[2]-lo[2]+1)>=.8*size[2]
    proved=space.get('checks',space.get('recognition',{}).get('checks',{})).get('original_connected_component') is True
    return not proved and volume>100000 and volume>size[0]*size[1]*size[2]*.1 and broad

def visible(view,space,candidate=False):
    cfg=config(view)
    if candidate and not cfg.get('show_candidates',True):return False
    overrides=cfg.get('visibility',{})
    if not overrides.get(space['id'],space.get('visible',True)):return False
    if not cfg.get('external',False) and external(space,view.doc):return False
    if cfg.get('only_selected') and not candidate and space['id']!=view.doc.active_space_id:return False
    return True

def alpha(view,space,candidate=False):
    if candidate:return int(config(view).get('candidate_alpha',110))
    value=round(space.get('opacity',80)*config(view).get('normal_alpha',80)/80)
    return max(0,min(255,value))

def candidate_spaces(view):
    return [dict(c,runs=c['volume_runs'],color='#ffce62',opacity=alpha(view,c,True),visible=True,_candidate=True) for c in getattr(view,'candidate_boxes',[]) if c.get('volume_runs') and visible(view,c,True)]

def color(space):
    original=space.get('color','#4fc7b0')
    if original!='#4fc7b0':return original
    import hashlib
    palette=('#66b5e5','#bb94de','#75cb9d','#e4a778','#72ccca','#e79ab5','#b7c66c','#8d9ee8')
    return palette[hashlib.blake2s(space['id'].encode()).digest()[0]%len(palette)]
