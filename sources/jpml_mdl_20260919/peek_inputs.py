"""Read-only peeks at join inputs (CourtListener courts table, judge entity appointment shapes)."""
import json
import sqlite3
from pathlib import Path

ROOT = Path('C:/Users/firas/Downloads/SCRAPE')
db = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
con = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True)
rows = con.execute("select id, full_name, short_name, jurisdiction, fjc_court_id, in_use, end_date from courts where jurisdiction='FD' order by id").fetchall()
print('FD courts', len(rows))
for r in rows:
    print(r)
print('people rows', con.execute('select count(*) from people').fetchone())
print('positions cols', [c[1] for c in con.execute('pragma table_info(positions)').fetchall()])
print('sample positions', con.execute("select id, person_id, court_id, position_type, date_start, date_termination from positions where court_id='njd' limit 5").fetchall())
con.close()

# judge entity appointment shapes
n = 0
with (ROOT / 'sources/judge_entities_20260918/entities.jsonl').open(encoding='utf-8') as fh:
    for line in fh:
        e = json.loads(line)
        if e.get('appointments') and any('fjc' in json.dumps(a).lower() for a in e['appointments']):
            print(json.dumps({k: e.get(k) for k in ('entity_id', 'display_name', 'name', 'appointments', 'courts', 'aliases')}, ensure_ascii=False)[:2500])
            n += 1
            if n >= 2:
                break
print('entity keys', sorted(e.keys()))
