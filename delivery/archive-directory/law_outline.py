"""Read-only, hash-gated table of contents for the saved law snapshot (sources/state_code_outline_20260919).

The outline holds headings and ranges of row numbers in the law catalog; provision text is read from the catalog itself.
Because ranges are row numbers, the adapter also requires the catalog to be the very file that was scanned (same size and
modification time) and closes otherwise. Never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

import bulk_laws

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/state_code_outline_20260919'
DB_NAME = 'outline.sqlite3'
KIND_LABELS = {'statutes': 'Statutes & codes', 'regulations': 'Regulations', 'court_rules': 'Court rules', 'constitutions': 'Constitution',
               'guidance': 'Agency guidance', 'executive_order': 'Executive orders', 'proclamation': 'Proclamations', 'memorandum': 'Presidential memoranda',
               'presidential_document': 'Presidential documents', 'ruling': 'Agency rulings', 'guideline': 'Sentencing guidelines', 'treaty': 'Tax treaties',
               'faq': 'Agency questions & answers', 'enforcement_action': 'Enforcement actions', 'administrative_guidance': 'Administrative guidance',
               'irs_notice': 'IRS notices', 'irs_rev_proc': 'IRS revenue procedures', 'irs_rev_rul': 'IRS revenue rulings', 'irs_announcement': 'IRS announcements'}
KIND_ORDER = ['statutes', 'constitutions', 'court_rules', 'regulations', 'guidance']
_LOCK = threading.Lock()
_CACHE: dict = {}


def _state():
    try:
        signature = tuple((name, (DATA / name).stat().st_size, (DATA / name).stat().st_mtime_ns) for name in ('validation.json', DB_NAME))
        catalog = bulk_laws.DB.stat()
    except OSError:
        return None, 'outline files missing'
    signature += (catalog.st_size, catalog.st_mtime_ns)
    with _LOCK:
        cached = _CACHE.get('state')
        if cached and cached['signature'] == signature:
            return cached['state'], cached['reason']
        state, reason = None, None
        try:
            gate = json.loads((DATA / 'validation.json').read_text(encoding='utf-8'))
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)}
            scanned = (gate.get('inputs') or [{}])[0]
            if gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'outline not published (status/ready gate closed)'
            elif not expected.get(DB_NAME) or hashlib.sha256((DATA / DB_NAME).read_bytes()).hexdigest() != expected[DB_NAME]:
                reason = 'outline does not match its recorded SHA-256'
            elif scanned.get('bytes') != catalog.st_size or scanned.get('mtime_ns') != catalog.st_mtime_ns:
                reason = 'law catalog changed since the outline was built'
            elif not bulk_laws.info().get('ready'):
                reason = 'law catalog not ready'
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


def _usps(value):
    value = str(value or '').strip()
    names = bulk_laws.state_names()
    if value.upper() in names:
        return value.upper()
    return next((code for code, name in names.items() if name.lower() == value.lower()), '')


def _int(value, default=0, low=0, high=10 ** 9):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _node(row):
    return {'id': row['id'], 'label': row['label'] or row['raw_label'] or 'Untitled heading', 'provisions': row['total'], 'direct': row['direct'],
            'has_children': bool(row['children']), 'label_basis': row['label_basis']}


AUDIT = ROOT / 'sources/law_snapshot_audit_20260920'


def _audit(code):
    """The publisher's own completeness statement for one jurisdiction's statutes; None when that layer is absent or fails its hash."""
    try:
        gate = json.loads((AUDIT / 'validation.json').read_text(encoding='utf-8'))
        recorded = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or []}
        body = (AUDIT / 'statute_audit.json').read_bytes()
        if gate.get('status') != 'passed' or gate.get('ready') is not True or hashlib.sha256(body).hexdigest() != recorded.get('statute_audit.json'):
            return None
        return (json.loads(body).get('jurisdictions') or {}).get(code)
    except (OSError, ValueError, AttributeError):
        return None


def collections(state):
    """Collections of one jurisdiction that can be browsed by heading."""
    ready, reason = _state()
    code = _usps(state)
    if not ready or not code:
        return {'available': False, 'reason': reason or 'unknown jurisdiction', 'collections': []}
    with _connect() as db:
        rows = db.execute('SELECT kind, provisions, headings FROM collections WHERE state=?', (code,)).fetchall()
    ordered = sorted(rows, key=lambda r: (KIND_ORDER.index(r['kind']) if r['kind'] in KIND_ORDER else len(KIND_ORDER), -r['provisions']))
    return {'available': True, 'state': bulk_laws.state_names().get(code, code), 'usps': code, 'qualification': ready['qualification'], 'statute_audit': _audit(code),
            'collections': [{'kind': r['kind'], 'label': KIND_LABELS.get(r['kind'], r['kind'].replace('_', ' ').capitalize()), 'provisions': r['provisions'], 'headings': r['headings']} for r in ordered]}


def _path(db, node):
    path = []
    while node:
        row = db.execute('SELECT id, parent, label, raw_label FROM nodes WHERE id=?', (node,)).fetchone()
        if not row:
            break
        path.append({'id': row['id'], 'label': row['label'] or row['raw_label']})
        node = row['parent']
    return path[::-1]


def children(state, kind, parent):
    """Headings directly under one heading (parent 0 = top of the collection). A single top heading is opened automatically."""
    ready, reason = _state()
    code = _usps(state)
    if not ready or not code:
        return {'available': False, 'reason': reason or 'unknown jurisdiction', 'nodes': []}
    parent = _int(parent)
    query = ('SELECT n.*, EXISTS(SELECT 1 FROM nodes c WHERE c.state=n.state AND c.kind=n.kind AND c.parent=n.id) AS children '
             'FROM nodes n WHERE n.state=? AND n.kind=? AND n.parent=? ORDER BY n.position')
    with _connect() as db:
        rows = db.execute(query, (code, str(kind or ''), parent)).fetchall()
        while len(rows) == 1 and rows[0]['children'] and not rows[0]['direct']:
            parent = rows[0]['id']
            rows = db.execute(query, (code, str(kind or ''), parent)).fetchall()
        return {'available': True, 'parent': parent, 'path': _path(db, parent), 'nodes': [_node(row) for row in rows]}


SORT_LIMIT = 4000  # headings with more direct provisions than this are listed in the publisher's stored order


def _natural(value):
    return [(0, int(piece), '') if piece.isdigit() else (1, 0, piece.lower()) for piece in re.findall(r'\d+|\D+', value or '')]


@lru_cache(maxsize=256)
def _ordered(node, signature):
    """Every provision directly under one heading, in citation order. `signature` ties the cache to the published outline."""
    with _connect() as db:
        runs = db.execute('SELECT lo, hi FROM segments WHERE node=? ORDER BY lo', (node,)).fetchall()
    rows = []
    with bulk_laws.connect() as catalog:
        for lo, hi in runs:
            rows.extend(catalog.execute('SELECT rowid, id, title, citation, status FROM records WHERE rowid BETWEEN ? AND ?', (lo, hi)).fetchall())
    rows.sort(key=lambda r: (_natural(r['citation'] or r['title']), r['rowid']))
    return tuple({'id': r['id'], 'title': r['title'] or '', 'citation': r['citation'] or '', 'status': r['status'] or ''} for r in rows)


def provisions(node, offset=0, limit=100):
    """Provisions filed directly under one heading, in citation order (publisher's stored order for very large headings)."""
    ready, reason = _state()
    if not ready:
        return {'available': False, 'reason': reason, 'results': []}
    node, offset, limit = _int(node), _int(offset), _int(limit, 100, 1, 200)
    with _connect() as db:
        head = db.execute('SELECT id, direct, state, kind FROM nodes WHERE id=?', (node,)).fetchone()
        if not head:
            return {'available': True, 'total': 0, 'results': [], 'path': []}
        runs = db.execute('SELECT lo, hi FROM segments WHERE node=? ORDER BY lo', (node,)).fetchall()
        path = _path(db, node)
    if head['direct'] <= SORT_LIMIT:
        ordered = _ordered(node, _CACHE['state']['signature'])
        return {'available': True, 'node': node, 'total': head['direct'], 'offset': offset, 'limit': limit, 'path': path, 'order': 'citation', 'results': list(ordered[offset:offset + limit])}
    wanted, skipped, remaining = [], 0, limit
    for lo, hi in runs:  # translate (offset, limit) over the node's row ranges
        size = hi - lo + 1
        if skipped + size > offset and remaining:
            start = lo + max(0, offset - skipped)
            take = min(hi - start + 1, remaining)
            wanted.append((start, start + take - 1))
            remaining -= take
        skipped += size
        if not remaining:
            break
    results = []
    with bulk_laws.connect() as catalog:
        for lo, hi in wanted:
            for row in catalog.execute('SELECT id, title, citation, status FROM records WHERE rowid BETWEEN ? AND ? ORDER BY rowid', (lo, hi)):
                results.append({'id': row['id'], 'title': row['title'] or '', 'citation': row['citation'] or '', 'status': row['status'] or ''})
    return {'available': True, 'node': node, 'total': head['direct'], 'offset': offset, 'limit': limit, 'path': path, 'results': results}


def context(record_id):
    """Where one provision sits: its headings, and the provisions before and after it under the same heading."""
    ready, reason = _state()
    if not ready or not str(record_id or '').startswith('oul:'):
        return {'available': False, 'reason': reason or 'not a law-collection record'}
    with bulk_laws.connect() as catalog:
        found = catalog.execute('SELECT rowid, state, kind FROM records WHERE id=?', (record_id,)).fetchone()
        if not found:
            return {'available': False, 'reason': 'record not found'}
        rowid = found['rowid']
        with _connect() as db:
            run = db.execute('SELECT node, lo, hi FROM segments WHERE lo<=? ORDER BY lo DESC LIMIT 1', (rowid,)).fetchone()
            if not run or run['hi'] < rowid:
                return {'available': False, 'reason': 'record is outside the outline'}
            node = run['node']
            before = rowid - 1 if rowid > run['lo'] else (db.execute('SELECT hi FROM segments WHERE node=? AND hi<? ORDER BY hi DESC LIMIT 1', (node, rowid)).fetchone() or [None])[0]
            after = rowid + 1 if rowid < run['hi'] else (db.execute('SELECT lo FROM segments WHERE node=? AND lo>? ORDER BY lo LIMIT 1', (node, rowid)).fetchone() or [None])[0]
            place = db.execute('SELECT coalesce(sum(hi-lo+1),0) FROM segments WHERE node=? AND hi<?', (node, run['lo'])).fetchone()[0] + (rowid - run['lo']) + 1
            head = db.execute('SELECT direct FROM nodes WHERE id=?', (node,)).fetchone()
            path = _path(db, node)
        if head and head['direct'] <= SORT_LIMIT:
            ordered = _ordered(node, _CACHE['state']['signature'])
            index = next((i for i, row in enumerate(ordered) if row['id'] == record_id), None)
            if index is not None:
                names = bulk_laws.state_names()
                pick = lambda i: {k: ordered[i][k] for k in ('id', 'title', 'citation')} if 0 <= i < len(ordered) else None
                return {'available': True, 'state': names.get(found['state'], found['state']), 'usps': found['state'], 'kind': found['kind'],
                        'kind_label': KIND_LABELS.get(found['kind'], found['kind']), 'node': node, 'path': path, 'position': index + 1, 'of': len(ordered),
                        'previous': pick(index - 1), 'next': pick(index + 1)}

        def neighbour(target):
            if target is None:
                return None
            row = catalog.execute('SELECT id, title, citation FROM records WHERE rowid=?', (target,)).fetchone()
            return {'id': row['id'], 'title': row['title'] or '', 'citation': row['citation'] or ''} if row else None
        names = bulk_laws.state_names()
        return {'available': True, 'state': names.get(found['state'], found['state']), 'usps': found['state'], 'kind': found['kind'],
                'kind_label': KIND_LABELS.get(found['kind'], found['kind']), 'node': node, 'path': path, 'position': place, 'of': head['direct'] if head else None,
                'previous': neighbour(before), 'next': neighbour(after)}
