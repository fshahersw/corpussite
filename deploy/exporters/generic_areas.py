"""Offline, source-backed generic-area export. Never contacts a remote service.

Existing adapters remain the authority for public formatting and validation. High
volume tables stream directly from SQLite; their pure detail-rendering tail is
compiled once so there is no per-record HTTP or database lookup. Smaller adapters
run their native listing once per mode, retaining source rows for native filters.
Outputs are staged with ready=False until the independent importer validates them.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import inspect
import json
import mimetypes
import re
import sqlite3
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, parse_qs

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'delivery/archive-directory'
OUT = ROOT / '_transfer_scratch/supabase_export/generic'
sys.path.insert(0, str(APP))

ALIASES = {
    'court-coverage':'docsupload_coverage', 'docsupload_coverage':'docsupload_coverage',
    'settlements':'settlements', 'statistics':'court_statistics', 'court_statistics':'court_statistics',
    'courts':'court_spine', 'court_spine':'court_spine', 'counsel':'mdl_counsel', 'mdl_counsel':'mdl_counsel',
    'urls':'url_directory', 'url_directory':'url_directory', 'uscourts':'uscourts_pages', 'uscourts_pages':'uscourts_pages',
    'state-proceedings':'state_proceedings', 'state_proceedings':'state_proceedings',
    'court-documents':'court_documents', 'court_documents':'court_documents',
    'judge-disclosures':'judge_disclosures', 'judge_disclosures':'judge_disclosures',
    'federal-register':'federal_register_history', 'federal_register_history':'federal_register_history',
    'mdl-cases':'mdl_case_inventory', 'mdl_case_inventory':'mdl_case_inventory',
    'mdl-appearances':'mdl_appearances', 'mdl_appearances':'mdl_appearances',
    'mdl-documents':'mdl_docket_documents', 'mdl_docket_documents':'mdl_docket_documents',
    'mdl-activity':'mdl_docket_activity', 'mdl_docket_activity':'mdl_docket_activity',
    'mdl-crosswalk':'mdl_crosswalk', 'mdl_crosswalk':'mdl_crosswalk',
    'sd-statutes':'sd_statutes', 'sd_statutes':'sd_statutes',
    'agency-documents':'agency_science_documents', 'agency_science_documents':'agency_science_documents',
    'saved-pages':'saved_pages', 'saved_pages':'saved_pages', 'indiana-code':'indiana_code', 'indiana_code':'indiana_code',
    'public-laws':'public_laws', 'public_laws':'public_laws', 'state-codes':'state_codes', 'state_codes':'state_codes',
    'counsel-directory':'counsel_directory', 'counsel_directory':'counsel_directory',
    'verdict-reports':'verdict_reports', 'verdict_reports':'verdict_reports',
    'cpsc-injury-data':'cpsc_injury_data', 'cpsc_injury_data':'cpsc_injury_data',
    'expert-rulings':'expert_rulings', 'expert_rulings':'expert_rulings',
    'source-documents':'source_documents', 'source_documents':'source_documents',
    'citation-guide':'citation_reference', 'citation_reference':'citation_reference',
    'limitation-periods':'limitation_periods', 'limitation_periods':'limitation_periods',
    'citation-index':'citation_index', 'citation_index':'citation_index',
    'court_reference':'court_reference', 'judge_portraits':'judge_portraits',
}
MODES = {
    'cpsc_injury_data': ('dataset', ['neiss', 'saferproducts']),
    'docsupload_coverage': ('mode', ['courts', 'documents']),
    'mdl_counsel': ('kind', ['firm', 'attorney', 'party']),
    'counsel_directory': ('kind', ['firm', 'attorney', 'philadelphia_liaison']),
}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def flatten(value):
    if isinstance(value, dict):
        return ' '.join(flatten(v) for k,v in value.items() if k not in ('url','local_path'))
    if isinstance(value, (list, tuple)):
        return ' '.join(flatten(v) for v in value)
    return str(value or '')


def values(value):
    if value is None or value == '': return []
    if isinstance(value, str) and value.startswith(('[', '{')):
        try: value = json.loads(value)
        except ValueError: pass
    if isinstance(value, dict): value = list(value)
    if not isinstance(value, (list, tuple, set)): value = [value]
    return list(dict.fromkeys(str(v) for v in value if v is not None and str(v) != ''))


def native_filters(name, raw, item, mode=None, extra=None):
    """Explicit source-column mappings, never reverse-engineered display labels."""
    r = dict(raw or {}); f = {}
    mappings = {
        'agency_science_documents': {'agency':'family','type':'ext'},
        'source_documents': {'agency':'family','type':'ext'},
        'court_documents': {'state':'state','court':'court_id','doc_type':'doc_type','file_type':'extension','manifest':'manifest','link_status':'link_status','host':'host'},
        'court_spine': {'system':'system','type':'jurisdiction_type','state':'state'},
        'court_statistics': {'series':'series_code','topic':'topic','format':'format','period':'period_end'},
        'judge_disclosures': {'entity_id':'entity_id','year':'year'},
        'federal_register_history': {'type':'type','year':'year'},
        'url_directory': {'layer':'layer','state':'state','agency':'agency_key','court':'court_id','kind':'doc_kind','saved':'saved_status','level':'jurisdiction_level','host':'host','content':'content_type'},
        'mdl_appearances': {'mdl':'mdl_number_extended','firm':'firm_id','role':'role_normalized','side':'party_side'},
        'mdl_docket_activity': {'mdl':'mdl_number','entry_type':'entry_type'},
        'mdl_docket_documents': {'mdl':'mdl_number','doc_type':'doc_type','date':'entry_date_filed'},
        'mdl_crosswalk': {'status':'mdl_status'},
        'indiana_code': {'title':'title_no','status':'status'},
        'saved_pages': {'collection':'collection_label','state':'state','layer':'layer','host':'host'},
        'uscourts_pages': {'section':'section_label'},
        'public_laws': {'congress':'congress','year':'year'},
        'expert_rulings': {'mdl':'mdl_number_crosswalk','court':'court','year':'year','kind':'doc_category'},
        'citation_reference': {'kind':'kind','cite_type':'cite_type'},
        'citation_index': {'kind':'kind','reporter':'reporter'},
        'limitation_periods': {'claim':'claim_type'},
        'verdict_reports': {'type':'result_type','state':'jurisdiction','year':'year','area':'primary_type','amount_band':'amount_band','mdl':'mdl_number'},
    }
    for key,col in mappings.get(name, {}).items(): f[key] = values(r.get(col))
    if name in ('agency_science_documents','source_documents'): f['text']=['yes' if r.get('text_chars') else 'no']
    if name=='court_spine':
        f.update(has_logo=['yes' if r.get('has_logo') else 'no'],has_mdls=['yes' if r.get('pending_mdl_count') else 'no'])
    if name=='court_statistics': f['period']=values(r.get('period_end') or 'undated')
    if name=='judge_disclosures': f['has_holdings']=['yes' if r.get('investment_count') else 'no']
    if name=='verdict_reports': f['mass_tort']=['yes'] if r.get('is_mass_tort') else []
    if name=='public_laws': f['year']=values(str(r.get('approved_date') or '')[:4])
    if name=='public_laws': f.update(usc_title=[],action=[],usc_effect=[])
    if name=='citation_index': f['layer']=[]
    if name=='counsel_directory': f.update(mdl=[],court=[],role=[],side=[],role_text=str(r.get('role_raw') or ''))
    if name=='state_codes': f['state']=values(str(item['id']).split(':')[-1])
    if name=='uscourts_pages':
        f['has']=(['documents'] if r.get('document_link_count') else [])+(['statistics'] if r.get('statistics_tables') not in (None,'[]',[]) else [])+(['document_text'] if r.get('looks_like_document') else [])
    if name=='settlements':
        for key in ('family','deadline_state'): f[key]=values(r.get(key))
        for key,col in (('mass_tort','mass_tort'),('has_documents','document_ids'),('has_court_documents','court_document_ids')):
            f[key]=['yes' if r.get(col) else 'no']
        f.update(state=values(r.get('states')),doc_type=values(r.get('document_types')),date=r.get('claim_deadline') or '')
    if name=='state_proceedings':
        mod=importlib.import_module(name)
        f.update(system=values(mod._system_value(r)),county=values(mod._county_list(r)),category=values(r.get('category')),
                 archived=(['yes' if r.get('archived') else 'no'] if r.get('type')=='nj_mcl' else []),has_related_mdl=['yes' if mod._has_related_mdl(r) else 'no'])
        f['date']=r.get('date_received_iso') or ''
    if name=='url_directory': f['is_noise']=[str(int(bool(r.get('is_noise'))))]
    if name=='citation_reference':
        f['state']=values(values(r.get('states'))+[r.get('jurisdiction')]); f['current']=['yes'] if r.get('still_published') else []
    if name=='citation_index': f['saved']=['yes'] if r.get('local_kind') else []
    if name=='limitation_periods':
        f.update(state=values([r.get('usps'),r.get('state')]),check=values(r.get('outcome') or 'not_checked'))
    if name=='mdl_case_inventory':
        f.update(mdl=values((r.get('mdl') or {}).get('mdl_number')),court=values((r.get('court') or {}).get('court_spine_id')),
                 year=values(r.get('filed_year')),status=values((r.get('status') or {}).get('aws_case_status')),
                 judge=values([x.get(k) for x in r.get('judges',[]) for k in ('judge_entity_id','cl_person_id')]))
    if name in ('mdl_docket_activity','mdl_docket_documents'):
        mod=importlib.import_module(name); f['year']=values(mod._year(r))
        if name=='mdl_docket_documents':
            f['has_free_document']=['yes' if r.get('download_url') and r.get('sha1') else 'no']
            f['date']=r.get('entry_date_filed') or ''
    if name=='mdl_counsel':
        mod=importlib.import_module(name); f.update(kind=values(r.get('kind') or mode),mdl=values(mod._mdls_of(r)))
    if name=='docsupload_coverage':
        f.update(mode=values(mode),collection=values(r.get('known_collection_ids') or r.get('collection_id')),
                 jurisdiction=values(r.get('jurisdictions') or r.get('jurisdiction')),level=values(r.get('level')),
                 availability=['saved' if r.get('linked_documents') else 'missing'])
    if name=='cpsc_injury_data':
        f['dataset']=[mode]
        if mode=='neiss':
            for key in ('year','month','age_band'): f[key]=values(r.get(key))
            for key in ('body_part','diagnosis','disposition','sex'): f[key]=values(r.get(key+'_code'))
            f['product']=values([r.get('product_%s_code'%n) for n in (1,2,3)])
            f['product_text']=[' '.join(str(r.get('product_%s_label'%n) or '') for n in (1,2,3))]
        else:
            for key in ('product_category','state','year','category_of_submitter'): f[key]=values(r.get(key))
    if mode and name in MODES: f[MODES[name][0]]=[mode]
    if extra:
        for key,value in extra.items(): f[key]=values(value)
    return f


@contextmanager
def expanded_listing(mod):
    """Raise only local pagination caps for an offline bulk read, then restore."""
    originals={}
    for name in ('_int','_integer'):
        func=getattr(mod,name,None)
        if not func: continue
        signature=inspect.signature(func)
        def expanded(*args,_func=func,_sig=signature,**kwargs):
            bound=_sig.bind(*args,**kwargs)
            requested=bound.arguments.get('value')
            params=bound.arguments.get('params',bound.arguments.get('p'))
            paramname=bound.arguments.get('name',bound.arguments.get('k'))
            if isinstance(params,dict) and paramname=='limit': requested=params.get('limit')
            if str(requested)=='900000000': return 900000000
            return _func(*args,**kwargs)
        originals[name]=func; setattr(mod,name,expanded)
    if hasattr(mod,'MAX_LIMIT'): originals['MAX_LIMIT']=mod.MAX_LIMIT; mod.MAX_LIMIT=900000000
    try: yield
    finally:
        for name,value in originals.items(): setattr(mod,name,value)


def listing_with_rows(mod, params):
    """Retain the native listing's source rows without serializing private rows."""
    captured={}
    def profile(frame,event,arg):
        if event=='return' and frame.f_code is mod.listing.__code__:
            captured.update(frame.f_locals)
    previous=sys.getprofile(); sys.setprofile(profile)
    try:
        with expanded_listing(mod): result=mod.listing(dict(params,limit=900000000))
    finally: sys.setprofile(previous)
    items=result.get('results') or []
    for key in ('selected','matched','rows','source','courts','tables'):
        rows=captured.get(key)
        if isinstance(rows,list) and len(rows)==len(items): return result,rows,captured
    if not items: return result,[],captured
    raise ValueError('Cannot reconcile native source rows with public items: '+mod.__name__)


def row_renderer(mod, function='detail'):
    """Compile the existing pure formatting tail, failing if it retains IO."""
    source=inspect.getsource(getattr(mod,function)); tree=ast.parse(source); func=tree.body[0]
    marker=None
    for i,node in enumerate(func.body):
        if isinstance(node,ast.If) and ast.unparse(node.test) in ('row is None','not row'):
            marker=i
    if marker is None and mod.__name__=='url_directory':
        marker=next((i for i,n in enumerate(func.body) if isinstance(n,ast.Try)),None)
    if marker is None: raise ValueError('No row validation boundary: '+function)
    body=func.body[marker+1:]
    for node in ast.walk(ast.Module(body=body,type_ignores=[])):
        if isinstance(node,ast.Attribute) and node.attr in ('execute','read_bytes','read_text','connect','open'):
            raise ValueError('Detail tail still contains IO: '+function)
    result=ast.FunctionDef(name='_render_export_row',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=k) for k in ('row','state','members','neighbours')],kwonlyargs=[],kw_defaults=[],defaults=[ast.Constant(None),ast.Constant(None),ast.Constant(None)]),body=body,decorator_list=[])
    compiled=ast.fix_missing_locations(ast.Module(body=[result],type_ignores=[])); namespace=dict(vars(mod))
    exec(compile(compiled,mod.__file__,'exec'),namespace)
    return namespace['_render_export_row']


def grouped(connection, query, key):
    groups=defaultdict(list)
    for row in connection.execute(query): groups[row[key]].append(dict(row))
    return groups


def source_extras(mod, captured, mode):
    """Load filter relationships once, never infer them from rendered strings."""
    name=mod.__name__; out=defaultdict(dict)
    if name not in ('citation_index','counsel_directory','public_laws','uscourts_pages'): return out
    if name in ('citation_index',): conn=mod._connect()
    else: conn=mod._connect(captured['state'])
    try:
        if name=='citation_index':
            for row in conn.execute('SELECT DISTINCT authority_id,layer FROM mentions'):
                out[str(row['authority_id'])].setdefault('layer',[]).append(row['layer'])
        elif name=='public_laws':
            for row in conn.execute('SELECT DISTINCT law_id,usc_title,action_type FROM usc_effects'):
                x=out[str(row['law_id'])]
                x.setdefault('usc_title',[]).append(row['usc_title']); x.setdefault('action',[]).append(row['action_type'])
                x.setdefault('usc_effect',[]).append('%s:%s'%(row['usc_title'],row['action_type']))
        elif name=='counsel_directory' and mode!='philadelphia_liaison':
            key='firm_id' if mode=='firm' else 'attorney_id'
            for row in conn.execute('SELECT ap.'+key+' AS entity_id,ap.mdl_number,ap.role_normalized,ap.side,dk.court FROM appearances ap LEFT JOIN dockets dk ON dk.id=ap.docket_id WHERE ap.'+key+' IS NOT NULL'):
                x=out[str(row['entity_id'])]
                for dest,col in (('mdl','mdl_number'),('role','role_normalized'),('side','side'),('court','court')):
                    x.setdefault(dest,[]).append(row[col])
        elif name=='uscourts_pages':
            small={r[0] for r in conn.execute('SELECT section_label FROM pages GROUP BY 1 HAVING count(*)<3')}
            for row in conn.execute('SELECT id,section_label FROM pages'):
                out[str(row['id'])]['section']=[row['section_label']]+(['other'] if row['section_label'] in small else [])
    finally: conn.close()
    return out


def is_categorized(name, raw):
    if name in ('court_documents',): return bool(raw.get('doc_type')) and raw['doc_type']!='other_unknown'
    if name=='mdl_docket_documents':return bool(raw.get('doc_type')) and str(raw['doc_type']).strip().lower() not in ('other','unknown','uncategorized','other_unknown')
    if name in ('source_documents','agency_science_documents'): return bool(raw.get('family')) and raw['family']!='uncategorized'
    if name=='url_directory': return bool(str(raw.get('content_type') or '').strip()) and not raw.get('is_noise')
    # These layers have a semantic research-area category. Do not reinterpret a
    # missing optional court/state/product field as an uncategorized document.
    return True


def small_rows(mod, listing, rawrows, captured, mode):
    extras=source_extras(mod,captured,mode)
    fulltext_table={'saved_pages':'pages','uscourts_pages':'pages'}.get(mod.__name__)
    text_conn=mod._connect(captured['state']) if fulltext_table else None
    try:
        for item,raw in zip(listing['results'],rawrows):
            row=dict(raw)
            if not is_categorized(mod.__name__,row):
                yield item,None,row,{'__excluded':True}; continue
            more=extras.get(str(item['id']),{})
            if text_conn:
                saved=text_conn.execute('SELECT text FROM '+fulltext_table+' WHERE id=?',(item['id'],)).fetchone()
                if saved: row['_full_text']=saved[0] or ''
            if mod.__name__ in ('source_documents','agency_science_documents'):
                path=mod._confined(row.get('text_path'))
                if path and row.get('text_sha256'):
                    blob=path.read_bytes()
                    if hashlib.sha256(blob).hexdigest()==row['text_sha256']: row['_full_text']=blob.decode('utf-8','replace')
            if mod.__name__=='counsel_directory' and mode=='philadelphia_liaison':
                # Role search is substring based; preserve every visible native option.
                more={'side':values(row.get('side')),'role':[o['value'] for f in listing.get('filters',[]) if f['name']=='role' for o in f.get('options',[]) if o['value'].lower() in str(row.get('role_raw') or '').lower()]}
            yield item,mod.detail(item['id']),row,more
    finally:
        if text_conn: text_conn.close()


def bulk_rows(mod, mode=None):
    """Yield (public item, public detail, raw native row, native filter extras)."""
    name=mod.__name__; state,reason=mod._state()
    if not state: raise ValueError(reason)
    connection=mod._connect(state)
    try:
        if name=='federal_register_history':
            renderer=row_renderer(mod)
            agencies=grouped(connection,'SELECT doc_id, agency_id FROM doc_agency','doc_id')
            cfr=grouped(connection,'SELECT doc_id,cfr_title,cfr_part FROM doc_cfr','doc_id')
            for row in connection.execute('SELECT * FROM docs ORDER BY date DESC,id DESC'):
                refs=cfr.get(row['id'],[])
                extra={'agency':[r['agency_id'] for r in agencies.get(row['id'],[])],
                       'cfr_title':[r['cfr_title'] for r in refs], 'cfr_pair':['%s:%s'%(r['cfr_title'],r['cfr_part']) for r in refs]}
                yield mod._result(row),renderer(row,state),dict(row),extra
        elif name=='url_directory':
            renderer=row_renderer(mod); members=grouped(connection,'SELECT * FROM memberships ORDER BY source_list','url_key')
            for row in connection.execute("SELECT * FROM urls WHERE content_type IS NOT NULL AND trim(content_type)<>'' AND is_noise=0 ORDER BY frontier_rank,host,url"):
                related=members.get(row['url_key'],[])
                yield mod._result(row),renderer(row,state,related[:40]),dict(row),{'list':[r['source_list'] for r in related]}
        elif name=='cpsc_injury_data':
            if mode=='saferproducts':
                table,order,formatter,fn='saferproducts_incidents','report_date_iso DESC,rid',mod._sp_result,'_sp_detail'
            else: table,order,formatter,fn='neiss_cases','treatment_date DESC,rid',mod._neiss_result,'_neiss_detail'
            renderer=row_renderer(mod,fn)
            for row in connection.execute('SELECT * FROM '+table+' ORDER BY '+order):
                yield formatter(row),renderer(row,state),dict(row),{}
        elif name=='indiana_code':
            renderer=row_renderer(mod)
            neighbours=defaultdict(list)
            for n in connection.execute('SELECT id,citation,heading,chapter,title_no FROM sections ORDER BY id'):
                key=(n['chapter'],n['title_no'])
                if len(neighbours[key])<80: neighbours[key].append(dict(n))
            for row in connection.execute('SELECT * FROM sections ORDER BY id'):
                item={'id':str(row['id']),'title':'%s %s'%(row['citation'],row['heading'] or ''),'subtitle':' '.join((row['text'] or '')[:220].split()),
                      'cells':{'title':row['title_heading'] or ('Title '+str(row['title_no'])),'chapter':row['chapter_heading'] or '','status':mod.STATUS_LABELS.get(row['status'],row['status'])},
                      'badges':[] if row['status']=='text' else [mod.STATUS_LABELS.get(row['status'],row['status'])],
                      'links':[{'label':'Indiana General Assembly (live code)','url':'https://iga.in.gov/laws/2026/ic/titles/%s'%row['title_no']}]}
                yield item,renderer(row,state,neighbours=neighbours[(row['chapter'],row['title_no'])]),dict(row),{}
        else: raise ValueError('No bulk renderer '+name)
    finally: connection.close()


def public_links(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k=='url' and isinstance(v,str) and v.startswith('/supplement-files/'): yield v
            else: yield from public_links(v)
    elif isinstance(value,list):
        for v in value: yield from public_links(v)


def detail_dependencies(name, detail):
    """IDs explicitly linked by the native adapter, including chart subviews."""
    ids=set()
    def walk(value):
        if isinstance(value,dict):
            candidate=value.get('id')
            if name=='court_statistics' and isinstance(candidate,str) and '@' in candidate: ids.add(candidate)
            url=value.get('url')
            if isinstance(url,str) and url.startswith('#'):
                route,_,query=url[1:].partition('?'); alias,_,ident=route.partition('/')
                if ALIASES.get(alias)==name:
                    if ident: ids.add(unquote(ident))
                    for key in ('id','item'):
                        ids.update(parse_qs(query).get(key,[]))
            for child in value.values(): walk(child)
        elif isinstance(value,list):
            for child in value: walk(child)
    walk(detail)
    return ids


def artifact(url):
    """Native gate validates original bytes; retain the source path, never copy."""
    parts=url.split('/')
    if len(parts)!=4 or parts[2] not in ALIASES: return None
    mod=importlib.import_module(ALIASES[parts[2]])
    if not hasattr(mod,'original'): return None
    captured={}
    def profile(frame,event,arg):
        if event=='return' and frame.f_code is mod.original.__code__: captured.update(frame.f_locals)
    old=sys.getprofile();sys.setprofile(profile)
    try: result=mod.original(unquote(parts[3]))
    finally: sys.setprofile(old)
    if not result: return None
    blob,mime,filename=result
    path=next((captured[k] for k in ('path','full_path','target') if isinstance(captured.get(k),Path)),None)
    if path is None and mod.__name__=='court_spine':
        logo=captured.get('logo') or {}; ext=mod.RASTER.get(logo.get('mime'))
        if ext: path=mod.DATA/'assets'/(logo['sha256']+'.'+ext)
    if path is None: raise ValueError('Native original path not captured: '+url)
    return {'url':url,'local_path':str(path.resolve()),'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob),'mime':mime,'filename':filename}


def record(name,item,detail,raw,filters,ordinal,artifacts):
    text=raw.get('_full_text',flatten(detail)); filters=dict(filters,_listing=['yes'])
    if name=='mdl_docket_documents':
        # Preserve the native search exclusion of individual plaintiff/caption text.
        text=' '.join(str(raw.get(k) or '') for k in ('docket_number','court','mdl_number','mdl_title','doc_type'))
    elif name=='judge_disclosures': text=' '.join(flatten(s) for s in (detail or {}).get('sections',[]))
    source_url=next((v.get('url') for v in (item.get('links') or []) if str(v.get('url','')).startswith('https://')),'')
    return {'id':str(item['id']),'dataset':name,'category':name,'state':(filters.get('state') or [''])[0],
            'county_geoids':values(raw.get('county_geoids') or raw.get('geoid')),'title':item.get('title') or '',
            'source_url':source_url,'item':item,'detail':detail,'text':text,'filters':filters,'artifacts':artifacts,'ordinal':ordinal}


def export(name,output=OUT,limit=None,with_artifacts=True):
    mod=importlib.import_module(name); output=Path(output); output.mkdir(parents=True,exist_ok=True)
    source_root=getattr(mod,'DATA',getattr(mod,'SOURCE_DIR',None)); source_signature={}; source_files=[]
    if isinstance(source_root,Path) and (source_root/'validation.json').is_file():
        gate_path=source_root/'validation.json';gate=json.loads(gate_path.read_text(encoding='utf-8-sig'))
        for entry in [{'path':'validation.json','sha256':hashlib.sha256(gate_path.read_bytes()).hexdigest()}]+(gate.get('data_files') or []):
            path=(source_root/entry['path']).resolve()
            if path.is_file():
                stat=path.stat();source_signature[path]=(stat.st_size,stat.st_mtime_ns)
                source_files.append({'path':entry['path'],'sha256':entry.get('sha256'),'bytes':stat.st_size})
    started=time.time(); modes=MODES.get(name,(None,[None])); configs={}; seen=set(); count=0; missing=[]; unsupported=[]; excluded=0; auxiliary=0; pending=set()
    facet_counts=defaultdict(lambda:defaultdict(lambda:defaultdict(int))); mode_counts=defaultdict(int)
    if name=='state_codes':
        baseline=mod.listing({}); rows=baseline.get('results',[])
        stream=[(r,mod.detail(r['id']),{}, {}) for r in rows]
        configs['default']={k:v for k,v in baseline.items() if k!='results'}
        streams=[(None,stream)]
    elif name=='judge_portraits':
        baseline=mod.listing({}); configs['default']={k:v for k,v in baseline.items() if k!='results'}; streams=[]
    else:
        streams=[]
        for mode in modes[1]:
            params={modes[0]:mode} if mode else {}
            baseline=mod.listing(params)
            if not baseline.get('available'): raise ValueError(baseline.get('reason','Native gate unavailable'))
            configs[mode or 'default']={k:v for k,v in baseline.items() if k!='results'}
            if name in ('federal_register_history','url_directory','cpsc_injury_data','indiana_code'):
                stream=bulk_rows(mod,mode)
            else:
                listing,rawrows,captured=listing_with_rows(mod,params)
                if len(listing.get('results',[]))!=listing.get('total'):
                    raise ValueError('Incomplete native bulk listing for '+name)
                # court_documents rehashes its entire DB on every detail. Validate once
                # in this single-process export, and use the same immutable snapshot.
                if name=='court_documents':
                    gate=mod._gate(); mod._gate=lambda:gate
                if name in ('mdl_case_inventory','mdl_crosswalk'):
                    # These JSON adapters rehash every source blob on every
                    # detail. The export already pins source stats and checks
                    # them again before publication; render one validated read.
                    snapshot=mod._load();mod._load=lambda *args,**kwargs:snapshot
                stream=small_rows(mod,listing,rawrows,captured,mode)
            streams.append((mode,stream))
    temp=output/(name+'.jsonl.partial'); final=output/(name+'.jsonl')
    assetcache={}
    with temp.open('w',encoding='utf-8',newline='\n') as handle:
        for mode,stream in streams:
            for item,detail,raw,extra in stream:
                if extra.pop('__excluded',False): excluded+=1; continue
                if str(item['id']) in seen: raise ValueError('Duplicate source identity '+str(item['id']))
                seen.add(str(item['id']))
                filters=native_filters(name,raw,item,mode,extra)
                mode_counts[mode or 'default']+=1
                for key,value in filters.items():
                    for v in values(value): facet_counts[mode or 'default'][key][v]+=1
                baseline=configs[mode or 'default']
                required={f['name'] for f in baseline.get('filters',[]) if f.get('type')=='select'}-{'include_noise'}
                unsupported.extend(sorted(required-set(filters)-{'q'}))
                assets=[]
                if with_artifacts:
                    for url in set(public_links([item,detail])):
                        if url not in assetcache: assetcache[url]=artifact(url)
                        if assetcache[url]: assets.append(assetcache[url])
                        else: missing.append(url)
                if detail is None and name!='judge_portraits': raise ValueError('Native detail missing '+str(item['id']))
                pending.update(detail_dependencies(name,detail))
                handle.write(dumps(record(name,item,detail,raw,filters,count,assets))+'\n'); count+=1
                if limit and count>=limit: break
                if count%10000==0: print(dumps({'dataset':name,'written':count,'elapsed_s':round(time.time()-started,1)}),flush=True)
            if limit and count>=limit: break
        if not limit:
            while pending-seen:
                ident=min(pending-seen); seen.add(ident)
                detail=mod.detail(ident)
                if detail is None: raise ValueError('Linked native detail missing '+ident)
                pending.update(detail_dependencies(name,detail))
                assets=[]
                if with_artifacts:
                    for url in set(public_links(detail)):
                        if url not in assetcache: assetcache[url]=artifact(url)
                        if assetcache[url]: assets.append(assetcache[url])
                        else: missing.append(url)
                item={'id':ident,'title':detail.get('title') or ident,'subtitle':detail.get('subtitle') or '','cells':{},'links':[]}
                exported=record(name,item,detail,{}, {},count,assets);exported['filters']['_listing']=['no']
                handle.write(dumps(exported)+'\n');count+=1;auxiliary+=1
    for path,signature in source_signature.items():
        stat=path.stat()
        if (stat.st_size,stat.st_mtime_ns)!=signature: raise ValueError('Source changed during export: '+path.name)
    temp.replace(final)
    native_expected=sum(c.get('total',0) for c in configs.values())
    expected=native_expected-excluded+auxiliary
    if name=='url_directory':
        state,_=mod._state(); connection=mod._connect(state)
        try:
            source_total=connection.execute('SELECT count(*) FROM urls').fetchone()[0]
            expected=connection.execute("SELECT count(*) FROM urls WHERE content_type IS NOT NULL AND trim(content_type)<>'' AND is_noise=0").fetchone()[0]
            excluded=source_total-expected;native_expected=source_total
        finally: connection.close()
    if name=='judge_portraits': expected=0
    if not limit:
        for mode,config in configs.items():
            config['total']=mode_counts[mode]
            for filt in config.get('filters',[]):
                if 'options' not in filt: continue
                counts=facet_counts[mode].get(filt['name'],{})
                filt['options']=[dict(o,count=counts.get(str(o['value']),0)) for o in filt['options'] if counts.get(str(o['value']),0) or filt['name']==modes[0]]
                if filt['name']==modes[0]:
                    for opt in filt['options']: opt['count']=mode_counts.get(str(opt['value']),0)
    with final.open('rb') as payload:export_hash=hashlib.file_digest(payload,'sha256').hexdigest()
    manifest={'id':name,'label':name.replace('_',' ').title(),'aliases':[k for k,v in ALIASES.items() if v==name],
              'listing':configs.get('default') or configs[next(iter(configs))],'listing_modes':configs,'mode_parameter':modes[0],
              'summary':{'records':count,'listing_records':count-auxiliary,'detail_only_records':auxiliary,'native_default_records':native_expected,'excluded_uncategorized':excluded,'artifacts':sum(v is not None for v in assetcache.values()),'missing_artifacts':len(set(missing))},
              'expected_records':expected,'exported_records':count,'ready':False,'export_complete':count==expected and not limit,
              'unsupported_filter_keys':sorted(set(unsupported)),'missing_assets':sorted(set(missing)),
              'search_semantics':'normalized source text; hosted tokenizer requires acceptance tests',
              'adapter_sha256':hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest(),
              'source_files':source_files,
              'export_jsonl_sha256':export_hash,
              'elapsed_s':round(time.time()-started,2)}
    (output/(name+'.dataset.json')).write_text(dumps(manifest),encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets',nargs='+',default=sorted(set(ALIASES.values())))
    parser.add_argument('--output',type=Path,default=OUT); parser.add_argument('--limit',type=int)
    parser.add_argument('--skip-artifacts',action='store_true')
    args=parser.parse_args(); receipts=[]
    for name in args.datasets:
        if name not in set(ALIASES.values()): raise ValueError('Unknown dataset '+name)
        try:
            receipt=export(name,args.output,args.limit,not args.skip_artifacts)
            receipts.append({'dataset':name,'status':'exported',**{k:receipt[k] for k in ('exported_records','expected_records','export_complete','unsupported_filter_keys','elapsed_s')}})
        except Exception as error: receipts.append({'dataset':name,'status':'failed','error':str(error)})
        print(dumps(receipts[-1]),flush=True)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'receipt.json').write_text(dumps(receipts),encoding='utf-8')
    return 0 if all(r['status']=='exported' for r in receipts) else 1


if __name__=='__main__': raise SystemExit(main())
