"""Pinned, bounded-memory Supabase TUS uploads; no publication or remote deletes.

The caller holds the shared import-writer lock. Journals live only in ignored local
transfer storage. Never log upload URLs, response bodies, or authentication headers.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

import requests
from supabase_client import BUCKET, ORIGIN, Client, CapacityError

CHUNK_BYTES = 6 * 1024 * 1024
PROJECT = 'xosqzzsnhxcyehcnirpa'
STORAGE_ORIGIN = f'https://{PROJECT}.storage.supabase.co'
PREFIX = '/storage/v1/upload/resumable'
ENDPOINT = STORAGE_ORIGIN + PREFIX
VERSION = '1.0.0'
RETRIES = 4
TRANSIENT = {408, 429, 500, 502, 503, 504, 520, 521, 522, 524}


class TusError(RuntimeError):
    pass


def safe_location(value):
    """Validate before any credential-bearing request, including journal resumes."""
    if not isinstance(value, str) or not value or any(ord(c) < 33 for c in value):
        raise TusError('Invalid resumable upload location')
    absolute = urljoin(ENDPOINT, value)
    u = urlsplit(absolute)
    allowed = {f'{PROJECT}.supabase.co', f'{PROJECT}.storage.supabase.co'}
    try:
        valid_port = u.port in (None, 443)
    except ValueError:
        valid_port = False
    decoded = unquote(u.path)
    if (u.scheme != 'https' or u.hostname not in allowed or not valid_port
            or u.username is not None or u.password is not None or u.query or u.fragment
            or not u.path.startswith(PREFIX + '/') or not decoded.startswith(PREFIX + '/')
            or len(decoded) <= len(PREFIX) + 1 or '\\' in decoded
            or any(part in ('.', '..') for part in decoded.split('/'))
            or re.search(r'%(?![0-9a-fA-F]{2})', u.path)
            or any(ord(c) < 33 for c in decoded)):
        raise TusError('Unapproved resumable upload location')
    return absolute


def _stat(file):
    s = os.fstat(file.fileno())
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _verify(file, asset):
    before = _stat(file)
    if before[2] != asset['bytes']:
        raise TusError('Source artifact byte count changed')
    digest = hashlib.sha256()
    chunks = []
    while data := file.read(CHUNK_BYTES):
        digest.update(data)
        chunks.append(hashlib.sha256(data).digest())
    if _stat(file) != before or digest.hexdigest() != asset['sha256']:
        raise TusError('Source artifact hash changed')
    return before, chunks


def _metadata(pin):
    values = {'bucketName': pin['bucket'], 'objectName': pin['object_key'],
              'contentType': pin['mime'], 'cacheControl': '3600'}
    return ','.join(k + ' ' + base64.b64encode(v.encode()).decode() for k, v in values.items())


def _metadata_values(value):
    result = {}
    try:
        for field in value.split(','):
            key, encoded = field.strip().split(' ', 1)
            if key in result:
                raise ValueError('duplicate')
            result[key] = base64.b64decode(encoded, validate=True).decode()
    except (ValueError, UnicodeError):
        raise TusError('Invalid resumable upload metadata') from None
    return result


def _integer(headers, key):
    value = headers.get(key, '')
    if not re.fullmatch(r'[0-9]+', value):
        raise TusError('Invalid resumable upload offset or length')
    return int(value)


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf8') as out:
        json.dump(state, out, separators=(',', ':'))
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


class Transport:
    def __init__(self, client, sleep=time.sleep):
        self.session = client.session
        self.sleep = sleep

    def once(self, method, url, headers=None, data=None):
        if url != ENDPOINT:
            safe_location(url)
        try:
            response = self.session.request(method, url, data=data,
                headers={'Tus-Resumable': VERSION, 'x-upsert': 'true', **(headers or {})},
                timeout=(30, 180), allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout):
            return None
        if 300 <= response.status_code < 400:
            raise TusError('Resumable upload redirect rejected')
        if response.status_code >= 400:
            reason = response.text.lower()
            if any(word in reason for word in ('read-only', 'read only', 'no space left', 'disk full')):
                raise CapacityError('Supabase rejected a resumable upload for capacity; transfer stopped')
        return response

    def retry(self, method, url, headers=None):
        for attempt in range(RETRIES):
            response = self.once(method, url, headers)
            if response is not None and response.status_code not in TRANSIENT:
                return response
            if attempt + 1 < RETRIES:
                self.sleep(min(5, attempt + 1))
        raise TusError('Resumable upload request exhausted bounded retries')

    def head(self, state, pin):
        response = self.retry('HEAD', state['location'])
        if response.status_code in (404, 410):
            return None
        if response.status_code not in (200, 204):
            raise TusError(f'Resumable upload HEAD rejected: HTTP {response.status_code}')
        if response.headers.get('Tus-Resumable') != VERSION:
            raise TusError('Resumable upload protocol version mismatch')
        offset = _integer(response.headers, 'Upload-Offset')
        if _integer(response.headers, 'Upload-Length') != pin['bytes'] or offset > pin['bytes']:
            raise TusError('Resumable upload length or offset mismatch')
        metadata = _metadata_values(response.headers.get('Upload-Metadata', ''))
        expected = _metadata_values(_metadata(pin))
        if any(metadata.get(k) != v for k, v in expected.items()):
            raise TusError('Resumable upload target metadata mismatch')
        return offset


def upload_file(path, asset, journal_dir, *, client_factory=Client, sleep=time.sleep):
    """Return (hash, bytes, key) only after source integrity and terminal HEAD checks."""
    digest = asset.get('sha256')
    size = asset.get('bytes')
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise TusError('Missing source content hash')
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise TusError('Invalid source byte count')
    key = digest[:2] + '/' + digest
    pin = {'version': 1, 'origin': ORIGIN, 'bucket': BUCKET, 'object_key': key,
           'sha256': digest, 'bytes': size, 'mime': asset.get('mime') or 'application/octet-stream'}
    journal = Path(journal_dir) / (digest + '.json')
    state = json.loads(journal.read_text(encoding='utf8')) if journal.exists() else None
    if state is not None:
        if any(state.get(k) != v for k, v in pin.items()):
            raise TusError('Resumable upload journal target mismatch')
        state['location'] = safe_location(state.get('location'))
        lower, upper = state.get('offset'), state.get('pending_end', state.get('offset'))
        if (not isinstance(lower, int) or isinstance(lower, bool) or not isinstance(upper, int)
                or isinstance(upper, bool) or not 0 <= lower <= upper <= size):
            raise TusError('Invalid resumable upload journal offset')
    with Path(path).open('rb') as file:
        snapshot, chunks = _verify(file, asset)  # Stream the complete SHA before creating a credential-bearing client.
        transport = Transport(client_factory(), sleep)
        if state is not None:
            offset = transport.head(state, pin)
            if offset is None:  # Server-confirmed expiration; never guess from local elapsed time.
                state = None
            elif not lower <= offset <= upper:
                raise TusError('Resumable upload offset disagrees with journal')
        if state is None:
            response = transport.retry('POST', ENDPOINT, {'Upload-Length': str(size), 'Upload-Metadata': _metadata(pin)})
            if response.status_code != 201 or response.headers.get('Tus-Resumable') != VERSION:
                raise TusError(f'Resumable upload creation rejected: HTTP {response.status_code}')
            state = {**pin, 'location': safe_location(response.headers.get('Location')),
                     'offset': 0, 'created_at_unix': int(time.time())}
            _save(journal, state)
            offset = transport.head(state, pin)
            if offset != 0:
                raise TusError('New resumable upload has an unexpected offset')
        state['offset'] = offset
        state.pop('pending_end', None)
        _save(journal, state)
        attempts_without_progress = 0
        while offset < size:
            if _stat(file) != snapshot:
                raise TusError('Source artifact changed during upload')
            base = (offset // CHUNK_BYTES) * CHUNK_BYTES
            file.seek(base)
            block = file.read(min(CHUNK_BYTES, size - base))
            if hashlib.sha256(block).digest() != chunks[base // CHUNK_BYTES]:
                raise TusError('Source artifact chunk changed during upload')
            body = block[offset - base:]
            end = offset + len(body)
            state['pending_end'] = end
            _save(journal, state)  # Persist the possible commit range before the request.
            response = transport.once('PATCH', state['location'],
                {'Upload-Offset': str(offset), 'Content-Type': 'application/offset+octet-stream'}, body)
            if response is not None and response.status_code == 204:
                if response.headers.get('Tus-Resumable') != VERSION or _integer(response.headers, 'Upload-Offset') != end:
                    raise TusError('Resumable upload acknowledged an incorrect offset')
                next_offset = end
            elif response is None or response.status_code in TRANSIENT or response.status_code == 409:
                # Never replay an uncertain PATCH until HEAD establishes what was committed.
                next_offset = transport.head(state, pin)
                if next_offset is None:
                    raise TusError('Resumable upload expired during transfer; rerun to restart')
                if not offset <= next_offset <= end:
                    raise TusError('Resumable upload advanced outside the attempted chunk')
            else:
                raise TusError(f'Resumable upload PATCH rejected: HTTP {response.status_code}')
            attempts_without_progress = attempts_without_progress + 1 if next_offset == offset else 0
            state['offset'] = next_offset
            state.pop('pending_end', None)
            _save(journal, state)
            offset = next_offset
            if attempts_without_progress >= RETRIES:
                raise TusError('Resumable upload made no progress after bounded retries')
            if attempts_without_progress:
                sleep(min(5, attempts_without_progress))
        if _stat(file) != snapshot:
            raise TusError('Source artifact changed during upload')
        if transport.head(state, pin) != size:
            raise TusError('Resumable upload final offset is incomplete')
        state['complete'] = True
        _save(journal, state)
        return digest, size, key
