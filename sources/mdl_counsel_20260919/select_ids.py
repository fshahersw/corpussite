"""Freeze the by-id attorney packet (round 3) -> seeds.jsonl. Offline; reads saved search receipts only.

Why by id: the connector's tool schemas are again exposed without parameter types, so `call_endpoint` rejects
`num_results` (needs integer) and `query` (needs object); paging attorneys by docket is impossible from this harness.
`get_endpoint_item` accepts string arguments (endpoint_id, item_id, fields), one request per record.

Selection rule (deterministic, documented, NOT a leadership roster and NOT a random sample):
  candidates = attorney ids listed in the CourtListener search-index `attorney_id` set of the five name-only master
  dockets; rank by (number of the five master dockets whose set lists the id, descending; id ascending); take LIMIT.
  An id listed for several master dockets is one CourtListener attorney record (same name + contact block) appearing in
  several MDLs, so one request yields rows for several MDLs.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECEIPTS = HERE / 'receipts'
JPML = HERE.parent / 'jpml_mdl_20260919' / 'mdls.jsonl'
TARGET_MDLS = (2738, 2846, 2873, 3060, 2789)
LIMIT = 30
PER_MDL = 6
FIELDS = 'id,name,contact_raw,date_modified'


def main():
    by_docket = {}
    for line in JPML.open(encoding='utf-8'):
        if line.strip():
            row = json.loads(line)
            docket_id = (row.get('cl_links') or {}).get('docket_id')
            if docket_id:
                by_docket[int(docket_id)] = int(row['mdl_number'])
    manifest = json.loads((RECEIPTS / 'manifest.json').read_text(encoding='utf-8'))
    sets = {}
    for entry in manifest['responses']:
        if entry['is_error'] or entry['tool'].split('__')[-1] != 'search':
            continue
        data = json.loads((RECEIPTS / entry['file']).read_text(encoding='utf-8'))
        for res in data.get('results') or []:
            mdl = by_docket.get(int(res.get('docket_id') or 0))
            if mdl in TARGET_MDLS:
                sets[mdl] = {'ids': {int(i) for i in res.get('attorney_id') or []}, 'receipt_file': entry['file']}
    listed = {}
    for mdl, info in sets.items():
        for aid in info['ids']:
            listed.setdefault(aid, []).append(mdl)
    # Measured 2026-09-19: all 4,406 candidate ids are listed for exactly ONE master docket, so the overlap rank never
    # applies; the effective rule is "the PER_MDL lowest attorney ids of each master docket's set" (lowest id = the
    # CourtListener attorney records created earliest for that docket), MDLs in TARGET_MDLS order.
    ranked = [aid for mdl in TARGET_MDLS for aid in sorted(sets.get(mdl, {'ids': set()})['ids'])[:PER_MDL]]
    rows = [{'seq': n, 'endpoint_id': 'attorneys', 'item_id': str(aid), 'fields': FIELDS, 'listed_for_mdls': sorted(listed[aid]),
             'basis': 'id listed in the search-index attorney_id set of these master dockets (receipts '
                      + ', '.join(sorted({sets[m]['receipt_file'][:3] for m in listed[aid]})) + ')'}
            for n, aid in enumerate(ranked[:LIMIT], 1)]
    with (HERE / 'seeds.jsonl').open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    overlap = {}
    for aid, mdls in listed.items():
        overlap[len(mdls)] = overlap.get(len(mdls), 0) + 1
    print(json.dumps({'candidate_ids': len(listed), 'ids_by_number_of_mdl_sets': overlap, 'selected': len(rows),
                      'selected_per_mdl': {m: sum(1 for r in rows if m in r['listed_for_mdls']) for m in TARGET_MDLS}}))
    for row in rows:
        print(row['item_id'], row['listed_for_mdls'])


if __name__ == '__main__':
    main()
