"""Offline: print the frozen plan (store scan sizes, skips, per-host fetch counts)."""
import json, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
receipt = json.loads((HERE / 'prepare_receipt.json').read_text(encoding='utf-8'))
for key, value in receipt['saved_url_stores_scanned'].items(): print(value, key)
seeds = [json.loads(x) for x in (HERE / 'seeds.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
for s in seeds:
    if s['decision'].startswith('skip'): print(s['decision'], s['state'], s['url'])
print(collections.Counter(s['host'] for s in seeds if s['decision'].startswith('fetch')).most_common(12))
print('pdf', [s['url'] for s in seeds if s['decision'] == 'fetch_http_document'])
