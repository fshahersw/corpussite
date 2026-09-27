"""Bounded official litigation-resource collector; no paid work on import.

Preserves provider captures separately from original HTTP document bytes. All
network requests obey the shared corpus host gate and robots policy. The queue
is durable: an uncertain submitted scrape is never silently submitted again.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from lxml import html as html_parser

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT/'pipeline'), str(ROOT/'scripts')]
import corpus_crawler as crawler
from firecrawl_batch_worker import worker_lock, Client as CreditClient
from judge_firecrawl_private import CREDENTIAL, private_key

DOCUMENT = re.compile(r'\.(pdf|docx?|rtf|odt|xlsx?|csv|txt|zip)$', re.I)
DIRECT_DOWNLOAD = re.compile(r'/(?:download_file/|DocumentCenter/(?:View|Download)/|DocumentView\.aspx)',re.I)
EXCLUDE = re.compile(r'(?:^|/)(?:news|newsroom|press|events?|calendar|careers?|jobs|procurement|bids|elections|parks|tourism|social-media|video|videos|community-engagement|court-tours|peer-court)(?:/|$)', re.I)
TOPIC = re.compile(r'\b(?:local rules?|rules? of court|forms?|standing orders?|administrative orders?|general orders?|filing|e-filing|efiling|service of process|fee schedules?|court fees?|clerk|courts?|judges?|judicial|litigation|civil|probate|family|small claims|self.help|legal|jury|juror|case access|case information|records|docket)\b', re.I)
BADPAGE = re.compile(r'^(?:404(?:\s|:)|page not found|access denied|forbidden|just a moment|verify you are human|attention required)', re.I)

def now(): return datetime.now(timezone.utc).isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def rel(path): return str(Path(path).resolve().relative_to(ROOT)).replace('\\','/')
def rows(path): return [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(raw);temp.replace(path)
def save_rows(path, values):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in values).encode()
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(raw);temp.replace(path)

def safe_url(value):
    url=crawler.canonical_url(value)
    if not url: raise ValueError('Invalid HTTP(S) URL')
    p=urllib.parse.urlsplit(url)
    if p.scheme not in {'https','http'} or p.port not in (None,80,443): raise ValueError('Only observed public HTTP(S) URLs are allowed')
    if p.hostname in {'localhost','127.0.0.1','::1'} or re.fullmatch(r'[\d.]+',p.hostname or ''):
        raise ValueError('Numeric/local host excluded')
    if any(crawler.ACTION_SEGMENT.fullmatch(s) for s in urllib.parse.unquote(p.path).replace('\\','/').split('/')):
        raise ValueError('Interactive account/action URL excluded')
    for key,value in urllib.parse.parse_qsl(p.query,keep_blank_values=True):
        if key.lower() in {'action','do','method','operation'} and re.search(r'delete|remove|create|edit|update|submit|upload|logout|login|purchase|checkout',value,re.I):
            raise ValueError('Action query excluded')
    return url

def seed_ok(seed):
    url=safe_url(seed['url'])
    if not isinstance(seed.get('county_geoids'),list) or not seed['county_geoids'] or not all(re.fullmatch(r'\d{5}',x) for x in seed['county_geoids']):
        raise ValueError('Explicit county GEOID provenance required')
    if not isinstance(seed.get('association'),dict) or not seed.get('source_authority'):
        raise ValueError('Source authority and association evidence required')
    if int(seed.get('depth',0))>2: raise ValueError('Depth exceeds bounded pilot')
    hosts=seed.get('allowed_hosts') or [crawler.host_of(url)]
    if crawler.host_of(url) not in hosts: raise ValueError('Seed outside reviewed host set')
    return url

def link_allowed(url, text, seed):
    try: url=safe_url(url)
    except (ValueError,KeyError): return False
    p=urllib.parse.urlsplit(url)
    if crawler.host_of(url) not in (seed.get('allowed_hosts') or [crawler.host_of(seed['url'])]): return False
    if EXCLUDE.search(p.path): return False
    if any(crawler.ACTION_SEGMENT.fullmatch(s) for s in urllib.parse.unquote(p.path).split('/')): return False
    if Path(p.path).suffix.lower() in crawler.ASSET_SUFFIXES: return False
    return bool(TOPIC.search(text+' '+urllib.parse.unquote(p.path).replace('-',' ').replace('_',' ')))

def quality(data,url,allowed_hosts):
    if not isinstance(data,dict): raise ValueError('Provider document missing')
    meta=data.get('metadata') or {}; title=str(meta.get('title') or '')
    final=safe_url(meta.get('url') or meta.get('sourceURL') or url)
    if crawler.host_of(final) not in allowed_hosts: raise ValueError('Unreviewed final redirect host')
    status=meta.get('statusCode')
    if status is not None and (not isinstance(status,int) or not 200<=status<300):
        raise ValueError('Source HTTP status '+str(status))
    markdown=str(data.get('markdown') or '').strip()
    html=str(data.get('rawHtml') or data.get('html') or '')
    if len(markdown)<80 or not html.strip(): raise ValueError('Empty/insufficient text or HTML')
    first=re.sub(r'^[#\s*]+','',markdown[:500]).strip()
    if BADPAGE.search(title) or BADPAGE.search(first): raise ValueError('Error/challenge page')
    if re.search(r'(?:verify you are human|checking your browser|enable javascript and cookies to continue)',markdown[:1800],re.I):
        raise ValueError('Access challenge text')
    return title, final, markdown, html, meta

class Runner:
    def __init__(self, request_cap, max_pages, max_seconds, workers=5, auto_discover=False, max_frontier=5000, per_county=250):
        if not 0<=request_cap<=5000 or not 1<=workers<=5: raise ValueError('Unsupported run bound')
        HERE.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(HERE/'queue.sqlite3',timeout=30);self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY,seed_json TEXT NOT NULL,status TEXT NOT NULL,resource_json TEXT,error TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,url TEXT,admitted_at TEXT,finished_at TEXT,api_status INTEGER,result TEXT);
        CREATE TABLE IF NOT EXISTS run_log(id TEXT PRIMARY KEY,started_at TEXT,ended_at TEXT,summary_json TEXT);''')
        columns={r[1] for r in self.db.execute('pragma table_info(queue)')}
        if 'priority' not in columns:self.db.execute('alter table queue add column priority INTEGER NOT NULL DEFAULT 3')
        if 'county_key' not in columns:self.db.execute("alter table queue add column county_key TEXT NOT NULL DEFAULT ''")
        if 'discovered' not in columns:self.db.execute('alter table queue add column discovered INTEGER NOT NULL DEFAULT 0')
        self.db.execute('CREATE INDEX IF NOT EXISTS queue_status_priority ON queue(status,priority,county_key)')
        for old in self.db.execute("select url,seed_json from queue where county_key='' ").fetchall():
            seed=json.loads(old['seed_json']);self.db.execute('update queue set priority=?,county_key=? where url=?',(int(seed.get('priority',3)),seed['county_geoids'][0],old['url']))
        self.db.commit();self.cap=request_cap;self.max_pages=max_pages;self.max_seconds=max_seconds;self.workers=workers
        self.auto_discover=auto_discover;self.max_frontier=max_frontier;self.per_county=per_county;self.county_started={};self.last_checkpoint=time.monotonic()
        self.saved_aliases=set()
        for saved in self.db.execute("select resource_json from queue where status='downloaded'"):
            item=json.loads(saved[0]);self.saved_aliases.update([item['source_url'],item['final_url']])
        sys.path.insert(0,str(ROOT/'sources/county_litigation_20260919'))
        import classify
        self.classifier=classify
        self.existing_urls=set()
        catalog=ROOT/'delivery/archive-directory/directory.sqlite3'
        if catalog.is_file():
            with sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True) as published:
                self.existing_urls.update(r[0] for r in published.execute('select source_url from browse where source_url is not null'))
        self.stop=threading.Event();self.started=time.monotonic();self.key=None;self.submit_lock=threading.Lock()
        self.network_lock=threading.Lock();self.scrapes=0;self.credit_start=None;self.credit_end=None
        self.cfg=crawler.Config.from_dict({'allow':[{'host':'placeholder.invalid','path_prefixes':['/']}], 'workers':workers,'per_host_delay':2.0,'respect_robots':True,'shared_host_dir':'corpus/_shared_hosts','follow_links':False,'max_depth':2,'max_retries':0,'timeout_seconds':25,'max_transfer_seconds':90,'max_response_bytes':40*1024*1024,'pause_host_on_access_block':True})
        self.art=crawler.Artifacts(HERE/'http',self.cfg)
        self.gate=crawler.Gate(self.cfg,{},self.stop,self.art)
        self.fetcher=crawler.Fetcher(self.cfg,self.art,self.gate,self.stop)

    def ingest(self,path):
        manifest=Path(path).read_bytes();manifest_sha=sha(manifest)
        archived=HERE/'seed_inputs'/f'{manifest_sha}.jsonl';archived.parent.mkdir(exist_ok=True)
        if not archived.exists():archived.write_bytes(manifest)
        seeds=rows(path)
        for seed in seeds:
            url=seed_ok(seed)
            seed['input_manifest']={'path':rel(archived),'sha256':manifest_sha}
            status='already_published' if url in self.existing_urls else 'pending'
            self.db.execute('INSERT OR IGNORE INTO queue(url,seed_json,status,resource_json,error,created_at,updated_at,priority,county_key) VALUES(?,?,?,NULL,NULL,?,?,?,?)',(url,json.dumps(seed),status,now(),now(),int(seed.get('priority',3)),seed['county_geoids'][0]))
        self.db.commit();return len(seeds)

    def credits(self):
        status,body=CreditClient(self.key).call('GET','team/credit-usage')
        value=body.get('data',{}).get('remainingCredits')
        if status!=200 or type(value) not in (int,float) or value<0: raise RuntimeError('Credit/auth preflight unavailable')
        return int(value)

    def wait_policy(self,url):
        while not self.stop.is_set():
            if time.monotonic()-self.started>self.max_seconds: raise RuntimeError('Run deadline')
            try:
                allowed,why=self.fetcher.robots(url)
                if not allowed: raise ValueError(why)
                return
            except crawler.PacingDeferred as error: self.stop.wait(min(1,max(.05,error.until-time.time())))
        raise RuntimeError('Run stopped')

    def reserve(self,host):
        while not self.stop.is_set():
            try: self.gate.reserve(host);return
            except crawler.PacingDeferred as error: self.stop.wait(min(1,max(.05,error.until-time.time())))
        raise RuntimeError('Run stopped')

    def scrape(self,seed,request_id):
        url=seed['url'];host=crawler.host_of(url)
        self.wait_policy(url);self.reserve(host)
        response_path=HERE/'.firecrawl'/f'{request_id}.json'; response_path.parent.mkdir(parents=True,exist_ok=True)
        payload={'url':url,'formats':['markdown','html','rawHtml','links'],'onlyMainContent':True,'maxAge':0,'timeout':60000,'proxy':'basic','parsers':[]}
        request=urllib.request.Request('https://api.firecrawl.dev/v2/scrape',data=json.dumps(payload).encode(),method='POST',headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        api_status=None
        try:
            opener=urllib.request.build_opener(crawler.NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            try:
                with opener.open(request,timeout=80) as response:
                    api_status=response.status;raw=response.read(40*1024*1024+1)
            except urllib.error.HTTPError as error:
                api_status=error.code;raw=error.read(1024*1024)
            if len(raw)>40*1024*1024: raise ValueError('Provider payload exceeds40MB')
            raw=raw.replace(self.key.encode(),b'[REDACTED]')
            response_path.write_bytes(raw)
            body=json.loads(raw)
            if api_status!=200 or body.get('success') is not True:
                if api_status in (401,402,429): self.stop.set()
                raise ValueError('Firecrawl API '+str(api_status)+': '+str(body.get('error') or 'unsuccessful')[:350])
            title,final,markdown,html,meta=quality(body.get('data'),url,seed.get('allowed_hosts') or [host])
            stamp=now();identity='county-litigation:'+sha(url.encode())[:24]
            text_path=HERE/'text'/f'{sha(markdown.encode())}.md';text_path.parent.mkdir(exist_ok=True);text_path.write_text(markdown,encoding='utf-8',newline='')
            html_path=HERE/'html'/f'{sha(html.encode())}.html';html_path.parent.mkdir(exist_ok=True);html_path.write_text(html,encoding='utf-8',newline='')
            record={'id':identity,'source_url':url,'final_url':final,'title':title or seed.get('anchor_text') or url,'kind':seed.get('resource_type','court_information'),'captured_at':stamp,'raw_path':rel(response_path),'raw_sha256':sha(raw),'html_path':rel(html_path),'html_sha256':sha(html.encode()),'text_path':rel(text_path),'text_sha256':sha(markdown.encode()),'mime_type':'application/json','text_mime_type':'text/markdown','capture_kind':'firecrawl_provider_capture','original_http_bytes':False,'provider_status':api_status,'source_http_status':meta.get('statusCode'),'provider_metadata':meta,'source_published_at':None,'effective_at':None,'source_as_of':None,'county_geoids':seed['county_geoids'],'state':seed.get('state'),'county':seed.get('county'),'association':seed['association'],'applicability':seed.get('applicability'),'source_authority':seed['source_authority'],'seed_provenance':seed,'links':body['data'].get('links',[]),'quality':{'complete_legal_corpus':False,'text_characters':len(markdown),'reading_method':'Firecrawl main-content Markdown','date_inference':False}}
            return {'status':'downloaded','resource':record,'api_status':api_status}
        except Exception as error:
            if api_status==200 and re.search(r'Source HTTP status (?:401|403)|challenge|access.denied|login.required',str(error),re.I):
                self.gate.update(host,reason='source_access_barrier')
            return {'status':'failed' if api_status is not None else 'uncertain_or_policy_failure','error':str(error)[:600].replace(self.key,'[REDACTED]'),'api_status':api_status,'raw_path':rel(response_path) if response_path.exists() else None}
        finally: self.gate.release(host)

    def document(self,seed):
        url=seed['url'];record=None
        while not self.stop.is_set():
            record=self.fetcher.fetch({'url':url,'retry_count':0})
            if record['status']!='pacing_deferred': break
            self.stop.wait(min(1,max(.05,record['next_attempt_at']-time.time())))
        if not record: return {'status':'interrupted','error':'Run stopped'}
        receipt=HERE/'http'/'metadata'/f'{sha(url.encode())}.json';save(receipt,record)
        if record['status']!='downloaded': return {'status':record['status'],'error':record.get('error'),'access_receipt_path':rel(receipt)}
        rawpath=HERE/'http'/record['raw_path'];textpath=HERE/'http'/record['text_path'] if record.get('text_path') else None
        raw=rawpath.read_bytes();text=textpath.read_text(encoding='utf-8') if textpath else ''
        if DOCUMENT.search(urllib.parse.urlsplit(url).path) and raw[:5]!=b'%PDF-' and raw.lstrip()[:30].lower().startswith((b'<!doctype html',b'<html')):
            return {'status':'failed','error':'Document URL returned HTML; no automatic provider reroute'}
        item={'id':'county-litigation:'+sha(url.encode())[:24],'source_url':url,'final_url':url,'title':record.get('title') or seed.get('anchor_text') or url,'kind':seed.get('resource_type','court_document'),'captured_at':record['fetched_at'],'raw_path':rel(rawpath),'raw_sha256':sha(raw),'text_path':rel(textpath) if textpath else None,'text_sha256':sha(textpath.read_bytes()) if textpath else None,'mime_type':record.get('headers',{}).get('content-type','application/octet-stream').split(';')[0],'capture_kind':'official_http_original','original_http_bytes':True,'source_http_status':record['http_status'],'source_published_at':None,'effective_at':None,'source_as_of':None,'county_geoids':seed['county_geoids'],'state':seed.get('state'),'county':seed.get('county'),'association':seed['association'],'applicability':seed.get('applicability'),'source_authority':seed['source_authority'],'seed_provenance':seed,'access_receipt_path':rel(receipt),'access_receipt_sha256':sha(receipt.read_bytes()),'quality':{'complete_legal_corpus':False,'text_characters':len(text),'extraction_status':record.get('extraction_status'),'reading_method':'native local extraction','date_inference':False},'links':[]}
        return {'status':'downloaded','resource':item}

    def annotate(self,item):
        text=(ROOT/item['text_path']).read_text(encoding='utf-8') if item.get('text_path') else ''
        if item.get('original_http_bytes') and item.get('access_receipt_path'):
            evidence=(ROOT/item['access_receipt_path']).read_bytes()
            if sha(evidence)!=item['access_receipt_sha256']:raise ValueError('Document extraction receipt mismatch')
            receipt=json.loads(evidence)
            item['quality'].update({k:receipt.get(k) for k in ['page_count','pages_extracted','extraction_status'] if k in receipt})
            pages=receipt.get('page_count',0)
            if pages:
                item['quality']['native_extraction_all_pages_processed']=receipt.get('pages_extracted')==pages
                item['quality']['low_text_density_review']=len(text.strip())<60*pages
                item['quality']['text_density_is_heuristic']=True
        if item.get('original_http_bytes') and item.get('seed_provenance',{}).get('anchor_text'):
            anchor=' '.join(item['seed_provenance']['anchor_text'].split())
            if len(anchor)>2 and anchor.lower() not in {'pdf','download','click here','view','here','document'}:
                item.setdefault('extracted_title',item['title']);item['title']=anchor
                item['title_provenance']={'method':'exact_publisher_link_label','parent_capture_id':item['seed_provenance'].get('parent_capture_id'),'parent_raw_sha256':item['seed_provenance'].get('parent_raw_sha256')}
        info=self.classifier.classify(item['title'],text,item['final_url'],item['mime_type'],item.get('links'))
        item['discovery_kind']=item['kind'];item['kind']=info['resource_type'];item['classification']=info
        item['quality']['document_shape']=info['document_shape'];item['quality']['substantive']=info['substantive']
        return item

    def discover(self,item):
        seed=item['seed_provenance'];depth=int(seed.get('depth',0))
        if depth>=2 or not item.get('html_path'):return 0
        authority=item.get('source_authority') or {}
        # An actual captured court page may verify the first gateway's authority.
        # This one exact host/title combination was manually reviewed in pilot.
        if crawler.host_of(item['final_url'])=='www.occourts.org' and 'Superior Court of California | County of Orange' in item['title']:
            authority={'class':'official_county_superior_court','verified':True,'basis':'Inspected publisher title explicitly names the court and County of Orange','capture_id':item['id'],'raw_sha256':item['raw_sha256']}
        if authority.get('verified') is not True or not re.search(r'court|judicial',authority.get('class',''),re.I):return 0
        raw=(ROOT/item['html_path']).read_bytes()
        if sha(raw)!=item['html_sha256']:raise ValueError('Discovery parent HTML hash mismatch')
        doc=html_parser.fromstring(raw);seen=set();candidates=[]
        host=crawler.host_of(item['final_url']);counts=self.db.execute('select count(*) from queue').fetchone()[0]
        county_count=self.db.execute('select count(*) from queue where county_key=?',(item['county_geoids'][0],)).fetchone()[0]
        # Only provider-reported final URLs are treated as observed aliases.
        aliases=set()
        for r in self.db.execute("select resource_json from queue where status='downloaded'"):
            parsed=json.loads(r[0]);aliases.add(parsed['source_url']);aliases.add(parsed['final_url'])
        for node in doc.xpath('//a[@href]'):
            url=crawler.canonical_url(node.get('href'),item['final_url']);label=' '.join(node.text_content().split())
            if not url or url in seen or url in aliases:continue
            seen.add(url);p=urllib.parse.urlsplit(url)
            if crawler.host_of(url)!=host:continue
            if EXCLUDE.search(p.path) or any(crawler.ACTION_SEGMENT.fullmatch(x) for x in p.path.split('/')):continue
            kind=self.classifier.classify_link(label,url)
            document=bool(DOCUMENT.search(p.path)) or node.get('type')=='application/pdf' or bool(re.search(r'\bPDF\b',label))
            context_document=document and item.get('classification',{}).get('document_shape') in {'rule_index','form_directory','order_index','fee_information','guide'}
            if not kind['eligible'] and not context_document:continue
            if not link_allowed(url,label,{**seed,'allowed_hosts':[host]}) and not context_document:continue
            if context_document and not kind['eligible']:
                kind={'resource_type':item['kind'],'priority':1 if item['kind'] in {'local_rule','standing_order'} else 2}
            child={**seed,'id':'county-litigation-seed:'+sha(url.encode())[:24],'url':url,'source_url':url,'resource_type':kind['resource_type'],'depth':depth+1,'allowed_hosts':[host],'parent_url':item['final_url'],'parent_capture_id':item['id'],'parent_raw_path':item['html_path'],'parent_raw_sha256':item['html_sha256'],'anchor_text':label,'source_authority':authority,'association':item['association'],'applicability':item.get('applicability'),'priority':int(kind['priority']),'existing_capture_suffices':url in self.existing_urls,'download_as_original':document}
            candidates.append(child)
        candidates.sort(key=lambda x:(x['priority'],not bool(DOCUMENT.search(urllib.parse.urlsplit(x['url']).path)),x['url']))
        added=0
        for child in candidates:
            if counts+added>=self.max_frontier or county_count+added>=self.per_county:break
            cursor=self.db.execute('INSERT OR IGNORE INTO queue(url,seed_json,status,resource_json,error,created_at,updated_at,priority,county_key,discovered) VALUES(?,?,?,NULL,NULL,?,?,?,?,1)',(child['url'],json.dumps(child),'already_published' if child['existing_capture_suffices'] else 'pending',now(),now(),child['priority'],child['county_geoids'][0]))
            added+=cursor.rowcount
        self.db.commit();return added

    def checkpoint(self):
        summary=self.export();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');folder=HERE/'checkpoints'/stamp
        resources=rows(HERE/'resources.jsonl');checked=0;verified=[];held=[]
        for item in resources:
            # A resource whose saved artifact is missing or altered is held out of the checkpoint (never published);
            # it must not abort the whole run. A path outside this collection remains a hard failure.
            problem=None;item_checked=0
            for key,digestkey in [('raw_path','raw_sha256'),('text_path','text_sha256'),('html_path','html_sha256'),('access_receipt_path','access_receipt_sha256')]:
                if not item.get(key):continue
                p=(ROOT/item[key]).resolve()
                if not p.is_relative_to(HERE):raise ValueError('Checkpoint artifact outside collection')
                if not p.is_file():problem={'id':item.get('id'),'source_url':item.get('source_url'),'artifact':key,'reason':'missing'};break
                if sha(p.read_bytes())!=item[digestkey]:problem={'id':item.get('id'),'source_url':item.get('source_url'),'artifact':key,'reason':'sha256_mismatch'};break
                item_checked+=1
            if problem:held.append(problem)
            else:verified.append(item);checked+=item_checked
        resources=verified
        save_rows(folder/'resources.jsonl',resources);save(folder/'summary.json',summary)
        validation={'status':'passed','validated_at':now(),'resources_sha256':sha((folder/'resources.jsonl').read_bytes()),'records':len(resources),'artifacts_verified':checked,'held_artifact_failures':held,'complete':False,'publication_classification_review_required':True}
        save(folder/'validation.json',validation);save(HERE/'latest.json',{'checkpoint':rel(folder),'resources_sha256':validation['resources_sha256'],'records':len(resources),'validated_at':validation['validated_at']})
        self.last_checkpoint=time.monotonic();return folder

    def run(self):
        runid=uuid.uuid4().hex;self.key=private_key(CREDENTIAL);self.credit_start=self.credits()
        self.cap=min(self.cap,self.credit_start);self.db.execute('INSERT INTO run_log VALUES(?,?,NULL,NULL)',(runid,now()));self.db.commit()
        save(HERE/'preflight.json',{'checked_at':now(),'remaining_credits':self.credit_start,'workers':self.workers,'run_request_cap':self.cap,'reserve':0,'no_purchases_or_overages':True,'key_printed':False,'shared_host_controls':True,'per_host_delay_seconds':2,'depth':2,'automatic_retries':0,'pdf_parsers_paid':False})
        futures={};completed=0;spent=0;activehosts=set()
        with worker_lock(ROOT/'sources/trellis/worker'),ThreadPoolExecutor(max_workers=self.workers) as pool:
            while (futures or not self.stop.is_set()) and completed<self.max_pages:
                if (HERE/'STOP_REQUESTED').exists():self.stop.set()
                if time.monotonic()-self.started>self.max_seconds:self.stop.set()
                pending=self.db.execute("SELECT * FROM queue WHERE status='pending' ORDER BY priority,created_at,url LIMIT 5000").fetchall()
                pending=sorted(pending,key=lambda x:(x['priority'],self.county_started.get(x['county_key'],0),x['created_at'],x['url']))
                admitted=0
                for row in pending:
                    if self.stop.is_set() or len(futures)>=self.workers or completed+len(futures)>=self.max_pages:break
                    host=crawler.host_of(row['url'])
                    if host in activehosts:continue
                    if row['url'] in self.saved_aliases:
                        self.db.execute("UPDATE queue SET status='captured_alias',error='Exact observed source/final URL is already captured',updated_at=? WHERE url=?",(now(),row['url']));self.db.commit();continue
                    if self.gate.ready_at(host,include_cooldown=False) is None:
                        self.db.execute("UPDATE queue SET status='host_deferred',error='Existing host pause or cooldown; no request submitted',updated_at=? WHERE url=?",(now(),row['url']));self.db.commit();continue
                    seed=json.loads(row['seed_json']);isdoc=bool(DOCUMENT.search(urllib.parse.urlsplit(row['url']).path)) or bool(DIRECT_DOWNLOAD.search(urllib.parse.urlsplit(row['url']).path)) or seed.get('download_as_original') is True
                    if not isdoc and spent>=self.cap:continue
                    requestid=uuid.uuid4().hex
                    self.county_started[row['county_key']]=self.county_started.get(row['county_key'],0)+1
                    if not isdoc:
                        spent+=1;self.db.execute('INSERT INTO requests VALUES(?,?,?,NULL,NULL,NULL)',(requestid,row['url'],now()))
                    self.db.execute("UPDATE queue SET status='in_flight',updated_at=? WHERE url=?",(now(),row['url']));self.db.commit()
                    future=pool.submit(self.document,seed) if isdoc else pool.submit(self.scrape,seed,requestid)
                    futures[future]=(row['url'],host,requestid,isdoc);activehosts.add(host);admitted+=1
                if not futures:break
                done,_=wait(futures,timeout=.5,return_when=FIRST_COMPLETED)
                for future in done:
                    url,host,requestid,isdoc=futures.pop(future);activehosts.remove(host);completed+=1
                    try:result=future.result()
                    except Exception as error:result={'status':'uncertain_or_policy_failure','error':str(error)[:600].replace(self.key,'[REDACTED]')}
                    if result.get('resource'):
                        result['resource']=self.annotate(result['resource']);self.saved_aliases.update([result['resource']['source_url'],result['resource']['final_url']])
                    self.db.execute('UPDATE queue SET status=?,resource_json=?,error=?,updated_at=? WHERE url=?',(result['status'],json.dumps(result.get('resource')),result.get('error'),now(),url))
                    if not isdoc:self.db.execute('UPDATE requests SET finished_at=?,api_status=?,result=? WHERE id=?',(now(),result.get('api_status'),json.dumps({k:v for k,v in result.items() if k!='resource'}),requestid))
                    self.db.commit()
                    if self.auto_discover and result.get('resource'):self.discover(result['resource'])
                    self.export()
                    if completed%100==0 or time.monotonic()-self.last_checkpoint>300:self.checkpoint()
                    print(json.dumps({'completed':completed,'requests_admitted':spent,'status':result['status'],'url':url}),flush=True)
                if spent and completed%5==0 and done:
                    self.credit_end=self.credits()
                    # Credit exhaustion prevents new provider calls; free direct
                    # document work may finish inside the same time/page bounds.
                    if self.credit_end<=0 or self.credit_start-self.credit_end>=self.cap:self.cap=spent
            self.stop.set()
        self.credit_end=self.credits();checkpoint=self.checkpoint();summary=self.export();summary['checkpoint']=rel(checkpoint)
        summary.update({'run_id':runid,'run_completed':completed,'run_requests_admitted':spent,'credits_before':self.credit_start,'credits_after':self.credit_end,'credit_delta':self.credit_start-self.credit_end,'elapsed_seconds':round(time.monotonic()-self.started,2)})
        self.db.execute('UPDATE run_log SET ended_at=?,summary_json=? WHERE id=?',(now(),json.dumps(summary),runid));self.db.commit();save(HERE/'run_summary.json',summary)
        print(json.dumps(summary),flush=True)

    def export(self):
        captures=[json.loads(r[0]) for r in self.db.execute("SELECT resource_json FROM queue WHERE status='downloaded' ORDER BY url")]
        save_rows(HERE/'resources.jsonl',captures)
        counts=dict(self.db.execute('SELECT status,count(*) FROM queue GROUP BY status').fetchall())
        summary={'generated_at':now(),'records':len(captures),'statuses':counts,'original_documents':sum(x['original_http_bytes'] for x in captures),'provider_pages':sum(not x['original_http_bytes'] for x in captures),'counties':len(set(g for x in captures for g in x['county_geoids'])),'complete':False,'quality_review_pending':True}
        save(HERE/'summary.json',summary);return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['ingest','run','export','discover','checkpoint']);p.add_argument('--seeds');p.add_argument('--request-cap',type=int,default=50);p.add_argument('--max-pages',type=int,default=100);p.add_argument('--max-seconds',type=int,default=600);p.add_argument('--workers',type=int,default=5);p.add_argument('--auto-discover',action='store_true');p.add_argument('--max-frontier',type=int,default=5000);p.add_argument('--per-county',type=int,default=250);a=p.parse_args()
    r=Runner(a.request_cap,a.max_pages,a.max_seconds,a.workers,a.auto_discover,a.max_frontier,a.per_county)
    try:
        if a.action=='ingest':print(json.dumps({'ingested':r.ingest(a.seeds)}))
        elif a.action=='run':r.run()
        elif a.action=='checkpoint':print(json.dumps({'checkpoint':rel(r.checkpoint())}))
        elif a.action=='discover':
            added=0
            for row in r.db.execute("select url,resource_json from queue where status='downloaded'").fetchall():
                item=r.annotate(json.loads(row['resource_json']));r.db.execute('update queue set resource_json=? where url=?',(json.dumps(item),row['url']));r.db.commit();added+=r.discover(item)
            print(json.dumps({'added':added,'summary':r.export()}))
        else:print(json.dumps(r.export()))
    finally:r.db.close()
if __name__=='__main__':main()
