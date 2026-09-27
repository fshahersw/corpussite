"""Public official-court discovery; original responses and provenance are retained.

No credentials, browser-session extraction, paid APIs, or challenge bypasses.
Only public GET requests; access barriers are recorded instead of retried.
"""
from __future__ import annotations
import concurrent.futures, csv, hashlib, io, json, re, sys, threading, time, zipfile, ssl
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
import truststore
truststore.inject_into_ssl()
import requests
from bs4 import BeautifulSoup

class WindowsTrustAdapter(requests.adapters.HTTPAdapter):
    """Give each request session its own system-trust context.

    Requests otherwise shares a preloaded SSL context across threads. Windows
    truststore temporarily configures its context during native verification,
    so separate contexts prevent another thread from observing that transition.
    """
    def __init__(self):
        self.system_context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        super().__init__()
    def build_connection_pool_key_attributes(self,request,verify,cert=None):
        host_params,pool_kwargs=super().build_connection_pool_key_attributes(request,verify,cert)
        if verify is True:pool_kwargs['ssl_context']=self.system_context
        return host_params,pool_kwargs

ROOT = Path(__file__).resolve().parent
for d in ['raw','text','links','manifests','datasets','reports']:
    (ROOT/d).mkdir(exist_ok=True)
UA = 'OfficialCourtCorpusResearch/1.0 (public source archival research)'
LOCK = threading.Lock()
HOST_LOCKS = {}
HOST_LAST = {}
MANIFEST = ROOT/'manifests/fetches.jsonl'

def now(): return datetime.now(timezone.utc).isoformat()
def write_json(path, data): path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
def read_jsonl(path):
    if not path.exists(): return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
def append_jsonl(path, data):
    with LOCK:
        with path.open('a', encoding='utf-8') as f: f.write(json.dumps(data,ensure_ascii=False)+'\n')
def relative(path): return path.relative_to(ROOT).as_posix()
def url_id(url): return hashlib.sha256(url.encode()).hexdigest()[:20]

def fetch(item):
    url = item['url']
    existing = next((r for r in reversed(read_jsonl(MANIFEST)) if r['requested_url']==url),None)
    if existing and not (existing.get('verification_status')=='transport_error' and 'CERTIFICATE_VERIFY_FAILED' in existing.get('error','') and existing.get('tls_trust')!='Windows system trust store'):
        return existing
    host=urlparse(url).netloc.lower()
    with LOCK: host_lock=HOST_LOCKS.setdefault(host,threading.Lock())
    rec={**item,'requested_url':url,'fetched_at_utc':now(),'source_id':url_id(url),'verification_status':'not_fetched','tls_trust':'Windows system trust store'}
    try:
        with host_lock:
            wait=max(0,0.75-(time.monotonic()-HOST_LAST.get(host,0)))
            if wait: time.sleep(wait)
            try:
                with requests.Session() as session:
                    session.mount('https://',WindowsTrustAdapter())
                    response=session.get(url,timeout=(12,35),headers={'User-Agent':UA},allow_redirects=True,verify=True)
            finally: HOST_LAST[host]=time.monotonic()
        rec.update(final_url=response.url,http_status=response.status_code,content_type=response.headers.get('Content-Type',''),response_headers={k:v for k,v in response.headers.items() if k.lower() in ['content-type','last-modified','etag','content-length']})
        body=response.content
        rec.update(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
        ct=rec['content_type'].lower()
        ext='.pdf' if body.startswith(b'%PDF') else '.xlsx' if body.startswith(b'PK\x03\x04') and (urlparse(response.url).path.lower().endswith('.xlsx') or 'spreadsheetml' in ct) else '.zip' if body.startswith(b'PK\x03\x04') else '.html' if 'html' in ct else '.json' if 'json' in ct else '.txt' if 'text' in ct else '.bin'
        raw=ROOT/'raw'/f"{rec['source_id']}{ext}"; raw.write_bytes(body); rec['raw_path']=relative(raw)
        if response.status_code in (401,403,429): rec['verification_status']='access_barrier'
        elif response.status_code>=400: rec['verification_status']='http_error'
        else: rec['verification_status']='retrieved'
        if ext=='.html':
            soup=BeautifulSoup(body,'html.parser')
            rec['title']=soup.title.get_text(' ',strip=True) if soup.title else ''
            for unwanted in soup(['script','style','noscript']): unwanted.decompose()
            text=soup.get_text('\n',strip=True)
            if response.status_code==200 and (re.search(r'^(Access Denied|Just a moment|Attention Required)',rec['title'],re.I) or re.search(r'verify you are human|validate your browser|checking your browser|request rejected|enable javascript and cookies to continue',text[:3000],re.I)):
                rec['verification_status']='challenge_page'
            elif response.status_code==200 and re.search(r'page not found|404.*not found|not found.*404',rec['title'],re.I):
                rec['verification_status']='soft_http_error'
            elif response.status_code==200 and len(text)<100 and len(body)>1000:
                rec['verification_status']='javascript_required'
            txt=ROOT/'text'/f"{rec['source_id']}.txt"; txt.write_text(text,encoding='utf-8'); rec['text_path']=relative(txt)
            links=[]; seen=set()
            for a in soup.find_all('a',href=True):
                try:
                    href=urldefrag(urljoin(response.url,a['href']))[0]
                    if urlparse(href).scheme not in ('http','https'): continue
                except ValueError:
                    rec['invalid_link_count']=rec.get('invalid_link_count',0)+1
                    continue
                label=a.get_text(' ',strip=True)
                key=(href,label)
                if key in seen: continue
                seen.add(key)
                links.append({'url':href,'label':label,'discovered_from':response.url,'source_id':rec['source_id']})
            lp=ROOT/'links'/f"{rec['source_id']}.json"; write_json(lp,links); rec['links_path']=relative(lp); rec['link_count']=len(links)
        elif ext=='.pdf' and rec['verification_status']=='retrieved':
            try:
                from pypdf import PdfReader
                reader=PdfReader(io.BytesIO(body))
                txt=ROOT/'text'/f"{rec['source_id']}.txt"
                txt.write_text('\n\n'.join((p.extract_text() or '') for p in reader.pages),encoding='utf-8')
                rec.update(text_path=relative(txt),pdf_pages=len(reader.pages))
            except Exception as e: rec['extraction_error']=str(e)[:400]
        elif ext in ('.txt','.json'):
            txt=ROOT/'text'/f"{rec['source_id']}.txt"; txt.write_text(response.text,encoding='utf-8'); rec['text_path']=relative(txt)
    except Exception as e:
        rec.update(verification_status='transport_error',error=str(e)[:600])
    append_jsonl(MANIFEST,rec)
    print(json.dumps({k:rec.get(k) for k in ['jurisdiction','category','requested_url','http_status','verification_status','bytes']},ensure_ascii=False),flush=True)
    return rec

def batch(items,workers=6):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch,items))

def links(rec):
    if rec.get('links_path'): return json.loads((ROOT/rec['links_path']).read_text(encoding='utf-8'))
    return []

def bootstrap():
    urls=[
        ('USA','government_court_index','https://www.usa.gov/courts','user task'),
        ('USA','government_court_index','https://www.justice.gov/jmd/ls/state','https://www.usa.gov/courts'),
        ('USA','census_gazetteer_index','https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2026.html','live Census page'),
        ('USA','census_gazetteer_index','https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html','live Census page'),
        ('Tennessee/Cocke County','county_government','https://www.cockecountytn.gov/','user screenshot'),
    ]
    records=batch([dict(jurisdiction=j,category=c,url=u,discovered_from=d) for j,c,u,d in urls])
    downloads=[]
    for rec in records:
        if rec['category']!='census_gazetteer_index': continue
        for a in links(rec):
            if re.search(r'Gaz_(counties|state)_national\.zip$',a['url'],re.I):
                downloads.append(dict(jurisdiction='USA',category='census_gazetteer_download',url=a['url'],discovered_from=rec['final_url'],label=a['label']))
    archives=batch(downloads,workers=2)
    for rec in archives:
        if rec['verification_status']!='retrieved': continue
        with zipfile.ZipFile(ROOT/rec['raw_path']) as archive:
            for name in archive.namelist():
                if Path(name).suffix.lower()!='.txt': continue
                content=archive.read(name)
                path=ROOT/'datasets'/Path(name).name
                path.write_bytes(content)
                text=content.decode('utf-8-sig')
                delim='|' if '|' in text.splitlines()[0] else '\t'
                rows=[{k.strip():v.strip() if v else '' for k,v in row.items()} for row in csv.DictReader(io.StringIO(text),delimiter=delim)]
                write_json(path.with_suffix('.json'),rows)
                with path.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
                    w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
                write_json(path.with_suffix('.provenance.json'),dict(source_url=rec['requested_url'],raw_archive_path=rec['raw_path'],archive_sha256=rec['sha256'],member=name,row_count=len(rows),fetched_at_utc=rec['fetched_at_utc'],geography_vintage=re.search(r'20\d\d',name)[0]))
    doj=next(r for r in records if r['requested_url']=='https://www.justice.gov/jmd/ls/state')
    states=[]
    for a in links(doj):
        if re.search(r'/jmd/ls/[^/]+$',a['url']) and a['label'].isupper() and len(a['label'])>3:
            states.append(dict(jurisdiction=a['label'].title(),category='doj_state_court_index',url=a['url'],discovered_from=doj['final_url']))
    write_json(ROOT/'manifests/doj_state_sources.json',states)
    batch(states,workers=3)

if __name__=='__main__':
    if sys.argv[1:] == ['bootstrap']: bootstrap()
    elif sys.argv[1:]: batch(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
