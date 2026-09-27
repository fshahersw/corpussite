"""Import verified Open US Law Parquets without executing upstream scraper code.

Individual-object requests stop after an observed access barrier. The exact
publisher-linked public bulk tar is handled as its own distribution, while
canonical gated HF access requires a separately authorized credential.
Public snapshots and checksums remain immutable. The SQLite index is separate
from the small central archive database; upstream scraper code is never run.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, contextlib, datetime, hashlib
import json, os, pathlib, re, shutil, sqlite3, sys, threading, time
import urllib.error, urllib.parse, urllib.request
import tarfile

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'sources/open_us_law_20260918'
MIRROR='https://oss-data-us.vaquill.ai/'
ATTRIBUTION='Structured US primary-law data from the Open US Law corpus by Vaquill AI (https://github.com/Vaquill-AI/open-us-law), used under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Local changes: checksum verification, search indexing, and source quality annotations; legal text not intentionally edited.'
STOP=threading.Event()

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8');os.replace(tmp,path)
def manifest():
    a=json.loads((OUT/'evidence/index.json').read_text(encoding='utf-8'));b=json.loads((OUT/'evidence/latest.json').read_text(encoding='utf-8'))
    fields=lambda m:[(x['file'],x['bytes'],x['sha256']) for x in m['files']]
    if a['version']!=b['version'] or fields(a)!=fields(b):raise ValueError('Pinned manifests disagree')
    # Manifest lists 229 Parquets plus README, checksums, totals and the tarball.
    # Its declared total_bytes covers the Parquets, not duplicate tar contents.
    a['files']=[x for x in a['files'] if x['file'].endswith('.parquet')]
    if len(a['files'])!=a['parquet_files'] or sum(x['bytes'] for x in a['files'])!=a['total_bytes']:raise ValueError('Manifest Parquet file/byte totals mismatch')
    if a['license_data']!='CC-BY-4.0':raise ValueError('Unreviewed data license')
    for x in a['files']:
        if not re.fullmatch(r'us_[a-z_]+\.parquet',x['file']):raise ValueError('Unsafe manifest filename')
        if not re.fullmatch('[a-f0-9]{64}',x['sha256']):raise ValueError('Malformed publisher checksum')
        if x['url']!=MIRROR+a['version']+'/'+x['file']:raise ValueError('URL differs from reviewed exact mirror distribution')
    return a
def open_request(url,headers=None):
    request=urllib.request.Request(url,headers={'User-Agent':'LegalArchiveResearch/1.0',**(headers or {})})
    return urllib.request.urlopen(request,timeout=90)
@contextlib.contextmanager
def importer_lock():
    OUT.mkdir(parents=True,exist_ok=True);f=(OUT/'import.lock').open('a+b');f.seek(0,2)
    if f.tell()==0:f.write(b'0');f.flush()
    f.seek(0)
    try:
        if os.name=='nt':
            import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl;fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        try:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        finally:f.close()

def download_one(item,m,source,token=None):
    target=OUT/'snapshot'/m['version']/item['file'];target.parent.mkdir(parents=True,exist_ok=True)
    receipt=OUT/'download_receipts'/f"{item['file']}.json"
    if target.is_file() and target.stat().st_size==item['bytes'] and sha(target)==item['sha256']:
        result={**item,'status':'checksum_verified','path':target.relative_to(ROOT).as_posix(),'checked_at':now()};write_json(receipt,result);return result
    if STOP.is_set():return {**item,'status':'not_attempted_after_barrier'}
    url=item['url'];headers={}
    if source=='huggingface':
        hf=json.loads((OUT/'evidence/huggingface_dataset.json').read_text(encoding='utf-8'));url=f"https://huggingface.co/datasets/vaquill/open-us-law/resolve/{hf['sha']}/{item['file']}";headers={'Authorization':'Bearer '+token}
    part=target.with_suffix('.parquet.part');result={**item,'requested_url':url,'started_at':now(),'status':'started'}
    try:
        # Full new GET avoids unsafe resume against a possibly changed object.
        with open_request(url,headers) as response,part.open('wb') as f:
            result.update(http_status=response.status,content_length=response.headers.get('Content-Length'),etag=response.headers.get('ETag'))
            total=0;h=hashlib.sha256()
            while chunk:=response.read(4*1024*1024):
                total+=len(chunk)
                if total>item['bytes']:raise ValueError('Response exceeds pinned manifest byte count')
                f.write(chunk);h.update(chunk)
            f.flush();os.fsync(f.fileno())
        result.update(downloaded_bytes=total,observed_sha256=h.hexdigest())
        if total!=item['bytes'] or h.hexdigest()!=item['sha256']:raise ValueError('Publisher size/checksum mismatch')
        os.replace(part,target);result.update(status='checksum_verified',path=target.relative_to(ROOT).as_posix())
    except urllib.error.HTTPError as e:
        result.update(status='access_barrier' if e.code in [401,403,429] else 'http_error',http_status=e.code,error=str(e))
        if e.code in [401,403,429]:STOP.set()
    except Exception as e:result.update(status='failed',error=f'{type(e).__name__}: {e}')
    result['completed_at']=now();write_json(receipt,result);return result

def download(m,args):
    if shutil.disk_usage(OUT).free<m['total_bytes']+10*1024**3:raise RuntimeError('Insufficient free disk for snapshot and index reserve')
    barrier=OUT/'access_barriers.json';token=None
    if args.source=='mirror' and barrier.exists():raise RuntimeError('Mirror data access is already denied. Do not retry unchanged access barriers; use authorized canonical access when available.')
    if args.source=='huggingface':
        token=os.environ.get(args.token_env)
        if not token:raise RuntimeError('Hugging Face is gated; an authorized credential with accepted dataset access is required. No credential was printed.')
    priority=lambda x:(0 if x['file'].startswith('us_ny_') else 1 if '_constitutions' in x['file'] else 2 if '_court_rules' in x['file'] else 3 if '_statutes' in x['file'] else 4,x['bytes'])
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(download_one,x,m,args.source,token) for x in sorted(m['files'],key=priority)]
        for future in concurrent.futures.as_completed(futures):
            result=future.result();rows.append(result)
            if len(rows)%10==0 or result['status'] not in ['checksum_verified','not_attempted_after_barrier']:print(json.dumps({'completed':len(rows),'expected':len(m['files']),'file':result['file'],'status':result['status']}),flush=True)
    write_json(OUT/'download_summary.json',{'completed_at':now(),'snapshot':m['version'],'source':args.source,'statuses':dict(collections.Counter(r['status'] for r in rows)),'verified_bytes':sum(r['bytes'] for r in rows if r['status']=='checksum_verified'),'files':rows})
    if STOP.is_set():raise RuntimeError('Download stopped after an access barrier; no retry scheduled')

def download_tar(m):
    """Exact distinct publisher-linked bulk file; no alternate path guessing."""
    original=json.loads((OUT/'evidence/index.json').read_text(encoding='utf-8'))
    entry=next(x for x in original['files'] if x['url']==original['combined_tarball_url'])
    folder=OUT/'snapshot'/m['version'];folder.mkdir(parents=True,exist_ok=True)
    target=folder/entry['file'];part=target.with_suffix('.tar.part')
    if shutil.disk_usage(OUT).free<entry['bytes']+m['total_bytes']+10*1024**3:raise RuntimeError('Insufficient free disk')
    receipt={'started_at':now(),'url':entry['url'],'expected_sha256':entry['sha256'],'expected_bytes':entry['bytes']}
    if not(target.exists() and target.stat().st_size==entry['bytes'] and sha(target)==entry['sha256']):
        try:
            with open_request(entry['url']) as response,part.open('wb') as f:
                receipt.update(http_status=response.status,headers=dict(response.headers));total=0;h=hashlib.sha256();last=0
                while chunk:=response.read(8*1024*1024):
                    total+=len(chunk)
                    if total>entry['bytes']:raise ValueError('Bulk object exceeds pinned size')
                    f.write(chunk);h.update(chunk)
                    if total-last>=128*1024**2:print(json.dumps({'bulk_downloaded_bytes':total,'expected':entry['bytes']}),flush=True);last=total
                f.flush();os.fsync(f.fileno())
            if total!=entry['bytes'] or h.hexdigest()!=entry['sha256']:raise ValueError('Bulk object checksum/size mismatch')
            os.replace(part,target);receipt.update(downloaded_bytes=total,observed_sha256=h.hexdigest(),status='checksum_verified')
        except Exception as e:
            receipt.update(status='failed',error=f'{type(e).__name__}: {e}',completed_at=now());write_json(OUT/'download_tar_receipt.json',receipt);raise
    else:receipt['status']='existing_checksum_verified'
    seen=set();expected={x['file']:x for x in m['files']};extracted=[]
    with tarfile.open(target,'r:') as archive:
        for member in archive:
            if not member.isfile():
                if member.isdir():continue
                raise ValueError('Unexpected non-regular archive member: '+member.name)
            logical=pathlib.PurePosixPath(member.name)
            if logical.is_absolute() or '..' in logical.parts:raise ValueError('Unsafe archive path')
            name=logical.name
            if name not in expected:raise ValueError('Unlisted archive member: '+member.name)
            if name in seen:raise ValueError('Duplicate archive member')
            item=expected[name]
            if member.size!=item['bytes']:raise ValueError('Archive member size mismatch')
            output=folder/name;temporary=output.with_suffix('.parquet.part');h=hashlib.sha256()
            with archive.extractfile(member) as source,temporary.open('wb') as dest:
                while chunk:=source.read(4*1024**2):dest.write(chunk);h.update(chunk)
            if h.hexdigest()!=item['sha256']:raise ValueError('Extracted Parquet hash mismatch: '+name)
            os.replace(temporary,output);seen.add(name);extracted.append({**item,'path':output.relative_to(ROOT).as_posix(),'status':'checksum_verified','archive_sha256':entry['sha256']})
    if seen!=set(expected):raise ValueError('Archive did not contain every manifest Parquet')
    receipt.update(completed_at=now(),extracted_verified_files=len(seen),source_path=target.relative_to(ROOT).as_posix());write_json(OUT/'download_tar_receipt.json',receipt)
    write_json(OUT/'download_summary.json',{'completed_at':now(),'snapshot':m['version'],'source':'exact_publisher_bulk_tar','statuses':{'checksum_verified':len(extracted)},'verified_bytes':sum(x['bytes'] for x in extracted),'files':extracted})
    print(json.dumps({'bulk_verified':True,'parquet_files':len(seen)}),flush=True)

def plain(value):
    if value is None:return ''
    if isinstance(value,str):return value
    if isinstance(value,(list,tuple)):return '\n'.join(plain(v) for v in value if v is not None)
    if isinstance(value,dict):
        for key in ['text','node_text','paragraphs','content']:
            if key in value:return plain(value[key])
        return json.dumps(value,ensure_ascii=False,default=str)
    return str(value)
def chosen(row,names):
    for name in names:
        if row.get(name) is not None and plain(row[name]).strip():return plain(row[name])
    return ''
def normalize(row,filename,row_index,snapshot):
    parts=filename.removesuffix('.parquet').split('_');state=parts[1].upper();kind='_'.join(parts[2:])
    text=chosen(row,['text','node_text','section_text','content','chunk_text','act_text'])
    source_id=chosen(row,['id','act_id','node_id']);citation=chosen(row,['citation','section_citation'])
    title=chosen(row,['section_title','node_name','title','act_title','heading']) or citation or source_id or f'{filename} row {row_index+1}'
    source=chosen(row,['source_url','link','url']);status=chosen(row,['act_status','status'])
    payload={k:v for k,v in row.items() if k not in ['text','node_text','section_text','content','chunk_text','act_text']}
    ident='oul:'+hashlib.sha256(f'{snapshot}/{source_id}/{filename}:{row_index}'.encode()).hexdigest()
    return (ident,source_id,title,state,kind,source,text,json.dumps(payload,ensure_ascii=False,default=str),filename,row_index,hashlib.sha256(text.encode()).hexdigest(),snapshot,citation,status)

def index(m,finalize_checked=False):
    import pyarrow.parquet as pq
    recovery=None
    if finalize_checked:
        recovery=json.loads((OUT/'finalization_recovery.json').read_text(encoding='utf-8'))
        building_stat=(OUT/'catalog.building.sqlite3').stat()
        if not recovery.get('fts_external_content_integrity_returned_successfully') or recovery['manifest_sha256']!=sha(OUT/'evidence/index.json'):raise RuntimeError('Finalize-only recovery lacks a matching prior FTS verification receipt')
        if (building_stat.st_size,building_stat.st_mtime_ns)!=(recovery['building_database_bytes'],recovery['building_database_mtime_ns']):raise RuntimeError('Building database changed after the recovery receipt')
    folder=OUT/'snapshot'/m['version'];files=[];missing=[]
    for item in m['files']:
        path=folder/item['file']
        if not path.is_file():missing.append(item['file']);continue
        if path.stat().st_size!=item['bytes'] or (not finalize_checked and sha(path)!=item['sha256']):raise ValueError('Unverified original: '+item['file'])
        files.append((item,path))
    if not files:raise RuntimeError('No verified Parquet content is available; refusing an empty searchable-corpus claim')
    published=OUT/'catalog.sqlite3';marker_path=OUT/'ready.json'
    if published.is_file() and marker_path.is_file():
        marker=json.loads(marker_path.read_text(encoding='utf-8'))
        if marker.get('ready') is True:
            if marker.get('snapshot')!=m['version'] or marker.get('manifest_sha256')!=sha(OUT/'evidence/index.json'):raise RuntimeError('Published catalog belongs to another frozen manifest; use a separate snapshot directory')
            if missing:raise RuntimeError('Published catalog exists but original Parquets are missing: '+', '.join(missing))
            if sha(published)!=marker.get('database_sha256'):raise RuntimeError('Published catalog checksum differs from its ready marker; refusing to overwrite it')
            print(json.dumps({'already_ready':True,'indexed_rows':marker['indexed_rows'],'verified_files':len(files),'fts_ready':True}),flush=True)
            return
    dbpath=OUT/'catalog.building.sqlite3';db=sqlite3.connect(dbpath)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=NORMAL');db.execute('PRAGMA cache_size=-131072')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS records(id TEXT UNIQUE NOT NULL,source_id TEXT,title TEXT,state TEXT,kind TEXT,source_url TEXT,text TEXT,payload TEXT,file_id TEXT,row_index INTEGER,content_hash TEXT,snapshot TEXT,citation TEXT,status TEXT,UNIQUE(file_id,row_index));
    CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,bytes INTEGER,rows INTEGER,state TEXT,kind TEXT,indexed_at TEXT,schema_json TEXT);
    CREATE TABLE IF NOT EXISTS import_meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(title,citation,text,content='records',content_rowid='rowid',tokenize='unicode61');
    ''')
    if finalize_checked:
        existing_files=dict(db.execute('SELECT id,sha256 FROM files'))
        if missing or existing_files!={item['file']:item['sha256'] for item in m['files']}:raise RuntimeError('Finalize-only recovery does not cover the same complete verified files')
        if db.execute('SELECT count(*) FROM records').fetchone()[0]!=recovery['indexed_rows']:raise RuntimeError('Record count changed after prior verification')
    samples=[];stats=[]
    for item,path in files:
        filename=item['file'];pf=pq.ParquetFile(path,memory_map=True,buffer_size=65536,pre_buffer=False);columns=pf.schema_arrow.names
        if not any(c in columns for c in ['text','node_text','section_text','content','chunk_text','act_text']):raise ValueError('Unrecognized text schema: '+filename)
        existing=db.execute('select rows,sha256 from files where id=?',(filename,)).fetchone()
        if existing and existing[1]==item['sha256']:continue
        # Per-file transactions make interrupted builds safely resumable.
        db.execute('DELETE FROM records WHERE file_id=?',(filename,));offset=0;missingtext=0;missingsource=0;sectionidentity=0;literalnewline=0;status_counts=collections.Counter();type_counts=collections.Counter();shorttext=0;local_samples=[]
        # Some federal text row groups exceed 3 GB uncompressed. Parallel
        # Arrow column prefetch expands working memory drastically; stream
        # small batches through one decoder and keep mmap read-only.
        for batch in pf.iter_batches(batch_size=128,use_threads=False):
            normalized=[]
            for row in batch.to_pylist():
                rec=normalize(row,filename,offset,m['version']);normalized.append(rec)
                missingtext+=not bool(rec[6].strip());missingsource+=not bool(re.match(r'^https?://',rec[5]));sectionidentity+=bool(row.get('section_number'));literalnewline+='\\n' in rec[6];shorttext+=len(rec[6].strip())<80;status_counts[rec[13] or 'unrecorded']+=1;type_counts[plain(row.get('document_type')) or 'unrecorded']+=1
                if offset in {0,pf.metadata.num_rows//2,pf.metadata.num_rows-1}:local_samples.append({'file':filename,'row_index':offset,'id':rec[0],'title':rec[2],'state':rec[3],'kind':rec[4],'source_url':rec[5],'citation':rec[12],'status':rec[13],'text_characters':len(rec[6]),'text_preview':rec[6][:1600],'source_sha256':item['sha256']})
                offset+=1
            db.executemany('INSERT INTO records VALUES('+','.join('?'*14)+')',normalized)
        if offset!=pf.metadata.num_rows:raise ValueError('Row count mismatch: '+filename)
        parts=filename.removesuffix('.parquet').split('_');db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)',(filename,path.relative_to(ROOT).as_posix(),item['sha256'],item['bytes'],offset,parts[1].upper(),'_'.join(parts[2:]),now(),json.dumps({f.name:str(f.type) for f in pf.schema_arrow})))
        db.commit();quality={'file':filename,'rows':offset,'missing_text':missingtext,'missing_http_source_url':missingsource,'rows_with_section_number':sectionidentity,'rows_with_literal_newline_sequences':literalnewline,'short_text_under80_characters':shorttext,'status_counts':dict(status_counts),'document_type_counts':dict(type_counts),'schema_columns':columns,'samples':local_samples};write_json(OUT/'quality_by_file'/f'{filename}.json',quality);stats.append(quality);print(json.dumps({'indexed_file':filename,'rows':offset,'missing_text':missingtext}),flush=True)
        write_json(OUT/'index_progress.json',{'observed_at':now(),'indexed_files':db.execute('select count(*) from files').fetchone()[0],'indexed_rows':db.execute('select count(*) from records').fetchone()[0],'fts_ready':False})
    for statement in ['CREATE INDEX IF NOT EXISTS records_state_kind ON records(state,kind)','CREATE INDEX IF NOT EXISTS records_citation ON records(citation)','CREATE INDEX IF NOT EXISTS records_source_id ON records(source_id)']:db.execute(statement)
    if finalize_checked:
        print(json.dumps({'stage':'resuming_finalization_after_utf8_metadata_error','prior_fts_integrity_receipt':'finalization_recovery.json'}),flush=True)
    else:
        print(json.dumps({'stage':'building_full_text_index','rows':db.execute('select count(*) from records').fetchone()[0]}),flush=True)
        db.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')");db.commit()
        print(json.dumps({'stage':'checking_full_text_external_content_integrity'}),flush=True)
        db.execute("INSERT INTO records_fts(records_fts,rank) VALUES('integrity-check',1)")
    print(json.dumps({'stage':'sqlite_quick_check'}),flush=True)
    quick=db.execute('PRAGMA quick_check').fetchone()[0]
    if quick!='ok':raise RuntimeError('SQLite quick_check failed: '+str(quick))
    write_json(OUT/'verification_checkpoint.json',{'checked_at':now(),'fts_external_content_integrity_checked':True,'fts_integrity_reused_from_receipt':finalize_checked,'sqlite_quick_check':quick,'manifest_sha256':sha(OUT/'evidence/index.json')})
    print(json.dumps({'stage':'assembling_metadata','sqlite_quick_check':quick}),flush=True)
    total=db.execute('select count(*) from records').fetchone()[0]
    coverage=[dict(zip(['state','kind','rows'],r)) for r in db.execute('select state,kind,count(*) from records group by state,kind order by state,kind')]
    stats=[json.loads(f.read_text(encoding='utf-8')) for f in sorted((OUT/'quality_by_file').glob('*.json'))];samples=[s for q in stats for s in q['samples']]
    summary={'completed_at':now(),'snapshot':m['version'],'snapshot_date':m['snapshot_date'],'manifest_files':len(m['files']),'verified_downloaded_files':len(files),'indexed_rows':total,'missing_files':missing,'fts_ready':True,'sqlite_quick_check':quick,'full_national_corpus_complete':False,'publisher_withdrawals':['Georgia statutes','North Carolina statutes'],'attribution':ATTRIBUTION,'database':'sources/open_us_law_20260918/catalog.sqlite3','state_coverage':coverage,'state_counts':dict(db.execute('select state,count(*) from records group by state order by state')),'kind_counts':dict(db.execute('select kind,count(*) from records group by kind order by kind')),'sample_rows_reviewed_automatically':len(samples),'source_text_is_original_government_response':False,'row_counts_are_not_unique_sections':True,'body_measurements':{'nonempty_text_rows':total-sum(q['missing_text'] for q in stats),'rows_with_section_number':sum(q['rows_with_section_number'] for q in stats),'short_text_under80_characters':sum(q['short_text_under80_characters'] for q in stats),'rows_with_literal_newline_sequences':sum(q['rows_with_literal_newline_sequences'] for q in stats),'level_classifier_exported':False,'verified_unique_substantive_sections':None},'official_source_urls_retrieved_by_this_import':False}
    for key,value in summary.items():db.execute('INSERT OR REPLACE INTO import_meta VALUES(?,?)',(key,json.dumps(value)))
    db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close();os.replace(dbpath,OUT/'catalog.sqlite3')
    write_json(OUT/'import_summary.json',summary);write_json(OUT/'validation_samples.json',samples);write_json(OUT/'file_quality.json',stats)
    print(json.dumps({'stage':'hashing_published_database'}),flush=True)
    write_json(OUT/'ready.json',{'ready':True,'published_at':now(),'database':summary['database'],'database_sha256':sha(OUT/'catalog.sqlite3'),'manifest_sha256':sha(OUT/'evidence/index.json'),'indexed_rows':total,'snapshot':m['version'],'fts_external_content_integrity_checked':True,'sqlite_quick_check':quick})
    write_json(OUT/'index_progress.json',{'observed_at':now(),'indexed_files':len(files),'indexed_rows':total,'fts_ready':True,'stage':'ready'})
    print(json.dumps({'indexed_rows':total,'verified_files':len(files),'fts_ready':True,'missing_files':len(missing)}),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--download',action='store_true');parser.add_argument('--download-tar',action='store_true');parser.add_argument('--index',action='store_true');parser.add_argument('--finalize-checked',action='store_true',help='Recover metadata publication only with a matching prior FTS verification receipt');parser.add_argument('--source',choices=['mirror','huggingface'],default='mirror');parser.add_argument('--token-env',default='HF_TOKEN');parser.add_argument('--workers',type=int,choices=[1,2,3,4],default=4);args=parser.parse_args()
    with importer_lock():
        m=manifest()
        if args.download:download(m,args)
        if args.download_tar:download_tar(m)
        if args.index or args.finalize_checked:index(m,finalize_checked=args.finalize_checked)
        if not args.download and not args.download_tar and not args.index and not args.finalize_checked:print(json.dumps({'manifest_verified':True,'snapshot':m['version'],'files':len(m['files']),'bytes':m['total_bytes'],'downloaded_files':len(list((OUT/'snapshot'/m['version']).glob('*.parquet')))}))

if __name__=='__main__':main()
