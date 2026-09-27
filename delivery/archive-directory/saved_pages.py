"""Read-only, hash-gated adapter for saved web pages from the August 2026 crawls (sources/saved_web_pages_20260919).

Generic view contract: listing(params), detail(id), for_state(usps), summary(). Pages are provider-rendered Markdown captures
from bounded crawls, not original HTTP bytes and never a whole site. Fail-closed on a missing/failed validation or a hash
mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/saved_web_pages_20260919'
DB_NAME = 'pages.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
READER_CHARS = 60000
_LOCK = threading.Lock()
_CACHE: dict = {}
LAYER_LABELS = {'legislature_law': 'Legislature & statutes', 'state_court': 'State courts', 'county_court': 'County & circuit courts', 'federal_court': 'Federal courts',
                'federal_agency': 'Federal agencies', 'other': 'Other'}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'qualification': '', 'filters': [], 'columns': [], 'results': []}


def _sha256(path: Path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _state(folder=None):
    folder = Path(folder) if folder else DATA
    try:
        signature = tuple((name, (folder / name).stat().st_size, (folder / name).stat().st_mtime_ns) for name in ('validation.json', DB_NAME))
    except OSError:
        return None, 'supplement files missing'
    with _LOCK:
        cached = _CACHE.get(str(folder))
        if cached and cached['signature'] == signature:
            return cached['state'], cached['reason']
        state, reason = None, None
        try:
            gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)} if isinstance(gate, dict) else {}
            if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'supplement not published (status/ready gate closed)'
            elif not expected.get(DB_NAME) or _sha256(folder / DB_NAME) != expected[DB_NAME]:
                reason = 'page index does not match its recorded SHA-256'
            else:
                state = {'folder': folder, 'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}}
        except (OSError, ValueError):
            reason = 'validation.json missing or unreadable'
        _CACHE[str(folder)] = {'signature': signature, 'state': state, 'reason': reason}
        return state, reason


def _connect(state):
    connection = sqlite3.connect(f"file:{(state['folder'] / DB_NAME).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _text(params, name):
    value = params.get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:160]


def _terms(text):
    return [term for term in re.findall(r'[\w][\w.\-]*', text, re.UNICODE) if term.lower() not in {'a','an','and','the','of','in','on','to','for','by','or','with','at','from'}][:8]


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    for name, column in (('collection', 'p.collection_label'), ('state', 'p.state'), ('layer', 'p.layer'), ('host', 'p.host')):
        value = _text(params, name)
        if value:
            clauses.append(column + '=?')
            values.append(value)
    terms = _terms(_text(params, 'q'))
    query = ' '.join('"%s"' % term for term in terms)
    try:
        connection = _connect(state)
        try:
            columns = 'p.id, p.url, p.host, p.title, p.collection_label, p.state, p.layer, p.text_chars, p.saved_on, p.looks_like_document'
            if query:
                base = 'FROM pages_fts f JOIN pages p ON p.id=f.rowid WHERE pages_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments, order = [query] + values, 'ORDER BY f.rank'
            else:
                base = 'FROM pages p' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments, order = values, 'ORDER BY p.host, p.title'
            total = connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT {columns} {base} {order} LIMIT ? OFFSET ?', arguments + [limit, (page - 1) * limit]).fetchall()
            hits = {}
            if terms and rows:
                # Excerpts only for this page of results; FTS5 snippet() is unusably slow on multi-megabyte texts.
                term = terms[0].lower()
                marks = ','.join('?' * len(rows))
                for rowid, hit in connection.execute(f'SELECT id, substr(text, max(1, instr(lower(text), ?) - 100), 280) FROM pages WHERE id IN ({marks}) AND instr(lower(text), ?) > 0',
                                                     [term] + [row['id'] for row in rows] + [term]):
                    hits[rowid] = '… ' + ' '.join((hit or '').split()) + ' …'
            facets = {name: connection.execute(f'SELECT {column}, count(*) FROM pages WHERE {column} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC').fetchall()
                      for name, column in (('collection', 'collection_label'), ('state', 'state'), ('layer', 'layer'))}
            hosts = connection.execute('SELECT host, count(*) FROM pages GROUP BY 1 ORDER BY 2 DESC LIMIT 60').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('page index could not be read: %s' % type(error).__name__)
    results = []
    for row in rows:
        badges = [LAYER_LABELS.get(row['layer'], row['layer'] or '')]
        if row['looks_like_document']:
            badges.append('Text of a PDF or document')
        results.append({'id': str(row['id']), 'title': row['title'] or row['url'], 'subtitle': hits.get(row['id']) or row['url'],
                        'cells': {'site': row['host'], 'place': row['state'] or 'Federal', 'length': '{:,} characters'.format(row['text_chars'] or 0), 'saved': row['saved_on'] or ''},
                        'badges': [b for b in badges if b], 'links': [{'label': 'Open the live address', 'url': row['url']}]})
    filters = [{'name': 'q', 'label': 'Search the saved page text', 'type': 'search', 'placeholder': 'e.g. local rules, statute of limitations, mass tort, expungement'},
               {'name': 'collection', 'label': 'Crawl', 'type': 'select', 'options': [{'value': v, 'label': v, 'count': n} for v, n in facets['collection']]},
               {'name': 'state', 'label': 'State', 'type': 'select', 'options': [{'value': v, 'label': v, 'count': n} for v, n in sorted((tuple(row) for row in facets['state']), key=lambda pair: str(pair[0]))]},
               {'name': 'layer', 'label': 'Category', 'type': 'select', 'options': [{'value': v, 'label': LAYER_LABELS.get(v, v), 'count': n} for v, n in facets['layer']]},
               {'name': 'host', 'label': 'Site', 'type': 'select', 'options': [{'value': v, 'label': v, 'count': n} for v, n in hosts]}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'title', 'label': 'Page'}, {'key': 'site', 'label': 'Site'}, {'key': 'place', 'label': 'Place'}, {'key': 'length', 'label': 'Length'},
                        {'key': 'saved', 'label': 'Saved'}],
            'results': results}


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not re.fullmatch(r'\d{1,8}', str(item_id or '')):
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM pages WHERE id=?', (int(item_id),)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    text = row['text'] or ''
    facts = [['Address', row['url']], ['Site', row['host']], ['Crawl', row['collection_label']], ['Category', LAYER_LABELS.get(row['layer'], row['layer'] or '')],
             ['Place', row['state'] or 'Federal or not stated'], ['Saved (date of the local capture file)', row['saved_on'] or 'not recorded'],
             ['Capture kind', 'Provider-rendered Markdown (not original HTTP bytes)'],
             ['Text length', '{:,} characters{}'.format(len(text), ' (first {:,} shown)'.format(READER_CHARS) if len(text) > READER_CHARS else '')],
             ['SHA-256 of the saved text (prefix)', row['text_sha256'][:16] + '…']]
    return {'title': row['title'] or row['url'], 'subtitle': row['host'], 'facts': facts, 'sections': [{'heading': 'Saved page text', 'text': text[:READER_CHARS]}],
            'links': [{'label': 'Open the live address', 'url': row['url']}], 'qualification': state['qualification']}


def for_state(usps, folder=None):
    state, _ = _state(folder)
    if state is None or not re.fullmatch(r'[A-Za-z]{2}', str(usps or '')):
        return None
    code = str(usps).upper()
    try:
        connection = _connect(state)
        try:
            total = connection.execute('SELECT count(*) FROM pages WHERE state=?', (code,)).fetchone()[0]
            rows = connection.execute('SELECT id, title, url FROM pages WHERE state=? ORDER BY text_chars DESC LIMIT 8', (code,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if not total:
        return None
    return {'total': total, 'results': [{'id': str(r['id']), 'title': r['title'] or r['url'], 'subtitle': r['url']} for r in rows], 'link': '#saved-pages?state=' + code,
            'qualification': state['qualification']}


def summary(folder=None):
    state, reason = _state(folder)
    if state is None:
        return {'available': False, 'reason': reason}
    return {'available': True, 'pages': state['counts'].get('pages'), 'by_collection': state['counts'].get('by_collection'), 'qualification': state['qualification']}
