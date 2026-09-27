"""Admit only literal saved anchors, with exact source and county evidence."""
import argparse
import sqlite3
from lxml import html
import collect as c

PILOT_PATHS={
'/forms-filing/rules-court','/forms-filing/forms','/forms-filing/fee-schedule','/forms-filing/notices-orders',
'/forms-filing/forms/forms-packet','/forms-filing/forms/unlawful-detainer-local-forms',
'/online-services/efiling','/online-services/efiling/court-policy-file','/online-services/efiling/efiling-faqs',
'/online-services/efiling/efiling-civil','/online-services/efiling/efiling-family','/online-services/efiling/efiling-small-claims',
'/system/files/forms-and-filings/l1038.pdf','/system/files/l1018.pdf','/system/files/l696.pdf'}

def main():
 p=argparse.ArgumentParser();p.add_argument('--pilot',action='store_true');p.add_argument('--limit',type=int,default=100);args=p.parse_args()
 sys=c.sys;sys.path.insert(0,str(c.ROOT/'sources/county_litigation_20260919'));import classify
 existing=set()
 db=sqlite3.connect((c.ROOT/'delivery/archive-directory/directory.sqlite3').as_uri()+'?mode=ro',uri=True)
 existing.update(x[0] for x in db.execute('select source_url from browse where source_url is not null'));db.close()
 captures=c.rows(c.HERE/'resources.jsonl');seen={r['source_url'] for r in captures}|{r['final_url'] for r in captures}
 seeds=[];deferred=[]
 for r in captures:
  if not r.get('html_path') or int(r['seed_provenance'].get('depth',0))>=2:continue
  # This pilot authority is established by the inspected exact page title and
  # county-to-court source chain; not inferred from a generic .gov suffix.
  if c.crawler.host_of(r['final_url'])!='www.occourts.org' or 'Superior Court of California | County of Orange' not in r['title']:continue
  raw=(c.ROOT/r['html_path']).read_bytes()
  if c.sha(raw)!=r['html_sha256']:raise ValueError('Parent HTML hash mismatch')
  doc=html.fromstring(raw)
  for a in doc.xpath('//a[@href]'):
   url=c.crawler.canonical_url(a.get('href'),r['final_url']);label=' '.join(a.text_content().split())
   if not url or url in seen:continue
   path=c.urllib.parse.urlsplit(url).path
   if args.pilot and path not in PILOT_PATHS:continue
   base={**r['seed_provenance'],'url':url,'allowed_hosts':['www.occourts.org']}
   if not c.link_allowed(url,label,base) and not (args.pilot and path in PILOT_PATHS and c.crawler.host_of(url)=='www.occourts.org'):continue
   seen.add(url)
   if url in existing:
    deferred.append({'url':url,'reason':'exact_source_url_already_published'});continue
   kind=classify.classify_link(label,url)
   reviewed_pilot_kinds={'/forms-filing/forms':'court_form','/forms-filing/notices-orders':'standing_order','/system/files/forms-and-filings/l1038.pdf':'court_form','/system/files/l1018.pdf':'court_information','/system/files/l696.pdf':'court_form'}
   if args.pilot and path in reviewed_pilot_kinds:
    kind={'eligible':True,'resource_type':reviewed_pilot_kinds[path],'priority':2,'reason':'Manually inspected literal official court anchor; destination classification still required'}
   if not kind['eligible']:continue
   authority={'class':'official_county_superior_court','verified':True,'basis':'Explicit captured publisher title names Superior Court of California and County of Orange; exact official page anchor','evidence':{'capture_id':r['id'],'raw_path':r['raw_path'],'raw_sha256':r['raw_sha256'],'html_path':r['html_path'],'html_sha256':r['html_sha256'],'publisher_title':r['title']}}
   seed={**base,'id':'county-litigation-seed:'+c.sha(url.encode())[:24],'source_url':url,'resource_type':kind['resource_type'],'parent_url':r['final_url'],'parent_capture_id':r['id'],'parent_raw_path':r['html_path'],'parent_raw_sha256':r['html_sha256'],'anchor_text':label,'source_authority':authority,'association':{'status':'explicit_county_court_source','basis':'Captured official court page names Orange County; exact link observed on that page','evidence':[r['id'],r['raw_sha256']]},'applicability':{'level':'county','state':'CA','county_fips':'06059','status':'county_court_source_document_scope_requires_content_review'},'priority':kind['priority'],'depth':int(r['seed_provenance'].get('depth',0))+1,'existing_capture_suffices':False}
   seeds.append(seed)
 seeds.sort(key=lambda x:(x['priority'],x['url']))
 selected=seeds[:args.limit]
 target=c.HERE/('pilot_children.jsonl' if args.pilot else 'discovered_children.jsonl')
 c.save_rows(target,selected);c.save(c.HERE/'discovery_receipt.json',{'generated_at':c.now(),'selected':len(selected),'selected_sha256':c.sha(target.read_bytes()),'candidate_count':len(seeds),'reused_or_deferred':deferred,'auto_source_authority_hosts':['www.occourts.org'],'classification_version':classify.VERSION})
 print({'selected':len(selected),'path':str(target),'already_saved':len(deferred)})
if __name__=='__main__':main()
