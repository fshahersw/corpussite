"""Validated, exact-URL attachments for the imported public-law directory.

Capture batches are pluggable. The built-in pilot batch (``DATA``) is always a
candidate; further batches come from a small hash-gated registry file
(``REGISTRY`` + ``capture_registry.validation.json``). Every batch is gated,
confined to its own folder and verified on its own, so one bad batch never
removes or contaminates another. Ids are unique across batches.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/public_law_acquisition_20260919'
REGISTRY = ROOT / 'sources/gap_fill_acquisition_20260919/capture_registry.json'

QUALIFICATION = 'Saved representation of this exact listed URL; linked documents may remain unsaved. Capture date does not certify legal currency.'
TEMPORAL_FIELDS = ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to')
FILTER_FIELDS = ('state', 'gap_type', 'law_family', 'doc_kind', 'batch')
MAX_LIMIT = 200
_SHA = re.compile(r'[a-f0-9]{64}')
_NAME = re.compile(r'[A-Za-z0-9_.-]{1,80}')
_LOCK = threading.Lock()
_CACHE = {}


def _public_url(value):
    if not isinstance(value, str): return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _artifact(row, field, folder):
    """Never serve a path supplied by a request or outside the registered batch."""
    value, digest = row.get(field + '_path'), row.get(field + '_sha256')
    if not value or not isinstance(digest, str) or not _SHA.fullmatch(digest): return None
    file = (ROOT / value).resolve()
    if not file.is_relative_to(folder.resolve()) or not file.is_file(): return None
    if _digest(file.read_bytes()) != digest: return None
    return file


def _gate(folder, watched=None):
    """Return the gated resources.jsonl bytes, or None. Supports the pilot gate
    (resources_sha256) and the uniform envelope (ready + every data file hash)."""
    gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    if not isinstance(gate, dict) or gate.get('status') != 'passed': return None
    payload = (folder / 'resources.jsonl').read_bytes()
    if 'data_files' not in gate:
        return payload if _digest(payload) == gate.get('resources_sha256') else None
    files = gate['data_files']
    if gate.get('ready') is not True or not isinstance(files, list): return None
    pinned, base = False, folder.resolve()
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str): return None
        file = (folder / entry['path']).resolve()
        if not file.is_relative_to(base) or not file.is_file(): return None
        is_manifest = file == (base / 'resources.jsonl')
        if not is_manifest and watched is not None: watched.append(file)
        if _digest(payload if is_manifest else file.read_bytes()) != entry['sha256']: return None
        pinned = pinned or is_manifest
    return payload if pinned else None


def _read_batch(folder, watched=None):
    """(records, rejection reason). Fail closed on altered receipts; validate all
    originals before marking saved."""
    try:
        payload = _gate(folder, watched)
        if payload is None: return {}, 'validation_gate_failed'
        rows = [json.loads(line) for line in payload.decode('utf-8-sig').splitlines() if line.strip()]
        if len({r['id'] for r in rows}) != len(rows): return {}, 'duplicate_id_in_batch'
        result = {}
        for row in rows:
            if not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,120}', row['id']): return {}, 'invalid_id'
            if not _public_url(row.get('source_url')): return {}, 'invalid_source_url'
            raw = _artifact(row, 'raw', folder)
            if not raw: continue
            text = _artifact(row, 'text', folder)
            if text and not text.read_text(encoding='utf-8-sig').strip(): text = None
            if watched is not None: watched.extend(x for x in (raw, text) if x)
            result[row['id']] = {**row, '_raw': raw, '_text': text}
        return result, None if result else 'no_verified_originals'
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return {}, 'unreadable_or_malformed'


def _registered():
    """Registry entries as (name, folder, id_prefix). Any defect rejects the whole
    registry; the built-in batch is unaffected."""
    try:
        payload = REGISTRY.read_bytes()
        gate = json.loads((REGISTRY.parent / (REGISTRY.stem + '.validation.json')).read_text(encoding='utf-8'))
        if gate.get('status') != 'passed' or gate.get('ready') is not True: return [], 'registry_gate_failed'
        pins = [x for x in gate.get('data_files') or [] if isinstance(x, dict) and x.get('path') == REGISTRY.name]
        if len(pins) != 1 or pins[0].get('sha256') != _digest(payload): return [], 'registry_hash_mismatch'
        listed = json.loads(payload.decode('utf-8-sig'))['batches']
        sources, used, entries = (ROOT / 'sources').resolve(), [DATA.resolve()], []
        names = {DATA.name}
        for item in listed:
            name, value, prefix = item['name'], item['folder'], item.get('id_prefix')
            if set(item) - {'name', 'folder', 'id_prefix', 'note'}: return [], 'registry_entry_invalid'
            if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names: return [], 'registry_entry_invalid'
            if prefix is not None and (not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,60}', prefix)): return [], 'registry_entry_invalid'
            parts = PurePosixPath(value).parts if isinstance(value, str) and '\\' not in value and ':' not in value else ()
            if not parts or value.startswith('/') or any(part in ('..', '.') for part in parts): return [], 'registry_folder_invalid'
            folder = (ROOT / value).resolve()
            if folder == sources or not folder.is_relative_to(sources) or not folder.is_dir(): return [], 'registry_folder_invalid'
            if any(folder == other or folder.is_relative_to(other) or other.is_relative_to(folder) for other in used): return [], 'registry_folder_overlap'
            used.append(folder); names.add(name); entries.append((name, folder, prefix))
        return entries, None
    except FileNotFoundError:
        return [], 'registry_absent'
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, AttributeError):
        return [], 'registry_unreadable_or_malformed'


def _stat(file):
    try:
        info = file.stat()
        return info.st_size, info.st_mtime_ns
    except OSError:
        return None


def _control():
    """Hash of the small control files; large artifacts are tracked by stat and
    always re-hashed before bytes are served."""
    entries, _ = _registered()
    digest = hashlib.sha256(repr((str(ROOT), str(DATA), str(REGISTRY))).encode())
    files = [REGISTRY, REGISTRY.parent / (REGISTRY.stem + '.validation.json')]
    for folder in [DATA, *[entry[1] for entry in entries]]:
        files += [folder / 'validation.json', folder / 'resources.jsonl']
    for file in files:
        try: digest.update(b'\0' + _digest(file.read_bytes()).encode())
        except OSError: digest.update(b'\0missing')
    return digest.hexdigest()


def _build():
    entries, registry_reason = _registered()
    merged, statuses, watched = {}, [], []
    for index, (name, folder, prefix) in enumerate([(DATA.name, DATA, None), *entries]):
        rows, reason = _read_batch(folder, watched)
        if rows and prefix and any(not ident.startswith(prefix) for ident in rows): rows, reason = {}, 'id_prefix_mismatch'
        if rows and any(ident in merged for ident in rows): rows, reason = {}, 'duplicate_id_across_batches'
        for ident, row in rows.items(): merged[ident] = {**row, '_batch': name}
        statuses.append({'name': name, 'origin': 'built_in' if index == 0 else 'registry', 'status': 'loaded' if rows else 'rejected',
                         'reason': reason if not rows else None, 'resources': len(rows), 'readable': sum(1 for row in rows.values() if row['_text']),
                         'id_prefix': prefix})
    return merged, statuses, registry_reason, watched


def _state():
    control = _control()
    with _LOCK:
        if _CACHE.get('control') == control and all(_stat(file) == stat for file, stat in _CACHE['watched']):
            return _CACHE
        merged, statuses, registry_reason, watched = _build()
        _CACHE.clear()
        _CACHE.update(control=control, records=merged, statuses=statuses, registry=registry_reason,
                      watched=[(file, _stat(file)) for file in dict.fromkeys(watched)])
        return _CACHE


def load(folder=None):
    """One explicit folder, or (default) every registered batch merged."""
    if folder is not None: return _read_batch(folder)[0]
    return _state()['records']


def batches():
    """Path-free load report for every candidate batch, in registry order."""
    state = _state()
    return [dict(item, registry_status=state['registry'] or 'passed') for item in state['statuses']]


def source_urls(records=None):
    if records is None: records = load()
    return {row['source_url'] for row in records.values()}


def _temporal(row):
    given = row.get('temporal') if isinstance(row.get('temporal'), dict) else {}
    result = {}
    for field in TEMPORAL_FIELDS:
        value = given.get(field, row.get(field) if field in ('captured_at', 'source_as_of') else None)
        basis = given.get(field + '_basis')
        if value is not None and basis is None and field == 'captured_at': basis = 'Collector HTTP receipt timestamp'
        result[field], result[field + '_basis'] = value, basis if value is not None else None
    return result


def _public(row):
    has_text = bool(row.get('_text'))
    return {
        'id': row['id'], 'title': row.get('title'), 'type': row.get('kind'),
        'captured_at': row.get('captured_at'), 'source_as_of': row.get('source_as_of'),
        'original_url': '/source-assets/' + row['id'] + '/original',
        'text_url': '/source-assets/' + row['id'] + '/text' if has_text else None,
        'publisher_url': row['source_url'], 'final_url': row.get('final_url'),
        'mime_type': row.get('mime_type'), 'raw_sha256': row['raw_sha256'],
        'text_sha256': row.get('text_sha256') if has_text else None,
        'batch': row.get('_batch'), 'state': row.get('state'), 'gap_type': row.get('gap_type'),
        'law_family': row.get('law_family'), 'doc_kind': row.get('doc_kind'), 'packet': row.get('packet'),
        'source_directory_id': row.get('source_directory_id'), 'raw_bytes': row.get('raw_bytes'),
        'text_characters': row.get('text_characters') if has_text else None,
        'reading_method': row.get('reading_method') if has_text else None,
        'capture_kind': row.get('capture_kind'), 'http_status': row.get('http_status'),
        'http_last_modified': row.get('http_last_modified'), 'temporal': _temporal(row),
        'content_scope_note': row.get('content_scope_note'),
        'qualification': QUALIFICATION,
    }


def attachments(url, records=None):
    if records is None: records = load()
    return [_public(row) for row in records.values() if row['source_url'] == url]


def _number(value, default, low, high):
    try: return max(low, min(high, int(value)))
    except (TypeError, ValueError): return default


def listing(records=None, state=None, gap_type=None, law_family=None, doc_kind=None, batch=None, q=None, limit=50, offset=0):
    """Filtered, paginated, path-free view of saved captures. Filters are exact
    (state is case-insensitive); q is a substring of title or listed URL."""
    if records is None: records = load()
    wanted = {'state': state.upper() if isinstance(state, str) and state else None, 'gap_type': gap_type or None,
              'law_family': law_family or None, 'doc_kind': doc_kind or None, 'batch': batch or None}
    needle = q.strip().lower() if isinstance(q, str) and q.strip() else None
    items = []
    for row in records.values():
        item = _public(row)
        if any(value is not None and item[field] != value for field, value in wanted.items()): continue
        if needle and needle not in (item['title'] or '').lower() and needle not in item['publisher_url'].lower(): continue
        items.append(item)
    items.sort(key=lambda item: (item['state'] or '~', item['publisher_url']))
    limit, offset = _number(limit, 50, 1, MAX_LIMIT), _number(offset, 0, 0, 10 ** 9)
    facets = {field: dict(sorted(Counter(item[field] for item in items if item[field]).items())) for field in FILTER_FIELDS}
    return {'total': len(items), 'limit': limit, 'offset': offset, 'items': items[offset:offset + limit], 'facets': facets,
            'qualification': QUALIFICATION}


def asset(token):
    parts = token.split('/')
    if len(parts) != 2 or parts[1] not in ('original', 'text'): return None
    row = load().get(parts[0])
    if not row: return None
    field = 'text' if parts[1] == 'text' else 'raw'
    file = row['_' + field]
    if not file: return None
    try: payload = file.read_bytes()
    except OSError: return None
    # Return the exact verified bytes. A concurrent rebuild cannot replace them
    # between this check and the HTTP response.
    if hashlib.sha256(payload).hexdigest() != row[field + '_sha256']: return None
    if field == 'text': return payload, 'text/plain; charset=utf-8', None
    # Originals are immutable. HTML/XML/JS cannot execute in the app's origin.
    if payload[:5] == b'%PDF-': return payload, 'application/pdf', None
    return payload, 'application/octet-stream', file.name
