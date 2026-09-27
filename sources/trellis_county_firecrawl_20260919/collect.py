"""Finite, resumable basic-provider county-profile collection. No discovery or identity joins.

Uses the existing user-bound private Firecrawl wrapper's runtime/key loader; the key
is present only in memory and the child environment. Every URL gets one attempt.
"""
from __future__ import annotations
import concurrent.futures as cf
import contextlib
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
QUEUE = ROOT / 'reports/trellis_refocus_20260919/county_audit/remaining_observed_county_urls.jsonl'
NEXT10 = QUEUE.with_name('next_10_observed_urls.jsonl')
MAX_JOBS = 5
BUDGET = 1500
RESERVE = 3000
FALSE_URL = 'https://trellis.law/coverage/texas/harrisoncountytexas.org'
REPRESENTATION = 'Firecrawl selected rendered county profile section; provider response is not original HTTP bytes.'

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path): return path.relative_to(ROOT).as_posix()
def readl(path): return [json.loads(x) for x in path.read_text(encoding='utf-8-sig').splitlines() if x.strip()] if path.exists() else []
def save(path, value):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temp, path)
def savel(path, values):
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in values), encoding='utf-8')
    os.replace(temp, path)

@contextlib.contextmanager
def writer_lock():
    import msvcrt
    with (HERE/'collector.lock').open('a+b') as handle:
        if handle.tell() == 0: handle.write(b'0'); handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try: yield
        finally: handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

class ProfileParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=None; self.parts=[]; self.heading=''; self.label=''; self.fields={}; self.website=None
    def handle_starttag(self,tag,attrs):
        if tag in {'h1','h2','h3'}: self.active=tag; self.parts=[]
        if tag=='br' and self.active: self.parts.append(' ')
        if tag=='a' and self.active=='h3' and self.label.lower()=='website':
            href=dict(attrs).get('href',''); p=urlsplit(href)
            if p.scheme in {'http','https'} and p.hostname and not p.username and not p.password: self.website=href
    def handle_data(self,data):
        if self.active: self.parts.append(data)
    def handle_endtag(self,tag):
        if tag != self.active: return
        text=' '.join(' '.join(self.parts).split())
        if tag=='h1': self.heading=text
        elif tag=='h2': self.label=text
        elif tag=='h3' and self.label: self.fields.setdefault(re.sub(r'\W+','_',self.label.lower()).strip('_'),text)
        self.active=None; self.parts=[]

def normalize(path, expected, captured, queue_sha, proof=None):
    obj=json.loads(path.read_text(encoding='utf-8-sig')); m=obj.get('metadata') or {}
    status=m.get('statusCode'); html=obj.get('html') or ''; markdown=obj.get('markdown') or ''
    if status in {401,403,429}: raise RuntimeError('access_barrier_'+str(status))
    if status!=200: raise ValueError('provider_http_'+str(status))
    if m.get('proxyUsed')!='basic': raise RuntimeError('unexpected_proxy')
    if m.get('creditsUsed') not in (0,1): raise RuntimeError('unexpected_response_credit_cost')
    if m.get('sourceURL')!=expected or str(m.get('url','')).rstrip('/')!=expected.rstrip('/'):
        raise ValueError('redirect_or_url_mismatch')
    if re.search(r'verify you are human|checking your browser|access denied|just a moment|captcha', markdown, re.I):
        raise RuntimeError('access_challenge')
    parser=ProfileParser(); parser.feed(html)
    if not parser.heading or not re.search(r'\bCourts Records$',parser.heading,re.I) or 'top-county-info-block__container' not in html:
        raise ValueError('no_county_profile_heading')
    if len(markdown)<100 or len(parser.fields)<2: raise ValueError('incomplete_county_info_section')
    ident=hashlib.sha256(expected.encode()).hexdigest()[:24]
    text=HERE/'text'/f'{ident}.md'; htmlpath=HERE/'html'/f'{ident}.html'
    text.write_text(markdown,encoding='utf-8');htmlpath.write_text(html,encoding='utf-8')
    bits=urlsplit(expected).path.strip('/').split('/')
    return {'id':'trellis-county-fc:'+ident,'source_url':expected,'url':expected,'state_slug':bits[1],'county_slug':bits[2],
        'title':parser.heading,'heading':parser.heading,'fields':parser.fields,'website_url':parser.website,
        'raw_path':rel(path),'raw_sha256':sha(path),'raw_bytes':path.stat().st_size,
        'text_path':rel(text),'text_sha256':sha(text),'html_path':rel(htmlpath),'html_sha256':sha(htmlpath),
        'captured_at':captured,'captured_at_basis':'Local acquisition completion; provider may serve a cached snapshot.',
        'provider':{'scrape_id':m.get('scrapeId'),'proxy_used':m.get('proxyUsed'),'cache_state':m.get('cacheState'),
                    'credits_used':m.get('creditsUsed'),'status_code':status},
        'county_geoid':None,'county_geoid_basis':'Unresolved: use explicit heading/state and source-backed crosswalk, not slug alone.',
        'source_queue_path':rel(QUEUE),'source_queue_sha256':queue_sha,'observed_parent':proof,
        'representation':REPRESENTATION,'quality':'Selected county information section; no filings, dockets or judge analyses acquired.'}

def shared_hold():
    p=ROOT/'corpus/_shared_hosts/hosts.sqlite3'
    if not p.exists(): return None
    c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        for row in c.execute("select * from host_state where host in ('trellis.law','api.firecrawl.dev')"):
            if row['pause_reason'] or row['cooldown_until']>time.time(): return dict(row)
    finally: c.close()
    return None

def credit_status(key):
    from firecrawl_batch_worker import Client
    status,body=Client(key).call('GET','team/credit-usage')
    data=body.get('data',{}) if isinstance(body,dict) else {}
    receipt={'checked_at':now(),'http_status':status,'remaining_credits':data.get('remainingCredits'),'total_credits':data.get('totalCredits')}
    with (HERE/'credit_receipts.jsonl').open('a',encoding='utf-8') as h:h.write(json.dumps(receipt)+'\n')
    if status!=200 or not isinstance(receipt['remaining_credits'],int): raise RuntimeError('credit_or_auth_status_failed')
    return receipt

def checkpoint(resources,attempts,initial,current,selected,reason,complete=False):
    save(HERE/'validation.json',{'ready':False,'status':'updating','validated_at':now()})
    savel(HERE/'resources.jsonl',sorted(resources.values(),key=lambda x:x['source_url']))
    savel(HERE/'attempts.jsonl',attempts)
    failures=[x for x in attempts if x['status']!='saved']
    savel(HERE/'failures.jsonl',failures)
    progress={'updated_at':now(),'status':reason,'selected_worker_urls':selected,'attempted':len(attempts),'accepted_profiles':len(resources),
              'worker_successes':sum(x['status']=='saved' for x in attempts),'failures':len(failures),
              'credits_at_start':initial,'remaining_credits':current,'account_credit_delta':None if current is None else initial-current,
              'budget':BUDGET,'reserve':RESERVE,'concurrency':MAX_JOBS,'complete':complete,'pid':os.getpid()}
    save(HERE/'progress.json',progress)
    save(HERE/'validation.json',{'ready':True,'status':'passed','manifest_path':'resources.jsonl','manifest_sha256':sha(HERE/'resources.jsonl'),
         'records':len(resources),'validated_at':now(),'complete':complete,'scope':'Only accepted exact-URL county profile sections, each with raw/text/HTML hashes.',
         'provider_representation':True,'qualification':REPRESENTATION+' County GEOIDs unresolved; profile fields are publisher claims.'})
    print(json.dumps(progress),flush=True)

def main():
    for name in ('raw','text','html','logs'): (HERE/name).mkdir(exist_ok=True)
    sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'pipeline'))
    import judge_firecrawl_private as private
    key=private.private_key(private.CREDENTIAL)
    env={**os.environ,'FIRECRAWL_API_KEY':key,'FIRECRAWL_NO_ENDPOINT_FEEDBACK':'1'}
    env['NODE_OPTIONS']=(env.get('NODE_OPTIONS','')+' --use-system-ca').strip()
    queue_sha=sha(QUEUE); rows=readl(QUEUE); exclude={x['url'] for x in readl(NEXT10)}|{FALSE_URL}
    c=sqlite3.connect((ROOT/'sources/trellis/worker/frontier.sqlite3').as_uri()+'?mode=ro',uri=True)
    try: exclude.update(x[0] for x in c.execute("select url from frontier where status='access_blocked' or error like '%403%' or error like '%429%'") )
    finally:c.close()
    proofs={}
    c=sqlite3.connect((ROOT/'sources/trellis/catalog/catalog.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        for row in c.execute("select source_url,url,label from links where category='coverage_county'"):
            proofs.setdefault(row['url'],dict(row))
    finally:c.close()
    resources={x['source_url']:x for x in readl(HERE/'resources.jsonl')};attempts=readl(HERE/'attempts.jsonl')
    # Existing raw successes, including root's five pilot captures, are normalized without a request.
    for path in sorted((HERE/'raw').glob('*.json')):
        try:
            obj=json.loads(path.read_text(encoding='utf-8-sig'));url=(obj.get('metadata')or{}).get('sourceURL','')
            if not re.fullmatch(r'https://trellis\.law/coverage/[^/]+/[^/?#]+',url) or url in resources:continue
            resources[url]=normalize(path,url,dt.datetime.fromtimestamp(path.stat().st_mtime,dt.timezone.utc).isoformat(),queue_sha,proofs.get(url))
            resources[url]['captured_at_basis']='Existing capture file modification time; root pilot acquisition receipt, not publisher update time.'
        except (ValueError,RuntimeError,TypeError):pass
    prior_starts={x['url']:x for x in readl(HERE/'started.jsonl')}
    known_attempts={a['url'] for a in attempts}
    for url,start in prior_starts.items():
        if url not in known_attempts and url not in resources:
            attempts.append({**start,'status':'failed','reason':'prior_interrupted_attempt_not_retried'})
    pending=[x for x in rows if x['url'] not in exclude and x['url'] not in resources and x['url'] not in {a['url'] for a in attempts}]
    # Validate all selected URLs against the already-observed catalog; no inferred links.
    assert all(x['url'] in proofs and re.fullmatch(r'https://trellis\.law/coverage/[^/]+/[^/?#]+',x['url']) for x in pending)
    savel(HERE/'selection.jsonl',pending)
    prior=readl(HERE/'credit_receipts.jsonl')
    current=credit_status(key)['remaining_credits'];initial=prior[0]['remaining_credits'] if prior else current
    selected=len(pending)+len(attempts);checkpoint(resources,attempts,initial,current,selected,'running')
    def acquire(row):
        url=row['url'];ident=hashlib.sha256(url.encode()).hexdigest()[:24];path=HERE/'raw'/f'{ident}.json';started=now()
        command=[str(private.NODE),str(private.CLI),'scrape',url,'--format','markdown,html,links','--include-tags','.top-county-info-block__container','--proxy','basic','--max-age','172800000','-o',str(path),'--json']
        entry={'url':url,'started_at':started,'status':'failed','raw_path':rel(path)}
        try:
            proc=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=100)
            log=re.sub(r'fc-[a-fA-F0-9]{20,}','[REDACTED]',(proc.stdout+'\n'+proc.stderr).replace(key,'[REDACTED]'))
            (HERE/'logs'/f'{ident}.txt').write_text(log,encoding='utf-8')
            entry['returncode']=proc.returncode
            if proc.returncode:
                if re.search(r'403|429|401|captcha|credit|payment|rate.limit|access.denied',log,re.I):raise RuntimeError('provider_access_auth_credit_or_rate_barrier')
                raise ValueError('provider_cli_failed')
            result=normalize(path,url,now(),queue_sha,proofs.get(url));entry.update(status='saved',credits_used=result['provider']['credits_used'],id=result['id'])
            return entry,result,False
        except subprocess.TimeoutExpired:
            entry['reason']='single_attempt_timed_out';return entry,None,True
        except (RuntimeError,ValueError,OSError) as exc:
            entry['reason']=str(exc)[:200];return entry,None,isinstance(exc,RuntimeError)
        finally:entry['completed_at']=now()
    reason='completed';complete=True
    with cf.ThreadPoolExecutor(max_workers=MAX_JOBS) as pool:
        for offset in range(0,len(pending),MAX_JOBS):
            batch=pending[offset:offset+MAX_JOBS]
            if shared_hold():reason='stopped_shared_host_hold';complete=False;break
            if initial-current+len(batch)>BUDGET or current-len(batch)<RESERVE:reason='stopped_credit_budget_or_reserve';complete=False;break
            halted=False;before=current
            # Register starts before calls so interruption never retries an uncertain attempt.
            starts=[{'url':x['url'],'started_at':now(),'status':'inflight'} for x in batch]
            with (HERE/'started.jsonl').open('a',encoding='utf-8') as h:
                for x in starts:h.write(json.dumps(x)+'\n')
            for entry,result,stop in pool.map(acquire,batch):
                attempts.append(entry);halted|=stop
                if result:resources[result['source_url']]=result
            try:current=credit_status(key)['remaining_credits']
            except Exception:reason='stopped_credit_status_unknown';current=None;complete=False;checkpoint(resources,attempts,initial,current,selected,reason);break
            if before-current>len(batch):halted=True;reason='stopped_unexpected_account_credit_delta'
            if halted:
                reason=reason if reason!='completed' else 'stopped_access_or_transport_barrier';complete=False
            checkpoint(resources,attempts,initial,current,selected,reason if halted else 'running')
            if halted:break
    if complete and any(x['status']!='saved' for x in attempts):
        reason='completed_finite_queue_with_gaps';complete=False
    checkpoint(resources,attempts,initial,current,selected,reason,complete)

if __name__=='__main__':
    with writer_lock():main()
