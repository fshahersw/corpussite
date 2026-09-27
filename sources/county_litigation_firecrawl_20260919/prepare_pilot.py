from pathlib import Path
import sqlite3
import collect as c

source=c.ROOT/'sources/county_litigation_20260919/seeds_orange_ca.jsonl'
packet=[r for r in c.rows(source) if r['url'] in {
    'http://www.occourts.org/directory/family/records.html',
    'https://www.ocgov.com/residents/law-justice'}]
for item in packet:
    c.seed_ok(item)
    parent=c.ROOT/item['parent_raw_path']
    if c.sha(parent.read_bytes())!=item['parent_raw_sha256']:raise ValueError('Parent capture hash mismatch')
c.save_rows(c.HERE/'pilot_seeds.jsonl',packet)
c.save(c.HERE/'plan.json',{'created_at':c.now(),'pilot_request_cap':50,'pilot_initial_seeds':2,'source_seed_manifest':c.rel(source),'source_seed_sha256':c.sha(source.read_bytes()),'per_host_parallel':1,'total_parallel':5,'per_host_minimum_delay':2,'robots':'shared corpus controls required','paid_document_parsing':False,'automatic_retries':0,'depth_limit':2,'discovery':'review exact returned links before admission','originals':'HTTP byte originals for documents; Firecrawl JSON/HTML separately labeled provider capture','scope_complete':False})
print({'pilot_seeds':len(packet),'hash_bound_parent_evidence':True})
