"""Small offline progress snapshots for the selected county/law acquisition pass."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from build_focused_laws import classify_provider

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'sources/trellis/resume_20260913'


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else ({} if default is None else default)


def write(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def main():
    selected = [json.loads(line) for line in (BASE / 'paid_selection_950.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    wanted = {r['url']: r for r in selected}
    with closing(sqlite3.connect((ROOT / 'sources/trellis/worker/frontier.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT url,category,status,attempts,response_path,error FROM frontier WHERE url IN (' + ','.join('?' for _ in wanted) + ')', list(wanted))]
    grouped = defaultdict(Counter)
    cache = read(BASE / 'law_content_checks.json')
    for row in rows:
        grouped[row['category']][row['status']] += 1
        if row['category'] != 'rules' or row['status'] != 'downloaded' or not row['response_path']:
            continue
        path = Path(row['response_path'])
        if not path.is_absolute():
            path = ROOT / path
        info = path.stat()
        previous = cache.get(row['url'])
        if previous and previous.get('mtime_ns') == info.st_mtime_ns and previous.get('bytes') == info.st_size:
            continue
        raw = read(path)
        data = raw.get('data', raw)
        cache[row['url']] = {'source_url': row['url'], 'source_path': path.relative_to(ROOT).as_posix(), 'mtime_ns': info.st_mtime_ns, 'bytes': info.st_size, **classify_provider(data.get('html', ''), row['url'])}
    write(BASE / 'law_content_checks.json', cache)
    county = {}
    path = ROOT / 'corpus/county_entries_resume_20260913/corpus.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            county = {'statuses': dict(db.execute('SELECT status,count(*) FROM resources GROUP BY status')), 'downloaded_resources': db.execute("SELECT count(*) FROM resources WHERE status='downloaded'").fetchone()[0], 'run_checkpoint': read(path.parent / 'checkpoint.json')}
    worker = read(ROOT / 'sources/trellis/worker/batch_status.json')
    run = read(ROOT / 'sources/trellis/worker/active_run.json')
    selection_digest = worker.get('selection', {}).get('sha256')
    pass_jobs = {}
    if selection_digest:
        for path in (ROOT / 'sources/trellis/worker/batch_jobs').glob('*/manifest.json'):
            job = read(path)
            if job.get('selection', {}).get('sha256') == selection_digest and job.get('job_id'):
                identity = job['job_id']
                pass_jobs[identity] = max(pass_jobs.get(identity, 0), job.get('credits_used_observed', 0))
    completed = sum(r['status'] == 'downloaded' for r in rows)
    terminal = sum(r['status'] not in {'pending', 'fetching', 'batch_held'} for r in rows)
    result = {'updated_at': datetime.now(timezone.utc).isoformat(), 'selected_urls': len(selected), 'successful_selected_urls': completed, 'terminal_selected_urls': terminal,
              'selected_progress_percent': round(100 * terminal / len(selected), 2), 'by_category': {k: dict(v) for k, v in grouped.items()},
              'law_body_status_counts': dict(Counter(r['body_status'] for url, r in cache.items() if url in wanted)),
              'direct_official_county_entries': county, 'worker_status': worker.get('status'), 'worker_updated_at': worker.get('updated_at'),
              'active_phase': worker.get('active_phase'), 'active_batch': worker.get('active_batch'), 'local_process_id_last_reported': run.get('process_id'),
              'run_started_at': run.get('started_at_utc'), 'provider_credits_used_this_run': worker.get('run_credits_used'),
              'provider_credits_used_selected_pass': sum(pass_jobs.values()),
              'provider_selected_pass_jobs': len(pass_jobs),
              'credit_usage_note': 'Selected-pass usage sums each matching durable batch manifest once, including process restarts; this-run usage describes only the latest process.',
              'provider_credits_last_preflight': worker.get('remaining_credits_last_observed'), 'full_underlying_corpus_complete': False,
              'denominator_note': 'Progress describes the fixed 950-URL paid selection only, not all remaining county and law URLs. Direct source counts include allowed redirect targets.'}
    write(ROOT / 'reports/county_law_resume_progress.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'direct_official_county_entries'}, indent=2))
    print(json.dumps({'direct_official_county_downloads': county.get('downloaded_resources')}))


if __name__ == '__main__':
    main()
