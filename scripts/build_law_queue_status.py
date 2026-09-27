"""Report runnable law work from saved queues and access evidence, without network I/O.

This does not change crawler rows, host controls, or source captures. A queue with
only recorded barriers is not proof that its jurisdiction's laws are complete.
"""
from __future__ import annotations

import collections
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import urllib.parse
import urllib.robotparser

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = (
    'official_law_pages', 'official_law_recovery_pass_1',
    'official_law_nebraska_chapters',
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def cached_denial(folder: Path, url: str, user_agent: str) -> dict | None:
    parts = urllib.parse.urlsplit(url)
    origin = f'{parts.scheme}://{parts.netloc}'
    path = folder / 'controls/robots' / (hashlib.sha256(origin.encode()).hexdigest() + '.json')
    if not path.exists():
        return None
    control = read_json(path)
    if control.get('origin') != origin or control.get('in_progress'):
        return None
    evidence = {
        'control_path': str(path.relative_to(ROOT)),
        'control_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'observed_at': control.get('checked_at'),
    }
    observations = control.get('observations', [])
    if not observations:
        return None
    response = observations[-1]
    raw_name = response.get('raw_path')
    if not raw_name or not response.get('raw_complete'):
        return None
    raw_path = (folder / raw_name).resolve()
    raw_path.relative_to((folder / 'raw').resolve())
    raw = raw_path.read_bytes()
    raw_hash = hashlib.sha256(raw).hexdigest()
    if raw_hash != response.get('sha256'):
        raise ValueError(f'Robots evidence hash mismatch: {raw_path}')
    robot_origin = urllib.parse.urlsplit(response['requested_url'])
    if robot_origin.hostname != parts.hostname or robot_origin.path != '/robots.txt':
        return None
    evidence.update(raw_path=str(raw_path.relative_to(ROOT)), raw_sha256=raw_hash)
    if response.get('http_status') in (401, 403) and control.get('problem') == 'robots_disallowed':
        return {**evidence, 'reason': 'saved_robots_access_denial'}
    if control.get('problem') or response.get('http_status') != 200:
        return None
    source = raw.decode('utf-8-sig')
    if '<html' in source[:1000].lower() or '<!doctype html' in source[:1000].lower():
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(response['requested_url'])
    parser.parse(source.splitlines())
    if not parser.can_fetch(user_agent, url):
        return {**evidence, 'reason': 'saved_robots_rule_denial'}
    return None


def build_snapshot() -> dict:
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    shared_path = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    with sqlite3.connect(shared_path.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        shared = {r['host']: dict(r) for r in db.execute('SELECT * FROM host_state')}
    collections_out, totals = {}, collections.Counter()
    for name in COLLECTIONS:
        folder = ROOT / 'corpus' / name
        config = read_json(folder / 'config.json')
        with sqlite3.connect((folder / 'corpus.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            local = {r['host']: dict(r) for r in db.execute('SELECT * FROM hosts')}
            rows = [dict(r) for r in db.execute("SELECT id,url,host,status,next_attempt_at FROM resources WHERE status IN ('pending','retry_wait','fetching') ORDER BY id")]
        hosts_out, classifications = {}, collections.Counter()
        for row in rows:
            host = row['host']
            shared_state, local_state = shared.get(host, {}), local.get(host, {})
            pause = shared_state.get('pause_reason') or local_state.get('pause_reason')
            denial = cached_denial(folder, row['url'], config['user_agent'])
            classification = ('in_flight' if row['status'] == 'fetching' else
                              'saved_host_pause' if pause else
                              'saved_robots_denial' if denial else 'pending_network_work')
            classifications[classification] += 1
            item = hosts_out.setdefault(host, {
                'counts': collections.Counter(), 'saved_pause_reason': pause,
                'shared_delay_seconds': shared_state.get('delay_seconds'),
                'shared_next_start_at': shared_state.get('next_start_at'),
                'shared_cooldown_until': shared_state.get('cooldown_until'),
                'robots_denial_evidence': denial,
                'resource_ids_by_classification': {},
            })
            item['counts'][classification] += 1
            item['resource_ids_by_classification'].setdefault(classification, []).append(row['id'])
        totals.update(classifications)
        collections_out[name] = {'pending_rows': len(rows), 'classifications': dict(classifications), 'hosts': hosts_out}
    worker = read_json(ROOT / 'sources/trellis/worker/status.json')
    trellis_laws_pending = worker.get('pending_by_category', {}).get('rules', 0)
    credit_paused = worker.get('status') == 'paused_credit_or_auth'
    active_batch = (ROOT / 'sources/trellis/worker/batch_active.json').exists()
    browser_access_path = ROOT / 'corpus/trellis_browser_laws/browser_access.json'
    browser_access = read_json(browser_access_path) if browser_access_path.exists() else {}
    browser_remaining = browser_access.get('remaining_content_views_at_observation', 0)
    browser_batch_review_needed = bool(trellis_laws_pending and browser_remaining > 1)
    public_remaining = totals['pending_network_work'] + totals['in_flight']
    accessible_exhausted = public_remaining == 0 and not active_batch and not browser_batch_review_needed and (not trellis_laws_pending or credit_paused)
    return {
        'checked_at': checked_at, 'network_requests': 0, 'source_rows_modified': False,
        'host_controls_modified': False, 'collections': collections_out,
        'direct_law_queue_counts': dict(totals),
        'direct_law_network_work_remaining': public_remaining,
        'trellis_laws_pending': trellis_laws_pending,
        'trellis_credit_paused_last_reported': credit_paused,
        'trellis_worker_observed_at': worker.get('updated_at'),
        'trellis_remote_batch_marker_present': active_batch,
        'browser_content_views_remaining_last_observed': browser_remaining,
        'browser_allowance_observed_at': browser_access.get('observed_at'),
        'bounded_browser_law_batch_review_needed': browser_batch_review_needed,
        'prepared_law_queues_accessible_work_exhausted': accessible_exhausted,
        'full_law_corpus_complete': False,
        'limitations': [
            'Saved access policies are reported at their observation dates; no fresh network entitlement or availability is asserted.',
            'Pending network work still follows all resource deadlines, shared pacing, robots checks and access controls in the actual crawler.',
            'No pending URL is marked downloaded or terminal, and no failed attempt or host pause is cleared.',
            'A working browser law path has a separate account-view allowance. Recheck it live and select a finite substantive batch before treating all Trellis acquisition as blocked.',
            'Exhausting accessible work in these prepared queues permits review for the next phase with explicit gaps; it does not establish whole-state legal completeness.',
        ],
    }


if __name__ == '__main__':
    snapshot = build_snapshot()
    output = ROOT / 'reports/law_queue_status.json'
    output.write_text(json.dumps(snapshot, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: snapshot[k] for k in (
        'checked_at', 'direct_law_queue_counts', 'direct_law_network_work_remaining',
        'trellis_laws_pending', 'prepared_law_queues_accessible_work_exhausted',
    )}))
