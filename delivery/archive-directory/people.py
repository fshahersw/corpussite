"""Read-only historical biographies from a separately verified local source layer.

Never joins these native person IDs to the main judge entity namespace. Photo flags
describe source availability; they are not local image artifacts.
"""
from contextlib import contextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path
import calendar, hashlib, json, re, sqlite3

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'sources/courtlistener_people_20260918'
DB = FOLDER / 'catalog.sqlite3'
QUALIFICATION = ('Historical source records, including aliases and nonjudicial roles. '
                 'Court affiliations and missing end dates do not establish current service. '
                 'These records are separate from the consolidated judge directory.')
ROLE_LABELS = {
    'jud':'Judge', 'jus':'Justice', 'prac':'Private practice', 'legis':'Legislative role',
    'ass-jud':'Associate judge', 'ass-jus':'Associate justice', 'prof':'Professor',
    'adj-prof':'Adjunct professor', 'c-jus':'Chief justice', 'pres':'President',
    'pres-jud':'Presiding judge', 'pres-jus':'Presiding justice',
    'act-jud':'Acting judge', 'act-jus':'Acting justice', 'ad-law-jud':'Administrative law judge',
    'trial-jud':'Trial judge', 'c-jud':'Chief judge', 'sup-jud':'Supervising judge',
    'ret-senior-jud':'Retired senior judge', 'ret-jus':'Retired justice',
    'mag':'Magistrate', 'c-mag':'Chief magistrate', 'mayor':'Mayor', 'gov':'Governor',
    'clerk':'Clerk', 'pub-def':'Public defender', 'staff-atty':'Staff attorney',
    'ada':'Assistant district attorney', 'da':'District attorney', 'pros':'Prosecutor',
    'att-gen-ass':'Assistant attorney general', 'sen':'Senator',
}
DEGREE_LABELS = {'ba':"Bachelor's degree",'ma':"Master's degree",'aa':'Associate degree',
                 'jd':'J.D.','llb':'LL.B.','llm':'LL.M.','mba':'M.B.A.','cert':'Certificate',
                 'phd':'Ph.D.','jsd':'J.S.D.','md':'M.D.'}


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def signature(path):
    s=Path(path).stat()
    return (str(Path(path).resolve()),s.st_size,s.st_mtime_ns,s.st_ctime_ns)


@lru_cache(maxsize=2)
def _verified(files):
    folder=Path(files[0][0]).parent
    ready=json.loads((folder/'ready.json').read_text(encoding='utf8'))
    if (not isinstance(ready,dict) or ready.get('ready') is not True or ready.get('status')!='verified'
        or ready.get('schema_version')!='courtlistener-people-catalog.v1'):
        raise ValueError('Biographical archive is not ready')
    database=folder/'catalog.sqlite3'
    if Path(ready.get('database','')).resolve()!=database.resolve():
        raise ValueError('Unexpected biographical database')
    if ready.get('database_bytes')!=database.stat().st_size or digest(database)!=ready.get('database_sha256'):
        raise ValueError('Biographical database differs from its receipt')
    for filename,field in [('source_manifest.json','source_manifest_sha256'),
                           ('summary.json','summary_sha256'),('validation.json','validation_sha256')]:
        if digest(folder/filename)!=ready.get(field):
            raise ValueError('Biographical source metadata differs from its receipt')
    validation=json.loads((folder/'validation.json').read_text(encoding='utf8'))
    summary=json.loads((folder/'summary.json').read_text(encoding='utf8'))
    manifest=json.loads((folder/'source_manifest.json').read_text(encoding='utf8'))
    if any(not isinstance(v,dict) for v in (validation,summary,manifest)):
        raise ValueError('Invalid biographical metadata container')
    if (manifest.get('schema_version')!='courtlistener-people-source-manifest.v1'
        or summary.get('schema_version')!='courtlistener-people-summary.v1'):
        raise ValueError('Unknown biographical source schema')
    counts=summary.get('counts')
    tables=manifest.get('tables')
    expected_tables={'people','positions','educations','schools','political_affiliations','courts'}
    if (not isinstance(counts,dict) or set(counts)!=expected_tables
        or any(type(v) is not int or v<0 for v in counts.values())
        or not isinstance(tables,dict) or set(tables)!=expected_tables
        or any(not isinstance(v,dict) or not isinstance(v.get('sha256'),str)
               or not re.fullmatch(r'[0-9a-f]{64}',v['sha256']) for v in tables.values())):
        raise ValueError('Invalid biographical table manifest')
    if any(counts[k]!=v.get('expected_rows') for k,v in tables.items()):
        raise ValueError('Biographical source counts differ from pinned manifest')
    required=['input_hashes_match_before_and_after_import','csv_headers_and_all_row_widths_match','native_primary_keys_unique']
    if (validation.get('passed') is not True or validation.get('foreign_key_errors')!=[]
        or validation.get('sqlite_integrity_check')!='ok'
        or validation.get('fts_external_content_integrity_check')!='passed'
        or any(validation.get(k) is not True for k in required)
        or validation.get('raw_table_counts')!=summary.get('counts')
        or validation.get('search_rows')!=summary.get('counts',{}).get('people')):
        raise ValueError('Biographical validation receipt is inconsistent')
    if tuple(signature(Path(item[0])) for item in files)!=files:
        raise ValueError('Biographical source changed during verification')
    return {'ready':ready,'summary':summary,'manifest':manifest}


def info():
    try:
        names=['ready.json','catalog.sqlite3','source_manifest.json','summary.json','validation.json']
        bundle=_verified(tuple(signature(FOLDER/name) for name in names))
        source=bundle['summary'];counts=source['counts']
        return {'ready':True,'summary':{
            'people':counts['people'],'positions':counts['positions'],'educations':counts['educations'],
            'schools':counts['schools'],'courts':counts['courts'],
            'aliases':source['people_with_alias_reference'],
            'photo_flags':source['people_with_photo_flags'],
            'snapshot_label':'June 30, 2026 (source file label)',
            'source_label':'CourtListener biographical archive',
            'qualification':QUALIFICATION},'bundle':bundle}
    except (OSError,ValueError,KeyError,TypeError):
        return {'ready':False,'summary':{},'message':'Historical biographies are awaiting source verification.'}


@contextmanager
def connect():
    c=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    c.row_factory=sqlite3.Row
    try:yield c
    finally:c.close()


def format_date(value, granularity):
    value=str(value or '').strip()
    if not value:return None
    try:parsed=date.fromisoformat(value)
    except ValueError:return {'text':'Date not readable','precision':'unrecognized','recorded_value':value}
    if granularity=='%Y':text=str(parsed.year);precision='year'
    elif granularity=='%Y-%m':text=f'{calendar.month_name[parsed.month]} {parsed.year}';precision='month'
    elif granularity=='%Y-%m-%d':text=f'{calendar.month_name[parsed.month]} {parsed.day}, {parsed.year}';precision='day'
    else:text=f'{parsed.year} (precision not specified)';precision='unspecified'
    return {'text':text,'precision':precision,'recorded_value':value}


def role_label(position):
    return str(position.get('job_title') or '').strip() or ROLE_LABELS.get(position.get('position_type'),'Recorded position')


def int_param(value,default,maximum):
    try:return min(max(int(value),0),maximum)
    except (TypeError,ValueError):return default


def position_view(row):
    d=dict(row)
    start=format_date(d.get('date_start'),d.get('date_granularity_start'))
    end=format_date(d.get('date_termination'),d.get('date_granularity_termination'))
    # Event dates have no companion granularity in this export. Show year with
    # an explicit precision qualification rather than assuming an exact day.
    events=[]
    for field,label in [('date_nominated','Nomination'),('date_elected','Election'),
                        ('date_confirmation','Confirmation'),('date_retirement','Retirement')]:
        value=format_date(d.get(field),'')
        if value:events.append({'label':label,**value})
    return {'id':d['id'],'role':role_label(d),'source_role_code':d.get('position_type',''),
            'court':d.get('court_full_name') or '', 'court_id':d.get('court_id') or '',
            'organization':d.get('organization_name') or '',
            'location':', '.join(x for x in (d.get('location_city'),d.get('location_state')) if x),
            'start':start['text'] if start else '', 'end':end['text'] if end else '',
            'start_detail':start,'end_detail':end,'dates':events,
            'inferred':d.get('has_inferred_values')=='t',
            'inference_note':'Some values in this position were inferred by the source.' if d.get('has_inferred_values')=='t' else ''}


def education_view(row):
    d=dict(row)
    detail=d.get('degree_detail') or ''
    level=d.get('degree_level') or ''
    return {'id':d['id'],'school':d.get('school_name') or 'School not specified',
            'school_id':d.get('school_id') or '',
            'degree':detail or DEGREE_LABELS.get(level,'Degree recorded' if level else ''),
            'degree_detail':'','source_degree_level':level,'source_degree_detail':detail,
            'year':d.get('degree_year') or ''}


def positions_for(c,key):
    return [position_view(r) for r in c.execute('''SELECT p.*,c.full_name AS court_full_name
      FROM positions p LEFT JOIN courts c ON c.id=p.court_id WHERE p.person_id=?
      ORDER BY nullif(p.date_start,'') DESC,p.id''',(key,))]


def educations_for(c,key):
    return [education_view(r) for r in c.execute('''SELECT e.*,s.name AS school_name
      FROM educations e LEFT JOIN schools s ON s.id=e.school_id WHERE e.person_id=?
      ORDER BY e.degree_year,e.id''',(key,))]


def listing(params):
    meta=info()
    limit=max(1,int_param(params.get('limit'),30,60));offset=int_param(params.get('offset'),0,1000000)
    q=str(params.get('q') or '').strip()[:200]
    court=str(params.get('court') or '')[:100]
    include_aliases=str(params.get('include_aliases') or '')=='1'
    base={'ready':meta['ready'],'total':0,'offset':offset,'limit':limit,'items':[],
          'courts':[],'summary':meta['summary'],
          'filters':{'q':q,'court':court,'include_aliases':include_aliases}}
    if not meta['ready']:return base|{'message':meta['message']}
    conditions=[];args=[]
    if not include_aliases:conditions.append('s.is_alias=0')
    if court:
        conditions.append('EXISTS(SELECT 1 FROM positions p WHERE p.person_id=s.person_id AND p.court_id=?)');args.append(court)
    if q:
        tokens=re.findall(r'[^\W_]+',q,re.UNICODE)[:12]
        if tokens:
            conditions.append('s.rowid IN (SELECT rowid FROM people_fts WHERE people_fts MATCH ?)')
            args.append(' AND '.join('"'+t.replace('"','""')+'"*' for t in tokens))
        else:conditions.append('0')
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    with connect() as c:
        base['total']=c.execute('SELECT count(*) FROM people_search s'+where,args).fetchone()[0]
        base['courts']=[{'id':r['id'],'name':r['full_name']} for r in c.execute('''SELECT c.id,c.full_name FROM courts c
           WHERE EXISTS(SELECT 1 FROM positions p WHERE p.court_id=c.id) ORDER BY c.full_name COLLATE NOCASE,c.id''')]
        rows=c.execute('SELECT s.* FROM people_search s'+where+' ORDER BY s.display_name COLLATE NOCASE,s.person_id LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        for r in rows:
            positions=positions_for(c,r['person_id']);education=educations_for(c,r['person_id'])
            courts=list(dict.fromkeys(p['court'] for p in positions if p['court']))
            recent=positions[0] if positions else {}
            base['items'].append({'id':r['person_id'],'name':r['display_name'],'courts':courts,
                'career_summary':' · '.join(x for x in (recent.get('role'),recent.get('court') or recent.get('organization')) if x),
                'education_summary':'; '.join(' · '.join(x for x in (e['school'],e['degree_detail'] or e['degree'],e['year']) if x) for e in education),
                'has_photo_reference':bool(r['has_photo']),'is_alias':bool(r['is_alias'])})
    return base


def profile(key):
    if not isinstance(key,str) or not re.fullmatch(r'[1-9][0-9]{0,11}',key):return None
    meta=info()
    if not meta['ready']:return {'ready':False,'message':meta['message']}
    with connect() as c:
        row=c.execute('''SELECT p.*,s.display_name,s.is_alias FROM people p
          JOIN people_search s ON s.person_id=p.id WHERE p.id=?''',(key,)).fetchone()
        if not row:return None
        p=dict(row);alias=None
        if p['is_alias_of_id']:
            target=c.execute('SELECT person_id,display_name FROM people_search WHERE person_id=?',(p['is_alias_of_id'],)).fetchone()
            alias={'id':target['person_id'],'name':target['display_name']} if target else {'id':p['is_alias_of_id'],'name':'Unresolved source alias'}
        positions=positions_for(c,key)
        result={'ready':True,'id':key,'name':p['display_name'],'is_alias':bool(p['is_alias']),
            'alias_of':alias,'photo_reference':p['has_photo']=='t',
            'birth':format_date(p['date_dob'],p['date_granularity_dob']),
            'death':format_date(p['date_dod'],p['date_granularity_dod']),
            'positions':positions,'educations':educations_for(c,key),
            'source':{'label':'CourtListener local biographical snapshot','snapshot_label':meta['summary']['snapshot_label'],
                'person_id':key,'legacy_fjc_id':p['fjc_id'],'qualification':QUALIFICATION,
                'has_inferred_values':any(x['inferred'] for x in positions),
                'source_files':[{'table':r['table_name'],'sha256':r['sha256']} for r in c.execute('SELECT * FROM source_files ORDER BY table_name')]}}
        return result
