"""Normalize only saved connector evidence and validated DOM batches; no network."""
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
LABEL='Trellis publisher-reported coverage metadata; internal research use; not for redistribution'
BROWSER_FOLDERS=['trellis_browser_backfill_20260918','trellis_browser_backfill_20260918T2213','trellis_browser_backfill_20260919']
PROVIDER_FOLDER=ROOT/'sources/trellis_county_firecrawl_20260919'
PROVIDER_RECOVERY_FOLDER=ROOT/'sources/trellis_county_offline_supplement_20260919'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def rows(path):return [json.loads(l) for l in path.read_text(encoding='utf-8-sig').splitlines() if l.strip()]
def write(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(path)
def writel(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in value),encoding='utf-8');tmp.replace(path)
def temporal(at=None,basis=None):
    return {'captured_at':at,'captured_at_basis':basis,**{k:None for k in ('source_as_of','source_as_of_basis','published_at','published_at_basis','effective_from','effective_from_basis','effective_to','effective_to_basis')}}


def browser_fields(dom):
    fields={};headings=dom.get('dom_profile_headings') or []
    for i,h in enumerate(headings[:-1]):
        if h.get('tag')=='h2' and headings[i+1].get('tag')=='h3':
            label=h['text'].lower().replace(' ','_');fields[label]=headings[i+1]['text']
            if label=='website':fields[label]=next((l['url'] for l in headings[i+1].get('links',[]) if l['url'].startswith(('https://','http://'))),None)
    reported={k:None for k in ('population','area_sq_mi','seat','established_year','name_meaning','description','form_of_government','board_of_supervisor','website','phone_number','administration_address','court_address','additional_phones')}
    for key,value in fields.items():
        dest={'county_seat':'seat','meaning':'name_meaning','board_of_supervisors':'board_of_supervisor'}.get(key,key)
        reported[dest]=value
    return reported


def provider_snapshot(folder=PROVIDER_FOLDER):
    """Take one consistent passed checkpoint, while its collector may continue."""
    gatepath=folder/'validation.json'
    if not gatepath.is_file():return None
    before=gatepath.read_bytes();gate=json.loads(before)
    if gate.get('ready') is not True or gate.get('status')!='passed':return None
    if gate.get('manifest_path')!='resources.jsonl' or gate.get('provider_representation') is not True:raise ValueError('Wrong provider contract')
    manifest=(folder/'resources.jsonl').read_bytes()
    if gatepath.read_bytes()!=before or hashlib.sha256(manifest).hexdigest()!=gate.get('manifest_sha256'):return None
    records=[json.loads(line) for line in manifest.decode('utf-8-sig').splitlines() if line.strip()]
    if len(records)!=gate.get('records') or len({r['id'] for r in records})!=len(records) or len({r['source_url'] for r in records})!=len(records):raise ValueError('Provider count/identity mismatch')
    return gate,records,before,manifest


def provider_section(raw):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(raw.get('html') or '', 'html.parser')
    sections=[s for s in soup.select('.top-county-info-block__container') if s.find('h1')]
    if len(sections)!=1:raise ValueError('Expected one selected county section')
    section=sections[0];heading=section.find('h1')
    if heading is None:raise ValueError('Missing profile heading')
    fields={};website=None
    for label in section.find_all('h2'):
        value=label.find_next_sibling('h3')
        if value is None:continue
        key=' '.join(label.stripped_strings).lower().replace(' ','_')
        fields[key]=' '.join(' '.join(value.stripped_strings).split())
        if key=='website':
            website=next((a['href'] for a in value.find_all('a',href=True) if a['href'].startswith(('http://','https://'))),None)
    headings=[]
    for key,value in fields.items():
        headings.extend([{'tag':'h2','text':key.replace('_',' ')},{'tag':'h3','text':value,'links':[{'url':website}] if key=='website' and website else []}])
    reported=browser_fields({'dom_profile_headings':headings})
    allowed=set(browser_fields({}))
    return ' '.join(heading.stripped_strings),fields,website,{k:v for k,v in reported.items() if k in allowed}


def provider_identity(url,heading,metadata,proofs,inventory):
    """An explicit parent label and profile/state evidence must corroborate the join."""
    part=urlsplit(url);pieces=part.path.strip('/').split('/')
    state_slugs={r['state'].lower().replace(' ','-'):r['usps'] for r in inventory}
    if part.scheme!='https' or part.netloc!='trellis.law' or part.query or part.fragment or len(pieces)!=3 or pieces[0]!='coverage' or pieces[1] not in state_slugs:raise ValueError('Invalid county profile URL')
    code=state_slugs[pieces[1]];state_name=next(r['state'] for r in inventory if r['usps']==code)
    labels=sorted({p['label'].strip() for p in proofs if p.get('url')==url and urlsplit(p['source_url']).netloc=='trellis.law' and urlsplit(p['source_url']).path.strip('/').split('/')[:2]==pieces[:2] and p.get('label')})
    # "Dockets" is a captured navigation suffix, not part of the geographic name.
    normalized_labels=sorted({re.sub(r'\s+Dockets$','',name,flags=re.I) for name in labels})
    corroborated=[name for name in normalized_labels if heading.casefold().startswith(name.casefold()+' ')]
    text=' '.join(str(metadata.get(k) or '') for k in ('title','description'))
    state_explicit=bool(re.search(r',\s*'+re.escape(code)+r'\b',text)) or state_name.casefold() in text.casefold()
    candidates=[r for r in inventory if r['usps']==code and r['name'].casefold() in {n.casefold() for n in corroborated}] if state_explicit else []
    match=candidates[0] if len(candidates)==1 else None
    name=match['name'] if match else (corroborated[0] if len(corroborated)==1 else heading)
    return {'state':code,'state_name':state_name,'county':name,'fips':match['geoid'] if match else None,
            'basis':'Exact observed parent county label + profile heading + explicit metadata state + Census name/state' if match else None,
            'state_explicit_in_metadata':state_explicit,'observed_labels':labels,'heading':heading,
            'parent_label_normalization':'Only exact trailing Dockets navigation suffix removed; names otherwise unchanged',
            'reason':None if match else 'No unique corroborated exact Census name/state match; no county inferred from URL slug'}


def main():
    write(HERE/'validation.json',{'schema_version':'1','status':'building','ready':False,'data_files':[]})
    inventory=rows(ROOT/'delivery/focused_legal_corpus/counties/counties.jsonl')
    state_names={r['usps']:r['state'] for r in inventory}
    by_name={(r['usps'],r['name'].casefold()):r for r in inventory};by_fips={r['geoid']:r for r in inventory}
    log=rows(HERE/'raw/_call_log.jsonl');logby={r['response_file']:r for r in log}
    missing=[r for r in log if not (HERE/'raw'/r['response_file']).exists()]
    states={};counties={};receipts=[];edges=[];unresolved=[];inputs=[];saved_profile_urls=set()
    def make_receipt(path,ident,tool,args,at,basis,kind):
        rec={'id':ident,'tool':tool,'arguments':args,'called_at_utc':at,'called_at_basis':basis,
             'response_path':path.relative_to(HERE).as_posix(),'response_sha256':sha(path),'bytes':path.stat().st_size,'record_kind':kind}
        receipts.append(rec);return rec
    def county_row(code,name,receipt,count=None,docs=None,listed=True,at=None,basis=None):
        match=by_name.get((code,name.casefold()));ident='trellis_county:'+code+':'+re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')
        return {'id':ident,'record_type':'trellis_county_coverage','state':code,'state_name':state_names[code],'county':name,
                'fips':match['geoid'] if match else None,'fips_basis':'exact county name + state match against the county inventory' if match else None,
                'listed_in_state_coverage':listed,'courthouse_count_reported':count,'has_documents':docs,
                'detail_captured':False,'publisher_reported':None,'courthouses_reported':None,'courthouse_count_detail':None,
                'practice_areas':None,'practice_area_presence':None,'has_documents_detail':None,'venue':None,
                'receipts':{'state':receipt if listed else None,'county':None,'browser':[]},'detail_sources':[],
                'qualification':LABEL,'license_ref':LABEL,**temporal(at,basis)}
    scout=ROOT/'reports/corpus_upgrade_20260919/understand/connector_samples'
    nj=json.loads((scout/'13_trellis_get_state_coverage_nj.response.json').read_text(encoding='utf8'))['result'][0]['text'].encode('utf8')
    for path in sorted((HERE/'raw').glob('*get_state_coverage*.response.json')):
        event=logby[path.name];code=event['arguments']['state'].upper();d=json.loads(path.read_text(encoding='utf8'))
        kind='agent-transcribed structured connector payload; not original HTTP bytes'
        if code=='NJ' and path.read_bytes()==nj:kind='connector text payload matching saved verbatim scout wrapper; not original HTTP bytes'
        rec=make_receipt(path,'tc-%04d'%event['seq'],'get_state_coverage',event['arguments'],event['called_at_utc'],event['called_at_basis'],kind)
        state_counties=d.get('counties') or []
        states[code]={'id':'trellis_state:'+code,'record_type':'trellis_state_coverage','state':code,'state_name':state_names[code],
            'coverage_flags':d.get('coverage_flags'),'counties_listed':len(state_counties),'counties_with_documents':sum(r['has_documents'] is True for r in state_counties),
            'courthouse_count_total_reported':sum(r['courthouse_count'] for r in state_counties),'connector_error':d.get('error'),
            'receipt':rec['id'],'qualification':LABEL,'license_ref':LABEL,**temporal(rec['called_at_utc'],rec['called_at_basis'])}
        for c in state_counties:
            key=(code,c['county_name'].casefold())
            if key in counties:raise ValueError('Duplicate state/county in saved responses')
            counties[key]=county_row(code,c['county_name'],rec['id'],c['courthouse_count'],c['has_documents'],at=rec['called_at_utc'],basis=rec['called_at_basis'])
    original=scout/'14_trellis_get_county_coverage_nj_middlesex.response.json'
    copied=HERE/'raw/scout_county_nj_middlesex.wrapper.json'
    if not copied.exists():shutil.copyfile(original,copied)
    if sha(copied)!=sha(original):raise ValueError('Changed scout evidence')
    d=json.loads(json.loads(copied.read_text(encoding='utf8'))['result'][0]['text'])
    rec=make_receipt(copied,'tc-scout-middlesex','get_county_coverage',{'state':'nj','county':'Middlesex'},None,'No per-call timestamp retained in saved wrapper','saved connector wrapper/text response; not original HTTP bytes')
    row=counties[('NJ',d['county_name'].casefold())]
    row.update(detail_captured=True,publisher_reported={**d['metadata'],**d['contact']},courthouses_reported=d['courthouses'],
        courthouse_count_detail=len(d['courthouses']),practice_areas=d['practice_areas'],practice_area_presence={a:True for a in d['practice_areas']},
        has_documents_detail=d['has_documents'],detail_capture_kind='connector_county_response',**temporal(None,'No per-call timestamp retained in county-detail wrapper'))
    row['receipts']['county']=rec['id'];row['detail_sources'].append({'kind':'connector_county_response','receipt':rec['id'],'captured_at':None})
    skipped=[];browser_count=0;provider_count=0;provider_recovery_count=0;provider_unresolved=[];provider_evidence=[];provider_checkpoint=None;provider_attempts={}
    for name in BROWSER_FOLDERS:
        folder=ROOT/'sources/counties'/name
        if not (folder/'resources.jsonl').is_file():continue
        gate=json.loads((folder/'validation.json').read_text(encoding='utf8'))
        if gate.get('status')!='passed':skipped.append(name);continue
        if sha(folder/'resources.jsonl')!=gate.get('resources_sha256'):raise ValueError('Browser manifest hash mismatch')
        if any(gate.get(k) is not True for k in ('directory_integration_ready_with_dom_qualification','county_name_state_and_observed_parent_links_verified','source_overlap_dedup_verified')):raise ValueError('Browser scope validation missing')
        inputs.append({'path':(folder/'resources.jsonl').relative_to(ROOT).as_posix(),'sha256':sha(folder/'resources.jsonl')})
        for r in rows(folder/'resources.jsonl'):
            if len(r.get('county_geoids',[]))!=1:raise ValueError('Ambiguous browser county')
            match=by_fips.get(r['county_geoids'][0])
            if not match or (r['state'],r['county'])!=(match['state'],match['name']):raise ValueError('Browser county inventory mismatch')
            for field,hashkey in (('raw_path','sha256'),('text_path','text_sha256'),('metadata_path','metadata_sha256')):
                p=(ROOT/r[field]).resolve()
                if not p.is_relative_to(folder.resolve()) or sha(p)!=r[hashkey]:raise ValueError('Unbound browser artifact')
            dom=json.loads((ROOT/r['raw_path']).read_text(encoding='utf8'));meta=json.loads((ROOT/r['metadata_path']).read_text(encoding='utf8'))
            if dom.get('source_url')!=r['source_url'] or meta.get('source_url')!=r['source_url'] or meta!=r.get('metadata') or r.get('raw_representation_kind')!='rendered_dom_json_not_http':raise ValueError('Browser DOM identity mismatch')
            if meta.get('county_geoid')!=match['geoid'] or (meta.get('state'),meta.get('county'))!=(match['state'],match['name']):raise ValueError('Browser metadata geography mismatch')
            if any(meta.get(k) is not False for k in ('paid_document_entitlement_verified','protected_case_fields_scraped','case_lists_included')):raise ValueError('Unqualified browser extraction scope')
            if meta.get('source_representation_sha256')!=r['sha256'] or meta.get('text_sha256')!=r['text_sha256'] or meta.get('captured_at')!=r['captured_at']:raise ValueError('Browser metadata hash/date mismatch')
            code=match['usps'];key=(code,match['name'].casefold())
            copyto=HERE/'browser_evidence'/name/Path(r['raw_path']).name;copyto.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/r['raw_path'],copyto)
            ident='tc-browser-'+hashlib.sha256(r['source_url'].encode()).hexdigest()[:16]
            rec=make_receipt(copyto,ident,'browser_county_profile',{'source_url':r['source_url']},r['captured_at'],'Browser tool observation timestamp','rendered DOM heading/link representation; not original HTTP bytes')
            if code not in states:states[code]={'id':'trellis_state:'+code,'record_type':'trellis_state_browser_context','state':code,'state_name':match['state'],
                'coverage_flags':None,'counties_listed':None,'counties_with_documents':None,'courthouse_count_total_reported':None,'connector_error':None,'receipt':None,
                'context_note':'County browser details only; no saved state-coverage response','qualification':LABEL,'license_ref':LABEL,**temporal()}
            if key not in counties:counties[key]=county_row(code,match['name'],None,listed=None)
            row=counties[key];reported=browser_fields(dom)
            if not row['detail_captured']:
                row.update(detail_captured=True,publisher_reported=reported,detail_capture_kind='rendered_dom_browser_profile',**temporal(r['captured_at'],'Browser tool observation timestamp'))
            row['receipts']['browser'].append(ident)
            row['detail_sources'].append({'kind':'rendered_dom_browser_profile','receipt':ident,'source_url':r['source_url'],'captured_at':r['captured_at'],'publisher_reported':reported,
                'qualification':'Observed publisher profile fields and hrefs; website authority, field currency and court territory not independently verified.'})
            browser_count+=1
            saved_profile_urls.add(r['source_url'])
    snapshot=provider_snapshot()
    if snapshot is None and (PROVIDER_FOLDER/'resources.jsonl').exists():
        raise ValueError('Provider checkpoint is not stable/ready; retry rather than publish reduced coverage')
    if snapshot:
        provider_gate,provider_rows,gate_bytes,manifest_bytes=snapshot
        checkpoint=HERE/'provider_evidence/checkpoints'/provider_gate['manifest_sha256']
        checkpoint.mkdir(parents=True,exist_ok=True)
        for name,payload in (('validation.json',gate_bytes),('resources.jsonl',manifest_bytes)):
            target=checkpoint/name;target.write_bytes(payload);provider_evidence.append(target)
        provider_checkpoint={'records':len(provider_rows),'validated_at':provider_gate.get('validated_at'),'complete':provider_gate.get('complete') is True,
                             'manifest_sha256':provider_gate['manifest_sha256'],'validation_sha256':hashlib.sha256(gate_bytes).hexdigest()}
        finalpath=PROVIDER_FOLDER/'final_receipt.json'
        if finalpath.is_file():
            finalbytes=finalpath.read_bytes();final=json.loads(finalbytes)
            if final.get('status')=='passed' and final.get('resources_sha256')==provider_gate['manifest_sha256'] and final.get('validation_sha256')==hashlib.sha256(gate_bytes).hexdigest():
                attemptbytes=(PROVIDER_FOLDER/'attempts.jsonl').read_bytes()
                if hashlib.sha256(attemptbytes).hexdigest()!=final.get('attempts_sha256'):raise ValueError('Final provider attempt receipt hash mismatch')
                provider_checkpoint['queue_exhausted']=final.get('queue_exhausted') is True
                for payload,name in ((finalbytes,'final_receipt.json'),(attemptbytes,'attempts.jsonl')):
                    target=checkpoint/name;target.write_bytes(payload);provider_evidence.append(target)
                provider_attempts={r['url']:r for r in (json.loads(l) for l in attemptbytes.decode('utf-8-sig').splitlines() if l.strip())}
        inputs.append({'path':(PROVIDER_FOLDER/'resources.jsonl').relative_to(ROOT).as_posix(),'sha256':provider_gate['manifest_sha256'],'checkpoint':provider_checkpoint})
        row_gates={r['source_url']:provider_gate['manifest_sha256'] for r in provider_rows}
        recovery_snapshot=provider_snapshot(PROVIDER_RECOVERY_FOLDER)
        if recovery_snapshot is None and (PROVIDER_RECOVERY_FOLDER/'resources.jsonl').exists():
            raise ValueError('Recovery checkpoint is not stable/ready; retry rather than discard saved recovery')
        if recovery_snapshot:
            recovery_gate,recovery_rows,recovery_gate_bytes,recovery_manifest_bytes=recovery_snapshot
            if set(row_gates)&{r['source_url'] for r in recovery_rows}:raise ValueError('Offline recovery duplicates an accepted main profile URL')
            recovery_checkpoint=HERE/'provider_evidence/checkpoints'/recovery_gate['manifest_sha256'];recovery_checkpoint.mkdir(parents=True,exist_ok=True)
            for name,payload in (('validation.json',recovery_gate_bytes),('resources.jsonl',recovery_manifest_bytes)):
                target=recovery_checkpoint/name;target.write_bytes(payload);provider_evidence.append(target)
            inputs.append({'path':(PROVIDER_RECOVERY_FOLDER/'resources.jsonl').relative_to(ROOT).as_posix(),'sha256':recovery_gate['manifest_sha256']})
            row_gates.update({r['source_url']:recovery_gate['manifest_sha256'] for r in recovery_rows})
            provider_rows.extend(recovery_rows);provider_recovery_count=len(recovery_rows)
            provider_checkpoint['offline_recovery_records']=provider_recovery_count
            provider_checkpoint['offline_recovery_manifest_sha256']=recovery_gate['manifest_sha256']
        db=sqlite3.connect((ROOT/'sources/trellis/catalog/catalog.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        parent_cache={};queue_cache={};proof_evidence=[]
        try:
            for r in provider_rows:
                artifacts={}
                for field,digest in (('raw_path','raw_sha256'),('text_path','text_sha256'),('html_path','html_sha256')):
                    p=(ROOT/r[field]).resolve()
                    allowed_roots=[PROVIDER_FOLDER.resolve()]
                    if row_gates[r['source_url']]!=provider_gate['manifest_sha256']:allowed_roots.append(PROVIDER_RECOVERY_FOLDER.resolve())
                    if not any(p.is_relative_to(root) for root in allowed_roots) or not p.is_file():raise ValueError('Provider artifact outside its collection')
                    payload=p.read_bytes()
                    if hashlib.sha256(payload).hexdigest()!=r[digest]:raise ValueError('Provider artifact hash mismatch')
                    artifacts[field]=payload
                raw=json.loads(artifacts['raw_path']);meta=raw.get('metadata') or {};url=r['source_url']
                if r.get('url')!=url or meta.get('sourceURL')!=url or meta.get('url')!=url or meta.get('statusCode')!=200 or len(artifacts['raw_path'])!=r.get('raw_bytes'):raise ValueError('Provider response identity/status mismatch')
                if artifacts['text_path'].decode('utf-8').replace('\r\n','\n')!=(raw.get('markdown') or '').replace('\r\n','\n') or artifacts['html_path'].decode('utf-8').replace('\r\n','\n')!=(raw.get('html') or '').replace('\r\n','\n'):raise ValueError('Provider derivative does not match captured response')
                heading,fields,website,reported=provider_section(raw)
                if heading!=r.get('heading') or any(r.get('fields',{}).get(k)!=v for k,v in fields.items()) or r.get('website_url')!=website:raise ValueError('Provider parsed section differs from captured evidence')
                saved_profile_urls.add(url)
                queuepath=(ROOT/r['source_queue_path']).resolve()
                if not queuepath.is_relative_to(ROOT):raise ValueError('Provider queue outside workspace')
                if queuepath not in queue_cache:
                    qbytes=queuepath.read_bytes();queue_cache[queuepath]=(hashlib.sha256(qbytes).hexdigest(),{x['url'] for x in (json.loads(l) for l in qbytes.decode('utf-8-sig').splitlines() if l.strip())})
                if queue_cache[queuepath][0]!=r['source_queue_sha256'] or url not in queue_cache[queuepath][1]:raise ValueError('Provider URL not in the bound observed queue')
                proofs=[]
                preferred=r.get('observed_parent') or {}
                for proof in db.execute("SELECT source_url,url,label FROM links WHERE url=? AND category='coverage_county' ORDER BY CASE WHEN source_url=? AND label=? THEN 0 ELSE 1 END, length(label),source_url",(url,preferred.get('source_url'),preferred.get('label'))):
                    proof=dict(proof);parent=proof['source_url']
                    if parent not in parent_cache:
                        page=db.execute('SELECT source_path,content_sha256 FROM pages WHERE url=? AND status=200',(parent,)).fetchone()
                        p=(ROOT/page['source_path']).resolve() if page and page['source_path'] else None
                        parent_cache[parent]={'parent_raw_path':page['source_path'],'parent_raw_sha256':sha(p),'catalog_content_sha256':page['content_sha256']} if p and p.is_relative_to(ROOT) and p.is_file() else None
                    if parent_cache[parent]:
                        proofs.append({**proof,**parent_cache[parent]})
                        break
                if not proofs:raise ValueError('No saved observed parent evidence for provider URL')
                identity=provider_identity(url,heading,meta,proofs,inventory);code=identity['state'];key=(code,identity['county'].casefold())
                proof_evidence.append({'source_url':url,'provider_sha256':r['raw_sha256'],'identity':identity,'parent_links':proofs,'queue_sha256':r['source_queue_sha256']})
                copyto=HERE/'provider_evidence/raw'/(r['raw_sha256']+'.json');copyto.parent.mkdir(parents=True,exist_ok=True)
                if not copyto.exists():copyto.write_bytes(artifacts['raw_path'])
                if sha(copyto)!=r['raw_sha256']:raise ValueError('Copied provider evidence mismatch')
                ident='tc-firecrawl-'+hashlib.sha256((url+'\n'+r['raw_sha256']).encode()).hexdigest()[:24]
                rec=make_receipt(copyto,ident,'firecrawl_selected_county_profile',{'source_url':url},r['captured_at'],r.get('captured_at_basis'),'Firecrawl selected rendered county profile section; provider JSON is not original HTTP bytes')
                rec.update(provider=r['provider'],provider_manifest_sha256=row_gates[url],source_text_sha256=r['text_sha256'],source_html_sha256=r['html_sha256'],offline_recovery=row_gates[url]!=provider_gate['manifest_sha256'])
                if code not in states:states[code]={'id':'trellis_state:'+code,'record_type':'trellis_state_provider_context','state':code,'state_name':identity['state_name'],
                    'coverage_flags':None,'counties_listed':None,'counties_with_documents':None,'courthouse_count_total_reported':None,'connector_error':None,'receipt':None,
                    'context_note':'County provider details only; no saved state-coverage response','qualification':LABEL,'license_ref':LABEL,**temporal()}
                if key not in counties:counties[key]=county_row(code,identity['county'],None,listed=None)
                row=counties[key]
                # The generic name match is insufficient for provider-only rows: retain the stricter evidence decision.
                if row['listed_in_state_coverage'] is None and not row['detail_captured']:
                    row['fips']=identity['fips'];row['fips_basis']=identity['basis']
                if identity['fips'] is None:
                    provider_unresolved.append({'source_url':url,'receipt':ident,**identity})
                    # Do not attach insufficiently corroborated details to an independently resolved county.
                    if row['fips'] is not None:continue
                if not row['detail_captured']:
                    row.update(detail_captured=True,publisher_reported=reported,detail_capture_kind='firecrawl_selected_profile',**temporal(r['captured_at'],r.get('captured_at_basis')))
                row['receipts'].setdefault('firecrawl',[]).append(ident)
                row['detail_sources'].append({'kind':'firecrawl_selected_profile','receipt':ident,'source_url':url,'captured_at':r['captured_at'],'publisher_reported':reported,
                    'identity_basis':identity['basis'],'observed_parent':proofs[0],'profile_heading':heading,'provider':r['provider'],
                    'qualification':'Publisher-reported selected profile section. Provider representation, not original HTTP; administration address is not a verified court address. Current accuracy and document entitlement are unverified.'})
                provider_count+=1
        finally:db.close()
        proofpath=checkpoint/'identity_evidence.jsonl';writel(proofpath,proof_evidence);provider_evidence.append(proofpath)
    # Historical catalog profiles are lower priority than current browser/provider details.
    catalog_count=0;catalog_states=0;catalog_gaps=[];catalog_evidence=[]
    db=sqlite3.connect((ROOT/'sources/trellis/catalog/catalog.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    state_slugs={r['state'].lower().replace(' ','-'):r['usps'] for r in inventory}
    from bs4 import BeautifulSoup
    try:
        catalog_rows=[dict(r) for r in db.execute("SELECT c.*,p.source_path,p.content_sha256,p.observed_at,p.provider,p.status FROM counties c JOIN pages p ON p.url=c.url WHERE p.status=200 ORDER BY c.url")]
        for saved in catalog_rows:
            url=saved['url'];pieces=urlsplit(url).path.strip('/').split('/')
            if len(pieces)!=3 or pieces[0]!='coverage' or pieces[1] not in state_slugs:
                catalog_gaps.append({'source_url':url,'reason':'Not a valid observed state/county profile URL'});continue
            p=(ROOT/saved['source_path']).resolve()
            if not p.is_relative_to(ROOT/'sources/trellis') or not p.is_file():raise ValueError('Missing catalog profile evidence')
            payload=p.read_bytes();digest=hashlib.sha256(payload).hexdigest()
            if digest!=saved['content_sha256']:raise ValueError('Historical catalog original hash mismatch')
            raw=json.loads(payload);meta=raw.get('metadata') or {}
            if meta.get('sourceURL')!=url or meta.get('statusCode')!=200 or meta.get('url',url)!=url:raise ValueError('Historical catalog URL/status mismatch')
            saved_profile_urls.add(url)
            headings=[BeautifulSoup(h,'html.parser').get_text(' ',strip=True) for h in re.findall(r'<h1\b[^>]*>(.*?)</h1\s*>',raw.get('html') or '',re.I|re.S)]
            heading=next((h for h in headings if 'Records' in h),None)
            if not heading:
                catalog_gaps.append({'source_url':url,'reason':'No captured county profile heading; original remains available'});continue
            proofs=[dict(x) for x in db.execute("SELECT MIN(source_url) source_url,url,label FROM links WHERE url=? AND category='coverage_county' GROUP BY label ORDER BY length(label),label",(url,))]
            identity=provider_identity(url,heading,meta,proofs,inventory)
            code=identity['state'];key=(code,identity['county'].casefold())
            original_fields=json.loads(saved['fields_json'] or '{}');reported=browser_fields({})
            for field,value in original_fields.items():
                normal=field.replace(' ','_');dest={'county_seat':'seat','meaning':'name_meaning','board_of_supervisors':'board_of_supervisor'}.get(normal,normal)
                if dest in reported:reported[dest]=value
            reported['website']=saved['official_website'] if saved.get('website_validation_status')=='observed_href' else None
            observed_website=json.loads(saved.get('website_provenance_json') or '{}')
            if reported['website'] and not any(x.get('url')==reported['website'] and x.get('observed_href') for x in observed_website.get('candidates',[])):raise ValueError('Unbound historical website href')
            evidence={'representation':'Extracted historical catalog profile; not original provider response or original HTTP bytes',
                'source_url':url,'original_source_path':saved['source_path'],'original_source_sha256':digest,'original_bytes':len(payload),
                'original_verified_at':datetime.now(timezone.utc).isoformat(),'observed_at':saved['observed_at'],
                'catalog_fields':original_fields,'publisher_reported':reported,'profile_heading':heading,'identity':identity,
                'observed_parent_links':proofs,'website_provenance':observed_website,'source_currency_verified':False}
            target=HERE/'catalog_evidence'/(digest+'.json');target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                previous=json.loads(target.read_text(encoding='utf8'))
                evidence['original_verified_at']=previous.get('original_verified_at',evidence['original_verified_at'])
            write(target,evidence)
            ident='tc-catalog-'+hashlib.sha256(url.encode()).hexdigest()[:24]
            rec=make_receipt(target,ident,'saved_catalog_profile_extraction',{'source_url':url},saved['observed_at'],'Historical catalog observation timestamp; source currency unknown',evidence['representation'])
            rec['original_source_sha256']=digest;rec['original_bytes']=len(payload)
            if code not in states:states[code]={'id':'trellis_state:'+code,'record_type':'trellis_state_catalog_context','state':code,'state_name':identity['state_name'],
                'coverage_flags':None,'counties_listed':None,'counties_with_documents':None,'courthouse_count_total_reported':None,'connector_error':None,'receipt':None,
                'context_note':'Saved county profiles only; connector state coverage not established','qualification':LABEL,'license_ref':LABEL,**temporal()}
            if key not in counties:counties[key]=county_row(code,identity['county'],None,listed=None)
            row=counties[key]
            if row['listed_in_state_coverage'] is None and not row['detail_captured']:
                row['fips']=identity['fips'];row['fips_basis']=identity['basis']
            if not identity['fips']:
                catalog_gaps.append({'source_url':url,**identity})
                if row['fips'] is not None:continue
            if not row['detail_captured']:
                row.update(detail_captured=True,publisher_reported=reported,detail_capture_kind='historical_catalog_profile',**temporal(saved['observed_at'],'Historical catalog observation timestamp; not a legal effective date'))
            row['receipts'].setdefault('catalog',[]).append(ident)
            row['detail_sources'].append({'kind':'historical_catalog_profile','receipt':ident,'source_url':url,'captured_at':saved['observed_at'],
                'publisher_reported':reported,'identity_basis':identity['basis'],'profile_heading':heading,'original_source_sha256':digest,
                'qualification':'Previously saved publisher profile fields; original provider response retained in archive. Field currency, website authority and court address not independently verified.'})
            catalog_count+=1
        for saved in db.execute("SELECT * FROM pages WHERE category='coverage_state' AND status=200 ORDER BY url"):
            saved=dict(saved);url=saved['url'];parts=urlsplit(url).path.strip('/').split('/')
            if len(parts)!=2 or parts[0]!='coverage' or parts[1] not in state_slugs:continue
            code=state_slugs[parts[1]];p=(ROOT/saved['source_path']).resolve()
            if not p.is_relative_to(ROOT/'sources/trellis'):raise ValueError('State original outside source scope')
            payload=p.read_bytes();digest=hashlib.sha256(payload).hexdigest()
            if digest!=saved['content_sha256']:raise ValueError('State original hash mismatch')
            raw=json.loads(payload);meta=raw.get('metadata') or {}
            if meta.get('sourceURL')!=url or meta.get('statusCode')!=200:raise ValueError('State original identity mismatch')
            evidence={'representation':'Historical state page metadata extracted from saved provider JSON; not original HTTP bytes',
                'source_url':url,'title':' '.join(str(meta.get('title') or '').split()),'original_source_path':saved['source_path'],
                'original_source_sha256':digest,'original_bytes':len(payload),'observed_at':saved['observed_at'],'coverage_flags':None}
            target=HERE/'catalog_evidence'/('state-'+code+'.json');write(target,evidence)
            ident='tc-catalog-state-'+code
            make_receipt(target,ident,'saved_catalog_state_page',{'source_url':url},saved['observed_at'],'Historical catalog observation timestamp; source currency unknown',evidence['representation'])
            if code not in states:states[code]={'id':'trellis_state:'+code,'record_type':'trellis_state_catalog_context','state':code,'state_name':state_names[code],
                'coverage_flags':None,'counties_listed':None,'counties_with_documents':None,'courthouse_count_total_reported':None,'connector_error':None,'receipt':None,
                'context_note':'Saved state page; connector coverage flags unknown','qualification':LABEL,'license_ref':LABEL,**temporal()}
            states[code]['saved_state_page']={'source_url':url,'receipt':ident,'captured_at':saved['observed_at'],'original_source_sha256':digest}
            catalog_states+=1
        known_urls={r[0] for r in db.execute("SELECT DISTINCT url FROM links WHERE category='coverage_county'")}|{r['url'] for r in catalog_rows}
    finally:db.close()
    excluded_artifacts={
        'https://trellis.law/coverage/texas/harrisoncountytexas.org':{'parent_url':'https://trellis.law/coverage/texas/harrison','label':'harrisoncountytexas.org'},
        'https://trellis.law/coverage/pennsylvania/www.alleghenycounty.us':{'parent_url':'https://trellis.law/coverage/pennsylvania/allegheny','label':'www.alleghenycounty.us'},
        'https://trellis.law/coverage/pennsylvania/www.co.delaware.pa.us':{'parent_url':'https://trellis.law/coverage/pennsylvania/delaware','label':'www.co.delaware.pa.us'},
    }
    def valid_county_url(url):
        parsed=urlsplit(url);pieces=parsed.path.strip('/').split('/')
        return parsed.scheme=='https' and parsed.netloc=='trellis.law' and not parsed.query and not parsed.fragment and len(pieces)==3 and pieces[0]=='coverage' and pieces[1] in state_slugs and url not in excluded_artifacts
    known_urls={u for u in known_urls if valid_county_url(u)}
    saved_profile_urls={u for u in saved_profile_urls if valid_county_url(u)}
    remaining=known_urls-saved_profile_urls;by_state=[]
    variant_reviews=[]
    db=sqlite3.connect((ROOT/'sources/trellis/catalog/catalog.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        for url in sorted(remaining):
            if provider_attempts.get(url,{}).get('reason')!='provider_http_404':continue
            code=state_slugs[urlsplit(url).path.strip('/').split('/')[1]]
            proofs=[];matches={}
            for proof in db.execute('SELECT MIN(source_url) source_url,label FROM links WHERE url=? GROUP BY label',(url,)):
                proof=dict(proof);name=re.sub(r'\s+Dockets$','',proof['label'] or '',flags=re.I)
                match=by_name.get((code,name.casefold()))
                if not match:continue
                source=db.execute('SELECT source_path,content_sha256 FROM pages WHERE url=? AND status=200',(proof['source_url'],)).fetchone()
                if not source:continue
                p=(ROOT/source['source_path']).resolve()
                if not p.is_relative_to(ROOT/'sources/trellis') or sha(p)!=source['content_sha256']:raise ValueError('404 identity parent evidence mismatch')
                proofs.append({**proof,'source_sha256':source['content_sha256']});matches[match['geoid']]=match
            if len(matches)!=1:continue
            fips,match=next(iter(matches.items()))
            alternatives=[r for r in counties.values() if r.get('fips')==fips and r['detail_captured']]
            alternate_urls=sorted({s['source_url'] for r in alternatives for s in r['detail_sources'] if s.get('source_url') in saved_profile_urls})
            variant_reviews.append({'unsaved_url':url,'state':code,'county':match['name'],'fips':fips,'observed_parent_links':proofs,
                'alternative_profile_detail_saved':bool(alternatives),'alternate_saved_urls':alternate_urls,
                'basis':'Exact observed parent label (only trailing Dockets removed) plus state matched Census; alternate profile independently resolves to the same FIPS. The failed URL is still unsaved.'})
    finally:db.close()
    for slug,code in sorted(state_slugs.items(),key=lambda x:x[1]):
        known={u for u in known_urls if urlsplit(u).path.split('/')[2]==slug}
        saved=known&saved_profile_urls
        if not known:continue
        by_state.append({'state':code,'state_name':state_names[code],'observed_county_urls':len(known),'saved_county_urls':len(saved),
            'remaining_county_urls':len(known-saved),'remaining_urls':sorted(known-saved),'saved_percent':round(100*len(saved)/len(known),2),
            'failed_url_variant_identity_reviews':[r for r in variant_reviews if r['state']==code],
            'failed_url_variants_with_saved_identity_details':sum(r['state']==code and r['alternative_profile_detail_saved'] for r in variant_reviews),
            'failed_url_variants_without_saved_identity_details':sum(r['state']==code and not r['alternative_profile_detail_saved'] for r in variant_reviews),
            'unique_resolved_fips':len({r['fips'] for r in counties.values() if r['state']==code and r.get('fips') and r['detail_captured']})})
    progress={'available':True,'as_of':datetime.now(timezone.utc).isoformat(),'observed_county_urls':len(known_urls),'saved_county_urls':len(known_urls&saved_profile_urls),
        'remaining_county_urls':len(remaining),'remaining_urls':sorted(remaining),'saved_percent':round(100*len(known_urls&saved_profile_urls)/len(known_urls),2),
        'unique_resolved_fips':len({r['fips'] for r in counties.values() if r.get('fips') and r['detail_captured']}),
        'saved_urls_outside_observed_inventory':sorted(saved_profile_urls-known_urls),'excluded_website_link_artifacts':sorted(excluded_artifacts),'states':by_state,
        'excluded_website_link_evidence':[{'url':u,**p,'reason':'Observed website-domain link resolved beneath county path; not a county profile'} for u,p in sorted(excluded_artifacts.items())],
        'remaining_attempts':[{'source_url':u,**{k:provider_attempts.get(u,{}).get(k) for k in ('status','reason','completed_at')}} for u in sorted(remaining)],
        'failed_url_variant_identity_reviews':variant_reviews,
        'failed_url_variants_with_saved_identity_details':sum(r['alternative_profile_detail_saved'] for r in variant_reviews),
        'failed_url_variants_without_saved_identity_details':sum(not r['alternative_profile_detail_saved'] for r in variant_reviews),
        'provider_checkpoint':provider_checkpoint,
        'qualification':'Distinct observed Trellis county-profile URLs with validated saved responses; not Census county coverage, saved case documents, or complete profile fields. Connecticut former counties and court venues may have no current county GEOID.'}
    write(HERE/'progress.json',progress)
    for r in counties.values():
        if r['fips']:edges.append({'from':{'type':'fips','id':'fips:'+r['fips']},'to':{'type':'state','id':'state:'+r['state']},'relation':'county_in_state','basis':r['fips_basis'],'evidence':r['receipts']})
        else:unresolved.append({'state':r['state'],'county':r['county'],'reason':'No exact county name + state match in saved inventory; no guessed GEOID'})
    state_rows=sorted(states.values(),key=lambda r:r['state']);county_rows=sorted(counties.values(),key=lambda r:(r['state'],r['county']))
    for collection in (state_rows,county_rows,receipts):
        if len({r['id'] for r in collection})!=len(collection):raise ValueError('Normalized identity collision')
    for name,data in (('states',state_rows),('counties',county_rows),('receipts',receipts),('edges',edges),('unresolved',unresolved)):writel(HERE/(name+'.jsonl'),data)
    write(HERE/'gaps.json',{'missing_logged_responses':missing,'skipped_unready_browser_batches':skipped,'unknown_county_identity':unresolved,'provider_identity_unresolved':provider_unresolved,'historical_catalog_gaps':catalog_gaps})
    files=[]
    for name,count in (('states.jsonl',len(states)),('counties.jsonl',len(counties)),('receipts.jsonl',len(receipts)),('edges.jsonl',len(edges)),('unresolved.jsonl',len(unresolved)),('gaps.json',1),('progress.json',1)):
        files.append({'path':name,'sha256':sha(HERE/name),'rows':count})
    for rec in receipts:files.append({'path':rec['response_path'],'sha256':rec['response_sha256'],'rows':1})
    for p in provider_evidence:files.append({'path':p.relative_to(HERE).as_posix(),'sha256':sha(p),'rows':1})
    files.append({'path':'raw/_call_log.jsonl','sha256':sha(HERE/'raw/_call_log.jsonl'),'rows':len(log)})
    gate={'schema_version':'1','status':'passed','ready':True,'validated_at':datetime.now(timezone.utc).isoformat(),'data_files':files,
        'counts':{'states':len(states),'state_coverage_responses':27,'counties':len(counties),'county_details':sum(r['detail_captured'] for r in county_rows),
                  'browser_county_details':browser_count,'county_details_connector':1,'firecrawl_county_details':provider_count,'offline_provider_recovery_details':provider_recovery_count,'firecrawl_unresolved_associations':len(provider_unresolved),
                  'historical_catalog_county_details':catalog_count,'historical_saved_state_pages':catalog_states,'historical_catalog_gaps':len(catalog_gaps),'unmatched_county_names':len(unresolved),'logged_responses_missing':len(missing)},
        'provider_checkpoint':provider_checkpoint,
        'qualification':LABEL,'license_ref':LABEL,'checks':['saved inputs only; no API calls','exact state and county inventory joins only','raw/transcribed/DOM source kinds retained','manifest and all listed files SHA-bound','unknown dates and coverage values remain null'],
        'inputs':[{'path':'delivery/focused_legal_corpus/counties/counties.jsonl','sha256':sha(ROOT/'delivery/focused_legal_corpus/counties/counties.jsonl')},*inputs],
        'limitations':['Coverage flags are publisher reports, not saved-document availability.','27 state responses include an Alabama error; four logged calls have no response files.','Browser-only state contexts do not establish state coverage.','Source dates and legal effective dates are unknown unless separately provided.']}
    write(HERE/'validation.json',gate);print(json.dumps(gate['counts']))

if __name__=='__main__':main()
