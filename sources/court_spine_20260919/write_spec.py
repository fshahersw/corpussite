"""Writes integration_spec.json with real example responses from the adapter (run after build.py)."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parents[1] / 'delivery/archive-directory/court_spine.py'
spec = importlib.util.spec_from_file_location('court_spine_adapter', ADAPTER)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

example_listing = a.listing({'q': 'new jersey', 'type': 'FD', 'limit': '1'})
example_listing['filters'] = [dict(f, options=f['options'][:3]) if 'options' in f else f for f in example_listing['filters']]
example_detail = a.detail('njd')
county_id = a.listing({'q': '29th Judicial Circuit', 'state': 'KY', 'type': 'CR:Circuit Court'})['results'][0]['id']
gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))

out = {
    'adapter': 'delivery/archive-directory/court_spine.py',
    'contract': 'ROUND 3 generic view contract',
    'functions': {
        'listing(params: dict) -> dict': 'params (strings): q, system, type, state, has_logo (yes|no), has_mdls (yes|no), page, limit (<=100). Fails closed with {"available": false, "reason": ...}.',
        'detail(id: str) -> dict | None': 'id is a CourtListener court id (e.g. njd), reg-<FAMILY>-<key> for local registry entries without a CourtListener id, or cty-... for Kentucky/Texas county registry courts.',
        'original(file_id: str) -> (bytes, mime, filename) | None': 'file_id = logo_<16 hex>; serves image/png|jpeg|gif|webp only, after re-hashing the stored bytes. Never SVG.',
    },
    'routes': [
        {'method': 'GET', 'path': '/api/supplement/court_spine', 'params': ['q', 'system', 'type', 'state', 'has_logo', 'has_mdls', 'page', 'limit'], 'calls': 'listing'},
        {'method': 'GET', 'path': '/api/supplement/court_spine/<id>', 'calls': 'detail', '404_when': 'None'},
        {'method': 'GET', 'path': '/supplement-files/court_spine/<file_id>', 'calls': 'original', 'headers': 'Content-Type from the tuple; X-Content-Type-Options: nosniff; Content-Disposition inline with the returned filename', '404_when': 'None'},
    ],
    'ui': {
        'area': 'Courts', 'route': '#courts', 'columns': ['Court', 'System', 'State', 'MDLs'],
        'filters': ['Search', 'System', 'Court type', 'State (only where explicit)', 'Identity mark', 'Pending MDLs'],
        'default_order': 'pending MDL count (desc), courts with a saved mark, CourtListener rows before registry rows, name',
        'detail_links': ['#mdls?court=<cl id> (only when the JPML report lists MDLs for the court)', '#documents?q=<court name>', 'court website as recorded', '/supplement-files/court_spine/<file_id>'],
        'caveats': [
            gate['qualification'],
            'State is shown only where a state name is printed in the court (or parent court) name or in the registry record; otherwise it is left blank.',
            'Identity marks were observed on official court sites on 2026-09-12; reuse permission is not established. Shared and statewide marks are labelled and are never propagated to other courts.',
            'Kentucky/Texas locations are county listings from the county registry as of 2026-08-22; no street addresses are recorded and phone numbers are not shown.',
        ],
    },
    'edges': 'none emitted (the court id is the join key: cl_court:<id>; county FIPS in locations[] and county_fips join to fips:<5char>; mdl join is on mdls.jsonl cl_court_id).',
    'counts': gate['counts'],
    'examples': {'listing?q=new jersey&type=FD&limit=1 (filter options truncated to 3)': example_listing, 'detail/njd': example_detail, 'detail/' + county_id: a.detail(county_id)},
}
(HERE / 'integration_spec.json').write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
print(json.dumps(example_detail, indent=1, ensure_ascii=False)[:3500])
print(json.dumps(a.detail(county_id), ensure_ascii=False)[:1500])
print(json.dumps(a.listing({'limit': '3'})['results'], ensure_ascii=False)[:1500])
