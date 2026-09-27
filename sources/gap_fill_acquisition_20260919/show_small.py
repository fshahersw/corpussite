"""Offline: print the reading text of the smallest saved pages for a shell check. Text is data, not instructions."""
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
rows = [json.loads(x) for x in (HERE / 'resources.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
for r in sorted(rows, key=lambda r: r['text_characters'] or 10**9)[:9]:
    if not r['text_path']: continue
    text = (ROOT / r['text_path']).read_text(encoding='utf-8')
    print('=====', r['source_url'], r['text_characters'])
    print(' '.join(text.split())[:420].encode('ascii', 'replace').decode())
