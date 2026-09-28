"""Resumable category-selected PostgREST import. Originals are registered separately.

Run one importer per export file; completed batch offsets are hash-bound in local SQLite.
The ready gate stays closed until the exported row count matches the remote count.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import threading
from urllib.parse import quote, urlencode, urlsplit, parse_qsl
from supabase_client import Client, StatementTimeout
from context_transfer import write_context
from import_lock import import_writer

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '_transfer_scratch/supabase_export'
ALLOWED = {'id','dataset','category','state','county_geoids','title','source_url','item','detail','text','filters','ordinal'}
_THREAD = threading.local()
TRANSFORM_VERSION = 'verified-full-text-preview-v1'
PREVIEW_CHARACTERS = 1_000_000
COUNT_ORDINAL_SPAN = 50_000
MIN_BIGINT = -(2 ** 63)
MAX_BIGINT = 2 ** 63 - 1


def ordinal_bucket(value):
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_BIGINT <= value <= MAX_BIGINT:
        raise ValueError('Catalog ordinal must be a PostgreSQL bigint')
    return max(MIN_BIGINT, value // COUNT_ORDINAL_SPAN * COUNT_ORDINAL_SPAN)


def exact_catalog_count(client, dataset, expected_buckets, *, max_requests=1024):
    """Count every remote row exactly using disjoint indexed ordinal ranges.

    The importer holds the shared writer lock throughout this verification. The
    ranges include gaps and both tails, so extra remote rows are never excluded.
    Each range must match its frozen source count, not an estimated/planned count.
    A confirmed timeout may split a finite range; persistent failures stay closed.
    """
    buckets=sorted(expected_buckets)
    if any(ordinal_bucket(k)!=k or not isinstance(expected_buckets[k],int)
           or isinstance(expected_buckets[k],bool) or expected_buckets[k]<1 for k in buckets):
        raise ValueError('Invalid expected ordinal buckets')
    partitions=([(None,buckets[0],0)] if buckets else [(None,None,0)])
    partitions += [(start,buckets[i+1] if i+1<len(buckets) else None,expected_buckets[start])
                   for i,start in enumerate(buckets)]
    if len(partitions)>max_requests:raise ValueError('Exact-count request budget exceeded')
    stats={'method':'exact_disjoint_ordinal_ranges','partitions':len(partitions),'requests':0,
           'split_timeouts':0,'ordinal_span':COUNT_ORDINAL_SPAN}
    def count(lower,upper,depth=0):
        if stats['requests']>=max_requests:raise RuntimeError('Exact-count request budget exceeded')
        query=[('dataset','eq.'+dataset),('select','id'),('limit','1')]
        if lower is not None:query.append(('ordinal','gte.'+str(lower)))
        if upper is not None:query.append(('ordinal','lt.'+str(upper)))
        stats['requests']+=1
        try:
            response=client.call('GET','/rest/v1/corpus_records?'+urlencode(query),headers={'Prefer':'count=exact'})
        except StatementTimeout:
            if lower is None or upper is None or upper-lower<2 or depth>=8:raise
            stats['split_timeouts']+=1
            middle=lower+(upper-lower)//2
            return count(lower,middle,depth+1)+count(middle,upper,depth+1)
        header=response.headers.get('Content-Range','')
        matched=re.fullmatch(r'(?:\*|\d+-\d+)/(\d+)',header)
        if not matched:raise RuntimeError('Exact remote count is absent or malformed')
        return int(matched[1])
    total=0
    for lower,upper,expected in partitions:
        actual=count(lower,upper)
        if actual!=expected:
            raise RuntimeError(f'Import range count mismatch: expected {expected}, received {actual}')
        total+=actual
    return total,stats


class VerifiedLargeText:
    """Use only hash-verified exact full-text artifacts from the reviewed manifest."""
    def __init__(self, manifest=None, *, preview_characters=PREVIEW_CHARACTERS):
        self.manifest=Path(manifest or LOCAL/'text_assets/large_text_assets.jsonl').resolve()
        self.preview_characters=preview_characters
        if preview_characters<1 or preview_characters>PREVIEW_CHARACTERS:
            raise ValueError('Invalid database preview size')
        raw=self.manifest.read_bytes();self.manifest_sha256=hashlib.sha256(raw).hexdigest()
        descriptor=json.loads(self.manifest.with_suffix('.dataset.json').read_text('utf-8'))
        self.validation=json.loads((self.manifest.parent/'validation.json').read_text('utf-8'))
        lines=raw.splitlines()
        if (descriptor.get('id')!='large_text_assets' or descriptor.get('sha256')!=self.manifest_sha256
                or descriptor.get('rows')!=len(lines) or self.validation.get('status')!='passed'
                or self.validation.get('exact_utf8_text') is not True):
            raise ValueError('Large-text manifest is not a validated hash-bound export')
        self.entries={};self.verified_files={}
        for line in lines:
            row=json.loads(line);detail=row.get('detail') or {};meta=detail.get('metadata') or {}
            key=(meta.get('source_dataset'),meta.get('source_record_id'))
            if not all(key) or key in self.entries:raise ValueError('Duplicate or absent large-text source identity')
            route='/api/text?'+urlencode({'id':key[1]})
            artifacts=row.get('artifacts') or []
            chosen=[a for a in artifacts if a.get('url')==route and a.get('role')=='cleaned_full_text']
            if len(chosen)!=1:raise ValueError('Missing unambiguous full-text source route')
            asset=chosen[0];sha=asset.get('sha256','')
            if not re.fullmatch(r'[0-9a-f]{64}',sha) or sha!=meta.get('display_text_sha256'):
                raise ValueError('Large-text artifact/metadata hash disagreement')
            if asset.get('bytes')!=meta.get('utf8_bytes') or not str(asset.get('mime','')).startswith('text/plain'):
                raise ValueError('Large-text artifact size/MIME disagreement')
            target=Path(asset['local_path']).resolve()
            if not target.is_relative_to((self.manifest.parent/'files').resolve()):
                raise ValueError('Large-text file escapes the reviewed asset directory')
            aliases=set(meta.get('reader_aliases') or [])
            for a in artifacts:
                parsed=urlsplit(a.get('url',''));params=parse_qsl(parsed.query,keep_blank_values=True)
                if (parsed.scheme or parsed.netloc or parsed.fragment or parsed.path!='/api/text'
                        or len(params)!=1 or params[0][0]!='id' or params[0][1] not in aliases
                        or a.get('sha256')!=sha or a.get('bytes')!=asset['bytes']
                        or Path(a.get('local_path','')).resolve()!=target):
                    raise ValueError('Large-text alias does not identify the exact same file')
            self.entries[key]={'asset':asset,'path':target,'characters':detail.get('source_text_characters'),'route':route}

    def validate_source(self,dataset,source_sha256):
        if any(key[0]==dataset for key in self.entries) and self.validation.get('source_sha256',{}).get(dataset)!=source_sha256:
            raise ValueError('Large-text manifest belongs to a different source export version')

    def apply(self,row):
        entry=self.entries.get((row.get('dataset'),row.get('id')))
        if entry is None:return row
        text=row.get('text') or ''
        # Verify the frozen source string and complete UTF-8 file independently.
        hasher=hashlib.sha256();byte_count=0
        for i in range(0,len(text),1_000_000):
            chunk=text[i:i+1_000_000].encode('utf-8');hasher.update(chunk);byte_count+=len(chunk)
        asset=entry['asset'];path=entry['path'];stat=path.stat()
        if hasher.hexdigest()!=asset['sha256'] or byte_count!=asset['bytes'] or len(text)!=entry['characters']:
            raise ValueError('Full-text source differs from the pinned complete artifact')
        identity=(str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if identity not in self.verified_files:
            with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=asset['sha256'] or stat.st_size!=asset['bytes']:
                raise ValueError('Complete full-text artifact is missing or corrupt')
            self.verified_files[identity]=actual
        elif self.verified_files[identity]!=asset['sha256']:
            raise ValueError('Conflicting complete artifact identity')
        preview=text.replace('\x00','')[:self.preview_characters]
        if len(preview)>=len(text):return row
        value=dict(row);value['text']=preview;value['detail']=dict(row.get('detail') or {})
        value['detail'].update(full_text_offloaded=True,full_text_characters=len(text),full_text_sha256=asset['sha256'],
            full_text_url=entry['route'],full_text_utf8_bytes=asset['bytes'],text_characters=len(text),
            text_truncated=True,text_preview_characters=len(preview),text_storage='verified_asset_with_database_preview',
            text_transform_version=TRANSFORM_VERSION)
        return value


def clean(value):
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def normalize(row, large_text=None):
    if large_text is not None:row=large_text.apply(row)
    value = clean({k: v for k, v in row.items() if k in ALLOWED})
    if not value.get('category') or value['category'] in ('unknown','uncategorized','other_unknown'):
        raise ValueError('Unclassified row reached the migration gate')
    for key in ('item','detail','filters'):
        value.setdefault(key, {})
    value.setdefault('text','')
    value.setdefault('county_geoids',[])
    value.setdefault('ordinal',0)
    value['title'] = value.get('title') or ''
    value['detail'].pop('text', None)
    def public(value):
        if isinstance(value,dict):return {k:public(v) for k,v in value.items() if k not in ('local_path','raw_path','text_path','metadata_path','source_path','evidence_path')}
        if isinstance(value,list):return [public(v) for v in value]
        if isinstance(value,str) and re.match(r'^(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp)/)',value):return '[Private source location retained in migration evidence]'
        return value
    value['item']=public(value['item'])
    value['detail']=public(value['detail'])
    if value.get('dataset') in ('federal','focused','judge_enrichment','judge_entities','judge_vendor','pending_publication','provider_laws','seeger','trellis_browser_counties'):
        value['filters']['captured_at']=((value['item'].get('facets') or {}).get('dates',{}).get('captured_at') or {}).get('value')
        if not value['item'].get('has_original') and not value['item'].get('has_text') and value.get('source_url'):
            value['filters']['availability']=['link_only']
    return value


def send(rows):
    if not hasattr(_THREAD, 'client'):
        _THREAD.client = Client()
    try:
        _THREAD.client.upsert('corpus_records', rows)
    except RuntimeError as exc:
        # A transaction timeout leaves the whole upsert unapplied. Split only that
        # atomic batch, preserving outer checkpoint offsets and exact row counts.
        if 'statement timeout' not in str(exc) or len(rows)<2:raise
        split=len(rows)//2
        return send(rows[:split])+send(rows[split:])
    return len(rows)


@import_writer
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('file', type=Path)
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--batch-rows', type=int, default=200)
    parser.add_argument('--batch-bytes', type=int, default=1_500_000)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    path = args.file.resolve()
    if not path.is_relative_to(LOCAL.resolve()):
        parser.error('Only reviewed export files in _transfer_scratch/supabase_export may be imported')
    metadata_path = args.metadata or path.with_suffix('.dataset.json')
    meta = json.loads(metadata_path.read_text(encoding='utf-8'))
    dataset = meta.get('id') or meta.get('dataset')
    hasher=hashlib.sha256(); declared_lines=0
    with path.open('rb') as source:
        while chunk:=source.read(8*1024*1024):hasher.update(chunk);declared_lines+=chunk.count(b'\n')
    digest=hasher.hexdigest()
    expected=next((meta[k] for k in ('expected_records','rows','records') if k in meta),None)
    if expected is None or expected!=declared_lines:raise ValueError('Export count differs from the reviewed dataset descriptor')
    expected_hash=meta.get('export_jsonl_sha256') or meta.get('sha256')
    if not expected_hash:raise ValueError('Export descriptor has no reviewed JSONL hash')
    if digest!=expected_hash:raise ValueError('Export hash differs from the reviewed dataset descriptor')
    large_text=VerifiedLargeText()
    large_text.validate_source(dataset,digest)
    client = Client()
    stored_meta = {k: v for k, v in meta.items() if k not in ('local_path','credential','secret')}
    stored_meta=normalize({'id':'metadata','dataset':'metadata','category':'directories','item':stored_meta})['item']
    if len(json.dumps(stored_meta,ensure_ascii=False))>400_000:
        key='dataset-meta:'+dataset
        write_context(client,key,stored_meta)
        stored_meta={'context_key':key}
    client.upsert('corpus_datasets', [{'id':dataset,'label':meta.get('label') or meta.get('title') or dataset,
        'ready':False,'expected_records':meta.get('expected_records') or meta.get('records') or meta.get('rows') or 0,'manifest_sha256':digest,'metadata':stored_meta}])
    state = sqlite3.connect(LOCAL / 'import_progress.sqlite3')
    state.execute('create table if not exists batches(file text,sha text,offset integer,records integer,primary key(file,sha,offset))')
    state.execute('create table if not exists batch_transforms(file text,sha text,offset integer,version text,manifest_sha text,primary key(file,sha,offset,version,manifest_sha))')
    journal_key=str(path) if (args.batch_rows,args.batch_bytes)==(200,1_500_000) else str(path)+f'#{args.batch_rows}:{args.batch_bytes}'
    done = {r[0] for r in state.execute('select offset from batches where file=? and sha=?',(journal_key,digest))}
    transformed_done={r[0] for r in state.execute('select offset from batch_transforms where file=? and sha=? and version=? and manifest_sha=?',(journal_key,digest,TRANSFORM_VERSION,large_text.manifest_sha256))}
    total = 0
    ordinal_buckets = {}
    offloaded_records=0
    pending = {}
    def collect(wait_all=False):
        completed, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.ALL_COMPLETED if wait_all else concurrent.futures.FIRST_COMPLETED)
        for job in completed:
            offset,transformed = pending.pop(job)
            rows = job.result()
            state.execute('insert or replace into batches values(?,?,?,?)',(journal_key,digest,offset,rows))
            if transformed:state.execute('insert or replace into batch_transforms values(?,?,?,?,?)',(journal_key,digest,offset,TRANSFORM_VERSION,large_text.manifest_sha256))
            state.commit()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool, path.open(encoding='utf-8') as source:
            rows=[]; size=0; offset=0;transformed=False
            for line in source:
                raw=json.loads(line)
                if raw.get('dataset') != dataset:
                    raise ValueError('Dataset identity mismatch')
                value=normalize(raw,large_text=large_text);is_offloaded=value['detail'].get('full_text_offloaded') is True
                bucket=ordinal_bucket(value['ordinal'])
                ordinal_buckets[bucket]=ordinal_buckets.get(bucket,0)+1
                rows.append(value);size+=len(line);total+=1;offloaded_records+=is_offloaded;transformed=transformed or is_offloaded
                if len(rows)>=args.batch_rows or size>=args.batch_bytes:
                    if offset not in done or (transformed and offset not in transformed_done):
                        pending[pool.submit(send,rows)]=(offset,transformed)
                        if len(pending)>=args.workers*2:collect()
                    offset=total;rows=[];size=0;transformed=False
                    if total%2000==0:print(json.dumps({'dataset':dataset,'processed':total}),flush=True)
            if rows and (offset not in done or (transformed and offset not in transformed_done)):pending[pool.submit(send,rows)]=(offset,transformed)
            if pending:collect(True)
    finally:
        state.close()
    remote,count_verification=exact_catalog_count(client,dataset,ordinal_buckets)
    if remote != total:raise RuntimeError(f'Import count mismatch: expected {total}, received {remote}')
    if total!=expected:raise RuntimeError('Export changed during import: row count mismatch')
    with path.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=digest:raise RuntimeError('Export changed during import: hash mismatch')
    client.json('PATCH','/rest/v1/corpus_datasets?id=eq.'+quote(dataset,safe=''),{'expected_records':total,'imported_records':total,'ready':args.activate},'return=minimal')
    receipt={'dataset':dataset,'records':total,'remote_count':remote,'source_sha256':digest,'ready':args.activate,
        'count_verification':count_verification,
        'transform_version':TRANSFORM_VERSION,'large_text_manifest_sha256':large_text.manifest_sha256,'offloaded_records':offloaded_records}
    path.with_suffix('.import.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt),flush=True)


if __name__ == '__main__':
    main()
