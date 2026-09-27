"""Bounded network packets for the federal regulation slice.

Packet 1 `ecfr_versions`  : www.ecfr.gov /api/versioner/v1/versions/title-N.json?part=P   (<= 98 requests)
Packet 2 `fr_documents`   : www.federalregister.gov /api/v1/documents.json by CFR part      (<= 98 requests)

Rules implemented here (BRIEF network rules): exact frozen URL list written to seeds.jsonl BEFORE any request,
one worker per host, >= 2.5 s between request starts on a host (coordinated through corpus/_shared_hosts),
no retries, no redirects followed, stop at barriers (403/429/5xx/redirect/non-JSON shell), <= 600 s per packet,
original bytes + SHA-256 + per-request receipt. Nothing but the User-Agent is sent; no contact data, no keys.

Usage:
  python fetch.py prepare                 # writes seeds.jsonl for packet 1 and packet 2 stage a
  python fetch.py run ecfr_versions
  python fetch.py run fr_documents a
  python fetch.py prepare-b               # freezes stage-b page URLs from stage-a counts (adds to seeds.jsonl)
  python fetch.py run fr_documents b
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROBE = ROOT / 'reports/corpus_upgrade_20260919/understand/packets/regulation_probe'
UA = 'LegalCorpusResearch/1.0'
DELAY = 2.5
PACKET_SECONDS = 570
MAX_BODY = 25_000_000
PACKET_LIMIT = 98
ALLOWED_PREFIXES = {
    'ecfr_versions': 'https://www.ecfr.gov/api/versioner/v1/versions/title-',
    'fr_documents': 'https://www.federalregister.gov/api/v1/documents.json?',
}
ROBOTS_BODIES = {  # saved today by the scout; reused instead of a duplicate request
    'www.ecfr.gov': PROBE / 'bodies/d88f2efa4f01b8ed.bin',
    'www.federalregister.gov': PROBE / 'bodies/6c180c52aac5e205.bin',
}
SCOUT_VERSIONS_314 = {
    'url': 'https://www.ecfr.gov/api/versioner/v1/versions/title-21.json?part=314',
    'body': PROBE / 'bodies/08c2e0645ec6be26.bin',
    'sha256': '08c2e0645ec6be260e9a0b4dc096fe56d3436f92277fa9ab30a68d4942767d8e',
}
FR_FIELDS = ['document_number', 'citation', 'title', 'type', 'action', 'publication_date', 'effective_on',
             'signing_date', 'comments_close_on', 'dates', 'docket_ids', 'regulation_id_numbers', 'cfr_references',
             'agencies', 'html_url', 'pdf_url', 'full_text_xml_url', 'raw_text_url', 'json_url', 'volume',
             'start_page', 'end_page', 'page_length', 'correction_of', 'corrections']


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def strict_disallows(text):
    """Every Disallow under `User-agent: *`, blank lines NOT ending the group (conservative reading)."""
    rules, active = [], False
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        key, value = (part.strip() for part in line.split(':', 1))
        key = key.lower()
        if key == 'user-agent':
            active = value == '*' or value.lower() in UA.lower()
        elif key == 'disallow' and active and value:
            rules.append(value)
    return rules


def robots_allows(url):
    parts = urllib.parse.urlsplit(url)
    body = ROBOTS_BODIES[parts.hostname].read_text(encoding='utf-8')
    target = parts.path + ('?' + parts.query if parts.query else '')
    return not any(target.startswith(rule) for rule in strict_disallows(body))


def fr_url(title, part, page):
    query = [('per_page', '100'), ('order', 'newest'), ('page', str(page)),
             ('conditions[type][]', 'RULE'), ('conditions[type][]', 'PRORULE'),
             ('conditions[cfr][title]', str(title)), ('conditions[cfr][part]', str(part))]
    query += [('fields[]', name) for name in FR_FIELDS]
    return 'https://www.federalregister.gov/api/v1/documents.json?' + urllib.parse.urlencode(query)


def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def append_jsonl(path, row):
    with path.open('a', encoding='utf-8') as out:
        out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def prepare():
    seeds_path = HERE / 'seeds.jsonl'
    if seeds_path.exists():
        raise SystemExit('seeds.jsonl already frozen; refusing to rewrite')
    config = json.loads((HERE / 'parts.json').read_text(encoding='utf-8'))
    seeds = []
    for title, parts in config['selected_parts'].items():
        for part in parts:
            url = f'https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json?part={part}'
            seed = {'packet': 'ecfr_versions', 'stage': 'a', 'title': title, 'part': part, 'url': url}
            if url == SCOUT_VERSIONS_314['url']:
                seed['reuse_saved_body'] = {'path': SCOUT_VERSIONS_314['body'].relative_to(ROOT).as_posix(),
                                            'sha256': SCOUT_VERSIONS_314['sha256'],
                                            'reason': 'same URL already saved today by the regulations scout'}
            seeds.append(seed)
    for title in config['federal_register_titles']:
        for part in config['selected_parts'][title]:
            seeds.append({'packet': 'fr_documents', 'stage': 'a', 'title': title, 'part': part, 'page': 1,
                          'url': fr_url(title, part, 1)})
    check(seeds)
    with seeds_path.open('w', encoding='utf-8') as out:
        for seed in seeds:
            out.write(json.dumps(seed, sort_keys=True) + '\n')
    print('seeds frozen', len(seeds))


def check(seeds):
    for seed in seeds:
        if not seed['url'].startswith(ALLOWED_PREFIXES[seed['packet']]):
            raise SystemExit('URL outside the reviewed allowlist: ' + seed['url'])
        if '/full/' in seed['url'] or '/renderer/' in seed['url'] or not robots_allows(seed['url']):
            raise SystemExit('robots-disallowed URL: ' + seed['url'])
    for packet in ALLOWED_PREFIXES:
        live = [s for s in seeds if s['packet'] == packet and 'reuse_saved_body' not in s]
        if len(live) > PACKET_LIMIT:
            raise SystemExit(f'{packet}: {len(live)} requests exceed the packet limit')
    if len({s['url'] for s in seeds}) != len(seeds):
        raise SystemExit('duplicate seed URL')


def prepare_b():
    seeds_path = HERE / 'seeds.jsonl'
    seeds = load_jsonl(seeds_path)
    if any(s['packet'] == 'fr_documents' and s['stage'] == 'b' for s in seeds):
        raise SystemExit('stage b already frozen')
    receipts = {r['url']: r for r in load_jsonl(HERE / 'receipts.jsonl') if r.get('accepted')}
    pending = []
    for seed in seeds:
        if seed['packet'] != 'fr_documents':
            continue
        receipt = receipts.get(seed['url'])
        if not receipt:
            continue
        body = json.loads((HERE / receipt['body_path']).read_bytes())
        pages = int(body.get('total_pages') or 0)
        pending.append((seed['title'], seed['part'], pages))
    budget = PACKET_LIMIT - sum(1 for s in seeds if s['packet'] == 'fr_documents')
    order = {'21': 0, '16': 1, '49': 2}
    extra, page = [], 2
    while budget > 0 and any(pages >= page for _, _, pages in pending):
        for title, part, pages in sorted(pending, key=lambda row: (order[row[0]], int(row[1]))):
            if pages >= page and budget > 0:
                extra.append({'packet': 'fr_documents', 'stage': 'b', 'title': title, 'part': part, 'page': page,
                              'url': fr_url(title, part, page),
                              'rule': 'round-robin by page depth (all page 2 first), Title 21 then 16 then 49'})
                budget -= 1
        page += 1
    check(seeds + extra)
    with seeds_path.open('a', encoding='utf-8') as out:
        for seed in extra:
            out.write(json.dumps(seed, sort_keys=True) + '\n')
    print('stage b frozen', len(extra))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def accepted_body(packet, status, content_type, body):
    if status != 200 or 'json' not in (content_type or '').lower() or not body.strip():
        return False, 'status/content-type/empty'
    try:
        value = json.loads(body)
    except ValueError:
        return False, 'not JSON'
    key = 'content_versions' if packet == 'ecfr_versions' else 'count'
    if not isinstance(value, dict) or key not in value:
        return False, 'expected key missing: ' + key
    return True, None


def run(packet, stage):
    sys.path.insert(0, str(ROOT / 'pipeline'))
    from corpus_crawler import PacingDeferred, SharedHosts
    seeds = [s for s in load_jsonl(HERE / 'seeds.jsonl') if s['packet'] == packet and s['stage'] == stage]
    check(seeds)
    done = {r['url'] for r in load_jsonl(HERE / 'receipts.jsonl')}  # any earlier attempt counts: no retries
    raw = HERE / 'raw' / packet
    raw.mkdir(parents=True, exist_ok=True)
    shared = SharedHosts(ROOT / 'corpus/_shared_hosts', HERE)
    opener = urllib.request.build_opener(NoRedirect)
    started, stopped = time.time(), None
    for seed in seeds:
        url = seed['url']
        if url in done:
            continue
        if stopped or time.time() - started > PACKET_SECONDS:
            stopped = stopped or 'packet time budget reached'
            append_jsonl(HERE / 'gaps_log.jsonl', {'url': url, 'packet': packet, 'title': seed['title'], 'part': seed['part'],
                                                   'page': seed.get('page'), 'reason': 'not requested: ' + stopped, 'at': now()})
            continue
        if 'reuse_saved_body' in seed:
            source = ROOT / seed['reuse_saved_body']['path']
            body = source.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != seed['reuse_saved_body']['sha256']:
                raise SystemExit('saved scout body changed')
            (raw / (digest + '.json')).write_bytes(body)
            append_jsonl(HERE / 'receipts.jsonl', {
                'url': url, 'packet': packet, 'stage': stage, 'title': seed['title'], 'part': seed['part'],
                'method': 'none (reused saved body)', 'status': 200, 'accepted': True, 'bytes': len(body), 'sha256': digest,
                'body_path': f'raw/{packet}/{digest}.json', 'requested_at': '2026-09-19T04:33:23Z',
                'receipt_origin': 'reports/corpus_upgrade_20260919/understand/packets/regulation_probe/receipts.jsonl',
                'note': 'Original HTTP bytes saved by the regulations scout; no new request was made.'})
            continue
        host = urllib.parse.urlsplit(url).hostname
        while True:
            try:
                shared.reserve(host, DELAY)
                break
            except PacingDeferred as wait:
                time.sleep(max(0.1, min(30, wait.until - time.time())))
        requested_at, tick = now(), time.time()
        receipt = {'url': url, 'packet': packet, 'stage': stage, 'title': seed['title'], 'part': seed['part'],
                   'page': seed.get('page'), 'method': 'GET', 'requested_at': requested_at, 'user_agent': UA}
        try:
            request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            try:
                with opener.open(request, timeout=60) as response:
                    body = response.read(MAX_BODY + 1)
                    status, headers, final = response.status, dict(response.headers.items()), response.geturl()
            except urllib.error.HTTPError as error:
                body, status, headers, final = error.read(MAX_BODY + 1), error.code, dict(error.headers.items()), url
            digest = hashlib.sha256(body).hexdigest()
            ok, why = accepted_body(packet, status, headers.get('Content-Type') or headers.get('content-type'), body)
            if len(body) > MAX_BODY:
                ok, why = False, 'body larger than cap'
            (raw / (digest + '.json')).write_bytes(body)
            receipt.update({'status': status, 'final_url': final, 'headers': headers, 'bytes': len(body), 'sha256': digest,
                            'body_path': f'raw/{packet}/{digest}.json', 'elapsed_s': round(time.time() - tick, 2),
                            'accepted': ok, 'rejected_reason': why})
            if not ok and (status in (401, 403, 429) or status >= 500 or 300 <= status < 400 or status == 200):
                stopped = f'barrier at {url}: HTTP {status} ({why})'
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            receipt.update({'status': None, 'accepted': False, 'rejected_reason': 'transport error: ' + type(error).__name__,
                            'elapsed_s': round(time.time() - tick, 2)})
            stopped = f'transport error at {url}'
        finally:
            shared.release(host)
        append_jsonl(HERE / 'receipts.jsonl', receipt)
        if not receipt.get('accepted'):
            append_jsonl(HERE / 'gaps_log.jsonl', {'url': url, 'packet': packet, 'title': seed['title'], 'part': seed['part'],
                                                   'page': seed.get('page'), 'at': now(),
                                                   'reason': f"HTTP {receipt.get('status')}: {receipt.get('rejected_reason')}"})
    print(packet, stage, 'elapsed', round(time.time() - started, 1), 'stopped:', stopped)


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'prepare':
        prepare()
    elif command == 'prepare-b':
        prepare_b()
    elif command == 'run' and len(sys.argv) >= 3 and sys.argv[2] in ALLOWED_PREFIXES:
        run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'a')
    else:
        raise SystemExit(__doc__)
