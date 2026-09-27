"""Public, resumable state-law source verification and document collection.

This collection records retrieval evidence; it does not assert exhaustive coverage.
Run: python collect_official_laws.py seeds | documents | report
"""
from __future__ import annotations
import argparse, concurrent.futures, csv, hashlib, json, re, threading, time, ssl
from collections import defaultdict
from itertools import zip_longest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
import truststore
truststore.inject_into_ssl()
import requests
from bs4 import BeautifulSoup
import fitz

class WindowsTrustAdapter(requests.adapters.HTTPAdapter):
    """Isolate the native Windows verification context for each request session."""
    def __init__(self):
        self.system_context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        super().__init__()
    def build_connection_pool_key_attributes(self,request,verify,cert=None):
        host_params,pool_kwargs=super().build_connection_pool_key_attributes(request,verify,cert)
        if verify is True:
            pool_kwargs['ssl_context']=self.system_context
        return host_params,pool_kwargs

ROOT = Path(__file__).resolve().parent
for dirname in ('raw', 'text', 'metadata', 'indexes', 'documents'):
    (ROOT / dirname).mkdir(exist_ok=True)
HOST_LOCK = threading.Lock()
HOST_NEXT: dict[str, float] = {}
HOST_STOP: dict[str, dict] = {}
if (ROOT/'indexes'/'paused_hosts.json').exists():
    HOST_STOP=json.loads((ROOT/'indexes'/'paused_hosts.json').read_text(encoding='utf-8'))
MAX_BYTES = 100 * 1024 * 1024
UA = 'LegalCorpusArchive/0.1 (public source archival; no authentication)'

def now():
    return datetime.now(timezone.utc).isoformat()

def savejson(path, obj):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def slug(value):
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')

def key(url):
    return hashlib.sha256(url.encode()).hexdigest()[:24]

def pace(host):
    with HOST_LOCK:
        if host in HOST_STOP:
            raise RuntimeError('host_paused: '+str(HOST_STOP[host]))
        delay = max(0, HOST_NEXT.get(host, 0) - time.monotonic())
        HOST_NEXT[host] = time.monotonic() + delay + 1.0
    if delay:
        time.sleep(delay)
    with HOST_LOCK:
        if host in HOST_STOP:
            raise RuntimeError('host_paused: '+str(HOST_STOP[host]))

def pause_host(host,reason,url):
    with HOST_LOCK:
        HOST_STOP[host]={'reason':reason,'source_url':url,'paused_at_utc':now()}
        savejson(ROOT/'indexes'/'paused_hosts.json',HOST_STOP)

def bootstrap_host_pauses():
    for p in (ROOT/'metadata').glob('*.json'):
        d=json.loads(p.read_text(encoding='utf-8'))
        blocked=d.get('http_status') in (401,403,429)
        if d.get('text_path') and d.get('format')=='html':
            sample=(ROOT/d['text_path']).read_text(encoding='utf-8')[:4000]
            blocked=blocked or bool(re.search(r'verify you are human|validate your browser|checking your browser|enable javascript and cookies to continue',sample,re.I))
        if blocked:
            for url in {d['source_url'],d.get('final_url',d['source_url'])}:
                host=urlparse(url).netloc.lower()
                if host not in HOST_STOP:
                    pause_host(host,'recorded_access_barrier',d['source_url'])

def fetch(url, kind='seed'):
    ident = key(url)
    metapath = ROOT / 'metadata' / (ident + '.json')
    if metapath.exists():
        return json.loads(metapath.read_text(encoding='utf-8'))
    result = {'source_url': url, 'retrieved_at_utc': now(), 'retrieval_method': 'direct_public_https' if url.startswith('https') else 'direct_public_http', 'kind': kind, 'verification_status': 'pending','tls_verification':'Windows system trust store; isolated per-request SSL context; verify=True'}
    try:
        pace(urlparse(url).netloc.lower())
        with requests.Session() as session:
            session.mount('https://',WindowsTrustAdapter())
            response=session.get(url, timeout=(15,45), headers={'User-Agent': UA}, stream=True, verify=True)
            result.update(http_status=response.status_code, final_url=response.url, content_type=response.headers.get('Content-Type', ''), redirect_chain=[{'url':r.url, 'status':r.status_code} for r in response.history])
            if response.status_code in (401,403,429):
                pause_host(urlparse(url).netloc.lower(),'http_'+str(response.status_code),url)
                if urlparse(response.url).netloc.lower()!=urlparse(url).netloc.lower():
                    pause_host(urlparse(response.url).netloc.lower(),'http_'+str(response.status_code),url)
            if response.status_code != 200:
                result['verification_status'] = 'blocked_http_' + str(response.status_code) if response.status_code in (401,403,429) else 'http_error_' + str(response.status_code)
                sample = response.raw.read(65536, decode_content=True)
                errpath = ROOT / 'raw' / (ident + '.error.html')
                errpath.write_bytes(sample)
                result['evidence_path'] = str(errpath.relative_to(ROOT))
                savejson(metapath,result)
                return result
            pieces, length = [], 0
            for part in response.iter_content(128*1024):
                length += len(part)
                if length > MAX_BYTES:
                    raise RuntimeError('download_over_100_MiB_limit')
                pieces.append(part)
            body = b''.join(pieces)
        ispdf = body.startswith(b'%PDF-')
        iszip = body.startswith(b'PK\x03\x04')
        extension = '.pdf' if ispdf else '.zip' if iszip else '.html' if b'<html' in body[:6000].lower() or 'html' in result['content_type'] else '.bin'
        path = ROOT / ('documents' if ispdf or iszip else 'raw') / (ident + extension)
        path.write_bytes(body)
        result.update(evidence_path=str(path.relative_to(ROOT)), bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), format=extension[1:])
        text = ''
        links = []
        if ispdf:
            with fitz.open(stream=body, filetype='pdf') as pdf:
                result['pdf_pages'] = pdf.page_count
                result['document_title'] = pdf.metadata.get('title') or ''
                text = '\n\n'.join('--- PAGE '+str(i+1)+' ---\n'+p.get_text() for i,p in enumerate(pdf))
        elif extension == '.html':
            html = body.decode(response.encoding or 'utf-8', errors='replace')
            soup = BeautifulSoup(html,'lxml')
            result['document_title'] = soup.title.get_text(' ',strip=True) if soup.title else ''
            base_tag=soup.find('base',href=True)
            link_base=urljoin(result['final_url'],base_tag['href'].strip()) if base_tag else result['final_url']
            for a in soup.select('a[href]'):
                try:
                    href = urldefrag(urljoin(link_base, a['href'].strip()))[0]
                except ValueError:
                    continue
                if href.startswith(('https://','http://')):
                    links.append({'url':href, 'label':a.get_text(' ',strip=True)})
            for trash in soup(['script','style','noscript','svg']):
                trash.decompose()
            text = soup.get_text('\n',strip=True)
        if text:
            textpath = ROOT / 'text' / (ident + '.txt')
            textpath.write_text(text, encoding='utf-8')
            result['text_path'] = str(textpath.relative_to(ROOT))
            result['text_characters'] = len(text)
        result['links'] = links
        title = result.get('document_title','').lower()
        if any(x in title for x in ['just a moment','access denied','page not found','404 not found','request rejected']) or 'please wait while we validate your browser' in text.lower():
            result['verification_status'] = 'challenge_or_soft_error'
            if any(x in title for x in ['just a moment','access denied','request rejected']) or 'please wait while we validate your browser' in text.lower():
                pause_host(urlparse(url).netloc.lower(),'browser_challenge_or_access_denied',url)
        elif extension == '.html' and len(text) < 350:
            result['verification_status'] = 'retrieved_requires_review_or_javascript'
        else:
            result['verification_status'] = 'retrieved'
    except Exception as exc:
        result['verification_status'] = 'paused_host' if 'host_paused:' in str(exc) else 'retrieval_error'
        result['error'] = type(exc).__name__ + ': ' + str(exc)[:700]
    savejson(metapath,result)
    return result

def readseeds():
    with (ROOT/'seeds.tsv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    overridefile = ROOT/'source_overrides.json'
    if overridefile.exists():
        for item in json.loads(overridefile.read_text(encoding='utf-8')):
            for row in rows:
                if row['jurisdiction']==item['jurisdiction']:
                    row[item['category']]=item['official_url']
    return rows

def csvwrite(path, records, fields):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        w.writeheader()
        w.writerows(records)

def collect_seeds():
    # Keep the original directory evidence and its actually observed hrefs separate
    # from manually seeded modern candidate paths.
    directory_url='https://www.cookcountyil.gov/state-and-territorial-statutes-and-constitutions'
    directory=fetch(directory_url,'discovery_directory')
    observed={x['url'] for x in directory.get('links',[])}
    urls = sorted({r[c] for r in readseeds() for c in ('statutes','constitution','court_rules') if r.get(c)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for i,r in enumerate(pool.map(fetch,urls),1):
            print(f"SEED {i}/{len(urls)} {r['verification_status']} {r['source_url']}",flush=True)
    overrides={(x['jurisdiction'],x['category']):x for x in json.loads((ROOT/'source_overrides.json').read_text(encoding='utf-8'))} if (ROOT/'source_overrides.json').exists() else {}
    records, candidates, seen = [], [], set()
    for row in readseeds():
        for category in ('statutes','constitution','court_rules'):
            url=row[category]
            data=json.loads((ROOT/'metadata'/(key(url)+'.json')).read_text(encoding='utf-8'))
            record={k:v for k,v in data.items() if k!='links'}
            record.update(jurisdiction=row['jurisdiction'], category=category, official_url=url, discovered_from=directory_url if url in observed else '', discovery_method='observed_directory_link' if url in observed else 'curated_candidate_verified_by_http', metadata_path='metadata/'+key(url)+'.json', completeness='source_entry_only')
            if (row['jurisdiction'],category) in overrides:
                record['discovered_from']=overrides[(row['jurisdiction'],category)]['discovered_from']
                record['discovery_method']='observed_search_result_or_source_link'
            if row['jurisdiction']=='District of Columbia' and category=='constitution':
                record.update(category='home_rule_act', completeness='district_has_no_state_constitution', note='District of Columbia Home Rule Act substitutes for state-constitution category; DC is not a state.')
            if 'nmonesource.com' in url:
                record['authority_note']='New Mexico Compilation Commission publisher portal; public delegated publisher.'
            if row['jurisdiction']=='Tennessee' and category=='statutes':
                record['note']='State legislature access directory. Code itself is linked to a separate publisher; this seed is not the code corpus.'
            records.append(record)
            for link in data.get('links',[]):
                target=link['url']
                item={'jurisdiction':row['jurisdiction'], 'category':record['category'], 'official_url':target, 'label':link['label'], 'discovered_from':data.get('final_url',url), 'requested_parent_url':url, 'source_evidence_path':record.get('evidence_path',''), 'verification_status':'discovered_not_fetched'}
                # Index every observed link, but select document downloads only from
                # the same public authority host, without guessed API endpoints.
                if (row['jurisdiction'],record['category'],target) not in seen:
                    seen.add((row['jurisdiction'],record['category'],target))
                    candidates.append(item)
    supplemental=[]
    if (ROOT/'supplemental_sources.json').exists():
        for item in json.loads((ROOT/'supplemental_sources.json').read_text(encoding='utf-8')):
            data=fetch(item['official_url'],'supplemental_source')
            supplemental.append({**item,**{k:v for k,v in data.items() if k!='links'}})
            for link in data.get('links',[]):
                candidates.append({'jurisdiction':item['jurisdiction'],'category':item['category'],'official_url':link['url'],'label':link['label'],'discovered_from':data.get('final_url',item['official_url']),'source_evidence_path':data.get('rendered_evidence_path') or data.get('evidence_path',''),'verification_status':'discovered_not_fetched'})
    savejson(ROOT/'indexes'/'supplemental_sources_manifest.json',supplemental)
    savejson(ROOT/'indexes'/'source_index.json',records)
    csvwrite(ROOT/'indexes'/'source_index.csv',records,['jurisdiction','category','official_url','final_url','discovered_from','discovery_method','verification_status','http_status','document_title','evidence_path','text_path','metadata_path','bytes','sha256','completeness','note'])
    savejson(ROOT/'indexes'/'discovered_links.json',candidates)
    csvwrite(ROOT/'indexes'/'discovered_links.csv',candidates,['jurisdiction','category','official_url','label','discovered_from','source_evidence_path','verification_status'])
    report()

def same_authority(a,b):
    a=urlparse(a).hostname or ''; b=urlparse(b).hostname or ''
    a=a.removeprefix('www.'); b=b.removeprefix('www.')
    return a==b or a.endswith('.'+b) or b.endswith('.'+a)

def document_candidates():
    links=json.loads((ROOT/'indexes'/'discovered_links.json').read_text(encoding='utf-8'))
    result=[]
    for item in links:
        url=item['official_url']
        if re.search(r'\.(pdf|zip|docx?)(?:$|\?)',url,re.I) and same_authority(url,item['discovered_from']):
            combined=(url+' '+item['label']).lower()
            if any(term in combined for term in ('/calendar/','/calendar.','/calendars/','sessioncalendar','weeklyschedule','weekly_schedule','rfp26','seatingchart')):
                continue
            if any(term in combined for term in ('constitution','statute','court','rule','code','title','chapter','practice','manual','procedure','amendment')):
                result.append(item)
    seeds=json.loads((ROOT/'indexes'/'source_index.json').read_text(encoding='utf-8'))
    for seed in seeds:
        if seed.get('format') in ('pdf','zip','doc','docx'):
            result.append({'jurisdiction':seed['jurisdiction'],'category':seed['category'],'official_url':seed['official_url'],'label':seed.get('document_title',''),'discovered_from':seed.get('discovered_from',''),'source_evidence_path':seed.get('evidence_path',''),'verification_status':'discovered_not_fetched'})
    return result

def collect_documents(limit):
    candidates=document_candidates()
    savejson(ROOT/'indexes'/'document_candidates.json',candidates)
    bystate=defaultdict(list)
    for x in candidates:
        if x['official_url'] not in bystate[x['jurisdiction']]:
            bystate[x['jurisdiction']].append(x['official_url'])
    # Round robin across jurisdictions prevents a large state's thousands of
    # chapter PDFs from delaying every other state's primary compilations.
    unique=list(dict.fromkeys(u for batch in zip_longest(*bystate.values()) for u in batch if u))
    if limit:
        unique=unique[:limit]
    completed={}
    def checkpoint():
        results=[]
        for item in candidates:
            if item['official_url'] in completed:
                results.append({**item,**{k:v for k,v in completed[item['official_url']].items() if k!='links'}})
        savejson(ROOT/'indexes'/'document_manifest.json',results)
        csvwrite(ROOT/'indexes'/'document_manifest.csv',results,['jurisdiction','category','official_url','label','discovered_from','verification_status','http_status','evidence_path','text_path','bytes','sha256','pdf_pages'])
        write_coverage(candidates, completed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(fetch,u,'linked_document'):u for u in unique}
        for i,f in enumerate(concurrent.futures.as_completed(futures),1):
            r=f.result()
            completed[r['source_url']]=r
            print(f"DOC {i}/{len(unique)} {r['verification_status']} {r['source_url']}",flush=True)
            if i%25==0:
                checkpoint()
    checkpoint()
    report()

def report():
    files=list((ROOT/'metadata').glob('*.json'))
    records=[json.loads(p.read_text(encoding='utf-8')) for p in files]
    states=readseeds()
    counts={}
    for r in records:
        status=r['verification_status']; counts[status]=counts.get(status,0)+1
    data={'as_of_utc':now(),'jurisdictions_indexed':len(states),'seed_category_entries':len(states)*3,'fetched_unique_urls':len(records),'retrieval_status_counts':counts,'pdf_files':sum(r.get('format')=='pdf' for r in records),'pdf_pages':sum(r.get('pdf_pages',0) for r in records),'saved_payload_bytes':sum(r.get('bytes',0) for r in records),'full_corpus_complete':False,'limitations':['This is a verified-access source map with preserved fetched pages and linked files, not a complete enumeration of every statute, rule, version, case, county, or historical record.','HTTP success establishes retrieval, not authoritative currency, publication certification, or complete legal text.','Blocked responses, JavaScript shells, and unavailable seed paths are recorded explicitly. No authentication, CAPTCHA bypass, proxy rotation, or paid purchase is used.','Candidate source paths are labeled separately from links actually observed in downloaded directories.','The District of Columbia has a Home Rule Act entry and no state-constitution entry.']}
    savejson(ROOT/'indexes'/'collection_summary.json',data)
    print(json.dumps(data,indent=2),flush=True)

def write_coverage(candidates=None, completed=None):
    if candidates is None:
        candidates=document_candidates()
    if completed is None:
        completed={}
        for p in (ROOT/'metadata').glob('*.json'):
            d=json.loads(p.read_text(encoding='utf-8')); completed[d['source_url']]=d
    groups=defaultdict(set)
    for x in candidates:
        groups[(x['jurisdiction'],x['category'])].add(x['official_url'])
    rows=[]
    seeds=json.loads((ROOT/'indexes'/'source_index.json').read_text(encoding='utf-8'))
    for seed in seeds:
        urls=groups[(seed['jurisdiction'],seed['category'])]
        attempted=[completed[u] for u in urls if u in completed]
        downloaded=[d for d in attempted if d.get('format') in ('pdf','zip','doc','docx') and d.get('verification_status')=='retrieved']
        rows.append({'jurisdiction':seed['jurisdiction'],'category':seed['category'],'expected_full_corpus_documents':None,'expected_status':'unknown_not_fully_enumerated','discovery_complete':False,'discovered_document_urls':len(urls),'attempted_document_urls':len(attempted),'downloaded_documents':len(downloaded),'failed_or_non_document_responses':len(attempted)-len(downloaded),'pending_discovered_documents':len(urls)-len(attempted),'downloaded_bytes':sum(d.get('bytes',0) for d in downloaded),'pdf_pages':sum(d.get('pdf_pages',0) for d in downloaded),'source_entry_status':seed['verification_status'],'source_entry_url':seed['official_url'],'source_entry_evidence_path':seed.get('evidence_path',''),'as_of_utc':now()})
    savejson(ROOT/'indexes'/'jurisdiction_coverage.json',rows)
    csvwrite(ROOT/'indexes'/'jurisdiction_coverage.csv',rows,list(rows[0]) if rows else [])

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('stage',choices=['seeds','documents','report']); parser.add_argument('--limit',type=int,default=0); args=parser.parse_args()
    bootstrap_host_pauses()
    if args.stage=='seeds': collect_seeds()
    elif args.stage=='documents': collect_documents(args.limit)
    else: report()
