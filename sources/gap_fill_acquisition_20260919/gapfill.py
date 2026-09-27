"""Bounded gap-fill capture packet (state law / court gaps), September 19, 2026.

Subcommands:  prepare (offline: freeze seeds.jsonl)  ->  run (network, one packet)
              ->  build (offline: resources.jsonl, gaps.json, validation.json, registry).

Reuses the reviewed county Firecrawl collector by import only (private DPAPI key
handling, credit client, global worker lock, quality gate, shared host gate and
robots policy). Nothing here prints, stores or copies the credential. Pages are
provider captures (NOT original HTTP bytes); PDFs are fetched directly from the
official host as original bytes. No retries, no proxy escalation, no barrier bypass.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, sqlite3, ssl, sys, threading, time, uuid
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANDIDATES = ROOT / 'reports/corpus_upgrade_20260919/understand/packets/gap_fill_candidates.jsonl'
COLLECTOR = ROOT / 'sources/county_litigation_firecrawl_20260919/collect.py'
PACKET = 'gap_fill_20260919'
TASK_CREDIT_CAP = 100
BALANCE_FLOOR = 500
ADMIT_SECONDS = 540
# reports/corpus_upgrade_20260919/understand/state_county_gaps.md, section 7 (lines 309-312)
BARRIER_HOSTS = ('courts.mo.gov', 'mass.gov', 'jud.ct.gov', 'njcourts.gov', 'nycourts.gov', 'courts.nh.gov',
                 'judicial.alabama.gov', 'alabamaadministrativecode.state.al.us', 'govt.westlaw.com')
TERMS_HOSTS = ('lexisnexis.com', 'westlaw.com')
EXTRA_FINAL_HOSTS = {'www.ncga.state.nc.us': ['www.ncleg.gov', 'ncleg.gov', 'www.ncleg.net']}  # reviewed note in the packet
SOFT404 = re.compile(r"(?:page (?:was |could )?not (?:be )?found|page you (?:are looking for|requested|were looking for)|"
                     r"no longer (?:available|exists)|(?:can ?not|could not|couldn't) be found|error 404|404 error|http 404|"
                     r"access (?:is |was )?denied|request (?:was )?blocked|service (?:is )?(?:temporarily )?unavailable|"
                     r"temporarily unavailable|site (?:is )?under maintenance)", re.I)
CHALLENGE = re.compile(r"(?:verify (?:that )?you are (?:a )?human|are you a robot|captcha|unusual traffic|pardon our interruption|"
                       r"access to this page has been denied|request unsuccessful\. incapsula|checking your browser|"
                       r"enable javascript and cookies to continue|attention required|just a moment)", re.I)
SCRIPT_SHELL = re.compile(r'(?:your browser is not supported|please (?:update|upgrade) your browser|enable javascript to (?:view|run|use)|requires javascript)', re.I)
LOGIN = re.compile(r'^\s*(?:sign[ -]?in|log[ -]?in|login|authentication required)\b', re.I)
BADTITLE = re.compile(r'^\s*(?:404\b|error\b|not found|forbidden|access denied|just a moment)', re.I)


def now(): return datetime.now(timezone.utc).isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def rel(path): return str(Path(path).resolve().relative_to(ROOT)).replace('\\', '/')
def read_rows(path): return [json.loads(x) for x in Path(path).read_text(encoding='utf-8-sig').splitlines() if x.strip()]


def write_bytes(path, raw):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp'); temp.write_bytes(raw); temp.replace(path)


def write_json(path, value): write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
def write_rows(path, values): write_bytes(path, ''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values).encode())


def host_in(host, family):
    host = (host or '').lower()
    return any(host == item or host.endswith('.' + item) for item in family)


def loose(url):
    p = urllib.parse.urlsplit(url.strip())
    host = (p.hostname or '').lower()
    if host.startswith('www.'): host = host[4:]
    return host + (p.path.rstrip('/') or '') + ('?' + p.query if p.query else '')


def family_of(gap_type):
    if gap_type == 'complex_litigation_program': return 'mass_tort_coordination'
    if gap_type == 'civil_jury_instructions': return 'jury_instructions'
    if gap_type.startswith('regulations'): return 'regulations'
    if gap_type.startswith(('statutes', 'constitution')): return 'statutes'
    if gap_type.startswith('court_rules') or gap_type == 'civil_procedure_evidence_rules' or gap_type == 'venue_philadelphia_local_rules': return 'court_rules'
    if gap_type == 'statewide_forms_absent' or gap_type.endswith('_forms'): return 'court_forms'
    return 'venue_local'


def reject_reason(title, markdown):
    """Extra soft-404 / shell rejection on top of the collector's quality gate. None means acceptable."""
    text = re.sub(r'\s+', ' ', markdown or '').strip()
    if len(text) < 400: return 'empty_or_insufficient_text'
    if len(text) < 2000 and SCRIPT_SHELL.search(text[:1500]): return 'script_shell_not_rendered'
    head = re.sub(r'^[#\s*>\-]+', '', text[:500])
    if BADTITLE.search(title or '') or BADTITLE.search(head): return 'error_title_or_heading'
    if SOFT404.search(title or '') or SOFT404.search(text[:500]): return 'soft_404_or_error_text'
    if CHALLENGE.search(title or '') or CHALLENGE.search(text[:1800]): return 'challenge_shell'
    if LOGIN.search(title or '') or (LOGIN.search(head) and len(text) < 1500): return 'login_shell'
    return None


def barrier_decision(row):
    status = str((row.get('directory_verification') or {}).get('http_status') or '')
    if host_in(row['host'], TERMS_HOSTS) and not host_in(row['host'], BARRIER_HOSTS):
        return 'skip_terms_review', 'Vendor-hosted code; terms/robots review required before any acquisition.'
    if status in ('403', '503'):
        return 'skip_barrier', f'Source directory recorded historical HTTP {status} for this exact URL; not retried or rerouted.'
    if host_in(row['host'], BARRIER_HOSTS):
        return 'skip_barrier', 'Host family is listed as a 403/503 barrier in the gap audit (state_county_gaps.md section 7); not rerouted through a provider.'
    return None, None


def saved_urls(wanted_exact, wanted_loose):
    """Exact and loose matches of candidate URLs against every local saved-URL store (read-only)."""
    hits, stats = {}, {}

    def see(url, store):
        if not isinstance(url, str) or not url: return
        if url in wanted_exact: hits.setdefault(url, []).append({'store': store, 'match': 'exact', 'saved_url': url})
        else:
            try: key = loose(url)
            except ValueError: return
            if key in wanted_loose: hits.setdefault(wanted_loose[key], []).append({'store': store, 'match': 'loose', 'saved_url': url})

    for name, file, queries in (
            ('directory.sqlite3', ROOT / 'delivery/archive-directory/directory.sqlite3',
             ['select source_url from records where source_url is not null', 'select source_url from browse where source_url is not null']),
            ('catalog/documents.sqlite3', ROOT / 'catalog/documents.sqlite3',
             ['select source_url from versions', 'select final_url from versions where final_url is not null'])):
        con = sqlite3.connect(file.as_uri() + '?mode=ro', uri=True)
        try:
            for query in queries:
                count = 0
                for (url,) in con.execute(query): count += 1; see(url, name + ':' + query.split(' from ')[1].split(' ')[0])
                stats[name + ' | ' + query] = count
        finally: con.close()
    pattern = re.compile(r'"(?:source_url|final_url)":\s*"((?:[^"\\]|\\.)*)"')
    for file in sorted((ROOT / 'sources').glob('*/resources.jsonl')):
        if file.parent == HERE: continue
        count = 0
        with file.open(encoding='utf-8-sig', errors='replace') as stream:
            for line in stream:
                for found in pattern.findall(line):
                    count += 1
                    try: see(json.loads('"' + found + '"'), rel(file))
                    except ValueError: pass
        stats[rel(file)] = count
    return hits, stats


def prepare(_args):
    if (HERE / 'seeds.jsonl').exists(): raise SystemExit('seeds.jsonl already frozen; not regenerated')
    raw = CANDIDATES.read_bytes(); rows = read_rows(CANDIDATES)
    exact = {r['url'] for r in rows}; loose_map = {loose(r['url']): r['url'] for r in rows}
    hits, stats = saved_urls(exact, loose_map)
    seeds = []
    for row in sorted(rows, key=lambda r: r['rank']):
        url, host = row['url'], row['host'].lower()
        decision, reason = barrier_decision(row)
        if not decision and url in hits:
            decision, reason = 'skip_already_saved', 'Already saved locally: ' + json.dumps(hits[url][:3])
        if not decision:
            decision = 'fetch_http_document' if row.get('content_kind') == 'pdf' else 'fetch_firecrawl'
            reason = 'Reviewed exact URL; no local saved copy; no listed barrier.'
        category = row.get('directory_category')
        family = family_of(row['gap_type'])
        bare = host[4:] if host.startswith('www.') else host
        seeds.append({'url': url, 'state': row['state'], 'gap_type': row['gap_type'], 'family': family,
                      'kind': category if category and category != 'uncategorized' else family,
                      'doc_kind': row.get('content_kind') or 'page', 'title': row.get('title'), 'host': host,
                      'allowed_hosts': sorted({host, bare, 'www.' + bare, *EXTRA_FINAL_HOSTS.get(host, [])}),
                      'source_directory_id': row.get('source_directory_id'), 'priority': row.get('priority'), 'rank': row['rank'],
                      'access_requirements': row.get('access_requirements'), 'directory_verification': row.get('directory_verification'),
                      'caution': row.get('caution') or None, 'decision': decision, 'decision_reason': reason})
    write_rows(HERE / 'seeds.jsonl', seeds)
    counts = {}
    for s in seeds: counts[s['decision']] = counts.get(s['decision'], 0) + 1
    write_json(HERE / 'prepare_receipt.json', {'prepared_at': now(), 'candidates': {'path': rel(CANDIDATES), 'sha256': sha(raw), 'rows': len(rows)},
               'seeds_sha256': sha((HERE / 'seeds.jsonl').read_bytes()), 'decisions': counts, 'states': len({s['state'] for s in seeds}),
               'saved_url_stores_scanned': stats, 'saved_matches': hits})
    print(json.dumps({'seeds': len(seeds), 'decisions': counts, 'saved_matches': len(hits)}))


def frozen_seeds():
    receipt = json.loads((HERE / 'prepare_receipt.json').read_text(encoding='utf-8'))
    raw = (HERE / 'seeds.jsonl').read_bytes()
    if sha(raw) != receipt['seeds_sha256']: raise SystemExit('seeds.jsonl differs from the frozen receipt')
    return read_rows(HERE / 'seeds.jsonl')


def collector():
    spec = importlib.util.spec_from_file_location('county_collect_reused', COLLECTOR)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def run(_args):
    c = collector(); crawler = c.crawler
    seeds = frozen_seeds()
    state_file = HERE / 'run_state.json'
    state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
    lock, stop, started = threading.Lock(), threading.Event(), time.monotonic()
    todo = [s for s in seeds if s['decision'].startswith('fetch') and s['url'] not in state]
    pages = [s for s in todo if s['decision'] == 'fetch_firecrawl']
    prior = sum(1 for v in state.values() if v.get('provider_request_admitted'))
    key = c.private_key(c.CREDENTIAL); client = c.CreditClient(key)

    def credits():
        status, body = client.call('GET', 'team/credit-usage')
        value = body.get('data', {}).get('remainingCredits')
        if status != 200 or type(value) not in (int, float) or value < 0: raise RuntimeError('Credit/auth preflight unavailable')
        return int(value)

    before = credits()
    cap = max(0, min(TASK_CREDIT_CAP - prior, before - BALANCE_FLOOR, len(pages)))
    write_json(HERE / 'preflight.json', {'checked_at': now(), 'remaining_credits': before, 'balance_floor': BALANCE_FLOOR, 'task_credit_cap': TASK_CREDIT_CAP,
               'prior_admitted_requests': prior, 'run_request_cap': cap, 'page_urls': len(pages), 'document_urls': len(todo) - len(pages), 'workers': 5,
               'per_host_delay_seconds': 2, 'automatic_retries': 0, 'proxy': 'basic', 'paid_parsers': False, 'key_printed': False,
               'no_purchases_or_overages': True, 'shared_host_controls': True})
    print(json.dumps({'credits_before': before, 'request_cap': cap, 'pages': len(pages), 'documents': len(todo) - len(pages)}), flush=True)
    cfg = crawler.Config.from_dict({'allow': [{'host': 'placeholder.invalid', 'path_prefixes': ['/']}], 'workers': 5, 'per_host_delay': 2.0,
        'respect_robots': True, 'shared_host_dir': 'corpus/_shared_hosts', 'follow_links': False, 'max_depth': 0, 'max_retries': 0,
        'timeout_seconds': 25, 'max_transfer_seconds': 120, 'max_response_bytes': 40 * 1024 * 1024, 'pause_host_on_access_block': True})
    art = crawler.Artifacts(HERE / 'http', cfg); gate = crawler.Gate(cfg, {}, stop, art); fetcher = crawler.Fetcher(cfg, art, gate, stop)
    admitted, blocked = [0], set()

    def put(url, value):
        with lock:
            state[url] = value; write_json(state_file, state)

    def paced(action):
        waited = time.monotonic()
        while not stop.is_set():
            try: return action()
            except crawler.PacingDeferred as error:
                if time.monotonic() - waited > 90: raise RuntimeError('host_paced_or_paused')
                stop.wait(min(1, max(.05, error.until - time.time())))
        raise RuntimeError('run_stopped')

    def scrape(seed):
        url, host = seed['url'], seed['host']
        allowed, why = paced(lambda: fetcher.robots(url))
        if not allowed: return {'status': 'robots_blocked', 'error': why}
        with lock:
            if admitted[0] >= cap: return None
            admitted[0] += 1
        request_id = uuid.uuid4().hex
        put(url, {'status': 'in_flight', 'provider_request_admitted': True, 'request_id': request_id, 'admitted_at': now()})
        paced(lambda: gate.reserve(host))
        response_path, api_status = HERE / '.firecrawl' / f'{request_id}.json', None
        try:
            payload = {'url': url, 'formats': ['markdown', 'html', 'rawHtml', 'links'], 'onlyMainContent': True, 'maxAge': 0, 'timeout': 60000, 'proxy': 'basic', 'parsers': []}
            request = urllib.request.Request('https://api.firecrawl.dev/v2/scrape', data=json.dumps(payload).encode(), method='POST',
                                             headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
            opener = urllib.request.build_opener(crawler.NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            try:
                with opener.open(request, timeout=80) as response: api_status = response.status; raw = response.read(40 * 1024 * 1024 + 1)
            except urllib.error.HTTPError as error: api_status = error.code; raw = error.read(1024 * 1024)
            if len(raw) > 40 * 1024 * 1024: raise ValueError('Provider payload exceeds 40MB')
            raw = raw.replace(key.encode(), b'[REDACTED]'); write_bytes(response_path, raw)
            body = json.loads(raw)
            if api_status != 200 or body.get('success') is not True:
                if api_status in (401, 402, 429): stop.set()
                raise ValueError('Firecrawl API ' + str(api_status) + ': ' + str(body.get('error') or 'unsuccessful')[:300])
            title, final, markdown, _html, meta = c.quality(body.get('data'), url, seed['allowed_hosts'])
            problem = reject_reason(title, markdown)
            if problem: raise ValueError('Rejected: ' + problem)
            html = str(body['data'].get('rawHtml') or body['data'].get('html') or '').encode('utf-8'); text = markdown.encode('utf-8')
            html_path, text_path = HERE / 'html' / f'{sha(html)}.html', HERE / 'text' / f'{sha(text)}.md'
            write_bytes(html_path, html); write_bytes(text_path, text)
            return {'status': 'captured', 'provider_request_admitted': True, 'request_id': request_id, 'captured_at': now(), 'title': title, 'final_url': final,
                    'raw_path': rel(html_path), 'raw_sha256': sha(html), 'raw_bytes': len(html), 'text_path': rel(text_path), 'text_sha256': sha(text),
                    'text_characters': len(markdown), 'mime_type': 'text/html', 'provider_receipt_path': rel(response_path), 'provider_receipt_sha256': sha(raw),
                    'provider_status': api_status, 'source_http_status': meta.get('statusCode'), 'capture_kind': 'provider_capture_not_original_http_bytes'}
        except Exception as error:
            message = str(error)[:500].replace(key, '[REDACTED]')
            if api_status == 200 and re.search(r'Source HTTP status (?:401|403|429|503)|challenge|access.denied|login', message, re.I):
                blocked.add(host); gate.update(host, reason='source_access_barrier')
            return {'status': 'rejected' if api_status == 200 else ('failed' if api_status is not None else 'uncertain_no_provider_status'),
                    'provider_request_admitted': True, 'request_id': request_id, 'error': message, 'provider_status': api_status,
                    'provider_receipt_path': rel(response_path) if response_path.exists() else None, 'finished_at': now()}
        finally: gate.release(host)

    def document(seed):
        url, record = seed['url'], None
        while not stop.is_set():
            record = fetcher.fetch({'url': url, 'retry_count': 0})
            if record['status'] != 'pacing_deferred': break
            if time.monotonic() - started > ADMIT_SECONDS + 60: return {'status': 'failed', 'error': 'host_paced_or_paused'}
            stop.wait(min(1, max(.05, record['next_attempt_at'] - time.time())))
        if not record: return {'status': 'failed', 'error': 'run_stopped'}
        receipt = HERE / 'http' / 'metadata' / f'{sha(url.encode())}.json'; write_json(receipt, record)
        base = {'access_receipt_path': rel(receipt), 'access_receipt_sha256': sha(receipt.read_bytes()), 'source_http_status': record.get('http_status')}
        if record['status'] != 'downloaded': return {**base, 'status': 'robots_blocked' if 'robots' in str(record['status']) else 'failed', 'error': str(record['status']) + ': ' + str(record.get('error'))[:300]}
        raw_path = HERE / 'http' / record['raw_path']; raw = raw_path.read_bytes()
        if raw[:5] != b'%PDF-': return {**base, 'status': 'rejected', 'error': 'Rejected: document URL did not return PDF bytes'}
        text_path = HERE / 'http' / record['text_path'] if record.get('text_path') else None
        text = text_path.read_text(encoding='utf-8', errors='replace') if text_path else ''
        return {**base, 'status': 'captured', 'captured_at': record['fetched_at'], 'title': record.get('title'), 'final_url': url, 'raw_path': rel(raw_path),
                'raw_sha256': sha(raw), 'raw_bytes': len(raw), 'text_path': rel(text_path) if text.strip() else None,
                'text_sha256': sha(text_path.read_bytes()) if text.strip() else None, 'text_characters': len(text) if text.strip() else None,
                'mime_type': 'application/pdf', 'capture_kind': 'official_http_original', 'extraction_status': record.get('extraction_status'),
                'page_count': record.get('page_count'), 'http_last_modified': (record.get('headers') or {}).get('last-modified')}

    def host_job(items):
        for seed in sorted(items, key=lambda s: (s['doc_kind'] == 'pdf', s['rank'])):
            if stop.is_set() or time.monotonic() - started > ADMIT_SECONDS: return
            if seed['host'] in blocked:
                put(seed['url'], {'status': 'skipped_host_barrier_observed', 'error': 'An earlier URL on this host returned an access barrier in this run; no request made.'}); continue
            try: result = scrape(seed) if seed['decision'] == 'fetch_firecrawl' else document(seed)
            except Exception as error: result = {'status': 'failed', 'error': str(error)[:300].replace(key, '[REDACTED]')}
            if result is not None:
                if state.get(seed['url'], {}).get('provider_request_admitted'): result.setdefault('provider_request_admitted', True)
                put(seed['url'], result)
                print(result['status'], seed['state'], seed['url'], (result.get('error') or '')[:120], flush=True)

    groups = {}
    for seed in todo: groups.setdefault(seed['host'][4:] if seed['host'].startswith('www.') else seed['host'], []).append(seed)
    with c.worker_lock(ROOT / 'sources/trellis/worker'), ThreadPoolExecutor(max_workers=5) as pool:
        for future in [pool.submit(host_job, items) for items in sorted(groups.values(), key=len, reverse=True)]: future.result()
    after = credits()
    runs = json.loads((HERE / 'run_log.json').read_text(encoding='utf-8')) if (HERE / 'run_log.json').exists() else []
    runs.append({'started_at_offset_seconds': 0, 'finished_at': now(), 'elapsed_seconds': round(time.monotonic() - started, 1), 'credits_before': before,
                 'credits_after': after, 'credit_delta': before - after, 'provider_requests_admitted': admitted[0], 'request_cap': cap,
                 'credit_basis': 'Firecrawl team/credit-usage remainingCredits before and after this run'})
    write_json(HERE / 'run_log.json', runs)
    print(json.dumps(runs[-1]))


def build(_args):
    seeds = frozen_seeds()
    state = json.loads((HERE / 'run_state.json').read_text(encoding='utf-8')) if (HERE / 'run_state.json').exists() else {}
    resources, gaps = [], []
    # Offline post-capture review (files stay on disk as evidence; the record is demoted to a gap, never retried).
    review, captured_loose = {}, {}
    for seed in seeds:
        got = state.get(seed['url'])
        if got and got.get('status') == 'captured' and got['capture_kind'] == 'provider_capture_not_original_http_bytes':
            problem = reject_reason(got.get('title'), (ROOT / got['text_path']).read_text(encoding='utf-8'))
            if problem: review[seed['url']] = 'Rejected at build review: ' + problem
            else: captured_loose[loose(seed['url'])] = seed['url']
    for seed in seeds:
        got = state.get(seed['url'])
        if got and got.get('status') == 'captured' and seed['url'] not in review and got.get('final_url'):
            target = captured_loose.get(loose(got['final_url']))
            if target and target != seed['url']: review[seed['url']] = 'Rejected at build review: redirect_alias_of_captured_url ' + target
    for seed in seeds:
        url, got = seed['url'], state.get(seed['url'])
        if url in review: got = {**got, 'status': 'rejected', 'error': review[url]}
        common = {'state': seed['state'], 'gap_type': seed['gap_type'], 'family': seed['family'], 'source_directory_id': seed['source_directory_id']}
        if got and got.get('status') == 'captured':
            for field in ('raw', 'text'):
                if got.get(field + '_path') and sha((ROOT / got[field + '_path']).read_bytes()) != got[field + '_sha256']: raise SystemExit('hash mismatch ' + url)
            text = (ROOT / got['text_path']).read_text(encoding='utf-8') if got.get('text_path') else ''
            heading = re.search(r'^#{1,3}\s+(.+)$', text, re.M)
            provider = got['capture_kind'] == 'provider_capture_not_original_http_bytes'
            resources.append({'id': 'gapfill:' + sha(url.encode())[:24], 'source_url': url, 'final_url': got.get('final_url') or url,
                'title': ' '.join((got.get('title') or '').split()) or (heading.group(1).strip() if heading else None) or seed.get('title') or url,
                'kind': seed['kind'], 'captured_at': got['captured_at'], 'raw_path': got['raw_path'], 'raw_sha256': got['raw_sha256'], 'raw_bytes': got['raw_bytes'],
                'text_path': got.get('text_path'), 'text_sha256': got.get('text_sha256'), 'text_characters': got.get('text_characters'),
                'mime_type': got['mime_type'], 'text_mime_type': 'text/markdown' if provider else 'text/plain',
                'reading_method': 'Firecrawl main-content Markdown (provider rendering)' if provider else 'native local PDF text extraction',
                'capture_kind': got['capture_kind'], 'original_http_bytes': not provider,
                'provider_receipt_path': got.get('provider_receipt_path'), 'provider_receipt_sha256': got.get('provider_receipt_sha256'),
                'access_receipt_path': got.get('access_receipt_path'), 'access_receipt_sha256': got.get('access_receipt_sha256'),
                'http_status': got.get('source_http_status'), 'http_status_basis': 'provider-reported source status' if provider else 'direct HTTP receipt',
                'http_last_modified': got.get('http_last_modified'), 'source_as_of': None, **common, 'law_family': seed['family'], 'doc_kind': seed['doc_kind'],
                'packet': PACKET, 'seed_rank': seed['rank'], 'jurisdiction': {'country': 'US', 'system': 'state', 'state': seed['state']},
                'temporal': {'captured_at': got['captured_at'], 'captured_at_basis': 'Provider response receipt time (UTC)' if provider else 'Collector HTTP receipt timestamp',
                             'source_as_of': None, 'source_as_of_basis': None, 'published_at': None, 'published_at_basis': None,
                             'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None},
                'content_scope_note': 'This saves the one listed URL as rendered for the provider (or the one PDF), not the rules, forms, orders or case lists linked from it. Capture date does not certify legal currency.'})
            continue
        if got: category, reason = got['status'], got.get('error')
        elif seed['decision'].startswith('skip'): category, reason = seed['decision'], seed['decision_reason']
        else: category, reason = 'not_attempted', 'Not reached within the packet time/credit bound; no request made.'
        gaps.append({'url': url, **common, 'category': category, 'reason': reason, 'credit_spent_possible': bool(got and got.get('provider_request_admitted')),
                     'provider_receipt_path': (got or {}).get('provider_receipt_path'), 'next_step': 'Manual review or publisher permission; do not reroute around the barrier.' if 'barrier' in category or category in ('robots_blocked', 'skip_terms_review') else 'Review before any later packet.'})
    write_rows(HERE / 'resources.jsonl', resources)
    tally = {}
    for g in gaps: tally[g['category']] = tally.get(g['category'], 0) + 1
    write_json(HERE / 'gaps.json', {'generated_at': now(), 'counts': tally, 'total': len(gaps), 'gaps': gaps})
    runs = json.loads((HERE / 'run_log.json').read_text(encoding='utf-8')) if (HERE / 'run_log.json').exists() else []
    files = [{'path': name, 'sha256': sha((HERE / name).read_bytes()), 'rows': rows} for name, rows in (('resources.jsonl', len(resources)), ('seeds.jsonl', len(seeds)), ('gaps.json', len(gaps)))]
    receipt = json.loads((HERE / 'prepare_receipt.json').read_text(encoding='utf-8'))
    ids = {r['id'] for r in resources}
    ok = bool(resources) and len(ids) == len(resources) and all(r['source_url'] in {s['url'] for s in seeds} for r in resources)
    write_json(HERE / 'validation.json', {'schema_version': '1', 'status': 'passed' if ok else 'failed', 'passed': ok, 'ready': ok, 'validated_at': now(),
        'resources_sha256': files[0]['sha256'], 'gaps_sha256': files[2]['sha256'], 'data_files': files,
        'counts': {'candidates': len(seeds), 'resources': len(resources), 'provider_page_captures': sum(1 for r in resources if not r['original_http_bytes']),
                   'original_pdf_documents': sum(1 for r in resources if r['original_http_bytes']), 'gaps': len(gaps), 'gap_categories': tally,
                   'states_in_packet': len({s['state'] for s in seeds}), 'states_captured': len({r['state'] for r in resources}),
                   'credits_used': sum(r['credit_delta'] for r in runs), 'provider_requests_admitted': sum(r['provider_requests_admitted'] for r in runs)},
        'checks': ['Frozen exact URL set (seeds.jsonl hash pinned in prepare_receipt.json)', 'Deduplicated against directory.sqlite3, catalog/documents.sqlite3 and every sources/*/resources.jsonl',
                   'Historical 403/503 URLs, audit-listed barrier hosts and vendor-hosted codes skipped without any request', 'robots.txt honored with the shared host gate; no retries; basic proxy only',
                   'Provider-reported source status must be 2xx and final host must be the reviewed host', 'Soft 404, challenge, login and empty bodies rejected even on HTTP 200',
                   'Raw and text files re-hashed at build time', 'Unique stable ids bound to the requested URL', 'Credential never written to artifacts (provider payloads redacted)'],
        'qualification': 'Saved representations of exact reviewed official URLs captured on 2026-09-19. Page captures are Firecrawl provider renderings, not original HTTP bytes; they do not include linked documents and do not certify that any rule, form or programme is current or complete.',
        'license_ref': 'Official government/court publications; third-party hosted portals (e.g. Florida Bar) keep their own terms. No redistribution licence is asserted.',
        'inputs': [receipt['candidates'], {'path': rel(COLLECTOR), 'sha256': sha(COLLECTOR.read_bytes())}]})
    registry = {'batches': [{'name': HERE.name, 'folder': rel(HERE), 'id_prefix': 'gapfill:', 'note': 'State law/court gap-fill packet; provider page captures plus original PDFs.'}]}
    write_json(HERE / 'capture_registry.json', registry)
    write_json(HERE / 'capture_registry.validation.json', {'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': now(),
        'data_files': [{'path': 'capture_registry.json', 'sha256': sha((HERE / 'capture_registry.json').read_bytes()), 'rows': 1}], 'counts': {'batches': 1},
        'checks': ['Registry hash pinned; each batch is gated separately by its own validation.json'], 'qualification': 'Registers capture batches for delivery/archive-directory/source_captures.py.',
        'license_ref': '', 'inputs': []})
    print(json.dumps({'resources': len(resources), 'gaps': tally, 'states_captured': len({r['state'] for r in resources}), 'status': 'passed' if ok else 'failed'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=['prepare', 'run', 'build'])
    arguments = parser.parse_args(); {'prepare': prepare, 'run': run, 'build': build}[arguments.command](arguments)
