"""Search saved judge observations with source and jurisdiction filters."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('query', nargs='?', default='')
    p.add_argument('--state')
    p.add_argument('--source', choices=['official_observation', 'trellis_profile', 'trellis_directory'])
    p.add_argument('--court')
    p.add_argument('--county')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--report', action='store_true', help='Return complete evidence-linked reports')
    a = p.parse_args()
    latest = json.loads((ROOT / 'delivery/judge_intelligence_20260913/latest.json').read_text(encoding='utf-8'))
    snapshot = (ROOT / latest['snapshot_path']).resolve()
    snapshot.relative_to((ROOT / 'delivery/judge_intelligence_20260913/snapshots').resolve())
    manifest_path = snapshot / 'files.sha256.json'
    if sha(manifest_path) != latest['manifest_sha256']:
        raise ValueError('Snapshot manifest changed after publication')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    database = snapshot / 'judge_search.sqlite3'
    entry = manifest[database.relative_to(ROOT).as_posix()]
    if sha(database) != entry['sha256']:
        raise ValueError('Search database changed after publication; rebuild the snapshot')
    db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    sql = 'SELECT o.report_json FROM observations o'
    clauses, values = [], []
    if a.query.strip():
        sql += ' JOIN search s ON s.record_id=o.record_id'
        # Literal token phrases; never interpolate user input into FTS grammar.
        clauses.append('search MATCH ?')
        values.append(' AND '.join('"' + token.replace('"', '""') + '"' for token in a.query.split()))
    for field, value in [('state_code', a.state.upper() if a.state else None), ('source_class', a.source)]:
        if value:
            clauses.append('o.' + field + '=?'); values.append(value)
    for field, value in [('courts', a.court), ('counties', a.county)]:
        if value:
            clauses.append('EXISTS(SELECT 1 FROM json_each(o.' + field + ') WHERE instr(lower(value),lower(?))>0)'); values.append(value)
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' ORDER BY o.name,o.source_class,o.record_id LIMIT ?'
    values.append(max(1, min(a.limit, 100)))
    for (text,) in db.execute(sql, values):
        row = json.loads(text)
        if not a.report:
            row = {key: row[key] for key in ['record_id', 'source_class', 'name', 'state_code', 'courts', 'counties', 'status', 'source_url', 'evidence_file', 'evidence_line']}
        print(json.dumps(row, ensure_ascii=True))
    db.close()

if __name__ == '__main__':
    main()
