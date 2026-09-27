"""Read-only, hash-gated adapter for the citation index (sources/citation_index_20260920).

Generic view contract: listing(params), detail(id). for_record(record_id) lists the saved documents that cite one provision of the
saved law text; for_document(layer, doc_id) lists the authorities one saved document cites. Counts are documents in this library,
never a measure of importance. Fail-closed; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/citation_index_20260920'
DB_NAME = 'citation_index.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
KINDS = {'case': 'Case', 'law': 'Statute or regulation', 'journal': 'Law journal article'}
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
                reason = 'citation index does not match its recorded SHA-256'
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


WHERE = {'law_text': 'Saved law text', 'federal_register': 'Saved Federal Register entry', 'public_law': 'Saved public law'}


def _where(row):
    if row['local_kind']:
        return WHERE.get(row['local_kind'], 'Saved here')
    return 'CourtListener link' if row['link_out'] else 'Citation only'


def _links(row):
    links = []
    if row['local_record_id']:
        links.append({'label': 'Open %s in the saved law text' % row['local_citation'], 'url': '#record/' + row['local_record_id']})
    elif row['local_link']:
        links.append({'label': 'Open the %s' % WHERE.get(row['local_kind'], 'saved record').lower().replace('saved ', 'saved '), 'url': row['local_link']})
    if row['link_out']:
        links.append({'label': 'Look up on CourtListener', 'url': row['link_out']})
    return links


def listing(params=None):
    ready, reason = _state()
    if not ready:
        return _unavailable(reason)
    params = params if isinstance(params, dict) else {}
    page, limit = _int(params.get('page'), 1, 1, 100000), _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, args = [], []
    kind, reporter, layer, local, query = _text(params, 'kind'), _text(params, 'reporter'), _text(params, 'layer'), _text(params, 'saved'), _text(params, 'q')
    if kind in KINDS:
        clauses.append('a.kind=?'); args.append(kind)
    if reporter:
        clauses.append('a.reporter=?'); args.append(reporter)
    if local == 'yes':
        clauses.append("a.local_kind<>''")
    if layer:
        clauses.append('a.id IN (SELECT authority_id FROM mentions WHERE layer=?)'); args.append(layer)
    if query:
        words = ' '.join('"%s"' % w.replace('"', '') for w in query.split() if w.strip('"'))
        clauses.append('a.id IN (SELECT rowid FROM authorities_fts WHERE authorities_fts MATCH ?)'); args.append(words or '""')
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with _connect() as db:
        try:
            total = db.execute('SELECT count(*) FROM authorities a' + where, args).fetchone()[0]
            rows = db.execute('SELECT a.* FROM authorities a' + where + ' ORDER BY a.documents DESC, a.mentions DESC, a.citation LIMIT ? OFFSET ?', args + [limit, (page - 1) * limit]).fetchall()
        except sqlite3.Error:
            total, rows = 0, []
        filters = [
            {'name': 'q', 'label': 'Citation or case name', 'type': 'search', 'placeholder': '21 U.S.C. 355, Daubert, 509 U.S. 579'},
            {'name': 'kind', 'label': 'Kind', 'type': 'select', 'options': [{'value': r['kind'], 'label': KINDS.get(r['kind'], r['kind']), 'count': r['n']} for r in db.execute('SELECT kind, count(*) AS n FROM authorities GROUP BY 1 ORDER BY n DESC')]},
            {'name': 'reporter', 'label': 'Reporter or code', 'type': 'select', 'options': [{'value': r['reporter'], 'label': r['reporter'], 'count': r['n']} for r in db.execute(
                "SELECT reporter, count(*) AS n FROM authorities WHERE reporter<>'' GROUP BY 1 ORDER BY n DESC LIMIT 60")]},
            {'name': 'layer', 'label': 'Cited in', 'type': 'select', 'options': [{'value': r['layer'], 'label': r['label'], 'count': r['with_citations']} for r in db.execute('SELECT layer, label, with_citations FROM layers WHERE scanned>0')]},
            {'name': 'saved', 'label': 'In this library', 'type': 'select', 'options': [{'value': 'yes', 'label': 'Opens a saved record'}]},
        ]
    results = [{'id': str(row['id']), 'title': row['citation'], 'subtitle': ' · '.join(p for p in (row['name'], row['year']) if p),
                'cells': {'citation': row['citation'], 'kind': KINDS.get(row['kind'], row['kind']), 'documents': str(row['documents']), 'mentions': str(row['mentions']), 'where': _where(row)},
                'badges': [], 'links': _links(row)} for row in rows]
    columns = [{'key': 'citation', 'label': 'Authority'}, {'key': 'kind', 'label': 'Kind'}, {'key': 'documents', 'label': 'Saved documents citing it'}, {'key': 'mentions', 'label': 'Mentions'},
               {'key': 'where', 'label': 'In this library'}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': ready['qualification'], 'filters': filters, 'columns': columns, 'results': results}


def _citing(db, authority_id, limit=60):
    rows = db.execute('''SELECT m.layer, m.doc_id, m.mentions, m.passage, d.title, d.url, l.label, l.route FROM mentions m
                         LEFT JOIN documents d ON d.layer=m.layer AND d.doc_id=m.doc_id LEFT JOIN layers l ON l.layer=m.layer
                         WHERE m.authority_id=? ORDER BY m.mentions DESC LIMIT ?''', (authority_id, limit)).fetchall()
    return [{'title': (r['title'] or r['url'] or 'Saved document')[:160], 'subtitle': '%s · %d mention%s · “…%s…”' % (r['label'] or r['layer'], r['mentions'], '' if r['mentions'] == 1 else 's', (r['passage'] or '')[:300]),
             'links': [{'label': 'Find in ' + (r['label'] or 'the library'), 'url': '#%s?q=%s' % (r['route'] or r['layer'], _query_for(r['title'], r['url']))}] + ([{'label': 'Original address', 'url': r['url']}] if str(r['url']).startswith('http') else [])}
            for r in rows]


def _query_for(title, url):
    import urllib.parse
    words = [w for w in str(title or '').replace('"', ' ').split() if len(w) > 2][:6]
    return urllib.parse.quote(' '.join(words) if words else str(url or '')[:80])


def detail(authority_id):
    ready, _reason = _state()
    number = _int(authority_id, 0, 0, 10 ** 9)
    if not ready or not number:
        return None
    with _connect() as db:
        row = db.execute('SELECT * FROM authorities WHERE id=?', (number,)).fetchone()
        if not row:
            return None
        citing = _citing(db, number)
    facts = [['Authority', row['citation']], ['Kind', KINDS.get(row['kind'], row['kind'])]]
    if row['name']:
        facts.append(['Name found beside the citation', row['name']])
    if row['year']:
        facts.append(['Year as parsed from the text', row['year']])
    if row['court']:
        facts.append(['Court as parsed (CourtListener id)', row['court']])
    facts += [['Saved documents citing it', str(row['documents'])], ['Mentions', str(row['mentions'])], ['In this library', _where(row)]]
    if row['local_basis']:
        facts.append(['Match to the saved text', row['local_basis']])
    return {'title': row['citation'], 'subtitle': ' · '.join(p for p in (row['name'], row['year']) if p), 'facts': facts,
            'sections': [{'heading': 'Saved documents citing this authority', 'items': citing}], 'links': _links(row), 'qualification': ready['qualification']}


def for_record(record_id):
    """Saved documents that cite one provision of the saved law text."""
    ready, _reason = _state()
    key = str(record_id or '').strip()[:120]
    if not ready or not key:
        return None
    with _connect() as db:
        rows = db.execute('SELECT id, citation, documents, mentions FROM authorities WHERE local_record_id=? ORDER BY documents DESC', (key,)).fetchall()
        if not rows:
            return {'available': True, 'total': 0, 'results': []}
        results = []
        for row in rows:
            results.extend(_citing(db, row['id'], 12))
        return {'available': True, 'total': sum(r['documents'] for r in rows), 'authority_ids': [r['id'] for r in rows], 'link': '#citation-index?q=' + _query_for(rows[0]['citation'], ''),
                'results': results[:12], 'qualification': ready['qualification']}


def for_document(layer, doc_id):
    """Authorities one saved document cites."""
    ready, _reason = _state()
    if not ready:
        return None
    with _connect() as db:
        rows = db.execute('''SELECT a.*, m.mentions AS here FROM mentions m JOIN authorities a ON a.id=m.authority_id WHERE m.layer=? AND m.doc_id=?
                             ORDER BY (a.local_record_id<>'') DESC, m.mentions DESC LIMIT 80''', (str(layer or '')[:40], str(doc_id or '')[:80])).fetchall()
    return {'available': True, 'total': len(rows), 'results': [{'id': str(r['id']), 'title': r['citation'], 'subtitle': ' · '.join(p for p in (r['name'], KINDS.get(r['kind'], ''), '%d here' % r['here']) if p),
                                                                'links': _links(r)} for r in rows]}
