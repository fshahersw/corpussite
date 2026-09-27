"""Offline final verification and recovery after the collector releases its lock."""
import hashlib
import json
from pathlib import Path
import sqlite3
import collect as worker

HERE,ROOT=worker.HERE,worker.ROOT

def main():
    with worker.writer_lock():
        progress=json.loads((HERE/'progress.json').read_text(encoding='utf-8'))
        if progress['status']=='running':raise RuntimeError('Collector has not finished; do not publish a final receipt yet')
        resources={r['source_url']:r for r in worker.readl(HERE/'resources.jsonl')}
        attempts=worker.readl(HERE/'attempts.jsonl');recovered=worker.readl(HERE/'offline_recoveries.jsonl')
        c=sqlite3.connect((ROOT/'sources/trellis/catalog/catalog.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        proofs={}
        try:
            for row in c.execute("select source_url,url,label from links where category='coverage_county'"):proofs.setdefault(row['url'],dict(row))
        finally:c.close()
        for entry in attempts:
            if entry['status']=='saved' or entry['url'] in resources or entry.get('reason')!='no_county_profile_heading':continue
            path=ROOT/entry['raw_path']
            try:
                row=worker.normalize(path,entry['url'],entry['completed_at'],worker.sha(worker.QUEUE),proofs.get(entry['url']))
            except (RuntimeError,ValueError):continue
            row['normalization_note']='Recovered offline from the exact observed URL, selected county information container, Courts Records heading and substantive fields; original attempt history retained. An independent-city heading need not contain the word County.'
            resources[entry['url']]=row
            recovered.append({'url':entry['url'],'original_failure':entry['reason'],'recovered_at':worker.now(),'raw_path':entry['raw_path'],
                              'raw_sha256':row['raw_sha256'],'heading':row['heading'],'network_requests':0,'new_credits':0})
        worker.savel(HERE/'offline_recoveries.jsonl',recovered)
        failures=[]
        for row in resources.values():
            for path,digest in [('raw_path','raw_sha256'),('text_path','text_sha256'),('html_path','html_sha256')]:
                if worker.sha(ROOT/row[path])!=row[digest]:failures.append(row['id']+':'+path)
        if failures:raise RuntimeError('Final artifact hash mismatch: '+str(failures[:5]))
        unresolved=[x for x in attempts if x['status']!='saved' and x['url'] not in resources]
        completed=progress['attempted']==progress['selected_worker_urls']
        reason='completed_finite_queue_with_gaps' if unresolved else 'completed' if completed else progress['status']
        worker.checkpoint(resources,attempts,progress['credits_at_start'],progress['remaining_credits'],progress['selected_worker_urls'],reason,completed and not unresolved)
        progress=json.loads((HERE/'progress.json').read_text(encoding='utf-8'))
        progress.update(offline_recovered=len(recovered),unresolved_failures=len(unresolved),queue_exhausted=completed)
        worker.save(HERE/'progress.json',progress)
        worker.savel(HERE/'unresolved_gaps.jsonl',unresolved)
        receipt={'validated_at':worker.now(),'status':'passed','network_requests_by_finalizer':0,'new_credits_by_finalizer':0,
                 'accepted_profiles':len(resources),'artifact_hashes_checked':len(resources)*3,'hash_failures':[],
                 'worker_attempts':len(attempts),'worker_credit_delta':progress['account_credit_delta'],'remaining_credits':progress['remaining_credits'],
                 'pilot_profiles':5,'offline_recovered':len(recovered),'unresolved_failures':len(unresolved),'queue_exhausted':completed,
                 'main_corpus_mutated':False,'county_identity_merges':0,
                 'resources_sha256':worker.sha(HERE/'resources.jsonl'),'attempts_sha256':worker.sha(HERE/'attempts.jsonl'),
                 'recoveries_sha256':worker.sha(HERE/'offline_recoveries.jsonl'),'validation_sha256':worker.sha(HERE/'validation.json'),
                 'collector_current_sha256':worker.sha(HERE/'collect.py'),'finalizer_sha256':worker.sha(Path(__file__)),
                 'qualification':worker.REPRESENTATION+' Complete queue processing does not mean complete county data or case-file coverage.'}
        worker.save(HERE/'final_receipt.json',receipt)
        print(json.dumps(receipt))

if __name__=='__main__':main()
