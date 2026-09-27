"""Deduplicate the fixed 51-capture browser-law snapshot from pending Firecrawl work.

No network, credentials, collector startup, original-content writes or index rebuild.
Only verified exact-URL laws are touched. Browser captures never become provider downloads.
"""
from __future__ import annotations
import argparse, collections, datetime, hashlib, importlib.util, json, os, re, sqlite3, subprocess, sys, uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'pipeline'))
from firecrawl_batch_worker import worker_lock
from firecrawl_worker import allowed, category
spec=importlib.util.spec_from_file_location('browser_law_scope',ROOT/'scripts/trellis_browser_archive.py')
archive=importlib.util.module_from_spec(spec);spec.loader.exec_module(archive)
EXPECTED_SHA='e1aae68fa03202bbd0d5906550e540af7ad32a5aa2505b7c28d15b0784118287'
EXPECTED_COUNT=51

def sha(data):return hashlib.sha256(data).hexdigest()
def stable(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def verified_capture(base,record,line):
    if record.get('status')!='captured' or record.get('capture_kind')!='browser_rendered_dom' or record.get('category')!='state_rule':
        raise ValueError(f'Line {line}: not a successful browser law capture')
    url=archive.law_url(record['source_url'])
    if not allowed(url) or category(url)[0]!='rules':raise ValueError('URL outside provider law scope')
    if record.get('archive_version') not in ('1.0.0','1.0.1'):raise ValueError('Unknown archive version')
    pairs={('law_text','div.rule-header'),('law_directory','div.rule-header' if record['archive_version']=='1.0.0' else 'div.profileBillingContainer')}
    if (record.get('content_kind'),record.get('dom_selector')) not in pairs:raise ValueError('Unreviewed DOM selector')
    if record.get('source_http_status') is not None or record.get('source_network_requests_by_archiver')!=0 or record.get('cookies_or_credentials_exported') is not False:
        raise ValueError('Incorrect browser/HTTP provenance')
    data={}
    for kind,suffix in [('raw','json'),('text','txt')]:
        h=record[kind+'_sha256']
        if not re.fullmatch('[0-9a-f]{64}',h):raise ValueError('Invalid artifact hash')
        expected=f'{kind}/{h[:2]}/{h}.{suffix}'
        if record[kind+'_path']!=expected:raise ValueError('Non-content-addressed artifact path')
        p=(base/expected).resolve();p.relative_to(base.resolve())
        data[kind]=p.read_bytes()
        if sha(data[kind])!=h or len(data[kind])!=record[kind+'_bytes']:raise ValueError('Artifact hash/size mismatch')
    raw=json.loads(data['raw'])
    fields={'url','title','heading','legal_text','legal_html','observed_law_links','captured_at','content_kind','dom_selector','signed_in_observed'}
    if not isinstance(raw,dict) or set(raw)-fields or not (fields-{'observed_law_links'}).issubset(raw):raise ValueError('Invalid raw capture fields')
    if raw['url']!=url or raw['legal_text'].encode('utf-8')!=data['text']:raise ValueError('Raw URL or text equality mismatch')
    for k in ('title','heading','legal_text','legal_html','captured_at','dom_selector'):
        if not isinstance(raw[k],str) or not raw[k].strip():raise ValueError('Missing capture field '+k)
    for k in ('title','heading','captured_at','content_kind','dom_selector','signed_in_observed','observed_law_links'):
        if raw.get(k,[])!=record.get(k,[]):raise ValueError('Manifest and raw capture disagree: '+k)
    if type(raw['signed_in_observed']) is not bool:raise ValueError('Invalid sign-in observation')
    if raw['content_kind']=='law_text' and len(raw['legal_text'].strip())<30:raise ValueError('Law body is too short')
    if datetime.datetime.fromisoformat(raw['captured_at'].replace('Z','+00:00')).tzinfo is None:raise ValueError('Missing capture timezone')
    links=raw.get('observed_law_links',[])
    if not isinstance(links,list) or len(links)>10000:raise ValueError('Invalid law links')
    for link in links:
        if set(link)!={'url','text'} or not isinstance(link['text'],str):raise ValueError('Invalid law link')
        archive.law_url(link['url'])
    content=json.dumps({k:v for k,v in raw.items() if k!='captured_at'},ensure_ascii=False,sort_keys=True).encode('utf-8')
    if sha(content)!=record['content_sha256']:raise ValueError('Timestamp-excluded content hash mismatch')
    return {'source_url':url,'capture_kind':'browser_rendered_dom','manifest_line':line,
        **{k:record[k] for k in ('archive_version','content_kind','captured_at','raw_path','raw_sha256','raw_bytes','text_path','text_sha256','text_bytes','content_sha256')},
        'raw_text_equality_verified':True,'artifact_hashes_verified':True,'source_http_status':None}

def check_no_worker_process():
    if os.name!='nt':return []  # The same exclusive worker lock remains authoritative.
    command="Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(pythonw?|node)(\\.exe)?$' -and $_.CommandLine -match 'firecrawl_(batch_)?worker\\.py|run_collector\\.py.+firecrawl' } | Select-Object ProcessId,Name | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,timeout=20,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    found=json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(found,dict):found=[found]
    if found:raise RuntimeError('A Firecrawl worker process is live: '+stable(found))
    return found

def table_fingerprint(db,table,exclude=()):
    if table not in ('frontier','attempts'):raise ValueError('Unsupported fingerprint table')
    query=f'SELECT * FROM {table}'
    if exclude:query+=' WHERE url NOT IN ('+','.join('?' for _ in exclude)+')'
    query+=' ORDER BY '+('url' if table=='frontier' else 'id')
    h=hashlib.sha256();count=0
    for row in db.execute(query,tuple(exclude)):
        h.update(stable(dict(row)).encode('utf-8')+b'\n');count+=1
    return {'rows':count,'logical_sha256':h.hexdigest()}

def reconcile(base,state,output,expected_sha=EXPECTED_SHA,expected_count=EXPECTED_COUNT,process_check=check_no_worker_process):
    manifest=base/'manifest.jsonl';manifest_bytes=manifest.read_bytes()
    if sha(manifest_bytes)!=expected_sha:raise ValueError('Manifest differs from the explicitly scoped snapshot')
    records=[json.loads(s) for s in manifest_bytes.decode('utf-8').splitlines() if s.strip()]
    if len(records)!=expected_count or len({r['source_url'] for r in records})!=expected_count:raise ValueError('Expected unique capture count differs')
    captures=[verified_capture(base,r,i) for i,r in enumerate(records,1)]
    urls=[x['source_url'] for x in captures]
    process_check()
    if (state/'batch_active.json').exists():raise RuntimeError('Remote/uncertain batch marker exists; do not reconcile')
    run_id=uuid.uuid4().hex
    with worker_lock(state):
        process_check()
        if (state/'batch_active.json').exists():raise RuntimeError('Remote/uncertain batch marker appeared')
        if manifest.read_bytes()!=manifest_bytes:raise ValueError('Browser manifest changed during validation')
        db=sqlite3.connect(state/'frontier.sqlite3');db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM frontier WHERE status IN ('fetching','batch_held')").fetchone()[0]:raise RuntimeError('Active or held provider rows exist')
            unrelated_before=table_fingerprint(db,'frontier',urls);attempts_before=table_fingerprint(db,'attempts')
            db.execute('''CREATE TABLE IF NOT EXISTS browser_capture_dedup(
                capture_key TEXT PRIMARY KEY,url TEXT NOT NULL,reconciled_at TEXT NOT NULL,run_id TEXT NOT NULL,
                action TEXT NOT NULL,browser_capture_json TEXT NOT NULL,before_row_json TEXT NOT NULL,after_row_json TEXT NOT NULL)''')
            results=[];changed=0;inserted=0
            for capture in captures:
                url=capture['source_url'];row=db.execute('SELECT * FROM frontier WHERE url=?',(url,)).fetchone()
                before=dict(row) if row else None
                identity=sha((url+'\n'+capture['raw_sha256']+'\n'+capture['text_sha256']).encode('utf-8'))
                old=db.execute('SELECT * FROM browser_capture_dedup WHERE capture_key=?',(identity,)).fetchone()
                if old:
                    if before!=json.loads(old['after_row_json']):raise ValueError('Previously reconciled provider row changed; requires explicit review: '+url)
                    results.append({'source_url':url,'action':'already_reconciled','original_action':old['action'],'capture_key':identity,'original_run_id':old['run_id']});continue
                if before and before['status']=='pending' and before['in_scope']==1 and before['category']=='rules':
                    count=db.execute("UPDATE frontier SET status='captured_elsewhere' WHERE url=? AND status='pending' AND in_scope=1 AND category='rules'",(url,)).rowcount
                    if count!=1:raise RuntimeError('Pending row changed during transaction')
                    action='updated_matching_pending';changed+=1
                elif before is None:
                    cat,priority,own_state=category(url)
                    db.execute('''INSERT INTO frontier(url,category,priority,state,discovered_from,status,attempts,response_path,error,in_scope,scope_reason)
                       VALUES(?,?,?,?,?,'captured_elsewhere',0,NULL,NULL,1,NULL)''',
                       (url,cat,priority,own_state,f'browser_rendered_dom:{manifest}:line={capture["manifest_line"]}'))
                    action='registered_absent_browser_capture';inserted+=1
                else:action='preserved_non_pending_or_out_of_scope'
                after=dict(db.execute('SELECT * FROM frontier WHERE url=?',(url,)).fetchone())
                if before and action=='updated_matching_pending':
                    assert {k:v for k,v in before.items() if k!='status'}=={k:v for k,v in after.items() if k!='status'}
                elif before and action=='preserved_non_pending_or_out_of_scope':assert before==after
                proof={**capture,'manifest_path':str(manifest),'manifest_sha256':expected_sha,'archive_root':str(base),
                    'provider_response_path_assigned':False,'provider_download_asserted':False}
                db.execute('INSERT INTO browser_capture_dedup VALUES(?,?,?,?,?,?,?,?)',
                    (identity,url,now(),run_id,action,stable(proof),stable(before),stable(after)))
                results.append({'source_url':url,'action':action,'capture_key':identity,'provider_before':before,'provider_after':after,'browser_capture':proof})
            unrelated_after=table_fingerprint(db,'frontier',urls);attempts_after=table_fingerprint(db,'attempts')
            if unrelated_after!=unrelated_before or attempts_after!=attempts_before:raise RuntimeError('Unrelated frontier rows or provider attempts changed')
            if (state/'batch_active.json').exists():raise RuntimeError('Remote batch marker appeared before commit')
            db.commit()
            ledger=[dict(r) for r in db.execute('SELECT * FROM browser_capture_dedup WHERE url IN ('+','.join('?' for _ in urls)+') ORDER BY url',urls)]
        except BaseException:
            db.rollback();raise
        finally:db.close()
        output.mkdir(parents=True,exist_ok=True)
        report={'completed_at':now(),'run_id':run_id,'script_version':'1.0.0','manifest_path':str(manifest),'manifest_sha256':expected_sha,
            'verified_browser_captures':len(captures),'pending_rows_changed':changed,'absent_capture_placeholders_inserted':inserted,
            'actions':dict(collections.Counter(r['action'] for r in results)),
            'unrelated_frontier_before':unrelated_before,'unrelated_frontier_after':unrelated_after,
            'provider_attempts_before':attempts_before,'provider_attempts_after':attempts_after,
            'network_requests':0,'provider_response_paths_or_attempt_counts_changed':False,
            'browser_capture_kind':'browser_rendered_dom','provider_downloads_asserted':0,
            'remote_batch_active_marker_present':False,'exclusive_worker_lock_used':True,
            'original_browser_manifest_unchanged':manifest.read_bytes()==manifest_bytes,
            'safe_to_launch_after_lock_release':True,'limitations':'Exact fixed browser-law snapshot only; later captures require explicit reconciliation.'}
        (output/f'{run_id}.report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        (output/f'{run_id}.changes.jsonl').write_text(''.join(stable(x)+'\n' for x in results),encoding='utf-8')
        (output/'browser_capture_mapping.jsonl').write_text(''.join(stable({**r,'browser_capture':json.loads(r.pop('browser_capture_json')),'provider_before':json.loads(r.pop('before_row_json')),'provider_after':json.loads(r.pop('after_row_json'))})+'\n' for r in ledger),encoding='utf-8')
        (output/'latest_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report

def main():
    args=argparse.ArgumentParser(description=__doc__);args.parse_args()
    report=reconcile(ROOT/'corpus/trellis_browser_laws',ROOT/'sources/trellis/worker',ROOT/'corpus/trellis_browser_laws/provider_queue_reconciliation')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
