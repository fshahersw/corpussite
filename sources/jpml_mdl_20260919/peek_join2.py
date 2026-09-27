"""Read-only peeks at join inputs: profiles.sqlite3 schema, CourtListener FD courts, judge enrichment facts layout."""
import json
import sqlite3
from pathlib import Path

ROOT = Path('C:/Users/firas/Downloads/SCRAPE')


def ro(p):
    return sqlite3.connect('file:%s?mode=ro' % Path(p).as_posix(), uri=True)


print('=== profiles.sqlite3')
con = ro(ROOT / 'sources/judge_presentation_20260918/profiles.sqlite3')
for (name, sql) in con.execute("select name, sql from sqlite_master where type='table'"):
    print(name, '::', (sql or '')[:700].replace('\n', ' '))
for (name,) in con.execute("select name from sqlite_master where type='table'"):
    print(name, con.execute('select count(*) from "%s"' % name).fetchone())
con.close()

print('=== courtlistener courts (FD)')
p = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
if p.exists():
    con = ro(p)
    print([c[1] for c in con.execute('pragma table_info(courts)').fetchall()])
    rows = con.execute("select id, full_name, short_name, fjc_court_id from courts where jurisdiction='FD' order by id").fetchall()
    print('FD courts', len(rows))
    for r in rows:
        print(r)
    print('positions cols', [c[1] for c in con.execute('pragma table_info(positions)').fetchall()])
    print('people cols', [c[1] for c in con.execute('pragma table_info(people)').fetchall()])
    con.close()
else:
    print('missing', p)

print('=== federal_biographies/facts.jsonl')
fp = ROOT / 'delivery/judge_enrichment_20260914/federal_biographies/facts.jsonl'
n = 0
keys = set()
with fp.open(encoding='utf-8') as fh:
    for line in fh:
        n += 1
        if n <= 2:
            print(line[:1500])
        keys.update(json.loads(line).keys())
print('rows', n, 'keys', sorted(keys))
