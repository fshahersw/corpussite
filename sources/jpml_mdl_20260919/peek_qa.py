"""Print failed QA checks, unresolved judges and the top-20 product-liability MDLs (offline, read-only)."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
qa = json.loads((HERE / 'qa.json').read_text(encoding='utf-8'))
print('=== failed checks')
for c in qa['checks']:
    if c['status'] == 'failed':
        print(c['name'], json.dumps(c['detail'], ensure_ascii=False)[:700])
print('=== unresolved judges')
for line in (HERE / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines():
    u = json.loads(line)
    print(u['reason'], '|', u['judge_name_as_printed'], u['district_code'], u['mdls'], '|', json.dumps(u.get('name_matches_elsewhere') or u.get('candidates') or u.get('same_last_name_at_court'), ensure_ascii=False)[:300])
print('=== match kinds sample (middle_omitted)')
rows = [json.loads(l) for l in (HERE / 'mdls.jsonl').read_text(encoding='utf-8').splitlines()]
for r in rows:
    for jl in r['judge_links']:
        if jl['match_kind'] == 'middle_omitted':
            print(r['mdl_number'], r['transferee_judge']['name_as_printed'], '->', jl['fjc_name'], jl['entity_id'])
print('=== top 20 products liability by pending')
pl = sorted([r for r in rows if r['status'] == 'pending' and r['litigation_type'] == 'Products Liability'], key=lambda r: -(r['actions_pending'] or 0))[:20]
for r in pl:
    print(r['mdl_number'], r['cl_court_id'], r['master_docket'], r['actions_pending'], '|', r['title'][:70], '|', r['transferee_judge']['name_as_printed'], '|', (r['judge_links'][0]['fjc_nid'] if r['judge_links'] else None))
print('=== sample row 3080')
print(json.dumps([r for r in rows if r['mdl_number'] == 3080][0], ensure_ascii=False)[:2500])
print('=== circuit summaries june')
print(json.dumps(qa['printed_totals']['by_circuit_2026-06-30'], ensure_ascii=False)[:600])
