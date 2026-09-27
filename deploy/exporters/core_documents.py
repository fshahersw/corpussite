"""Stream categorized directory and Open US Law records to private migration JSONL.

Reads SQLite in mode=ro; no HTTP calls, source writes, cloud uploads or crawling.
Keep the output under ignored _transfer_scratch. Full readable text is stored once.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, hashlib, json, mimetypes, sqlite3, sys, time
from pathlib import Path
from urllib.parse import urlencode
try:
    import orjson
except ImportError:
    orjson=None

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'delivery/archive-directory'
OUTPUT = ROOT / '_transfer_scratch/supabase_export/core'
sys.path.insert(0, str(APP))
import categories, county_reader, readable, record_facets
from evidence_dates import document_dates

ALLOWED = {'statutes','rules','constitutions','regulations','forms','guidance','directories','judges'}
LABELS = {**categories.LABELS, 'judges':'Judge profiles & observations'}
BODY_KEYS = {'text','body','content','markdown','rawHtml','html','profile_text','inline_text','body_text','text_content','plain_text'}

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def dumps(v): return json.dumps(v, ensure_ascii=False, separators=(',', ':'))
def load_json(v):return orjson.loads(v) if orjson else json.loads(v)
def encode_line(v):return orjson.dumps(v,option=orjson.OPT_APPEND_NEWLINE) if orjson else (dumps(v)+'\n').encode('utf-8')
def write_json(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
def connect(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON'); return db
def stamp(p):
    s=p.stat();return [s.st_size,s.st_mtime_ns]
def digest(p):
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def strip_body(value, body, path='', removed=None):
    """Keep source claims and dates while avoiding a second whole document in JSON metadata."""
    removed=removed if removed is not None else []
    if isinstance(value,dict):
        result={}
        for k,v in value.items():
            current=path+'.'+k if path else k
            if isinstance(v,str) and len(v)>1000 and (k in BODY_KEYS or v==body):
                removed.append(current);continue
            result[k]=strip_body(v,body,current,removed)
        return result
    if isinstance(value,list):return [strip_body(v,body,path+'[]',removed) for v in value]
    return value

class Artifacts:
    def __init__(self):self.cache={};self.missing=[]
    def get(self, path, url, role, expected=None):
        if not path:return None
        p=Path(path);p=(p if p.is_absolute() else ROOT/p).resolve()
        # Core directory only serves explicitly registered files under these data roots.
        try:r=p.relative_to(ROOT)
        except ValueError:
            self.missing.append({'url':url,'reason':'Outside archive root'});return None
        if not r.parts or r.parts[0] not in {'sources','corpus','catalog','delivery','reports'} or not p.is_file():
            self.missing.append({'url':url,'reason':'Missing or outside registered data roots'});return None
        key=str(p)
        if key not in self.cache:
            self.cache[key]={'local_path':key,'sha256':digest(p),'bytes':p.stat().st_size,
                'mime':mimetypes.guess_type(p.name)[0] or 'application/octet-stream','file_stamp':stamp(p)}
        value=dict(self.cache[key]);value.pop('file_stamp',None)
        if expected and value['sha256']!=expected:raise ValueError('Artifact SHA mismatch for '+url)
        return dict(value,url=url,role=role)

class Writer:
    def __init__(self,folder):
        self.folder=folder;self.streams={};self.counts=collections.Counter();self.cats=collections.defaultdict(collections.Counter)
        self.states=collections.defaultdict(set);self.kinds=collections.defaultdict(set);self.bytes=collections.Counter();self.hashes={}
    def add(self,row):
        ds=row['dataset']
        if ds not in self.streams:
            self.streams[ds]=(self.folder/(ds+'.jsonl.tmp')).open('wb');self.hashes[ds]=hashlib.sha256()
        raw=encode_line(row);self.streams[ds].write(raw);self.hashes[ds].update(raw);self.bytes[ds]+=len(raw)
        self.counts[ds]+=1;self.cats[ds][row['category']]+=1
        if row.get('state'):self.states[ds].update(str(row['state']).split('; '))
        if row.get('item',{}).get('kind'):self.kinds[ds].add(row['item']['kind'])
    def finish(self):
        descriptors=[]
        for ds,f in self.streams.items():
            f.close();temp=self.folder/(ds+'.jsonl.tmp');temp.replace(self.folder/(ds+'.jsonl'))
            desc={'id':ds,'dataset':ds,'rows':self.counts[ds],'path':ds+'.jsonl','bytes':self.bytes[ds],
                'sha256':self.hashes[ds].hexdigest(),'categories':dict(self.cats[ds]),'states':sorted(self.states[ds]),'kinds':sorted(self.kinds[ds])}
            write_json(self.folder/(ds+'.dataset.json'),desc);descriptors.append(desc)
        return descriptors
    def close(self):
        for f in self.streams.values():f.close()

def public_item(row, facet):
    category=facet['derived_category'];rid=row['id'];has_text=bool(row['content_id'] or row['text_id'] or row['inline_text'])
    return {'id':rid,'title':row['title'],'dataset':row['dataset'],'state':row['state'],'county':row['county'],'kind':row['kind'],
        'source_url':row['source_url'],'quality':row['quality'],'group':row['group_name'],'category':category,'category_label':record_facets.LABELS['category'][category],
        'original_category':categories.classify(row['kind']),'has_original':bool(row['original_id']),'has_text':has_text,
        'original_url':'/files/'+row['original_id'] if row['original_id'] else '',
        'text_url':'/api/text?'+urlencode({'id':rid}) if has_text else '',
        'extracted_url':'/files/'+row['text_id'] if row['text_id'] else ''}

def export_main(writer, artifacts, limit=None):
    paths=[APP/'directory.sqlite3',ROOT/'catalog/documents.sqlite3',ROOT/'sources/record_facets_20260919/facets.sqlite3',ROOT/'sources/reading_views_20260918/reading.sqlite3']
    before={str(p):stamp(p) for p in paths}
    if record_facets.db_path() != str(paths[2]):raise ValueError('Derived-facet publication gate is closed')
    db=connect(paths[0]);db.execute('ATTACH DATABASE ? AS archive',(paths[1].resolve().as_uri()+'?mode=ro',))
    db.execute('ATTACH DATABASE ? AS facets',(paths[2].resolve().as_uri()+'?mode=ro',))
    db.execute('ATTACH DATABASE ? AS clean',(paths[3].resolve().as_uri()+'?mode=ro',))
    facets={r['record_id']:dict(r) for r in db.execute('SELECT * FROM facets.facets')}
    files=dict(db.execute('SELECT id,path FROM files'))
    county_map=collections.defaultdict(list);groups=collections.defaultdict(list)
    for rid,geoid in db.execute('SELECT record_id,geoid FROM record_counties EXCEPT SELECT record_id,geoid FROM facets.wrong_county_joins'):county_map[rid].append(geoid)
    for rid,g in db.execute('SELECT record_id,group_name FROM record_groups'):groups[rid].append(g)
    members=dict(db.execute('SELECT record_id,display_id FROM display_members'))
    dataset_ids=dict(db.execute('SELECT id,dataset FROM records'))
    kept=set();excluded=[];exported=0
    sql='''SELECT r.*,a.text AS canonical_text,a.text_sha256 AS canonical_text_sha,
           v.text AS clean_text,v.links AS clean_links,v.notes AS clean_notes,v.version AS clean_version
           FROM records r LEFT JOIN archive.contents a ON a.id=r.content_id
           LEFT JOIN clean.reading v ON v.record_id=r.id ORDER BY r.rowid'''
    for ordinal,row in enumerate(db.execute(sql),1):
        facet=facets.get(row['id']) or {}
        if facet.get('derived_category') not in ALLOWED:
            excluded.append({'id':row['id'],'dataset':row['dataset'],'reason':'Unmapped category or other','category':facet.get('derived_category')});continue
        if limit is not None and exported>=limit:break
        p=json.loads(row['payload']);raw_path=p.get('raw_path');raw_sha=p.get('raw_sha256') or p.get('sha256')
        text=row['canonical_text'] if row['content_id'] and row['canonical_text'] is not None else row['inline_text'] or ''
        if not row['content_id'] and row['text_id'] and files.get(row['text_id']):
            tp=ROOT/files[row['text_id']]
            if tp.is_file():text=tp.read_text('utf-8-sig',errors='replace')
        expected_text=row['canonical_text_sha'] or p.get('text_file_sha256') or p.get('text_sha256')
        view=county_reader.reading_view(row['source_url'],ROOT/raw_path if raw_path else None,raw_sha)
        if view is None and row['clean_text'] is not None and row['clean_version']==readable.VERSION:
            notes=json.loads(row['clean_notes'] or '{}')
            if (notes.get('parent_raw_sha256')==raw_sha and (not expected_text or notes.get('parent_text_sha256',notes.get('input_text_sha256'))==expected_text)
                and bool(notes.get('section_text_from_exact_derivative'))==str(p.get('kind','')).endswith('_provision')):
                view={'text':row['clean_text'],'links':json.loads(row['clean_links'] or '[]'),'notes':notes}
        if view is None:
            if row['dataset'] in {'judge_enrichment','judge_vendor','judge_entities'}:
                view={'text':p.get('profile_text') or readable.plain_profile(p),'links':[],'notes':{'method':'Source-bound judge profile','original_preserved':True}}
            else:view=readable.reading_view(text,title=row['title'],source_url=row['source_url'],raw_path=None if str(p.get('kind','')).endswith('_provision') else ROOT/raw_path if raw_path else None)
        text=view['text'];item=public_item(row,facet);item['has_text']=bool(text.strip())
        if not item['has_text']:item['text_url']=''
        public_facets=record_facets._public(facet)
        item['facets']=public_facets
        removed=[];metadata=strip_body(p,text,removed=removed)
        notes=dict(view['notes']);payload_metadata=p.get('metadata') if isinstance(p.get('metadata'),dict) else {}
        recovered=payload_metadata.get('text_recovery')
        if recovered:notes['recovery']={k:recovered.get(k) for k in ['method','quality_notes','text_sha256']}
        if p.get('capture_kind')=='ocr_derivative' or str(p.get('extraction_status','')).startswith('ocr_'):
            evidence=p.get('source_evidence_json') or {}
            if isinstance(evidence,str):
                try:evidence=json.loads(evidence)
                except ValueError:evidence={}
            if not isinstance(evidence,dict):evidence={}
            notes['ocr']={k:evidence.get(k) for k in ['ocr_status','ocr_scope','ocr_pages_completed','ocr_pages_required','pdf_pages','mean_page_confidence','pages_below_70_confidence','ocr_engine']}
            notes['ocr']['engine_confidence_is_not_verified_accuracy']=True
        if removed:notes['body_metadata_fields_omitted_for_export']=removed
        detail=dict(item,metadata=metadata,text_characters=len(text),text_truncated=False,reading_notes=notes,links=view['links'],source_records=[])
        if view.get('county_profile') is not None:detail['county_profile']=view['county_profile']
        detail.update(document_dates(p))
        row_artifacts=[]
        for field,role,url in [('original_id','original',item['original_url']),('text_id','extracted_text',item['extracted_url'])]:
            if row[field] and files.get(row[field]):
                aid=artifacts.get(files[row[field]],url,role)
                if aid:row_artifacts.append(aid)
        filters={k:facet.get(k) for k in ['derived_category','review_state','doc_subtype','record_type','file_type','jurisdiction_level','validity','saved_at','source_as_of','published_at','effective_from','saved_lo','saved_hi','source_lo','source_hi','published_lo','published_hi','effective_lo','effective_hi','representation']}
        filters.update(group=row['group_name'],groups=groups[row['id']],kind=row['kind'],state=row['state'],county=row['county'],county_geoids=county_map[row['id']],
            availability=['original' for _ in [1] if item['has_original']]+['text' for _ in [1] if item['has_text']],
            display_id=members.get(row['id'],row['id']),retrieval_eligible=payload_metadata.get('retrieval_eligible',True),categories=public_facets.get('categories',[]))
        writer.add({'id':row['id'],'dataset':row['dataset'],'category':facet['derived_category'],'state':row['state'],'county_geoids':county_map[row['id']],
            'title':row['title'],'source_url':row['source_url'],'item':item,'detail':detail,'text':text,'filters':filters,'artifacts':row_artifacts,'ordinal':ordinal})
        kept.add(row['id']);exported+=1
        if exported%2000==0:print(dumps({'stage':'main','rows':exported}),flush=True)
    group_rows=[]
    grouped=collections.defaultdict(list)
    for rid,gid in members.items():
        if rid in kept:grouped[gid].append(rid)
    for r in db.execute('SELECT * FROM display_groups'):
        if r['id'] not in grouped:continue
        g=dict(r);g['member_ids']=grouped[r['id']];g['original_preferred_id']=g['preferred_id']
        g['original_source_count']=g['source_count'];g['source_count']=sum(dataset_ids[rid]!='judge_entities' for rid in g['member_ids']) or 1
        if g['preferred_id'] not in kept:g['preferred_id']=sorted(g['member_ids'])[0];g['preferred_selection_note']='Original preferred source excluded by category; retained member selected.'
        g['retained_members']=len(g['member_ids']);group_rows.append(g)
    for name,rows in [('display_groups.jsonl',group_rows),('excluded_main.jsonl',excluded),('counties.jsonl',[{'id':r['geoid'],'state':r['state'],'title':r['name'],'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM counties')])]:
        (writer.folder/name).write_text(''.join(dumps(r)+'\n' for r in rows),encoding='utf-8')
    db.close()
    if any(stamp(Path(p))!=s for p,s in before.items()):raise ValueError('Source SQLite changed during export')
    return {'source_rows':len(dataset_ids),'exported_rows':exported,'excluded_rows_seen':len(excluded),'display_groups':len(group_rows),
        'artifact_files':len(artifacts.cache),'artifact_bytes':sum(v['bytes'] for v in artifacts.cache.values()),'snapshot_signatures':before,'limited':limit is not None}

def export_bulk(writer, artifacts, limit=None):
    path=ROOT/'sources/open_us_law_20260918/catalog.sqlite3';before=stamp(path);db=connect(path)
    state_names={'FEDERAL':'Federal','PR':'Puerto Rico'}
    with (ROOT/'delivery/focused_legal_corpus/counties/counties.jsonl').open(encoding='utf-8-sig') as stream:
        for line in stream:
            r=json.loads(line)
            if r.get('usps'):state_names[r['usps']]=r['state']
    files={r['id']:dict(r) for r in db.execute('SELECT * FROM files')};selected={key for key,f in files.items() if categories.classify(f['kind']) in ALLOWED}
    file_artifacts={key:artifacts.get(f['path'],'/bulk-files/'+key,'publisher_original',f['sha256']) for key,f in files.items() if key in selected}
    exported=0;excluded=0;excluded_files=collections.Counter()
    for row in db.execute('SELECT rowid AS source_rowid,* FROM records ORDER BY rowid'):
        if row['file_id'] not in selected:excluded+=1;excluded_files[row['file_id']]+=1;continue
        if limit is not None and exported>=limit:break
        cat=categories.classify(row['kind']);text=row['text'] or '';newline=row['state']=='NY' and row['kind']=='statutes' and '\\n' in text
        if newline:text=text.replace('\\n','\n')
        payload=load_json(row['payload'] or '{}');removed=[];payload=strip_body(payload,text,removed=removed)
        state=state_names.get(row['state'],row['state']);quality='Publisher snapshot '+str(row['snapshot'])+'; verify edition and current legal status'
        if not row['source_url']:quality+='; original-source URL missing in publisher data. Verified publisher file remains available.'
        item={'id':row['id'],'title':row['title'],'dataset':'open_us_law','group':'laws','state':state,'county':'','kind':row['kind'],'category':cat,
            'category_label':LABELS[cat],'source_url':row['source_url'],'quality':quality,'has_original':bool(file_artifacts[row['file_id']]),'has_text':bool(text),
            'original_url':'/bulk-files/'+row['file_id'],'text_url':'/api/text?'+urlencode({'id':row['id']}),'source_count':1,
            'group_basis':'Publisher record and snapshot; distinct source/version retained'}
        f=files[row['file_id']]
        detail=dict(item,metadata={'citation':row['citation'],'status':row['status'],'snapshot':row['snapshot'],'source_id':row['source_id'],'row_index':row['row_index'],
            'file':{'id':row['file_id'],'sha256':f['sha256'],'bytes':f['bytes']},'attribution':'open-us-law; CC BY 4.0 compilation; original official-source URLs retained','publisher_record':payload},
            text_characters=len(text),text_truncated=False,reading_notes={'method':'Publisher structured record text','original_preserved':True,'currency_verified':False,
                'publisher_newline_escapes_rendered':newline,'source_text_sha256':row['content_hash'],'display_text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'body_metadata_fields_omitted_for_export':removed},source_records=[],links=[],snapshot_label=row['snapshot'])
        detail.update(document_dates(detail['metadata']))
        writer.add({'id':row['id'],'dataset':'open_us_law','category':cat,'state':state,'county_geoids':[],'title':row['title'],'source_url':row['source_url'],
            'item':item,'detail':detail,'text':text,'filters':{'state':state,'state_usps':row['state'],'group':'laws','groups':['laws'],'kind':row['kind'],
                'category':cat,'citation':row['citation'],'status':row['status'],'snapshot':row['snapshot'],'file_id':row['file_id'],'row_index':row['row_index'],
                'source_rowid':row['source_rowid'],'availability':['original']+(['text'] if text else [])},
            'artifacts':[file_artifacts[row['file_id']]] if file_artifacts[row['file_id']] else [],'ordinal':row['source_rowid']})
        exported+=1
        if exported%25000==0:print(dumps({'stage':'bulk','rows':exported}),flush=True)
    db.close()
    if before!=stamp(path):raise ValueError('Publisher SQLite changed during export')
    excluded_receipt=[{'file_id':key,'kind':files[key]['kind'],'rows':count,'reason':'No semantic category mapping; classified as other'} for key,count in sorted(excluded_files.items())]
    write_json(writer.folder/'excluded_bulk_files.json',excluded_receipt)
    return {'source_rows':sum(f['rows'] for f in files.values()),'expected_selected_rows':sum(f['rows'] for key,f in files.items() if key in selected),
        'exported_rows':exported,'excluded_rows_seen':excluded,'excluded_file_kinds':sorted({f['kind'] for key,f in files.items() if key not in selected}),
        'artifact_files':sum(bool(v) for v in file_artifacts.values()),'artifact_bytes':sum(v['bytes'] for v in file_artifacts.values() if v),
        'exclusion_receipt':'excluded_bulk_files.json','limited':limit is not None,'snapshot_signature':before}

def export(output=OUTPUT, only='all', limit=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);start=time.monotonic();writer=Writer(output);artifacts=Artifacts();stages={}
    try:
        if only in ('main','all'):stages['main']=export_main(writer,artifacts,limit)
        if only in ('bulk','all'):stages['bulk']=export_bulk(writer,artifacts,limit)
        descriptors=writer.finish()
        prior=output/'dataset.json';old=json.loads(prior.read_text('utf-8')) if prior.is_file() else {}
        by_id={r['id']:r for r in old.get('datasets',[])};by_id.update({r['id']:r for r in descriptors})
        receipt={'schema_version':1,'status':'passed','generated_at':now(),'datasets':list(by_id.values()),'stages':{**old.get('stages',{}),**stages},
            'elapsed_seconds':round(time.monotonic()-start,2),'network_calls':0,'remote_writes':0,'category_policy':'Existing semantic category in allowlist; other/unmapped excluded. Capture quality flags preserved.',
            'local_paths_private_transfer_only':True,'source_databases_read_only':True,'artifact_files_hashed_this_run':len(artifacts.cache),
            'artifact_bytes_hashed_this_run':sum(v['bytes'] for v in artifacts.cache.values()),'missing_artifacts':artifacts.missing,
            'group_metadata':'display_groups.jsonl','county_metadata':'counties.jsonl','complete':limit is None and set({**old.get('stages',{}),**stages})=={'main','bulk'} and all(not v.get('limited') for v in {**old.get('stages',{}),**stages}.values())}
        if old.get('main_validation') and only=='bulk':receipt['main_validation']=old['main_validation']
        write_json(prior,receipt);return receipt
    finally:writer.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=OUTPUT);p.add_argument('--only',choices=['main','bulk','all'],default='all');p.add_argument('--limit',type=int)
    args=p.parse_args();print(dumps(export(args.output,args.only,args.limit)),flush=True)
