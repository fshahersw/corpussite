"""Offline, read-only look at the prior run's 31 unresolved attempts and the county inventory. No network."""
import json, re, unicodedata
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / 'sources/trellis_county_firecrawl_20260919'

def readl(p):
    return [json.loads(x) for x in p.read_text(encoding='utf-8-sig').splitlines() if x.strip()]

resources = {r['source_url']: r for r in readl(PRIOR / 'resources.jsonl')}
gaps = readl(PRIOR / 'unresolved_gaps.jsonl')
print('resources', len(resources), 'gaps', len(gaps))
for g in gaps:
    url = g['url']
    raw = ROOT / g['raw_path']
    info = {}
    if raw.exists():
        obj = json.loads(raw.read_text(encoding='utf-8-sig'))
        m = obj.get('metadata') or {}
        md = obj.get('markdown') or ''
        info = {'status': m.get('statusCode'), 'cache': m.get('cacheState'), 'md_len': len(md), 'html_len': len(obj.get('html') or ''),
                'title': (m.get('title') or '')[:70], 'md': md[:160].replace('\n', ' | ')}
    sib = None
    if url.endswith('city'):
        s = url[:-4]
        if s in resources:
            sib = resources[s]['heading']
    print(url.split('/coverage/')[1], g['reason'], info, 'SIBLING_SAVED=' + repr(sib))

inv = readl(ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl')
print('inventory rows', len(inv), 'keys', sorted(inv[0].keys()))
def fold(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c)).casefold()
for usps, pat in [('CT', ''), ('DE', ''), ('VA', 'roanoke'), ('NM', 'ana'), ('VA', 'salem')]:
    rows = [r for r in inv if r['usps'] == usps and pat in fold(r['name'])]
    print(usps, pat, [(r['geoid'], r['name']) for r in rows])
