"""Trellis publisher coverage and qualified browser details, independent of main DB."""
import copy
import hashlib
import json
import os
import re
import stat as stat_module
import threading
from collections import OrderedDict
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / 'sources/trellis_coverage_20260919'
MAX_LIMIT = 500
LABEL = 'Publisher-reported coverage metadata; internal research use; not for redistribution'
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()


def _confined(folder, value):
    if not isinstance(value,str) or not value or ':' in value or '..' in value.replace('\\','/').split('/') or value.startswith(('/','\\')):
        return None
    path=(Path(folder)/value).resolve()
    return path if path.is_relative_to(Path(folder).resolve()) and path.is_file() else None


def _fingerprint(folder, names):
    """Cheap evidence-change check; hashes are reverified on every change."""
    result=[]
    for name in names:
        if not isinstance(name,str) or not name or ':' in name or '..' in name.replace('\\','/').split('/') or name.startswith(('/','\\')):return None
        # Paths were confined before caching. A changed target identity invalidates
        # this snapshot and the slow verifier resolves/constrains every path again.
        path=os.path.join(str(folder),name)
        stat=os.stat(path)
        if not stat_module.S_ISREG(stat.st_mode):return None
        result.append((name,path,stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
    return tuple(result)


def _load_verified(folder):
    try:
        folder=Path(folder)
        watched={}
        def read(name):
            before=_fingerprint(folder,[name])
            if before is None:raise ValueError('Missing evidence')
            payload=_confined(folder,name).read_bytes()
            if _fingerprint(folder,[name])!=before:raise ValueError('Evidence changed during read')
            watched[name]=before[0]
            return payload
        gate=json.loads(read('validation.json').decode('utf-8-sig'))
        if gate.get('status')!='passed' or gate.get('ready') is not True or not isinstance(gate.get('data_files'),list): return {}
        files={}
        for item in gate['data_files']:
            path=_confined(folder,item.get('path'));digest=item.get('sha256')
            if path is None or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest): return {}
            data=read(item['path'])
            if hashlib.sha256(data).hexdigest()!=digest or item['path'] in files:return {}
            files[item['path']]=data
        result={'gate':gate}
        for name in ('states','counties','edges','receipts'):
            rows=[json.loads(line) for line in files[name+'.jsonl'].decode('utf-8-sig').splitlines() if line.strip()]
            result[name]=rows
        result['progress']=json.loads(files['progress.json']) if 'progress.json' in files else None
        for name in ('states','counties','receipts'):
            if len({r['id'] for r in result[name]})!=len(result[name]):return {}
        if gate.get('counts',{}).get('states')!=len(result['states']) or gate.get('counts',{}).get('counties')!=len(result['counties']):return {}
        for row in result['receipts']:
            path=_confined(folder,row.get('response_path'))
            if path is None:return {}
            data=files.get(row['response_path'])
            if data is None:data=read(row['response_path'])
            if hashlib.sha256(data).hexdigest()!=row.get('response_sha256') or len(data)!=row.get('bytes'):return {}
        names=tuple(sorted(watched))
        fingerprint=tuple(watched[name] for name in names)
        if _fingerprint(folder,names)!=fingerprint:return {}
        return result,names,fingerprint
    except (OSError,ValueError,TypeError,KeyError,UnicodeError):return {}


def load(folder=DATA):
    """Cache only a fully verified immutable snapshot; missing/edited evidence closes it."""
    try:
        folder=Path(folder).resolve();key=str(folder)
        with _CACHE_LOCK:
            cached=_CACHE.get(key)
            if cached and _fingerprint(folder,cached[1])==cached[2]:
                _CACHE.move_to_end(key)
                return cached[0]
            _CACHE.pop(key,None)
            verified=_load_verified(folder)
            if not verified:return {}
            _CACHE[key]=verified
            while len(_CACHE)>8:_CACHE.popitem(last=False)
            return verified[0]
    except (OSError,ValueError,TypeError):return {}


def _public(value):
    if isinstance(value,dict):return {k:_public(v) for k,v in value.items() if 'path' not in k.lower()}
    if isinstance(value,list):return [_public(v) for v in value]
    if isinstance(value,str) and (re.match(r'^[A-Za-z]:[\\/]',value) or value.startswith('raw/')):return None
    return copy.deepcopy(value)


def summary(folder=DATA):
    data=load(folder)
    if not data:return {'available':False,'states':0,'counties':0,'qualification':LABEL,'license_ref':LABEL}
    rows=data['counties'];resolved=sum(bool(r.get('fips')) for r in rows)
    return {'available':True,'states':len(data['states']),'counties':len(rows),
            'state_coverage_responses':sum(r.get('record_type')=='trellis_state_coverage' for r in data['states']),
            'state_connector_errors':sum(bool(r.get('connector_error')) for r in data['states']),
            'counties_with_detail':sum(r.get('detail_captured') is True for r in rows),
            'counties_with_fips':resolved,'counties_without_fips':len(rows)-resolved,
            'venues_with_detail':sum(bool(r.get('venue')) and r.get('detail_captured') is True for r in rows),
            'source_counts':_public(data['gate'].get('counts',{})),
            'provider_checkpoint':_public(data['gate'].get('provider_checkpoint')),
            'validated_at':data['gate'].get('validated_at'),'qualification':LABEL,'license_ref':LABEL}


def progress(state=None,folder=DATA):
    value=load(folder).get('progress')
    if not value:return {'available':False}
    if state is None:return _public(value)
    if not isinstance(state,str):return None
    requested=state.casefold().replace('-',' ')
    row=next((r for r in value.get('states',[]) if requested in (r['state'].casefold(),r['state_name'].casefold())),None)
    return {**_public(row),'available':True,'as_of':value.get('as_of'),'qualification':value.get('qualification')} if row else None


def state(code, folder=DATA):
    if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z]{2}',code):return None
    data=load(folder);code=code.upper()
    row=next((r for r in data.get('states',[]) if r['state']==code),None)
    if row is None:return None
    return {**_public(row),'counties':[_public(r) for r in sorted(data['counties'],key=lambda x:x['county'].casefold()) if r['state']==code]}


def county(fips=None,state=None,name=None,folder=DATA):
    rows=load(folder).get('counties',[])
    if fips is not None:
        if not isinstance(fips,str) or not re.fullmatch(r'\d{5}',fips):return None
        matches=[r for r in rows if r.get('fips')==fips]
    elif isinstance(state,str) and re.fullmatch(r'[A-Za-z]{2}',state) and isinstance(name,str) and name:
        matches=[r for r in rows if r['state']==state.upper() and r['county'].casefold()==name.casefold()]
    else:return None
    return _public(matches[0]) if len(matches)==1 else None


def listing(state=None,detail=None,has_documents=None,practice_area=None,venue=None,fips_resolved=None,q='',limit=50,offset=0,folder=DATA):
    try:limit=min(MAX_LIMIT,max(1,int(limit)))
    except (ValueError,TypeError):limit=50
    try:offset=max(0,int(offset))
    except (ValueError,TypeError):offset=0
    data=load(folder);result=[]
    for row in data.get('counties',[]):
        if state is not None and (not isinstance(state,str) or row['state']!=state.upper()):continue
        if detail is not None and row.get('detail_captured') is not detail:continue
        if has_documents is not None and row.get('has_documents') is not has_documents:continue
        if venue is not None and bool(row.get('venue')) is not venue:continue
        if fips_resolved is not None and bool(row.get('fips')) is not fips_resolved:continue
        if practice_area and str(practice_area).casefold() not in [a.casefold() for a in row.get('practice_areas') or []]:continue
        if q and str(q).casefold() not in (row['county']+' '+row['state']+' '+row['state_name']).casefold():continue
        result.append(row)
    result.sort(key=lambda r:(r['state'],r['county'].casefold(),r['id']))
    return {'available':bool(data),'total':len(result),'items':[_public(r) for r in result[offset:offset+limit]],
            'limit':limit,'offset':offset,'qualification':LABEL,'license_ref':LABEL}


def receipt(ident,folder=DATA):
    if not isinstance(ident,str) or not re.fullmatch(r'tc-[A-Za-z0-9_-]+',ident):return None
    data=load(folder);row=next((r for r in data.get('receipts',[]) if r['id']==ident),None)
    if row is None:return None
    path=_confined(folder,row.get('response_path'))
    try:payload=path.read_bytes() if path else None
    except OSError:return None
    if payload is None or hashlib.sha256(payload).hexdigest()!=row['response_sha256']:return None
    return payload,'application/json',_public(row)
