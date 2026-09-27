"""Freeze packet 1: the audit's reviewed official URLs. Offline; no guessed URLs.

Input: reports/corpus_upgrade_20260919/understand/packets/settlement_urls.jsonl (91 lines).
Output: packet1/seeds.jsonl, packet1/config.json, packet1/plan.json.
"""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = HERE / 'packet1'
PACKET = ROOT / 'reports/corpus_upgrade_20260919/understand/packets/settlement_urls.jsonl'
EXPECTED_SHA = 'e3d265792ae9b0f2d7ef6c1376eabba546fcb6652f3083fb36c160d1e49cea66'
# Claim submission, login and form-post pages are never captured.
BAD_PATH = re.compile(r'log-?in|sign-?in|submit|file-?a?-?claim|claim-?form|/claim\b|dashboard|register|portal', re.I)


def known_urls():
    known = set()
    with sqlite3.connect((ROOT / 'catalog/documents.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        known.update(x[0] for x in db.execute('SELECT source_url FROM versions UNION SELECT final_url FROM versions') if x[0])
    with sqlite3.connect((ROOT / 'delivery/archive-directory/directory.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        known.update(x[0] for x in db.execute('SELECT source_url FROM records') if x[0])
    return known


def shared_blocks():
    path = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    if not path.exists():
        return {}
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        return dict(db.execute("SELECT host,pause_reason FROM host_state WHERE COALESCE(pause_reason,'')!=''"))


def robots_ok(robots):
    """Audit-confirmed, or explicitly deferred to the collector's own robots check at fetch time."""
    if robots.get('allowed_for_user_agent') is True:
        return True
    return (robots.get('allowed_for_user_agent') is None and robots.get('verdict') == 'parsed'
            and str(robots.get('path_decision', '')).startswith('deferred'))


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')


def prepare():
    OUT.mkdir(exist_ok=True)
    payload = PACKET.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SHA:
        raise SystemExit('Audit packet changed: ' + digest)
    rows = [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]
    known, blocked = known_urls(), shared_blocks()
    seeds, skipped, seen = [], [], set()
    for number, row in enumerate(rows, 1):
        url = row['url']
        parts = urlsplit(url)
        host = parts.netloc.lower()
        reason = None
        if url in seen:
            reason = 'duplicate_in_packet'
        elif url in known:
            reason = 'already_saved_exact_url'
        elif host in blocked:
            reason = 'shared_host_block'
        elif BAD_PATH.search(parts.path) or parts.query:
            reason = 'claim_login_or_form_like_url'
        elif not robots_ok(row.get('robots') or {}):
            reason = 'robots_not_confirmed_by_audit'
        if reason:
            skipped.append({'url': url, 'reason': reason, 'access_reason': blocked.get(host)})
            continue
        seen.add(url)
        seeds.append({
            'url': url, 'title': row.get('title'), 'source_family': row['source_family'],
            'category': row.get('expected_document_type') or row.get('expected_page_type') or 'unspecified',
            'jurisdiction': {'country': 'US'},
            'scope': {'host': host, 'path_prefixes': [parts.path or '/']},
            'packet': 1, 'audit_packet_line': number, 'audit_packet_sha256': digest,
            'audit_settlement_family_label': row.get('settlement_family'),
            'audit_evidence': row.get('evidence'), 'audit_case_reference': row.get('case_reference'),
            'audit_robots': row.get('robots'), 'audit_risk': row.get('risk'), 'audit_note': row.get('note'),
            'filename_date_hint': row.get('filename_date_hint'), 'filename_date_semantics': row.get('filename_date_semantics'),
        })
    assert len(seeds) <= 91 and len({s['url'] for s in seeds}) == len(seeds)
    (OUT / 'seeds.jsonl').write_text(''.join(json.dumps(s, ensure_ascii=False) + '\n' for s in seeds), encoding='utf-8', newline='\n')
    hosts = {}
    for seed in seeds:
        hosts.setdefault(seed['scope']['host'], []).extend(seed['scope']['path_prefixes'])
    write(OUT / 'config.json', {
        'allow': [{'host': host, 'path_prefixes': sorted(set(paths))} for host, paths in sorted(hosts.items())],
        'workers': 8, 'per_host_delay': 2.0, 'respect_robots': True, 'shared_host_dir': 'corpus/_shared_hosts',
        'follow_links': False, 'follow_external_allowed_links': False, 'max_depth': 0, 'max_retries': 0,
        'timeout_seconds': 30, 'max_transfer_seconds': 120, 'pause_host_on_access_block': True,
        'user_agent': 'LegalCorpusResearch/1.0'})
    write(OUT / 'plan.json', {
        'packet': 1, 'input': PACKET.relative_to(ROOT).as_posix(), 'input_sha256': digest, 'input_rows': len(rows),
        'selected': len(seeds), 'hosts': len(hosts), 'skipped': skipped,
        'scope': 'Exact URLs reviewed by the settlements audit. GET only, no link following, no forms, no claim pages.',
        'limits': {'workers': 8, 'max_resource_attempts': 100, 'max_seconds': 600, 'minimum_host_delay_seconds': 2,
                   'robots': 'evaluated again at fetch time by the collector; crawl-delay honored', 'automatic_retries': 0},
        'deduplication': 'Exact URL against catalog/documents.sqlite3 versions (source_url, final_url) and directory.sqlite3 records.source_url.',
        'complete_site_claim': False})
    print(json.dumps({'seeds': len(seeds), 'hosts': len(hosts), 'skipped': skipped}))


if __name__ == '__main__':
    prepare()
