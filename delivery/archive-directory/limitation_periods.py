"""Read-only, hash-gated adapter for limitation periods (sources/limitation_periods_20260920).

Generic view contract: listing(params), detail(id); for_state(name or USPS) gives the compact block used on a state page.
The published table is third-party (CC BY 4.0, attribution carried in every response); the library check only reports whether
the saved statute text contains the same period wording. Fail-closed; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/limitation_periods_20260920'
DB_NAME = 'limitation_periods.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 60, 100
OUTCOMES = {
    'period_wording_found': 'Same period found in the saved statute',
    'same_period_general': 'Same period in the saved section (general wording)',
    'different_wording_found': 'Saved statute states a different period',
    'section_saved_no_period': 'Saved section found; no period wording recognised',
    'section_not_saved': 'Section not in the saved code',
    '': 'Not checked against the saved code',
}
_LOCK = threading.Lock()
_CACHE: dict = {}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}


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
                reason = 'limitation periods do not match their recorded SHA-256'
            else:
                state = {'qualification': gate.get('qualification') or '', 'attribution': gate.get('attribution') or '', 'counts': gate.get('counts') or {}}
        except (OSError, ValueError, AttributeError):
            reason = 'validation.json missing or unreadable'
        _CACHE['state'] = {'signature': signature, 'state': state, 'reason': reason}
        return state, reason


def _connect():
    connection = sqlite3.connect(f"file:{(DATA / DB_NAME).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _text(params, name):
    value = params.get(name) if isinstance(params, dict) else ''
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:80]


def _links(row):
    links = []
    if row['record_id']:
        links.append({'label': 'Open %s in the saved code' % row['candidate_citation'], 'url': '#record/' + row['record_id']})
    links.append({'label': 'Search the saved %s code for limitations' % row['state'], 'url': '#laws?state=%s&category=statutes&q=limitation' % row['state'].replace(' ', '%20')})
    return links


def listing(params=None):
    ready, reason = _state()
    if not ready:
        return _unavailable(reason)
    params = params if isinstance(params, dict) else {}
    page, limit = _int(params.get('page'), 1, 1, 10000), _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, args = [], []
    state, claim, outcome, query = _text(params, 'state'), _text(params, 'claim'), _text(params, 'check'), _text(params, 'q')
    if state:
        clauses.append('(usps=? COLLATE NOCASE OR state=? COLLATE NOCASE)'); args.extend([state, state])
    if claim:
        clauses.append('claim_type=?'); args.append(claim)
    if outcome:
        clauses.append('outcome=?'); args.append('' if outcome == 'not_checked' else outcome)
    if query:
        clauses.append('(state LIKE ? OR claim_label LIKE ? OR note LIKE ? OR candidate_citation LIKE ?)'); args.extend(['%' + query.replace('%', '') + '%'] * 4)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with _connect() as db:
        total = db.execute('SELECT count(*) FROM periods' + where, args).fetchone()[0]
        rows = db.execute('SELECT p.*, c.position FROM periods p JOIN claim_types c ON c.id=p.claim_type' + where + ' ORDER BY p.state, c.position LIMIT ? OFFSET ?', args + [limit, (page - 1) * limit]).fetchall()
        filters = [
            {'name': 'state', 'label': 'State', 'type': 'select', 'options': [{'value': r['usps'], 'label': r['state']} for r in db.execute('SELECT DISTINCT usps, state FROM periods ORDER BY state')]},
            {'name': 'claim', 'label': 'Claim type', 'type': 'select', 'options': [{'value': r['id'], 'label': r['label']} for r in db.execute('SELECT id, label FROM claim_types ORDER BY position')]},
            {'name': 'check', 'label': 'Check against the saved code', 'type': 'select', 'options': [{'value': r['outcome'] or 'not_checked', 'label': OUTCOMES.get(r['outcome'], r['outcome']), 'count': r['n']}
                                                                                                  for r in db.execute('SELECT outcome, count(*) AS n FROM periods GROUP BY 1 ORDER BY n DESC')]},
            {'name': 'q', 'label': 'Search', 'type': 'search', 'placeholder': 'State, claim or section'},
        ]
    results = [{'id': row['id'], 'title': '%s: %s' % (row['state'], row['claim_label']), 'subtitle': row['note'],
                'cells': {'name': '%s: %s' % (row['state'], row['claim_label']), 'period': row['years_label'], 'section': row['candidate_citation'], 'check': OUTCOMES.get(row['outcome'], row['outcome'])},
                'badges': ['Differs from saved statute'] if row['outcome'] == 'different_wording_found' else [], 'links': _links(row)} for row in rows]
    columns = [{'key': 'name', 'label': 'State and claim'}, {'key': 'period', 'label': 'Period (published table)'}, {'key': 'section', 'label': 'Section looked up'},
               {'key': 'check', 'label': 'Saved statute check'}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': ready['qualification'] + ' Source table: ' + ready['attribution'],
            'filters': filters, 'columns': columns, 'results': results}


def detail(period_id):
    ready, _reason = _state()
    if not ready or not isinstance(period_id, str):
        return None
    with _connect() as db:
        row = db.execute('SELECT * FROM periods WHERE id=?', (period_id[:80],)).fetchone()
    if not row:
        return None
    facts = [['State', row['state']], ['Claim type', row['claim_label']], ['Period in the published table', row['years_label']]]
    if row['note']:
        facts.append(['Caveat in the published table', row['note']])
    if row['candidate_citation']:
        facts.append(['Section looked up in the saved code', row['candidate_citation']])
    facts.append(['Result of the look-up', OUTCOMES.get(row['outcome'], row['outcome'])])
    sections = []
    if row['excerpt']:
        heading = 'Sentence in the saved statute' if row['outcome'] != 'different_wording_found' else 'Sentence in the saved statute (differs from the table)'
        sections.append({'heading': heading, 'text': row['excerpt']})
    if row['other_periods']:
        sections.append({'heading': 'Periods named in the saved section', 'text': row['other_periods']})
    if row['snapshot_note']:
        sections.append({'heading': 'Saved code', 'text': row['snapshot_note']})
    return {'title': '%s: %s' % (row['state'], row['claim_label']), 'subtitle': row['years_label'], 'facts': facts, 'sections': sections, 'links': _links(row),
            'qualification': ready['qualification'] + ' Source table: ' + ready['attribution']}


def for_state(state):
    """All nine claim types for one state, for the state page."""
    ready, _reason = _state()
    key = str(state or '').strip()[:60]
    if not ready or not key:
        return None
    with _connect() as db:
        rows = db.execute('SELECT p.*, c.position FROM periods p JOIN claim_types c ON c.id=p.claim_type WHERE p.usps=? COLLATE NOCASE OR p.state=? COLLATE NOCASE ORDER BY c.position', (key, key)).fetchall()
    if not rows:
        return None
    return {'available': True, 'state': rows[0]['state'], 'attribution': ready['attribution'], 'qualification': ready['qualification'],
            'link': '#limitation-periods?state=' + rows[0]['usps'],
            'rows': [{'id': r['id'], 'claim': r['claim_label'], 'period': r['years_label'], 'note': r['note'], 'section': r['candidate_citation'], 'check': OUTCOMES.get(r['outcome'], ''),
                      'outcome': r['outcome'], 'record_id': r['record_id']} for r in rows]}
