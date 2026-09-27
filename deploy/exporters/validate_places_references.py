"""Compare exported routes with the running local native API (read-only)."""
import json
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '_transfer_scratch/supabase_export'


def main():
    cases = []
    tests = [
        ('/api/counties', {'state': 'CA', 'availability': 'litigation_resources'}),
        ('/api/counties', {'q': 'Orange'}),
        ('/api/judges', {'has': 'photo'}),
        ('/api/judges', {'has': 'analysis'}),
        ('/api/judges', {'has': 'reports'}),
        ('/api/judges', {'state': 'Montana'}),
        ('/api/judges', {'q': 'M. Casey', 'sort': 'name'}),
        ('/api/judges', {'status': 'active', 'system': 'federal'}),
        ('/api/people', {}),
        ('/api/people', {'include_aliases': '1', 'q': 'john rob'}),
        ('/api/people', {'court': 'scotus'}),
        ('/api/sources', {'category': 'court_forms', 'jurisdiction': 'California'}),
        ('/api/sources', {'category': 'court_rules', 'order': 'registry', 'limit': '7'}),
        ('/api/mdls', {}),
        ('/api/mdls', {'status': 'all', 'q': 'products', 'sort': 'title'}),
        ('/api/mdls', {'court': 'flnd', 'min_pending': '10'}),
        ('/api/mdls/for-person', {'cl_person_id': '2268'}),
    ]
    for path, params in tests:
        with urlopen('http://127.0.0.1:8769' + path + '?' + urlencode(params), timeout=90) as response:
            value = json.load(response)
        rows = value.get('items', value.get('results', []))
        expected = {'total': value.get('total'), 'ids': [str(r.get('id') or r.get('geoid') or r.get('mdl_number')) for r in rows]}
        if path == '/api/judges': expected['courts'] = value['courts']
        if path == '/api/counties': expected['availabilities'] = value['availabilities']
        if path == '/api/mdls': expected['facets'] = value['facets']
        cases.append({'path': path, 'params': params, 'expected': expected})
    (OUTPUT / 'native-parity-cases.json').write_text(json.dumps(cases, ensure_ascii=False), encoding='utf8')
    print('Saved %s native parity cases' % len(cases))


if __name__ == '__main__': main()
