"""Bounded registry-entry transfer preparation. Reads old collections only."""
from collections import Counter, defaultdict, deque
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
REGISTRY = 'sources/official_courts/datasets/cisa_county_government_domains.json'
OLD_SEEDS = 'reports/geography/cisa_county_entries/seeds.jsonl'
OLD_COLLECTION = 'corpus/county_sites/corpus.sqlite3'
FAMILY = 'official_cisa_county_gov_domain_registry'
BLOCKS = {'forbidden', 'unauthorized', 'login_required', 'access_required', 'challenge', 'host_paused'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def alias(value):
    try:
        p = urlsplit(value or '')
        return (p.hostname or '').lower().removeprefix('www.') if p.scheme in ('http', 'https') else ''
    except ValueError:
        return ''


def records(path):
    raw = path.read_bytes()
    if path.suffix == '.jsonl':
        result = [json.loads(x) for x in raw.decode('utf-8-sig').splitlines() if x.strip()]
    else:
        value = json.loads(raw)
        result = value if isinstance(value, list) else next((value[k] for k in ('documents', 'records', 'entries', 'sources') if isinstance(value.get(k), list)), [])
    return result, {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(raw), 'bytes': len(raw), 'rows': len(result)}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def lines(name, values):
    (OUT / name).write_bytes(b''.join(packed(v) + b'\n' for v in values))


def main():
    if (OUT / 'corpus.sqlite3').exists():
        raise RuntimeError('Preparation refuses to overwrite an ingested collection.')
    started = now()
    activity = json.loads(subprocess.check_output([
        'powershell', '-NoProfile', '-Command',
        "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'corpus_crawler.py' } | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Depth 3 -Compress"
    ], text=True, encoding='utf-8') or '[]')
    if isinstance(activity, dict):
        activity = [activity]
    if any('county_sites' in (p.get('CommandLine') or '') for p in activity):
        raise RuntimeError('Old county_sites collector is still running; transfer prohibited.')
    save('source_process_snapshot.json', {'checked_at': started, 'old_collector_running': False, 'crawler_processes': activity})
    registry, registry_input = records(ROOT / REGISTRY)
    prior_seeds, prior_seed_input = records(ROOT / OLD_SEEDS)
    seed_by_url = {r['url']: r for r in prior_seeds}
    domains = {r['domain'] for r in registry}
    inputs = [registry_input, prior_seed_input]
    existing = defaultdict(list)
    blocked = defaultdict(list)
    old_contexts = defaultdict(list)
    snapshots = []
    for path in sorted((ROOT / 'corpus').rglob('corpus.sqlite3')):
        relative = path.relative_to(ROOT).as_posix()
        c = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        c.row_factory = sqlite3.Row
        c.execute('BEGIN')
        try:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'resources' not in tables:
                continue
            rows = [dict(r) for r in c.execute('SELECT id,url,host,status,attempts,retry_count,raw_path,text_path,metadata_path,sha256,raw_complete,redirect_url,error,last_http_status FROM resources ORDER BY id')]
            hosts = [dict(r) for r in c.execute('SELECT * FROM hosts')] if 'hosts' in tables else []
            for row in rows:
                for value in {row['url'], row['redirect_url']} - {None}:
                    key = alias(value)
                    if key in domains:
                        ref = {'source_path': relative, 'table': 'resources', **row, 'alias_evidence_url': value}
                        if ref not in existing[key]:
                            existing[key].append(ref)
                if row['status'] in BLOCKS or row['status'].startswith('robots_') or row['last_http_status'] in (401, 403, 429):
                    key = alias(row['url'])
                    blocked[key].append({'source_path': relative, 'table': 'resources', 'id': row['id'], 'url': row['url'], 'status': row['status'], 'error': row['error'], 'http_status': row['last_http_status']})
            for row in hosts:
                if row['pause_reason'] or row['cooldown_until'] > time.time():
                    blocked[row['host'].lower().removeprefix('www.')].append({'source_path': relative, 'table': 'hosts', **row})
            if relative == OLD_COLLECTION:
                for r in c.execute('SELECT rc.resource_id,c.id,c.source_family,c.seed_url,c.seed_json FROM resource_contexts rc JOIN contexts c ON c.id=rc.context_id'):
                    old_contexts[r['resource_id']].append(dict(r))
            snapshots.append({'path': relative, 'read_mode': 'SQLite mode=ro transaction', 'resources': len(rows), 'resource_rows_sha256': sha(packed(rows)), 'host_rows_sha256': sha(packed(hosts))})
        finally:
            c.rollback()
            c.close()
    shared = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    c = sqlite3.connect(shared.resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    shared_rows = [dict(r) for r in c.execute('SELECT * FROM host_state ORDER BY host')]
    c.close()
    for r in shared_rows:
        if r['pause_reason'] or r['cooldown_until'] > time.time():
            blocked[r['host'].lower().removeprefix('www.')].append({'source_path': shared.relative_to(ROOT).as_posix(), 'table': 'host_state', **r})
    snapshots.append({'path': shared.relative_to(ROOT).as_posix(), 'read_mode': 'SQLite mode=ro', 'host_rows': len(shared_rows), 'host_rows_sha256': sha(packed(shared_rows))})
    # Historical captures/attempts and actual legacy queues, not unqueued discovery graphs.
    legacy_paths = [
        'sources/official_courts/manifests/fetches.jsonl',
        'sources/official_courts/manifests/remaining_frontier.jsonl',
        'sources/official_courts/manifests/downloaded_documents.jsonl',
        'sources/official_courts/continuation/attempted_url_manifest.jsonl',
        'sources/official_courts/continuation/captured_url_manifest.jsonl',
        'sources/official_laws/indexes/document_manifest.json',
    ]
    for name in legacy_paths:
        path = ROOT / name
        if not path.exists():
            continue
        rows, info = records(path)
        inputs.append(info)
        for number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            for key in ('url', 'source_url', 'official_url', 'requested_url', 'final_url'):
                value = row.get(key)
                if isinstance(value, str) and alias(value) in domains:
                    existing[alias(value)].append({'source_path': name, 'record_number': number, 'url': value, 'status': row.get('status') or row.get('verification_status'), 'kind': 'historical_attempt_capture_or_queue'})
    # Every host in the other agent's initial selection also excludes its follow-up apex/www targets.
    continuation = ROOT / 'corpus/county_entries_continuation_20260913'
    other_seed_paths = [continuation / 'seeds.jsonl', continuation / 'canonical_pass2/seeds.jsonl']
    for path in other_seed_paths:
        if not path.exists():
            continue
        rows, info = records(path)
        inputs.append(info)
        for number, row in enumerate(rows, 1):
            key = alias(row.get('url'))
            if key in domains:
                existing[key].append({'source_path': info['path'], 'record_number': number, 'url': row['url'], 'kind': 'other_active_county_seed_and_all_apex_www_followups'})
    paused_path = ROOT / 'sources/official_laws/indexes/paused_hosts.json'
    raw = paused_path.read_bytes()
    inputs.append({'path': paused_path.relative_to(ROOT).as_posix(), 'sha256': sha(raw), 'bytes': len(raw)})
    for host, info in json.loads(raw).items():
        blocked[host.lower().removeprefix('www.')].append({'source_path': paused_path.relative_to(ROOT).as_posix(), 'host': host, **info})
    eligible, decisions = [], []
    for number, record in enumerate(registry, 1):
        domain, url = record['domain'], record['official_url']
        refs = existing[domain]
        p = urlsplit(url)
        seed = seed_by_url.get(url)
        clean_pending = lambda r: r.get('source_path') == OLD_COLLECTION and r.get('status') == 'pending' and r.get('attempts') == 0 and not any(r.get(k) for k in ('raw_path', 'text_path', 'metadata_path', 'sha256', 'raw_complete', 'redirect_url'))
        decision = {'domain': domain, 'url': url, 'registry_record_number': number, 'registry_state_code': record.get('usps'), 'prior_rows': refs, 'saved_host_controls': blocked.get(domain, [])}
        if p.scheme != 'https' or p.hostname != domain or p.path != '/' or p.query or not seed:
            reason = 'not_an_exact_previously_saved_registry_entry'
        elif blocked.get(domain):
            reason = 'saved_host_pause_or_robots_access_outcome'
        elif refs and not all(clean_pending(r) for r in refs):
            reason = 'prior_attempt_capture_other_queue_or_alias'
        elif refs and not any(r['url'] == url for r in refs):
            reason = 'no_exact_unattempted_registry_row_to_transfer'
        elif refs and any(not old_contexts[r['id']] or any(x['source_family'] != FAMILY for x in old_contexts[r['id']]) for r in refs):
            reason = 'old_row_not_exclusively_registry_provenance'
        else:
            reason = 'eligible_unattempted_pending_transfer' if refs else 'eligible_never_enqueued_entry'
            item = json.loads(json.dumps(seed))
            item['registry_record'] = record
            item['registry_record_sha256'] = sha(packed(record))
            item['selection_reason'] = reason
            item['transfer_lineage'] = [{'source_collection': OLD_COLLECTION, 'resource_id': r['id'], 'url': r['url'], 'prior_status': r['status'], 'prior_attempts': r['attempts'], 'prior_raw_text_metadata_hash_empty': True, 'source_row_unchanged': True, 'source_row_sha256': sha(packed({k:v for k,v in r.items() if k not in ('source_path','table','alias_evidence_url')})), 'context_ids': [x['id'] for x in old_contexts[r['id']]]} for r in refs]
            item['collection_scope'] = 'Only the saved registered root entry and allowed server redirects; no link traversal.'
            eligible.append(item)
        decision['decision'] = reason
        decisions.append(decision)
    groups = defaultdict(deque)
    for seed in sorted(eligible, key=lambda s: (bool(s['registry_record'].get('suborganization')), s['url'])):
        groups[seed['registry_record'].get('usps') or 'UNKNOWN'].append(seed)
    ordered = []
    while any(groups.values()):
        for state in sorted(groups):
            if groups[state]:
                ordered.append(groups[state].popleft())
    selected, deferred = ordered[:500], ordered[500:]
    for rank, seed in enumerate(selected, 1):
        seed['selection_rank'] = rank
    hosts = sorted({host for seed in selected for host in seed['allowed_registered_www_hosts']})
    cfg = {'allow': [{'host': host, 'path_prefixes': ['/']} for host in hosts], 'workers': 6, 'per_host_delay': 2.0, 'timeout_seconds': 30, 'max_transfer_seconds': 120, 'max_response_bytes': 16777216, 'max_extract_chars': 4194304, 'min_free_bytes': 10737418240, 'max_depth': 0, 'max_retries': 0, 'backoff_base_seconds': 30, 'max_backoff_seconds': 900, 'respect_robots': True, 'follow_links': False, 'follow_external_allowed_links': False, 'pause_host_on_access_block': True, 'user_agent': 'LegalCorpusResearch/1.0', 'shared_host_dir': 'corpus/_shared_hosts'}
    if shutil.disk_usage(OUT).free < cfg['min_free_bytes'] + 1024 * 1024 * 1024:
        raise RuntimeError('Insufficient space for guarded batch.')
    inputs_dir = OUT / 'inputs'
    inputs_dir.mkdir(exist_ok=True)
    for name in (REGISTRY, OLD_SEEDS, 'sources/official_courts/raw/a65253f8228936495546.txt'):
        src = ROOT / name
        raw = src.read_bytes()
        target = inputs_dir / ('registry.json' if name == REGISTRY else 'previous_registry_seeds.jsonl' if name == OLD_SEEDS else 'cisa_current_full.csv')
        target.write_bytes(raw)
        inputs.append({'path': name, 'sha256': sha(raw), 'bytes': len(raw), 'snapshot_path': target.relative_to(ROOT).as_posix()})
    lines('seeds.jsonl', selected)
    lines('deferred_eligible_entries.jsonl', deferred)
    lines('decisions.jsonl', decisions)
    save('config.json', cfg)
    save('input_evidence.json', {'generated_at': now(), 'files': inputs, 'database_snapshots': snapshots})
    summary = {'prepared_at': now(), 'status': 'prepared_not_started', 'collection': OUT.relative_to(ROOT).as_posix(), 'registry_records': len(registry), 'registry_sha256': registry_input['sha256'], 'decisions': dict(Counter(r['decision'] for r in decisions)), 'selected_entries': len(selected), 'deferred_eligible_entries': len(deferred), 'selected_state_codes': sorted(groups), 'selection_order': 'Round-robin registry state codes, then saved entry URL; suborganization-empty entries first within state.', 'old_collector_process_absent': True, 'old_queue_rows_modified': 0, 'entry_path': '/', 'max_depth': 0, 'follow_links': False, 'automatic_retries': 0, 'authority_classification': 'official_government_domain_registration', 'county_name_association': 'UNREVIEWED', 'court_fips_association_verified': False, 'redirect_limitation': 'The existing crawler enforces a global allowlist of the selected registered/www pairs. Any cross-pair redirect is recorded for review, and never promotes county/FIPS authority.', 'full_corpus_complete': False}
    save('preparation.json', summary)
    assert len(selected) <= 500 and len({s['url'] for s in selected}) == len(selected)
    assert all(s['url'] in seed_by_url and not blocked.get(s['registered_domain']) for s in selected)
    save('preparation_hashes.json', {'generated_at': now(), 'files': [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'preparation_hashes.json']})
    print(json.dumps({k: summary[k] for k in ('status','registry_records','decisions','selected_entries','deferred_eligible_entries','old_collector_process_absent')}))


if __name__ == '__main__':
    main()
