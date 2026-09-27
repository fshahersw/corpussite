"""Read-only, hash-gated adapter for court reference facts and seals (sources/court_reference_flp_20260920).

Everything is keyed by CourtListener court id, the id the court registry already uses. for_court(id) returns extra facts and a
seal for a registry court; original(id) serves a seal only when its bytes still match the recorded SHA-256; resolve(text)
names the courts a caption string can mean, using the installed courts-db matcher. Fail-closed; never raises on bad input.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/court_reference_flp_20260920'
DB_NAME = 'court_reference.sqlite3'
ID_RE = re.compile(r'[A-Za-z0-9_.-]{1,40}')
_LOCK = threading.Lock()
_CACHE: dict = {}


def _state():
    try:
        signature = tuple((name, (DATA / name).stat().st_size, (DATA / name).stat().st_mtime_ns) for name in ('validation.json', DB_NAME))
    except OSError:
        return None, 'supplement files missing'
    with _LOCK:
        cached = _CACHE.get('state')
        if cached and cached['signature'] == signature:
            return cached['state'], cached['reason']
        state, reason = None, None
        try:
            gate = json.loads((DATA / 'validation.json').read_text(encoding='utf-8'))
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)}
            if gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'supplement not published (status/ready gate closed)'
            elif not expected.get(DB_NAME) or hashlib.sha256((DATA / DB_NAME).read_bytes()).hexdigest() != expected[DB_NAME]:
                reason = 'court reference does not match its recorded SHA-256'
            else:
                state = {'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}}
        except (OSError, ValueError, AttributeError):
            reason = 'validation.json missing or unreadable'
        _CACHE['state'] = {'signature': signature, 'state': state, 'reason': reason}
        return state, reason


def _connect():
    connection = sqlite3.connect(f"file:{(DATA / DB_NAME).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _span(entry):
    start, end = entry.get('start') or 'unknown', entry.get('end') or 'present'
    return [start, end, entry.get('notes') or '']


def for_court(court_id):
    """Extra facts, sections and a seal for one registry court, or None when nothing is recorded for that id."""
    ready, _reason = _state()
    if not ready or not isinstance(court_id, str) or not ID_RE.fullmatch(court_id):
        return None
    with _connect() as db:
        row = db.execute('SELECT * FROM courts WHERE id=?', (court_id,)).fetchone()
        seal = db.execute('SELECT * FROM seals WHERE court_id=?', (court_id,)).fetchone()
    if not row and not seal:
        return None
    facts, sections = [], []
    if row:
        for label, value in (('Cited as', row['citation_string']), ('Court level', row['level_label']), ('Court type', row['type_label']), ('Place', row['place']),
                             ('Parent court', row['parent_name'] or row['parent'])):
            if value:
                facts.append([label, value])
        spans = json.loads(row['dates'] or '[]')
        if spans and any(s.get('start') or s.get('notes') for s in spans):
            sections.append({'heading': 'When the court existed (publisher record)', 'header': ['From', 'To', 'Note'], 'rows': [_span(s) for s in spans]})
        forms = json.loads(row['name_forms'] or '[]')
        if forms:
            sections.append({'heading': 'Names this court appears under in case captions', 'text': '; '.join(forms[:25])})
        if row['notes'] and not spans:
            sections.append({'heading': 'Publisher note', 'text': row['notes']})
    if seal:
        origin = 'Hash-verified original, reduced to 256 px.' if seal['upstream_hash_verified'] else 'Project rendition of an SVG original; not hash-verified.'
        sections.insert(0, {'heading': 'Court seal', 'items': [{'title': seal['court_name'] or 'Court seal', 'subtitle': origin + ' A seal identifies the court; it is not an endorsement.',
                                                                 'links': [{'label': 'Court seal image', 'url': '/supplement-files/court_reference/' + court_id}]}]})
    return {'facts': facts, 'sections': sections, 'has_seal': bool(seal), 'seal_url': '/supplement-files/court_reference/' + court_id if seal else '',
            'source': 'Free Law Project court and seal databases, matched by court id'}


def seal_ids():
    ready, _reason = _state()
    if not ready:
        return set()
    with _connect() as db:
        return {r[0] for r in db.execute('SELECT court_id FROM seals')}


def original(file_id):
    """(bytes, mime, filename) for one seal; None unless the file still matches its recorded hash."""
    ready, _reason = _state()
    if not ready or not isinstance(file_id, str) or not ID_RE.fullmatch(file_id):
        return None
    with _connect() as db:
        row = db.execute('SELECT file, sha256, bytes FROM seals WHERE court_id=?', (file_id,)).fetchone()
    if not row:
        return None
    path = (DATA / 'seals' / row['file']).resolve()
    try:
        path.relative_to((DATA / 'seals').resolve())
        blob = path.read_bytes()
    except (OSError, ValueError):
        return None
    if len(blob) != row['bytes'] or hashlib.sha256(blob).hexdigest() != row['sha256'] or blob[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return blob, 'image/png', row['file']


def resolve(text):
    """Courts a caption string can mean. Uses the installed courts-db matcher; an absent package answers available:false."""
    ready, reason = _state()
    text = ' '.join(str(text or '').split())[:300]
    if not ready or not text:
        return {'available': bool(ready), 'reason': reason or 'nothing to resolve', 'query': text, 'results': []}
    basis = 'citation abbreviation, exactly as published'
    with _connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM courts WHERE citation_string<>'' AND citation_string=? COLLATE NOCASE LIMIT 12", (text,))]
    if not ids:
        try:
            from courts_db import find_court
            wants_bankruptcy = 'bankr' in text.lower()  # a caption that does not say bankruptcy should not match the district's bankruptcy court
            ids, basis = sorted(set(find_court(text, bankruptcy=wants_bankruptcy)))[:12], 'whole-name pattern match'
            if not ids:
                ids = sorted(set(find_court(text)))[:12]
            if not ids:
                ids, basis = sorted(set(find_court(text, bankruptcy=wants_bankruptcy, allow_partial_matches=True)))[:12], 'court name found inside the text (partial match)'
        except Exception:
            return {'available': False, 'reason': 'court name matcher is not installed', 'query': text, 'results': []}
    results = []
    with _connect() as db:
        for court_id in ids:
            row = db.execute('SELECT id, name, citation_string, place, system, level_label, in_registry, has_seal FROM courts WHERE id=?', (court_id,)).fetchone()
            results.append({'id': court_id, 'name': row['name'] if row else court_id, 'cited_as': row['citation_string'] if row else '', 'place': row['place'] if row else '',
                            'system': row['system'] if row else '', 'level': row['level_label'] if row else '', 'in_registry': bool(row and row['in_registry']),
                            'link': '#courts?q=' + court_id if row and row['in_registry'] else ''})
    return {'available': True, 'query': text, 'results': results, 'basis': basis, 'note': 'More than one result means the text is ambiguous (for example a district court and its bankruptcy court).'}


def listing(params=None):
    """Courts with a seal on file (generic contract, so the seal files can be served through the shared file route)."""
    ready, reason = _state()
    if not ready:
        return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': 50, 'filters': [], 'columns': [], 'results': []}
    with _connect() as db:
        rows = db.execute('SELECT court_id, court_name, upstream_hash_verified, in_registry FROM seals ORDER BY court_name LIMIT 400').fetchall()
    return {'available': True, 'total': len(rows), 'page': 1, 'limit': 400, 'qualification': ready['qualification'], 'filters': [],
            'columns': [{'key': 'court', 'label': 'Court'}, {'key': 'id', 'label': 'Court id'}, {'key': 'origin', 'label': 'Seal file'}],
            'results': [{'id': r['court_id'], 'title': r['court_name'], 'subtitle': '', 'cells': {'court': r['court_name'], 'id': r['court_id'], 'origin': 'Hash-verified original' if r['upstream_hash_verified'] else 'Project rendition'},
                         'badges': [], 'links': [{'label': 'Court seal image', 'url': '/supplement-files/court_reference/' + r['court_id']}]} for r in rows]}


def detail(court_id):
    extra = for_court(court_id)
    if not extra:
        return None
    ready, _reason = _state()
    return {'title': court_id, 'subtitle': extra['source'], 'facts': extra['facts'], 'sections': extra['sections'], 'links': [{'label': 'Open in the court registry', 'url': '#courts?q=' + court_id}],
            'qualification': ready['qualification'] if ready else ''}
