"""Loopback-only transport for reviewed browser DOM observations; no source fetching."""
import hashlib
import json
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/counties/trellis_browser_backfill_20260919'
PLAN = ROOT / 'reports/trellis_refocus_20260919/county_audit/next_10_observed_urls.jsonl'
PORT = 8774
TOKEN = secrets.token_hex(24)
ALLOWED = {json.loads(line)['url'] for line in PLAN.read_text(encoding='utf8').splitlines() if line.strip()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send(self, code, body):
        data = body.encode('utf8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Host') != f'127.0.0.1:{PORT}' or self.path != '/':
            return self.send(404, 'Not found')
        self.send(200, '<title>Save county observations</title><h1>Save reviewed county observations</h1>'
                  '<form method="post"><input type="hidden" name="token" value="'+TOKEN+'">'
                  '<label>Observations JSON<textarea name="observations" rows="12" cols="100"></textarea></label>'
                  '<button type="submit">Save local observations</button></form>')

    def do_POST(self):
        if (self.headers.get('Host') != f'127.0.0.1:{PORT}' or self.path != '/'
                or self.headers.get('Origin') != f'http://127.0.0.1:{PORT}'):
            return self.send(403, 'Loopback origin required')
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size < 300_000: raise ValueError('size')
            fields = parse_qs(self.rfile.read(size).decode('utf8'), strict_parsing=True)
            if fields.get('token') != [TOKEN]: raise ValueError('token')
            rows = json.loads(fields['observations'][0])
            if not isinstance(rows, list) or not 0 < len(rows) <= 10: raise ValueError('rows')
            seen = set()
            for row in rows:
                url = row.get('source_url')
                if url not in ALLOWED or url in seen: raise ValueError('unplanned/duplicate URL')
                seen.add(url)
                if not row.get('profile_heading') or not 3 <= len(row.get('dom_profile_headings', [])) <= 41:
                    raise ValueError('missing county profile')
            data = (json.dumps(rows, ensure_ascii=False, indent=2)+'\n').encode('utf8')
            OUT.mkdir(parents=True, exist_ok=True)
            with (OUT / 'observations.json').open('xb') as handle: handle.write(data)
            self.send(200, f'<h1>Saved {len(rows)} county observations</h1><p>SHA-256 {hashlib.sha256(data).hexdigest()}</p>')
        except (ValueError, KeyError, TypeError, FileExistsError):
            self.send(400, 'Invalid observation batch or already saved; existing observations preserved')


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
