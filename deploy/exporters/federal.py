"""Source-backed export of federal regulations and agency safety API contracts."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
import zlib
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'delivery/archive-directory'))
OUT=ROOT/'_transfer_scratch/supabase_export/federal'

def js(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'),default=str)
def rows(db,sql,args=()): return [dict(r) for r in db.execute(sql,args)]
def group(db,sql,key):
    result=defaultdict(list)
    for row in db.execute(sql): result[row[key]].append(dict(row))
    return result
def flat(value):
    if isinstance(value,dict): return ' '.join(flat(v) for v in value.values())
    if isinstance(value,list): return ' '.join(flat(v) for v in value)
    return str(value or '')
def writer(output,name): return (output/(name+'.jsonl.partial')).open('w',encoding='utf-8',newline='\n')
def write_record(handle,dataset,item,detail,text,filters,ordinal,artifacts=None):
    value={'id':str(item['id']),'dataset':dataset,'category':'regulations' if dataset.startswith('federal_') else 'agency_safety',
           'state':item.get('state') or '', 'county_geoids':[],'title':str(item.get('heading') or item.get('title') or item.get('citation') or ''),
           'source_url':item.get('ecfr_url') or item.get('html_url') or '', 'ordinal':ordinal,'item':item,'detail':detail,
           'text':text,'filters':filters,'artifacts':artifacts or []}
    handle.write(js(value)+'\n')
def finish(output,name,count,expected,summary=None,limit=None):
    (output/(name+'.jsonl.partial')).replace(output/(name+'.jsonl'))
    metadata={'id':name,'label':name.replace('_',' ').title(),'expected_records':expected,'exported_records':count,'ready':False,
              'export_complete':count==expected and not limit,'summary':summary or {}}
    with (output/(name+'.jsonl')).open('rb') as source:metadata['export_jsonl_sha256']=hashlib.file_digest(source,'sha256').hexdigest()
    (output/(name+'.dataset.json')).write_text(js(metadata),encoding='utf-8')
    print(js({'dataset':name,'records':count,'expected':expected,'complete':metadata['export_complete']}),flush=True)
    return metadata
def context(handle,key,data,source_sha256): handle.write(js({'key':key,'data':data,'source_sha256':source_sha256})+'\n')

def source_signature(folder):
    manifest=json.loads((folder/'validation.json').read_text(encoding='utf-8'))
    paths=[folder/'validation.json']+[folder/r['path'] for r in manifest.get('data_files',[])]
    return [(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in paths]

def regulation_detail(mod,raw):
    ident='cfr:%s:%s'%(raw['title'],raw['section'])
    detail=mod.section(ident)
    if detail is not None:return detail
    # The native parser rejects 27 publisher range identifiers (for example
    # 112.48-112.49). Resolve only the exact row already observed in section_index;
    # all text/hash/history handling still runs through the native renderer.
    if re.fullmatch(r'\d+\.\d+[a-z]?[-–]\d+\.\d+[a-z]?',raw['section']):
        original=mod._parse_ref
        mod._parse_ref=lambda value,title=None: {'title':raw['title'],'part':raw['part'],'section':raw['section']} if value==ident else original(value,title)
        try:return mod.section(ident)
        finally:mod._parse_ref=original
    return None


def export_regulations(output,context_file,limit=None):
    import federal_regulations as mod
    import federal_register_history as history
    info=mod.info()
    if not info.get('available'): raise ValueError('Federal regulation publication gate closed')
    signature=source_signature(mod.DATA)
    state,_=mod._state();db=mod._connect(state).__enter__();db.row_factory=sqlite3.Row
    source_hash=hashlib.sha256((mod.DATA/'validation.json').read_bytes()).hexdigest()
    context(context_file,'federal:info',info,source_hash)
    title_listing=mod.titles();context(context_file,'federal:titles',title_listing,source_hash)
    agencies=mod.agencies();context(context_file,'federal:agencies',agencies,source_hash)
    context(context_file,'federal:search-template',mod.search(limit=1)|{'results':[],'total':0,'pages':0},source_hash)
    title_map=mod._titles(db); part_map={(r['title'],r['part']):r for r in rows(db,'SELECT * FROM parts')}
    versions=defaultdict(list)
    for row in rows(db,"SELECT * FROM versions WHERE type='section' ORDER BY version_date,rowid"):
        versions[(row['title'],row['part'])].append(row)
    current=defaultdict(list)
    for row in rows(db,'SELECT * FROM section_index'): current[(row['title'],row['part'])].append(row)
    section_dates=defaultdict(lambda:defaultdict(set))
    for row in rows(db,"SELECT title,section,amendment_date,issue_date FROM versions WHERE type='section'"):
        for col,key in [('amendment_date','amendment_date'),('issue_date','ecfr_issue_date')]:
            if row[col]:section_dates[(row['title'],row['section'])][key].add(row[col])
    for row in rows(db,'SELECT title,section,amddate_iso FROM gpo_sections'):
        if row['amddate_iso']: section_dates[(row['title'],row['section'])]['gpo_amendment_marker'].add(row['amddate_iso'])
    for row in rows(db,'SELECT title,section,snapshot_date FROM oul_provisions'):
        if row['snapshot_date']:section_dates[(row['title'],row['section'])]['oul_snapshot_date'].add(row['snapshot_date'])
    agency_parts=defaultdict(list)
    for agency in agencies['agencies']:
        for ref in agency.get('slice_parts') or []:
            parsed=mod._parse_ref(ref)
            if parsed and parsed['title']:agency_parts[(parsed['title'],parsed['part'])].append(agency['slug'])
    part_dataset='federal_regulations_parts';part_count=0
    with writer(output,part_dataset) as handle:
        for key,raw in sorted(part_map.items(),key=lambda kv:(mod._num_key(kv[0][0]),mod._num_key(kv[0][1]))):
            title=title_map.get(key[0],{});item=mod._part_public(raw,title)
            write_record(handle,part_dataset,item,item,flat(item),{'title':[key[0]],'part':[key[1]],'in_slice':['1' if raw['in_slice'] else '0']},part_count)
            part_count+=1
            current_rows=sorted(current[key],key=lambda r:mod._num_key(r['section']))
            summaries={r['section']:mod._section_summary(r,title) for r in current_rows}
            history_rows=versions[key];history_templates={}
            for version in history_rows:
                ident=version['section']
                if ident not in summaries:
                    heading=re.sub(r'^§+\s*'+re.escape(ident)+r'\s*','',version.get('name') or '').strip() or None
                    history_templates[ident]=dict(mod._section_summary({'title':key[0],'part':key[1],'section':ident,'heading':heading,'subpart':version.get('subpart')},title),in_current_index=False,text_sources_available=[])
            stored={'part':item,'current':list(summaries.values()),'versions':history_rows,'historical_templates':history_templates,
                    'as_of_template':mod._as_of_listing('2026-09-27',raw,current_rows,history_rows,title)[1],
                    'fr_history':history.for_cfr(*key)}
            context(context_file,'federal:part:'+':'.join(key),stored,source_hash)
            if limit and part_count>=limit:break
    receipts=[finish(output,part_dataset,part_count,len(part_map),limit=limit)]
    name='federal_regulations_sections';section_count=0
    ordered=sorted((r for rr in current.values() for r in rr),key=lambda r:(mod._num_key(r['title']),mod._num_key(r['part']),mod._num_key(r['section'])))
    with writer(output,name) as handle:
        for raw in ordered:
            item=dict(mod._section_summary(raw,title_map.get(raw['title'],{})),matched_in=[])
            detail=regulation_detail(mod,raw)
            if detail is None:raise ValueError('Missing native section '+item['id'])
            dates={key:sorted(value) for key,value in section_dates[(raw['title'],raw['section'])].items()}
            filters={'title':[raw['title']],'part':[raw['part']],'part_id':[item['part_id']],'section':[raw['section']],
                     'record_type':['section'],'agency':agency_parts[(raw['title'],raw['part'])],'date_types':list(dates),**dates}
            text='\n\n'.join(t['text'] for t in detail.get('texts',[]) if t.get('text') is not None)
            write_record(handle,name,item,detail,text,filters,section_count);section_count+=1
            if section_count%1000==0:print(js({'dataset':name,'written':section_count}),flush=True)
            if limit and section_count>=limit:break
    receipts.append(finish(output,name,section_count,len(ordered),limit=limit))
    name='federal_regulations_documents';doc_count=0
    docs=rows(db,'SELECT * FROM fr_documents ORDER BY publication_date DESC,document_number')
    linked=group(db,'SELECT document_number,title,part FROM fr_doc_parts','document_number')
    with writer(output,name) as handle:
        for row in docs:
            item=dict(mod._doc_summary(row),matched_in=[]);detail=mod._doc_public(db,row)
            refs=linked[row['document_number']]
            filters={'record_type':['fr_document'],'title':sorted({r['title'] for r in refs}),
                     'part':sorted({r['part'] for r in refs}),'part_id':sorted({'cfr:%s:%s'%(r['title'],r['part']) for r in refs}),
                     'agency':[s.lower() for s in json.loads(row.get('agency_slugs') or '[]')]}
            for key,col in mod._FR_DATE_COLUMN.items():
                if row.get(col):filters[key]=row[col]
            filters['date_types']=[k for k in mod.FR_DATE_TYPES if filters.get(k)]
            write_record(handle,name,item,detail,flat(detail),filters,1_000_000+doc_count);doc_count+=1
            if limit and doc_count>=limit:break
    receipts.append(finish(output,name,doc_count,len(docs),limit=limit));db.close()
    if source_signature(mod.DATA)!=signature:raise ValueError('Regulation source changed during export')
    return receipts


def agency_original(mod,state,file_id):
    original=state['originals'].get(file_id)
    if not original:return None
    result=mod.original(file_id)
    if not result:return None
    blob,mime,filename=result
    path=(mod.DATA/original['path']).resolve()
    return {'url':'/agency-files/'+file_id,'local_path':str(path),'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob),'mime':mime,'filename':filename}


def export_agency(output,context_file,limit=None):
    import agency_safety as mod
    status=mod.status()
    if not status.get('available'):raise ValueError('Agency safety publication gate closed')
    signature=source_signature(mod.DATA)
    state,_=mod._state(None);db=mod._connect(state).__enter__();db.row_factory=sqlite3.Row;specs=mod._datasets_by_id(db)
    source_hash=hashlib.sha256((mod.DATA/'validation.json').read_bytes()).hexdigest()
    context(context_file,'agency:status',status,source_hash)
    context(context_file,'agency:datasets',{'items':mod.datasets(),'status':status},source_hash)
    context(context_file,'agency:specs',[{'dataset':k,'group':v['grp'],'id_prefix':v['id_prefix']} for k,v in specs.items()],source_hash)
    for row in db.execute('SELECT * FROM firm_names'):
        context(context_file,'agency:firm:'+row['firm_norm'],{'firm_id':row['firm_id'],'names':json.loads(row['names']),'datasets':json.loads(row['datasets'])},source_hash)
    code_counts={r[0]:r[1] for r in db.execute('SELECT product_code,count(*) FROM pma GROUP BY product_code')}
    cfr_groups=defaultdict(list)
    classification_rows=rows(db,'SELECT i.*,c.cfr_part,c.regulation_number,c.device_class,c.device_name,c.medical_specialty_description,c.cfr_id FROM device_classification c JOIN record_index i ON i.rid=c.rid ORDER BY c.regulation_number,c.product_code')
    classification_dates=mod._dates_for(db,[r['rid'] for r in classification_rows],specs,classification_rows)
    for row in classification_rows:
        item=mod._summary(row,classification_dates[row['rid']],specs)
        item.update({k:row[k] for k in ('regulation_number','device_class','device_name','medical_specialty_description','cfr_id')})
        for key in (row['cfr_part'],row['regulation_number']):
            if key:cfr_groups[key].append(item)
    for key,items in cfr_groups.items():
        codes=[r['product_code'] for r in items]
        valid=sorted({c for c in codes if c is not None})
        context(context_file,'agency:cfr:21:'+key,{'classifications':items,'product_codes':valid,'related_pma_rows':sum(code_counts.get(c,0) for c in valid)},source_hash)
    receipts=[];asset_cache={};ordinal=0
    for native,spec in specs.items():
        name='agency_safety_'+native;count=0;expected=spec['rows']
        if not re.fullmatch('[a-z_]{1,40}',spec['table_name']):raise ValueError('Unsafe source table')
        file_id=spec.get('file_id')
        if file_id and file_id not in asset_cache:asset_cache[file_id]=agency_original(mod,state,file_id)
        original=asset_cache.get(file_id)
        with writer(output,name) as handle:
            cursor=db.execute('SELECT * FROM record_index WHERE dataset=? ORDER BY sort_date IS NULL,sort_date DESC,rid',(native,))
            while True:
                batch=[dict(r) for r in cursor.fetchmany(500)]
                if not batch:break
                ids=[r['rid'] for r in batch];marks=','.join('?'*len(ids))
                fields={r['rid']:dict(r) for r in db.execute('SELECT * FROM '+spec['table_name']+' WHERE rid IN ('+marks+')',ids)}
                publishers={r[0]:r[1] for r in db.execute('SELECT rid,raw_z FROM raw_records WHERE rid IN ('+marks+')',ids)}
                dates=mod._dates_for(db,ids,specs,batch)
                products=defaultdict(list);submissions=defaultdict(list)
                if native=='openfda_drugsfda':
                    for r in rows(db,'SELECT * FROM drugsfda_products WHERE parent_rid IN ('+marks+') ORDER BY product_number',ids):products[r.pop('parent_rid')].append(r)
                    for r in rows(db,'SELECT * FROM drugsfda_submissions WHERE parent_rid IN ('+marks+') ORDER BY submission_status_date,submission_type,submission_number',ids):submissions[r.pop('parent_rid')].append(r)
                for row in batch:
                    rid=row['rid'];item=mod._summary(row,dates[rid],specs)
                    field={k:v for k,v in fields.get(rid,{}).items() if k not in ('rid','dataset')}
                    publisher=json.loads(zlib.decompress(publishers[rid]).decode('utf-8')) if rid in publishers else None
                    detail=dict(item,fields=field,publisher_record=publisher,temporal=mod._temporal(row,spec),qualification=spec['qualification'],publisher=spec['publisher'],source_as_of=spec['source_as_of'])
                    if native=='openfda_drugsfda':detail.update(products=products[rid],submissions=submissions[rid])
                    links={}
                    if row['firm_norm']:links['firm_id']='firm:'+row['firm_norm'].lower().replace(' ','-')
                    if field.get('cfr_id'):links['cfr_id']=field['cfr_id']
                    if field.get('openfda_regulation_number') and re.fullmatch(r'\d{3}\.\d{1,4}[a-z]?',field['openfda_regulation_number']):links['cfr_id']='cfr:21:'+field['openfda_regulation_number']
                    detail['links']=links
                    registered=state['originals'].get(file_id or '')
                    detail['original_file']=mod._file_public(registered) if registered else None
                    filters={'dataset':[native,spec['grp']],'firm_norm':row['firm_norm'] or '',
                             'classification':[str(row['classification'] or '').lower()], 'status':[str(row['status'] or '').lower()],
                             'product_code':[row['product_code']] if row['product_code'] else [],'sort_date':row['sort_date'] or '','date':row['sort_date'] or '',
                             'date_types':[k for k,v in dates[rid].items() if v]}
                    filters.update({k:v for k,v in dates[rid].items() if v})
                    if native=='openfda_device_classification':
                        filters.update(cfr_part=[field.get('cfr_part')],cfr_section=[field.get('regulation_number')])
                    write_record(handle,name,item,detail,flat(publisher) or flat(field),filters,row['rid'],[original] if original and count==0 else [])
                    ordinal+=1;count+=1
                    if limit and count>=limit:break
                if limit and count>=limit:break
        receipts.append(finish(output,name,count,expected,summary={'native_dataset':native,'group':spec['grp'],'id_prefix':spec['id_prefix']},limit=limit))
    db.close()
    if source_signature(mod.DATA)!=signature:raise ValueError('Agency source changed during export')
    return receipts


def export_hubs(context_file):
    import agency_hub as mod
    source_hash=hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()
    # Reuse native exact-identity joins while limiting address counts to the
    # categorized source rows included in this deployment.
    original_gate=mod._gate
    class CategorizedURLs:
        def __init__(self,connection):self.connection=connection
        def execute(self,sql,args=()):
            return self.connection.execute(sql.replace('WHERE is_noise=0',"WHERE is_noise=0 AND content_type IS NOT NULL AND trim(content_type)<>''"),args)
        def close(self):self.connection.close()
    def selected_gate(name,path):
        connection,reason=original_gate(name,path)
        return (CategorizedURLs(connection) if connection is not None and name=='url_directory' else connection),reason
    mod._gate=selected_gate;mod._CACHE.clear()
    try:
        context(context_file,'agency:hub',mod.listing(),source_hash)
        for item in mod.AGENCIES:
            detail=mod.detail(item['key'])
            if detail is not None:context(context_file,'agency:hub:'+item['key'],detail,source_hash)
    finally:mod._gate=original_gate;mod._CACHE.clear()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT);parser.add_argument('--limit',type=int)
    parser.add_argument('--only',choices=['regulations','agency','hubs','all'],default='all')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True);receipts=[]
    context_path=args.output/('contexts.'+args.only+'.jsonl')
    with context_path.with_suffix('.partial').open('w',encoding='utf-8',newline='\n') as handle:
        if args.only in ('regulations','all'):receipts+=export_regulations(args.output,handle,args.limit)
        if args.only in ('agency','all'):receipts+=export_agency(args.output,handle,args.limit)
        if args.only in ('hubs','all') and not args.limit:export_hubs(handle)
    context_path.with_suffix('.partial').replace(context_path)
    (args.output/('receipt.'+args.only+'.json')).write_text(js(receipts),encoding='utf-8')


if __name__=='__main__':main()
