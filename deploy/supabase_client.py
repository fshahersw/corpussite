"""Bounded PostgREST/Storage client for local migration; no credentials in receipts."""
from __future__ import annotations
import json
import os
import time
import ssl
import requests
from private_supabase import secret

ORIGIN = 'https://xosqzzsnhxcyehcnirpa.supabase.co'
BUCKET = 'corpus-originals'
class CapacityError(RuntimeError):
    """Stop the entire transfer when the project rejects writes for capacity."""


class StatementTimeout(RuntimeError):
    """A confirmed PostgreSQL statement timeout; caller may split its atomic batch."""


class Client:
    def __init__(self):
        self.session = requests.Session()
        class SystemTrustAdapter(requests.adapters.HTTPAdapter):
            def init_poolmanager(self, *args, **kwargs):
                kwargs['ssl_context'] = ssl.create_default_context()
                return super().init_poolmanager(*args, **kwargs)
        self.session.mount('https://', SystemTrustAdapter())
        self.session.headers.update({'apikey': secret()})

    def call(self, method, path, *, data=None, headers=None, timeout=90):
        # Idempotent upserts only. No method ever follows an off-origin redirect with the key.
        for attempt in range(7):
            try:
                response = self.session.request(method, ORIGIN + path, data=data, headers=headers, timeout=timeout, allow_redirects=False)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 6:raise
                time.sleep(1 + attempt)
                continue
            if response.status_code>=400:
                reason=response.text.lower()
                if any(word in reason for word in ('read-only','read only','no space left','disk full')):
                    raise CapacityError('Supabase rejected writes because the database is read-only or out of disk. Transfer stopped; increase/verify capacity before resuming.')
                try:
                    error=response.json()
                except ValueError:
                    error={}
                if isinstance(error,dict) and error.get('code')=='57014' and 'statement timeout' in str(error.get('message','')).lower():
                    raise StatementTimeout(f'{method} {path.split("?")[0]}: PostgreSQL 57014: statement timeout')
            # 525 is a Cloudflare-to-origin handshake failure. Retry the same
            # bounded idempotent request; keep client TLS validation enabled.
            if response.status_code not in (408, 429, 500, 502, 503, 504, 520, 521, 522, 524, 525) or attempt == 6:
                break
            time.sleep(min(5, 1 + attempt * 2))
        if response.status_code >= 400 or 300 <= response.status_code < 400:
            try:
                body = response.json()
                message = body.get('message') or body.get('error') or body.get('code') or 'Request failed'
            except ValueError:
                message = 'Request failed'
            raise RuntimeError(f'{method} {path.split("?")[0]}: HTTP {response.status_code}: {str(message)[:250]}')
        return response

    def json(self, method, path, body=None, prefer=None):
        headers = {'Content-Type': 'application/json'}
        if prefer:
            headers['Prefer'] = prefer
        response = self.call(method, path, data=None if body is None else json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode(), headers=headers)
        return response.json() if response.content else None

    def upsert(self, table, rows):
        return self.json('POST', '/rest/v1/' + table, rows, 'resolution=merge-duplicates,return=minimal')


if __name__ == '__main__':
    data = Client().json('GET', '/rest/v1/corpus_datasets?select=id,ready,imported_records')
    print(json.dumps({'connection_verified': True, 'datasets': data}))
