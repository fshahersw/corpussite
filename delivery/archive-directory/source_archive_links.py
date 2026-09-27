"""Validated, read-only links from source-directory references to records already saved in this archive.

The link manifest is produced offline by sources/source_archive_links_20260919/build.py.
This adapter performs no acquisition, never opens a request-supplied path and fails closed
when the manifest, its gate or a registered asset root is altered.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/source_archive_links_20260919'
DB = Path(__file__).resolve().parent / 'directory.sqlite3'
KINDS = ('directory_record', 'judge_entity', 'retained_capture')
HEX = re.compile(r'[a-f0-9]{64}')
IDENT = re.compile(r'[A-Za-z0-9_:.-]{1,160}')
PUBLICATION_LABELS = {
    'published_focused_release': 'Published capture in the validated release',
    'imported_collection': 'Imported original from a local collection',
    'federal_supplement': 'Federal supplement capture',
    'awaiting_publication': 'Saved download awaiting publication',
    'retained_capture_not_in_published_release': 'Retained capture; not part of the published release',
}
QUALIFICATIONS = {
    'directory_record': ('Existing saved record whose recorded source URL exactly matches this reference. '
                         'The saved date records collection, not legal currency; content review flags are retained.'),
    'judge_entity': ('Judge profile whose source observation was recorded from this page. '
                     'Current service is not independently verified by this link.'),
    'retained_capture': ('Successful capture retained in the shared archive index but not selected for the published release. '
                         'The capture date does not certify legal currency or completeness.'),
}
SUMMARY_QUALIFICATION = ('Links attach existing saved records to source references by exact URL match only. '
                         'They are not new downloads, do not establish current availability of the publisher page, '
                         'and unlinked references are not claimed absent from the corpus.')


def _public_url(value):
    if not isinstance(value, str): return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def _ident(value): return isinstance(value, str) and bool(IDENT.fullmatch(value))


def _confined(root, roots, value):
    """Resolve a manifest path only inside a registered asset root; never a caller path."""
    if not isinstance(value, str) or not value or value.startswith(('/', '\\')) or ':' in value.split('/')[0]: return None
    file = (root / value).resolve()
    if not any(file.is_relative_to(base) for base in roots) or not file.is_file(): return None
    return file


def _check_target(target, root, roots):
    kind = target.get('kind')
    if kind == 'directory_record':
        ok = (_ident(target.get('record_id')) and isinstance(target.get('title'), str) and isinstance(target.get('dataset'), str)
              and target.get('publication') in PUBLICATION_LABELS
              and all(target.get(k) is None or _ident(target.get(k)) for k in ('original_file_id', 'text_file_id')))
        return ok
    if kind == 'judge_entity':
        return _ident(target.get('record_id')) and isinstance(target.get('entity_id'), str) and isinstance(target.get('name'), str)
    if kind == 'retained_capture':
        if not (_ident(target.get('version_id')) and isinstance(target.get('raw_sha256'), str) and HEX.fullmatch(target['raw_sha256'])
                and target.get('publication') in PUBLICATION_LABELS and isinstance(target.get('title'), str)):
            return False
        raw = _confined(root, roots, target.get('raw_path'))
        if raw is None: return False
        text = None
        if target.get('text_path') is not None:
            if not (isinstance(target.get('text_sha256'), str) and HEX.fullmatch(target['text_sha256'])): return False
            text = _confined(root, roots, target['text_path'])
            if text is None: return False
        target['_raw'], target['_text'] = raw, text
        return True
    return False


@lru_cache(maxsize=4)
def _decode(folder, root, digest, payload, roots):
    root = Path(root)
    bases = tuple((root / base).resolve() for base in roots)
    rows = [json.loads(line) for line in payload.decode('utf-8-sig').splitlines() if line.strip()]
    result = {}
    for row in rows:
        url, targets = row.get('source_url'), row.get('targets')
        if not _public_url(url) or url in result or not isinstance(targets, list) or not targets or not _ident(row.get('source_id')):
            raise ValueError('Archive link manifest identity failure')
        for target in targets:
            if not isinstance(target, dict) or target.get('kind') not in KINDS or not _check_target(target, root, bases):
                raise ValueError('Archive link target failed validation')
        result[url] = row
    return result


def _gate(folder):
    return json.loads((Path(folder) / 'validation.json').read_text(encoding='utf-8'))


def load(folder=DATA, root=ROOT):
    """Fail closed on an altered manifest, gate or asset root. Returns {source_url: row}."""
    try:
        gate = _gate(folder)
        payload = (Path(folder) / 'links.jsonl').read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        roots = gate.get('asset_roots')
        if (gate.get('status') != 'passed' or gate.get('ready') is not True or digest != gate.get('links_sha256')
                or not isinstance(roots, list) or not all(isinstance(r, str) and r and not r.startswith(('/', '\\')) and '..' not in r for r in roots)):
            return {}
        return _decode(str(Path(folder)), str(Path(root)), digest, payload, tuple(roots))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return {}


def source_urls(records=None):
    if records is None: records = load()
    return set(records)


def link_counts(records=None):
    if records is None: records = load()
    return {url: len(row['targets']) for url, row in records.items()}


def summary(records=None, folder=DATA):
    if records is None: records = load(folder)
    counts = {kind: 0 for kind in KINDS}
    for row in records.values():
        for target in row['targets']: counts[target['kind']] += 1
    generated = None
    try: generated = _gate(folder).get('generated_at')
    except (OSError, ValueError, TypeError): pass
    return {'ready': bool(records), 'linked_references': len(records), 'directory_records': counts['directory_record'],
            'judge_entities': counts['judge_entity'], 'retained_captures': counts['retained_capture'],
            'generated_at': generated if records else None, 'qualification': SUMMARY_QUALIFICATION}


def _live_rows(db, ids):
    """Check every directory-backed target against the current directory database."""
    result = {}
    if not ids: return result
    con = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    try:
        for ident in ids:
            row = con.execute('SELECT source_url FROM records WHERE id=?', (ident,)).fetchone()
            if row: result[ident] = row[0]
    finally:
        con.close()
    return result


def _file_known(db, ident):
    if not ident: return False
    con = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    try: return bool(con.execute('SELECT 1 FROM files WHERE id=?', (ident,)).fetchone())
    finally: con.close()


def records_for(url, records=None, db=DB):
    """Public, path-free view of every still-valid link for one exact source URL."""
    if records is None: records = load()
    row = records.get(url) if isinstance(url, str) else None
    if not row: return []
    checked = [t for t in row['targets'] if t['kind'] in ('directory_record', 'judge_entity')]
    try:
        live = _live_rows(db, [t['record_id'] for t in checked])
    except (OSError, sqlite3.Error):
        return []
    result = []
    for target in row['targets']:
        kind = target['kind']
        if kind in ('directory_record', 'judge_entity') and live.get(target['record_id']) != url: continue
        if kind == 'directory_record':
            try:
                original = _file_known(db, target.get('original_file_id')); text = bool(target.get('usable_text'))
            except (OSError, sqlite3.Error):
                continue
            result.append({'kind': kind, 'record_id': target['record_id'], 'title': target['title'], 'dataset': target['dataset'],
                           'publication': target['publication'], 'publication_label': PUBLICATION_LABELS[target['publication']],
                           'state': target.get('state') or '', 'county': target.get('county') or '', 'record_kind': target.get('record_kind') or '',
                           'quality': target.get('quality') or '', 'captured_at': target.get('captured_at'), 'captured_at_basis': target.get('captured_at_basis'),
                           'usable_text': text, 'hash_verified': target.get('original_hash_verified'), 'review_flags': list(target.get('review_flags') or []),
                           'match_basis': target.get('match_basis') or 'exact_source_url', 'recorded_source_url': target.get('recorded_source_url') or url,
                           'original_url': '/files/' + target['original_file_id'] if original else '',
                           'text_url': '/api/text?id=' + target['record_id'] if text else '',
                           'extracted_url': '/files/' + target['text_file_id'] if target.get('text_file_id') and _file_known(db, target['text_file_id']) else '',
                           'qualification': QUALIFICATIONS[kind]})
        elif kind == 'judge_entity':
            result.append({'kind': kind, 'record_id': target['record_id'], 'entity_id': target['entity_id'], 'name': target['name'],
                           'profile_route': 'judge/' + target['record_id'], 'observation_count': target.get('observation_count') or 0,
                           'current_service_verified': bool(target.get('current_service_verified')), 'captured_at': target.get('captured_at'),
                           'qualification': QUALIFICATIONS[kind]})
        else:
            result.append({'kind': kind, 'version_id': target['version_id'], 'title': target['title'], 'collection': target.get('collection') or '',
                           'publication': target['publication'], 'publication_label': PUBLICATION_LABELS[target['publication']],
                           'retrieved_at': target.get('retrieved_at'), 'retrieval_time_basis': target.get('retrieval_time_basis'),
                           'content_type': target.get('content_type') or '', 'match_basis': target.get('match_basis') or row.get('match_basis'),
                           'final_url': target.get('final_url'),
                           'original_url': '/source-assets/archive:' + target['version_id'] + '/original',
                           'text_url': '/source-assets/archive:' + target['version_id'] + '/text' if target.get('_text') else '',
                           'qualification': QUALIFICATIONS[kind]})
    return result


def asset(token, records=None, root=ROOT):
    """Serve exactly the bytes verified here; a concurrent rebuild cannot swap them after the check."""
    if not isinstance(token, str): return None
    parts = token.split('/')
    if len(parts) != 2 or not parts[0].startswith('archive:') or parts[1] not in ('original', 'text'): return None
    version = parts[0][len('archive:'):]
    if records is None: records = load(root=root)
    target = next((t for row in records.values() for t in row['targets'] if t['kind'] == 'retained_capture' and t['version_id'] == version), None)
    if not target: return None
    file = target.get('_text') if parts[1] == 'text' else target.get('_raw')
    if not file: return None
    try: payload = file.read_bytes()
    except OSError: return None
    if hashlib.sha256(payload).hexdigest() != target['text_sha256' if parts[1] == 'text' else 'raw_sha256']: return None
    if parts[1] == 'text': return payload, 'text/plain; charset=utf-8', None
    if payload[:5] == b'%PDF-': return payload, 'application/pdf', None
    return payload, 'application/octet-stream', file.name
