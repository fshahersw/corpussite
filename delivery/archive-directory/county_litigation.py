"""Read-only, hash-gated official county litigation resource packets."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
from urllib.parse import urlsplit, quote

DATA = Path(__file__).resolve().parents[2] / 'sources/county_litigation_20260919'
TYPES = {'local_rule':'Local rules','court_form':'Court forms','standing_order':'Standing orders',
         'filing_guidance':'Filing guidance','fee_schedule':'Fees','court_information':'Court information',
         'court_contact':'Court contacts','court_staff':'Court staff','source_directory':'Source directories','unknown':'Needs classification'}
AVAILABILITY = {'saved':'Saved content','linked':'Source link only','needs_review':'Needs review'}
QUALIFICATION = 'County association identifies where this source was found or explicitly linked. Court applicability and legal currency require the source evidence shown with each resource.'
_LOCK = threading.Lock()
_CACHE = {}

def digest(data): return hashlib.sha256(data).hexdigest()
def _path(value, folder, artifact=False):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value: return None
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in ('.','..') for x in p.parts): return None
    if artifact and (len(p.parts)<2 or p.parts[0] not in ('assets','text')): return None
    result=(folder/value).resolve()
    return result if result.is_relative_to(folder.resolve()) and result.is_file() else None

def _url(value):
    try:
        p=urlsplit(value or '')
        return value if p.scheme in ('http','https') and p.hostname and not p.username and not p.password else None
    except ValueError: return None

def _public_evidence(value):
    """Keep source facts, but do not disclose collector filesystem locations."""
    if isinstance(value,dict):return {k:_public_evidence(v) for k,v in value.items() if 'path' not in k.casefold()}
    if isinstance(value,list):return [_public_evidence(v) for v in value]
    if isinstance(value,str) and (re.search(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]',value) or value.startswith(('\\\\','/Users/','/home/','/tmp/'))):return '[Local evidence location retained privately]'
    return value

def _stamp(path):
    try:
        s=path.stat();return (str(path.resolve()),s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    except OSError:return None

def _read(folder):
    watched={}
    def read(path):
        before=_stamp(path);data=path.read_bytes()
        if before != _stamp(path): raise ValueError('Changed during verification')
        watched[path]=before;return data
    try:
        gate_path=folder/'validation.json';gate_bytes=read(gate_path);gate=json.loads(gate_bytes)
        if gate.get('ready') is not True or gate.get('status')!='passed':return None
        bound={};verified={};names=set()
        for pin in gate['data_files']:
            name=pin['path'];path=_path(name,folder)
            if not path or name in names or not re.fullmatch('[a-f0-9]{64}',pin['sha256']):return None
            data=read(path)
            if digest(data)!=pin['sha256']:return None
            names.add(name);verified[path]=(pin['sha256'],len(data))
            if name in ('resources.jsonl','artifacts.jsonl'):bound[name]=data
        records=[json.loads(s) for s in bound['resources.jsonl'].decode('utf-8-sig').splitlines() if s.strip()]
        artifacts=[json.loads(s) for s in bound['artifacts.jsonl'].decode('utf-8-sig').splitlines() if s.strip()]
        for name,rows in [('resources.jsonl',records),('artifacts.jsonl',artifacts)]:
            pin=next(x for x in gate['data_files'] if x['path']==name)
            if 'rows' in pin and pin['rows']!=len(rows):return None
        files={}
        for artifact in artifacts:
            path=_path(artifact.get('path'),folder,True)
            if not path or artifact['path'] in files:return None
            proof=verified.get(path)
            if proof is None:
                raw=read(path);proof=(digest(raw),len(raw))
            if proof!=(artifact.get('sha256'),artifact.get('bytes')):return None
            files[artifact['path']]={**artifact,'_path':path}
        result={}
        for row in records:
            ident=row['id'];meta=row.get('metadata') or {}
            if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,180}',ident) or ident in result:return None
            if not _url(row.get('source_url')) or row.get('resource_kind') not in TYPES:return None
            geoids=row.get('county_geoids') or ([row['county_fips']] if row.get('county_fips') else [])
            if not isinstance(geoids,list) or any(not isinstance(x,str) or not re.fullmatch(r'\d{5}',x) for x in geoids):return None
            assets={}
            for kind,key,hashkey in [('original','raw_path','sha256'),('text','text_path','text_sha256')]:
                if not row.get(key):continue
                artifact=files.get(row[key])
                if not artifact or artifact['sha256']!=row.get(hashkey):return None
                if kind=='text' and not artifact['_path'].read_text(encoding='utf-8-sig',errors='replace').strip():continue
                assets[kind]=artifact
            availability=meta.get('availability','saved' if assets.get('original') else 'linked')
            if availability not in AVAILABILITY or availability=='saved' and not assets.get('original'):return None
            result[ident]={**row,'county_geoids':geoids,'_assets':assets,'_availability':availability}
        if gate_path.read_bytes()!=gate_bytes or any(_stamp(path)!=stamp for path,stamp in watched.items()):return None
        return {'rows':result,'gate':gate,'watched':watched}
    except (OSError,ValueError,KeyError,TypeError,UnicodeError):return None

def load(folder=None):
    folder=Path(folder or DATA).resolve();key=str(folder)
    with _LOCK:
        previous=_CACHE.get(key)
        if previous and all(_stamp(path)==stamp for path,stamp in previous['watched'].items()):return previous
        _CACHE.pop(key,None);result=_read(folder)
        if result:_CACHE[key]=result
        return result or {'rows':{},'gate':{},'watched':{}}

def _integer(value,default,maximum):
    try:return min(maximum,max(1,int(value)))
    except (ValueError,TypeError):return default

def _public(row):
    m=row.get('metadata') or {};temporal=m.get('temporal') or {};ident=quote(row['id'],safe='')
    assets=row['_assets'];url='/api/county-litigation-asset?id='+ident+'&kind='
    return {'id':row['id'],'title':row.get('title') or 'County resource','source_url':row['source_url'],
            'state':row.get('state'),'county':row.get('county'),'county_geoids':row['county_geoids'],
            'group':'counties','dataset':'county_litigation','kind':row['resource_kind'],'resource_type':row['resource_kind'],
            'resource_type_label':TYPES[row['resource_kind']],'availability':row['_availability'],
            'availability_label':AVAILABILITY[row['_availability']],'document_shape':m.get('document_shape'),
            'legal_status':m.get('legal_status','unknown'),'has_original':'original' in assets,'has_text':'text' in assets,
            'original_url':url+'original' if 'original' in assets else None,'text_url':'/api/text?id='+ident if 'text' in assets else None,
            'saved_at':row.get('captured_at'),'source_as_of':temporal.get('source_as_of'),
            'published_at':temporal.get('published_at'),'effective_date':temporal.get('effective_at') or temporal.get('effective_from'),
            'applicability':_public_evidence(m.get('applicability') or {}),'authority':_public_evidence(m.get('source_authority') or m.get('authority') or {}),
            'quality':_public_evidence(row.get('quality')),'description':_public_evidence(m.get('description') or m.get('content_scope_note'))}

def query(params,folder=None):
    data=load(folder);rows=list(data['rows'].values());geoid=params.get('geoid','');term=params.get('q','').strip().casefold()
    if params.get('state'):rows=[r for r in rows if str(r.get('state','')).casefold()==params['state'].strip().casefold()]
    if geoid:rows=[r for r in rows if geoid in r['county_geoids']]
    type_counts=Counter(r['resource_kind'] for r in rows);availability_counts=Counter(r['_availability'] for r in rows)
    resource_type=params.get('resource_type') or params.get('category')
    if resource_type:rows=[r for r in rows if r['resource_kind']==resource_type]
    elif params.get('include_directories')!='1':rows=[r for r in rows if r['resource_kind'] not in ('source_directory','unknown')]
    if params.get('availability'):rows=[r for r in rows if r['_availability']==params['availability']]
    if term:rows=[r for r in rows if term in ' '.join(str(r.get(k) or '') for k in ('title','county','state','resource_kind')).casefold()]
    rows.sort(key=lambda r:((r.get('title') or '').casefold(),r['id']))
    page=_integer(params.get('page'),1,100000);limit=_integer(params.get('limit'),24,100)
    return {'available':bool(data['gate']),'total':len(rows),'items':[_public(r) for r in rows[(page-1)*limit:page*limit]],'page':page,'limit':limit,
            'facets':{'resource_types':[{'value':k,'label':v,'count':type_counts[k]} for k,v in TYPES.items() if type_counts[k]],
                      'availability':[{'value':k,'label':v,'count':availability_counts[k]} for k,v in AVAILABILITY.items() if availability_counts[k]]},
            'qualification':QUALIFICATION,'validated_at':data['gate'].get('validated_at'),'county_geoid':geoid or None}

def detail(ident,folder=None):
    row=load(folder)['rows'].get(ident)
    if row is None:return None
    result=_public(row);m=row.get('metadata') or {};content=asset(ident,'text',folder)
    text=content[0].decode('utf-8-sig',errors='replace') if content else ''
    result.update(text=text[:60000],text_characters=len(text),text_truncated=len(text)>60000,
                  metadata=_public_evidence({k:m.get(k) for k in ('availability','applicability','source_authority','authority','temporal','document_shape','legal_status','facts','classification','extraction','related_links','document_structure') if m.get(k) is not None}),
                  reading_notes={'method':'Saved county resource reading copy','original_preserved':bool(result['original_url']),'scope':m.get('content_scope_note')},
                  qualification=QUALIFICATION)
    result['metadata']['source_artifacts']=[{k:a.get(k) for k in ('role','sha256','bytes','mime_type')} for a in row['_assets'].values()]
    return result

def asset(ident,kind,folder=None):
    if kind not in ('original','text'):return None
    row=load(folder)['rows'].get(ident);a=(row or {}).get('_assets',{}).get(kind)
    if not a:return None
    try:
        # Return exactly the same verified bytes, never a later path read.
        data=a['_path'].read_bytes()
        if len(data)!=a['bytes'] or digest(data)!=a['sha256']:return None
        mime='text/plain; charset=utf-8' if kind=='text' else a.get('mime_type') or 'application/octet-stream'
        if not re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+(?:; charset=utf-8)?',mime):mime='application/octet-stream'
        return data,mime,a['_path'].name
    except OSError:return None

def county_counts(folder=None):
    result=Counter()
    for row in load(folder)['rows'].values():
        if row['resource_kind'] in ('source_directory','unknown'):continue
        for geoid in row['county_geoids']:result[geoid]+=1
    return dict(result)
