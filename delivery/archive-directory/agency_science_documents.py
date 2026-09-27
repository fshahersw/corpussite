"""Read-only, hash-gated adapter for downloaded agency and science documents (sources/agency_science_documents_20260919).

Generic view contract: listing(params), detail(id), original(file_id), for_agency(family), summary(). The index is a snapshot
of a growing download collection. Original bytes and extracted text are read from the collection only after a fresh SHA-256
check against the index, with the path confined to the collection root. Fail-closed; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/agency_science_documents_20260919'
COLLECTION = ROOT / 'corpus/agency_science_documents_20260919'
DB_NAME = 'documents.sqlite3'
ADAPTER = 'agency_science_documents'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
READER_CHARS = 60000
_LOCK = threading.Lock()
_CACHE: dict = {}
FAMILY_LABELS = {'atsdr': 'ATSDR', 'epa': 'EPA', 'cpsc': 'CPSC', 'jpml': 'JPML', 'ntp': 'National Toxicology Program', 'osha': 'OSHA', 'cdc': 'CDC', 'nhtsa': 'NHTSA',
                 'cms': 'CMS', 'fda': 'FDA'}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'qualification': '', 'filters': [], 'columns': [], 'results': []}


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
                reason = 'index file does not match its recorded SHA-256'
            else:
                state = {'folder': folder, 'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}, 'validated_at': gate.get('validated_at')}
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


def _confined(relative):
    """Resolved path inside the collection root, else None."""
    if not relative:
        return None
    path = (COLLECTION / relative).resolve()
    try:
        path.relative_to(COLLECTION.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _size(count):
    count = count or 0
    return '%.1f MB' % (count / 1e6) if count >= 1e6 else '%d KB' % max(1, round(count / 1e3))


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    family = _text(params, 'agency')
    if family:
        clauses.append('d.family=?'); values.append(family)
    extension = _text(params, 'type')
    if extension:
        clauses.append('d.ext=?'); values.append(extension.lower())
    if _text(params, 'text') == 'yes':
        clauses.append('d.text_chars>0')
    elif _text(params, 'text') == 'no':
        clauses.append('coalesce(d.text_chars,0)=0')
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            if query:
                base = 'FROM docs_fts f JOIN docs d ON d.id=f.rowid WHERE docs_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments, order = [query] + values, 'ORDER BY f.rank'
            else:
                base = 'FROM docs d' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments, order = values, 'ORDER BY d.family, d.title'
            total = connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT d.* {base} {order} LIMIT ? OFFSET ?', arguments + [limit, (page - 1) * limit]).fetchall()
            families = connection.execute('SELECT family, count(*) FROM docs GROUP BY family ORDER BY 2 DESC').fetchall()
            extensions = connection.execute('SELECT ext, count(*) FROM docs GROUP BY ext ORDER BY 2 DESC').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('index could not be read: %s' % type(error).__name__)
    results = []
    for row in rows:
        badges = [(row['ext'] or 'file').upper()]
        if not row['text_chars']:
            badges.append('No extractable text (scan or unsupported)')
        results.append({'id': str(row['id']), 'title': row['title'], 'subtitle': row['url'],
                        'cells': {'agency': FAMILY_LABELS.get(row['family'], row['family'] or ''), 'type': (row['ext'] or '').upper(), 'size': _size(row['bytes']), 'saved': (row['fetched_at'] or '')[:10]},
                        'badges': badges, 'links': [{'label': 'Open saved copy', 'url': '/supplement-files/%s/%s' % (ADAPTER, row['id'])}, {'label': 'Source address', 'url': row['url']}]})
    filters = [{'name': 'q', 'label': 'Search titles and document text', 'type': 'search', 'placeholder': 'e.g. benzene, minimal risk level, recall, transfer order'},
               {'name': 'agency', 'label': 'Publisher', 'type': 'select', 'options': [{'value': f, 'label': FAMILY_LABELS.get(f, f), 'count': n} for f, n in families if f]},
               {'name': 'type', 'label': 'File type', 'type': 'select', 'options': [{'value': e, 'label': e.upper(), 'count': n} for e, n in extensions if e]},
               {'name': 'text', 'label': 'Text', 'type': 'select', 'options': [{'value': 'yes', 'label': 'Searchable text extracted'}, {'value': 'no', 'label': 'No extractable text'}]}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'title', 'label': 'Document'}, {'key': 'agency', 'label': 'Publisher'}, {'key': 'type', 'label': 'Type'}, {'key': 'size', 'label': 'Size'}, {'key': 'saved', 'label': 'Saved'}],
            'results': results}


def _row(state, item_id):
    if not re.fullmatch(r'\d{1,9}', str(item_id or '')):
        return None
    try:
        connection = _connect(state)
        try:
            return connection.execute('SELECT * FROM docs WHERE id=?', (int(item_id),)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None:
        return None
    row = _row(state, item_id)
    if row is None:
        return None
    facts = [['Source address', row['url']], ['Publisher', FAMILY_LABELS.get(row['family'], row['family'] or row['host'])], ['Title basis', row['title_basis']],
             ['File', '%s (%s)' % (row['filename'], _size(row['bytes']))], ['Saved (download time)', row['fetched_at'] or 'not recorded'],
             ['Published / effective date', 'Not recorded by the download; read the document'], ['SHA-256 (prefix)', row['sha256'][:16] + '…']]
    others = json.loads(row['other_urls'] or '[]')
    if others:
        facts.append(['Same file also served at', '; '.join(others[:5])])
    sections = []
    path = _confined(row['text_path'])
    if path and row['text_sha256']:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() == row['text_sha256']:
            text = data.decode('utf-8', 'replace')
            sections.append({'heading': 'Extracted text' + (' (first %s of %s characters)' % ('{:,}'.format(READER_CHARS), '{:,}'.format(len(text))) if len(text) > READER_CHARS else ''),
                             'text': text[:READER_CHARS]})
        else:
            facts.append(['Extracted text', 'Withheld: the saved text no longer matches its recorded hash'])
    else:
        facts.append(['Extracted text', 'None: scanned or unsupported format (status: %s)' % (row['extraction_status'] or 'unknown')])
    return {'title': row['title'], 'subtitle': row['host'], 'facts': facts, 'sections': sections,
            'links': [{'label': 'Open saved copy', 'url': '/supplement-files/%s/%s' % (ADAPTER, row['id'])}, {'label': 'Source address', 'url': row['url']}],
            'qualification': state['qualification']}


def original(file_id, folder=None):
    state, _ = _state(folder)
    if state is None:
        return None
    row = _row(state, str(file_id or '').strip('/'))
    if row is None:
        return None
    path = _confined(row['raw_path'])
    if path is None:
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row['sha256']:
        return None
    mime = mimetypes.guess_type(row['filename'] or '')[0] or 'application/octet-stream'
    return data, mime, re.sub(r'[^A-Za-z0-9._-]+', '_', row['filename'] or 'document')[:120]


def for_agency(family, folder=None):
    state, _ = _state(folder)
    if state is None or not re.fullmatch(r'[a-z_]{2,24}', str(family or '')):
        return None
    try:
        connection = _connect(state)
        try:
            total = connection.execute('SELECT count(*) FROM docs WHERE family=?', (family,)).fetchone()[0]
            rows = connection.execute('SELECT id, title, url FROM docs WHERE family=? ORDER BY title LIMIT 12', (family,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if not total:
        return None
    return {'total': total, 'results': [{'id': str(r['id']), 'title': r['title'], 'subtitle': r['url']} for r in rows], 'link': '#agency-documents?agency=' + family,
            'qualification': state['qualification']}


def summary(folder=None):
    state, reason = _state(folder)
    if state is None:
        return {'available': False, 'reason': reason}
    return {'available': True, 'validated_at': state['validated_at'], **{k: state['counts'].get(k) for k in ('documents', 'with_extracted_text', 'bytes', 'by_family')}}
