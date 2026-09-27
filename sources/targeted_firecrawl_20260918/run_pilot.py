"""Finite, source-controlled provider captures; never labels them HTTP originals."""
from __future__ import annotations
import concurrent.futures, datetime, hashlib, json, os, pathlib, subprocess, sys, threading, time, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'pipeline'))
from corpus_crawler import Config, Artifacts, Gate, Fetcher, PacingDeferred, PausedHost

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def collect(seed):
    url=seed['source_url']; host=urllib.parse.urlsplit(url).netloc.lower()
    ident=hashlib.sha256(url.encode()).hexdigest()[:20]
    attempt=OUT/'attempts'/f'{ident}.json'
    if attempt.exists():
        previous=json.loads(attempt.read_text())
        if previous['status'] not in {'shared_host_deferred','host_pacing_deferred'}:return previous
        # Scheduling deferrals made no provider request; retain that evidence.
        save(OUT/'scheduling_deferrals'/f'{ident}-{time.time_ns()}.json',previous)
    result={'id':'fc-'+ident,**seed,'started_at':now(),'status':'preflight','provider':'Firecrawl','original_http_bytes':False}
    cfg=Config(allow=[{'host':host,'path_prefixes':['/']}],timeout_seconds=20,max_retries=0,follow_links=False,shared_host_dir=str(ROOT/'corpus/_shared_hosts'))
    artifacts=Artifacts(OUT/'access_evidence'/ident,cfg); stop=threading.Event();gate=Gate(cfg,{},stop,artifacts);fetcher=Fetcher(cfg,artifacts,gate,stop)
    # Existing source-control evidence predates SharedHosts for some NY seeds.
    for line in (ROOT/'reports/laws/resource_inventory.jsonl').open(encoding='utf-8'):
        known=json.loads(line);known_host=urllib.parse.urlsplit(known.get('url','')).netloc.lower()
        same_publisher=known_host.removeprefix('www.')==host.removeprefix('www.')
        if same_publisher and known.get('host_pause'):
            result.update(status='existing_access_barrier',barrier_url=known['url'],barrier=known['host_pause']);save(attempt,result);return result
    shared,busy=gate.shared.inspect(host)
    result['host_preflight']=shared
    if shared.get('pause_reason') or shared.get('cooldown_until',0)>time.time() or busy:
        result.update(status='shared_host_deferred');save(attempt,result);return result
    try:
        # A robots fetch is independently preserved and paced using the established crawler.
        for _ in range(15):
            try:
                allowed,reason=fetcher.robots(url);break
            except PacingDeferred as e:time.sleep(min(5,max(.1,e.until-time.time())))
        else:raise RuntimeError('Robots check deferred by existing host pacing')
        result['robots']={'allowed':allowed,'reason':reason}
        if not allowed:
            result.update(status=reason);save(attempt,result);return result
        for _ in range(15):
            try:gate.reserve(host);break
            except PacingDeferred as e:time.sleep(min(5,max(.1,e.until-time.time())))
        else:
            result.update(status='host_pacing_deferred');save(attempt,result);return result
        raw=OUT/'provider'/f'{ident}.json';raw.parent.mkdir(exist_ok=True)
        args=[sys.executable,str(ROOT/'scripts/judge_firecrawl_private.py'),'scrape',url,'--format','markdown,links,rawHtml','--only-main-content','--proxy','basic','--json','-o',str(raw)]
        result['command_without_credentials']=args[2:]
        save(attempt,result)
        try:
            proc=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=100)
            result['exit_code']=proc.returncode;result['diagnostic']=(proc.stderr+proc.stdout)[-2500:]
        finally:gate.release(host)
        if not raw.exists():
            result.update(status='provider_failed',finished_at=now());save(attempt,result);return result
        payload=json.loads(raw.read_text(encoding='utf-8'));data=payload.get('data',payload)
        meta=data.get('metadata',{})
        result.update(provider_raw_path=raw.relative_to(ROOT).as_posix(),provider_sha256=digest(raw),metadata=meta,finished_at=now())
        status=meta.get('statusCode')
        if status in [401,403,429] or not payload.get('success',True):
            result.update(status='provider_access_or_api_failure');save(attempt,result);return result
        text=data.get('markdown','')
        if len(text.strip())<250:
            result.update(status='provider_insufficient_text',text_characters=len(text));save(attempt,result);return result
        text_path=OUT/'text'/f'{ident}.md';text_path.parent.mkdir(exist_ok=True);text_path.write_text(text,encoding='utf-8')
        links_path=OUT/'links'/f'{ident}.json';save(links_path,{'observed_from':url,'provider_sha256':digest(raw),'links':data.get('links',[]),'saved_pages':False})
        result.update(status='saved_provider_capture',title=meta.get('title') or seed['title'],raw_path=raw.relative_to(ROOT).as_posix(),text_path=text_path.relative_to(ROOT).as_posix(),sha256=digest(raw),text_sha256=digest(text_path),captured_at=now(),text_characters=len(text),quality={'capture_type':'provider_response_not_original_http_bytes','publisher':'official_state_government','full_legal_text_correctness_verified':False,'currency_verified':False,'scope':seed.get('scope_note','Selected source only'),'source_http_status_reported_by_provider':status})
    except Exception as error:
        result.update(status='exception',error=str(error),finished_at=now())
    save(attempt,result);return result

if __name__=='__main__':
    if (OUT/'PILOT_CLOSED.json').exists():
        raise SystemExit('Pilot closed. Do not rerun: provider redirects require original-source preflight before any future independently reviewed collection.')
    seeds=json.loads((OUT/'selected_inputs.json').read_text(encoding='utf-8'))
    if len(seeds)>20:raise SystemExit('Refusing unreviewed expansion beyond twenty candidate pages')
    # The selected substantive pages now share one host. Serialize this pilot
    # while shared controls continue to coordinate all other collectors.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        for r in pool.map(collect,seeds):print(json.dumps({k:r.get(k) for k in ['id','source_url','status','text_characters','error']}),flush=True)
    rows=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((OUT/'attempts').glob('*.json'))]
    (OUT/'resources.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows if r['status']=='saved_provider_capture'),encoding='utf-8')
    save(OUT/'attempts_summary.json',{'observed_at':now(),'attempts':len(rows),'captures':sum(r['status']=='saved_provider_capture' for r in rows),'statuses':{s:sum(r['status']==s for r in rows) for s in sorted({r['status'] for r in rows})},'max_provider_parallel_jobs':2,'final_pass_provider_parallel_jobs':1,'provider_response_is_original_http_bytes':False})
