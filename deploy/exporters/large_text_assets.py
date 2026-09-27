"""Save exact large cleaned-reader text as private downloadable artifacts.

The stable core exports remain unchanged. Each artifact retains the source record's
category and original URL, with aliases for deduplicated preferred-record readers.
"""
from __future__ import annotations
import argparse,collections,datetime,hashlib,json,time
from pathlib import Path
from urllib.parse import urlencode
try:import orjson
except ImportError:orjson=None

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'_transfer_scratch/supabase_export'
ALLOWED={'statutes','rules','constitutions','regulations','forms','guidance','directories','judges'}

def load(raw):return orjson.loads(raw) if orjson else json.loads(raw)
def line(row):return orjson.dumps(row,option=orjson.OPT_APPEND_NEWLINE) if orjson else (json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf-8')

def export(source=BASE/'core',output=BASE/'text_assets',threshold=3*1024*1024):
    source=Path(source).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    if not source.is_relative_to(BASE.resolve()) or not output.is_relative_to(BASE.resolve()):raise ValueError('Use reviewed private transfer folders')
    descriptor=load((source/'dataset.json').read_bytes())
    if not descriptor.get('complete') or descriptor.get('status')!='passed':raise ValueError('Core export is not complete and validated')
    aliases=collections.defaultdict(set)
    for raw in (source/'display_groups.jsonl').read_bytes().splitlines():
        group=load(raw);aliases[group['preferred_id']].add(group['id'])
    manifest=output/'large_text_assets.jsonl';temp=manifest.with_suffix('.tmp')
    written=0;ordinal=0;routes=set();objects={};counts=collections.Counter();categories=collections.Counter();source_hashes={}
    digest=hashlib.sha256();manifest_bytes=0;started=time.monotonic();source_rows=0
    with temp.open('wb') as dst:
        for ds in descriptor['datasets']:
            path=(source/ds['path']).resolve()
            if path.parent!=source:raise ValueError('Unexpected source export path')
            input_hash=hashlib.sha256();seen=0;dataset_assets=0
            with path.open('rb') as stream:
                for raw in stream:
                    input_hash.update(raw);seen+=1;source_rows+=1
                    # JSON escaping can only make this line larger than the UTF-8 text.
                    if len(raw)<=threshold:continue
                    record=load(raw);body=(record.get('text') or '').encode('utf-8')
                    if len(body)<=threshold:continue
                    if record.get('category') not in ALLOWED:raise ValueError('Unclassified source reached derivative export')
                    sha=hashlib.sha256(body).hexdigest();target=output/'files'/sha[:2]/(sha+'.txt')
                    if sha not in objects:
                        target.parent.mkdir(parents=True,exist_ok=True)
                        if target.exists():
                            with target.open('rb') as existing:actual=hashlib.file_digest(existing,'sha256').hexdigest()
                            if actual!=sha:raise ValueError('Existing derivative hash mismatch')
                        else:target.write_bytes(body)
                        objects[sha]={'bytes':len(body),'path':str(target)}
                    ids={record['id']}|aliases.get(record['id'],set())
                    artifacts=[]
                    for rid in sorted(ids):
                        url='/api/text?'+urlencode({'id':rid});routes.add(url)
                        artifacts.append({'url':url,'local_path':str(target),'sha256':sha,'bytes':len(body),'mime':'text/plain; charset=utf-8','role':'cleaned_full_text'})
                    ordinal+=1;row_id='large-text:'+record['id'];meta=record.get('detail') or {}
                    item={'id':row_id,'dataset':'large_text_assets','title':record['title'],'state':record.get('state'),
                        'source_url':record.get('source_url'),'category':record['category'],'kind':'cleaned_full_text','has_original':True,
                        'has_text':False,'original_url':'/api/text?'+urlencode({'id':record['id']}),'text_url':''}
                    detail={**item,'metadata':{'source_record_id':record['id'],'source_dataset':record['dataset'],'display_text_sha256':sha,
                        'utf8_bytes':len(body),'encoding':'UTF-8','reader_aliases':sorted(ids),'derivative_note':'Exact complete cleaned-reader text from the validated core export; original publisher sources remain separate.'},
                        'source_as_of':meta.get('source_as_of'),'saved_at':meta.get('saved_at'),'effective_date':meta.get('effective_date'),
                        'date_evidence':meta.get('date_evidence'),'text_characters':0,'source_text_characters':len(record['text']),'text_truncated':False}
                    row={'id':row_id,'dataset':'large_text_assets','category':record['category'],'state':record.get('state'),
                        'county_geoids':record.get('county_geoids') or [],'title':record['title'],'source_url':record.get('source_url'),
                        'item':item,'detail':detail,'text':'','filters':{'_listing':'no','source_record_id':record['id'],'source_dataset':record['dataset'],
                        'kind':'cleaned_full_text','availability':['original']},'artifacts':artifacts,'ordinal':ordinal}
                    encoded=line(row);dst.write(encoded);digest.update(encoded);manifest_bytes+=len(encoded);written+=1;dataset_assets+=1
                    counts[record['dataset']]+=1;categories[record['category']]+=1
            actual=input_hash.hexdigest()
            if seen!=ds['rows'] or actual!=ds['sha256']:raise ValueError('Stable core export hash/count mismatch: '+ds['id'])
            source_hashes[ds['id']]=actual
            print(json.dumps({'dataset':ds['id'],'scanned':seen,'large_text_assets':dataset_assets}),flush=True)
    temp.replace(manifest)
    dataset={'id':'large_text_assets','label':'Complete large reader-text downloads','rows':written,'path':manifest.name,
        'bytes':manifest_bytes,'sha256':digest.hexdigest(),'categories':dict(categories),'reader_only':True,'listing':{'available':True,'results':[]}}
    save(output/'large_text_assets.dataset.json',dataset)
    receipt={'status':'passed','generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'threshold_utf8_bytes':threshold,
        'records':written,'unique_objects':len(objects),'route_aliases':len(routes),'artifact_bytes':sum(v['bytes'] for v in objects.values()),
        'largest_artifact_bytes':max((v['bytes'] for v in objects.values()),default=0),'source_rows_scanned':source_rows,
        'source_dataset_counts':dict(counts),'source_sha256':source_hashes,'source_files_unchanged':True,'exact_utf8_text':True,
        'elapsed_seconds':round(time.monotonic()-started,2),'network_calls':0,'remote_writes':0}
    save(output/'validation.json',receipt);return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=BASE/'core')
    parser.add_argument('--output',type=Path,default=BASE/'text_assets');parser.add_argument('--threshold-bytes',type=int,default=3*1024*1024)
    args=parser.parse_args()
    if args.threshold_bytes<1:parser.error('threshold must be positive')
    print(json.dumps(export(args.source,args.output,args.threshold_bytes)),flush=True)
