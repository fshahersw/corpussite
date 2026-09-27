"""Save read-only native navigation expectations for the migration review."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
import jurisdiction_coverage as coverage
import trellis_coverage as trellis
import local_library
import doj_resources as doj


def compact(value):
    return {'total': value.get('total'),
            'ids': [str(row.get('provision_id') or row.get('record_id') or row.get('id')) for row in value.get('items', [])],
            **{key: value[key] for key in ('page', 'page_size', 'limit', 'by_source_tier') if key in value}}


def main():
    cases = []
    for params in ({'topic': 'sol', 'limit': '5'}, {'state': 'Montana', 'topic': 'jurisdiction', 'limit': '7'},
                   {'state': 'CA', 'limit': '8'}, {'state': 'invalid', 'topic': 'sol'}):
        value = coverage.topics(state=params.get('state'), topic=params.get('topic'), limit=int(params.get('limit',25)))
        cases.append({'path': '/api/coverage/topics', 'params': params, 'expected': compact(value)})
    for params in ({'state': 'MT', 'limit': '7'}, {'detail': 'true', 'state': 'CA', 'limit': '9'}, {'q': 'Orange', 'limit': '8'}):
        values = dict(params)
        values['limit'] = int(values.get('limit',50))
        if 'detail' in values: values['detail'] = values['detail'] == 'true'
        cases.append({'path': '/api/trellis-coverage', 'params': params, 'expected': compact(trellis.listing(**values))})
    for card in local_library.collections()[:2]:
        params = {'id': card['id']}
        cases.append({'path': '/api/collection', 'params': params, 'expected': compact(local_library.collection(card['id']))})
    for params in ({'state': 'Montana', 'limit': '9'}, {'state': 'federal', 'q': 'court', 'limit': '5'}):
        cases.append({'path': '/api/resources/state', 'params': params, 'expected': compact(doj.state_resources(params['state'], params=params))})
    output = ROOT / '_transfer_scratch/supabase_export/navigation-review-fixtures.json'
    output.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'cases': len(cases)}))


if __name__ == '__main__': main()
