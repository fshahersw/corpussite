"""Durable public Firecrawl queue. API key remains in process environment only.
API contracts: https://docs.firecrawl.dev/api-reference/endpoint/scrape
and https://docs.firecrawl.dev/api-reference/endpoint/credit-usage .
"""
import argparse,concurrent.futures,contextlib,datetime,email.utils,hashlib,json,os,pathlib,re,shutil,sqlite3,ssl,time,urllib.request,urllib.error,urllib.parse
from corpus_crawler import canonical_url

BASE=pathlib.Path(__file__).resolve().parents[1]/'sources'/'trellis'
STATE=BASE/'worker';STATE.mkdir(exist_ok=True)
API='https://api.firecrawl.dev/v2/'
SCOPE_VERSION='2026-09-13-laws-judges-counties'

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    # A Windows reader can briefly deny replacement of an otherwise writable
    # checkpoint. Keep the old file intact and retry only the atomic rename.
    deadline=time.monotonic()+1
    while True:
        try:
            temp.replace(path)
            break
        except PermissionError:
            if os.name!='nt' or time.monotonic()>=deadline:raise
            time.sleep(0.05)
def category(url):
    p=urllib.parse.urlsplit(url);s=p.path.strip('/').split('/')
    if p.hostname!='trellis.law':return None
    if s[0]=='state-rules':return ('rules',10,s[1] if len(s)>1 else '')
    if s[0]=='judges':return ('judge_directory',20,s[1] if len(s)>1 else '')
    if s[0]=='judge' and len(s)==2:return ('judge_profile',21,'')
    if s[0]=='coverage' and not p.query:
        if len(s)==3:return ('county',30,s[1])
        if len(s)<=2:return ('coverage',30,s[1] if len(s)>1 else '')
    return None
def allowed(url):
    if not url:return False
    p=urllib.parse.urlsplit(url)
    if p.username or p.password or not category(url):return False
    if any(k in dict(urllib.parse.parse_qsl(p.query)) for k in ('sort','order','output','post_purchase_path')):return False
    # Pagination can enumerate judges; search/filter permutations are not part
    # of the user's narrowed law/judge/county-content request.
    if p.query and (not p.path.startswith('/judges') or any(k!='page' or not v.isdigit() for k,v in urllib.parse.parse_qsl(p.query))):return False
    return not re.search(r'/(?:upgrade|pricing|account|logout|login|request-|remove|delete|subscribe)(?:/|$)',p.path,re.I)
class NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if urllib.parse.urlsplit(newurl).hostname!='api.firecrawl.dev':raise RuntimeError('API redirect outside credential origin')
        return super().redirect_request(req,fp,code,msg,headers,newurl)
def api(path,key,payload=None):
    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
    req=urllib.request.Request(API+path,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
    opener=urllib.request.build_opener(NoCrossHostRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req,timeout=95) as res:return res.status,json.loads(res.read(80*1024*1024).decode())
    except urllib.error.HTTPError as exc:
        raw=exc.read(1024*1024).decode(errors='replace').replace(key,'[REDACTED]')
        try:body=json.loads(raw)
        except ValueError:body={'success':False,'error':raw[:1000]}
        body['_collector_transport']={'retry_after':exc.headers.get('Retry-After')}
        return exc.code,body

def retry_delay(body,at=None):
    at=time.time() if at is None else at
    delays=[60.0]
    header=(body.get('_collector_transport') or {}).get('retry_after')
    if header:
        try:delays.append(float(header))
        except (TypeError,ValueError):
            try:delays.append(email.utils.parsedate_to_datetime(header).timestamp()-at)
            except (TypeError,ValueError,OverflowError):pass
    error=str(body.get('error',''))
    after=re.search(r'retry after\s+(\d+(?:\.\d+)?)s',error,re.I)
    if after:delays.append(float(after.group(1)))
    reset=re.search(r'resets at\s+(.+?GMT[+-]\d{4})',error,re.I)
    if reset:
        try:delays.append(datetime.datetime.strptime(reset.group(1),'%a %b %d %Y %H:%M:%S GMT%z').timestamp()-at)
        except ValueError:pass
    return max(delays)+1

class Queue:
    def __init__(self,path):
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS frontier(url TEXT PRIMARY KEY,category TEXT,priority INTEGER,state TEXT,discovered_from TEXT,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,response_path TEXT,error TEXT);
        CREATE INDEX IF NOT EXISTS queue_order ON frontier(status,priority,state);
        CREATE TABLE IF NOT EXISTS edges(source_url TEXT,url TEXT,PRIMARY KEY(source_url,url));
        CREATE TABLE IF NOT EXISTS imported(path TEXT PRIMARY KEY,mtime REAL);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,url TEXT,attempted_at TEXT,api_status INTEGER,target_status INTEGER,result_status TEXT,credits_used INTEGER,response_path TEXT);
        ''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(frontier)')}
        if 'in_scope' not in columns:self.db.execute('ALTER TABLE frontier ADD COLUMN in_scope INTEGER NOT NULL DEFAULT 1')
        if 'scope_reason' not in columns:self.db.execute('ALTER TABLE frontier ADD COLUMN scope_reason TEXT')
    def reconcile_scope(self):
        changes=[]
        for row in self.db.execute('SELECT url,category,priority,state,status FROM frontier'):
            if allowed(row['url']):
                cat,priority,state=category(row['url'])
                changes.append((1,None,cat,priority,state or row['state'],'pending' if row['status']=='deferred_scope' else row['status'],row['url']))
            else:
                changes.append((0,'Excluded by user scope '+SCOPE_VERSION,row['category'],row['priority'],row['state'],'deferred_scope' if row['status'] in ('pending','fetching') else row['status'],row['url']))
        self.db.executemany('UPDATE frontier SET in_scope=?,scope_reason=?,category=?,priority=?,state=?,status=? WHERE url=?',changes)
        self.db.commit()
    def add(self,url,source='',state=''):
        url=canonical_url(url)
        if not allowed(url):return
        if source:self.db.execute('INSERT OR IGNORE INTO edges VALUES(?,?)',(source,url))
        cat,priority,own_state=category(url)
        self.db.execute('INSERT OR IGNORE INTO frontier(url,category,priority,state,discovered_from) VALUES(?,?,?,?,?)',(url,cat,priority,own_state or state,source))
    def import_data(self,data,path):
        meta=data.get('metadata',{});url=canonical_url(meta.get('sourceURL') or meta.get('url',''))
        if not allowed(url):return
        self.add(url)
        status='downloaded' if 200<=int(meta.get('statusCode') or 0)<300 and (data.get('markdown') or data.get('html')) else 'provider_error'
        cat=category(url);state=cat[2] if cat else ''
        self.db.execute('UPDATE frontier SET status=?,response_path=? WHERE url=?',(status,str(path),url))
        for link in data.get('links',[]):
            if isinstance(link,str):self.add(link,url,state)
    def import_saved(self):
        self.db.execute("UPDATE frontier SET status='pending' WHERE status='fetching'")
        for f in BASE.rglob('*.firecrawl.json'):
            old=self.db.execute('SELECT mtime FROM imported WHERE path=?',(str(f),)).fetchone()
            if old and old[0]==f.stat().st_mtime:continue
            try:data=json.loads(f.read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            self.import_data(data,f)
            self.db.execute('INSERT OR REPLACE INTO imported VALUES(?,?)',(str(f),f.stat().st_mtime))
        path=BASE/'discovery'/'county_urls.json'
        if path.exists():
            for row in json.loads(path.read_text(encoding='utf-8')):self.add(row['url'],row.get('discovered_from',''))
        self.db.commit()
        self.reconcile_scope()
    def claim(self):
        priorities=self.db.execute("SELECT min(priority) FROM frontier WHERE status IN ('pending','fetching') AND in_scope=1").fetchone()[0]
        if priorities is None:return None
        states=self.db.execute("SELECT state FROM frontier WHERE status='pending' AND in_scope=1 AND priority=? GROUP BY state",(priorities,)).fetchall()
        if not states:return None
        counts={r[0]:r[1] for r in self.db.execute("SELECT state,count(*) FROM frontier WHERE status='downloaded' AND in_scope=1 AND priority=? GROUP BY state",(priorities,))}
        state=min((r[0] for r in states),key=lambda s:(counts.get(s,0),s))
        row=self.db.execute("SELECT * FROM frontier WHERE status='pending' AND in_scope=1 AND priority=? AND state=? ORDER BY length(url),url LIMIT 1",(priorities,state)).fetchone()
        self.db.execute("UPDATE frontier SET status='fetching',attempts=attempts+1 WHERE url=?",(row['url'],));self.db.commit();return dict(row)
    def summary(self,reason,credits,run_used):
        data={'updated_at':now(),'pid':os.getpid(),'status':reason,'full_requested_corpus_complete':False,'remaining_credits_last_observed':credits,'run_credits_used':run_used,
              'scope_version':SCOPE_VERSION,'phase_order':['rules','judge_directory','judge_profile','county'],
              'counts':{r[0]:r[1] for r in self.db.execute('SELECT status,count(*) FROM frontier WHERE in_scope=1 GROUP BY status')},
              'pending_by_category':{r[0]:r[1] for r in self.db.execute("SELECT category,count(*) FROM frontier WHERE status='pending' AND in_scope=1 GROUP BY category")},
              'archived_out_of_scope_counts':{r[0]:r[1] for r in self.db.execute('SELECT status,count(*) FROM frontier WHERE in_scope=0 GROUP BY status')},
              'active_phase':next((r[0] for r in self.db.execute("SELECT category FROM frontier WHERE status IN ('pending','fetching','batch_held') AND in_scope=1 ORDER BY priority LIMIT 1")),None),
              'cooldown_until_epoch':getattr(self,'cooldown_until',0),
              'public_only':True,'authenticated_pdfs_included':False}
        save(STATE/'status.json',data);print(json.dumps(data),flush=True);return data

def run(args):
    if (STATE/'batch_active.json').exists():
        raise RuntimeError('A remote or uncertain batch is recorded; resolve batch_active.json before starting the single-page worker')
    key=os.environ.get('FIRECRAWL_API_KEY','')
    if not key:raise RuntimeError('FIRECRAWL_API_KEY is missing')
    q=Queue(STATE/'frontier.sqlite3');q.import_saved()
    status,credit=api('team/credit-usage',key)
    save(STATE/'credit_preflight.json',{'checked_at':now(),'api_status':status,'response':credit})
    credits=credit.get('data',{}).get('remainingCredits') if status==200 else None
    if not isinstance(credits,(int,float)) or credits<=0:
        q.summary('paused_credit_or_auth',credits,0);return
    completed=0;used=0;reason='running';fatal=False
    previous=json.loads((STATE/'status.json').read_text()) if (STATE/'status.json').exists() else {}
    q.cooldown_until=float(previous.get('cooldown_until_epoch') or 0)
    q.summary(reason,credits,used)
    workers=max(1,min(2,args.workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        active={}
        while True:
            if shutil.disk_usage(BASE).free<10*1024**3:reason='paused_disk_guard';fatal=True
            if (STATE/'STOP').exists():reason='operator_checkpoint';fatal=True
            if reason=='rate_limit_cooldown' and time.time()>=q.cooldown_until:reason='running'
            while not fatal and time.time()>=q.cooldown_until and len(active)<workers and credits-len(active)>1 and (args.pages==0 or completed+len(active)<args.pages):
                row=q.claim()
                if not row:break
                future=pool.submit(api,'scrape',key,{'url':row['url'],'formats':['markdown','links','html'],'proxy':'basic','maxAge':0,'timeout':60000,'skipTlsVerification':False})
                active[future]=row
                # This key's verified allowance is ten scrape requests/minute.
                time.sleep(6.5)
            if not active:
                if not fatal and time.time()<q.cooldown_until:
                    q.summary('rate_limit_cooldown',credits,used)
                    time.sleep(min(20,q.cooldown_until-time.time()));continue
                if reason=='running':reason='paused_credit_reserve' if credits<=1 else 'batch_complete' if args.pages and completed>=args.pages else 'frontier_exhausted_or_failed'
                break
            done,_=concurrent.futures.wait(active,timeout=20,return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                row=active.pop(fut);url=row['url'];stamp=now();ident=hashlib.sha256(url.encode()).hexdigest()
                try:api_status,body=fut.result()
                except Exception as exc:api_status,body=0,{'success':False,'error':str(exc).replace(key,'[REDACTED]')[:1000]}
                rawfile=STATE/'responses'/f'{ident}_{int(time.time()*1000)}.json'
                save(rawfile,json.loads(json.dumps(body).replace(key,'[REDACTED]')))
                data=body.get('data') or {}
                if not isinstance(data,dict):data={}
                meta=data.get('metadata') or {};target=int(meta.get('statusCode') or 0)
                cost=int(meta.get('creditsUsed') or (1 if body.get('success') else 0));used+=cost;credits-=cost
                challenge=re.search(r'just a moment|attention required|access denied',str(meta.get('title','')),re.I) or re.search(r'verify you are human|checking your browser',str(data.get('markdown',''))[:1500],re.I)
                good=api_status==200 and body.get('success') and 200<=target<300 and bool(data.get('markdown') or data.get('html')) and not challenge
                if good:
                    data.setdefault('metadata',{}).setdefault('sourceURL',url)
                    parts=urllib.parse.urlsplit(url).path.strip('/').split('/')
                    folder='counties' if row['category']=='county' else 'rules' if row['category']=='rules' else 'judges' if row['category'].startswith('judge') else row['category']
                    name='_'.join(parts[1:]) if row['category']=='county' and not urllib.parse.urlsplit(url).query else ident
                    output=BASE/folder/(name+'.firecrawl.json');save(output,data)
                    q.import_data(data,output);result='downloaded'
                    q.db.execute("UPDATE frontier SET status='downloaded',response_path=?,error=NULL WHERE url=?",(str(output),url))
                else:
                    error=str(body.get('error','Target HTTP '+str(target)))[:1000]
                    result='access_blocked' if target in (401,403) or challenge else 'provider_error'
                    if api_status==429:
                        q.cooldown_until=time.time()+retry_delay(body)
                        reason='rate_limit_cooldown';result='pending'
                    elif api_status in (401,402) or re.search(r'credit|quota|payment',error,re.I):
                        reason='paused_provider_limit';fatal=True;result='pending'
                    if target in (401,403) or challenge:reason='paused_target_access';fatal=True
                    if not fatal and api_status in (0,500,502,503,504) and row['attempts']<2:
                        result='pending';time.sleep(15*(row['attempts']+1))
                    q.db.execute('UPDATE frontier SET status=?,response_path=?,error=? WHERE url=?',(result,str(rawfile),error,url))
                q.db.execute('INSERT INTO attempts(url,attempted_at,api_status,target_status,result_status,credits_used,response_path) VALUES(?,?,?,?,?,?,?)',(url,stamp,api_status,target,result,cost,str(rawfile)))
                q.db.commit();completed+=1
                if completed%20==0 and not fatal:
                    cs,cr=api('team/credit-usage',key);remaining=cr.get('data',{}).get('remainingCredits') if cs==200 else None
                    if isinstance(remaining,(int,float)):credits=remaining
                    else:reason='paused_credit_check_failed';fatal=True
                if completed%10==0:q.summary(reason,credits,used)
            if not done:q.summary(reason,credits,used)
    q.summary(reason,credits,used);q.db.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pages',type=int,default=0);p.add_argument('--workers',type=int,default=2);a=p.parse_args()
    import msvcrt
    with (STATE/'worker.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise SystemExit('A Firecrawl worker is already active')
        try:run(a)
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
