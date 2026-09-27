"""Read-only, hash-gated adapter for the saved uscourts.gov pages (sources/uscourts_pages_20260919).

Generic view contract: listing(params), detail(id), summary(). Pages are provider-rendered Markdown captures from one August 2026
crawl, not original HTTP bytes and not the whole site. Fail-closed on a missing/failed validation or a hash mismatch; never
raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/uscourts_pages_20260919'
DB_NAME = 'uscourts_pages.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
READER_CHARS = 60000
_LOCK = threading.Lock()
_CACHE: dict = {}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}


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
            elif not expected.get(DB_NAME) or hashlib.sha256((folder / DB_NAME).read_bytes()).hexdigest() != expected[DB_NAME]:
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
    return str(value or '').strip()[:120]


def _fts_query(text):
    return ' '.join('"%s"' % term for term in re.findall(r'[\w][\w.\-]*', text, re.UNICODE)[:8])


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 10000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    section = _text(params, 'section')
    if section == 'other':
        clauses.append('p.section_label IN (SELECT section_label FROM pages GROUP BY 1 HAVING count(*)<3)')
    elif section:
        clauses.append('p.section_label=?')
        values.append(section)
    has = _text(params, 'has')
    if has == 'documents':
        clauses.append('p.document_link_count>0')
    elif has == 'statistics':
        clauses.append("p.statistics_tables<>'[]'")
    elif has == 'document_text':
        clauses.append('p.looks_like_document=1')
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            columns = 'p.id, p.url, p.title, p.section_label, p.text_chars, p.document_link_count, p.statistics_tables, p.looks_like_document, p.crawled_on'
            hits = {}
            if query:
                base = 'FROM pages_fts f JOIN pages p ON p.id=f.rowid WHERE pages_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments, order = [query] + values, 'ORDER BY f.rank'
            else:
                base = 'FROM pages p' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments, order = values, 'ORDER BY p.section_label, p.title'
            total = connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT {columns} {base} {order} LIMIT ? OFFSET ?', arguments + [limit, (page - 1) * limit]).fetchall()
            if query and rows:
                # Excerpts are computed only for the rows on this page: a snippet over every match would re-read every long report.
                # (FTS5 snippet() is avoided: on multi-megabyte report texts it takes minutes.)
                marks = ','.join('?' * len(rows))
                term = re.findall(r'[\w][\w.\-]*', _text(params, 'q'), re.UNICODE)[0].lower()
                for rowid, hit in connection.execute(f'SELECT id, substr(text, max(1, instr(lower(text), ?) - 100), 280) FROM pages WHERE id IN ({marks}) AND instr(lower(text), ?) > 0',
                                                     [term] + [row['id'] for row in rows] + [term]):
                    hits[rowid] = '… ' + ' '.join((hit or '').split()) + ' …'
            sections = connection.execute('SELECT section_label, count(*) FROM pages GROUP BY 1 ORDER BY 2 DESC').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('page index could not be read: %s' % type(error).__name__)
    options = [{'value': label, 'label': label, 'count': count} for label, count in sections if count >= 3]
    small = sum(count for _, count in sections if count < 3)
    if small:
        options.append({'value': 'other', 'label': 'Other single pages', 'count': small})
    results = []
    for row in rows:
        badges = []
        if row['looks_like_document']:
            badges.append('Report or document text')
        if row['document_link_count']:
            badges.append('%d linked files' % row['document_link_count'])
        if row['statistics_tables'] != '[]':
            badges.append('Links a parsed statistics table')
        results.append({'id': str(row['id']), 'title': row['title'] or row['url'], 'subtitle': hits.get(row['id']) or row['url'],
                        'cells': {'section': row['section_label'], 'length': '{:,} characters'.format(row['text_chars'] or 0), 'saved': row['crawled_on'] or ''},
                        'badges': badges, 'links': [{'label': 'Open on uscourts.gov', 'url': row['url']}]})
    filters = [{'name': 'q', 'label': 'Search the saved page text', 'type': 'search', 'placeholder': 'e.g. multidistrict, judgeship, caseload, fee schedule'},
               {'name': 'section', 'label': 'Site section', 'type': 'select', 'options': options},
               {'name': 'has', 'label': 'Contains', 'type': 'select', 'options': [{'value': 'documents', 'label': 'Links to reports, tables or forms'},
                                                                              {'value': 'statistics', 'label': 'A statistics table parsed in this archive'},
                                                                              {'value': 'document_text', 'label': 'Text of a report or PDF'}]}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'section', 'label': 'Section'}, {'key': 'length', 'label': 'Length'}, {'key': 'saved', 'label': 'Captured'}], 'results': results}


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
    shown = text[:READER_CHARS]
    facts = [['Address', row['url']], ['Site section', row['section_label']], ['Captured (crawl completion date)', row['crawled_on'] or 'not recorded'],
             ['Capture kind', 'Provider-rendered Markdown (not original HTTP bytes)'], ['HTTP status reported by the crawl', str(row['http_status'])],
             ['Text length', '{:,} characters{}'.format(len(text), ' (first {:,} shown)'.format(READER_CHARS) if len(text) > READER_CHARS else '')],
             ['SHA-256 of the saved text (prefix)', row['text_sha256'][:16] + '…']]
    sections = [{'heading': 'Saved page text', 'text': shown}]
    links = json.loads(row['document_links'] or '[]')
    if links:
        sections.append({'heading': 'Reports, tables and forms linked from this page (%d)' % row['document_link_count'], 'items': links[:120]})
    out_links = [{'label': 'Open on uscourts.gov', 'url': row['url']}]
    for table in json.loads(row['statistics_tables'] or '[]')[:12]:
        out_links.append({'label': 'Parsed statistics table %s' % table, 'url': '#statistics/' + table})
    return {'title': row['title'] or row['url'], 'subtitle': row['section_label'], 'facts': facts, 'sections': sections, 'links': out_links, 'qualification': state['qualification']}


def summary(folder=None):
    state, reason = _state(folder)
    if state is None:
        return {'available': False, 'reason': reason}
    return {'available': True, 'pages': state['counts'].get('pages_published'), 'by_section': state['counts'].get('by_section'), 'qualification': state['qualification']}
