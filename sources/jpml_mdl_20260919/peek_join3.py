"""Read-only peeks: FJC fact categories/name facts, identity folder, profiles.sqlite3 row shape."""
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path('C:/Users/firas/Downloads/SCRAPE')
ENR = ROOT / 'delivery/judge_enrichment_20260914'


def ro(p):
    return sqlite3.connect('file:%s?mode=ro' % Path(p).as_posix(), uri=True)


print('=== facts categories')
cats = Counter()
shown = set()
with (ENR / 'federal_biographies/facts.jsonl').open(encoding='utf-8') as fh:
    for line in fh:
        f = json.loads(line)
        cats[(f['category'], f['field'])] += 1
        key = f['category']
        if key not in shown and key not in ('appointment', 'education'):
            shown.add(key)
            print(key, json.dumps(f['value'], ensure_ascii=False)[:400])
print(cats.most_common(40))

print('=== identity folder')
for p in sorted((ENR / 'identity').rglob('*')):
    if p.is_file():
        print(p.relative_to(ENR).as_posix(), p.stat().st_size)
for p in sorted((ENR / 'federal_biographies').rglob('*')):
    if p.is_file():
        print(p.relative_to(ENR).as_posix(), p.stat().st_size)

print('=== SCHEMA.md head')
print((ENR / 'SCHEMA.md').read_text(encoding='utf-8')[:3000])

print('=== profiles.sqlite3 sample')
con = ro(ROOT / 'sources/judge_presentation_20260918/profiles.sqlite3')
row = con.execute("select id, entity_id, name, search, has_details, card, profile from profiles where name like '%Chhabria%' limit 1").fetchone()
if row:
    print(row[:5])
    print('card', str(row[5])[:800])
    prof = json.loads(row[6]) if row[6] else None
    if isinstance(prof, dict):
        print('profile keys', sorted(prof.keys()))
        print(json.dumps({k: prof[k] for k in prof if k in ('identity', 'members', 'sources', 'ids', 'appointments', 'service', 'courts', 'external_ids', 'entity', 'fjc')}, ensure_ascii=False)[:2500])
print('courts sample', con.execute("select * from courts where id=? limit 5", (row[0],)).fetchall() if row else None)
print('systems sample', con.execute("select * from systems where id=? limit 5", (row[0],)).fetchall() if row else None)
print('court values top', con.execute("select court, count(*) from courts group by court order by 2 desc limit 15").fetchall())
con.close()
