"""Finite judge-only batches, isolated from the historical Trellis frontier."""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, pathlib, sys, time, traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import firecrawl_worker as legacy
from firecrawl_batch_worker import BatchWorker, Client, read_selection, worker_lock
from run_collector import private_key

BASE = ROOT / 'sources/trellis/judge_focus_20260913'
STATE = BASE / 'worker'
CREDENTIAL = ROOT / '.auth/firecrawl_judge.dpapi'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def save(path, data):
    legacy.save(path, data)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection-file', type=pathlib.Path, required=True)
    parser.add_argument('--seconds', type=int, default=900)
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--reserve', type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600 or not 1 <= args.batch_size <= 100 or args.reserve < 1:
        parser.error('Finite time, batch and credit bounds are required')
    selection = read_selection(args.selection_file)
    if not selection['urls'] or any(legacy.category(url)[0] not in {'judge_profile', 'judge_directory'} for url in selection['urls']):
        parser.error('Only an exact nonempty judge profile/directory selection is permitted')
    original_allowed = legacy.allowed
    legacy.allowed = lambda url: original_allowed(url) and legacy.category(url)[0] in {'judge_profile', 'judge_directory'}
    legacy.SCOPE_VERSION = '20260913-judge-focus-only'
    BASE.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    active = {'pid': os.getpid(), 'started_at': now(), 'status': 'running', 'selection_file': str(args.selection_file.resolve()),
              'selection_order_sha256': selection['sha256'], 'selected_urls': len(selection['urls']),
              'seconds_budget': args.seconds, 'batch_size': args.batch_size, 'credit_reserve': args.reserve,
              'credential': 'Windows DPAPI; key remains in memory', 'original_frontier_modified': False,
              'target_scope': 'judge profiles/directories only', 'provider_max_concurrency': 2}
    # Also hold the historical global provider lock: no simultaneous legacy worker.
    with worker_lock(legacy.STATE), worker_lock(STATE):
        with (STATE / 'background.log').open('a', encoding='utf-8', buffering=1) as out, (STATE / 'background.errors.log').open('a', encoding='utf-8', buffering=1) as err:
            sys.stdout, sys.stderr = out, err
            save(STATE / 'active_run.json', active)
            print(json.dumps({'event': 'started', **active}), flush=True)
            key = private_key(CREDENTIAL)
            try:
                latest = None
                while time.monotonic() - start < args.seconds:
                    worker = BatchWorker(Client(key), base=BASE, state=STATE, batch_size=args.batch_size, reserve=args.reserve,
                                         cache_max_age_ms=86400000, page_timeout_ms=120000, selection_file=args.selection_file)
                    # Selector validation does not insert URLs. Seed only its exact reviewed URLs.
                    before = worker.q.db.execute('SELECT count(*) FROM frontier').fetchone()[0]
                    for url in selection['urls']:
                        worker.q.add(url)
                    worker.q.db.commit()
                    if not (STATE / 'seed_ingest_receipt.json').exists():
                        save(STATE / 'seed_ingest_receipt.json', {'ingested_at': now(), 'selected_urls': len(selection['urls']),
                            'selection_order_sha256': selection['sha256'], 'seed_file_sha256': hashlib.sha256(args.selection_file.read_bytes()).hexdigest(),
                            'resources_before': before, 'resources_after': worker.q.db.execute('SELECT count(*) FROM frontier').fetchone()[0],
                            'original_frontier_modified': False, 'no_case_or_document_seeds': True})
                    latest = worker.run(once=True, poll_seconds=3)
                    print(json.dumps({'event': 'batch_checkpoint', 'at': now(), 'elapsed_seconds': round(time.monotonic()-start, 1),
                                      'status': latest['status'], 'selection_counts': latest.get('selected_status_counts'),
                                      'selected_eligible_pending': latest.get('selected_eligible_pending'),
                                      'remaining_credits': latest.get('remaining_credits_last_observed')}), flush=True)
                    if latest['status'] != 'batch_complete' or not latest.get('selected_eligible_pending', 0):
                        break
                active.update(status='checkpointed', ended_at=now(), elapsed_seconds=round(time.monotonic()-start, 1),
                              stop_reason=latest['status'] if latest and latest['status'] != 'batch_complete' else 'selection_exhausted' if latest and not latest.get('selected_eligible_pending', 0) else 'finite_time_budget',
                              latest_status=latest)
            except BaseException as exc:
                active.update(status='failed', ended_at=now(), error_type=type(exc).__name__)
                print(traceback.format_exc().replace(key, '[REDACTED]'), file=err, flush=True)
                raise
            finally:
                save(STATE / 'active_run.json', active)
                print(json.dumps({'event': active['status'], 'at': now(), 'stop_reason': active.get('stop_reason')}), flush=True)

if __name__ == '__main__':
    main()
