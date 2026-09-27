"""Small metadata views for the categorized main document corpus."""
import collections,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'delivery/archive-directory'),str(ROOT/'deploy')]
import server,explore,bulk_laws,record_facets,categories
from exporters.core_documents import connect
from import_catalog import normalize
OUT=ROOT/'_transfer_scratch/supabase_export/core'
def main():
    groups=[json.loads(x) for x in (OUT/'display_groups.jsonl').open(encoding='utf-8')]
    kept={v for g in groups for v in g['member_ids']}
    local,meta=explore._local_rows('all',(str(OUT),))
    selected=[r for r in local if r['id'] in kept]
    with connect(server.DB) as db:
        memberships=collections.defaultdict(list)
        for rid,g in db.execute('select record_id,group_name from record_groups'):
            if rid in kept:memberships[rid].append(g)
        summary=server.enriched_summary(json.loads(db.execute("select payload from settings where key='summary'").fetchone()[0]));summary.pop('datasets',None)
    rows=[{k:r[k] for k in ('id','display_id','dataset','states','categories','saved_text','saved_file','has_link')}|{'groups':memberships[r['id']]} for r in selected]
    with bulk_laws.connect() as db:
        bulk=[dict(r) for r in db.execute('select state,kind,count(*) records,sum(length(trim(coalesce(text,\'\')))>0) text_records from records group by state,kind') if categories.classify(r['kind'])!='other']
    for r in bulk:r.update(state=bulk_laws.state_names().get(r['state'],r['state']),category=categories.classify(r['kind']),dataset='open_us_law')
    summary['deduplication'].update(source_records=len(kept),display_groups=len(groups),grouped_record_difference=len(kept)-len(groups))
    summary['enrichment']['open_us_law'].update(records=sum(r['records'] for r in bulk),indexed_records=sum(r['records'] for r in bulk))
    summary['migration']={'selection':'Mapped semantic categories only; source originals preserved locally','excluded_main_records':21,'excluded_open_us_law_records':9994}
    descriptors=[json.loads(p.read_text()) for p in OUT.glob('*.dataset.json')]
    contexts={'core:summary':summary,'core:explore':{'local':rows,'bulk':bulk,'datasets':meta.get('datasets',[]),'bulk_info':bulk_laws.info()},
      'core:configuration':{'states':sorted({s for d in descriptors for s in d.get('states',[])}),'kinds':sorted({s for d in descriptors for s in d.get('kinds',[])}),'facets':server.facet_options(),'date_types':record_facets.DATE_TYPES,'labels':record_facets.LABELS['category'],'datasets':[d['id'] for d in descriptors]}}
    with (OUT/'contexts.jsonl').open('w',encoding='utf-8') as stream:
        for key,value in contexts.items():
            value=normalize({'id':'context','dataset':'context','category':'directories','item':value})['item']
            raw=json.dumps(value,ensure_ascii=False,separators=(',',':'))
            stream.write(json.dumps({'key':key,'data':value,'source_sha256':hashlib.sha256(raw.encode()).hexdigest()},ensure_ascii=False)+'\n')
    with (OUT/'groups.table.jsonl').open('w',encoding='utf-8') as stream:
        for g in groups:stream.write(json.dumps({'id':g['id'],'preferred_id':g['preferred_id'],'metadata':g},ensure_ascii=False)+'\n')
    print(json.dumps({'groups':len(groups),'contexts':len(contexts),'explore_local_rows':len(rows),'bulk_counts':sum(r['records'] for r in bulk)}))
if __name__=='__main__':main()
