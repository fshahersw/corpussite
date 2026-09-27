"""Bounded JPML packet fetcher: frozen seeds, one host, robots + crawl-delay, no retries.

Run once:  python fetch.py            (network; www.jpml.uscourts.gov only)
Every request writes raw bytes (raw/), SHA-256 and a receipt line (receipts.jsonl).
A URL that already has a receipt is never requested again. Barriers go to gaps.json.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
HOST = 'www.jpml.uscourts.gov'
USER_AGENT = 'LegalCorpusResearch/1.0'
MIN_DELAY = 10.5          # robots.txt Crawl-delay: 10
MAX_URLS = 25
MAX_SECONDS = 600
CHALLENGE = re.compile(rb'(captcha|access denied|request unsuccessful|incapsula|cf-chl|attention required)', re.I)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


class _NoRedirectOffHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname != HOST or urlsplit(newurl).scheme != 'https':
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_name(url):
    name = unquote(urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]) or 'index'
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:120]


def classify(payload, expect, status):
    if status != 200: return 'http_' + str(status)
    if not payload: return 'empty_body'
    if expect == 'pdf': return 'ok' if payload[:5] == b'%PDF-' else 'not_pdf_soft_404_or_shell'
    if CHALLENGE.search(payload[:20000]): return 'challenge_or_block_shell'
    if expect == 'html' and b'<html' not in payload[:4000].lower(): return 'not_html'
    return 'ok'


def main():
    seeds = [json.loads(line) for line in (HERE / 'seeds.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(seeds) > MAX_URLS: sys.exit('seed list exceeds packet limit')
    if any(urlsplit(s['url']).hostname != HOST for s in seeds): sys.exit('off-host seed')
    receipts_path = HERE / 'receipts.jsonl'
    done = set()
    if receipts_path.exists():
        done = {json.loads(line)['url'] for line in receipts_path.read_text(encoding='utf-8').splitlines() if line.strip()}
    opener = urllib.request.build_opener(_NoRedirectOffHost)
    robots = urllib.robotparser.RobotFileParser()
    robots_loaded = False
    robots_file = HERE / 'raw' / 'robots.txt'
    if robots_file.exists():
        robots.parse(robots_file.read_text(encoding='utf-8', errors='replace').splitlines()); robots_loaded = True
    started = time.monotonic(); last = 0.0; gaps = []
    for seed in seeds:
        url = seed['url']
        if url in done: continue
        if time.monotonic() - started > MAX_SECONDS - 70:
            gaps.append({'url': url, 'reason': 'packet_time_limit_not_requested'}); continue
        if seed['kind'] != 'robots':
            if not robots_loaded:
                gaps.append({'url': url, 'reason': 'robots_unavailable_not_requested'}); continue
            if not robots.can_fetch(USER_AGENT, url):
                gaps.append({'url': url, 'reason': 'robots_disallow_not_requested'}); continue
        wait = MIN_DELAY - (time.monotonic() - last)
        if last and wait > 0: time.sleep(wait)
        requested_at = now(); last = time.monotonic()
        receipt = {'seq': seed['seq'], 'url': url, 'kind': seed['kind'], 'report': seed.get('report'), 'requested_at': requested_at,
                   'user_agent': USER_AGENT, 'method': 'GET'}
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
            with opener.open(request, timeout=90) as response:
                payload = response.read()
                receipt.update(status=response.status, final_url=response.geturl(), headers=dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            payload = b''
            receipt.update(status=error.code, final_url=error.geturl(), headers=dict(error.headers.items()) if error.headers else {})
        except Exception as error:  # no retries: record and move on
            payload = b''
            receipt.update(status=None, final_url=None, headers={}, error=type(error).__name__ + ': ' + str(error)[:200])
        last = time.monotonic()
        verdict = classify(payload, seed['expect'], receipt.get('status'))
        receipt.update(completed_at=now(), bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest() if payload else None,
                       verdict=verdict, raw_path=None)
        if payload and verdict == 'ok':
            name = 'robots.txt' if seed['kind'] == 'robots' else '%02d_%s' % (seed['seq'], safe_name(url))
            if seed['expect'] == 'html' and not name.endswith('.html'): name += '.html'
            (HERE / 'raw' / name).write_bytes(payload)
            receipt['raw_path'] = 'raw/' + name
            if seed['kind'] == 'robots':
                robots.parse(payload.decode('utf-8', errors='replace').splitlines()); robots_loaded = True
        else:
            gaps.append({'url': url, 'reason': verdict, 'status': receipt.get('status')})
        with receipts_path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        print(seed['seq'], receipt.get('status'), len(payload), verdict, flush=True)
    previous = []
    if (HERE / 'gaps.json').exists():
        previous = json.loads((HERE / 'gaps.json').read_text(encoding='utf-8')).get('fetch_gaps', [])
    (HERE / 'gaps.json').write_text(json.dumps({'fetch_gaps': previous + gaps}, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
