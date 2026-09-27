"""Import only a completed local outline export; resume verified batches safely.

Remote writes happen only when this script is explicitly invoked. Root migration
coordination owns invocation. The public outline gate stays closed until all three
table counts match and --activate is supplied; the API also requires the law catalog.
"""
from __future__ import annotations
import argparse,concurrent.futures,hashlib,json,sqlite3,threading
from pathlib import Path
from supabase_client import Client
from import_lock import import_writer

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'_transfer_scratch/supabase_export'
THREAD=threading.local()

def clean(value):
    if isinstance(value,str):return value.replace('\x00','')
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    return value

def upload(table,rows):
    if not hasattr(THREAD,'client'):THREAD.client=Client()
    THREAD.client.upsert(table,rows)
    return len(rows)

@import_writer
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,default=LOCAL/'law_outline')
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--activate',action='store_true')
    args=parser.parse_args();folder=args.folder.resolve()
    if not folder.is_relative_to(LOCAL.resolve()):parser.error('Outline export must be inside the reviewed private transfer folder')
    manifest=json.loads((folder/'manifest.json').read_text('utf-8'))
    if manifest.get('status')!='passed':raise ValueError('Export validation did not pass')
    expected=('corpus_law_collections','corpus_law_nodes','corpus_law_segments')
    if tuple(x['table'] for x in manifest['tables'])!=expected:raise ValueError('Unexpected export tables/order')
    for descriptor in manifest['tables']:
        path=(folder/descriptor['path']).resolve()
        if path.parent!=folder:raise ValueError('Unexpected export path')
        with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        if sha!=descriptor['sha256']:raise ValueError('Export hash mismatch')
    context=clean(json.loads((folder/manifest['context']).read_text('utf-8')))
    context['data']['ready']=False
    client=Client();client.upsert('corpus_context',[context])
    journal=sqlite3.connect(folder/'import_progress.sqlite3')
    journal.execute('create table if not exists batches(table_name text,sha text,offset integer,rows integer,primary key(table_name,sha,offset))')
    workers=max(1,min(args.workers,8));receipts=[]
    for descriptor in manifest['tables']:
        table=descriptor['table'];sha=descriptor['sha256'];path=folder/descriptor['path']
        done={r[0] for r in journal.execute('select offset from batches where table_name=? and sha=?',(table,sha))}
        total=0;offset=0;size=0;batch=[];pending={}
        def collect(all_jobs=False):
            completed,_=concurrent.futures.wait(pending,return_when=concurrent.futures.ALL_COMPLETED if all_jobs else concurrent.futures.FIRST_COMPLETED)
            for job in completed:
                start=pending.pop(job);count=job.result()
                journal.execute('insert or replace into batches values(?,?,?,?)',(table,sha,start,count));journal.commit()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool,path.open('r',encoding='utf-8') as stream:
            for line in stream:
                batch.append(clean(json.loads(line)));size+=len(line);total+=1
                if len(batch)>=1000 or size>=1500000:
                    if offset not in done:
                        pending[pool.submit(upload,table,batch)]=offset
                        if len(pending)>=workers*2:collect()
                    offset=total;size=0;batch=[]
                    if total%20000==0:print(json.dumps({'table':table,'processed':total}),flush=True)
            if batch and offset not in done:pending[pool.submit(upload,table,batch)]=offset
            if pending:collect(True)
        response=client.call('GET','/rest/v1/'+table+'?select=*&limit=1',headers={'Prefer':'count=exact'})
        remote=int(response.headers['Content-Range'].split('/')[-1])
        if remote!=total or total!=descriptor['rows']:raise ValueError('Outline table count mismatch: '+table)
        receipts.append({'table':table,'records':total,'remote_records':remote,'sha256':sha})
    context['data']['ready']=args.activate;client.upsert('corpus_context',[context])
    receipt={'status':'passed','tables':receipts,'outline_ready':args.activate,'law_catalog_readiness_required_separately':True}
    (folder/'import_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n','utf-8')
    print(json.dumps(receipt),flush=True)

if __name__=='__main__':main()
