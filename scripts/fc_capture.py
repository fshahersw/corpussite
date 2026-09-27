"""Small Firecrawl capture tool for bounded, reviewed URL lists (2026-09-19).

Uses the project's private DPAPI credential in memory only (never printed, never written). Basic public fetching only:
no login, no CAPTCHA solving, no stealth proxy, robots.txt respected through the shared crawler policy, one request per
host at a time, no client retries. Every response is saved with a receipt; failures are recorded, never retried here.

    python scripts/fc_capture.py credits
    python scripts/fc_capture.py scrape --out <dir> --urls <file with one URL per line> [--max 200]
    python scripts/fc_capture.py search --out <dir> --query "..." [--limit 8]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'pipeline'), str(ROOT / 'scripts')]
import corpus_crawler as crawler  # noqa: E402
from judge_firecrawl_private import CREDENTIAL, private_key  # noqa: E402

API = 'https://api.firecrawl.dev/v2/'


def now():
    return datetime.now(timezone.utc).isoformat()


def call(key, method, path, payload=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return response.status, response.read(40 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1024 * 1024)


def credits(key):
    status, raw = call(key, 'GET', 'team/credit-usage')
    body = json.loads(raw or b'{}')
    return int(body.get('data', {}).get('remainingCredits')) if status == 200 else None


def robots_allowed(url, cache, lock):
    parts = urllib.parse.urlsplit(url)
    origin = parts.scheme + '://' + parts.netloc
    with lock:
        rules = cache.get(origin)
    if rules is None:
        rules = urllib.robotparser.RobotFileParser()
        try:
            request = urllib.request.Request(origin + '/robots.txt', headers={'User-Agent': 'LegalCorpusResearch/1.0'})
            with urllib.request.urlopen(request, timeout=20) as response:
                rules.parse(response.read(512 * 1024).decode('utf-8', 'replace').splitlines())
        except urllib.error.HTTPError as error:
            rules.parse([] if error.code in (404, 410) else ['User-agent: *', 'Disallow: /'] if error.code in (401, 403) else [])
        except Exception:
            rules.parse([])
        with lock:
            cache[origin] = rules
    return rules.can_fetch('LegalCorpusResearch/1.0', url)


def scrape_all(key, urls, out, limit):
    out.mkdir(parents=True, exist_ok=True)
    receipts = out / 'receipts.jsonl'
    done = set()
    if receipts.exists():
        for line in receipts.read_text(encoding='utf-8').splitlines():
            if line.strip():
                done.add(json.loads(line)['url'])
    todo = [u for u in dict.fromkeys(u.strip() for u in urls if u.strip()) if u not in done and crawler.canonical_url(u)][:limit]
    cache, lock, write_lock = {}, threading.Lock(), threading.Lock()
    host_locks = {}
    before = credits(key)

    def one(url):
        host = urllib.parse.urlsplit(url).netloc.lower()
        with lock:
            host_lock = host_locks.setdefault(host, threading.Lock())
        with host_lock:
            record = {'url': url, 'requested_at': now()}
            if not robots_allowed(url, cache, lock):
                record.update(status='robots_disallowed')
            else:
                payload = {'url': url, 'formats': ['markdown', 'links'], 'onlyMainContent': True, 'maxAge': 0, 'timeout': 60000, 'proxy': 'basic', 'parsers': ['pdf']}
                status, raw = call(key, 'POST', 'scrape', payload)
                raw = raw.replace(key.encode(), b'[REDACTED]')
                name = hashlib.sha256(url.encode()).hexdigest()[:24] + '.json'
                (out / name).write_bytes(raw)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                meta = (body.get('data') or {}).get('metadata') or {}
                ok = status == 200 and body.get('success') is True and meta.get('statusCode') == 200
                record.update(status='captured' if ok else 'failed', api_status=status, source_status=meta.get('statusCode'), file=name, sha256=hashlib.sha256(raw).hexdigest(),
                              title=meta.get('title'), final_url=meta.get('url'), markdown_chars=len((body.get('data') or {}).get('markdown') or ''),
                              links=len((body.get('data') or {}).get('links') or []), error=None if ok else str(body.get('error') or '')[:300])
                time.sleep(1.0)
            with write_lock:
                with open(receipts, 'a', encoding='utf-8', newline='\n') as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            return record

    with ThreadPoolExecutor(max_workers=5) as pool:
        records = list(pool.map(one, todo))
    after = credits(key)
    summary = {'requested': len(todo), 'skipped_already_done': len(done), 'captured': sum(r['status'] == 'captured' for r in records), 'failed': sum(r['status'] == 'failed' for r in records),
               'robots_disallowed': sum(r['status'] == 'robots_disallowed' for r in records), 'credits_before': before, 'credits_after': after}
    print(json.dumps(summary))


def search(key, query, out, limit):
    out.mkdir(parents=True, exist_ok=True)
    status, raw = call(key, 'POST', 'search', {'query': query, 'limit': max(1, min(limit, 10))})
    name = 'search_' + hashlib.sha256(query.encode()).hexdigest()[:16] + '.json'
    (out / name).write_bytes(raw.replace(key.encode(), b'[REDACTED]'))
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    data = body.get('data') or {}
    results = data.get('web') if isinstance(data, dict) else data
    print(json.dumps({'api_status': status, 'file': name, 'results': [{'url': r.get('url'), 'title': r.get('title'), 'description': (r.get('description') or '')[:200]} for r in (results or [])]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['credits', 'scrape', 'search'])
    parser.add_argument('--out', type=Path)
    parser.add_argument('--urls', type=Path)
    parser.add_argument('--query')
    parser.add_argument('--max', type=int, default=200)
    parser.add_argument('--limit', type=int, default=8)
    args = parser.parse_args()
    key = private_key(CREDENTIAL)
    if args.action == 'credits':
        print(json.dumps({'remaining_credits': credits(key)}))
    elif args.action == 'scrape':
        scrape_all(key, args.urls.read_text(encoding='utf-8').splitlines(), args.out, args.max)
    else:
        search(key, args.query, args.out, args.limit)


if __name__ == '__main__':
    main()
