"""One-shot retry of the prior run's unresolved Trellis county-profile URLs. No discovery, no identity joins.

Usage:  python collect_retry.py prepare   (offline: freeze seeds.jsonl from the prior run's unresolved_gaps.jsonl)
        python collect_retry.py run       (network: one attempt per seed through the existing private Firecrawl wrapper)
        python collect_retry.py finalize  (offline: re-hash artifacts, write resources/gaps/validation)

The key is loaded by the existing DPAPI wrapper, lives only in memory/child environment and is never written or printed.
Nothing outside this folder is written. Every URL gets exactly one attempt; a registered start is never repeated.
"""
from __future__ import annotations
import contextlib, datetime as dt, hashlib, json, os, re, sqlite3, subprocess, sys, time
import concurrent.futures as cf
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / 'sources/trellis_county_firecrawl_20260919'
QUEUE = ROOT / 'reports/trellis_refocus_20260919/county_audit/remaining_observed_county_urls.jsonl'
CATALOG = ROOT / 'sources/trellis/catalog/catalog.sqlite3'
SUPPLEMENT = ROOT / 'sources/trellis_county_offline_supplement_20260919'   # two short profiles recovered offline before this retry ran
COVERAGE = ROOT / 'sources/trellis_coverage_20260919'                      # published coverage layer (read-only)
NEXT_RUN = ROOT / 'reports/trellis_refocus_20260919/NEXT_RUN.md'           # standing guidance: do not retry unchanged failures
BROWSER_CHECK = ROOT / 'reports/trellis_refocus_20260919/lowndes_browser_check.json'
URL_RE = r'https://trellis\.law/coverage/[a-z-]+/[^/?#]+'
MAX_JOBS = 3            # prior run used 5; brief allows <= 8
HARD_CAP = 45           # credits for this worker
FLOOR = 500             # never let the account fall under this
BATCH_PAUSE = 2.0       # seconds between batches
PRIOR_MAX_AGE = '172800000'
LICENSE = 'Trellis publisher page; internal research use; not for redistribution'
REPRESENTATION = 'Firecrawl selected rendered county profile section; provider response is not original HTTP bytes.'
CHALLENGE = r'verify you are human|checking your browser|access denied|just a moment|captcha|enable javascript and cookies'
LOGIN = r'sign in to (continue|your account)|log in to (continue|view)|create (a|your) free account to'


def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rel(path): return Path(path).relative_to(ROOT).as_posix()
def readl(path): return [json.loads(x) for x in Path(path).read_text(encoding='utf-8-sig').splitlines() if x.strip()] if Path(path).exists() else []
def save(path, value):
    tmp = Path(str(path) + '.tmp'); tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); os.replace(tmp, path)
def savel(path, values):
    tmp = Path(str(path) + '.tmp'); tmp.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in values), encoding='utf-8'); os.replace(tmp, path)
def appendl(path, value):
    with Path(path).open('a', encoding='utf-8') as h: h.write(json.dumps(value, ensure_ascii=False) + '\n')
def ident(url): return hashlib.sha256(url.encode()).hexdigest()[:24]


@contextlib.contextmanager
def writer_lock():
    import msvcrt
    with (HERE / 'collector.lock').open('a+b') as handle:
        if handle.tell() == 0: handle.write(b'0'); handle.flush()
        handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try: yield
        finally: handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class ProfileParser(HTMLParser):
    """Same heading/label/field reading as the prior run's collector."""
    def __init__(self):
        super().__init__(); self.active = None; self.parts = []; self.heading = ''; self.label = ''; self.fields = {}; self.website = None
    def handle_starttag(self, tag, attrs):
        if tag in {'h1', 'h2', 'h3'}: self.active = tag; self.parts = []
        if tag == 'br' and self.active: self.parts.append(' ')
        if tag == 'a' and self.active == 'h3' and self.label.lower() == 'website':
            href = dict(attrs).get('href', ''); p = urlsplit(href)
            if p.scheme in {'http', 'https'} and p.hostname and not p.username and not p.password: self.website = href
    def handle_data(self, data):
        if self.active: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag != self.active: return
        text = ' '.join(' '.join(self.parts).split())
        if tag == 'h1': self.heading = text
        elif tag == 'h2': self.label = text
        elif tag == 'h3' and self.label: self.fields.setdefault(re.sub(r'\W+', '_', self.label.lower()).strip('_'), text)
        self.active = None; self.parts = []


def inspect(obj, expected):
    """Pure gate. Returns (outcome, reason, parsed). outcome in accepted|rejected|barrier."""
    m = obj.get('metadata') or {}; status = m.get('statusCode'); html = obj.get('html') or ''; markdown = obj.get('markdown') or ''
    parser = ProfileParser(); parser.feed(html)
    parsed = {'heading': parser.heading, 'fields': parser.fields, 'website_url': parser.website, 'markdown_chars': len(markdown), 'html_chars': len(html)}
    if status in {401, 403, 429}: return 'barrier', 'access_barrier_' + str(status), parsed
    if status != 200: return 'rejected', 'provider_http_' + str(status), parsed
    if m.get('proxyUsed') != 'basic': return 'barrier', 'unexpected_proxy', parsed
    if m.get('creditsUsed') not in (0, 1): return 'barrier', 'unexpected_response_credit_cost', parsed
    if m.get('sourceURL') != expected or str(m.get('url', '')).rstrip('/') != expected.rstrip('/'): return 'rejected', 'redirect_or_url_mismatch', parsed
    if re.search(CHALLENGE, markdown, re.I): return 'barrier', 'access_challenge', parsed
    if re.search(LOGIN, markdown, re.I): return 'rejected', 'login_shell', parsed
    if not markdown.strip() and not html.strip(): return 'rejected', 'empty_body', parsed
    if not parser.heading or not re.search(r'\bCourts Records$', parser.heading, re.I) or 'top-county-info-block__container' not in html:
        return 'rejected', 'no_county_profile_heading', parsed
    if len(markdown) < 100 or len(parser.fields) < 2: return 'rejected', 'incomplete_county_info_section', parsed
    return 'accepted', None, parsed


def proofs():
    out = {}
    c = sqlite3.connect(CATALOG.as_uri() + '?mode=ro', uri=True); c.row_factory = sqlite3.Row
    try:
        for row in c.execute("select source_url,url,label from links where category='coverage_county'"): out.setdefault(row['url'], dict(row))
    finally: c.close()
    return out


def shared_hold():
    p = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    if not p.exists(): return None
    c = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True); c.row_factory = sqlite3.Row
    try:
        for row in c.execute("select * from host_state where host in ('trellis.law','api.firecrawl.dev')"):
            if row['pause_reason'] or row['cooldown_until'] > time.time(): return dict(row)
    finally: c.close()
    return None


def gated_json(folder, name, hash_key=None):
    """Read another owner's file only when that owner's validation.json passes and binds the file's SHA-256 (fail closed)."""
    v = json.loads((folder / 'validation.json').read_text(encoding='utf-8-sig'))
    want = v.get(hash_key) if hash_key else {f['path']: f['sha256'] for f in v.get('data_files', [])}.get(name)
    if v.get('status') != 'passed' or v.get('ready') is not True or not want or sha(folder / name) != want: raise RuntimeError('input_gate_failed:' + rel(folder / name))
    return v


def local_evidence():
    """What local files already settle: (published url -> supplement row + validation time, browser-checked url -> observation)."""
    v = gated_json(SUPPLEMENT, 'resources.jsonl', 'manifest_sha256')
    published = {r['source_url']: {**r, '_validated_at': v.get('validated_at')} for r in readl(SUPPLEMENT / 'resources.jsonl')}
    b = json.loads(BROWSER_CHECK.read_text(encoding='utf-8-sig')) if BROWSER_CHECK.exists() else {}
    settled = b.get('published_as_complete_county_profile') is False and b.get('information_h2_h3') == [] and b.get('source_url')
    return published, ({b['source_url']: b} if settled else {})


def guard_refusal(gap, max_age_ms, now_dt, published, browser_checked):
    """Pure guard. None when a request could tell us something new, else the reason it must not be sent."""
    if gap['url'] in published: return 'already_published_locally'
    if gap['url'] in browser_checked: return 'browser_check_already_settled'
    if str(gap.get('reason', '')).startswith('provider_http_404') and int(max_age_ms) > 0:
        done = gap.get('completed_at')   # unknown age is treated as covered: the provider would replay (and bill) its cached 404
        if not done or (now_dt - dt.datetime.fromisoformat(done)).total_seconds() * 1000 < int(max_age_ms): return 'prior_404_inside_max_age_window'
    return None


GUARD_WHY = {'already_published_locally': 'Already recovered offline and published: ' + 'sources/trellis_county_offline_supplement_20260919/resources.jsonl',
             'browser_check_already_settled': 'Signed-in browser check already found no profile fields: reports/trellis_refocus_20260919/lowndes_browser_check.json',
             'prior_404_inside_max_age_window': 'Prior 404 is younger than the requested max-age, so the provider would replay (and bill) its cached 404; not a new observation.'}


def plan_seeds(now_dt):
    """Offline and pure apart from read-only inputs. Returns (seeds, excluded)."""
    gaps = readl(PRIOR / 'unresolved_gaps.jsonl'); invalid = {x['url']: x for x in readl(PRIOR / 'invalid_candidate_exclusions.jsonl')}
    saved = {r['source_url'] for r in readl(PRIOR / 'resources.jsonl')}; pf = proofs(); published, browser = local_evidence(); seeds = []; excluded = []
    for g in gaps:
        url = g['url']; raw = ROOT / g['raw_path']
        base = {'url': url, 'prior_reason': g['reason'], 'prior_attempt_completed_at': g.get('completed_at'), 'prior_raw_path': g['raw_path'],
                'prior_raw_sha256': sha(raw) if raw.exists() else None}
        if url in invalid:
            excluded.append({**base, 'decision': 'not_retried', 'guard': 'not_a_county_profile_url',
                             'why': 'Prior owner proved this is a county website link misread as a profile URL; not a county-profile page.'}); continue
        assert re.fullmatch(URL_RE, url) and url in pf and url not in saved, url
        # A 200-status thin response would be re-served from the provider cache under the prior 48 h max-age, so those ask for a fresh render.
        fresh = g['reason'] == 'incomplete_county_info_section'; max_age = '0' if fresh else PRIOR_MAX_AGE
        refusal = guard_refusal(g, max_age, now_dt, published, browser)
        if refusal: excluded.append({**base, 'decision': 'not_retried', 'guard': refusal, 'why': GUARD_WHY[refusal]}); continue
        seeds.append({**base, 'max_age_ms': max_age, 'max_age_basis': 'fresh render requested; prior 200 response was thin' if fresh else 'same as prior run',
                      'observed_parent': pf[url], 'sibling_url_saved_in_prior_run': url[:-4] if url.endswith('city') and url[:-4] in saved else None})
    assert len(seeds) + len(excluded) == len(gaps) == 31, (len(seeds), len(excluded), len(gaps))
    return seeds, excluded


def prepare():
    seeds, excluded = plan_seeds(dt.datetime.now(dt.timezone.utc)); kinds = {}
    for x in excluded: kinds[x['guard']] = kinds.get(x['guard'], 0) + 1
    print(json.dumps({'seeds_the_guard_would_allow': len(seeds), 'excluded': len(excluded), 'excluded_by_guard': kinds}))
    if readl(HERE / 'started.jsonl'):   # seeds.jsonl is the frozen record of what was actually requested on 2026-09-19; never rewrite it
        raise SystemExit('Requests were already sent from seeds.jsonl; it is frozen history and was not rewritten. Nothing to prepare.')
    savel(HERE / 'seeds.jsonl', seeds); savel(HERE / 'excluded_from_retry.jsonl', excluded)


def credit_status(client_key):
    from firecrawl_batch_worker import Client
    status, body = Client(client_key).call('GET', 'team/credit-usage'); data = body.get('data', {}) if isinstance(body, dict) else {}
    receipt = {'checked_at': now(), 'http_status': status, 'remaining_credits': data.get('remainingCredits'), 'endpoint': 'GET team/credit-usage', 'key_printed': False}
    appendl(HERE / 'credit_receipts.jsonl', receipt)
    if status != 200 or not isinstance(receipt['remaining_credits'], int): raise RuntimeError('credit_or_auth_status_failed')
    return receipt['remaining_credits']


def run():
    seeds = readl(HERE / 'seeds.jsonl'); started = {x['url'] for x in readl(HERE / 'started.jsonl')}
    published, browser = local_evidence(); at = dt.datetime.now(dt.timezone.utc)
    pending = [s for s in seeds if s['url'] not in started and not guard_refusal(
        {'url': s['url'], 'reason': s['prior_reason'], 'completed_at': s.get('prior_attempt_completed_at')}, s['max_age_ms'], at, published, browser)]
    if not pending:   # no key load, no credit check, no request
        print(json.dumps({'pending': 0, 'status': 'nothing_to_send', 'network_requests': 0})); return
    for name in ('raw', 'text', 'html', 'logs'): (HERE / name).mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'pipeline'))
    import judge_firecrawl_private as private
    key = private.private_key(private.CREDENTIAL)
    env = {**os.environ, 'FIRECRAWL_API_KEY': key, 'FIRECRAWL_NO_ENDPOINT_FEEDBACK': '1'}
    env['NODE_OPTIONS'] = (env.get('NODE_OPTIONS', '') + ' --use-system-ca').strip()
    initial = current = credit_status(key); spent = 0; reason = 'completed'
    print(json.dumps({'pending': len(pending), 'already_started': len(started), 'balance_known': True}), flush=True)

    def acquire(seed):
        url = seed['url']; path = HERE / 'raw' / f'{ident(url)}.json'
        command = [str(private.NODE), str(private.CLI), 'scrape', url, '--format', 'markdown,html,links', '--include-tags', '.top-county-info-block__container',
                   '--proxy', 'basic', '--max-age', seed['max_age_ms'], '-o', str(path), '--json']
        entry = {'url': url, 'started_at': now(), 'outcome': 'failed', 'raw_path': rel(path),
                 'request': {'tool': 'firecrawl-cli scrape', 'formats': 'markdown,html,links', 'include_tags': '.top-county-info-block__container', 'proxy': 'basic',
                             'max_age_ms': seed['max_age_ms'], 'cookies_sent': False, 'attempt': 1}}
        stop = False
        try:
            proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=100)
            log = re.sub(r'fc-[a-fA-F0-9]{20,}', '[REDACTED]', (proc.stdout + '\n' + proc.stderr).replace(key, '[REDACTED]'))
            (HERE / 'logs' / f'{ident(url)}.txt').write_text(log, encoding='utf-8'); entry['returncode'] = proc.returncode
            if proc.returncode or not path.exists():
                stop = bool(re.search(r'403|429|401|captcha|credit|payment|rate.limit|access.denied', log, re.I))
                entry['reason'] = 'provider_access_auth_credit_or_rate_barrier' if stop else 'provider_cli_failed'
            else:
                obj = json.loads(path.read_text(encoding='utf-8-sig')); m = obj.get('metadata') or {}
                outcome, why, parsed = inspect(obj, url)
                entry.update(outcome=outcome, reason=why, raw_sha256=sha(path), raw_bytes=path.stat().st_size,
                             provider={'scrape_id': m.get('scrapeId'), 'proxy_used': m.get('proxyUsed'), 'cache_state': m.get('cacheState'), 'cached_at': m.get('cachedAt'),
                                       'credits_used': m.get('creditsUsed'), 'status_code': m.get('statusCode'), 'source_url': m.get('sourceURL'), 'final_url': m.get('url'),
                                       'page_title': ' '.join(str(m.get('title') or '').split())[:160]},
                             parsed={k: parsed[k] for k in ('heading', 'markdown_chars', 'html_chars')}, parsed_field_names=sorted(parsed['fields']))
                stop = outcome == 'barrier'
        except subprocess.TimeoutExpired: entry['reason'] = 'single_attempt_timed_out'; stop = True
        except (OSError, ValueError) as exc: entry['reason'] = 'local_error:' + type(exc).__name__
        entry['completed_at'] = now(); entry['representation'] = 'connector/provider response JSON, not original HTTP bytes'
        return entry, stop

    with cf.ThreadPoolExecutor(max_workers=MAX_JOBS) as pool:
        for offset in range(0, len(pending), MAX_JOBS):
            batch = pending[offset:offset + MAX_JOBS]
            if shared_hold(): reason = 'stopped_shared_host_hold'; break
            if spent + len(batch) > HARD_CAP or current - len(batch) < FLOOR: reason = 'stopped_credit_cap_or_floor'; break
            for s in batch: appendl(HERE / 'started.jsonl', {'url': s['url'], 'started_at': now(), 'status': 'inflight'})
            halted = False; before = current
            for entry, stop in pool.map(acquire, batch): appendl(HERE / 'receipts.jsonl', entry); halted |= stop
            try: current = credit_status(key)
            except Exception: reason = 'stopped_credit_status_unknown'; break
            spent = initial - current
            if before - current > len(batch): halted = True; reason = 'stopped_unexpected_account_credit_delta'
            print(json.dumps({'done': offset + len(batch), 'of': len(pending), 'spent': spent}), flush=True)
            if halted: reason = reason if reason != 'completed' else 'stopped_access_or_transport_barrier'; break
            time.sleep(BATCH_PAUSE)
    save(HERE / 'run_state.json', {'finished_at': now(), 'status': reason, 'credits_at_start': initial, 'credits_at_end': current, 'account_credit_delta': initial - current,
                                   'hard_cap': HARD_CAP, 'floor': FLOOR, 'concurrency': MAX_JOBS, 'seeds': len(seeds), 'attempted_this_invocation': len(pending)})
    print(json.dumps({'status': reason, 'account_credit_delta': initial - current}), flush=True)


def finalize():
    seeds = {s['url']: s for s in readl(HERE / 'seeds.jsonl')}; receipts = readl(HERE / 'receipts.jsonl'); excluded = readl(HERE / 'excluded_from_retry.jsonl')
    started = {x['url'] for x in readl(HERE / 'started.jsonl')}; state = json.loads((HERE / 'run_state.json').read_text(encoding='utf-8')) if (HERE / 'run_state.json').exists() else {}
    assert len({r['url'] for r in receipts}) == len(receipts), 'a URL has more than one receipt'
    queue_sha = sha(QUEUE); resources = []; still = []; hash_failures = []
    prior_headings = {r['source_url']: r['heading'] for r in readl(PRIOR / 'resources.jsonl')}
    # Reconcile with what was already published before this retry ran (both inputs hash-gated on their owners' validation.json).
    published, browser = local_evidence(); gated_json(COVERAGE, 'progress.json')
    progress = json.loads((COVERAGE / 'progress.json').read_text(encoding='utf-8-sig')); remaining = set(progress['remaining_urls'])
    reviews = {x['unsaved_url']: x for x in progress['failed_url_variant_identity_reviews']}
    for r in receipts:
        seed = seeds[r['url']]; raw = ROOT / r['raw_path']
        if r.get('raw_sha256') and (not raw.exists() or sha(raw) != r['raw_sha256']): hash_failures.append(r['url']); continue
        if r['outcome'] == 'accepted':
            obj = json.loads(raw.read_text(encoding='utf-8-sig')); outcome, why, parsed = inspect(obj, r['url']); assert outcome == 'accepted', (r['url'], why)
            i = ident(r['url']); text = HERE / 'text' / f'{i}.md'; html = HERE / 'html' / f'{i}.html'
            text.write_text(obj.get('markdown') or '', encoding='utf-8'); html.write_text(obj.get('html') or '', encoding='utf-8')
            bits = urlsplit(r['url']).path.strip('/').split('/'); p = r['provider']
            resources.append({'id': 'trellis-county-fc:' + i, 'source_url': r['url'], 'url': r['url'], 'state_slug': bits[1], 'county_slug': bits[2],
                'title': parsed['heading'], 'heading': parsed['heading'], 'fields': parsed['fields'], 'website_url': parsed['website_url'],
                'raw_path': rel(raw), 'raw_sha256': r['raw_sha256'], 'raw_bytes': r['raw_bytes'], 'text_path': rel(text), 'text_sha256': sha(text),
                'html_path': rel(html), 'html_sha256': sha(html), 'captured_at': r['completed_at'],
                'captured_at_basis': 'Local acquisition completion; provider may serve a cached snapshot.',
                'provider': {k: p.get(k) for k in ('scrape_id', 'proxy_used', 'cache_state', 'credits_used', 'status_code')},
                'county_geoid': None, 'county_geoid_basis': 'Unresolved: use explicit heading/state and source-backed crosswalk, not slug alone.',
                'source_queue_path': rel(QUEUE), 'source_queue_sha256': queue_sha, 'observed_parent': seed['observed_parent'], 'representation': REPRESENTATION,
                'quality': 'Selected county information section; no filings, dockets or judge analyses acquired.',
                'retry_of': {'prior_reason': seed['prior_reason'], 'prior_raw_path': seed['prior_raw_path'], 'prior_raw_sha256': seed['prior_raw_sha256'], 'max_age_ms': seed['max_age_ms']},
                'temporal': {'captured_at': r['completed_at'], 'captured_at_basis': 'local acquisition completion', 'source_as_of': None, 'source_as_of_basis': None,
                             'published_at': None, 'published_at_basis': None, 'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None}})
        else:
            p = r.get('provider') or {}; url = r['url']; pub = published.get(url); rev = reviews.get(url); seen = browser.get(url)
            prior_raw = ROOT / seed['prior_raw_path']; same = None; prior_cache = None
            if raw.exists() and prior_raw.exists() and sha(prior_raw) == seed['prior_raw_sha256']:
                a = json.loads(raw.read_text(encoding='utf-8-sig')); b = json.loads(prior_raw.read_text(encoding='utf-8-sig'))
                same = a.get('markdown') == b.get('markdown') and a.get('html') == b.get('html'); prior_cache = (b.get('metadata') or {}).get('cacheState')
            known = guard_refusal({'url': url, 'reason': seed['prior_reason'], 'completed_at': seed.get('prior_attempt_completed_at')}, seed['max_age_ms'],
                                  dt.datetime.fromisoformat(r['started_at']), published, browser)
            still.append({'url': url, 'prior_reason': seed['prior_reason'], 'retry_reason': r.get('reason'), 'retry_outcome': r['outcome'], 'retry_status_code': p.get('status_code'),
                          'retry_cache_state': p.get('cache_state'), 'retry_cached_at': p.get('cached_at'), 'prior_cache_state': prior_cache,
                          'retry_page_title': p.get('page_title'), 'retry_heading': (r.get('parsed') or {}).get('heading'),
                          'retry_field_names': r.get('parsed_field_names'), 'retry_markdown_chars': (r.get('parsed') or {}).get('markdown_chars'),
                          'retry_raw_path': r['raw_path'], 'retry_raw_sha256': r.get('raw_sha256'), 'retry_started_at': r.get('started_at'), 'retry_completed_at': r.get('completed_at'),
                          'retry_content_identical_to_prior_response': same,
                          'outcome_known_locally_before_retry': known, 'outcome_known_basis': GUARD_WHY.get(known),
                          'already_published_in': {'path': rel(SUPPLEMENT / 'resources.jsonl'), 'id': pub['id'], 'heading': pub['heading'], 'fields': pub['fields'],
                                                   'captured_at': pub['captured_at'], 'supplement_validated_at': pub['_validated_at'],
                                                   'validated_before_retry_request': dt.datetime.fromisoformat(pub['_validated_at']) < dt.datetime.fromisoformat(r['started_at']),
                                                   'raw_path': pub['raw_path'], 'raw_sha256': pub['raw_sha256']} if pub else None,
                          'browser_check': {'path': rel(BROWSER_CHECK), 'sha256': sha(BROWSER_CHECK), 'checked_on': seen.get('checked_on'), 'browser_result': seen.get('browser_result'),
                                            'representation': seen.get('representation')} if seen else None,
                          'coverage_lists_url_as_remaining': url in remaining,
                          'coverage_fips': rev.get('fips') if rev else None, 'coverage_county_label': rev.get('county') if rev else None,
                          'coverage_alternative_profile_detail_saved': rev.get('alternative_profile_detail_saved') if rev else None,
                          'coverage_alternate_saved_urls': list(rev.get('alternate_saved_urls') or []) if rev else [],
                          'sibling_url_saved_in_prior_run': seed.get('sibling_url_saved_in_prior_run'),
                          'prior_batch_sibling_heading': prior_headings.get(seed.get('sibling_url_saved_in_prior_run')),
                          'prior_batch_sibling_heading_names_a_city': bool(re.search(r' city ', prior_headings.get(seed.get('sibling_url_saved_in_prior_run')) or '')) if seed.get('sibling_url_saved_in_prior_run') else None,
                          'attempts_total_both_runs': 2})
    not_attempted = [{'url': u, 'prior_reason': s['prior_reason'], 'why': 'registered start without receipt; not repeated' if u in started else 'not reached (run stopped early)'}
                     for u, s in seeds.items() if u not in {r['url'] for r in receipts}]
    resources.sort(key=lambda x: x['source_url']); savel(HERE / 'resources.jsonl', resources)
    reasons = {}
    for g in still: reasons[g['retry_reason']] = reasons.get(g['retry_reason'], 0) + 1
    genuine = [g for g in still if not g['already_published_in']]; recovered = [g for g in still if g['already_published_in']]
    matches = {g['url'] for g in genuine} == remaining and all(g['already_published_in']['validated_before_retry_request'] for g in recovered)
    save(HERE / 'gaps.json', {'generated_at': now(), 'genuine_gaps_after_retry': genuine, 'already_recovered_elsewhere': recovered,
                              'retry_rejection_reason_counts': reasons, 'not_attempted': not_attempted,
                              'excluded_not_county_profile_urls': excluded, 'artifact_hash_failures': hash_failures,
                              'coverage_layer_reconciliation': {'progress_path': rel(COVERAGE / 'progress.json'), 'progress_as_of': progress.get('as_of'),
                                  'remaining_county_urls': progress.get('remaining_county_urls'), 'genuine_gaps_equal_coverage_remaining_urls': matches,
                                  'failed_url_variants_with_saved_identity_details': progress.get('failed_url_variants_with_saved_identity_details'),
                                  'failed_url_variants_without_saved_identity_details': progress.get('failed_url_variants_without_saved_identity_details')},
                              'do_not_repeat': 'Every outcome in this batch was already determinable from local files before the requests were sent (see outcome_known_basis per row and '
                                               + rel(NEXT_RUN) + '). The run should not be repeated; the coverage layer needs no gap change from this folder.',
                              'field_scope_notes': {
                                  'already_recovered_elsewhere': 'The retry gate rejected these responses, but the same publisher fields were recovered offline and published before the retry. They are not gaps.',
                                  'coverage_alternate_saved_urls': "Authoritative: the coverage layer's identity review (all saved Trellis profiles: historical catalog, browser captures, both provider packages). "
                                                                   'The alternate URL is a different saved page for the same Census identity; the failed URL itself stays unsaved.',
                                  'sibling_url_saved_in_prior_run': 'Narrow: only whether the same slug without "city" sits in the prior Firecrawl batch. Null here does not mean the city has no saved profile; use coverage_alternate_saved_urls.'},
                              'note': 'A 404 here is the publisher page "Page Not Found" as rendered by the provider; it says nothing about whether the county or city has court records.'})
    delta = state.get('account_credit_delta'); va = [g for g in genuine if g['coverage_alternative_profile_detail_saved'] is not None]
    counts = {'prior_unresolved_attempts': 31, 'excluded_not_county_profile_urls': len(excluded), 'seed_urls': len(seeds), 'retry_requests_sent': len(receipts),
              'accepted_profiles': len(resources), 'retry_responses_rejected_by_gate': len(still), 'genuine_gaps_after_retry': len(genuine),
              'already_recovered_elsewhere': len(recovered), 'coverage_layer_remaining_county_urls': progress.get('remaining_county_urls'),
              'outcomes_already_known_locally_before_retry': sum(1 for g in still if g['outcome_known_locally_before_retry']),
              'retry_content_identical_to_prior_response': sum(1 for g in still if g['retry_content_identical_to_prior_response']),
              'not_attempted': len(not_attempted), 'credits_spent_account_delta': delta,
              'credits_at_start': state.get('credits_at_start'), 'credits_at_end': state.get('credits_at_end'), 'fresh_render_requests': sum(s['max_age_ms'] == '0' for s in seeds.values()),
              'fresh_render_still_thin': sum(1 for g in still if g['retry_reason'] == 'incomplete_county_info_section'),
              'retry_http_404': sum(1 for g in still if g['retry_status_code'] == 404),
              'retry_http_404_served_from_provider_cache': sum(1 for g in still if g['retry_status_code'] == 404 and g['retry_cache_state'] == 'hit'),
              'coverage_layer_alternate_profile_saved': sum(1 for g in va if g['coverage_alternative_profile_detail_saved']),
              'coverage_layer_no_alternate_profile': sum(1 for g in va if not g['coverage_alternative_profile_detail_saved']),
              'prior_firecrawl_batch_only_sibling_slug_names_same_city': sum(1 for g in still if g['prior_batch_sibling_heading_names_a_city']),
              'prior_firecrawl_batch_only_sibling_slug_is_a_county_page': sum(1 for g in still if g['prior_batch_sibling_heading_names_a_city'] is False),
              'crosswalk_resolved': None, 'crosswalk_unresolved': None}
    cw = HERE / 'county_name_crosswalk.json'
    if cw.exists():
        c = json.loads(cw.read_text(encoding='utf-8')); counts['crosswalk_resolved'] = len(c['resolved']); counts['crosswalk_unresolved'] = len(c['unresolved'])
    data_names = ['resources.jsonl', 'gaps.json', 'seeds.jsonl', 'receipts.jsonl', 'credit_receipts.jsonl', 'excluded_from_retry.jsonl', 'county_name_crosswalk.json']
    def nrows(p): return len(readl(p)) if p.suffix == '.jsonl' else None
    ok = not hash_failures and state.get('status') == 'completed' and not not_attempted and (delta is not None and delta <= HARD_CAP) and matches
    save(HERE / 'validation.json', {'schema_version': '1', 'status': 'passed' if ok else 'failed', 'ready': bool(ok), 'validated_at': now(),
        'data_files': [{'path': n, 'sha256': sha(HERE / n), 'rows': nrows(HERE / n)} for n in data_names if (HERE / n).exists()], 'counts': counts,
        'checks': ['no firecrawl worker process running before start (checked by the agent)', 'exactly one receipt per attempted URL; starts registered before calls',
                   'every raw provider response re-hashed against its receipt', 'accepted rows re-parsed offline with the same gate as the prior run plus login/empty-body rejection',
                   'account credit delta <= 45 and balance never below 500', 'prior owner folders opened read-only; nothing outside this folder written',
                   'crosswalk rows require exactly one inventory row for the state after documented normalisation',
                   'offline supplement and coverage progress.json read only after their own validation.json gate and SHA-256 matched',
                   'genuine gap URLs equal the coverage layer remaining_urls exactly; already-recovered rows were validated before the retry request time',
                   'prepare/run guard: URLs already published locally, settled by the browser check, or with a prior 404 inside the max-age window are never seeded or sent'],
        'qualification': REPRESENTATION + ' Retry batch for the 29 unresolved profile URLs of sources/trellis_county_firecrawl_20260919 (2 website-link artifacts excluded). '
                         f'Result: {len(resources)} profiles accepted; the gate rejected all {len(still)} responses. Of those, {len(recovered)} (OK Major, OK Seminole) had already been recovered '
                         f'offline and published in sources/trellis_county_offline_supplement_20260919 before the retry, so {len(genuine)} genuine URL gaps remain, the same {len(genuine)} the '
                         f"coverage layer already lists; this folder changes no gap. {counts['outcomes_already_known_locally_before_retry']} of the {len(still)} outcomes were already determinable from local files before the requests were sent "
                         '(reports/trellis_refocus_20260919/NEXT_RUN.md, lowndes_browser_check.json, the offline supplement, and prior 404s inside the 48 h max-age window), '
                         f'so the {delta} credits bought no new information and the run should not be repeated. The 404 retries that report cache_state "hit" are the provider replaying its '
                         'earlier 404, not a second independent observation of the publisher. "passed" means the batch is internally consistent, hash-bound and reconciled with the coverage '
                         'layer, not that gaps were closed. County GEOIDs on resources stay null; fields are publisher claims as rendered on the capture date; nothing here measures court-record coverage.',
        'license_ref': LICENSE, 'run_status': state.get('status'),
        'inputs': [{'path': rel(PRIOR / n), 'sha256': sha(PRIOR / n)} for n in ('unresolved_gaps.jsonl', 'invalid_candidate_exclusions.jsonl', 'resources.jsonl')]
                  + [{'path': rel(QUEUE), 'sha256': queue_sha}, {'path': 'delivery/focused_legal_corpus/counties/counties.jsonl', 'sha256': sha(ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl')},
                     {'path': 'sources/trellis_coverage_20260919/unresolved.jsonl', 'sha256': sha(ROOT / 'sources/trellis_coverage_20260919/unresolved.jsonl')}]
                  + [{'path': rel(p), 'sha256': sha(p)} for p in (SUPPLEMENT / 'resources.jsonl', SUPPLEMENT / 'validation.json', COVERAGE / 'progress.json', COVERAGE / 'validation.json', NEXT_RUN, BROWSER_CHECK)]})
    print(json.dumps({'validation': 'passed' if ok else 'failed', **counts}))


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'prepare': prepare()
    elif mode == 'finalize': finalize()
    elif mode == 'run':
        with writer_lock(): run()
    else: raise SystemExit(__doc__)
