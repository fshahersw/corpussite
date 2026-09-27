"""Revalidate only the finite historical TLS-warning candidate set.

Original files are never replaced. Successful TLS responses are hashed and
compared; changed payloads and exact differences are retained separately.
"""
from __future__ import annotations
import argparse, csv, difflib, hashlib, json, re, time, warnings, uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests
from urllib3.exceptions import InsecureRequestWarning
import collect_official_laws as c

AUDIT=c.ROOT/'indexes'/'historical_tls_audit.json'
DEST=c.ROOT/'tls_revalidation'
for name in ('responses','changed_payloads','diffs'):
    (DEST/name).mkdir(parents=True,exist_ok=True)
PAUSES=DEST/'paused_hosts.json'
LAST={}

def atomic(path,data):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt==7:
                raise
            time.sleep(0.05*(2**attempt))

def paused_hosts():
    result={}
    for path in (c.ROOT/'indexes'/'paused_hosts.json',PAUSES):
        if path.exists():
            result.update(json.loads(path.read_text(encoding='utf-8')))
    return result

def pause(host,reason,url):
    data=json.loads(PAUSES.read_text(encoding='utf-8')) if PAUSES.exists() else {}
    data[host]={'reason':reason,'source_url':url,'paused_at_utc':c.now()}
    atomic(PAUSES,data)
    # Merge into the shared persistent list without replacing original records.
    shared_path=c.ROOT/'indexes'/'paused_hosts.json'
    shared=json.loads(shared_path.read_text(encoding='utf-8')) if shared_path.exists() else {}
    shared.update(data)
    atomic(shared_path,shared)

def safe_request(url):
    chain=[]
    for _ in range(8):
        host=(urlparse(url).hostname or '').lower()
        if host in paused_hosts():
            raise RuntimeError('persistently_paused_host: '+host)
        wait=max(0,1.5-(time.monotonic()-LAST.get(host,0)))
        if wait:
            time.sleep(wait)
        if host in paused_hosts():
            raise RuntimeError('persistently_paused_host: '+host)
        with requests.Session() as session:
            session.mount('https://',c.WindowsTrustAdapter())
            # TLS warnings are errors, never hidden or accepted as verified.
            with warnings.catch_warnings():
                warnings.simplefilter('error',InsecureRequestWarning)
                response=session.get(url,timeout=(15,45),headers={'User-Agent':c.UA},stream=True,verify=True,allow_redirects=False)
                LAST[host]=time.monotonic()
                if response.status_code in (301,302,303,307,308):
                    target=urljoin(url,response.headers.get('Location',''))
                    chain.append({'url':url,'http_status':response.status_code,'location':target})
                    response.close()
                    if urlparse(target).scheme!='https':
                        raise RuntimeError('redirect_would_leave_verified_https: '+target)
                    url=target
                    continue
                headers={k:v for k,v in response.headers.items() if k.lower() in ('content-type','content-length','last-modified','etag','date')}
                body=bytearray()
                for piece in response.iter_content(131072):
                    body.extend(piece)
                    if len(body)>c.MAX_BYTES:
                        raise RuntimeError('revalidation_response_exceeds_100_MiB')
                status=response.status_code
                response.close()
                return {'final_url':url,'http_status':status,'headers':headers,'redirect_chain':chain},bytes(body)
    raise RuntimeError('redirect_limit_reached')

def visible_text(body):
    soup=BeautifulSoup(body,'lxml')
    for tag in soup(['script','style','noscript','svg']):
        tag.decompose()
    return soup.get_text('\n',strip=True)

def verify(item,retry_transport_errors=False):
    ident=c.key(item['source_url'])
    outfile=DEST/'responses'/(ident+'.json')
    previous=None
    if outfile.exists():
        previous=json.loads(outfile.read_text(encoding='utf-8'))
        retryable=previous.get('status')=='error' and any(k in previous.get('error','') for k in ('ReadTimeout','ConnectTimeout','ConnectionError'))
        if not retry_transport_errors or not retryable or previous.get('attempt_count',1)>=2:
            return previous
        history=DEST/'responses'/(ident+'.attempt1.json')
        if not history.exists():
            atomic(history,previous)
    result={'source_url':item['source_url'],'original_evidence_path':item['evidence_path'],'original_manifest_sha256':item['sha256'],'original_format':item.get('format'),'verified_at_utc':c.now(),'tls_verification':'verify=True; isolated per-session Windows system trust context; InsecureRequestWarning treated as error','original_preserved':True,'attempt_count':2 if previous else 1}
    if previous:
        result['prior_attempt_path']='tls_revalidation/responses/'+ident+'.attempt1.json'
        result['prior_attempt_error']=previous.get('error')
    original=(c.ROOT/item['evidence_path']).read_bytes()
    result['original_local_sha256']=hashlib.sha256(original).hexdigest()
    result['original_local_matches_manifest']=result['original_local_sha256']==item['sha256']
    try:
        if not result['original_local_matches_manifest']:
            raise RuntimeError('original_local_file_does_not_match_manifest')
        if urlparse(item['source_url']).scheme!='https':
            raise RuntimeError('original_source_not_https')
        details,body=safe_request(item['source_url'])
        result.update(details)
        result['verified_response_sha256']=hashlib.sha256(body).hexdigest()
        result['verified_response_bytes']=len(body)
        if details['http_status'] in (401,403,429):
            pause(urlparse(details['final_url']).hostname or '', 'http_'+str(details['http_status']),item['source_url'])
        ishtml='html' in details['headers'].get('Content-Type','').lower() or b'<html' in body[:5000].lower()
        newtext=visible_text(body) if ishtml else ''
        challenge=bool(re.search(r'verify you are human|validate your browser|checking your browser|enable javascript and cookies to continue',newtext[:5000],re.I))
        if challenge:
            pause(urlparse(details['final_url']).hostname or '', 'browser_validation_challenge',item['source_url'])
        if details['http_status']!=200 or challenge:
            errfile=DEST/'changed_payloads'/(ident+'.error.html')
            errfile.write_bytes(body)
            result['response_evidence_path']=str(errfile.relative_to(c.ROOT))
            result['status']='blocked' if details['http_status'] in (401,403,429) or challenge else 'http_error'
        elif result['verified_response_sha256']==item['sha256']:
            result['status']='sha256_match'
            result['byte_comparison']='identical'
        else:
            result['status']='sha256_differs'
            result['byte_comparison']='different'
            result['byte_length_delta']=len(body)-len(original)
            suffix=Path(item['evidence_path']).suffix or '.bin'
            changed=DEST/'changed_payloads'/(ident+suffix)
            changed.write_bytes(body)
            result['verified_new_version_path']=str(changed.relative_to(c.ROOT))
            if item.get('format')=='html' and ishtml:
                oldtext=visible_text(original)
                result['visible_text_identical']=oldtext==newtext
                result['original_visible_text_sha256']=hashlib.sha256(oldtext.encode()).hexdigest()
                result['verified_visible_text_sha256']=hashlib.sha256(newtext.encode()).hexdigest()
                rawdiff='\n'.join(difflib.unified_diff(original.decode('utf-8',errors='replace').splitlines(),body.decode('utf-8',errors='replace').splitlines(),fromfile=item['evidence_path'],tofile=str(changed.relative_to(c.ROOT)),lineterm=''))
                diffpath=DEST/'diffs'/(ident+'.raw.diff')
                diffpath.write_text(rawdiff,encoding='utf-8')
                result['raw_diff_path']=str(diffpath.relative_to(c.ROOT))
                textdiff='\n'.join(difflib.unified_diff(oldtext.splitlines(),newtext.splitlines(),fromfile='original_visible_text',tofile='verified_visible_text',lineterm=''))
                textpath=DEST/'diffs'/(ident+'.text.diff')
                textpath.write_text(textdiff,encoding='utf-8')
                result['visible_text_diff_path']=str(textpath.relative_to(c.ROOT))
                result['raw_diff_lines']=len(rawdiff.splitlines())
                result['visible_text_diff_lines']=len(textdiff.splitlines())
    except Exception as exc:
        result['status']='paused_host' if 'persistently_paused_host' in str(exc) else 'error'
        result['error']=type(exc).__name__+': '+str(exc)
    atomic(outfile,result)
    return result

def update_report(base,rows):
    byurl={r['source_url']:r for r in rows}
    base['revalidation_as_of_utc']=c.now()
    base['revalidation_completed_responses']=len(rows)
    base['revalidation_status_counts']=dict(Counter(r['status'] for r in rows))
    base['revalidation_complete']=len(rows)==base['affected_saved_responses']
    base['revalidation_summary_path']='tls_revalidation/summary.json'
    for item in base['responses']:
        if item['source_url'] in byurl:
            r=byurl[item['source_url']]
            item['status']=r['status']
            item['revalidation_path']='tls_revalidation/responses/'+c.key(item['source_url'])+'.json'
    summary={'as_of_utc':c.now(),'expected_responses':base['affected_saved_responses'],'completed_responses':len(rows),'request_attempts':sum(r.get('attempt_count',1) for r in rows),'status_counts':dict(Counter(r['status'] for r in rows)),'original_files_modified':0,'changed_responses':[r for r in rows if r['status']=='sha256_differs'],'blocked_or_errors':[r for r in rows if r['status'] not in ('sha256_match','sha256_differs')],'resolved_transport_errors':[{'source_url':r['source_url'],'prior_attempt_error':r['prior_attempt_error'],'prior_attempt_path':r['prior_attempt_path'],'final_status':r['status']} for r in rows if r.get('prior_attempt_error') and r['status'] in ('sha256_match','sha256_differs')],'tls_warnings_accepted':0,'all_original_local_hashes_match':all(r.get('original_local_matches_manifest') for r in rows)}
    atomic(DEST/'summary.json',summary)
    atomic(AUDIT,base)
    c.csvwrite(DEST/'results.csv',rows,['source_url','status','original_manifest_sha256','verified_response_sha256','verified_response_bytes','http_status','final_url','original_evidence_path','verified_new_version_path','raw_diff_path','visible_text_identical','visible_text_diff_path','error'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--retry-transport-errors',action='store_true'); args=parser.parse_args()
    base=json.loads(AUDIT.read_text(encoding='utf-8'))
    initial=c.ROOT/'indexes'/'historical_tls_audit.initial.json'
    if not initial.exists():
        atomic(initial,base)
    rows=[]
    # One sequential revalidation worker is independent of the live document
    # queue and adds at most one request every 1.5 seconds to any target host.
    for i,item in enumerate(base['responses'],1):
        result=verify(item,args.retry_transport_errors); rows.append(result)
        if i%5==0 or i==len(base['responses']):
            update_report(base,rows)
        print(json.dumps({'completed':i,'expected':len(base['responses']),'url':item['source_url'],'status':result['status'],'visible_text_identical':result.get('visible_text_identical'),'error':result.get('error')}),flush=True)
    print(json.dumps({k:v for k,v in json.loads((DEST/'summary.json').read_text(encoding='utf-8')).items() if k not in ('changed_responses','blocked_or_errors')},indent=2),flush=True)
