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
from urllib.parse import quote
from supabase_client import Client
from context_transfer import write_context
from import_lock import import_writer

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '_transfer_scratch/supabase_export'
ALLOWED = {'id','dataset','category','state','county_geoids','title','source_url','item','detail','text','filters','ordinal'}
_THREAD = threading.local()


def clean(value):
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def normalize(row):
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
    journal_key=str(path) if (args.batch_rows,args.batch_bytes)==(200,1_500_000) else str(path)+f'#{args.batch_rows}:{args.batch_bytes}'
    done = {r[0] for r in state.execute('select offset from batches where file=? and sha=?',(journal_key,digest))}
    total = 0
    pending = {}
    def collect(wait_all=False):
        completed, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.ALL_COMPLETED if wait_all else concurrent.futures.FIRST_COMPLETED)
        for job in completed:
            offset = pending.pop(job)
            rows = job.result()
            state.execute('insert or replace into batches values(?,?,?,?)',(journal_key,digest,offset,rows));state.commit()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool, path.open(encoding='utf-8') as source:
        rows=[]; size=0; offset=0
        for line in source:
            raw=json.loads(line)
            if raw.get('dataset') != dataset:
                raise ValueError('Dataset identity mismatch')
            rows.append(normalize(raw));size+=len(line);total+=1
            if len(rows)>=args.batch_rows or size>=args.batch_bytes:
                if offset not in done:
                    pending[pool.submit(send,rows)]=offset
                    if len(pending)>=args.workers*2:collect()
                offset=total;rows=[];size=0
                if total%2000==0:print(json.dumps({'dataset':dataset,'processed':total}),flush=True)
        if rows and offset not in done:pending[pool.submit(send,rows)]=offset
        if pending:collect(True)
    response=client.call('GET','/rest/v1/corpus_records?dataset=eq.'+quote(dataset,safe='')+'&select=id&limit=1',headers={'Prefer':'count=exact'})
    remote=int(response.headers['Content-Range'].split('/')[-1])
    if remote != total:raise RuntimeError(f'Import count mismatch: expected {total}, received {remote}')
    if total!=expected:raise RuntimeError('Export changed during import: row count mismatch')
    with path.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=digest:raise RuntimeError('Export changed during import: hash mismatch')
    client.json('PATCH','/rest/v1/corpus_datasets?id=eq.'+quote(dataset,safe=''),{'expected_records':total,'imported_records':total,'ready':args.activate},'return=minimal')
    receipt={'dataset':dataset,'records':total,'remote_count':remote,'source_sha256':digest,'ready':args.activate}
    path.with_suffix('.import.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt),flush=True)


if __name__ == '__main__':
    main()
