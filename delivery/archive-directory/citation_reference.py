"""Read-only, hash-gated adapter for the citation guide (sources/citation_reference_flp_20260920).

Generic view contract: listing(params), detail(id), plus lookup(abbreviation) for readers that meet a citation in text.
Fail-closed on a missing/failed validation or a hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/citation_reference_flp_20260920'
DB_NAME = 'citation_reference.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
KIND_LABELS = {'reporter': 'Case reporter', 'law': 'Statute, regulation or session law', 'journal': 'Law journal'}
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
                reason = 'citation guide does not match its recorded SHA-256'
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


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _text(params, name):
    value = params.get(name) if isinstance(params, dict) else ''
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:160]


def _years(row):
    first, last = row['first_year'], row['last_year']
    if not first and not last:
        return ''
    return '%s to %s' % (first or 'unknown', last or ('present' if row['still_published'] else 'unknown'))


def _options(db, column, labels=None, where='1'):
    rows = db.execute(f'SELECT {column} AS value, count(*) AS n FROM entries WHERE {where} AND {column}<>"" GROUP BY 1 ORDER BY n DESC').fetchall()
    return [{'value': r['value'], 'label': (labels or {}).get(r['value'], r['value']), 'count': r['n']} for r in rows]


def listing(params=None):
    ready, reason = _state()
    if not ready:
        return _unavailable(reason)
    params = params if isinstance(params, dict) else {}
    page, limit = _int(params.get('page'), 1, 1, 100000), _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, args = [], []
    kind, cite_type, place, current, query = _text(params, 'kind'), _text(params, 'cite_type'), _text(params, 'state'), _text(params, 'current'), _text(params, 'q')
    if kind:
        clauses.append('e.kind=?'); args.append(kind)
    if cite_type:
        clauses.append('e.cite_type=?'); args.append(cite_type)
    if place:
        clauses.append('(e.states LIKE ? OR e.jurisdiction=?)'); args.extend(['%"' + place.replace('%', '').replace('"', '') + '"%', place])
    if current == 'yes':
        clauses.append('e.still_published=1')
    with _connect() as db:
        if query:
            # an exact abbreviation or variant first ("F.Supp.3d", "USC"), then words anywhere in the names
            exact = [r['entry_id'] for r in db.execute('SELECT DISTINCT entry_id FROM variants WHERE variant=? COLLATE NOCASE', (query,))]
            words = ' '.join('"%s"*' % w.replace('"', '') for w in query.replace('.', ' ').split() if w.strip('"'))
            matched = [r['id'] for r in db.execute('SELECT m.id FROM entries_fts f JOIN fts_map m ON m.number=f.rowid WHERE entries_fts MATCH ? LIMIT 3000', (words,))] if words else []
            ids = list(dict.fromkeys(exact + matched))
            if not ids:
                clauses.append('0')
            else:
                clauses.append('e.id IN (%s)' % ','.join('?' * len(ids))); args.extend(ids)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        total = db.execute('SELECT count(*) FROM entries e' + where, args).fetchone()[0]
        order = 'ORDER BY e.kind=\'reporter\' DESC, e.cite_type IN (\'federal\',\'scotus_early\',\'leg_statute\') DESC, e.abbreviation COLLATE NOCASE'
        rows = db.execute('SELECT e.* FROM entries e' + where + ' ' + order + ' LIMIT ? OFFSET ?', args + [limit, (page - 1) * limit]).fetchall()
        if query and rows:
            rank = {value: index for index, value in enumerate(ids)}
            rows = sorted(rows, key=lambda r: rank.get(r['id'], 10 ** 6))
        states = sorted({name for r in db.execute('SELECT states FROM entries WHERE states<>"[]"') for name in json.loads(r['states'])})
        filters = [
            {'name': 'q', 'label': 'Abbreviation or name', 'type': 'search', 'placeholder': 'F. Supp. 3d, Cal. App., U.S.C. ...'},
            {'name': 'kind', 'label': 'Kind', 'type': 'select', 'options': _options(db, 'kind', KIND_LABELS)},
            {'name': 'cite_type', 'label': 'Type', 'type': 'select', 'options': [{'value': r['value'], 'label': r['label'], 'count': r['n']} for r in db.execute(
                'SELECT cite_type AS value, cite_type_label AS label, count(*) AS n FROM entries GROUP BY 1,2 ORDER BY n DESC')]},
            {'name': 'state', 'label': 'State', 'type': 'select', 'options': [{'value': name, 'label': name} for name in states]},
            {'name': 'current', 'label': 'Publication', 'type': 'select', 'options': [{'value': 'yes', 'label': 'Still published'}]},
        ]
    results = []
    for row in rows:
        editions = json.loads(row['editions'])
        results.append({'id': row['id'], 'title': row['abbreviation'], 'subtitle': row['name'] if row['name'] != row['abbreviation'] else '',
                        'cells': {'abbreviation': row['abbreviation'], 'name': row['name'], 'type': row['cite_type_label'], 'jurisdiction': row['jurisdiction'], 'years': _years(row),
                                  'editions': str(len(editions)) if editions else ''},
                        'badges': [], 'links': []})
    columns = [{'key': 'abbreviation', 'label': 'Cited as'}, {'key': 'name', 'label': 'Publication'}, {'key': 'type', 'label': 'Type'}, {'key': 'jurisdiction', 'label': 'Jurisdiction'},
               {'key': 'years', 'label': 'Years'}, {'key': 'editions', 'label': 'Series'}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': ready['qualification'], 'filters': filters, 'columns': columns, 'results': results}


def detail(entry_id):
    ready, _reason = _state()
    if not ready or not isinstance(entry_id, str):
        return None
    with _connect() as db:
        row = db.execute('SELECT * FROM entries WHERE id=?', (entry_id[:200],)).fetchone()
    if not row:
        return None
    editions, variations, examples = json.loads(row['editions']), json.loads(row['variations']), json.loads(row['examples'])
    facts = [['Cited as', row['abbreviation']], ['Kind', KIND_LABELS.get(row['kind'], row['kind'])], ['Type', row['cite_type_label']]]
    if row['jurisdiction']:
        facts.append(['Jurisdiction', row['jurisdiction']])
    if _years(row):
        facts.append(['Years recorded', _years(row)])
    if row['publisher']:
        facts.append(['Publisher', row['publisher']])
    sections = []
    if editions:
        sections.append({'heading': 'Series', 'header': ['Series', 'From', 'To'], 'rows': [[e['edition'], e['start'] or '', e['end'] or 'present'] for e in editions]})
    if variations:
        sections.append({'heading': 'Other spellings seen in citations', 'header': ['Written as', 'Means'], 'rows': [[v['variant'], v['edition']] for v in variations[:400]]})
    if examples:
        sections.append({'heading': 'Examples', 'items': examples[:40]})
    scopes, states = json.loads(row['scopes']), json.loads(row['states'])
    if states or scopes:
        sections.append({'heading': 'Courts and places covered', 'items': scopes + states})
    if row['notes']:
        sections.append({'heading': 'Publisher note', 'text': row['notes']})
    links = [{'label': 'Publisher reference', 'url': row['href']}] if str(row['href']).startswith('http') else []
    return {'title': row['abbreviation'], 'subtitle': row['name'], 'facts': facts, 'sections': sections, 'links': links, 'qualification': ready['qualification']}


def lookup(abbreviation):
    """The publications an abbreviation or variant spelling can stand for (exact, case-insensitive)."""
    ready, _reason = _state()
    text = str(abbreviation or '').strip()[:80]
    if not ready or not text:
        return []
    with _connect() as db:
        rows = db.execute('SELECT e.id, e.abbreviation, e.name, e.cite_type_label, v.edition FROM variants v JOIN entries e ON e.id=v.entry_id WHERE v.variant=? COLLATE NOCASE LIMIT 8', (text,)).fetchall()
    return [{'id': r['id'], 'abbreviation': r['abbreviation'], 'name': r['name'], 'type': r['cite_type_label'], 'series': r['edition']} for r in rows]
