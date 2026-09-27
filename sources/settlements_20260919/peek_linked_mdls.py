"""List MDLs in sources/jpml_mdl_20260919 that carry a verified CourtListener master docket id (read-only peek)."""
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'jpml_mdl_20260919'
first = True
for line in (SRC / 'mdls.jsonl').read_text(encoding='utf-8').splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    if first:
        print('KEYS', sorted(row.keys()))
        first = False
    blob = json.dumps(row)
    if 'cl_docket_id' in blob and row.get('cl_docket_id') or (row.get('cl_links') or row.get('courtlistener')):
        print(row.get('mdl_number'), '|', row.get('cl_docket_id'), '|', (row.get('title') or row.get('caption') or '')[:70], '|',
              json.dumps(row.get('cl_links') or row.get('courtlistener'))[:400])
n = 0
rel = {}
for line in (SRC / 'edges.jsonl').read_text(encoding='utf-8').splitlines():
    if not line.strip():
        continue
    e = json.loads(line)
    rel[e.get('relation')] = rel.get(e.get('relation'), 0) + 1
    if 'docket' in json.dumps(e).lower() and n < 40:
        n += 1
        print('EDGE', json.dumps(e)[:400])
print(rel)
