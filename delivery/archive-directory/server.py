"""Read-only, loopback-only catalogue over the published legal archive.

Run with Python 3.11+ and lxml. No arbitrary workspace serving.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, hashlib, json, mimetypes, os
from pathlib import Path
import sqlite3, threading, shutil, gzip, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from contextlib import contextmanager
from readable import reading_view,plain_profile,VERSION as READING_VERSION
from presentation import build_groups,source_list
import bulk_laws
import recovery
import categories
import judges
import local_library
import people
import explore
import county_registry
import source_directory
import source_captures
import source_api_context
import source_archive_links
import supplements
import doj_resources
import agency_safety
import jurisdiction_coverage
import federal_regulations
import record_facets
import mdl_registry
import trellis_coverage
import county_reader
import county_litigation
import hosting
from evidence_dates import document_dates

# Query parameters that the derived-facet sidecar understands (public name -> sidecar filter key).
FACET_PARAMS={'file_type':'ftype','record_type':'rtype','review':'review','jur_level':'jur_level','subtype':'subtype','dtype':'dtype',
              'validity':'validity','date_type':'date_type','dfrom':'dfrom','dto':'dto','undated':'undated'}
FACET_LIST_COLUMNS=('derived_category','review_state','doc_subtype','record_type','file_type','jurisdiction_level','display_title','title_basis',
                    'title_issue','validity','saved_at','saved_at_field','source_as_of','published_at','effective_from','court_label_as_published')

def facet_filters(params):
    """Sidecar filters requested by the caller; validity defaults to sound captures plus link-only records."""
    filters={key:params[name] for name,key in FACET_PARAMS.items() if params.get(name)}
    return filters

def attach_facets(c):
    path=record_facets.db_path()
    if not path:return False
    c.execute('ATTACH DATABASE ? AS facets',(Path(path).resolve().as_uri()+'?mode=ro',))
    return True

def facet_condition(filters,category):
    """SQL membership test over the attached sidecar; invalid values match nothing. Never mutates the caller's filters."""
    filters=dict(filters)
    validity=filters.pop('validity',None)
    if category:filters['category']=category
    fragment,args=record_facets.where_clause(filters,alias='f')
    if validity=='any':extra=''
    elif validity:
        if validity not in record_facets.LABELS['validity']:return '0',[]
        extra=' AND f.validity=?';args=list(args)+[validity]
    else:extra=" AND f.validity IN ('ok','no_capture')"
    return 'id IN (SELECT f.record_id FROM facets.facets f WHERE '+fragment+extra+')',list(args)

def facet_rows(c,ids):
    if not ids:return {}
    out={}
    for i in range(0,len(ids),400):
        chunk=ids[i:i+400]
        for row in c.execute('SELECT record_id,'+','.join(FACET_LIST_COLUMNS)+' FROM facets.facets WHERE record_id IN ('+','.join('?'*len(chunk))+')',chunk):
            d=dict(row)
            out[d['record_id']]={'derived_category':d['derived_category'],'category_label':record_facets.LABELS['category'].get(d['derived_category'],d['derived_category']),
                                 'review_state':d['review_state'],'review_label':record_facets.LABELS['review'].get(d['review_state'],d['review_state']),
                                 'doc_subtype':d['doc_subtype'],'record_type':d['record_type'],'file_type':d['file_type'],'jurisdiction_level':d['jurisdiction_level'],
                                 'court_label_as_published':d['court_label_as_published'],'display_title':d['display_title'],'title_basis':d['title_basis'],'title_issue':d['title_issue'],
                                 'validity':d['validity'],'dates':{'saved_at':d['saved_at'],'saved_at_field':d['saved_at_field'],'source_as_of':d['source_as_of'],'published_at':d['published_at'],'effective_from':d['effective_from']}}
    return out

def facet_options():
    labels=record_facets.LABELS
    return {'category':labels['category'],'review':labels['review'],'record_type':labels.get('rtype',{}),'file_type':labels.get('ftype',{}),
            'jur_level':labels.get('jur_level',{}),'validity':labels.get('validity',{}),'subtype':labels.get('subtype',{}),'dtype':labels.get('dtype',{}),
            'date_type':{k:k.replace('_',' ') for k in record_facets.DATE_TYPES}}

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FOCUSED = ROOT / 'delivery/focused_legal_corpus'
DB = HERE / 'directory.sqlite3'
CATALOG = ROOT / 'catalog/documents.sqlite3'
READING = ROOT / 'sources/reading_views_20260918/reading.sqlite3'
PORT = 8769
PATH_CACHE = None
TRELLIS_BROWSER_BATCHES = (
    'sources/counties/trellis_browser_backfill_20260918',
    'sources/counties/trellis_browser_backfill_20260918T2213',
)

def read_json(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError): return {} if default is None else default

def jsonl(path):
    if not Path(path).exists(): return
    with Path(path).open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip(): yield json.loads(line)

@contextmanager
def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    try:yield db
    finally:db.close()

def dumps(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def sid(value): return hashlib.sha256(str(value).encode()).hexdigest()[:32]

def safe_path(value):
    if not isinstance(value, str) or not value or '\x00' in value: return None
    if PATH_CACHE is not None and value in PATH_CACHE:return PATH_CACHE[value]
    path = (ROOT / value).resolve()
    try: relative = path.relative_to(ROOT)
    except ValueError: return None
    # Explicit data roots only; secrets/configuration/code are never downloadable.
    if not relative.parts or relative.parts[0] not in {'delivery', 'corpus', 'sources', 'official_laws', 'official_courts', 'trellis_public', 'reports', 'catalog'}: return None
    if any(p.startswith('.') for p in relative.parts): return None
    result=path if path.is_file() else None
    if PATH_CACHE is not None:PATH_CACHE[value]=result
    return result

def paths_in(value):
    """Only fields which explicitly identify evidence artifacts, not arbitrary strings."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'source_path','raw_path','text_path','evidence_file','file','path','metadata_path'} and isinstance(child,str):
                p = safe_path(child)
                if p: yield p
            elif isinstance(child,(dict,list)): yield from paths_in(child)
    elif isinstance(value,list):
        for child in value: yield from paths_in(child)

def snapshot(folder):
    return ROOT / read_json(ROOT / folder / 'latest.json').get('snapshot_path', folder)

def load_trellis_browser_batches(root=None, folders=None):
    """Read immutable, explicitly registered batches; reject partial or changed evidence."""
    root = Path(root or ROOT).resolve()
    folders = TRELLIS_BROWSER_BATCHES if folders is None else folders
    batches, seen_ids, seen_urls = [], set(), set()
    for folder in folders:
        base = (root / folder).resolve()
        if not base.is_relative_to(root):
            raise ValueError('Trellis browser batch is outside the archive')
        try:
            manifest = (base / 'resources.jsonl').read_bytes()
            summary = json.loads((base / 'summary.json').read_text(encoding='utf-8-sig'))
            receipt = json.loads((base / 'validation.json').read_text(encoding='utf-8-sig'))
            resources = [json.loads(line) for line in manifest.decode('utf-8-sig').splitlines() if line.strip()]
        except (OSError, ValueError) as exc:
            raise ValueError(f'Incomplete or invalid Trellis browser batch: {folder}') from exc
        digest = hashlib.sha256(manifest).hexdigest()
        if not isinstance(summary, dict) or not isinstance(receipt, dict) or not resources:
            raise ValueError(f'Invalid Trellis browser batch receipt: {folder}')
        if (receipt.get('status') != 'passed'
                or any(receipt.get(key) is not True for key in (
                    'directory_integration_ready_with_dom_qualification',
                    'county_name_state_and_observed_parent_links_verified',
                    'source_overlap_dedup_verified'))
                or receipt.get('resources_sha256') != digest
                or summary.get('resources_sha256') != digest
                or summary.get('profile_captures') != len(resources)
                or receipt.get('profiles') != len(resources)
                or receipt.get('unique_source_urls') != len(resources)
                or summary.get('source_overlap_with_prior_saved_profiles') != 0):
            raise ValueError(f'Unvalidated or changed Trellis browser manifest: {folder}')
        for p in resources:
            if not isinstance(p, dict):
                raise ValueError(f'Invalid Trellis browser resource: {folder}')
            source_url, key = p.get('source_url'), p.get('id')
            url = urlsplit(source_url if isinstance(source_url, str) else '')
            parts = url.path.strip('/').split('/')
            canonical_url = source_url.rstrip('/') if isinstance(source_url, str) else ''
            if (url.scheme != 'https' or url.netloc != 'trellis.law' or url.query or url.fragment
                    or len(parts) != 3 or parts[0] != 'coverage' or not all(parts)
                    or not isinstance(key, str) or not key):
                raise ValueError(f'Invalid Trellis browser identity: {folder}')
            if key in seen_ids or canonical_url in seen_urls:
                raise ValueError(f'Duplicate Trellis browser ID or source URL: {folder}')
            seen_ids.add(key); seen_urls.add(canonical_url)
            if (p.get('raw_representation_kind') != 'rendered_dom_json_not_http'
                    or p.get('group') != 'counties' or p.get('resource_kind') != 'coverage_county'
                    or not isinstance(p.get('county_geoids'), list) or len(p['county_geoids']) != 1
                    or not p.get('state') or not p.get('county')):
                raise ValueError(f'Unqualified Trellis county profile: {folder}')
            artifacts = {}
            for field, hash_field in [('raw_path', 'sha256'), ('text_path', 'text_sha256'), ('metadata_path', 'metadata_sha256')]:
                value = p.get(field)
                if not isinstance(value, str) or not value:
                    raise ValueError(f'Missing Trellis browser artifact path: {folder}')
                path = (root / value).resolve()
                if not path.is_relative_to(base):
                    raise ValueError(f'Trellis browser artifact outside its batch: {folder}')
                try:
                    artifacts[field] = path.read_bytes()
                except OSError as exc:
                    raise ValueError(f'Missing Trellis browser artifact: {folder}') from exc
                if hashlib.sha256(artifacts[field]).hexdigest() != p.get(hash_field):
                    raise ValueError(f'Changed Trellis browser artifact: {folder}')
            try:
                metadata = json.loads(artifacts['metadata_path'].decode('utf-8-sig'))
                raw = json.loads(artifacts['raw_path'].decode('utf-8-sig'))
                text = artifacts['text_path'].decode('utf-8-sig')
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f'Invalid Trellis browser artifact content: {folder}') from exc
            if (not isinstance(metadata, dict) or metadata != p.get('metadata')
                    or not isinstance(raw, dict) or raw.get('source_url') != source_url
                    or not text.strip()
                    or any(metadata.get(k) != p.get(k) for k in ('source_url', 'captured_at', 'state', 'county', 'text_path', 'text_sha256'))
                    or metadata.get('county_geoid') != p['county_geoids'][0]
                    or metadata.get('source_representation_path') != p['raw_path']
                    or metadata.get('source_representation_sha256') != p['sha256']
                    or any(metadata.get(k) is not False for k in ('paid_document_entitlement_verified', 'protected_case_fields_scraped', 'case_lists_included'))):
                raise ValueError(f'Unbound Trellis browser metadata: {folder}')
        batches.append({'folder': folder, 'resources': resources, 'summary': summary,
                        'resources_sha256': digest, 'profile_captures': len(resources)})
    return batches

def build_browse(c):
    """Keep listing/filter scans out of rows containing large document bodies."""
    c.executescript('''DROP TABLE IF EXISTS browse;
    CREATE TABLE browse AS SELECT id,title,group_name,dataset,state,county,kind,source_url,quality,content_id,(inline_text!='') AS inline_text,original_id,text_id,coalesce(json_extract(payload,'$.metadata.retrieval_eligible'),1) AS eligible FROM records;
    CREATE UNIQUE INDEX browse_id ON browse(id);
    CREATE INDEX browse_dataset ON browse(dataset,title COLLATE NOCASE,id);
    CREATE INDEX browse_kind ON browse(kind,state);
    CREATE INDEX browse_content ON browse(content_id);
    DROP TABLE IF EXISTS record_groups;
    CREATE TABLE record_groups(record_id TEXT,group_name TEXT,PRIMARY KEY(record_id,group_name));
    CREATE INDEX browse_group ON record_groups(group_name,record_id);
    INSERT OR IGNORE INTO record_groups SELECT id,group_name FROM browse;
    INSERT OR IGNORE INTO record_groups SELECT r.id,j.value FROM records r,json_each(json_extract(r.payload,'$.package_groups_json')) j WHERE r.dataset='focused';
    ''')

def build():
    from pending_titles import writer_lock
    with writer_lock(DB):
        return _build()

def _build():
    """Refresh a metadata-only catalogue; full legal text stays in the shared index."""
    global PATH_CACHE
    # Validate small browser batches before opening or replacing any directory database.
    browser_batches = load_trellis_browser_batches()
    county_rows = list(jsonl(FOCUSED / 'counties/counties.jsonl'))
    county_by_geoid = {x['geoid']: x for x in county_rows}
    prior_profile_urls = {url.rstrip('/') for x in county_rows for url in x.get('trellis_profile_urls') or []}
    for batch in browser_batches:
        for p in batch['resources']:
            county = county_by_geoid.get(p['county_geoids'][0])
            if not county or (county['state'], county['name']) != (p['state'], p['county']):
                raise ValueError('Trellis browser profile does not match the county inventory')
            if p['source_url'].rstrip('/') in prior_profile_urls:
                raise ValueError('Trellis browser profile overlaps a published profile')
    PATH_CACHE={}
    temp = HERE / 'directory.build.sqlite3'
    if temp.exists(): temp.unlink()
    c = sqlite3.connect(temp)
    c.executescript('''
      CREATE TABLE records(id TEXT PRIMARY KEY,title TEXT,group_name TEXT,dataset TEXT,state TEXT,county TEXT,kind TEXT,source_url TEXT,quality TEXT,content_id INTEGER,payload TEXT,inline_text TEXT,original_id TEXT,text_id TEXT);
      CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2');
      CREATE TABLE files(id TEXT PRIMARY KEY,path TEXT UNIQUE);
      CREATE TABLE counties(geoid TEXT PRIMARY KEY,state TEXT,name TEXT,payload TEXT);
      CREATE TABLE record_counties(record_id TEXT,geoid TEXT,PRIMARY KEY(record_id,geoid));
      CREATE INDEX geo_records ON record_counties(geoid,record_id);
      CREATE TABLE settings(key TEXT PRIMARY KEY,payload TEXT);
      CREATE INDEX record_group ON records(group_name,state,kind);
      CREATE INDEX record_content ON records(content_id);
    ''')
    path_cache={}
    def file_id(value):
        if value in path_cache: return path_cache[value]
        p = safe_path(str(value)) if value else None
        if not p: return None
        rel = p.relative_to(ROOT).as_posix(); key = sid(rel)
        c.execute('INSERT OR IGNORE INTO files VALUES(?,?)',(key,rel)); path_cache[value]=key;return key
    states = {x.get('usps'):x['state'] for x in county_rows}
    state_names = set(states.values())
    state_lookup={s.lower():s for s in state_names};state_lookup.update({s.lower():v for s,v in states.items() if s})
    county_by_geoid={x['geoid']:x for x in county_rows}
    county_names={(x['state']+'/'+x['name']).lower():x['geoid'] for x in county_rows}
    url_geo=collections.defaultdict(set);raw_geo=collections.defaultdict(set)
    for p in county_rows:
        for field in ['trellis_profile_urls','reported_website_urls']:
            for v in p.get(field,[]):url_geo[v].add(p['geoid'])
        for field in ['trellis_raw_paths','reported_site_raw_paths','candidate_site_raw_paths']:
            for v in p.get(field,[]):raw_geo[v].add(p['geoid'])
    def state_name(v):
        return state_lookup.get(v.lower(),v) if isinstance(v,str) else ''
    def add(key,title,group,dataset,state,county,kind,url,quality,payload,content_id=None,text='',original=None,text_path=None):
        key = sid(key)
        if isinstance(quality,(dict,list)):quality=json.dumps(quality,ensure_ascii=False,indent=2)
        url = url if isinstance(url,str) and urlsplit(url).scheme in {'http','https'} else ''
        original_id,text_id = file_id(original),file_id(text_path)
        if text_id and not content_id and not text:
            candidate=safe_path(text_path)
            if not candidate or not candidate.read_text(encoding='utf-8',errors='replace').strip():text_id=None
        if not original_id:
            candidates = list(paths_in(payload))
            if candidates: original_id = file_id(str(candidates[0]))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key,title or url or 'Untitled record',group,dataset,state_name(state or ''),county or '',kind or 'Unclassified',url,quality or 'Review required',content_id,dumps(payload),text,original_id,text_id))
        c.execute('INSERT INTO search VALUES(?,?)',(key,' '.join([str(title or ''),str(state or ''),str(county or ''),str(kind or ''),str(url or ''),text])))
    published_triples=set()
    with ro(FOCUSED / 'focused.sqlite3') as f:
        for row in f.execute('SELECT * FROM documents'):
            d = dict(row); contexts = read_json_value(d.get('jurisdictions_json'),[])
            published_triples.add((d['collection'],d['source_url'],d['raw_sha256']))
            geos=set(read_json_value(d.get('county_geoids_json'),[])) | url_geo[d['source_url']] | raw_geo[d['raw_path']]
            for x in contexts:
                if isinstance(x,dict) and x.get('geoid'):geos.add(str(x['geoid']))
                elif isinstance(x,str):
                    norm='/'.join(t.strip() for t in x.split('/') if t.strip()!='US').lower()
                    if norm in county_names:geos.add(county_names[norm])
            geos={g for g in geos if g in county_by_geoid}
            state_values = list(dict.fromkeys(state_name(x.get('state','')) for x in contexts if isinstance(x,dict) and x.get('state')))
            for x in contexts:
                if isinstance(x,str):
                    for value in x.split('/'):
                        if value.strip().lower() in state_lookup:state_values.append(state_name(value.strip()))
            state_values=list(dict.fromkeys(state_values+[county_by_geoid[g]['state'] for g in sorted(geos)]))
            if not state_values:
                state_values = [v for v in state_names if v in (d.get('jurisdiction') or '').split(' / ')]
            county = '; '.join(dict.fromkeys([str(x.get('county')) for x in contexts if isinstance(x,dict) and x.get('county')]+[county_by_geoid[g]['name'] for g in sorted(geos)]))
            group = d['package_group']
            add('focused:'+d['version_id'],d['title'],group,'focused', '; '.join(state_values),county,d.get('county_reviewed_resource_kind') or d.get('content_kind') or d.get('category'),d['source_url'],d.get('body_status') or d.get('index_text_status'),d,d['content_id'],original=d['raw_path'],text_path=d['text_path'])
            for geoid in geos:c.execute('INSERT OR IGNORE INTO record_counties VALUES(?,?)',(sid('focused:'+d['version_id']),geoid))
    judges = snapshot('delivery/judge_enrichment_20260914')
    with ro(judges / 'judge_corpus.sqlite3') as j:
        analyses = collections.defaultdict(list)
        for row in j.execute('SELECT observation_id,payload FROM analyses'):
            analyses[row['observation_id']].append(json.loads(row['payload']))
        for row in j.execute('SELECT * FROM observations'):
            p = json.loads(row['payload']); p['analyses'] = analyses[row['observation_id']]
            text = json.dumps(p,ensure_ascii=False,indent=2)
            add('judge:'+row['observation_id'],row['name'],'judges','judge_enrichment',row['state_code'],'; '.join(read_json_value(row['counties'],[])),row['source_class'],p.get('source_url'),'Source observation; current service and identity may be unresolved',p,text=text)
    vendor = snapshot('delivery/judge_vendor_enrichment_20260914')
    va = collections.defaultdict(list)
    for p in jsonl(vendor/'analyses.jsonl'): va[p.get('source_observation_id')].append(p)
    for p in jsonl(vendor/'observations.jsonl'):
        p['analyses']=va[p.get('source_observation_id')]
        add('vendor:'+p['source_observation_id'],p['name'],'judges','judge_vendor',p.get('state_code'),'','Vendor preview and reported analysis',p.get('source_url'),'Limited public preview; publisher-reported, not independent analytics',p,text=json.dumps(p,ensure_ascii=False,indent=2),original=p.get('source_path'))
    for p in jsonl(ROOT/'sources/judge_entities_20260918/entities.jsonl'):
        members=p.get('members') or [];url=p.get('source_url') or next((m.get('source_url') for m in members if m.get('source_url')),'')
        add('entity:'+p['entity_id'],p['name'],'judges','judge_entities','; '.join(state_name(v) for v in p.get('state_codes') or []),'; '.join(p.get('counties') or []),'Judge profile',url,p.get('identity_status') or 'Evidence-linked profile; current service not independently verified',p,text=p.get('profile_text') or plain_profile(p))
    recovered_text=recovery.load()
    supplements = [(jsonl(ROOT/folder/'resources.jsonl'), group, dataset) for folder,group,dataset in [('sources/federal_directory_20260918','federal','federal'),('sources/targeted_firecrawl_20260918','laws','provider_laws'),('sources/directory_pending_20260918','all','pending_publication'),('sources/seeger_import_20260918','laws','seeger')]]
    supplements += [(batch['resources'], 'counties', 'trellis_browser_counties') for batch in browser_batches]
    for resources,group,dataset in supplements:
        for p in resources:
            if dataset=='seeger':p=recovery.overlay(p,recovered_text)
            if dataset=='pending_publication' and (p.get('collection') or p.get('metadata',{}).get('collection'),p.get('source_url'),p.get('sha256')) in published_triples:continue
            record_key=dataset+':'+str(p.get('id') or p.get('source_url'))
            text_path=p.get('text_path')
            if dataset=='pending_publication' and p.get('metadata',{}).get('text_evidence',{}).get('nonempty') is False:text_path=None
            add(record_key,p.get('title'),p.get('group') or group,dataset,p.get('state_if_explicit') or p.get('state'),p.get('county') or '',p.get('resource_kind') or 'Source resource',p.get('source_url'),p.get('quality') or 'Supplementary capture; completeness not asserted',p,original=p.get('raw_path'),text_path=text_path,text=(safe_path(text_path).read_text(encoding='utf-8',errors='replace') if safe_path(text_path) else ''))
            for geoid in p.get('county_geoids') or []:
                if geoid in county_by_geoid:c.execute('INSERT OR IGNORE INTO record_counties VALUES(?,?)',(sid(record_key),geoid))
    local = {x['geoid']:x for x in jsonl(FOCUSED/'county_local_resources/county_ledger.jsonl')}
    pending_local=dict(c.execute("SELECT rc.geoid,count(DISTINCT r.id) FROM records r JOIN record_counties rc ON rc.record_id=r.id WHERE r.dataset='pending_publication' AND r.group_name='counties' GROUP BY rc.geoid"))
    browser_profiles=collections.defaultdict(set)
    for geoid,url in c.execute("SELECT rc.geoid,r.source_url FROM records r JOIN record_counties rc ON rc.record_id=r.id WHERE r.dataset='trellis_browser_counties'"):
        browser_profiles[geoid].add(url)
    for p in county_rows:
        l = local.get(p['geoid'],{})
        item={'geoid':p['geoid'],'name':p['name'],'state':p['state'],'profile_status':p.get('trellis_profile_status','unknown'),'saved_profiles':len(p.get('trellis_profile_ids') or []),'saved_sites':len(set((p.get('reported_site_capture_ids') or [])+(p.get('candidate_site_capture_ids') or []))),'local_resources':l.get('captured_local_resource_pages_or_documents',0),'complete':False,'website_evidence_status':p.get('website_evidence_status'),'source_association_verified':False}
        item['published_local_resources']=item['local_resources']
        item['pending_local_resources']=pending_local.get(p['geoid'],0)
        item['local_resources']+=item['pending_local_resources']
        new_profile_urls=browser_profiles.get(p['geoid'],set())-set(p.get('trellis_profile_urls') or [])
        item['saved_profile_backfill_count']=len(new_profile_urls)
        item['saved_profiles']+=len(new_profile_urls)
        c.execute('INSERT INTO counties VALUES(?,?,?,?)',(p['geoid'],p['state'],p['name'],dumps(item)))
    definitions=[('focused','Laws, rules & county archive','Published capture records with original files and full-text search.','all',FOCUSED),('judge_enrichment','Judge profiles & evaluations','Source observations, biographies, evidence facts and official evaluations; identity groups are not unique current judges.','judges',judges),('judge_vendor','Vendor judge previews','Two source profiles with 450 publisher-reported measures; coverage is limited.','judges',vendor),('federal','Federal court & DOJ sources','Federal resources and observed links, separate from state/county coverage.','federal',ROOT/'sources/federal_directory_20260918'),('provider_laws','Targeted state-law additions','New provider captures shown separately pending publication review.','laws',ROOT/'sources/targeted_firecrawl_20260918')]
    definitions.append(('pending_publication','New downloads awaiting publication','Saved originals and available extracted text; not yet part of the validated publication totals.','all',ROOT/'sources/directory_pending_20260918'))
    definitions.append(('trellis_browser_counties','County court profile backfill',f"Saved visible county profile fields and source links from {len(browser_batches)} validated browser capture batches. DOM representations, not original HTTP responses or paid case files.",'counties',None))
    definitions.append(('judge_entities','Consolidated judge profiles','Evidence-linked identities with the richest profile, source-bound facts and analysis. Uncertain matches remain separate.','judges',ROOT/'sources/judge_entities_20260918'))
    definitions.append(('seeger','Seeger court documents & legal provisions','Imported original documents and structured provisions with source lineage, extraction status and explicit gaps.','laws',ROOT/'sources/seeger_import_20260918'))
    datasets=[]
    for key,title,description,group,folder in definitions:
        exports=[]
        export_folders = [ROOT / batch['folder'] for batch in browser_batches] if key=='trellis_browser_counties' else [folder]
        for export_folder in export_folders:
            for name in ['README.md','summary.json','documents.csv','documents.jsonl','observations.jsonl','analyses.jsonl','entities.jsonl','members.jsonl','possible_matches.jsonl','resources.jsonl','validation.json']:
                fid=file_id(str(export_folder/name))
                if fid: exports.append({'title':(export_folder.name+' / ' if key=='trellis_browser_counties' else '')+name,'url':'/files/'+fid})
        entry={'id':key,'title':title,'description':description,'group':group,'count':c.execute('SELECT count(*) FROM records WHERE dataset=?',(key,)).fetchone()[0],'guide_url':next((x['url'] for x in exports if x['title']=='README.md' or x['title'].endswith(' / README.md')),''),'files':exports}
        if key=='trellis_browser_counties':
            entry['batches']=[{'folder':b['folder'],'profile_captures':b['profile_captures'],'resources_sha256':b['resources_sha256']} for b in browser_batches]
        datasets.append(entry)
    for key,title,folder in [('county_inventory','County inventory & evidence',FOCUSED/'counties'),('county_local','Local county resources & gaps',FOCUSED/'county_local_resources'),('law_coverage','Law coverage & missing sources',FOCUSED/'laws')]:
        files=[]
        for name in ['README.md','summary.json','counties.csv','counties.jsonl','county_ledger.jsonl','resources.jsonl','official_resources.csv','coverage.csv','jurisdiction_coverage.csv','official_resource_gaps.csv','trellis_gaps.csv']:
            fid=file_id(str(folder/name))
            if fid: files.append({'title':name,'url':'/files/'+fid})
        datasets.append({'id':key,'title':title,'description':'Coverage manifests and source-evidence inventories. Download files for deeper analysis.','group':'counties' if 'county' in key else 'laws','count':3144 if key=='county_inventory' else None,'guide_url':next((x['url'] for x in files if x['title']=='README.md'),''),'files':files})
    summary=read_json(FOCUSED/'summary.json'); cs=read_json(FOCUSED/'counties/summary.json'); ls=read_json(FOCUSED/'laws/summary.json'); local_summary=read_json(FOCUSED/'county_local_resources/summary.json')
    baseline={'published':{'capture_records':summary.get('selected_capture_records'),'searchable_texts':summary.get('distinct_searchable_texts'),'completed_at':summary.get('completed_at'),'full_corpus_complete':False},'coverage':{'states':cs.get('states_and_dc'),'counties':cs.get('county_equivalent_rows'),'matched_counties':cs.get('matched_counties'),'local_counties':sum(bool(x.get('captured_local_resource_pages_or_documents')) for x in local.values()),'official_law_urls':ls.get('official',{}).get('downloaded_resource_urls'),'local_captures':local_summary.get('captured_pages_or_documents')},'judges':{'observations':read_json(judges/'summary.json').get('source_observations'),'profiles':1300,'federal_biographies':4074,'evaluations':116,'analyses':258,'vendor_measures':450},'datasets':datasets,'directory_built_at':now(),'limitations':['Inventory coverage is not complete document coverage.','Saved legal text may require content, edition and currency review.','County website associations are not verified court jurisdiction.','Judge observations include historical records and unresolved identities.']}
    baseline['deduplication']=build_groups(c,ROOT)
    build_browse(c)
    baseline['judges']['entities']=read_json(ROOT/'sources/judge_entities_20260918/summary.json').get('entities')
    c.execute('INSERT INTO settings VALUES(?,?)',('summary',dumps(baseline)))
    c.commit(); c.close(); os.replace(temp,DB)
    PATH_CACHE=None
    (HERE/'build_receipt.json').write_text(json.dumps({'built_at':now(),'database':str(DB.relative_to(ROOT)),'records':sum(x['count'] or 0 for x in datasets[:len(definitions)]),'metadata_only':True,'source_bytes_modified':False},indent=2)+'\n',encoding='utf-8')
    return baseline

def read_json_value(s,default):
    try:return json.loads(s) if s else default
    except (ValueError,TypeError):return default

def enriched_summary(s):
    bulk=bulk_laws.info();seeger=read_json(ROOT/'sources/seeger_import_20260918/summary.json');judges=read_json(ROOT/'sources/judge_entities_20260918/summary.json')
    with ro(DB) as c:
        source_total=c.execute("SELECT count(*) FROM browse WHERE dataset!='judge_entities'").fetchone()[0]
    dedup=dict(s.get('deduplication') or {});dedup['source_observations_total']=source_total
    s['enrichment']={'open_us_law':{'ready':bulk.get('ready',False),'records':bulk.get('indexed_rows') or bulk.get('records') or 0,'files':229 if bulk.get('ready') else 0,'snapshot':bulk.get('snapshot') or 'v2026.08','attribution':'open-us-law; compilation CC BY 4.0','limitations':['Snapshot records include historical, repealed, reserved and guidance material.','Publisher-withdrawn GA and NC statute files are not included.']},'seeger':{'records':seeger.get('resources',0),'originals':seeger.get('retained_court_originals',0)+seeger.get('reference_originals',0),'provisions':seeger.get('structured_provisions',0),'text_gaps':seeger.get('originals_with_partial_or_missing_text_evidence',0)},'judges':{'entities':judges.get('entities',0),'source_observations':judges.get('source_observations',0),'facts':judges.get('source_fact_claims',0),'analyses':judges.get('source_analysis_records',0)},'reading':read_json(ROOT/'sources/reading_views_20260918/summary.json'),'deduplication':dedup}
    s['enrichment']['open_us_law']['indexed_records']=bulk.get('indexed_rows',0)
    if not bulk.get('ready'):
        s['enrichment']['open_us_law']['records']=bulk.get('downloaded_parquet_rows',0)
        s['enrichment']['open_us_law']['files']=bulk.get('downloaded_parquet_files',0)
    recovery_status=read_json(recovery.FOLDER/'validation.json')
    recovery_receipt=read_json(recovery.FOLDER/'directory_receipt.json')
    if recovery_status.get('status')=='passed' and recovery_status.get('overlay_ready') is True and recovery_receipt.get('recovery_manifest_sha256')==recovery_status.get('resources_sha256'):
        s['enrichment']['seeger']['text_recovery']=read_json(recovery.FOLDER/'summary.json')
        for dataset in s['datasets']:
            if dataset['id']=='seeger':
                dataset['files'] += [{'title':'Text recovery: '+name,'url':'/recovery-metadata/'+name} for name in recovery.METADATA_FILES if (recovery.FOLDER/name).is_file()]
    if bulk.get('ready'):
        exports=[]
        for name in bulk_laws.METADATA_FILES:
            if (bulk_laws.FOLDER/name).is_file():exports.append({'title':name,'url':'/bulk-metadata/'+name})
        s['datasets']=[d for d in s['datasets'] if d['id']!='open_us_law']+[{'id':'open_us_law','title':'Open US Law bulk snapshot','description':'Structured law snapshot with verified file checksums: statutes, constitutions, court rules, regulations and guidance. Publisher citations and status reflect this dated snapshot. Official source links are missing for some rows.','group':'laws','count':bulk.get('indexed_rows') or bulk.get('records'),'guide_url':'/bulk-metadata/README.md','files':exports}]
    # Display titles only: the stored summary predates the neutral wording; identifiers and files are unchanged.
    for entry in s.get('datasets') or []:
        if entry.get('id')=='trellis_browser_counties':entry['title']='County court profile backfill'
    return s

def public_item(row):
    d=dict(row)
    # The immutable capture records describe their pre-integration state. Once
    # served from this indexed dataset, show its current directory availability.
    if d.get('dataset') == 'trellis_browser_counties' and isinstance(d.get('quality'), str):
        d['quality'] = d['quality'].replace('; awaiting directory integration', '; available in this directory')
    if (d.get('title') or '').strip().startswith('['):d['title']=(d.get('source_url') or 'Saved source')+' '+d['title'].strip()
    has_text=bool(d['usable_text']) if 'usable_text' in d else bool(d['text_id'] or d['content_id'] or d['inline_text'])
    return {k:d.get(k) for k in ['id','title','dataset','state','county','kind','source_url','quality','source_count','group_basis']} | {'group':d['group_name'],'category':categories.classify(d['kind']),'category_label':categories.LABELS[categories.classify(d['kind'])],'has_original':bool(d['original_id']),'has_text':has_text,'original_url':'/files/'+d['original_id'] if d['original_id'] else '', 'text_url':'/api/text?id='+d['id'] if has_text else '', 'extracted_url':'/files/'+d['text_id'] if d['text_id'] else ''}

def query_documents(params):
    page=max(1,int(params.get('page','1'))); limit=min(100,max(1,int(params.get('limit','50'))))
    with ro(DB) as c:
        c.execute('ATTACH DATABASE ? AS archive',(CATALOG.as_uri()+'?mode=ro',))
        where=[]; args=[]
        group=params.get('group','all')
        if params.get('dataset'):where.append('dataset=?');args.append(params['dataset'])
        sources=params.get('view')=='sources'
        if sources:where.append("dataset!='judge_entities'")
        elif params.get('kind')!='administrative_document':where.append('eligible!=0')
        if group!='all':
            # Published records can belong to more than one group.
            where.append('id IN (SELECT record_id FROM record_groups WHERE group_name=?)')
            args += [group]
        if params.get('state'):
            where.append("instr('; ' || state || '; ', '; ' || ? || '; ') > 0");args.append(params['state'])
        if params.get('kind'):where.append('kind=?');args.append(params['kind'])
        # Derived facets (category, review state, file type, dates, capture validity) come from the hash-gated sidecar;
        # without it the retained kind-based classification is the fallback and sidecar-only filters match nothing.
        facets_ready=attach_facets(c)
        requested=facet_filters(params)
        if facets_ready:
            expression,values=facet_condition(requested,params.get('category'))
            where.append(expression);args.extend(values)
        elif requested:where.append('0')
        elif params.get('category'):
            expression,values=categories.condition(params['category'],[r[0] for r in c.execute('SELECT DISTINCT kind FROM browse')])
            where.append(expression);args.extend(values)
        if params.get('county'):
            where.append('id IN (SELECT record_id FROM record_counties WHERE geoid=?)');args.append(params['county'])
        q=params.get('q','').strip()[:300]
        if q:
            phrase='"'+q.replace('"','""')+'"'
            where.append('(id IN (SELECT id FROM search WHERE search MATCH ?) OR content_id IN (SELECT rowid FROM archive.content_fts WHERE content_fts MATCH ?))');args += [phrase,phrase]
        availability=params.get('availability','')
        if sources and availability:where.append(explore.availability_condition(availability))
        clause=' WHERE '+' AND '.join(where) if where else ''
        source_total=c.execute('SELECT count(*) FROM browse'+clause+(' AND ' if where else ' WHERE ')+"dataset!='judge_entities'",args).fetchone()[0]
        list_columns="r.id,r.title,r.group_name,r.dataset,r.state,r.county,r.kind,r.source_url,r.quality,r.content_id,r.inline_text,r.original_id,r.text_id,"+explore.local_text_sql('r')+' AS usable_text'
        if sources:
            main_total=source_total
            sql='SELECT '+list_columns+' FROM browse r'+clause+' ORDER BY r.title COLLATE NOCASE,r.id LIMIT ? OFFSET ?'
        else:
            matched='SELECT m.display_id FROM display_members m WHERE m.record_id IN (SELECT id FROM browse'+clause+')'
            available=(' AND g.preferred_id IN (SELECT a.id FROM browse a WHERE '+explore.availability_condition(availability,'a')+')') if availability else ''
            main_total=c.execute('SELECT count(*) FROM display_groups g WHERE g.id IN ('+matched+')'+available,args).fetchone()[0]
            source_total=c.execute('SELECT coalesce(sum(g.source_count),0) FROM display_groups g WHERE g.id IN ('+matched+')'+available,args).fetchone()[0]
            # Match every member, then display the best preserved representation.
            sql='SELECT '+list_columns+',g.id AS display_id,g.title AS display_title,g.state AS display_state,g.county AS display_county,g.source_count,g.group_basis FROM display_groups g JOIN browse r ON r.id=g.preferred_id WHERE g.id IN ('+matched+')'+available+' ORDER BY g.title COLLATE NOCASE,g.id LIMIT ? OFFSET ?'
        offset=(page-1)*limit
        rows=c.execute(sql,args+[limit,offset]).fetchall() if offset<main_total else []
        items=[]
        facet_map=facet_rows(c,[row['id'] for row in rows]) if facets_ready else {}
        for row in rows:
            value=dict(row);record_id=value['id']
            if not sources:value.update(id=value['display_id'],title=value['display_title'],state=value['display_state'],county=value['display_county'])
            item=public_item(value);facet=facet_map.get(record_id)
            if facet:
                item['original_category']=item['category'];item['category']=facet['derived_category'];item['category_label']=facet['category_label']
            item['facets']=facet
            items.append(item)
        # Open US Law publisher rows are not directory records and carry no derived facets: sidecar-only filters exclude them.
        bulk_total=bulk_laws.count(params) if not requested else 0
        if len(items)<limit and not requested:items.extend(bulk_laws.query(params,max(0,offset-main_total),limit-len(items)))
        filter_clause=' WHERE id IN (SELECT record_id FROM record_groups WHERE group_name=?)' if group!='all' else '';filter_args=[group] if group!='all' else []
        states=sorted({s for r in c.execute('SELECT DISTINCT state FROM browse'+filter_clause,filter_args) for s in r[0].split('; ') if s})
        kinds=[r[0] for r in c.execute('SELECT DISTINCT kind FROM browse'+filter_clause+' ORDER BY kind',filter_args) if r[0]]
        if bulk_laws.eligible(params):
            extra_states,extra_kinds=bulk_laws.filters();states=sorted(set(states+extra_states));kinds=sorted(set(kinds+extra_kinds))
        category_options=[{'id':k,'label':v} for k,v in record_facets.LABELS['category'].items()] if facets_ready else categories.options(kinds)
        return {'items':items,'total':main_total+bulk_total,'source_total':source_total+bulk_total,'page':page,'limit':limit,'states':states,'kinds':kinds,'categories':category_options,
                'facets_ready':facets_ready,'facet_options':facet_options() if facets_ready else {},
                'facet_note':('Categories, review states, file types and dates come from the derived-facet sidecar; the original kind stays on each record. '
                              'Parked, empty and shell captures are hidden unless a capture validity is chosen.') if facets_ready else 'Derived facets are not available; categories fall back to the retained kind.',
                'view':'sources' if sources else 'grouped','order':'Local collections by title, then bulk records in publisher order'}

def record_detail(key,full=False):
    if key.startswith('county-litigation:'):
        result=county_litigation.detail(key)
        if result and full:
            content=county_litigation.asset(key,'text')
            result['text']=content[0].decode('utf-8-sig',errors='replace') if content else ''
            result['text_truncated']=False
        return result
    if key.startswith('oul:'):
        result = bulk_laws.detail(key,full)
        if result:
            result.update(document_dates(result.get('metadata')))
            result['snapshot_label'] = (result.get('metadata') or {}).get('snapshot')
        return result
    with ro(DB) as c:
        group=c.execute('SELECT * FROM display_groups WHERE id=?',(key,)).fetchone()
        source_key=group['preferred_id'] if group else key
        row=c.execute('SELECT * FROM records WHERE id=?',(source_key,)).fetchone()
        if row is None:return None
        result=public_item(row); p=json.loads(row['payload']); text=row['inline_text'] or '';canonical_text_sha=None
        facet=record_facets.facets_for(source_key)
        if facet:result['original_category']=result['category'];result['category']=facet['derived_category'];result['category_label']=facet.get('category_label') or record_facets.LABELS['category'].get(facet['derived_category'],facet['derived_category'])
        result['facets']=facet
        if group:result.update(id=key,title=group['title'],state=group['state'],county=group['county'],source_count=group['source_count'],group_basis=group['group_basis'],text_url='/api/text?id='+key)
        if row['content_id']:
            with ro(CATALOG) as a:
                found=a.execute('SELECT text,text_sha256 FROM contents WHERE id=?',(row['content_id'],)).fetchone()
                if found:text=found[0];canonical_text_sha=found[1]
        elif row['text_id']:
            f=c.execute('SELECT path FROM files WHERE id=?',(row['text_id'],)).fetchone(); path=safe_path(f[0]) if f else None
            if path:text=path.read_text(encoding='utf-8',errors='replace')
        view=county_reader.reading_view(row['source_url'],safe_path(p.get('raw_path')),p.get('raw_sha256') or p.get('sha256'))
        if view is None and READING.exists():
            with ro(READING) as clean:
                cached=clean.execute('SELECT text,links,notes FROM reading WHERE record_id=? AND version=?',(source_key,READING_VERSION)).fetchone()
                if cached:
                    notes=json.loads(cached['notes']);expected_raw=p.get('raw_sha256') or p.get('sha256');expected_text=canonical_text_sha or p.get('text_file_sha256') or p.get('text_sha256')
                    same_raw=notes.get('parent_raw_sha256')==expected_raw
                    same_text=not expected_text or notes.get('parent_text_sha256',notes.get('input_text_sha256'))==expected_text
                    same_scope=bool(notes.get('section_text_from_exact_derivative'))==str(p.get('kind','')).endswith('_provision')
                    if same_raw and same_text and same_scope:view={'text':cached['text'],'links':json.loads(cached['links']),'notes':notes}
        if view is None:
            if row['dataset'] in {'judge_enrichment','judge_vendor','judge_entities'}:
                view={'text':p.get('profile_text') or plain_profile(p),'links':[],'notes':{'method':'Source-bound judge profile','original_preserved':True}}
            else:view=reading_view(text,title=row['title'],source_url=row['source_url'],raw_path=None if str(p.get('kind','')).endswith('_provision') else safe_path(p.get('raw_path')))
        if p.get('capture_kind')=='ocr_derivative' or str(p.get('extraction_status','')).startswith('ocr_'):
            evidence=read_json_value(p.get('source_evidence_json'),{})
            view['notes']['ocr']={k:evidence.get(k) for k in ['ocr_status','ocr_scope','ocr_pages_completed','ocr_pages_required','pdf_pages','mean_page_confidence','pages_below_70_confidence','ocr_engine']}
            view['notes']['ocr']['engine_confidence_is_not_verified_accuracy']=True
        recovered=p.get('metadata',{}).get('text_recovery') if isinstance(p.get('metadata'),dict) else None
        if recovered:view['notes']['recovery']={k:recovered.get(k) for k in ['method','quality_notes','metadata_path','text_sha256']}
        text=view['text'];sources=source_list(c,key) if group else [public_item(row)]
        result.update({'metadata':p,'text':text if full else text[:60000],'text_truncated':not full and len(text)>60000,'text_characters':len(text),'reading_notes':view['notes'],'links':view['links'],'source_records':sources})
        if view.get('county_profile') is not None:result['county_profile']=view['county_profile']
        result.update(document_dates(p))
        result['has_text'] = bool(text.strip())
        if not result['has_text']: result['text_url'] = ''
        return result

COUNTY_AVAILABILITY = {
    'local_resources': ('Has local resources', "COALESCE(json_extract(payload,'$.local_resources'),0)>0"),
    'saved_information': ('Has saved county information', "COALESCE(json_extract(payload,'$.saved_profiles'),0)+COALESCE(json_extract(payload,'$.saved_sites'),0)>0"),
    'no_local_resources': ('No local resources saved yet', "COALESCE(json_extract(payload,'$.local_resources'),0)=0"),
}


def query_counties(params):
    """Filter recorded availability without treating inventory as complete coverage."""
    where=[];args=[];page=max(1,int(params.get('page',1)));limit=min(100,max(1,int(params.get('limit',50))))
    registry_geoids = county_registry.covered_geoids()
    registry_sql = 'geoid IN ('+','.join('?' for _ in registry_geoids)+')' if registry_geoids else '0'
    choices = {key:(label,expression,[]) for key,(label,expression) in COUNTY_AVAILABILITY.items()}
    label,expression,_ = choices['saved_information']
    choices['saved_information'] = (label,'('+expression+' OR '+registry_sql+')',list(registry_geoids))
    choices['court_registry'] = ('Has court & clerk registry',registry_sql,list(registry_geoids))
    litigation_counts=county_litigation.county_counts()
    litigation_geoids=sorted(litigation_counts)
    litigation_sql='geoid IN ('+','.join('?' for _ in litigation_geoids)+')' if litigation_geoids else '0'
    choices['litigation_resources']=('Has structured litigation resources',litigation_sql,litigation_geoids)
    coverage = trellis_coverage.load()
    trellis_geoids = sorted({r['fips'] for r in coverage.get('counties', []) if r.get('fips') and r.get('detail_captured') is True})
    trellis_sql = 'geoid IN ('+','.join('?' for _ in trellis_geoids)+')' if trellis_geoids else '0'
    choices['trellis_details'] = ('Has refreshed county profile details',trellis_sql,trellis_geoids)
    saved_label,saved_expression,saved_args = choices['saved_information']
    choices['saved_information'] = (saved_label,'('+saved_expression+' OR '+trellis_sql+')',saved_args+trellis_geoids)
    detailed_by_fips = {r['fips']:r for r in coverage.get('counties',[]) if r.get('fips') and r.get('detail_captured') is True}
    if params.get('state'):where.append('state=?');args.append(params['state'])
    if params.get('q'):
        term=params['q'][:200]
        literal=term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        where.append("(name LIKE ? ESCAPE '\\' OR geoid=?)");args.extend(['%'+literal+'%',term])
    base_clause=' WHERE '+' AND '.join(where) if where else ''
    with ro(DB) as connection:
        facets=[]
        for value,(label,expression,extra_args) in choices.items():
            clause=base_clause+(' AND ' if where else ' WHERE ')+expression
            count=connection.execute('SELECT count(*) FROM counties'+clause,args+extra_args).fetchone()[0]
            facets.append({'value':value,'label':label,'count':count})
        if params.get('availability'):
            choice=choices.get(params['availability'])
            where.append(choice[1] if choice else '0')
            if choice: args.extend(choice[2])
        clause=' WHERE '+' AND '.join(where) if where else ''
        total=connection.execute('SELECT count(*) FROM counties'+clause,args).fetchone()[0]
        items=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM counties'+clause+' ORDER BY state,name LIMIT ? OFFSET ?',args+[limit,(page-1)*limit])]
        for item in items:
            item['litigation_resources']=litigation_counts.get(item.get('geoid'),0)
            item['has_court_registry'] = item.get('geoid') in registry_geoids
            item['has_trellis_detail'] = item.get('geoid') in trellis_geoids
            refreshed = detailed_by_fips.get(item.get('geoid'), {})
            refreshed_urls = {r['source_url'].rstrip('/') for r in refreshed.get('detail_sources',[]) if r.get('source_url')}
            stored_urls = {r[0].rstrip('/') for r in connection.execute('SELECT r.source_url FROM records r JOIN record_counties rc ON r.id=rc.record_id WHERE rc.geoid=?',(item.get('geoid'),)) if r[0]} if refreshed_urls else set()
            item['refreshed_trellis_profiles'] = len(refreshed_urls)
            item['refreshed_profiles_outside_document_index'] = len(refreshed_urls-stored_urls)
            item['saved_profiles'] = (item.get('saved_profiles') or 0)+item['refreshed_profiles_outside_document_index']
            visual=local_library.county_visual(item.get('geoid'))
            if visual:
                visual['image_url']='/library-assets/'+visual['asset_id'];item['visual']=visual
        return {'items':items,'total':total,'page':page,'limit':limit,'states':[row[0] for row in connection.execute('SELECT DISTINCT state FROM counties ORDER BY state')],
                'availabilities':facets}


GENERIC_AREAS={'court-coverage':'docsupload_coverage','docsupload_coverage':'docsupload_coverage',
               'settlements':'settlements','statistics':'court_statistics','court_statistics':'court_statistics','courts':'court_spine','court_spine':'court_spine','counsel':'mdl_counsel','mdl_counsel':'mdl_counsel',
               'urls':'url_directory','url_directory':'url_directory','uscourts':'uscourts_pages','uscourts_pages':'uscourts_pages',
               'state-proceedings':'state_proceedings','court-documents':'court_documents','judge-disclosures':'judge_disclosures',
               'state_proceedings':'state_proceedings','court_documents':'court_documents','judge_disclosures':'judge_disclosures',
               'federal-register':'federal_register_history','federal_register_history':'federal_register_history',
               'mdl-cases':'mdl_case_inventory','mdl_case_inventory':'mdl_case_inventory','mdl-appearances':'mdl_appearances','mdl_appearances':'mdl_appearances',
               'mdl-documents':'mdl_docket_documents','mdl_docket_documents':'mdl_docket_documents','mdl-activity':'mdl_docket_activity','mdl_docket_activity':'mdl_docket_activity',
               'mdl-crosswalk':'mdl_crosswalk','mdl_crosswalk':'mdl_crosswalk','sd-statutes':'sd_statutes','sd_statutes':'sd_statutes',
               'agency-documents':'agency_science_documents','agency_science_documents':'agency_science_documents',
               'saved-pages':'saved_pages','saved_pages':'saved_pages','indiana-code':'indiana_code','indiana_code':'indiana_code','public-laws':'public_laws','public_laws':'public_laws','state-codes':'state_codes','state_codes':'state_codes',
               'counsel-directory':'counsel_directory','counsel_directory':'counsel_directory','verdict-reports':'verdict_reports','verdict_reports':'verdict_reports',
               'cpsc-injury-data':'cpsc_injury_data','cpsc_injury_data':'cpsc_injury_data','expert-rulings':'expert_rulings','expert_rulings':'expert_rulings',
               'source-documents':'source_documents','source_documents':'source_documents',
               'citation-guide':'citation_reference','citation_reference':'citation_reference','limitation-periods':'limitation_periods','limitation_periods':'limitation_periods',
               'citation-index':'citation_index','citation_index':'citation_index','court_reference':'court_reference','judge_portraits':'judge_portraits'}
# Saved-document areas whose records carry a list of the authorities they cite (citation index layer name).
CITED_IN_AREAS={'agency-documents':'agency-documents','agency_science_documents':'agency-documents','source-documents':'source-documents','source_documents':'source-documents',
                'uscourts':'uscourts','uscourts_pages':'uscourts','saved-pages':'saved-pages','saved_pages':'saved-pages'}
def enrich_generic_item(name,record_id,item):
    """Optional additions to one generic record; every layer is lazy and failure-tolerant, and none can remove what the adapter returned."""
    try:
        if name in ('courts','court_spine'):
            extra=judge_layer('court_reference','for_court',record_id)
            if extra:
                item['facts']=list(item.get('facts') or [])+[f for f in extra['facts'] if f[0] not in {x[0] for x in item.get('facts') or []}]
                item['sections']=extra['sections'][:1]+list(item.get('sections') or [])+extra['sections'][1:]
        elif name in CITED_IN_AREAS:
            cited=None
            try:
                import importlib
                cited=importlib.import_module('citation_index').for_document(CITED_IN_AREAS[name],record_id)
            except Exception:cited=None
            if cited and cited.get('results'):
                item['sections']=list(item.get('sections') or [])+[{'heading':'Authorities cited in this document (%d)'%cited['total'],'items':cited['results'][:40]}]
    except Exception:pass
    return item
def generic_adapter(name):
    """None = unknown area; False = adapter not published yet; else the module. Imported lazily so a missing layer never breaks the server."""
    module=GENERIC_AREAS.get(name)
    if not module:return None
    try:
        import importlib
        loaded=importlib.import_module(module)
        return loaded if hasattr(loaded,'listing') and hasattr(loaded,'detail') else False
    except Exception:
        return False

_SUPPLEMENT_CACHE={'at':0.0,'value':None}
PROSE_KEYS={'qualification','license_ref','license','licence','description','note','notes','attribution','label','title','summary'}
PROSE_NAMES=(('Open US Law index by Vaquill AI','Open US Law index'),('Open US Law by Vaquill AI','Open US Law'),('Vaquill AI / ',''),('Vaquill AI','the snapshot publisher'),('Vaquill','the snapshot publisher'),
             ('Trellis publisher page','Publisher page'),('Trellis publisher-reported','Publisher-reported'),('Trellis county-profile','county-profile'),('Trellis county profile','county court profile'),
             ('Trellis Law','the county directory publisher'),('Trellis','the county directory publisher'))
def display_prose(value,key=None):
    """Vendor names out of reader-facing prose; identifiers, paths and addresses are never touched."""
    if isinstance(value,dict):return {k:display_prose(v,k) for k,v in value.items()}
    if isinstance(value,list):return [display_prose(v,key) for v in value]
    if isinstance(value,str) and key in PROSE_KEYS and ('Trellis' in value or 'Vaquill' in value) and not value.startswith(('http://','https://','/')):
        for old,new in PROSE_NAMES:value=value.replace(old,new)
    return value

def supplement_status(max_age=45):
    """Readiness scan re-hashes large data files; reuse the answer briefly so navigation never waits on it."""
    import time
    def refresh():
        try:_SUPPLEMENT_CACHE['value']=display_prose(supplements.status());_SUPPLEMENT_CACHE['at']=time.time()  # scrubbed once per scan, not per request
        finally:_SUPPLEMENT_CACHE['busy']=False
    if _SUPPLEMENT_CACHE['value'] is None:
        refresh()
    elif time.time()-_SUPPLEMENT_CACHE['at']>max_age and not _SUPPLEMENT_CACHE.get('busy'):
        # Answer with the last scan at once and re-hash in the background, so navigation never waits on large files.
        _SUPPLEMENT_CACHE['busy']=True;threading.Thread(target=refresh,daemon=True).start()
    return _SUPPLEMENT_CACHE['value']

def judge_layer(module,function,entity_id):
    """Optional judge data layer, imported lazily; a missing or failing layer simply contributes nothing."""
    try:
        import importlib
        return getattr(importlib.import_module(module),function)(entity_id)
    except Exception:
        return None

def judge_overlay(entity_id):
    """Structured FJC/CourtListener overlay for a judge profile when that layer is published; otherwise nothing."""
    try:
        import importlib
        return importlib.import_module('judge_structured').overlay_for(entity_id)
    except Exception:
        return None

class Handler(BaseHTTPRequestHandler):
    server_version='LegalArchive/1.0'
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def send_response(self,*args,**kwargs):
        self.response_started=True
        return super().send_response(*args,**kwargs)
    def send_body(self,status,data,mime='application/json; charset=utf-8',attachment=None,sandbox=False):
        if not isinstance(data,bytes):data=dumps(data).encode('utf-8')
        compressed=len(data)>16384 and 'gzip' in self.headers.get('Accept-Encoding','')
        if compressed:data=gzip.compress(data,compresslevel=4)
        self.send_response(status); self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
        if compressed:self.send_header('Content-Encoding','gzip');self.send_header('Vary','Accept-Encoding')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'none'; sandbox" if sandbox else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if attachment:self.send_header('Content-Disposition','attachment; filename="'+attachment.replace('"','_')+'"')
        self.end_headers()
        if self.command=='HEAD':return
        for offset in range(0,len(data),65536):self.wfile.write(data[offset:offset+65536])
    def do_HEAD(self):self.do_GET()
    def do_GET(self):
        self.response_started=False
        rejected=hosting.authorize(self.headers,self.server.server_port,getattr(self.server,'hosting_config',None) or hosting.configuration('127.0.0.1'))
        if rejected:return self.send_body(rejected[0],{'error':rejected[1]})
        u=urlsplit(self.path); p={k:v[0] for k,v in parse_qs(u.query).items()};path=u.path
        try:
            if path=='/api/health':return self.send_body(200,{'service':'legal-archive','ready':DB.is_file()})
            if path.startswith('/api/trellis-coverage'):
                if path == '/api/trellis-coverage/summary': return self.send_body(200,trellis_coverage.summary())
                if path == '/api/trellis-coverage/progress': return self.send_body(200,trellis_coverage.progress(p.get('state')))
                if path == '/api/trellis-coverage/state':
                    result = trellis_coverage.state(p.get('state'))
                    return self.send_body(200,result or {'available':False})
                if path == '/api/trellis-coverage/county':
                    result = trellis_coverage.county(fips=p.get('fips'),state=p.get('state'),name=p.get('name'))
                    return self.send_body(200,dict(result,available=True) if result else {'available':False})
                if path == '/api/trellis-coverage/receipt':
                    result = trellis_coverage.receipt(p.get('id'))
                    if not result:return self.send_body(404,{'error':'Saved receipt not found'})
                    return self.send_body(200,result[0],result[1],sandbox=True)
                if path == '/api/trellis-coverage':
                    def boolean(key):return {'true':True,'false':False}.get(p.get(key,''))
                    return self.send_body(200,trellis_coverage.listing(state=p.get('state'),detail=boolean('detail'),has_documents=boolean('has_documents'),
                        practice_area=p.get('practice_area'),venue=boolean('venue'),fips_resolved=boolean('fips_resolved'),q=p.get('q',''),limit=p.get('limit',50),offset=p.get('offset',0)))
                return self.send_body(404,{'error':'Unknown Trellis coverage view'})
            if path in {'/','/index.html','/app.js','/areas.js','/usmap.js','/statsviz.js','/judgeui.js','/regsui.js','/lawreader.js','/styles.css'}:
                file=HERE/('index.html' if path=='/' else path[1:])
                if not file.is_file():return self.send_body(200,b'/* module not installed yet */','application/javascript; charset=utf-8')
                return self.send_body(200,file.read_bytes(),mimetypes.guess_type(file)[0]+'; charset=utf-8')
            if path=='/assets/us-counties-albers-10m.json':
                # Open US boundary geometry (us-atlas, ISC licence; see the SOURCE.txt beside it) for the state and county map.
                return self.send_body(200,(HERE/'assets'/'us-counties-albers-10m.json').read_bytes(),'application/json; charset=utf-8')
            if path.startswith('/api/resource'):
                # DOJ JMD state legal resource map, read from the saved captures; links are references, not saved content.
                if path=='/api/resources/states':return self.send_body(200,doj_resources.states())
                if path=='/api/resources/sections':return self.send_body(200,doj_resources.sections(p.get('state') or None))
                if path=='/api/resources/circuits':return self.send_body(200,doj_resources.circuits())
                if path=='/api/resources/edges':return self.send_body(200,doj_resources.edges(p))
                if path=='/api/resources':return self.send_body(200,doj_resources.state_resources(p.get('state',''),p.get('section') or None,p))
                if path=='/api/resource':
                    item=doj_resources.resource(p.get('id',''))
                    return self.send_body(200,item) if item else self.send_body(404,{'error':'Resource not found'})
            if path.startswith('/api/agency/') or path.startswith('/agency-files/'):
                # Agency safety/enforcement records as published (openFDA, FDA.gov exports, local CPSC file); never legal conclusions.
                if path=='/api/agency/status':return self.send_body(200,agency_safety.status())
                if path=='/api/agency/datasets':return self.send_body(200,{'items':agency_safety.datasets(),'status':agency_safety.status()})
                if path=='/api/agency/search':
                    allowed={'dataset','q','firm','classification','status','product_code','date_type','dfrom','dto'}
                    args={k:v for k,v in p.items() if k in allowed and v}
                    return self.send_body(200,agency_safety.search(page=p.get('page',1),limit=p.get('limit',25),**args))
                if path=='/api/agency/record':
                    item=agency_safety.record(p.get('dataset',''),p.get('id',''))
                    return self.send_body(200,item) if item else self.send_body(404,{'error':'Agency record not found'})
                if path=='/api/agency/firm':return self.send_body(200,agency_safety.firm(p.get('name',''),page=p.get('page',1),limit=p.get('limit',25)))
                if path=='/api/agency/cfr':return self.send_body(200,agency_safety.for_cfr(p.get('citation',''),page=p.get('page',1),limit=p.get('limit',100)))
                if path.startswith('/agency-files/'):
                    asset=agency_safety.original(path[len('/agency-files/'):])
                    return self.send_body(200,asset[0],asset[1],attachment=asset[2],sandbox=True) if asset else self.send_body(404,{'error':'Agency file not found'})
            if path.startswith('/api/coverage/'):
                # Jurisdiction coverage, official-tier labels and search-derived topic candidates; never a legal survey.
                if path=='/api/coverage/matrix':return self.send_body(200,display_prose(jurisdiction_coverage.matrix()))
                if path=='/api/coverage/state':
                    item=jurisdiction_coverage.state_detail(p.get('state',''))
                    return self.send_body(200,item) if item else self.send_body(404,{'error':'Jurisdiction not found'})
                if path=='/api/coverage/topics':return self.send_body(200,jurisdiction_coverage.topics(state=p.get('state') or None,topic=p.get('topic') or None,page=p.get('page',1),limit=p.get('limit',25)))
                if path=='/api/coverage/venues':return self.send_body(200,jurisdiction_coverage.venues())
                if path=='/api/coverage/labels':return self.send_body(200,jurisdiction_coverage.labels(state=p.get('state') or None,law_body_class=p.get('law_body_class') or None,rule_set=p.get('rule_set') or None,confidence=p.get('confidence') or None,page=p.get('page',1),limit=p.get('limit',25)))
            if path.startswith('/api/regulation'):
                # Federal regulation slice: official GPO text, publisher text, eCFR history and Federal Register documents kept as separate dated facts.
                if path=='/api/regulations/info':return self.send_body(200,display_prose(federal_regulations.info()))
                if path=='/api/regulations/titles':return self.send_body(200,federal_regulations.titles())
                if path=='/api/regulations/parts':return self.send_body(200,federal_regulations.parts(p.get('title',''),include_all=p.get('include_all')=='1',page=p.get('page',1),limit=p.get('limit',500)))
                if path=='/api/regulations/sections':return self.send_body(200,federal_regulations.sections(p.get('part',''),as_of=p.get('as_of') or None,title=p.get('title') or None,page=p.get('page',1),limit=p.get('limit',500)))
                if path=='/api/regulations/agencies':return self.send_body(200,federal_regulations.agencies(slice_only=p.get('slice_only','1')=='1',q=p.get('q') or None))
                if path=='/api/regulations/search':
                    allowed={'q','title','part','agency','record_type','date_type','dfrom','dto'}
                    args={k:v for k,v in p.items() if k in allowed and v}
                    return self.send_body(200,federal_regulations.search(page=p.get('page',1),limit=p.get('limit',25),**args))
                if path=='/api/regulations/document':
                    item=federal_regulations.fr_document(p.get('id',''))
                    return self.send_body(200,item) if item else self.send_body(404,{'error':'Federal Register document not found'})
                if path=='/api/regulation':
                    item=federal_regulations.section(p.get('citation',''))
                    if item and item.get('title') and item.get('part'):
                        try:
                            import importlib
                            item['fr_history']=importlib.import_module('federal_register_history').for_cfr(str(item['title']),str(item['part']))
                        except Exception:item['fr_history']=None
                    return self.send_body(200,item) if item else self.send_body(404,{'error':'Regulation section not found'})
            if path.startswith('/api/mdl') or path.startswith('/mdl-files/'):
                # JPML multidistrict litigation registry: publisher-reported counts as of the printed report date.
                if path=='/api/mdls':return self.send_body(200,mdl_registry.listing(p))
                if path=='/api/mdls/summary':return self.send_body(200,mdl_registry.summary())
                if path=='/api/mdls/for-judge':return self.send_body(200,mdl_registry.for_judge(p.get('entity_id','')))
                if path=='/api/mdls/for-person':return self.send_body(200,mdl_registry.for_person(p.get('cl_person_id','')))
                if path=='/api/mdl':
                    item=mdl_registry.detail(p.get('number',''))
                    if item:
                        try:
                            number=int(str(item.get('mdl_number') or p.get('number','')).strip())
                            item['state_proceedings']=judge_layer('state_proceedings','for_mdl',number)
                            for key,module in (('appearances','mdl_appearances'),('docket_documents','mdl_docket_documents'),('docket_activity','mdl_docket_activity'),('cases','mdl_case_inventory'),('counsel_directory','counsel_directory'),('verdict_reports','verdict_reports'),('expert_rulings','expert_rulings')):
                                item[key]=judge_layer(module,'for_mdl',number)
                                if isinstance(item[key],dict):item[key].setdefault('mdl_number',number)
                        except (TypeError,ValueError):pass
                    return self.send_body(200,item) if item else self.send_body(404,{'error':'MDL not found'})
                if path.startswith('/mdl-files/'):
                    asset=mdl_registry.original(path[len('/mdl-files/'):])
                    return self.send_body(200,asset[0],asset[1],attachment=asset[2],sandbox=True) if asset else self.send_body(404,{'error':'MDL document not found'})
            if path.startswith('/api/area/') or path.startswith('/supplement-files/'):
                # Generic listing/detail contract shared by the newer data layers; a missing or failing adapter answers "not available".
                parts=path.split('/')
                name=parts[3] if path.startswith('/api/area/') and len(parts)>3 else (parts[2] if len(parts)>2 else '')
                adapter=generic_adapter(name)
                if adapter is None:return self.send_body(404,{'error':'Unknown research area'})
                if adapter is False:return self.send_body(200,{'available':False,'reason':'This data layer is not published yet.','total':0,'results':[],'filters':[],'columns':[]})
                if path.startswith('/supplement-files/'):
                    asset=adapter.original('/'.join(parts[3:])) if hasattr(adapter,'original') and len(parts)>3 else None
                    return self.send_body(200,asset[0],asset[1],attachment=asset[2],sandbox=True) if asset else self.send_body(404,{'error':'File not found'})
                if len(parts)>4 and parts[4]=='item':
                    item=adapter.detail(p.get('id',''))
                    return self.send_body(200,enrich_generic_item(name,p.get('id',''),item)) if item else self.send_body(404,{'error':'Record not found'})
                return self.send_body(200,adapter.listing(p))
            if path=='/api/court-resolve':
                # Which courts a caption string can mean (courts-db matcher); absent layer -> available:false.
                return self.send_body(200,judge_layer('court_reference','resolve',p.get('q','')) or {'available':False,'results':[]})
            if path=='/api/citations/record':
                # Saved documents that cite one provision of the saved law text.
                return self.send_body(200,judge_layer('citation_index','for_record',p.get('id','')) or {'available':False,'total':0,'results':[]})
            if path.startswith('/api/law-outline'):
                # Table of contents for the saved law collection; an absent or closed outline answers available:false.
                try:
                    import importlib
                    outline=importlib.import_module('law_outline')
                    if path=='/api/law-outline/children':result=outline.children(p.get('state',''),p.get('kind',''),p.get('parent','0'))
                    elif path=='/api/law-outline/provisions':result=outline.provisions(p.get('node',''),p.get('offset','0'),p.get('limit','100'))
                    elif path=='/api/law-outline/context':result=outline.context(p.get('id',''))
                    else:result=outline.collections(p.get('state',''))
                except Exception:result={'available':False,'reason':'Outline temporarily unavailable'}
                return self.send_body(200,result)
            if path.startswith('/api/agency-hub'):
                # Agency-centric join over the regulation, Federal Register, safety-data, document and address layers.
                try:
                    import importlib
                    hub=importlib.import_module('agency_hub')
                    result=hub.detail(p.get('key','')) if path=='/api/agency-hub/item' else hub.listing()
                except Exception:result=None
                return self.send_body(200,result) if result else self.send_body(404,{'error':'Agency overview not available'})
            if path.startswith('/api/county-filing'):
                # Official rules, forms, e-filing and fee sources by county; an absent or failing layer answers available:false.
                if path=='/api/county-filing/state':result=judge_layer('county_filing','for_state',p.get('state',''))
                elif path=='/api/county-filing/state-counties':result=judge_layer('county_filing','counties_for_state',p.get('state',''))
                elif path=='/api/county-filing/coverage':
                    try:
                        import importlib
                        result=importlib.import_module('county_filing').coverage()
                    except Exception:result=None
                else:result=judge_layer('county_filing','for_county',p.get('fips',''))
                return self.send_body(200,result or {'available':False})
            if path=='/api/blocks':
                # Optional related-material blocks for a state or court page; each layer is lazy and failure-tolerant.
                if p.get('state'):return self.send_body(200,{'state_proceedings':judge_layer('state_proceedings','for_state',p.get('state')),'court_documents':judge_layer('court_documents','for_state',p.get('state')),'saved_pages':judge_layer('saved_pages','for_state',p.get('state')),'limitation_periods':judge_layer('limitation_periods','for_state',p.get('state'))})
                if p.get('court'):return self.send_body(200,{'court_documents':judge_layer('court_documents','for_court',p.get('court')),'urls':judge_layer('url_directory','for_court',p.get('court'))})
                return self.send_body(200,{})
            if path=='/api/urls/block':
                # Compact URL-directory counts for a state, federal publisher or court page; absent layer -> null block.
                block=None
                for key,function in (('state','for_state'),('agency','for_agency'),('court','for_court')):
                    if p.get(key):block=judge_layer('url_directory',function,p.get(key));break
                return self.send_body(200,{'block':block})
            if path=='/api/supplements':
                # Readiness of every published supplement from its validation envelope; never a content claim.
                if p.get('name'):
                    item=supplements.item(p.get('name'))
                    return self.send_body(200,display_prose(item)) if item else self.send_body(404,{'error':'Supplement not found'})
                return self.send_body(200,supplement_status())
            if path=='/api/summary':
                with ro(DB) as c:s=json.loads(c.execute("SELECT payload FROM settings WHERE key='summary'").fetchone()[0])
                s=enriched_summary(s);s.pop('datasets',None);return self.send_body(200,s)
            if path=='/api/documents':return self.send_body(200,query_documents(p))
            if path=='/api/explore':return self.send_body(200,explore.summary(p))
            if path=='/api/sources':
                saved=source_captures.load()
                archive=source_archive_links.load()
                listing=source_directory.listing(p,saved_urls=source_captures.source_urls(saved),archive_counts=source_archive_links.link_counts(archive))
                listing['summary']['api_context']=source_api_context.summary()
                listing['summary']['archive_links']=source_archive_links.summary(archive)
                return self.send_body(200,listing)
            if path=='/api/source':
                item=source_directory.detail(p.get('id',''))
                if not item:return self.send_body(404,{'error':'Source reference not found'})
                item['captures']=source_captures.attachments(item['url'])
                item['has_saved_content']=bool(item['captures'])
                # Existing saved records are attached by exact URL only and re-checked against the live directory.
                item['archive_records']=source_archive_links.records_for(item['url'])
                item['has_archive_records']=bool(item['archive_records'])
                item['archive_record_count']=len(item['archive_records'])
                item['api_reference']=source_api_context.detail(item['source_record']['id'],item['url'])
                item['doj_listings']=doj_resources.source_listings(item['id'])
                return self.send_body(200,item)
            if path=='/api/county-registry':return self.send_body(200,county_registry.county(p.get('geoid','')))
            if path=='/api/county-litigation':return self.send_body(200,county_litigation.query(p))
            if path=='/api/county-litigation-record':
                item=county_litigation.detail(p.get('id',''))
                return self.send_body(200,item) if item else self.send_body(404,{'error':'County resource not found or not published'})
            if path=='/api/county-litigation-asset':
                asset=county_litigation.asset(p.get('id',''),p.get('kind','original'))
                return self.send_body(200,asset[0],asset[1],attachment=asset[2] if p.get('kind','original')=='original' else None,sandbox=True) if asset else self.send_body(404,{'error':'Verified county artifact not found'})
            if path=='/api/judges':
                listing=judges.listing(p)
                for row in listing.get('items') or []:
                    if not row.get('photo_url'):row.update({k:v for k,v in (judge_layer('judge_portraits','for_judge',row.get('entity_id') or row.get('id')) or {}).items()})
                return self.send_body(200,listing)
            if path=='/api/people':
                listing=people.listing(p)
                for row in listing.get('items') or []:row.update(judge_layer('judge_portraits','for_person',row.get('id')) or {})
                return self.send_body(200,listing)
            if path=='/api/person':
                person=people.profile(p.get('id',''))
                if person:person.update(judge_layer('judge_portraits','for_person',p.get('id','')) or {})
                return self.send_body(200,person) if person else self.send_body(404,{'error':'Biographical record not found'})
            if path=='/api/collections':return self.send_body(200,{'items':local_library.collections()})
            if path=='/api/collection':
                collection=local_library.collection(p.get('id',''),p)
                return self.send_body(200,collection) if collection else self.send_body(404,{'error':'Collection not found'})
            if path.startswith('/source-assets/'):
                token=path[len('/source-assets/'):]
                # Retained archive captures and pilot captures are both served only as hash-verified bytes.
                asset=source_archive_links.asset(token) if token.startswith('archive:') else source_captures.asset(token)
                return self.send_body(200,asset[0],asset[1],attachment=asset[2],sandbox=True) if asset else self.send_body(404,{'error':'Source asset not found'})
            if path.startswith('/library-assets/'):
                asset=local_library.asset(path[len('/library-assets/'):])
                if not asset:return self.send_body(404,{'error':'Library item not found'})
                file,mime,attachment=asset
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(file.stat().st_size))
                self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
                self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
                if attachment:self.send_header('Content-Disposition','attachment; filename="'+file.name.replace('"','_')+'"')
                self.end_headers()
                if self.command=='HEAD':return
                with file.open('rb') as source:shutil.copyfileobj(source,self.wfile,1024*1024)
                return
            if path=='/api/judge':
                profile=judges.profile(p.get('id',''))
                if profile:
                    # MDL assignments as printed in the JPML report, joined by exact name and court; never a current-assignment claim.
                    try:profile['mdls']=mdl_registry.for_judge(p.get('id',''))
                    except Exception:profile['mdls']={'available':False,'total':0,'results':[]}
                    profile['structured']=judge_overlay(profile.get('entity_id') or p.get('id',''))
                    profile['evidence']=judge_layer('judge_evidence','evidence_for',profile.get('entity_id') or p.get('id',''))
                    profile['disclosures']=judge_layer('judge_disclosures','for_judge',profile.get('entity_id') or p.get('id',''))
                    if not profile.get('photo_url'):profile.update(judge_layer('judge_portraits','for_judge',profile.get('entity_id') or p.get('id','')) or {})
                return self.send_body(200,profile) if profile else self.send_body(404,{'error':'Judge profile not found'})
            if path.startswith('/judge-images/'):
                asset=judges.image_file(path[len('/judge-images/'):])
                return self.send_body(200,asset[0].read_bytes(),asset[1]) if asset else self.send_body(404,{'error':'Image not found'})
            if path in {'/api/record','/api/text'}:
                item=record_detail(p.get('id',''),full=path=='/api/text')
                if item is None:return self.send_body(404,{'error':'Record not found'})
                return self.send_body(200,item['text'].encode('utf-8'),'text/plain; charset=utf-8') if path=='/api/text' else self.send_body(200,item)
            if path=='/api/counties':
                return self.send_body(200,query_counties(p))
            if path.startswith(('/files/','/bulk-files/','/bulk-metadata/','/recovery-metadata/')):
                if path.startswith('/bulk-files/'):file=bulk_laws.artifact(path[len('/bulk-files/'):])
                elif path.startswith('/recovery-metadata/'):
                    name=path[len('/recovery-metadata/'):]
                    file=recovery.FOLDER/name if name in recovery.METADATA_FILES else None
                    if file and not file.is_file():file=None
                elif path.startswith('/bulk-metadata/'):
                    name=path[len('/bulk-metadata/'):]
                    file=bulk_laws.FOLDER/name if name in bulk_laws.METADATA_FILES else None
                    if file and not file.is_file():file=None
                else:
                    with ro(DB) as c:row=c.execute('SELECT path FROM files WHERE id=?',(path[7:],)).fetchone()
                    file=safe_path(row[0]) if row else None
                if not file:return self.send_body(404,{'error':'Artifact not found'})
                # Raw web captures can never execute in the application's origin.
                ext=file.suffix.lower();mime='application/pdf' if ext=='.pdf' else ('text/plain; charset=utf-8' if ext in {'.txt','.md','.json','.jsonl','.csv','.xml'} else 'application/octet-stream')
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(file.stat().st_size));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
                if mime=='application/octet-stream':self.send_header('Content-Disposition','attachment; filename="'+file.name.replace('"','_')+'"')
                self.end_headers()
                with file.open('rb') as source:shutil.copyfileobj(source,self.wfile,1024*1024)
                return
            return self.send_body(404,{'error':'Not found'})
        except (ValueError,sqlite3.Error):return self.send_body(400,{'error':'Invalid query or unavailable index; refresh the directory after a publication finishes.'})
        except (OSError,TypeError) as error:
            print(dumps({'time':now(),'event':'request_error','path':path,'error_type':type(error).__name__,'response_started':self.response_started}),file=sys.stderr,flush=True)
            self.close_connection=True
            if not self.response_started:return self.send_body(503,{'error':'Artifact temporarily unavailable'})
    def do_POST(self):self.send_body(405,{'error':'Read-only directory'})

def main():
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group();mode.add_argument('--build',action='store_true');mode.add_argument('--refresh-pending-titles',action='store_true');parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=PORT);parser.add_argument('--host',default='127.0.0.1');args=parser.parse_args()
    try:host_config=hosting.configuration(args.host)
    except ValueError as exc:parser.error(str(exc))
    if args.serve and not DB.exists() and not args.build:
        parser.error('Archive data is not installed. Restore it with bootstrap.py pull; serving never starts an implicit rebuild.')
    if args.refresh_pending_titles:
        import pending_titles
        print(dumps(pending_titles.refresh()),flush=True)
    elif args.build or not DB.exists():
        summary=build();print(dumps({'built':True,'published':summary['published'],'datasets':[{k:d[k] for k in ('id','count')} for d in summary['datasets']]}),flush=True)
    if args.serve:
        threading.Thread(target=supplement_status,daemon=True).start()  # warm the readiness scan before the first page asks
        def warm_pages():
            # First use of these layers costs several seconds (large files); pay it at start-up, not on the first page view.
            for module,function,args in (('jurisdiction_coverage','matrix',()),('county_filing','coverage',()),('url_directory','summary',()),('federal_register_history','listing',({'limit':1},)),('agency_hub','listing',()),('law_outline','collections',('CA',))):
                try:
                    import importlib
                    getattr(importlib.import_module(module),function)(*args)
                except Exception:pass
        threading.Thread(target=warm_pages,daemon=True).start()
        server=ThreadingHTTPServer((args.host,args.port),Handler)
        server.hosting_config=host_config
        try:(HERE/'server.json').write_text(json.dumps({'pid':os.getpid(),'port':args.port,'url':f'http://{args.host}:{args.port}','started_at':now()},indent=2)+'\n')
        except OSError:pass  # a read-only deployment does not need the desktop process receipt
        print(f'Listening at http://{args.host}:{args.port}',flush=True);server.serve_forever()

if __name__=='__main__':main()
