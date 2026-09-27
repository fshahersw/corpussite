"""Read-only look at the reviewed candidate packet and the saved-URL stores (no network)."""
import json, sqlite3, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
rows = [json.loads(l) for l in (ROOT/'reports/corpus_upgrade_20260919/understand/packets/gap_fill_candidates.jsonl').read_text(encoding='utf-8-sig').splitlines() if l.strip()]
print(len(rows), 'candidates;', len({r['url'] for r in rows}), 'unique urls;', len({r['state'] for r in rows}), 'states')
print('keys', sorted({k for r in rows for k in r}))
for f in ('gap_type', 'content_kind', 'access_requirements', 'directory_category'):
    print(f, dict(collections.Counter(r.get(f) for r in rows)))
print('http_status', dict(collections.Counter((r.get('directory_verification') or {}).get('http_status') for r in rows)))
print('saved_url_match', dict(collections.Counter(r.get('saved_url_match') for r in rows)))
print('cautions:')
for r in rows:
    if r.get('caution'): print(' ', r['state'], r['url'], '|', r['caution'][:110])
print('hosts', len({r['host'] for r in rows}))
for db, name in ((ROOT/'catalog/documents.sqlite3', 'catalog'), (ROOT/'delivery/archive-directory/directory.sqlite3', 'directory')):
    con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
    for t, sql in con.execute("select name, sql from sqlite_master where type in ('table','index') and (name like 'version%' or name like 'record%' or sql like '%source_url%')"):
        print(name, t, (sql or '')[:400].replace('\n', ' '))
    con.close()
