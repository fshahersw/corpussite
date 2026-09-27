"""Offline, evidence-bound document children from normalized court landing pages.

No network, queue writes, automatic ingestion, or legal-applicability promotion.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib,json,re,sqlite3
from pathlib import Path
from urllib.parse import urlsplit,urljoin,urldefrag
from lxml import html

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
KINDS={'local_rule','court_form','standing_order','fee_schedule','filing_guidance'}
SHAPES={'rule_index','form_directory','order_index','fee_information','fee_table','guide','rule_body','filing_guide'}
VERIFIED_PARENT={'verified_parent_discovery_association','verified_court_source_association_only','official_state_directory_court_label'}
DOC=re.compile(r'\.(?:pdf|docx?|rtf|odt)$',re.I)
UNRELATED=re.compile(r'\b(?:news|newsletter|press release|annual report|budget|employment|job application|job posting|recruitment|election|quorum|quroum|vehicle|sheriff|criminal investigations|community outreach|court tour|privacy policy|website accessibility)\b',re.I)
PRIORITY={'local_rule':1,'standing_order':1,'court_form':2,'fee_schedule':2,'filing_guidance':2}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def stamp():return datetime.now(timezone.utc).isoformat()
def normpath(value):return str(value).replace('\\','/')
def rows(raw):return [json.loads(x) for x in raw.decode('utf-8-sig').splitlines() if x.strip()]
def readbound(root,relative,digest):
 p=(root/relative).resolve()
 if not p.is_relative_to(root.resolve()) or not p.is_file():raise ValueError('Artifact missing or outside registered root')
 raw=p.read_bytes()
 if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or sha(raw)!=digest:raise ValueError('Artifact digest mismatch')
 return raw
def safe_url(value,base=None):
 try:
  url=urldefrag(urljoin(base,value) if base else value)[0];p=urlsplit(url)
  if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password or p.port not in (None,80,443):return None
  if re.search(r'[\x00-\x20]',url):return None
  return url
 except (ValueError,TypeError):return None
def write_json(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def load_inputs(checkpoint,publication,root):
 checkpoint=checkpoint.resolve();publication=publication.resolve()
 if not checkpoint.is_relative_to((root/'sources/county_litigation_firecrawl_20260919/checkpoints').resolve()):raise ValueError('Unregistered collector checkpoint')
 if not publication.is_relative_to(root.resolve()):raise ValueError('Publication outside workspace')
 cgraw=(checkpoint/'validation.json').read_bytes();cg=json.loads(cgraw)
 if cg.get('status')!='passed':raise ValueError('Collector checkpoint has not passed')
 cr=readbound(checkpoint,'resources.jsonl',cg.get('resources_sha256'));captures=rows(cr)
 if cg.get('records')!=len(captures):raise ValueError('Collector count mismatch')
 pgraw=(publication/'validation.json').read_bytes();pg=json.loads(pgraw)
 if pg.get('status')!='passed' or pg.get('ready') is not True:raise ValueError('Publication is not ready')
 bindings={x['path']:x for x in pg.get('data_files',[]) if isinstance(x,dict)}
 if 'resources.jsonl' not in bindings:raise ValueError('Publication manifest binding missing')
 pr=readbound(publication,'resources.jsonl',bindings['resources.jsonl'].get('sha256'));published=rows(pr)
 if bindings['resources.jsonl'].get('rows')!=len(published):raise ValueError('Publication count mismatch')
 identity={'checkpoint':normpath(checkpoint.relative_to(root)),'checkpoint_resources_sha256':sha(cr),'checkpoint_validation_sha256':sha(cgraw),'publication':normpath(publication.relative_to(root)),'publication_resources_sha256':sha(pr),'publication_validation_sha256':sha(pgraw)}
 return captures,published,identity,(cgraw,pgraw)

def content_tree(raw):
 # Firecrawl returned UTF-8 HTML. Explicit decoding prevents Windows-1252
 # reinterpretation of punctuation/anchor labels; original bytes remain bound.
 tree=html.fromstring(raw.decode('utf-8-sig'))
 candidates=tree.xpath('//main | //*[@role="main"] | //article | //*[@id="main-content"] | //*[@id="content"]')
 candidates=[x for x in candidates if len(' '.join(x.text_content().split()))>80]
 main=max(candidates,key=lambda x:len(x.text_content())) if candidates else tree
 for node in list(main.xpath('.//nav|.//footer|.//header|.//aside|.//script|.//style|.//noscript|.//template|.//*[@hidden]|.//*[@role="navigation"]|.//*[@role="banner"]|.//*[@role="contentinfo"]')):
  if node.getparent() is not None:node.drop_tree()
 for node in list(main.xpath('.//*[@class or @id]')):
  label=' '.join([node.get('id',''),node.get('class','')])
  if re.search(r'(?:^|[\s_-])(?:breadcrumb|footer|sidebar|nav|menu|social|cookie|share)(?:$|[\s_-])',label,re.I) and node.getparent() is not None:node.drop_tree()
 return main,bool(candidates)

def geography_allowed(published,tree):
 m=published.get('metadata') or {};app=m.get('applicability') or {}
 fips=published.get('county_fips');state=published.get('state');county=published.get('county')
 if not isinstance(fips,str) or not re.fullmatch(r'\d{5}',fips) or fips not in published.get('county_geoids',[]):return False,'missing_normalized_county_identity'
 if not isinstance(state,str) or not re.fullmatch('[A-Z]{2}',state) or app.get('state')!=state:return False,'missing_or_conflicting_state_evidence'
 if not app.get('evidence'):return False,'missing_geographic_evidence'
 if app.get('status') in VERIFIED_PARENT:return True,'validated_parent_association'
 if app.get('status')!='explicit_source_text':return False,'unverified_county_association'
 heading=published.get('title','')+'\n'+'\n'.join(' '.join(x.text_content().split()) for x in tree.xpath('.//h1|.//h2')[:12])
 names=[county or '']
 if county and county.endswith(' County'):names.append('County of '+county[:-7])
 if not any(n and re.search(r'\b'+re.escape(n)+r'\b',heading,re.I) for n in names):return False,'county_only_in_body_or_multicounty_index_requires_review'
 return True,'explicit_county_heading_and_normalized_state_evidence'

def parent_gate(published,capture,identity,root,publication):
 m=published.get('metadata') or {};kind=m.get('directory_kind') or m.get('resource_type') or published.get('resource_kind');shape=m.get('document_shape')
 if kind not in KINDS or shape not in SHAPES:return None,'not_reviewed_resource_landing_page'
 if m.get('availability')!='saved' or not capture.get('html_path'):return None,'not_readable_html_landing_page'
 source=m.get('source_checkpoint') or {}
 if normpath(source.get('path',''))!=identity['checkpoint'] or source.get('manifest_sha256')!=identity['checkpoint_resources_sha256'] or source.get('gate_sha256')!=identity['checkpoint_validation_sha256']:raise ValueError('Normalized checkpoint identity mismatch')
 if m.get('source_resource_id')!=capture['id'] or m.get('original_raw_sha256')!=capture['raw_sha256'] or normpath(m.get('original_raw_path',''))!=normpath(capture['raw_path']):raise ValueError('Normalized source capture identity mismatch')
 if published.get('source_url')!=capture['source_url'] or published.get('canonical_url')!=capture['final_url']:raise ValueError('Normalized requested/final URL mismatch')
 readbound(root,capture['raw_path'],capture['raw_sha256'])
 raw=readbound(root,capture['html_path'],capture['html_sha256'])
 clean=readbound(publication,published['text_path'],published['text_sha256']).decode('utf-8-sig')
 if len(clean.strip())<80:return None,'empty_or_insufficient_clean_text'
 tree,semantic=content_tree(raw)
 authority=m.get('source_authority') or {};court_title=published.get('title','')+' '+clean[:500]
 if authority.get('class')!='court_source_self_identified' and not re.search(r'\b(?:court|judicial circuit|judicial district)\b',court_title,re.I):return None,'no_source_court_context'
 allowed,why=geography_allowed(published,tree)
 if not allowed:return None,why
 return {'tree':tree,'kind':kind,'shape':shape,'semantic_main':semantic,'geography_basis':why,'clean_sha256':published['text_sha256']},None

def collect_children(capture,published,proof,known):
 base=capture['final_url'];host=urlsplit(base).netloc.lower();children=[];held=[];seen=set();parent_depth=int((capture.get('seed_provenance') or {}).get('depth',0))
 if parent_depth>=2:return [],[{'parent_id':capture['id'],'reason':'depth_bound'}]
 for a in proof['tree'].xpath('.//a[@href]'):
  raw_href=a.get('href');url=safe_url(raw_href,base);label=' '.join(a.text_content().split())
  if not url or url in seen:continue
  seen.add(url);p=urlsplit(url)
  if p.netloc.lower()!=host:continue
  if not (DOC.search(p.path) or a.get('type') in {'application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document'} or re.search(r'\b(?:PDF|DOCX|Word document)\b',label,re.I)):continue
  if UNRELATED.search(label+' '+p.path.replace('-',' ').replace('_',' ')):held.append({'url':url,'reason':'unrelated_topic'});continue
  if re.search(r'/(?:news|events|careers|jobs|login|register|account|admin)(?:/|$)',p.path,re.I):continue
  if url in known:held.append({'url':url,'reason':known[url]});continue
  if not label or label.casefold() in {'download','click here','here','pdf','view'}:
   # Weak generic labels need the nearest bounded row/paragraph text; avoid
   # inheriting every word of a large page container.
   contexts=a.xpath('ancestor::tr[1]|ancestor::li[1]|ancestor::p[1]')
   nearby=min(contexts,key=lambda x:len(x.text_content())) if contexts else None
   context=' '.join(nearby.text_content().split()) if nearby is not None else ''
   if not context or len(context)>700:held.append({'url':url,'reason':'generic_anchor_without_bounded_document_context'});continue
  else:context=label
  metadata=published['metadata'];county=published['county_fips'];state=published['state']
  anchor={'raw_href':raw_href,'resolved_url':url,'text':label,'context_excerpt':context,'xpath':a.getroottree().getpath(a),'main_content_selected':proof['semantic_main']}
  seed={'id':'county-document-child:'+sha(url.encode())[:24],'url':url,'source_url':url,'resource_type':proof['kind'],'state':state,'county':published['county'],'county_fips':county,'county_geoids':[county],'parent_url':base,'parent_capture_id':capture['id'],'parent_raw_path':capture['html_path'],'parent_raw_sha256':capture['html_sha256'],'anchor_text':label,'depth':parent_depth+1,'priority':PRIORITY[proof['kind']],'allowed_hosts':[host],'download_as_original':True,'existing_capture_suffices':False,'source_authority':metadata['source_authority'],'association':{'status':'validated_parent_source_association','basis':proof['geography_basis'],'evidence':{'normalized_resource_id':published['id'],'normalized_applicability':metadata['applicability'],'anchor':anchor}},'applicability':{'level':'unknown','state':state,'county_fips':None,'status':'document_territory_requires_review','note':'County association identifies the discovery source; the linked document is not asserted to govern that county.'},'parent_publication':{'resource_id':published['id'],'kind':proof['kind'],'document_shape':proof['shape'],'clean_text_sha256':proof['clean_sha256']},'link_proof':anchor,'automatic_ingestion':False}
  children.append(seed)
 return children,held

def known_targets(root,captures,published):
 known={}
 for r in captures:
  for key in ('source_url','final_url'):
   if r.get(key):known[r[key]]='already_captured_source_or_observed_alias'
 for r in published:
  for key in ('source_url','canonical_url'):
   if r.get(key):known[r[key]]='already_normalized_capture'
 q=root/'sources/county_litigation_firecrawl_20260919/queue.sqlite3'
 if q.is_file():
  db=sqlite3.connect(q.as_uri()+'?mode=ro',uri=True);db.execute('begin')
  try:
   for url,status,resource in db.execute('select url,status,resource_json from queue'):
    known[url]='existing_collector_queue_'+status
    if status=='downloaded' and resource:
     item=json.loads(resource)
     if item.get('final_url'):known[item['final_url']]='already_captured_observed_alias'
  finally:db.close()
 catalog=root/'delivery/archive-directory/directory.sqlite3'
 if catalog.is_file():
  db=sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True)
  try:
   for row in db.execute('select source_url from browse where source_url is not null'):known[row[0]]='already_published_source_url'
  finally:db.close()
 return known

def generate(checkpoint,publication,output,root=ROOT,limit=2000):
 if not 1<=limit<=6000:raise ValueError('Output limit outside approved finite bounds')
 captures,published,identity,gates=load_inputs(checkpoint,publication,root)
 by_id={r['id']:r for r in captures};known=known_targets(root,captures,published)
 seeds={};held=[];parents=[]
 for pub in published:
  ident=(pub.get('metadata') or {}).get('source_resource_id');capture=by_id.get(ident)
  if not capture:continue
  proof,reason=parent_gate(pub,capture,identity,root,publication)
  if reason:held.append({'parent_id':capture['id'],'url':capture['source_url'],'reason':reason});continue
  candidates,declined=collect_children(capture,pub,proof,known);held.extend(declined)
  parents.append({'capture_id':capture['id'],'normalized_id':pub['id'],'source_url':capture['final_url'],'county_fips':pub['county_fips'],'kind':proof['kind'],'shape':proof['shape'],'candidates':len(candidates)})
  for seed in candidates:
   seed['input_evidence']=identity
   if seed['url'] not in seeds:seeds[seed['url']]=seed
   else:
    prior=seeds[seed['url']];prior['county_geoids']=sorted(set(prior['county_geoids']+seed['county_geoids']))
    prior.setdefault('additional_parent_associations',[]).append({'county_fips':seed['county_fips'],'state':seed['state'],'parent_publication':seed['parent_publication'],'association':seed['association'],'link_proof':seed['link_proof']})
    if prior['state']!=seed['state']:prior['state']=None;prior['county_fips']=None;prior['county']=None
 if (checkpoint/'validation.json').read_bytes()!=gates[0] or (publication/'validation.json').read_bytes()!=gates[1]:raise ValueError('Input gate changed during preparation')
 selected=sorted(seeds.values(),key=lambda x:(x['priority'],x.get('state') or '',x['url']))[:limit]
 output=output.resolve()
 if not output.is_relative_to((root/'sources/county_litigation_firecrawl_20260919/document_children').resolve()):raise ValueError('Unregistered helper output root')
 if output.exists():raise ValueError('Output already exists; select a new immutable output directory')
 output.mkdir(parents=True)
 for name,data in [('seeds.jsonl',selected),('held.jsonl',held),('parents.jsonl',parents)]:
  (output/name).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data),encoding='utf-8')
 summary={'prepared_at':stamp(),'input_evidence':identity,'source_checkpoint_records':len(captures),'eligible_parent_pages':len(parents),'new_document_seeds':len(selected),'candidate_count':len(seeds),'omitted_by_limit':max(0,len(seeds)-limit),'county_associations':len({g for s in selected for g in s['county_geoids']}),'states':sorted({s['state'] for s in selected if s['state']}),'held_reasons':dict(Counter(x['reason'] for x in held)),'network_requests':0,'queue_writes':0,'automatic_ingestion':False,'legal_applicability_inherited':False,'complete_corpus':False}
 write_json(output/'summary.json',summary)
 validation={'status':'passed','validated_at':stamp(),'seeds':len(selected),'seeds_sha256':sha((output/'seeds.jsonl').read_bytes()),'held_sha256':sha((output/'held.jsonl').read_bytes()),'parents_sha256':sha((output/'parents.jsonl').read_bytes()),'input_evidence':identity,'qualification':'Exact same-host document links from reviewed source pages; candidates for next direct-download phase, not downloaded or territorially applicable documents.'}
 write_json(output/'validation.json',validation)
 return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--checkpoint',required=True);p.add_argument('--publication',default='sources/county_litigation_20260919');p.add_argument('--output',required=True);p.add_argument('--limit',type=int,default=2000);args=p.parse_args()
 print(json.dumps(generate((ROOT/args.checkpoint).resolve(),(ROOT/args.publication).resolve(),(ROOT/args.output).resolve(),limit=args.limit),indent=2))
