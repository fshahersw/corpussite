"""Bounded network packets for the agency safety supplement.

Usage: python fetch.py --packet 1   (openFDA bulk index + 9 bulk zips)
       python fetch.py --packet 2   (two www.fda.gov XLSX exports, 30 s apart)

Rules implemented here: exact URLs from seeds.jsonl only, one request at a time,
per-host delay (robots Crawl-delay for www.fda.gov = 30 s, otherwise 2 s), no
retries, no cross-host redirects, streamed to disk with SHA-256, one receipt per
request, 600 s budget per packet, stop at the first barrier on a host.
Seeds that already have a saved, hash-matching file are not requested again.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
RAW = HERE / 'raw'
USER_AGENT = 'LegalCorpusResearch/1.0'
ALLOWED_HOSTS = {'api.fda.gov', 'download.open.fda.gov', 'www.fda.gov'}
HOST_DELAY = {'www.fda.gov': 30.0, 'api.fda.gov': 2.0, 'download.open.fda.gov': 2.0}
PACKET_BUDGET_S = 600.0
CHUNK = 1 << 20


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname not in ALLOWED_HOSTS or urlsplit(newurl).scheme != 'https':
            raise urllib.error.HTTPError(req.full_url, code, 'redirect outside reviewed hosts refused: ' + newurl, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(SameHostRedirect)


def read_jsonl(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def file_name(seed):
    tail = urlsplit(seed['url']).path.rsplit('/', 1)[-1]
    if seed['kind'] == 'fda_xlsx':
        return seed['seed_id'] + '.xlsx'
    if seed['kind'] == 'robots':
        return seed['seed_id'] + '.txt'
    return tail


def robots_verdict(body, path):
    """Minimal longest-match evaluation for the '*' group and our own token."""
    groups, agents, rules, seen_rule = [], [], [], False
    for raw in body.decode('utf-8', 'replace').splitlines():
        line = raw.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = [part.strip() for part in line.split(':', 1)]
        key = key.lower()
        if key == 'user-agent':
            if seen_rule:
                groups.append((agents, rules)); agents, rules, seen_rule = [], [], False
            agents.append(value.lower())
        elif key in ('allow', 'disallow'):
            seen_rule = True
            rules.append((key, value))
    groups.append((agents, rules))
    chosen = [r for a, r in groups if any(x in ('*',) or x in USER_AGENT.lower() for x in a)]
    best = ('allow', '')
    for rules in chosen:
        for kind, value in rules:
            if value and path.startswith(value.rstrip('*')) and len(value) > len(best[1]):
                best = (kind, value)
    return best[0] == 'allow', best


def validate(seed, path):
    head = path.open('rb').read(8)
    if seed['kind'] == 'openfda_bulk_zip':
        if head[:4] != b'PK\x03\x04':
            return 'not a zip'
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not any(name.endswith('.json') for name in names):
                return 'zip without json member'
            if archive.testzip() is not None:
                return 'zip crc failure'
    elif seed['kind'] == 'fda_xlsx':
        if head[:4] != b'PK\x03\x04':
            return 'not an xlsx container (html or challenge shell?)'
        with zipfile.ZipFile(path) as archive:
            if 'xl/workbook.xml' not in archive.namelist():
                return 'zip without xl/workbook.xml'
    elif seed['kind'] == 'openfda_index':
        try:
            if 'results' not in json.loads(path.read_bytes().decode('utf-8')):
                return 'index without results'
        except ValueError:
            return 'index is not json'
    return None


def request(seed, started, receipts_path):
    receipt = {'seed_id': seed['seed_id'], 'packet': seed['packet'], 'url': seed['url'], 'method': 'GET',
               'user_agent': USER_AGENT, 'requested_at': now()}
    partial = RAW / ('_partial_' + seed['seed_id'])
    t0 = time.monotonic()
    body_error = None
    try:
        req = urllib.request.Request(seed['url'], headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
        with OPENER.open(req, timeout=60) as response, partial.open('wb') as out:
            receipt.update(status=response.status, final_url=response.geturl(), headers=dict(response.headers.items()))
            digest, size = hashlib.sha256(), 0
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if size > seed['max_bytes']:
                    body_error = 'aborted: larger than max_bytes cap'
                    break
                if time.monotonic() - started > PACKET_BUDGET_S - 5:
                    body_error = 'aborted: packet time budget'
                    break
                digest.update(block); out.write(block)
            receipt.update(bytes=size, sha256=digest.hexdigest())
    except urllib.error.HTTPError as error:
        body = error.read(200000) if error.fp else b''
        receipt.update(status=error.code, final_url=error.geturl(), headers=dict(error.headers.items()) if error.headers else {},
                       bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), error=str(error.reason)[:300])
        (RAW / '_rejected').mkdir(exist_ok=True)
        (RAW / '_rejected' / (receipt['sha256'][:16] + '.bin')).write_bytes(body)
        receipt['rejected_body'] = 'raw/_rejected/' + receipt['sha256'][:16] + '.bin'
        body_error = 'http ' + str(error.code)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        receipt.update(status=None, error=repr(error)[:300])
        body_error = 'network error'
    receipt['completed_at'] = now()
    receipt['elapsed_s'] = round(time.monotonic() - t0, 2)
    if body_error is None and receipt.get('status') != 200:
        body_error = 'http ' + str(receipt.get('status'))
    if body_error is None:
        body_error = validate(seed, partial)
    if body_error is None:
        target = RAW / file_name(seed)
        if target.exists():
            target = RAW / (receipt['sha256'][:16] + '__' + file_name(seed))
        partial.replace(target)
        receipt.update(outcome='saved', raw_path='raw/' + target.name)
    else:
        if partial.exists():
            partial.unlink()
        receipt.update(outcome='rejected: ' + body_error)
    with receipts_path.open('a', encoding='utf-8', newline='\n') as out:
        out.write(json.dumps(receipt, ensure_ascii=False) + '\n')
    return receipt


def already_saved(seed, receipts):
    for receipt in receipts:
        if receipt['seed_id'] == seed['seed_id'] and receipt.get('outcome') == 'saved':
            path = HERE / receipt['raw_path']
            if path.is_file() and sha256_file(path) == receipt['sha256']:
                return receipt
    return None


def run(packet):
    RAW.mkdir(exist_ok=True)
    receipts_path = HERE / 'receipts.jsonl'
    seeds = [s for s in read_jsonl(HERE / 'seeds.jsonl') if s['packet'] == packet]
    assert len(seeds) <= 100 and all(urlsplit(s['url']).hostname in ALLOWED_HOSTS for s in seeds)
    started, last_start, blocked, listed, log = time.monotonic(), {}, {}, None, []
    for seed in seeds:
        host = urlsplit(seed['url']).hostname
        prior = already_saved(seed, read_jsonl(receipts_path))
        if prior:
            log.append({'seed_id': seed['seed_id'], 'outcome': 'skipped: already saved with matching hash'})
            if seed['kind'] == 'openfda_index':
                listed = listed_files(HERE / prior['raw_path'])
            continue
        reason = None
        if host in blocked:
            reason = 'skipped: host stopped after barrier (' + blocked[host] + ')'
        elif time.monotonic() - started > PACKET_BUDGET_S - 10:
            reason = 'skipped: packet time budget'
        elif seed['kind'] == 'openfda_bulk_zip' and listed is not None and seed['url'] not in listed:
            reason = 'skipped: URL no longer listed in the current download.json'
        elif seed['kind'] == 'openfda_bulk_zip' and listed is None:
            reason = 'skipped: current download.json was not obtained'
        if reason:
            entry = {'seed_id': seed['seed_id'], 'packet': packet, 'url': seed['url'], 'requested_at': None,
                     'outcome': reason, 'logged_at': now()}
            with receipts_path.open('a', encoding='utf-8', newline='\n') as out:
                out.write(json.dumps(entry) + '\n')
            log.append(entry); continue
        wait = HOST_DELAY[host] - (time.monotonic() - last_start.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        last_start[host] = time.monotonic()
        receipt = request(seed, started, receipts_path)
        last_start[host] = max(last_start[host], time.monotonic() - 0.0)  # delay counts from request end
        log.append({k: receipt.get(k) for k in ('seed_id', 'status', 'bytes', 'sha256', 'elapsed_s', 'outcome')})
        if seed['kind'] == 'robots':
            if receipt.get('status') == 200 and receipt['outcome'] == 'saved':
                body = (HERE / receipt['raw_path']).read_bytes()
                for other in seeds:
                    if urlsplit(other['url']).hostname == host and other is not seed:
                        allowed, rule = robots_verdict(body, urlsplit(other['url']).path)
                        if not allowed:
                            blocked[host] = 'robots.txt disallow ' + rule[1]
            elif receipt.get('status') in (403, 404):
                rejected = HERE / receipt.get('rejected_body', '_none_')
                text = rejected.read_bytes()[:2000] if rejected.is_file() else b''
                server = (receipt.get('headers') or {}).get('Server', '') + (receipt.get('headers') or {}).get('server', '')
                # An S3 "no such key / access denied" XML answer means no robots file exists on the
                # bucket; an HTML "Access Denied" page from a WAF is a barrier.
                # CORRECTED 2026-09-19 after packet 1: a 403 on robots.txt is a barrier under this
                # project's convention, including the S3 AccessDenied XML case. The first run of
                # packet 1 wrongly treated that case as "no robots file" and fetched the zips; see
                # gaps.json "deviations". Do not restore that exception without the user's approval.
                if receipt.get('status') == 403:
                    blocked[host] = 'robots.txt refused with 403 (server ' + server + ', body ' + text[:80].decode('ascii', 'replace') + ')'
                elif receipt.get('status') == 404:
                    log.append({'note': 'robots.txt 404; no robots rules'})
                else:
                    blocked[host] = 'robots.txt refused with ' + str(receipt.get('status'))
            else:
                blocked[host] = 'robots.txt not obtained'
        elif receipt['outcome'] != 'saved':
            if receipt.get('status') in (401, 403, 429) or receipt.get('status') is None:
                blocked[host] = receipt['outcome']
        elif seed['kind'] == 'openfda_index':
            listed = listed_files(HERE / receipt['raw_path'])
    print(json.dumps({'packet': packet, 'elapsed_s': round(time.monotonic() - started, 1), 'log': log, 'blocked': blocked}, indent=1))


def listed_files(path):
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'file' and isinstance(value, str):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(json.loads(path.read_bytes().decode('utf-8')))
    return found


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--packet', type=int, required=True, choices=(1, 2))
    run(parser.parse_args().packet)
