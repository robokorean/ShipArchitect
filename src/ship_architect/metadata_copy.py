"""Independent metadata snapshots, preserving tuples and shared subtrees.

Avoid deepcopy dispatch for every numeric coordinate in large voxel run lists.
Unknown extension types retain Python's deepcopy semantics.
"""
import copy
_ATOMS=(str,int,float,bool,type(None))


def snapshot(value,memo=None):
    kind=type(value)
    if kind in _ATOMS:return value
    if memo is None:memo={}
    if id(value) in memo:return memo[id(value)]
    if kind is list:
        result=[];memo[id(value)]=result
        result.extend(snapshot(v,memo) if type(v) not in _ATOMS else v for v in value)
    elif kind is dict:
        result={};memo[id(value)]=result
        result.update((k,snapshot(v,memo) if type(v) not in _ATOMS else v) for k,v in value.items())
    elif kind is tuple:
        result=tuple(snapshot(v,memo) if type(v) not in _ATOMS else v for v in value);memo[id(value)]=result
    else:result=copy.deepcopy(value,memo)
    return result


def history_after(before_value,after_value,before_snapshot):
    """Share unchanged data only between private, read-only history snapshots.

    The live after_value never aliases either history snapshot. Undo/Redo already
    materializes an independent copy before restoring it to the document.
    """
    if type(before_value) is type(after_value) and before_value==after_value:
        return before_snapshot
    if type(before_value) is dict and type(after_value) is dict:
        return {k:history_after(before_value[k],v,before_snapshot[k]) if k in before_value else snapshot(v) for k,v in after_value.items()}
    if type(before_value) is type(after_value) and type(after_value) in (list,tuple) and len(before_value)==len(after_value):
        values=[history_after(a,b,c) for a,b,c in zip(before_value,after_value,before_snapshot)]
        return tuple(values) if type(after_value) is tuple else values
    return snapshot(after_value)
