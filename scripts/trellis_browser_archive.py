"""Loopback-only form for saving selected, already-rendered Trellis law content.

Chrome's content-export command is unavailable. The browser agent fills this
ordinary local form with a read-only DOM capture; this server never fetches a
source, reads browser credentials, or serves harvested HTML as executable code.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'corpus/trellis_browser_laws'
VERSION = '1.0.1'
MAX_BYTES = 4 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def law_url(value: str) -> str:
    p = urllib.parse.urlsplit(value)
    if (p.scheme != 'https' or p.netloc != 'trellis.law' or p.query or p.fragment
            or not (p.path == '/state-rules' or p.path.startswith('/state-rules/'))
            or '\\' in value or any(x in p.path for x in ('/../', '/./', '//'))):
        raise ValueError('Only exact observed HTTPS Trellis state-rule URLs are accepted')
    return value


def archive_capture(payload: dict, folder: Path = ARCHIVE) -> dict:
    allowed = {'url', 'title', 'heading', 'legal_text', 'legal_html', 'observed_law_links',
               'captured_at', 'content_kind', 'dom_selector', 'signed_in_observed'}
    if set(payload) - allowed:
        raise ValueError('Capture contains unsupported fields')
    url = law_url(payload['url'])
    kind = payload['content_kind']
    if kind not in ('law_text', 'law_directory'):
        raise ValueError('Unknown legal content kind')
    for key in ('title', 'heading', 'legal_text', 'legal_html', 'captured_at', 'dom_selector'):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f'Missing nonempty capture field: {key}')
    expected_selector = {'law_text': 'div.rule-header', 'law_directory': 'div.profileBillingContainer'}[kind]
    if payload['dom_selector'] != expected_selector:
        raise ValueError('The reviewed legal-content selector is required')
    if kind == 'law_text' and len(payload['legal_text'].strip()) < 30:
        raise ValueError('Full-text candidate is too short to accept automatically')
    parsed_time = datetime.datetime.fromisoformat(payload['captured_at'].replace('Z', '+00:00'))
    if parsed_time.tzinfo is None:
        raise ValueError('Capture time must include a time zone')
    links = payload.get('observed_law_links', [])
    if not isinstance(links, list) or len(links) > 10000:
        raise ValueError('Invalid observed link list')
    for link in links:
        if set(link) != {'url', 'text'} or not isinstance(link['text'], str):
            raise ValueError('Invalid observed link record')
        law_url(link['url'])
    if not isinstance(payload.get('signed_in_observed'), bool):
        raise ValueError('Record the observed sign-in state without account identity')
    serialized_content = json.dumps({k: v for k, v in payload.items() if k != 'captured_at'}, ensure_ascii=False, sort_keys=True).encode('utf-8')
    content_hash = digest(serialized_content)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / 'manifest.jsonl'
    previous = []
    if manifest.exists():
        previous = [json.loads(line) for line in manifest.read_text(encoding='utf-8').splitlines() if line.strip()]
    for record in previous:
        if record['source_url'] == url and record['content_sha256'] == content_hash:
            for path_key, hash_key in (('raw_path', 'raw_sha256'), ('text_path', 'text_sha256')):
                path = folder / record[path_key]
                path.resolve().relative_to(folder.resolve())
                if digest(path.read_bytes()) != record[hash_key]:
                    raise ValueError('Existing capture failed file/hash validation')
            return {'status': 'already_saved', 'source_url': url, 'source_records': len({r['source_url'] for r in previous})}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    text_data = payload['legal_text'].encode('utf-8')
    raw_hash, text_hash = digest(raw), digest(text_data)
    raw_relative = f'raw/{raw_hash[:2]}/{raw_hash}.json'
    text_relative = f'text/{text_hash[:2]}/{text_hash}.txt'
    for relative, data in ((raw_relative, raw), (text_relative, text_data)):
        p = folder / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            if p.read_bytes() != data:
                raise ValueError('Content-addressed artifact collision')
        else:
            with p.open('xb') as stream:
                stream.write(data)
    parts = urllib.parse.urlsplit(url).path.split('/')
    record = {
        'status': 'captured', 'capture_kind': 'browser_rendered_dom', 'source_url': url,
        'title': payload['title'], 'heading': payload['heading'],
        'state_code': parts[2].upper() if len(parts) > 2 else None,
        'category': 'state_rule', 'content_kind': kind,
        'captured_at': payload['captured_at'],
        'archived_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'archive_version': VERSION, 'dom_selector': payload['dom_selector'],
        'signed_in_observed': payload['signed_in_observed'],
        'content_sha256': content_hash, 'raw_path': raw_relative, 'raw_sha256': raw_hash,
        'raw_bytes': len(raw), 'text_path': text_relative, 'text_sha256': text_hash,
        'text_bytes': len(text_data), 'observed_law_links': links,
        'source_http_status': None, 'source_network_requests_by_archiver': 0,
        'cookies_or_credentials_exported': False,
        'limitations': [
            'A selected DOM representation from the signed-in browser, not original HTTP response bytes or a Firecrawl response.',
            'Only the selected legal-text or law-directory DOM container and law links are saved. Account navigation and related case/document previews are excluded.',
            'No current-law, official-certification, complete-jurisdiction or complete-source assertion is made.',
        ],
    }
    with manifest.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    source_count = len({r['source_url'] for r in previous} | {url})
    return {'status': 'saved', 'source_url': url, 'source_records': source_count,
            'raw_sha256': raw_hash, 'text_sha256': text_hash, 'text_bytes': len(text_data)}


def serve(port: int) -> None:
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    latest = {'status': 'ready'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != '/':
                self.send_error(404)
                return
            result = html.escape(json.dumps(latest, ensure_ascii=False))
            body = (f'<!doctype html><html><head><meta charset="utf-8"><title>Local legal capture archive</title></head>'
                    f'<body><h1>Local legal capture archive</h1><pre id="result">{result}</pre>'
                    f'<form method="post" action="/capture"><input type="hidden" name="token" value="{token}">'
                    '<label for="payload">Observed legal capture JSON</label><br>'
                    '<textarea id="payload" name="payload" rows="12" cols="90"></textarea><br>'
                    '<button type="submit">Save legal capture</button></form></body></html>').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            nonlocal latest
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if self.path != '/capture' or self.headers.get('Origin') != origin:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BYTES:
                    raise ValueError('Capture body is outside the supported size limit')
                form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8'), strict_parsing=True)
                if set(form) != {'token', 'payload'} or any(len(v) != 1 for v in form.values()) or not secrets.compare_digest(form['token'][0], token):
                    self.send_error(403)
                    return
                with lock:
                    latest = archive_capture(json.loads(form['payload'][0]))
            except (ValueError, KeyError, TypeError, OSError) as error:
                latest = {'status': 'error', 'message': str(error)}
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(json.dumps({'server_url': f'http://127.0.0.1:{server.server_port}/', 'pid': os.getpid(), 'archive': str(ARCHIVE)}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    serve(args.port)
