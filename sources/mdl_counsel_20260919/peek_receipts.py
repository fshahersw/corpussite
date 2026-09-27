"""Read-only peek at the search receipts: set sizes per docket (no contact data printed)."""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / 'receipts'
man = json.loads((R / 'manifest.json').read_text(encoding='utf-8'))
for e in man['responses']:
    if e['tool'].endswith('__search') and not e['is_error']:
        d = json.loads((R / e['file']).read_text(encoding='utf-8'))
        r = d['results'][0]
        print(e['file'], e['spilled_by_harness'], d.get('count'), r['docket_id'], r['docketNumber'],
              {k: len(r.get(k) or []) for k in ('attorney', 'attorney_id', 'firm', 'firm_id', 'party', 'party_id')},
              'keys', sorted(d.keys()))
        print('   sample firms', (r.get('firm') or [])[:4], '| sample parties', (r.get('party') or [])[:3])
errs = [(e['file'], e['tool'].split('__')[-1]) for e in man['responses'] if e['is_error']]
print('errors', errs)
