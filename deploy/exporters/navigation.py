"""Export validated navigation data and finite context; never acquire remote data."""
from __future__ import annotations
import collections, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'delivery/archive-directory'),str(ROOT/'deploy')]
from import_catalog import normalize
from exporters.core_documents import Writer,Artifacts
import jurisdiction_coverage as coverage, county_filing, county_registry, trellis_coverage as trellis
import doj_resources as doj, local_library, supplements

OUT=ROOT/'_transfer_scratch/supabase_export/navigation'
def safe(value):
    return normalize({'id':'context','dataset':'context','category':'directories','item':value})['item']
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    writer=Writer(OUT);artifacts=Artifacts();contexts=[]
    def context(key,value):
        if value is None:return
        value=safe(value);raw=json.dumps(value,sort_keys=True,ensure_ascii=False).encode()
        contexts.append({'key':key,'data':value,'source_sha256':hashlib.sha256(raw).hexdigest()})
    def row(dataset,ident,item,filters=None,category='directories',ordinal=0,assets=None,title=None):
        writer.add({'id':str(ident),'dataset':dataset,'category':category,'title':title or item.get('title') or item.get('link_text') or item.get('name') or str(ident),
          'state':item.get('state') or item.get('usps') or '', 'county_geoids':[], 'source_url':item.get('source_url') or item.get('url') or '',
          'item':safe(item),'detail':safe(item),'text':'','filters':filters or {},'ordinal':ordinal,'artifacts':assets or []})
    data=coverage.load()
    if not data:raise ValueError('Coverage validation gate is closed')
    context('coverage:matrix',coverage.matrix());context('coverage:venues',coverage.venues());context('state:aliases',data['names'])
    for state in data['coverage']['jurisdictions']:context('coverage:state:'+state,coverage.state_detail(state))
    context('coverage:topics',{k:v for k,v in coverage.topics().items() if k!='items'})
    tiers=collections.defaultdict(collections.Counter)
    for ordinal,r in enumerate(data['provisions']):
        item=coverage._public_provision(r,None);item['_topic_evidence']=r['topics']
        row('coverage_topics',r['provision_id'],item,{'state':r['state'],'topic':[t['topic'] for t in r['topics']]},r['family'] if r['family'] in {'rules','statutes','regulations','constitutions'} else 'guidance',ordinal)
        for state in ('',r['state']):
            for topic in ('',*[t['topic'] for t in r['topics']]):tiers[state+'|'+topic][r['source_tier']]+=1
    context('coverage:topic_tiers',dict(tiers))
    for ordinal,r in enumerate(data['labels']):row('coverage_labels',r.get('record_id') or r.get('id') or ordinal,r,{k:r.get(k) for k in ('state','law_body_class','rule_set','confidence')},'guidance',ordinal)
    filing=county_filing._state()
    if not filing:raise ValueError('County filing validation gate is closed')
    context('county-filing:coverage',county_filing.coverage())
    for state in filing['states']:
        context('county-filing:state:'+state,county_filing.for_state(state));context('county-filing:state-counties:'+state,county_filing.counties_for_state(state))
    for geoid in filing['counties']:context('county-filing:county:'+geoid,county_filing.for_county(geoid))
    for geoid,value in county_registry._load().items():context('county-registry:'+geoid,value)
    t=trellis.load()
    if not t:raise ValueError('County publisher validation gate is closed')
    context('trellis:summary',trellis.summary());context('trellis:progress',trellis.progress())
    context('trellis:states',[trellis._public(r) for r in t['states']])
    context('trellis:counties',[trellis._public(r) for r in t['counties']])
    for r in t['receipts']:
        artifact=artifacts.get(trellis.DATA/r['response_path'],'/api/trellis-coverage/receipt?id='+r['id'],'receipt',r['response_sha256'])
        row('trellis_receipts',r['id'],trellis._public(r),assets=[artifact] if artifact else [])
    d=doj._try()
    if not d:raise ValueError('DOJ validation gate is closed')
    context('doj:states',doj.states());context('doj:circuits',doj.circuits());context('doj:aliases',d['aliases'])
    context('doj:resources',d['resources']);context('doj:edges',d['edges']);context('doj:qualification',doj.QUALIFICATION)
    cards=local_library.collections();context('collections',{'items':cards})
    asset_list=[]
    for asset in local_library._load('assets.json'):
        got=local_library.asset(asset['id'])
        if got:
            p,mime,attachment=got
            digest=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
            asset_list.append({'url':'/library-assets/'+asset['id'],'local_path':str(p),'sha256':digest,'bytes':p.stat().st_size,'mime':mime})
    for card in cards:
        rows=local_library._load(card['id']+'.jsonl');context('collection:'+card['id'],{'card':card,'rows':rows})
    row('library_assets','manifest',{'title':'Allowlisted library illustrations and documents'},assets=asset_list)
    status=supplements.status();context('supplements',status)
    for entry in status.get('items',[]):
        key=entry.get('name') or entry.get('id')
        if key:context('supplement:'+key,supplements.item(key))
    descriptors=writer.finish()
    with (OUT/'contexts.jsonl').open('w',encoding='utf-8') as stream:
        for item in contexts:stream.write(json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n')
    receipt={'contexts':len(contexts),'datasets':descriptors,'missing_artifacts':artifacts.missing}
    (OUT/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'contexts':len(contexts),'datasets':[{k:v for k,v in d.items() if k in ('id','rows')} for d in descriptors],'missing_artifacts':len(artifacts.missing)}))
if __name__=='__main__':main()
