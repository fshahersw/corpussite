"""Read-only adapter for a verified open-us-law snapshot; no giant JSON export."""
import json,sqlite3,hashlib
from pathlib import Path
from urllib.parse import urlencode
from functools import lru_cache
from contextlib import contextmanager
import categories

ROOT=Path(__file__).resolve().parents[2]
FOLDER=ROOT/'sources/open_us_law_20260918'
DB=FOLDER/'catalog.sqlite3'
METADATA_FILES=('README.md','import_summary.json','snapshot_metadata_validation.json','source_manifest.json','manifest.json','resources.jsonl','ready.json','quality_summary.json','source_url_gap_validation.json','ny_validation.json','catalog_validation.json','state_binding_validation.json')

@lru_cache(maxsize=1)
def state_names():
    values={'FEDERAL':'Federal','PR':'Puerto Rico'}
    with (ROOT/'delivery/focused_legal_corpus/counties/counties.jsonl').open(encoding='utf-8-sig') as source:
        for line in source:
            row=json.loads(line)
            if row.get('usps'):values[row['usps']]=row['state']
    return values

def info():
    if not (FOLDER/'ready.json').exists() or not DB.exists():
        summary=json.loads((FOLDER/'import_summary.json').read_text(encoding='utf-8-sig')) if (FOLDER/'import_summary.json').exists() else {}
        progress=json.loads((FOLDER/'index_progress.json').read_text(encoding='utf-8-sig')) if (FOLDER/'index_progress.json').exists() else {}
        return summary|{'ready':False,'records':0,'indexed_rows':progress.get('indexed_rows',0)}
    value=json.loads((FOLDER/'ready.json').read_text(encoding='utf-8-sig'))
    if value.get('ready') is not True:return {'ready':False,'records':0}
    summary=json.loads((FOLDER/'import_summary.json').read_text(encoding='utf-8-sig')) if (FOLDER/'import_summary.json').exists() else {}
    return summary|value|{'ready':True}

@contextmanager
def connect():
    c=sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:yield c
    finally:c.close()

def filters():
    if not info()['ready']:return [],[]
    with connect() as c:
        return [state_names().get(r[0],r[0]) for r in c.execute('SELECT DISTINCT state FROM files WHERE state IS NOT NULL ORDER BY state')],[r[0] for r in c.execute('SELECT DISTINCT kind FROM files ORDER BY kind')]

def all_text_nonempty():
    meta=info()
    return bool(meta.get('indexed_rows')) and meta.get('body_measurements',{}).get('nonempty_text_rows')==meta.get('indexed_rows')


def availability_condition(value):
    if not value:return ''
    if value=='original':return '1'  # Preserved publisher Parquet file, not an invented official PDF.
    if value=='text':return '1' if all_text_nonempty() else 'length(trim(coalesce(text,\'\')))>0'
    return '0'  # Every indexed record retains its source file; none is link-only.


def clauses(params):
    where=[];args=[]
    for key in ('state','kind'):
        if params.get(key):
            value=params[key]
            if key=='state':value=next((k for k,v in state_names().items() if v==value),value)
            where.append(key+'=?');args.append(value)
    if params.get('category'):
        with connect() as c:kinds=[r[0] for r in c.execute('SELECT DISTINCT kind FROM files')]
        expression,values=categories.condition(params['category'],kinds)
        where.append(expression);args.extend(values)
    if params.get('q','').strip():
        where.append('rowid IN (SELECT rowid FROM records_fts WHERE records_fts MATCH ?)')
        args.append('"'+params['q'].strip()[:300].replace('"','""')+'"')
    if params.get('availability'):where.append(availability_condition(params['availability']))
    return (' WHERE '+' AND '.join(where) if where else ''),args

def eligible(params):
    return params.get('group','all') in {'all','laws'} and params.get('dataset','') in {'','open_us_law'} and not params.get('county') and info()['ready']

def count(params):
    if not eligible(params):return 0
    clause,args=clauses(params)
    with connect() as c:
        metadata_only=not params.get('q','').strip() and not (params.get('availability')=='text' and not all_text_nonempty())
        if metadata_only:return c.execute('SELECT coalesce(sum(rows),0) FROM files'+clause,args).fetchone()[0]
        return c.execute('SELECT count(*) FROM records'+clause,args).fetchone()[0]

def item(row):
    d=dict(row);key=d['id']
    quality='Publisher snapshot '+str(d['snapshot'])+'; verify edition and current legal status'
    if not d['source_url']:quality+='; original-source URL missing in publisher data. Verified publisher file remains available.'
    return {'id':key,'title':d['title'],'dataset':'open_us_law','group':'laws','state':state_names().get(d['state'],d['state']),'county':'','kind':d['kind'],'category':categories.classify(d['kind']),'category_label':categories.LABELS[categories.classify(d['kind'])],'source_url':d['source_url'],'quality':quality,'has_original':True,'has_text':bool(d.get('has_text',True)),'original_url':'/bulk-files/'+str(d['file_id']),'text_url':'/api/text?'+urlencode({'id':key}),'source_count':1,'group_basis':'Publisher record and snapshot; distinct source/version retained'}

def query(params,offset,limit):
    if not eligible(params) or limit<=0:return []
    clause,args=clauses(params)
    meta=info();all_nonempty=meta.get('body_measurements',{}).get('nonempty_text_rows')==meta.get('indexed_rows')
    body_flag='1' if all_nonempty and meta.get('indexed_rows') else '(length(text)>0)'
    with connect() as c:
        return [item(r) for r in c.execute('SELECT id,title,state,kind,source_url,snapshot,file_id,'+body_flag+' AS has_text FROM records'+clause+' ORDER BY rowid LIMIT ? OFFSET ?',args+[limit,offset])]

def detail(key,full=False):
    if not info()['ready']:return None
    with connect() as c:
        row=c.execute('SELECT * FROM records WHERE id=?',(key,)).fetchone()
        if row is None:return None
        d=dict(row);result=item(d);text=d.get('text') or '';payload=json.loads(d.get('payload') or '{}')
        rendered_newlines=d.get('state')=='NY' and d.get('kind')=='statutes' and '\\n' in text
        if rendered_newlines:text=text.replace('\\n','\n')
        f=c.execute('SELECT path,sha256,bytes FROM files WHERE id=?',(d['file_id'],)).fetchone()
        result.update({'metadata':{'citation':d.get('citation'),'status':d.get('status'),'snapshot':d.get('snapshot'),'source_id':d.get('source_id'),'row_index':d.get('row_index'),'file':dict(f) if f else None,'attribution':'open-us-law; CC BY 4.0 compilation; original official-source URLs retained','publisher_record':payload},'text':text if full else text[:60000],'text_characters':len(text),'text_truncated':not full and len(text)>60000,'reading_notes':{'method':'Publisher structured record text','original_preserved':True,'currency_verified':False,'publisher_newline_escapes_rendered':rendered_newlines,'source_text_sha256':d.get('content_hash'),'display_text_sha256':hashlib.sha256(text.encode()).hexdigest()},'source_records':[],'links':[]})
    return result


def metadata_rows():
    """Only the 229 file-level counts; never scan the three-million-row text table."""
    if not info()['ready']:return []
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT state,kind,sum(rows) AS records FROM files GROUP BY state,kind')]

def artifact(key):
    if not info()['ready']:return None
    with connect() as c:
        row=c.execute('SELECT path FROM files WHERE id=?',(key,)).fetchone()
        if not row:return None
        path=(ROOT/row[0]).resolve()
        try:path.relative_to(FOLDER)
        except ValueError:return None
        return path if path.is_file() and path.suffix=='.parquet' else None
