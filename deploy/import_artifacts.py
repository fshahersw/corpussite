"""Upload only category-selected, hash-verified originals to private Storage.

Content hashes deduplicate objects across exports. Route aliases stay separate.
Local checkpoints are not part of the web build. No artifact path is published.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import urlsplit, parse_qsl, urlencode
from supabase_client import Client, BUCKET, CapacityError
from import_lock import import_writer
from storage_tus import CHUNK_BYTES, upload_file
try:
    import orjson
except ImportError:
    orjson=None

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '_transfer_scratch/supabase_export'
_THREAD = threading.local()


def canonical_route(url):
    value = urlsplit(url)
    if value.scheme or value.netloc or not value.path.startswith('/'):
        raise ValueError('Artifact URL must be an existing same-origin route')
    return value.path + ('?' + urlencode(sorted(parse_qsl(value.query, keep_blank_values=True))) if value.query else '')


def declared_bytes(asset):
    if not isinstance(asset.get('bytes'),int) or isinstance(asset['bytes'],bool) or asset['bytes']<0:
        raise ValueError('Missing nonnegative source byte count')
    return asset['bytes']


def reviewed_file(asset):
    declared_bytes(asset)
    path = Path(asset['local_path']).resolve()
    if not path.is_absolute() or any(x in {'.auth','.git','.env','.firecrawl'} for x in path.parts):
        raise ValueError('Forbidden artifact path')
    if not path.is_relative_to(ROOT.parent):
        raise ValueError('Artifact outside the user-selected local collection roots')
    if not re.fullmatch('[a-f0-9]{64}', asset['sha256']):
        raise ValueError('Missing source content hash')
    if path.stat().st_size != asset['bytes']:
        raise ValueError('Source artifact byte count changed')
    return path


def collect_artifacts(source):
    """Stat each identical file/hash/byte declaration once, but validate every route."""
    assets={};routes={};reviewed={};canonical={}
    with Path(source).open('rb') as stream:
        for line in stream:
            row=orjson.loads(line) if orjson is not None else json.loads(line)
            for a in row.get('artifacts') or []:
                identity=(a['local_path'],a['sha256'],declared_bytes(a))
                if identity not in reviewed:
                    reviewed[identity]=reviewed_file(a)
                path=reviewed[identity]
                if a['url'] not in canonical:canonical[a['url']]=canonical_route(a['url'])
                route=canonical[a['url']]
                previous=routes.get(route)
                if previous and (previous['sha256'],previous['bytes'])!=(a['sha256'],a['bytes']):
                    raise ValueError('Conflicting bytes for the same artifact route')
                previous_object=assets.get(a['sha256'])
                if previous_object and previous_object['bytes']!=a['bytes']:
                    raise ValueError('Conflicting byte declarations for the same content hash')
                routes[route]={**a,'local_path':str(path)}
                assets.setdefault(a['sha256'],routes[route])
    return assets,routes


def upload(asset):
    # Scan-cache entries never substitute for validation at the actual upload.
    path=reviewed_file(asset)
    if asset['bytes']>CHUNK_BYTES:
        return upload_file(path,asset,LOCAL/'tus_progress',client_factory=Client)
    raw=path.read_bytes()
    if len(raw)!=asset['bytes'] or hashlib.sha256(raw).hexdigest()!=asset['sha256']:
        raise ValueError('Source artifact hash changed')
    if not hasattr(_THREAD,'client'):_THREAD.client=Client()
    client=_THREAD.client
    key=asset['sha256'][:2]+'/'+asset['sha256']
    client.call('POST','/storage/v1/object/'+BUCKET+'/'+key,data=raw,
        headers={'Content-Type':asset.get('mime') or 'application/octet-stream','x-upsert':'true','Cache-Control':'private, max-age=3600'},timeout=180)
    return asset['sha256'],asset['bytes'],key


@import_writer
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('file',type=Path)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--max-total-gib',type=float,default=80)
    args=parser.parse_args()
    workers=max(1,min(args.workers,4))
    source=args.file.resolve()
    if not source.is_relative_to(LOCAL.resolve()):parser.error('Use a reviewed export in _transfer_scratch/supabase_export')
    assets,routes=collect_artifacts(source)
    state=sqlite3.connect(LOCAL/'artifact_progress.sqlite3')
    state.execute('create table if not exists objects(sha256 text primary key,bytes integer,object_key text)')
    done={row[0]:{'bytes':row[1],'key':row[2]} for row in state.execute('select * from objects')}
    new=[a for h,a in assets.items() if h not in done]
    if sum(a['bytes'] for a in new)+sum(a['bytes'] for a in done.values())>args.max_total_gib*1024**3:
        state.close()
        raise RuntimeError('Reviewed storage transfer ceiling would be exceeded; no uploads started')
    client=Client()
    buckets=client.json('GET','/storage/v1/bucket')
    bucket=next((b for b in buckets if b['id']==BUCKET),None)
    if bucket and bucket.get('public'):raise ValueError('Corpus bucket must be private')
    if not bucket:client.json('POST','/storage/v1/bucket',{'id':BUCKET,'name':BUCKET,'public':False})
    print(json.dumps({'unique_objects':len(assets),'new_objects':len(new),'new_bytes':sum(a['bytes'] for a in new),'routes':len(routes)}),flush=True)
    failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={}
        iterator=iter(new)
        while True:
            while len(jobs)<workers*2:
                a=next(iterator,None)
                if a is None:break
                jobs[pool.submit(upload,a)]=a
            if not jobs:break
            finished,_=concurrent.futures.wait(jobs,return_when=concurrent.futures.FIRST_COMPLETED)
            for job in finished:
                candidate=jobs.pop(job)
                try:digest,size,key=job.result()
                except CapacityError:
                    for pending in jobs:pending.cancel()
                    raise
                except Exception as exc:
                    failures.append({'sha256':candidate['sha256'],'bytes':candidate['bytes'],'error':str(exc)[:300]})
                    print(json.dumps({'held_artifact':candidate['sha256'],'error_type':type(exc).__name__}),flush=True)
                    continue
                state.execute('insert or replace into objects values(?,?,?)',(digest,size,key));state.commit()
                done[digest]={'bytes':size,'key':key}
                if len(done)%100==0:print(json.dumps({'uploaded_objects_total':len(done)}),flush=True)
    batch=[]
    for route,a in routes.items():
        if a['sha256'] not in done:continue
        batch.append({'route':route,'sha256':a['sha256'],'bytes':a['bytes'],'object_key':done[a['sha256']]['key'],
                      'mime':a.get('mime') or 'application/octet-stream','filename':Path(a['local_path']).name,'ready':False})
        if len(batch)>=200:client.upsert('corpus_artifacts',batch);batch=[]
    if batch:client.upsert('corpus_artifacts',batch)
    result={'status':'held' if failures else 'uploaded','objects':len(assets),'routes':len(routes),'bytes':sum(a['bytes'] for a in assets.values()),
            'private_bucket':True,'source_hashes_verified':True,'remote_readback_audit':'pending',
            'ready':False,'publication':'held_pending_independent_acceptance'}
    result['failures']=failures
    source.with_suffix('.artifacts.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    state.close()
    print(json.dumps({**result,'failures':len(failures)}),flush=True)


if __name__=='__main__':main()
