"""Readiness of every published supplement, read from its validation envelope.

A uniform envelope (schema_version, data_files with sha256) is re-verified against the files on
disk; legacy envelopes are reported as such and never treated as verified. Nothing outside the
supplement's own folder is ever opened, and no absolute path is exposed.
"""
from __future__ import annotations

import hashlib
import threading
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'sources'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}')
HEX = re.compile(r'[a-f0-9]{64}')


# Digests are remembered across restarts, keyed by path + size + modification time, so a restart does not re-read
# gigabytes of unchanged data before the first page can open. Any change to a file changes its key and forces a fresh hash.
_MEMO_FILE = Path(__file__).resolve().parent / '.digest_cache.json'
_MEMO_LOCK = threading.Lock()
try:
    _MEMO = json.loads(_MEMO_FILE.read_text(encoding='utf-8'))
    if not isinstance(_MEMO, dict): _MEMO = {}
except (OSError, ValueError):
    _MEMO = {}


@lru_cache(maxsize=20000)
def _digest(path, size, mtime_ns):
    key = f'{path}|{size}|{mtime_ns}'
    known = _MEMO.get(key)
    if isinstance(known, str) and HEX.fullmatch(known):
        return known
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''): digest.update(chunk)
    value = digest.hexdigest()
    with _MEMO_LOCK:
        _MEMO[key] = value
        if size >= 1 << 20 or len(_MEMO) % 200 == 0:
            try:
                temporary = _MEMO_FILE.with_suffix('.tmp'); temporary.write_text(json.dumps(_MEMO), encoding='utf-8'); temporary.replace(_MEMO_FILE)
            except OSError:
                pass
    return value


def _confined(folder, value):
    """A data-file path must be relative and stay inside the supplement folder."""
    if not isinstance(value, str) or not value or value.startswith(('/', '\\')) or '..' in value.replace('\\', '/').split('/'): return None
    if ':' in value.split('/')[0].split('\\')[0]: return None
    candidate = (folder / value).resolve()
    if not candidate.is_relative_to(folder.resolve()) or not candidate.is_file(): return None
    return candidate


def _describe(folder):
    name = folder.name
    item = {'name': name, 'envelope': 'invalid', 'status': None, 'ready': False, 'validated_at': None, 'counts': {},
            'qualification': None, 'license_ref': None, 'data_files': [], 'issues': [], 'readme': (folder / 'README.md').is_file()}
    try:
        gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeError):
        item['issues'].append('validation.json is unreadable or not JSON')
        return item
    if not isinstance(gate, dict):
        item['issues'].append('validation.json is not an object')
        return item
    status = gate.get('status')
    if status is None and gate.get('passed') is True: status = 'passed'
    if status is None and gate.get('valid') is True: status = 'passed'
    item['status'] = status if isinstance(status, str) else None
    item['validated_at'] = next((gate.get(k) for k in ('validated_at', 'verified_at', 'generated_at', 'checked_at') if isinstance(gate.get(k), str)), None)
    item['qualification'] = gate.get('qualification') if isinstance(gate.get('qualification'), str) else None
    item['license_ref'] = gate.get('license_ref') if isinstance(gate.get('license_ref'), str) else None
    counts = gate.get('counts') if isinstance(gate.get('counts'), dict) else {}
    item['counts'] = {k: v for k, v in counts.items() if isinstance(k, str) and isinstance(v, (int, float, str)) and not isinstance(v, bool)}
    ready_flag = gate.get('ready')
    if ready_flag is None: ready_flag = gate.get('passed') is True or gate.get('valid') is True or item['status'] == 'passed'
    passed = item['status'] == 'passed' and ready_flag is True
    if gate.get('schema_version') is None or not isinstance(gate.get('data_files'), list):
        item['envelope'] = 'legacy'
        item['issues'].append('legacy envelope: hashes not re-verified here; the adapter for this supplement applies its own gate')
        item['ready'] = passed
        return item
    item['envelope'] = 'uniform'
    verified_all = True
    for entry in gate['data_files']:
        if not isinstance(entry, dict): verified_all = False; item['issues'].append('data_files entry is not an object'); continue
        rel = entry.get('path'); rows = entry.get('rows') if isinstance(entry.get('rows'), int) else None
        public = {'path': rel if isinstance(rel, str) and NAME.fullmatch(rel.replace('/', '_').replace('\\', '_').replace('..', '_')) else 'invalid path', 'rows': rows, 'verified': False}
        file = _confined(folder, rel)
        expected = entry.get('sha256')
        if file is None:
            verified_all = False; item['issues'].append(f'data file missing or outside the supplement folder: {public["path"]}')
        elif not (isinstance(expected, str) and HEX.fullmatch(expected)):
            verified_all = False; item['issues'].append(f'no sha256 recorded for {public["path"]}')
        else:
            stat = file.stat()
            try:
                actual = _digest(str(file), stat.st_size, stat.st_mtime_ns)
            except OSError:
                actual = None
            if actual == expected: public['verified'] = True
            else:
                verified_all = False; item['issues'].append(f'sha256 hash mismatch for {public["path"]}')
        item['data_files'].append(public)
    if not gate['data_files']:
        verified_all = False; item['issues'].append('uniform envelope lists no data files')
    item['ready'] = passed and verified_all
    if not passed: item['issues'].append('status is not passed/ready')
    return item


def status(root=ROOT):
    root = Path(root)
    items = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and (p / 'validation.json').is_file())
    except OSError:
        folders = []
    for folder in folders:
        if not NAME.fullmatch(folder.name): continue
        items.append(_describe(folder))
    return {'items': items, 'total': len(items), 'ready': sum(1 for i in items if i['ready']),
            'qualification': ('Readiness means the supplement\'s validation envelope passed and, for uniform envelopes, every listed data file '
                              'matches its recorded SHA-256 now. It says nothing about legal currency or completeness of the underlying sources.')}


def item(name, root=ROOT):
    if not isinstance(name, str) or not NAME.fullmatch(name): return None
    folder = Path(root) / name
    if not folder.is_dir() or not (folder / 'validation.json').is_file(): return None
    return _describe(folder)
