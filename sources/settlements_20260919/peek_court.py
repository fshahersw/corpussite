"""Eyeball the court docket layer after the settlement-specific phrase split (read-only)."""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'delivery' / 'archive-directory'))
import settlements  # noqa: E402

docs = [json.loads(l) for l in (HERE / 'court_documents.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
print('types', dict(Counter(d['type'] for d in docs)))
for d in docs:
    if d['type'] in ('case_management_or_settlement_order', 'case_management_order'):
        print(' ', d['mdl_number'], d['type'], d['matched_settlement_specific_terms'], d['matched_non_specific_terms'])
listing = settlements.listing({'has_court_documents': 'yes', 'limit': '100'})
for row in listing['results']:
    print(row['id'], '|', row['title'], '|', row['badges'][1:], '|', row['cells']['documents'])
item = settlements.detail('mdl-docket-3060')
print(item['subtitle'])
for fact in item['facts'][:3]:
    print(' ', fact)
print([s['heading'] for s in item['sections']])
print(item['sections'][1]['items'][1]['title'])
print(item['sections'][1]['items'][1]['subtitle'][-160:])
print((HERE / 'edges.jsonl').read_text(encoding='utf-8').count('\n'), 'edges;', '3060' in (HERE / 'edges.jsonl').read_text(encoding='utf-8'))
