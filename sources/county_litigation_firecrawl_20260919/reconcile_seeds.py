"""Apply root-reviewed tighter seed scope without retrying uncertain requests."""
import json
import collect as c

path=c.ROOT/'sources/county_litigation_seed_review_20260919/seeds.jsonl'
expected='71b25436db204c3934392aebbc4c702058c7b95ea51268b3697624a1f3855a15'
raw=path.read_bytes()
if c.sha(raw)!=expected:raise ValueError('Reviewed seed manifest digest changed')
new={x['url']:x for x in c.rows(path)}
r=c.Runner(0,1,1)
try:
 r.ingest(path);removed=[];refreshed=0
 archive=c.HERE/'seed_inputs'/f'{expected}.jsonl'
 for row in r.db.execute("select url,seed_json from queue where status='pending'").fetchall():
  old=json.loads(row['seed_json'])
  if not old.get('source_batch'):continue
  if row['url'] not in new:
   r.db.execute("update queue set status='excluded_seed_review',error='Removed by root tighter reviewed seed manifest',updated_at=? where url=?",(c.now(),row['url']));removed.append(row['url'])
  else:
   seed={**new[row['url']],'input_manifest':{'path':c.rel(archive),'sha256':expected}}
   r.db.execute('update queue set seed_json=?,priority=?,county_key=?,updated_at=? where url=?',(json.dumps(seed),seed['priority'],seed['county_geoids'][0],c.now(),row['url']));refreshed+=1
 r.db.commit();r.export()
 report={'reconciled_at':c.now(),'new_manifest_sha256':expected,'new_seeds':len(new),'pending_removed':len(removed),'removed_urls':removed,'pending_provenance_refreshed':refreshed,'completed_preserved':True,'uncertain_preserved':True,'automatic_retry':False,'statuses':dict(r.db.execute('select status,count(*) from queue group by status'))}
 c.save(c.HERE/'seed_reconciliation.json',report);print(json.dumps({k:v for k,v in report.items() if k!='removed_urls'}))
finally:r.db.close()
