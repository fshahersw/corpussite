"""Offline: one line per saved resource for a human spot-check (state, kind, size, title)."""
import json, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
rows = [json.loads(x) for x in (HERE / 'resources.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
for r in rows:
    print(r['state'], r['doc_kind'], r['family'], r['raw_bytes'], r['text_characters'], '|', (r['title'] or '')[:70], '|', r['source_url'][:80], '' if r['final_url'] == r['source_url'] else '-> ' + r['final_url'])
print(sorted(collections.Counter(r['state'] for r in rows).items()))
print(collections.Counter(r['family'] for r in rows))
gaps = json.loads((HERE / 'gaps.json').read_text(encoding='utf-8'))['gaps']
print(sorted({g['state'] for g in gaps} - {r['state'] for r in rows}))
