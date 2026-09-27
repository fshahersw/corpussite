"""Read-only, hash-gated adapter for judge portraits (sources/judge_portraits_flp_20260920).

A portrait belongs to a CourtListener person id. for_person(id) serves the saved biographies; for_judge(entity_id) reaches the
same portrait through the judge-entity bridge (native ids only). original(portrait_id) returns bytes only while they still match
the recorded SHA-256. Fail-closed; never raises on bad input.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/judge_portraits_flp_20260920'
DB_NAME = 'portraits.sqlite3'
PORTRAIT_RE = re.compile(r'portrait-[0-9a-f]{20}')
_LOCK = threading.Lock()
_CACHE: dict = {}


def _state():
    try:
        signature = tuple((name, (DATA / name).stat().st_size, (DATA / name).stat().st_mtime_ns) for name in ('validation.json', DB_NAME))
    except OSError:
        return None
    with _LOCK:
        cached = _CACHE.get('state')
        if cached and cached['signature'] == signature:
            return cached['state']
        state = None
        try:
            gate = json.loads((DATA / 'validation.json').read_text(encoding='utf-8'))
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)}
            if gate.get('status') == 'passed' and gate.get('ready') is True and expected.get(DB_NAME) == hashlib.sha256((DATA / DB_NAME).read_bytes()).hexdigest():
                db = sqlite3.connect(f"file:{(DATA / DB_NAME).as_posix()}?mode=ro", uri=True)
                people = {row[0]: {'id': row[1], 'licence': row[2], 'source': row[3]} for row in db.execute('SELECT person_id, id, licence, source FROM portraits WHERE published=1 AND is_primary=1')}
                entities = {row[0]: row[1] for row in db.execute('SELECT entity_id, person_id FROM entity_links')}
                db.close()
                state = {'people': people, 'entities': entities, 'qualification': gate.get('qualification') or ''}
        except (OSError, ValueError, AttributeError, sqlite3.Error):
            state = None
        _CACHE['state'] = {'signature': signature, 'state': state}
        return state


def _view(entry):
    return {'photo_url': '/supplement-files/judge_portraits/' + entry['id'], 'photo_credit': 'Portrait: Free Law Project judicial portrait collection; recorded as a %s.' % entry['licence'].lower(),
            'photo_source': entry['source'] if str(entry['source']).startswith('http') else ''}


def for_person(person_id):
    state = _state()
    entry = state and state['people'].get(str(person_id or '').strip())
    return _view(entry) if entry else None


def for_judge(entity_id):
    state = _state()
    person = state and state['entities'].get(str(entity_id or '').strip())
    entry = person and state['people'].get(person)
    return _view(entry) if entry else None


def original(file_id):
    state = _state()
    if not state or not isinstance(file_id, str) or not PORTRAIT_RE.fullmatch(file_id):
        return None
    try:
        with sqlite3.connect(f"file:{(DATA / DB_NAME).as_posix()}?mode=ro", uri=True) as db:
            row = db.execute('SELECT file, sha256, bytes FROM portraits WHERE id=? AND published=1', (file_id,)).fetchone()
        if not row:
            return None
        path = (DATA / 'images' / row[0]).resolve()
        path.relative_to((DATA / 'images').resolve())
        blob = path.read_bytes()
    except (OSError, ValueError, sqlite3.Error):
        return None
    if len(blob) != row[2] or hashlib.sha256(blob).hexdigest() != row[1] or blob[:3] != bytes((0xFF, 0xD8, 0xFF)):
        return None
    return blob, 'image/jpeg', row[0]


def listing(params=None):
    """Counts only; portraits are shown on biographies and judge profiles, not as a collection of their own."""
    state = _state()
    if not state:
        return {'available': False, 'reason': 'portrait layer not published', 'total': 0, 'page': 1, 'limit': 50, 'filters': [], 'columns': [], 'results': []}
    return {'available': True, 'total': len(state['people']), 'page': 1, 'limit': 50, 'qualification': state['qualification'], 'filters': [], 'columns': [], 'results': [],
            'counts': {'people_with_a_portrait': len(state['people']), 'judge_profiles_with_a_portrait': len(state['entities'])}}


def detail(_portrait_id):
    return None
