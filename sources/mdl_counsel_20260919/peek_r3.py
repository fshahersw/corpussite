"""Inspection helper (round 3): print the by-id attorney rows, per-MDL coverage labels and unresolved rows."""
import json
from pathlib import Path

D = Path(__file__).resolve().parent
for line in (D / 'attorneys.jsonl').open(encoding='utf-8'):
    r = json.loads(line)
    if r.get('record_scope') == 'id_name_firm_line_only':
        print(r['mdl_number'], r['cl_attorney_id'], '|', r['name'], '|', r['firm_text'], '|', r['city'], r['state'])
cov = json.loads((D / 'coverage.json').read_text(encoding='utf-8'))
for k, v in cov['mdls'].items():
    print(k, v['attorney_name_only_rows_suppressed_as_same_name_as_a_record'], '|', v['attorney_coverage_label'])
print((D / 'unresolved.jsonl').read_text(encoding='utf-8')[:400])
