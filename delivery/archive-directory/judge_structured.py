"""Evidence-based judge overlay keyed by judge entity id (read-only, fail-closed).

Data is produced offline by sources/judge_structured_20260919/build.py. This adapter performs no acquisition and no
identity resolution of its own: it serves the source-attributed blocks exactly as built (FJC structured appointments and a
dated FJC-reported status, the native-id chain nid -> jid -> CourtListener person, CourtListener blocks, a normalised role
with its basis, a deterministic evidence-coverage score). It never opens a caller-supplied path, exposes no local paths,
computes no rates and fails closed when the gate or any declared data file differs from its receipt.

Public functions
  overlay_for(entity_id) -> dict | None
  facets() -> {'status': [[value, n]], 'role': [[value, n]], 'appointing_president': [[value, n]]}
  ids_for(filters) -> set of entity ids; filters: status, role, president (all optional, AND-combined)
  summary() -> {'available': bool, 'reason' | counts/qualification/labels}
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/judge_structured_20260919'
REQUIRED = ('overlay.jsonl',)
REVERIFY_SECONDS = 300
HEX = re.compile(r'[a-f0-9]{64}')
FILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}')
ENTITY = re.compile(r'judge-entity-[0-9a-z]{1,40}')
ROLE_LABELS = {
    'district': 'U.S. district judge (Article III)',
    'circuit': 'U.S. circuit judge (Article III)',
    'magistrate': 'U.S. magistrate judge',
    'bankruptcy': 'U.S. bankruptcy judge',
    'state_trial': 'State trial-court judge',
    'state_appellate': 'State appellate judge or justice',
    'other': 'Other or not established from saved evidence',
}
STATUS_LABELS = {
    'active': 'FJC-reported active service',
    'senior': 'FJC-reported senior status',
    'terminated': 'FJC-reported service terminated',
    'deceased': 'FJC-reported deceased',
}
FILTERS = ('status', 'role', 'president')
QUALIFICATION = ('Source-attributed blocks shown side by side; nothing here merges people by name. Status is the Federal '
                 'Judicial Center export status as of its snapshot date, not independently verified service. Political '
                 'affiliation is shown only as recorded by the source with its stated basis. Completeness describes evidence '
                 'coverage in the saved corpus, not the judge.')

_LOCK = threading.Lock()
_CACHE = {}


class _State:
    __slots__ = ('signature', 'verified', 'gate', 'offsets', 'status', 'role', 'president', 'path')


def _stat(path):
    info = path.stat()
    return (path.name, info.st_size, info.st_mtime_ns)


def _gate(folder):
    raw = (folder / 'validation.json').read_bytes()
    gate = json.loads(raw.decode('utf-8-sig'))
    files = gate.get('data_files') if isinstance(gate, dict) else None
    if (not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True
            or not isinstance(files, list) or not files):
        raise ValueError('gate closed')
    names = []
    for entry in files:
        name, digest = entry.get('path'), entry.get('sha256')
        if (not isinstance(name, str) or not FILE.fullmatch(name) or name in names or name == 'validation.json'
                or not isinstance(digest, str) or not HEX.fullmatch(digest)):
            raise ValueError('bad data file entry')
        names.append(name)
    if not set(REQUIRED) <= set(names):
        raise ValueError('required data file not declared')
    return raw, gate


def _verify(folder, gate):
    state = _State()
    state.gate, state.path = gate, folder / 'overlay.jsonl'
    overlay = None
    for entry in gate['data_files']:
        payload = (folder / entry['path']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry['sha256']:
            raise ValueError('data file differs from receipt')
        if entry['path'] == 'overlay.jsonl':
            overlay = payload
    state.offsets, state.status, state.role, state.president = {}, {}, {}, {}
    position = 0
    for line in overlay.splitlines(keepends=True):
        start, position = position, position + len(line)
        if not line.strip():
            continue
        row = json.loads(line)
        key = row['entity_id']
        if not isinstance(key, str) or not ENTITY.fullmatch(key) or key in state.offsets:
            raise ValueError('overlay identity failure')
        state.offsets[key] = (start, len(line), hashlib.sha256(line).hexdigest())
        facets = row.get('facets') or {}
        if facets.get('status') in STATUS_LABELS:
            state.status.setdefault(facets['status'], set()).add(key)
        role = facets.get('role') if facets.get('role') in ROLE_LABELS else 'other'
        state.role.setdefault(role, set()).add(key)
        for name in facets.get('appointing_president') or []:
            if isinstance(name, str) and name:
                state.president.setdefault(name, set()).add(key)
    return state


def _load(folder=DATA):
    """Return the verified state or None. Re-hashes whenever the gate or a declared file changes, and periodically."""
    try:
        folder = Path(folder)
        raw_gate, gate = _gate(folder)
        signature = (hashlib.sha256(raw_gate).hexdigest(),) + tuple(_stat(folder / e['path']) for e in gate['data_files'])
        key = str(folder.resolve())
        with _LOCK:
            state = _CACHE.get(key)
            if state is not None and state.signature == signature and time.monotonic() - state.verified < REVERIFY_SECONDS:
                return state
            _CACHE.pop(key, None)
            state = _verify(folder, gate)
            state.signature, state.verified = signature, time.monotonic()
            _CACHE[key] = state
            return state
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        try:
            with _LOCK:
                _CACHE.pop(str(Path(folder).resolve()), None)
        except (OSError, TypeError, ValueError):
            pass
        return None


def overlay_for(entity_id, folder=DATA):
    """Overlay for a consolidated judge entity id, or None. The row is re-hashed against the verified index when read."""
    if not isinstance(entity_id, str) or not ENTITY.fullmatch(entity_id):
        return None
    state = _load(folder)
    if state is None or entity_id not in state.offsets:
        return None
    try:
        start, length, digest = state.offsets[entity_id]
        with state.path.open('rb') as handle:
            handle.seek(start)
            line = handle.read(length)
        if hashlib.sha256(line).hexdigest() != digest:
            return None
        row = json.loads(line)
    except (OSError, ValueError):
        return None
    row['role_label'] = ROLE_LABELS.get((row.get('role_normalized') or {}).get('value'), ROLE_LABELS['other'])
    row['qualification'] = QUALIFICATION
    return row


def _pairs(index):
    return [[value, len(members)] for value, members in sorted(index.items(), key=lambda item: (-len(item[1]), item[0]))]


def facets(folder=DATA):
    """Facet values with judge counts. A judge appointed by two presidents is counted once under each."""
    state = _load(folder)
    if state is None:
        return {'status': [], 'role': [], 'appointing_president': []}
    return {'status': _pairs(state.status), 'role': _pairs(state.role), 'appointing_president': _pairs(state.president)}


def ids_for(filters=None, folder=DATA):
    """Entity ids matching every given filter (status, role, president). Empty/absent filters are ignored; an unknown or
    malformed value matches nothing; a closed gate yields an empty set (use summary()['available'] to tell them apart)."""
    state = _load(folder)
    if state is None:
        return set()
    filters = filters if isinstance(filters, dict) else {}
    result = None
    for name, index in (('status', state.status), ('role', state.role), ('president', state.president)):
        value = filters.get(name)
        if value in (None, ''):
            continue
        members = index.get(value, set()) if isinstance(value, str) else set()
        result = set(members) if result is None else result & members
    return set(state.offsets) if result is None else result


def summary(folder=DATA):
    state = _load(folder)
    if state is None:
        return {'available': False, 'reason': 'judge_structured supplement missing or failed its hash gate'}
    gate = state.gate
    return {'available': True, 'counts': dict(gate.get('counts') or {}), 'validated_at': gate.get('validated_at'),
            'qualification': gate.get('qualification') or QUALIFICATION, 'license_ref': gate.get('license_ref') or '',
            'source_as_of': dict(gate.get('source_as_of') or {}), 'role_labels': dict(ROLE_LABELS),
            'status_labels': dict(STATUS_LABELS), 'filters': list(FILTERS)}
