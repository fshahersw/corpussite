"""Search sealed professional judge observations and official evaluations."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'delivery/judge_enrichment_20260914'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query', nargs='?', default='')
    parser.add_argument('--state')
    parser.add_argument('--source')
    parser.add_argument('--system', choices=['state','federal','administrative','unknown'])
    parser.add_argument('--court')
    parser.add_argument('--has-analysis', action='store_true')
    parser.add_argument('--analysis-type', help='Literal analysis type in analyses.jsonl')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--report', help='Judge ID returned by search')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit must be 1..100')
    latest = read(BASE / 'latest.json')
    review = (ROOT / latest['independent_review_path']).resolve()
    review.relative_to(ROOT)
    if sha(review) != latest['independent_review_sha256']:
        raise ValueError('Independent review receipt changed')
    snap = (ROOT / latest['snapshot_path']).resolve()
    snap.relative_to((BASE / 'snapshots').resolve())
    manifest_path = snap / 'files.sha256.json'
    if sha(manifest_path) != latest['manifest_sha256']:
        raise ValueError('Snapshot manifest changed')
    receipt = read(review)
    if receipt.get('validated') is not True or receipt.get('unresolved_material_findings') != 0:
        raise ValueError('Independent review did not pass')
    if receipt.get('snapshot_path') != snap.relative_to(ROOT).as_posix() or receipt.get('manifest_sha256') != latest['manifest_sha256']:
        raise ValueError('Independent review is bound to a different snapshot')
    dbpath = snap / 'judge_corpus.sqlite3'
    item = read(manifest_path)[dbpath.relative_to(ROOT).as_posix()]
    if sha(dbpath) != item['sha256']:
        raise ValueError('Sealed database changed')
    db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    if args.report:
        item = db.execute('SELECT payload FROM judges WHERE judge_id=?', (args.report,)).fetchone()
        if not item:
            parser.error('Judge ID not found')
        report = json.loads(item['payload'])
        for table, key in [('observations','source_observations'), ('facts','fact_claims'), ('analyses','analysis')]:
            report[key] = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM ' + table + ' WHERE judge_id=?', (args.report,))]
        report['limitations'] = ['Source-reported claims retain conflicts and historical dates.', 'Performance evaluations are not litigation outcome rates.', 'Current service and unique national coverage are not certified.']
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        joins = ''
        clauses = []
        params = []
        query = args.query.strip()
        if query:
            joins = ' JOIN observation_search s ON s.observation_id=o.observation_id'
            clauses.append('observation_search MATCH ?')
            params.append('"' + query.replace('"','""') + '"')
        for value, col in [(args.state.upper() if args.state else None,'o.state_code'), (args.source,'o.source_class'), (args.system,'o.judge_system')]:
            if value:
                clauses.append(col + '=?')
                params.append(value)
        if args.court:
            clauses.append('instr(lower(o.courts),lower(?))>0')
            params.append(args.court)
        if args.has_analysis:
            clauses.append('EXISTS(SELECT 1 FROM analyses a WHERE a.observation_id=o.observation_id)')
        if args.analysis_type:
            clauses.append('EXISTS(SELECT 1 FROM analyses a WHERE a.observation_id=o.observation_id AND a.analysis_type=?)')
            params.append(args.analysis_type)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        sql = 'SELECT o.observation_id,o.judge_id,o.name,o.state_code,o.judge_system,o.source_class,o.courts,(SELECT COUNT(*) FROM analyses a WHERE a.observation_id=o.observation_id) AS analysis_records FROM observations o' + joins + where + ' ORDER BY o.name,o.observation_id LIMIT ?'
        params.append(args.limit)
        result = []
        for row in db.execute(sql, params):
            item = dict(row)
            item['courts'] = json.loads(item['courts'])
            result.append(item)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    db.close()

if __name__ == '__main__':
    main()
