"""Read-only, hash-gated adapter for the 2026 Indiana Code section index (sources/indiana_code_2026_20260919).

Generic view contract: listing(params), detail(id), for_state(usps). Text is the 2026 edition as saved locally, not verified
current. Fail-closed on a missing/failed validation or a hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/indiana_code_2026_20260919'
DB_NAME = 'indiana_code.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
_LOCK = threading.Lock()
_CACHE: dict = {}
STATUS_LABELS = {'text': 'Section text', 'repealed': 'Repealed (as printed)', 'expired': 'Expired (as printed)'}


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
                reason = 'index file does not match its recorded SHA-256'
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


def _sort_key(value):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', str(value or ''))]


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    title = _text(params, 'title')
    if re.fullmatch(r'\d{1,2}', title):
        clauses.append('s.title_no=?'); values.append(title)
    status = _text(params, 'status')
    if status in STATUS_LABELS:
        clauses.append('s.status=?'); values.append(status)
    raw = _text(params, 'q')
    citation = re.fullmatch(r'(?:IC\s*)?(\d{1,2}(?:-[\d.]+){1,3})', raw, re.I)
    terms = [term for term in re.findall(r'[\w][\w.\-]*', raw, re.UNICODE) if term.lower() not in {'a','an','and','the','of','in','on','to','for','by','or','with','at','from'}][:8]
    try:
        connection = _connect(state)
        try:
            if citation:
                prefix = 'IC ' + citation.group(1)
                base = 'FROM sections s WHERE (s.citation=? OR s.citation LIKE ?)' + ''.join(' AND ' + clause for clause in clauses)
                arguments, order = [prefix, prefix + '-%'] + values, 'ORDER BY s.id'
            elif terms:
                base = 'FROM sections_fts f JOIN sections s ON s.id=f.rowid WHERE sections_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments, order = [' '.join('"%s"' % term for term in terms)] + values, 'ORDER BY f.rank'
            else:
                base = 'FROM sections s' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments, order = values, 'ORDER BY s.id'
            total = connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT s.id, s.citation, s.heading, s.title_no, s.title_heading, s.article_heading, s.chapter_heading, s.status, s.text_chars, substr(s.text, 1, 220) AS opening '
                                      f'{base} {order} LIMIT ? OFFSET ?', arguments + [limit, (page - 1) * limit]).fetchall()
            titles = connection.execute('SELECT title_no, max(title_heading), count(*) FROM sections GROUP BY title_no').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('index could not be read: %s' % type(error).__name__)
    results = [{'id': str(row['id']), 'title': '%s %s' % (row['citation'], row['heading'] or ''), 'subtitle': ' '.join((row['opening'] or '').split()),
                'cells': {'title': row['title_heading'] or ('Title ' + str(row['title_no'])), 'chapter': row['chapter_heading'] or '', 'status': STATUS_LABELS.get(row['status'], row['status'])},
                'badges': [] if row['status'] == 'text' else [STATUS_LABELS.get(row['status'], row['status'])],
                'links': [{'label': 'Indiana General Assembly (live code)', 'url': 'https://iga.in.gov/laws/2026/ic/titles/%s' % row['title_no']}]} for row in rows]
    filters = [{'name': 'q', 'label': 'Search text, or type a citation', 'type': 'search', 'placeholder': 'e.g. product liability, 34-20-2-1, statute of limitations'},
               {'name': 'title', 'label': 'Title', 'type': 'select',
                'options': [{'value': str(t[0]), 'label': (t[1] or 'Title %s' % t[0])[:70], 'count': t[2]} for t in sorted(titles, key=lambda t: _sort_key(t[0]))]},
               {'name': 'status', 'label': 'Status as printed', 'type': 'select', 'options': [{'value': key, 'label': label} for key, label in STATUS_LABELS.items()]}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'section', 'label': 'Section'}, {'key': 'title', 'label': 'Title'}, {'key': 'chapter', 'label': 'Chapter'}, {'key': 'status', 'label': 'Status'}],
            'results': results}


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not re.fullmatch(r'\d{1,8}', str(item_id or '')):
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM sections WHERE id=?', (int(item_id),)).fetchone()
            neighbours = connection.execute('SELECT id, citation, heading FROM sections WHERE chapter=? AND title_no=? ORDER BY id LIMIT 80', (row['chapter'], row['title_no'])).fetchall() if row else []
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    facts = [['Citation', row['citation']], ['Title', row['title_heading'] or row['title_no']], ['Article', row['article_heading'] or 'Not stated'],
             ['Chapter', row['chapter_heading'] or 'Not stated'], ['Status as printed', STATUS_LABELS.get(row['status'], row['status'])],
             ['Edition', '2026 Indiana Code (HTML edition, local copy; download date not recorded)'], ['Currency', 'Not verified against the current code']]
    if row['history']:
        facts.append(['History note (as printed)', row['history']])
    sections = [{'heading': 'Section text (2026 edition)', 'text': row['text']}]
    if len(neighbours) > 1:
        sections.append({'heading': 'Other sections in this chapter', 'items': ['%s %s' % (n['citation'], n['heading'] or '') for n in neighbours if n['id'] != row['id']][:60]})
    return {'title': '%s %s' % (row['citation'], row['heading'] or ''), 'subtitle': row['chapter_heading'] or row['title_heading'] or '', 'facts': facts, 'sections': sections,
            'links': [{'label': 'Indiana General Assembly (live code, this title)', 'url': 'https://iga.in.gov/laws/2026/ic/titles/%s' % row['title_no']}],
            'qualification': state['qualification']}


def for_state(usps, folder=None):
    if str(usps or '').upper() != 'IN':
        return None
    state, _ = _state(folder)
    if state is None:
        return None
    return {'total': state['counts'].get('sections'), 'titles': state['counts'].get('titles_with_sections'), 'link': '#indiana-code', 'qualification': state['qualification']}
