"""Read-only peek: which JPML MDLs already carry a verified CourtListener docket id."""
import json
from pathlib import Path

J = Path(__file__).resolve().parents[1] / 'jpml_mdl_20260919'
rows = [json.loads(l) for l in (J / 'mdls.jsonl').open(encoding='utf-8') if l.strip()]
hit = [r for r in rows if (r.get('cl_links') or {}).get('docket_id')]
hit.sort(key=lambda r: -(r.get('actions_pending') or 0))
for r in hit:
    c = r['cl_links']
    print(r['mdl_number'], '|', r.get('litigation_type'), '|', r.get('actions_pending'), '|', r.get('total_actions'), '|',
          c['docket_id'], c['court_id'], c['docket_number'], '|', r['title'][:70])
