"""Export a separate hash-pinned addition; never rewrite the base migration plan."""
from pathlib import Path
import hashlib, json, sys
from collections import defaultdict
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'delivery/archive-directory'))
sys.path.insert(0,str(ROOT/'deploy'))
import enrichment
from import_catalog import normalize

OUT=ROOT/'_transfer_scratch/supabase_export/additions_20260927_v2'
DATASET='gap_enrichment_20260927'

def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def projection(data):
    """Small listing/identity index and bounded per-entity graph contexts."""
    adjacent=defaultdict(list); captured=defaultdict(set)
    records={r['id']:r for r in data['resources']}
    nodes={n['id']:n for n in data['nodes']}
    for edge in data['edges']:
        adjacent[edge['source']].append(edge)
        if edge['target']!=edge['source']:adjacent[edge['target']].append(edge)
        if edge['relation']=='captured_as':captured[edge['source']].add(edge['target'])
    fields=('id','title','state','lane','resource_type','category','document_shape','native_id','source_url',
            'mdl_number','captured_at','published_at','effective_from','filed_at','source_as_of','original_url',
            'qualification','review_status','page_range','source_page','legal_status','contains_rescinded_rule_notices')
    index={k:data[k] for k in ('available','summary','generated_at','qualification')}
    index['resources']=[enrichment.public_value({k:r[k] for k in fields if k in r}) for r in data['resources']]
    scopes={}
    for entity,edges in adjacent.items():
        if not entity.startswith(('county:','mdl:')):continue
        ids={e[k] for e in edges for k in ('source','target')}
        if entity.startswith('county:'):
            ids.update(target for ident in tuple(ids) for target in captured.get(ident,()))
        else:
            ids.update(r['id'] for r in data['resources'] if str(r.get('mdl_number') or '')==entity[4:])
        scopes[entity]=sorted(ids & records.keys())
    index['scopes']=scopes
    index['graph_entities']=sorted(adjacent)
    graphs={}
    for entity,edges in adjacent.items():
        selected=edges[:500];ids={e[k] for e in selected for k in ('source','target')}
        graphs[entity]=enrichment.public_value({'available':True,'entity':entity,'total':len(edges),'edges':selected,
            'nodes':[nodes[ident] for ident in sorted(ids) if ident in nodes]})
    return index,graphs

def main():
    data=enrichment.state()
    if not data:raise ValueError('Local enrichment publication gate is closed')
    gate_sha=sha(enrichment.DATA/'validation.json')
    if (OUT/'delta_plan.json').exists():raise ValueError('Delta already frozen; create a new version')
    OUT.mkdir(parents=True,exist_ok=True)
    contexts=[]
    def put(key,value):
        value=normalize({'id':'context','dataset':'context','category':'directories','item':value})['item']
        contexts.append({'key':key,'data':value,'source_sha256':hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()})
    index,graphs=projection(data)
    put('enrichment:index',index)
    for entity,graph in graphs.items():put('enrichment:graph:'+entity,graph)
    with (OUT/(DATASET+'.jsonl')).open('w',encoding='utf-8') as target:
        for ordinal,r in enumerate(data['resources']):
            # Work from one verified immutable snapshot, instead of re-statting
            # a thousand registered files three times for every reader.
            body=(enrichment.DATA/r['text_file']).read_bytes() if r.get('text_file') else b''
            if body and hashlib.sha256(body).hexdigest()!=r['text_sha256']:raise ValueError('Reader changed during export')
            graph=graphs.get(r['id'],{'available':True,'entity':r['id'],'total':0,'edges':[],'nodes':[]})
            selected=graph['edges'][:100];node_ids={e[k] for e in selected for k in ('source','target')}
            detail=enrichment.public_record(r)
            detail.update(text=body.decode('utf-8-sig'),relationships=dict(graph,edges=selected,nodes=[n for n in graph['nodes'] if n['id'] in node_ids],truncated=graph['total']>100,summary=data['summary'],qualification=data['qualification']))
            put('enrichment:record:'+r['id'],detail)
            category=r.get('resource_type') or r.get('category')
            if not category or category in ('unknown','uncategorized'):raise ValueError('Unclassified enrichment reached export')
            artifacts=[]
            for kind,file_key,hash_key,url_key in [('original','original_file','raw_sha256','original_url'),('text','text_file','text_sha256','text_url')]:
                if not r.get(file_key):continue
                p=enrichment.DATA/r[file_key]
                artifacts.append({'url':r[url_key],'local_path':str(p.resolve()),'sha256':r[hash_key],'bytes':p.stat().st_size,'mime':'text/plain; charset=utf-8' if kind=='text' else r.get('mime_type') or 'application/octet-stream'})
            record={'id':r['id'],'dataset':DATASET,'category':category,'state':r.get('state'),'title':r['title'],'source_url':r['source_url'],
                    'county_geoids':r.get('county_geoids') or ([r['county_fips']] if isinstance(r.get('county_fips'),str) else r.get('county_fips') or []),
                    'item':enrichment.public_record(r),'detail':{k:v for k,v in detail.items() if k!='text'},'text':detail.get('text') or '',
                    'filters':{'state':r.get('state'),'resource_type':category,'lane':r['lane']},'ordinal':ordinal,'artifacts':artifacts}
            normalize(record) # Validate without removing the private upload manifest from this local export.
            target.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
    with (OUT/'contexts.jsonl').open('w',encoding='utf-8') as f:
        for row in contexts:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    rows=OUT/(DATASET+'.jsonl')
    descriptor={'id':DATASET,'label':'Dated source additions and evidence connections','expected_records':len(data['resources']),'export_jsonl_sha256':sha(rows),'qualification':data['qualification']}
    (OUT/(DATASET+'.dataset.json')).write_text(json.dumps(descriptor,indent=2)+'\n',encoding='utf-8')
    if sha(enrichment.DATA/'validation.json')!=gate_sha or enrichment.state() is None:raise ValueError('Local publication changed during export')
    plan={'schema_version':1,'project':'xosqzzsnhxcyehcnirpa','generated_at':datetime.now(timezone.utc).isoformat(),'publication':'held','base_plan_modified':False,
          'files':[{'path':p.name,'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(OUT.iterdir()) if p.is_file()],
          'records':len(data['resources']),'contexts':len(contexts),'supersedes_unimported_delta':'additions_20260927','revision_reason':'Expose source-recorded rescission status in reader and listing payloads','requires':['Base migration acceptance and effective disk capacity','Import categorized dataset, then originals and contexts','Verify remote counts and hashes, context chunks and signed originals','Only then publish this dataset and all enrichment context dependencies; include delta dataset in the final release inventory']}
    (OUT/'delta_plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8');print(json.dumps({'records':plan['records'],'contexts':plan['contexts'],'publication':'held','base_plan_modified':False}))

if __name__=='__main__':main()
