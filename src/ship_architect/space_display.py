"""View-only space presentation; shared by tree, plans and 3D, never voxel edits."""
from .space_model import bbox
DEFAULTS={'normal_alpha':80,'candidate_alpha':110,'geometry':'mesh','external':False,'only_selected':False,'mode':'both','show_candidates':True,'visibility':{}}

def config(view):return getattr(view,'space_display',DEFAULTS)
def external(space,doc):
    if space.get('space_class') in ('exterior_or_merged','exterior_air','sky_exposed','exposed_deck'):return True
    runs=space.get('volume_runs',space.get('runs',[]))
    if not runs:return False
    volume=space['volume'] if 'volume' in space else sum(b-a+1 for _,_,a,b in runs)
    if volume<=100000:return False
    lo,hi=bbox({'runs':runs});size=doc.schematic.size
    broad=(hi[0]-lo[0]+1)>=.8*size[0] and (hi[2]-lo[2]+1)>=.8*size[2]
    proved=space.get('checks',space.get('recognition',{}).get('checks',{})).get('original_connected_component') is True
    return not proved and volume>100000 and volume>size[0]*size[1]*size[2]*.1 and broad

def hidden_reason(view,space,candidate=False):
    cfg=config(view)
    mode=cfg.get('mode','both')
    sid=space['id']
    key=('candidate:' if candidate else 'space:')+sid
    if key==getattr(view,'temporary_space',None):
        return None
    if mode=='none' or (candidate and mode=='confirmed') or (not candidate and mode in ('candidates','changed','recovered','uncertain','fragments')):
        return '현재 표시 모드에서 숨김'
    if candidate and mode=='changed' and sid not in getattr(view,'changed_candidate_ids',set()):
        return '변경 후보 필터'
    if candidate and mode=='recovered' and not space.get('discovery',{}).get('legacy_only_cells'):
        return '복원 후보 필터'
    if candidate and mode=='uncertain' and space.get('decision') not in ('review','exclude'):
        return '검토 필터'
    if candidate and mode=='fragments' and not space.get('fragment'):
        return '조각 필터'
    if candidate and space.get('decision')=='registered' and space.get('engine_id')=='precision':
        return '이미 확정 공간에 등록됨'
    if candidate and not cfg.get('show_candidates',True):
        return '후보 레이어 꺼짐'
    overrides=cfg.get('visibility',{})
    if not overrides.get(sid,space.get('visible',True)):
        return '개별 가시성 꺼짐'
    inspect_mode=mode in ('recovered','uncertain','fragments')
    managed=getattr(view,'space_classes',{}).get(key) if candidate else None
    if cfg.get('organized') and managed is not None:
        from .space_organization import VISIBLE_CLASSES, LABELS
        if cfg.get('min_volume',0)>managed.volume:
            return '최소 부피 필터'
        if cfg.get('use_filter','all') not in ('all',managed.kind):
            return '활용 분류 필터'
        if not inspect_mode and not cfg.get('external',False) and sid not in overrides and managed.kind not in VISIBLE_CLASSES:
            return LABELS[managed.kind]+' 기본 숨김'
    if candidate and space.get('decision')=='exclude' and not cfg.get('external',False) and not inspect_mode and sid not in overrides:
        if managed is None or not cfg.get('organized') or managed.kind not in ('movement','support','major'):
            return '분석 제외 후보'
    if not cfg.get('external',False) and not inspect_mode and sid not in overrides and external(space,view.doc):
        return '외부 후보 숨김'
    if cfg.get('only_selected') and not candidate and sid!=view.doc.active_space_id:
        return '선택 공간 오버레이만 표시'
    return None


def visible(view,space,candidate=False):
    return hidden_reason(view,space,candidate) is None


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
