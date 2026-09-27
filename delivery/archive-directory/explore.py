"""Small cached navigation facets over existing read-only legal catalogs.

Local counts are distinct display groups, with availability measured on their
displayed preferred record. Bulk counts are publisher records, kept separate.
"""
from contextlib import contextmanager
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
import bulk_laws
import categories
import record_facets

ROOT=Path(__file__).resolve().parents[2]
DB=ROOT/'delivery/archive-directory/directory.sqlite3'
CATALOG=ROOT/'catalog/documents.sqlite3'
AVAILABILITY={'text':'Saved text','original':'Saved source file','link_only':'Link only'}
GROUPS={'all','laws','counties','federal','judges','reference'}


def _prefix(alias):
    if alias and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',alias):raise ValueError('Invalid SQL alias')
    return alias+'.' if alias else ''


def local_text_sql(alias=''):
    p=_prefix(alias)
    # content_id takes precedence in the actual reader. A file-reference ID alone
    # does not prove nonempty text; the compact inline flag does.
    # The catalog importer sets searchable only for content.strip() and retains
    # an indexed versions(content_id) bridge. Reading that compact provenance
    # avoids fetching character metadata after large text overflow pages.
    return (f"(CASE WHEN {p}content_id IS NOT NULL THEN EXISTS(SELECT 1 FROM archive.versions availability_content "
            f"WHERE availability_content.content_id={p}content_id AND availability_content.index_text_status='searchable') "
            f"ELSE coalesce({p}inline_text,0)!=0 END)")


def availability_condition(value,alias=''):
    p=_prefix(alias)
    if not value:return ''
    if value=='text':return local_text_sql(alias)
    if value=='original':return f"coalesce({p}original_id,'')!=''"
    if value=='link_only':return f"(NOT {local_text_sql(alias)} AND coalesce({p}original_id,'')='' AND coalesce({p}source_url,'')!='')"
    return '0'


def _signature(path):
    stat=Path(path).stat()
    return str(Path(path).resolve()),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns


@contextmanager
def connect():
    c=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('ATTACH DATABASE ? AS archive',(CATALOG.resolve().as_uri()+'?mode=ro',))
    try:yield c
    finally:c.close()


@lru_cache(maxsize=8)
def _local_rows(group,signature):
    if group not in GROUPS:return (),{}
    where='b.eligible!=0'
    args=[]
    if group!='all':
        where+=' AND b.id IN (SELECT record_id FROM record_groups WHERE group_name=?)';args.append(group)
    with connect() as c:
        # The derived-facet sidecar (when published) supplies the category and hides shell/parked/empty captures.
        facets_path=record_facets.db_path()
        if facets_path:c.execute('ATTACH DATABASE ? AS facets',(Path(facets_path).resolve().as_uri()+'?mode=ro',))
        sql=('SELECT b.id,b.dataset,b.state,b.kind,m.display_id,'+local_text_sql('p')+' AS saved_text,'
             "coalesce(p.original_id,'')!='' AS saved_file,coalesce(p.source_url,'')!='' AS has_link"
             +(",f.derived_category AS derived_category,(SELECT group_concat(fc.category,';') FROM facets.facet_categories fc WHERE fc.record_id=b.id) AS derived_categories" if facets_path else '')+
             ' FROM browse b JOIN display_members m ON m.record_id=b.id JOIN display_groups g ON g.id=m.display_id '
             'JOIN browse p ON p.id=g.preferred_id'+(' LEFT JOIN facets.facets f ON f.record_id=b.id' if facets_path else '')+' WHERE '+where
             +(" AND coalesce(f.validity,'ok') IN ('ok','no_capture')" if facets_path else ''))
        rows=[]
        for r in c.execute(sql,args):
            d=dict(r);d['states']=tuple(s for s in (d['state'] or '').split('; ') if s)
            d['category']=d.get('derived_category') or categories.classify(d['kind'])
            # A record can belong to more than one derived category (facet_categories); match on every membership.
            d['categories']=tuple(x for x in (d.get('derived_categories') or '').split(';') if x) or (d['category'],);rows.append(d)
        found=c.execute("SELECT payload FROM settings WHERE key='summary'").fetchone()
        meta=json.loads(found[0]) if found else {}
    return tuple(rows),meta


@lru_cache(maxsize=4)
def _bulk_rows(signature):
    return tuple(bulk_laws.metadata_rows()),bulk_laws.info(),bulk_laws.all_text_nonempty()


def _availability(row,value):
    if not value:return True
    if value=='text':return bool(row['saved_text'])
    if value=='original':return bool(row['saved_file'])
    if value=='link_only':return not row['saved_text'] and not row['saved_file'] and bool(row['has_link'])
    return False


def _matches(row,params):
    return ((not params.get('state') or params['state'] in row['states'])
        and (not params.get('category') or params['category'] in row.get('categories',(row['category'],)))
        and (not params.get('dataset') or params['dataset']==row['dataset'])
        and _availability(row,params.get('availability')))


def _counts(local,bulk,params):
    selected=[r for r in local if _matches(r,params)]
    ids={r['display_id'] for r in selected}
    bulk_count=sum(r['records'] for r in bulk if _matches(r,params))
    return {'local_documents':len(ids),'local_source_observations':sum(r['dataset']!='judge_entities' for r in selected),
            'bulk_records':bulk_count,'total':len(ids)+bulk_count}


def summary(params):
    group=params.get('group') or 'laws'
    selected={k:str(params.get(k) or '')[:200] for k in ('state','category','dataset','availability')}
    facets_path=record_facets.db_path()
    local,meta=_local_rows(group,(_signature(DB),_signature(CATALOG),_signature(Path(facets_path)) if facets_path else None))
    bulk=[];bulk_meta={};bulk_text_known=True
    if group in {'all','laws'} and bulk_laws.info().get('ready'):
        paths=[bulk_laws.DB,bulk_laws.FOLDER/'ready.json',bulk_laws.FOLDER/'import_summary.json']
        files,bulk_meta,bulk_text_known=_bulk_rows(tuple(_signature(p) for p in paths))
        names=bulk_laws.state_names()
        bulk=[{'states':(names.get(r['state'],r['state']),),'category':categories.classify(r['kind']),'categories':(categories.classify(r['kind']),),
               'dataset':'open_us_law','records':r['records'],'saved_file':True,'saved_text':bulk_text_known,'has_link':False} for r in files]
    totals=_counts(local,bulk,selected)
    # One display group may retain evidence from multiple source collections,
    # categories or states. Facets are real drilldowns, not an additive partition.
    state_names=set(bulk_laws.state_names().values()) if group=='laws' else set()
    state_names.update(s for r in local for s in r['states'])
    state_names.update(s for r in bulk for s in r['states'] if s)
    jurisdiction_params=selected|{'state':''}
    jurisdictions=[{'state':state,'label':state,**_counts(local,bulk,jurisdiction_params|{'state':state})} for state in sorted(state_names)]
    labels=record_facets.LABELS['category'] if facets_path else categories.LABELS
    category_rows=[{'id':key,'label':label,**_counts(local,bulk,selected|{'category':key})} for key,label in labels.items()]
    availability=[{'id':key,'label':label,**_counts(local,bulk,selected|{'availability':key})} for key,label in AVAILABILITY.items()]
    dataset_meta={d['id']:d for d in meta.get('datasets',[])}
    datasets=[]
    for key in sorted({r['dataset'] for r in local}|{r['dataset'] for r in bulk}):
        counts=_counts(local,bulk,selected|{'dataset':key})
        if not counts['total']:continue
        is_bulk=key=='open_us_law'
        datasets.append({'id':key,'label':dataset_meta.get(key,{}).get('title', 'Open US Law bulk snapshot' if is_bulk else key.replace('_',' ').title()),
            **counts,'snapshot_label':bulk_meta.get('snapshot') if is_bulk else 'Saved source records; editions vary',
            'snapshot_date':bulk_meta.get('snapshot_date') if is_bulk else None,
            'source_as_of':None,'source_as_of_label':'Legal currency varies by record; no uniform as-of date is verified.',
            'published_at':bulk_meta.get('published_at') if is_bulk else meta.get('published',{}).get('completed_at') if key=='focused' else None,
            'indexed_at':bulk_meta.get('completed_at') if is_bulk else meta.get('directory_built_at')})
    return {'group':group,'state':selected['state'],'filters':selected,'totals':totals,
            'jurisdictions':jurisdictions,'categories':category_rows,'datasets':datasets,'availability':availability,
            'count_basis':'Distinct local display groups plus separate publisher records. State, category and source facets may overlap; do not add facet counts.',
            'text_availability_basis':'Nonempty indexed text or saved inline body for the displayed local record; verified publisher nonempty-text measurement for bulk records.',
            'bulk_text_counts_verified':bulk_text_known,'limitations':[
                'Saved inventory counts are not completion percentages or certification of current law.',
                'Saved source file includes preserved publisher Parquet files for bulk records, not an implied original official PDF.',
                'A zero category count means no matching records in this library, not that a jurisdiction has no such law.',
                'Snapshot and publication dates do not establish legal effective dates.',
                'This navigation summary does not apply free-text search; use the document results for search counts.']}
