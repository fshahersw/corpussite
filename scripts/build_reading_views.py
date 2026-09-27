"""Build source-bound, non-destructive reading text for the existing local directory."""
from pathlib import Path
import argparse,concurrent.futures,datetime,hashlib,json,sqlite3,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'delivery/archive-directory'))
from readable import reading_view,VERSION
OUT=ROOT/'sources/reading_views_20260918'

def task(row):
    record_id,title,url,text_path,raw_path,parent_sha,is_provision,text_sha,content_id=row
    text=(ROOT/text_path).read_text(encoding='utf-8',errors='replace') if text_path and (ROOT/text_path).is_file() else ''
    if content_id:
        catalog=sqlite3.connect((ROOT/'catalog/documents.sqlite3').as_uri()+'?mode=ro',uri=True)
        indexed=catalog.execute('SELECT text FROM contents WHERE id=?',(content_id,)).fetchone();catalog.close()
        if indexed:text=indexed[0]
    value=reading_view(text,title=title,source_url=url,raw_path=ROOT/raw_path if raw_path and not is_provision else None)
    if is_provision:value['notes']['section_text_from_exact_derivative']=True
    value['notes']['parent_record_id']=record_id;value['notes']['parent_raw_path']=raw_path;value['notes']['parent_raw_sha256']=parent_sha
    value['notes']['parent_text_sha256']=text_sha;value['notes']['section_text_from_exact_derivative']=is_provision
    value['notes']['canonical_content_id']=content_id
    return record_id,value

def main():
    p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=4);p.add_argument('--limit',type=int,default=0);args=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    source=sqlite3.connect((ROOT/'delivery/archive-directory/directory.sqlite3').as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    catalog=sqlite3.connect((ROOT/'catalog/documents.sqlite3').as_uri()+'?mode=ro',uri=True)
    indexed_hashes=dict(catalog.execute('SELECT id,text_sha256 FROM contents'));catalog.close()
    tasks=[]
    for row in source.execute("select id,title,source_url,payload,content_id from records where dataset in ('focused','pending_publication','provider_laws','federal','seeger')"):
        d=json.loads(row['payload']);text_path=d.get('text_path');raw_path=d.get('raw_path')
        if not text_path and not raw_path:continue
        if isinstance(d.get('metadata'),dict) and d['metadata'].get('text_evidence',{}).get('nonempty') is False:continue
        tasks.append((row['id'],row['title'],row['source_url'],text_path,raw_path,d.get('raw_sha256') or d.get('sha256'),str(d.get('kind','')).endswith('_provision'),indexed_hashes.get(row['content_id']) or d.get('text_file_sha256') or d.get('text_sha256'),row['content_id']))
    source.close()
    if args.limit:tasks=tasks[:args.limit]
    db=sqlite3.connect(OUT/'reading.sqlite3');db.execute('pragma journal_mode=WAL')
    db.executescript('CREATE TABLE IF NOT EXISTS reading(record_id TEXT PRIMARY KEY,text TEXT,links TEXT,notes TEXT,version TEXT);CREATE TABLE IF NOT EXISTS failures(record_id TEXT PRIMARY KEY,error TEXT);')
    # Reuse only a current reader version and the same expected parent evidence.
    prior={r[0]:json.loads(r[1]) for r in db.execute('select record_id,notes from reading where version=?',(VERSION,))}
    tasks=[t for t in tasks if t[0] not in prior or prior[t[0]].get('parent_raw_sha256')!=t[5] or prior[t[0]].get('parent_text_sha256',prior[t[0]].get('input_text_sha256'))!=t[7] or bool(prior[t[0]].get('section_text_from_exact_derivative'))!=t[6]]
    start=time.monotonic();done=0;errors=0;methods={}
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending={};it=iter(tasks)
        for _ in range(args.workers*3):
            item=next(it,None)
            if item:pending[pool.submit(task,item)]=item[0]
        while pending:
            completed,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in completed:
                key=pending.pop(future)
                try:
                    record_id,value=future.result();notes=value['notes'];methods[notes['method']]=methods.get(notes['method'],0)+1
                    db.execute('INSERT OR REPLACE INTO reading VALUES(?,?,?,?,?)',(record_id,value['text'],json.dumps(value['links'],ensure_ascii=False),json.dumps(notes,ensure_ascii=False),VERSION));db.execute('DELETE FROM failures WHERE record_id=?',(record_id,));done+=1
                except Exception as exc:db.execute('INSERT OR REPLACE INTO failures VALUES(?,?)',(key,type(exc).__name__+': '+str(exc)[:300]));errors+=1
                if (done+errors)%100==0:
                    db.commit();(OUT/'progress.json').write_text(json.dumps({'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'processed':done+errors,'planned':len(tasks),'saved':done,'failures':errors,'elapsed_seconds':round(time.monotonic()-start,1)},indent=2)+'\n');print(f'reading views {done+errors}/{len(tasks)}',flush=True)
                item=next(it,None)
                if item:pending[pool.submit(task,item)]=item[0]
    db.commit();count,readable=db.execute("select count(*),sum(text!='') from reading where version=?",(VERSION,)).fetchone();db.execute('pragma wal_checkpoint(TRUNCATE)');db.close()
    summary={'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'version':VERSION,'records':count,'readable_records':readable,'empty_records':count-readable,'new_saved':done,'errors':errors,'methods_this_run':methods,'elapsed_seconds':round(time.monotonic()-start,1),'originals_modified':False,'cleaning_scope':'Derived reading presentation; legal currency and substantive completeness not certified'}
    (OUT/'progress.json').write_text(json.dumps({'updated_at':summary['completed_at'],'status':'complete','processed':done+errors,'planned':len(tasks),'saved':done,'failures':errors},indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
