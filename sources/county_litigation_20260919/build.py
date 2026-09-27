"""Offline litigation resource normalization. All source collections remain read-only."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
import argparse,hashlib,json,mimetypes,re,shutil,sqlite3,sys,itertools,os
from urllib.parse import urlsplit,urljoin,urldefrag

def safe_target(base,href):
    # A malformed href (e.g. a bracketed pseudo-host) makes urllib raise; such a link simply never matches.
    try:return urldefrag(urljoin(base,href))[0]
    except ValueError:return None
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(ROOT/'delivery/archive-directory'))
sys.path.insert(0,str(ROOT/'scripts'))
from classify import classify,classify_link,COURT,VERSION
from readable import reading_view
from county_litigation_sections import extract_structure
import trellis_coverage
COLLECTIONS=['corpus/county_local_documents_20260914','corpus/county_local_rules_washington_20260914','corpus/county_local_backfill_20260918','corpus/county_local_backfill_20260918T2213']
_MATCHERS=None
def stamp():return datetime.now(timezone.utc).isoformat()
def sha(data):return hashlib.sha256(data).hexdigest()
def readl(path):return [json.loads(l) for l in path.read_text(encoding='utf-8-sig').splitlines() if l.strip()]
def atomic_bytes(path,data):
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp');temporary.write_bytes(data);temporary.replace(path)
def write(path,value):atomic_bytes(path,(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
def writel(path,rows):atomic_bytes(path,''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode('utf8'))
def readbound(path,digest,root):
    p=path.resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():raise ValueError('Source artifact outside scope or missing: '+str(path))
    data=p.read_bytes()
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or sha(data)!=digest:raise ValueError('Source artifact hash mismatch: '+str(path))
    return data
def normurl(url):
    p=urlsplit(url);return (p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/') or '/',p.query)
def public_url(url):
    try:p=urlsplit(url);return p.scheme in ('http','https') and bool(p.hostname) and not p.username and not p.password
    except (ValueError,TypeError):return False

def geography(title,text,contexts,inventory,website_map,url,principal_text=None):
    global _MATCHERS
    if _MATCHERS is None:
        _MATCHERS=[(r,re.compile(r'\b'+re.escape(r['state'])+r'\b',re.I),[(v,re.compile(r'\b'+re.escape(v)+r'\b',re.I)) for v in [r['name']]+(['County of '+r['name'][:-7]] if r['name'].endswith(' County') else [])]) for r in inventory]
    header=title+'\n'+text[:1800];fold=' '.join(header.casefold().split());found=[]
    identity=title+'\n'+(principal_text or '')
    states={r['state'] for r in inventory if r['state'].casefold() in fold or re.search(r',\s*'+r['usps']+r'\s+\d{5}\b',header) or re.search(r'\b(?:County|Parish|Borough|City)\s*[,|–-]?\s+'+r['usps']+r'\b',title)}
    for r,sp,alternates in _MATCHERS:
        if r['state'] not in states:continue
        state_explicit=sp.search(header) or re.search(r',\s*'+r['usps']+r'\s+\d{5}\b',header) or re.search(r'\b(?:County|Parish|Borough|City)\s*[,|–-]?\s+'+r['usps']+r'\b',title)
        county_explicit=next((v for v,p in alternates if p.search(identity)),None)
        if state_explicit and county_explicit:
            found.append((r,county_explicit))
    # State/county words in broad navigation do not establish local applicability.
    if len(found)==1:
        r,name=found[0];m=re.search(re.escape(name),identity,re.I)
        sm=re.search(r'\b'+re.escape(r['state'])+r'\b',header,re.I) or re.search(r',\s*'+r['usps']+r'\s+\d{5}\b',header) or re.search(r'\b(?:County|Parish|Borough|City)\s*[,|–-]?\s+'+r['usps']+r'\b',title)
        proof={'source':'principal source title/H1 or opening document court caption','county_excerpt':identity[max(0,m.start()-100):m.end()+150],'state_excerpt':sm.group(0),'method':'exact principal county identity + explicit state name, title postal code, or postal-address state code; incidental body/table references excluded'}
        return r,{'level':'county' if COURT.search(header) else 'unknown','state':r['usps'],'county_fips':r['geoid'] if COURT.search(header) else None,'status':'explicit_source_text','evidence':[proof],'note':'Source explicitly identifies this county and state; government/court source association does not independently certify governing scope or legal currency.'}
    reported=website_map.get(normurl(url),[])
    if len(reported)==1:
        r=reported[0]
        return r,{'level':'unknown','state':r['usps'],'county_fips':None,'status':'discovery_association_only','evidence':[{'source':'validated Trellis county identity and exact reported website href','county_fips':r['geoid'],'url':url}],'note':'A county website association does not establish court territorial applicability.'}
    # Old FIPS hints alone are insufficient. An exact current website crosswalk,
    # original parent hash, and reproduced target href can establish discovery.
    from bs4 import BeautifulSoup
    parent_matches=[]
    for context in contexts:
        for p in (context.get('seed') or {}).get('provenance',[]):
            parents=website_map.get(normurl(p.get('parent_url') or ''),[])
            if len(parents)!=1 or not p.get('parent_raw_path') or not p.get('parent_raw_sha256'):continue
            try:parentraw=readbound(ROOT/p['parent_raw_path'],p['parent_raw_sha256'],ROOT)
            except (ValueError,OSError):continue
            soup=BeautifulSoup(parentraw,'html.parser');base=p.get('observed_link_base_url') or p['parent_url']
            anchor=next((a for a in soup.find_all('a',href=True) if safe_target(base,a['href'])==urldefrag(url)[0]),None)
            if anchor:
                parent_matches.append((parents[0],{'source':'validated Trellis website crosswalk plus hash-verified observed parent href','parent_url':p['parent_url'],'parent_sha256':p['parent_raw_sha256'],'target_url':url,'anchor':anchor.get_text(' ',strip=True),'county_fips':parents[0]['geoid']}))
    if len({r['geoid'] for r,_ in parent_matches})==1:
        r=parent_matches[0][0]
        return r,{'level':'unknown','state':r['usps'],'county_fips':None,'status':'verified_parent_discovery_association','evidence':[p for _,p in parent_matches],'note':'Exact observed link from a hash-verified source listed by a validated Trellis county profile; court authority and document territorial applicability remain unverified.'}
    # A WA official rule directory's exact court label is evidence distinct from
    # old crawl-context county hints. Keep municipality/multi-county labels unassigned.
    for context in contexts:
        seed=context.get('seed') or {};label=seed.get('court_label') or context.get('court_label');jur=context.get('jurisdiction') or {}
        if not label or urlsplit(url).hostname not in {'www.courts.wa.gov','courts.wa.gov'}:continue
        candidates=[r for r in inventory if r['usps']=='WA' and re.search(r'\b'+re.escape(r['name'])+r'\b',label,re.I)]
        if len(candidates)==1 and candidates[0]['geoid']==jur.get('geoid'):
            r=candidates[0];return r,{'level':'county','state':'WA','county_fips':r['geoid'],'status':'official_state_directory_court_label','court_name':label,'evidence':[{'source':'saved official WA court-rule directory seed label','excerpt':label,'seed_provenance':seed.get('provenance',[])}],'note':'Exact single county court label from the saved official state directory; present rule effectiveness is unknown.'}
    explicit_states=sorted({r['usps'] for r,sp,_ in _MATCHERS if r['state'] in states and sp.search(header)})
    state=explicit_states[0] if len(explicit_states)==1 else None
    if urlsplit(url).hostname in {'www.courts.wa.gov','courts.wa.gov'}:state='WA'
    return None,{'level':'unknown','state':state,'county_fips':None,'status':'unresolved','evidence':[],'note':'Old crawl-context county labels are retained as discovery hints only; no county inferred from URL or shared host.'}

def mime(data,declared=''):
    if data[:1024].lstrip().startswith(b'%PDF-'):return 'application/pdf','.pdf'
    if data.lstrip().startswith(b'{\\rtf'):return 'application/rtf','.rtf'
    if data[:8]==bytes.fromhex('d0cf11e0a1b11ae1'):return 'application/msword','.doc'
    if data[:2]==b'PK':return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.docx'
    if re.search(br'<(?:html|!doctype|head|body)',data[:2000],re.I):return 'text/html','.html'
    if declared=='application/json':return 'application/json','.json'
    return declared.split(';')[0] or 'application/octet-stream','.bin'

def save_artifact(data,mime_type,role,artifacts,extension):
    digest=sha(data);name=('text/' if role=='clean_text' else 'assets/')+digest+extension
    path=HERE/name;path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():path.write_bytes(data)
    if sha(path.read_bytes())!=digest:raise ValueError('Stored supplement artifact changed')
    row={'path':name,'sha256':digest,'bytes':len(data),'mime_type':mime_type,'mime':mime_type,'role':role};artifacts[name]=row;return row

def make_resource(source,inventory,website_map,artifacts):
    raw=source['raw'];original=source.get('text') or '';url=source['source_url'];title=' '.join((source.get('title') or url).split())
    original_title=title
    title=re.sub(r'\s+(?:Facebook\s+Twitter\s+Instagram\s+Youtube)\s*$','',title,flags=re.I).strip()
    view=reading_view(original,title=title,source_url=source.get('canonical_url') or url,raw_path=source.get('reader_path') or source['original_raw_path'])
    text=view['text'];mtype,ext=mime(raw,source.get('mime_type',''));classification=classify(title,text,source.get('canonical_url') or url,mtype,view['links'])
    negative=source.get('semantic_review') or {}
    if negative.get('reviewed') and any(word in (negative.get('actual_resource_kind') or '').lower() for word in ('election','quorum','business','permit','zoning','facility','recreation')):
        classification.update(resource_type='source_directory',document_shape='reviewed_non_litigation_reference',substantive=False,status='preserved_prior_negative_review',evidence=[{'field':'prior_hash_bound_semantic_review','value':negative}])
    if not text.strip():
        classification.update(document_shape='unparsed_document',substantive=False,status='needs_text_extraction',legal_status='unknown',legal_status_evidence=[])
    principal=source.get('principal_text') or ''
    if mtype=='application/pdf':
        opening=text[:1200].splitlines();captions=[]
        for i,line in enumerate(opening):
            if len(line)>200:continue
            if re.match(r'^\s*(?:LOCAL RULES\s*[–—:-]\s*)?(?:(?:IN\s+THE|THE)\s+)?(?:SUPERIOR|CIRCUIT|DISTRICT|MUNICIPAL|PROBATE|FAMILY|JUVENILE|COUNTY)\s+COURT\b',line,re.I):
                captions.append(line)
                for following in opening[i+1:i+4]:
                    if re.match(r'^\s*(?:FOR\s+THE\s+)?COUNTY\s+OF\s+[A-Z .-]+\s*$',following,re.I):captions.append(following)
        principal='\n'.join(captions)
    county,applicability=geography(title,text,source.get('contexts',[]),inventory,website_map,url,principal)
    if county is None and source.get('verified_parent_county'):
        county=next((r for r in inventory if r['geoid']==source['verified_parent_county']['geoid']),None)
        if county:
            applicability={'level':'unknown','state':county['usps'],'county_fips':None,'status':'verified_court_source_association_only','evidence':[source['verified_parent_county']],'note':'Exact observed link from a captured court page naming this county and state; document-specific territorial applicability remains unverified.'}
    rawart=save_artifact(raw,mtype,'source_snapshot' if source.get('representation') else 'original',artifacts,ext)
    textart=save_artifact(text.encode('utf-8'),'text/plain; charset=utf-8','clean_text',artifacts,'.txt')
    served_artifacts=[rawart,textart]
    if source.get('html_bytes'):
        served_artifacts.append(save_artifact(source['html_bytes'],'text/html','rendered_html_snapshot',artifacts,'.html'))
    related=[];seen=set()
    for link in view['links']:
        target=safe_target('',link['url']);label=link.get('label') or ''
        if not target:continue
        if not public_url(target) or target in seen:continue
        hint=classify_link(label,target)
        if hint['eligible']:
            related.append({'url':target,'label':label,'resource_type_hint':hint['resource_type'],'relationship':'observed_link','applicability_inherited':False});seen.add(target)
    authority={'class':'court_source_self_identified' if COURT.search(title+' '+text[:800]) else 'government_or_other_source','status':'source_identified_not_independently_certified','verified':False,
        'evidence':[{'source_url':url,'excerpt':title,'basis':'Captured source title/content; government domain alone does not establish court jurisdiction.'}]}
    facts=[]
    for line in text.splitlines()[:150]:
        line=line.strip()
        if re.search(r'(?i)(?:phone|telephone|fax|hours|clerk of court|court address)',line) and len(line)<230 and not line.endswith(':') and re.search(r'\d|:\s*\S.{2}',line):
            facts.append({'field':'contact_as_published','value':line,'source_excerpt':line,'source_location':'clean_text exact line','method':'labeled source line retained verbatim','confidence':'source_explicit','published_at':None,'effective_at':None})
        if len(facts)>=12:break
    temporal={'captured_at':source.get('captured_at'),'published_at':None,'effective_at':None,'source_as_of':None,'bases':{'captured_at':'Original saved fetch/provider capture timestamp; normalization time is separate'}}
    practice=[name for name in ('civil','criminal','family','probate','juvenile','small claims','domestic violence') if re.search(r'\b'+name+r'\b',title+' '+text[:1800],re.I)]
    resource_id='county-litigation:'+sha((source.get('collection','')+'\n'+url+'\n'+sha(raw)).encode())[:32]
    metadata={'resource_type':classification['resource_type'],'availability':'needs_review' if classification['resource_type']=='unknown' or not text.strip() else 'saved','document_shape':classification['document_shape'],'legal_status':classification['legal_status'],'legal_status_evidence':classification.get('legal_status_evidence',[]),
        'court_name':applicability.get('court_name'),'practice_areas':practice,'applicability':applicability,'source_authority':authority,'authority':authority,'temporal':temporal,'facts':facts,
        'classification':classification,'extraction':{'status':'clean_text_saved' if text.strip() else 'native_text_missing','needs_offline_ocr_or_extraction_review':not bool(text.strip()),'method':view['notes']['method'],'reader':view['notes'],'original_status':source.get('extraction_status')},
        'artifacts':served_artifacts,'related_links':related,'duplicate_group':'sha256:'+sha(raw),'discovery_contexts':source.get('contexts',[]),
        'representation':source.get('representation') or 'Original downloaded source bytes','original_http_bytes':not bool(source.get('representation')),'source_collection':source.get('collection'),'source_resource_id':source.get('resource_id'),
        'original_raw_path':str(source['original_raw_path'].relative_to(ROOT)),'original_raw_sha256':sha(raw),'semantic_review':negative,'normalized_at':stamp(),'legal_currency_verified':False,
        'original_title':original_title,'extracted_title':source.get('extracted_title'),'source_checkpoint':source.get('source_checkpoint'),'source_seed_provenance':source.get('seed_provenance'),
        'document_structure':extract_structure(text,classification['resource_type'])}
    return {'id':resource_id,'title':title,'source_url':url,'origin_url':url,'canonical_url':source.get('canonical_url') or url,'state':county['usps'] if county else applicability.get('state'),
        'county':county['name'] if county else None,'county_fips':county['geoid'] if county else None,'county_geoids':[county['geoid']] if county else [],'group':'counties','resource_kind':classification['resource_type'],
        'raw_path':rawart['path'],'text_path':textart['path'],'sha256':rawart['sha256'],'text_sha256':textart['sha256'],'captured_at':source.get('captured_at'),
        'quality':'Saved source with conservative content classification; geographic applicability and current legal status require their separate evidence fields.','metadata':metadata}

def corpus_sources(collection,only_ids=None):
    folder=ROOT/collection;db=sqlite3.connect((folder/'corpus.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        contexts={r['id']:{'id':r['id'],'jurisdiction':json.loads(r['jurisdiction_json']),'seed':json.loads(r['seed_json'])} for r in db.execute('SELECT * FROM contexts')}
        associations=defaultdict(list)
        for r in db.execute('SELECT * FROM resource_contexts'):associations[r['resource_id']].append(contexts[r['context_id']])
        for r in db.execute("SELECT * FROM resources WHERE status='downloaded' AND last_http_status=200 AND raw_complete=1 ORDER BY id"):
            r=dict(r)
            if only_ids and r['id'] not in only_ids:continue
            raw=readbound(folder/r['raw_path'],r['sha256'],folder);metapath=(folder/r['metadata_path']).resolve()
            if not metapath.is_relative_to(folder):raise ValueError('Metadata path outside collection')
            meta=json.loads(metapath.read_text(encoding='utf8'))
            if meta.get('requested_url')!=r['url'] or meta.get('sha256')!=r['sha256'] or meta.get('fetch_id')!=r['last_fetch_id']:raise ValueError('Capture metadata identity mismatch')
            text=readbound(folder/r['text_path'],meta.get('text_sha256'),folder).decode('utf8') if r.get('text_path') and meta.get('text_sha256') else ''
            principal=''
            if mime(raw,meta.get('mime_type') or '')[0]=='text/html':
                from bs4 import BeautifulSoup
                parsed=BeautifulSoup(raw,'html.parser');principal='\n'.join(h.get_text(' ',strip=True) for h in parsed.find_all('h1')[:3])
            yield {'source_url':r['url'],'canonical_url':meta.get('final_url') or meta.get('response_url') or r['url'],'title':r.get('title'),'raw':raw,'text':text,'original_raw_path':folder/r['raw_path'],'principal_text':principal,
                'mime_type':meta.get('mime_type') or (meta.get('headers') or {}).get('content-type',''),'captured_at':meta.get('fetched_at'),'contexts':associations[r['id']],
                'collection':collection,'resource_id':r['id'],'extraction_status':r.get('extraction_status'),'source_text_sha256':meta.get('text_sha256')}
    finally:db.close()

def provider_sources(checkpoint,inventory=None,existing_identities=None):
    """Consume only a frozen hash-bound checkpoint; validate each source artifact."""
    checkpoint=Path(checkpoint).resolve();provider=ROOT/'sources/county_litigation_firecrawl_20260919'
    if not checkpoint.is_relative_to(provider/'checkpoints'):raise ValueError('Checkpoint outside collector scope')
    gate_bytes=(checkpoint/'validation.json').read_bytes();gate=json.loads(gate_bytes)
    if gate.get('status')!='passed':raise ValueError('Collector checkpoint is not passed')
    manifest=readbound(checkpoint/'resources.jsonl',gate['resources_sha256'],checkpoint)
    rows=[json.loads(l) for l in manifest.decode('utf8').splitlines() if l.strip()]
    if len(rows)!=gate['records']:raise ValueError('Collector checkpoint count mismatch')
    from bs4 import BeautifulSoup
    byfips={r['geoid']:r for r in (inventory or readl(ROOT/'delivery/focused_legal_corpus/counties/counties.jsonl'))}
    for row in rows:
        if row.get('source_http_status')!=200:raise ValueError('Non-200 collector capture')
        # Incremental callers independently audit their existing package first.
        # A matching URL + original-byte hash is the same stored resource, so
        # its source parsing/geography need not be repeated.
        identity='county-litigation:'+sha(('sources/county_litigation_firecrawl_20260919\n'+row['source_url']+'\n'+row['raw_sha256']).encode())[:32]
        if existing_identities is not None and identity in existing_identities:continue
        # Originals whose text could not be extracted locally (scanned PDF, unsupported format) stay in the collector as
        # text-gap originals; without text they cannot be classified, so they are not published.
        if not row.get('text_path'):print('held (no extracted text): '+str(row.get('source_url')),file=sys.stderr);continue
        rawpath=ROOT/row['raw_path'];raw=readbound(rawpath,row['raw_sha256'],provider)
        text=readbound(ROOT/row['text_path'],row['text_sha256'],provider).decode('utf8')
        htmlpath=ROOT/row['html_path'] if row.get('html_path') else None
        html=readbound(htmlpath,row['html_sha256'],provider) if htmlpath else None
        principal='\n'.join(h.get_text(' ',strip=True) for h in BeautifulSoup(html,'html.parser').find_all('h1')[:3]) if html else ''
        url=row['source_url'];meta=row.get('provider_metadata') or {};canonical=meta.get('url') or row.get('final_url') or url
        if row.get('capture_kind')=='firecrawl_provider_capture':
            data=json.loads(raw).get('data',{});savedmeta=data.get('metadata') or {}
            if savedmeta.get('sourceURL') not in {None,url} or savedmeta.get('statusCode')!=200:raise ValueError('Provider source identity mismatch')
            canonical=savedmeta.get('url') or canonical
        seed=row.get('seed_provenance') or {};parent_proof=None
        if seed.get('parent_raw_path') and seed.get('parent_raw_sha256'):
            parent=readbound(ROOT/seed['parent_raw_path'],seed['parent_raw_sha256'],ROOT)
            soup=BeautifulSoup(parent,'html.parser');ptitle=soup.title.get_text(' ',strip=True) if soup.title else ''
            parent_url=seed.get('parent_url') or ''
            anchors=[a for a in soup.find_all('a',href=True) if safe_target(parent_url,a['href'])==urldefrag(url)[0]]
            declared=byfips.get(seed.get('county_fips'));names=[declared['name']] if declared else []
            if declared and declared['name'].endswith(' County'):names.append('County of '+declared['name'][:-7])
            header=ptitle+' '+(' '.join(h.get_text(' ',strip=True) for h in soup.find_all(['h1','h2'])[:4]))
            if anchors and declared and COURT.search(header) and re.search(r'\b'+re.escape(declared['state'])+r'\b',header,re.I) and any(re.search(r'\b'+re.escape(n)+r'\b',header,re.I) for n in names):
                parent_proof={'geoid':declared['geoid'],'source_url':parent_url,'parent_sha256':sha(parent),'parent_title':ptitle,'parent_heading_evidence':header,'observed_anchor':anchors[0].get_text(' ',strip=True),'target_url':url,'method':'Exact source court title/headings and state/county plus reproduced literal href; source association only'}
        title=row.get('title')
        if row.get('mime_type')=='application/pdf' and seed.get('anchor_text'):title=seed['anchor_text']
        yield {'source_url':url,'canonical_url':canonical,'title':title,'extracted_title':row.get('title'),'raw':raw,'text':text,'original_raw_path':rawpath,'reader_path':htmlpath or rawpath,
            'html_bytes':html,'mime_type':row.get('mime_type',''),'captured_at':row.get('captured_at'),'contexts':[],'principal_text':principal,
            'collection':'sources/county_litigation_firecrawl_20260919','resource_id':row['id'],'source_text_sha256':row['text_sha256'],
            'representation':None if row.get('original_http_bytes') else 'Firecrawl provider JSON and selected rendered HTML; not original HTTP response bytes',
            'source_checkpoint':{'path':str(checkpoint.relative_to(ROOT)),'manifest_sha256':sha(manifest),'gate_sha256':sha(gate_bytes)},'seed_provenance':seed,'verified_parent_county':parent_proof}
    if (checkpoint/'validation.json').read_bytes()!=gate_bytes:raise ValueError('Checkpoint gate changed during normalization')

def publish(resources,artifacts,summary):
    if len({r['id'] for r in resources})!=len(resources):raise ValueError('Duplicate resource identity')
    write(HERE/'validation.json',{'status':'publishing','ready':False,'data_files':[]})
    writel(HERE/'resources.jsonl',resources);writel(HERE/'artifacts.jsonl',list(artifacts.values()));write(HERE/'summary.json',summary)
    files=[{'path':name,'sha256':sha((HERE/name).read_bytes()),'rows':count} for name,count in [('resources.jsonl',len(resources)),('artifacts.jsonl',len(artifacts)),('summary.json',1)]]
    files.extend({'path':a['path'],'sha256':a['sha256'],'rows':1} for a in artifacts.values())
    write(HERE/'validation.json',{'status':'passed','ready':True,'validated_at':stamp(),'schema_version':'1','data_files':files,'counts':summary,'qualification':'Offline saved evidence normalization; not a completeness or current-law certificate.'})

def main(sample=False,checkpoint=None,include_official_courts=False):
    HERE.mkdir(exist_ok=True)
    inventory=readl(ROOT/'delivery/focused_legal_corpus/counties/counties.jsonl');byfips={r['geoid']:r for r in inventory}
    coverage=trellis_coverage.load();assert coverage
    website_map=defaultdict(list)
    for row in coverage['counties']:
        if row.get('fips') not in byfips:continue
        for source in row.get('detail_sources',[]):
            site=(source.get('publisher_reported') or {}).get('website')
            if site and public_url(site) and byfips[row['fips']] not in website_map[normurl(site)]:website_map[normurl(site)].append(byfips[row['fips']])
        site=(row.get('publisher_reported') or {}).get('website')
        if site and public_url(site) and byfips[row['fips']] not in website_map[normurl(site)]:website_map[normurl(site)].append(byfips[row['fips']])
    sources=[]
    if sample:
        sources.extend(corpus_sources('corpus/county_entries_continuation_20260913',{102}));sources.extend(corpus_sources('corpus/county_local_documents_20260914',{2}))
    else:
        sources=itertools.chain.from_iterable(corpus_sources(collection) for collection in COLLECTIONS+(['corpus/official_courts'] if include_official_courts else []))
    if checkpoint:sources=itertools.chain(sources,provider_sources(checkpoint,inventory))
    semantic_path=ROOT/'reports/counties/local_documents_20260914/content_kind_sample.jsonl';semantic={r['resource_key']:r for r in readl(semantic_path)} if semantic_path.exists() else {}
    holdback_path=HERE/'publication_holdbacks.jsonl'
    holdback_ids={row['id'] for row in readl(holdback_path)} if holdback_path.exists() else set()
    resources=[];artifacts={};excluded=[]
    for source in sources:
        resource_id='county-litigation:'+sha((source.get('collection','')+'\n'+source['source_url']+'\n'+sha(source['raw'])).encode())[:32]
        if resource_id in holdback_ids:
            excluded.append(resource_id);continue
        review=semantic.get(source['collection']+':'+str(source['resource_id']))
        if review and review['raw_evidence']['sha256']==sha(source['raw']) and review['text_evidence']['sha256']==source.get('source_text_sha256'):source['semantic_review']={'reviewed':True,'actual_resource_kind':review['actual_resource_kind'],'evidence':review,'source_manifest':str(semantic_path.relative_to(ROOT))}
        row=make_resource(source,inventory,website_map,artifacts)
        resources.append(row)
        if len(resources)%100==0:print(json.dumps({'normalized_records':len(resources)}),flush=True)
    summary={'resources':len(resources),'by_type':dict(Counter(r['resource_kind'] for r in resources)),'county_geoids':len({g for r in resources for g in r['county_geoids']}),'artifacts':len(artifacts),'sample':sample,'network_requests':0,'normalized_at':stamp(),'classifier_version':VERSION,'source_collections':COLLECTIONS,'original_sources_modified':False,'full_corpus_complete':False}
    summary.update(by_shape=dict(Counter(r['metadata']['document_shape'] for r in resources)),by_state=dict(Counter(r.get('state') or 'unresolved' for r in resources)),unassigned_county_records=sum(not r['county_geoids'] for r in resources),text_gaps=sum(r['metadata']['extraction']['status']=='native_text_missing' for r in resources),checkpoint=str(checkpoint) if checkpoint else None,explicitly_held_non_litigation_captures=len(excluded))
    publish(resources,artifacts,summary);print(json.dumps(summary))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--sample',action='store_true');parser.add_argument('--checkpoint');parser.add_argument('--include-official-courts',action='store_true');args=parser.parse_args()
    import msvcrt
    with (HERE/'build.lock').open('a+b') as guard:
        guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0)
        try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise SystemExit('Another county litigation build holds the publication lock')
        try:main(args.sample,args.checkpoint,args.include_official_courts)
        finally:guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_UNLCK,1)
