"""Read-only, hash-gated adapter for the Federal Register document index 1994 to 2026 (sources/federal_register_history_20260919).

Generic view contract: listing(params), detail(id), plus for_cfr(title, part) and for_agency(agency_id). Rows are document
identities as listed by the federalregister.gov API on 2026-08-20; no document text is stored and links go to
federalregister.gov. Fail-closed on a missing/failed validation or hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/federal_register_history_20260919'
DB_NAME = 'federal_register.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
_LOCK = threading.Lock()
_CACHE: dict = {}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}


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


def _fts_query(text):
    return ' '.join('"%s"' % term for term in re.findall(r'[\w][\w.\-]*', text, re.UNICODE)[:8])


def _url(row):
    return 'https://www.federalregister.gov/d/' + row['number']


def _result(row):
    agencies = json.loads(row['agencies'] or '[]')
    cfr = json.loads(row['cfr'] or '[]')
    badges = [row['type']]
    if json.loads(row['rins'] or '[]'):
        badges.append('RIN ' + json.loads(row['rins'])[0])
    if row['correction_of']:
        badges.append('Correction')
    return {'id': str(row['id']), 'title': row['title'] or row['number'], 'subtitle': ' · '.join(agencies[:3]) or 'Agency not listed by the API',
            'cells': {'published': row['date'], 'type': row['type'], 'cfr': ', '.join(cfr[:4]) + (' …' if len(cfr) > 4 else ''), 'citation': row['citation'] or row['number']},
            'badges': badges, 'links': [{'label': 'Open on federalregister.gov', 'url': _url(row)}]}


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    document_type = _text(params, 'type')
    if document_type:
        clauses.append('d.type=?'); values.append(document_type)
    year = _text(params, 'year')
    if re.fullmatch(r'\d{4}', year):
        clauses.append('d.year=?'); values.append(int(year))
    cfr_title, cfr_part = _text(params, 'cfr_title'), _text(params, 'cfr_part')
    if re.fullmatch(r'\d{1,2}', cfr_title) and re.fullmatch(r'[0-9A-Za-z.\-]{1,12}', cfr_part):
        clauses.append('d.id IN (SELECT doc_id FROM doc_cfr WHERE cfr_title=? AND cfr_part=?)'); values += [int(cfr_title), cfr_part]
    elif re.fullmatch(r'\d{1,2}', cfr_title):
        clauses.append('d.id IN (SELECT doc_id FROM doc_cfr WHERE cfr_title=?)'); values.append(int(cfr_title))
    agency = _text(params, 'agency')
    if re.fullmatch(r'\d{1,5}', agency):
        clauses.append('d.id IN (SELECT doc_id FROM doc_agency WHERE agency_id=?)'); values.append(int(agency))
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            if query:
                base = 'FROM docs_fts f JOIN docs d ON d.id=f.rowid WHERE docs_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments = [query] + values
            else:
                base = 'FROM docs d' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments = values
            total = state['counts'].get('documents', 0) if not query and not clauses else connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT d.* {base} ORDER BY d.date DESC, d.id DESC LIMIT ? OFFSET ?', arguments + [limit, (page - 1) * limit]).fetchall()
            facets = {}
            for facet, value, count in connection.execute('SELECT facet, value, n FROM facet_counts'):
                facets.setdefault(facet, []).append((value, count))
            agencies = connection.execute('SELECT agency_id, name, documents FROM agencies WHERE documents>0 ORDER BY documents DESC LIMIT 120').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('index could not be read: %s' % type(error).__name__)
    filters = [
        {'name': 'q', 'label': 'Search titles, agencies, dockets, RINs, document numbers', 'type': 'search', 'placeholder': 'e.g. talc, airbag, PFAS, 0910-AI12, FDA-2019-N-1482'},
        {'name': 'type', 'label': 'Document type', 'type': 'select', 'options': [{'value': v, 'label': v, 'count': n} for v, n in sorted(facets.get('type', []), key=lambda pair: -pair[1])]},
        {'name': 'agency', 'label': 'Agency', 'type': 'select', 'options': [{'value': str(a['agency_id']), 'label': a['name'], 'count': a['documents']} for a in agencies]},
        {'name': 'cfr_title', 'label': 'CFR title', 'type': 'select', 'options': [{'value': str(v), 'label': 'Title %s' % v, 'count': n} for v, n in sorted(facets.get('cfr_title', []), key=lambda pair: int(pair[0]))]},
        {'name': 'cfr_part', 'label': 'CFR part (with a title)', 'type': 'search', 'placeholder': 'e.g. 314'},
        {'name': 'year', 'label': 'Year', 'type': 'select', 'options': [{'value': str(v), 'label': str(v), 'count': n} for v, n in sorted(facets.get('year', []), key=lambda pair: -int(pair[0]))]},
    ]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'title', 'label': 'Document'}, {'key': 'published', 'label': 'Published'}, {'key': 'type', 'label': 'Type'}, {'key': 'cfr', 'label': 'CFR parts cited'},
                        {'key': 'citation', 'label': 'Citation'}],
            'results': [_result(row) for row in rows]}


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not re.fullmatch(r'\d{1,9}', str(item_id or '')):
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM docs WHERE id=?', (int(item_id),)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    year, month, day = row['date'].split('-')
    facts = [['Document number', row['number']], ['Published in the Federal Register', row['date']], ['Type', row['type']]]
    if row['citation']:
        facts.append(['Citation', row['citation'] + ((' (pages %s)' % row['pages']) if row['pages'] else '')])
    for label, field in (('Agencies (as listed by the API)', 'agencies'), ('CFR parts cited', 'cfr'), ('Regulation Identifier Numbers (RIN)', 'rins'), ('Docket identifiers (as printed)', 'dockets')):
        values = json.loads(row[field] or '[]')
        if values:
            facts.append([label, '; '.join(str(value) for value in values)])
    if row['correction_of']:
        facts.append(['Corrects document', row['correction_of']])
    corrections = json.loads(row['corrections'] or '[]')
    if corrections:
        facts.append(['Corrected by', '; '.join(str(value) for value in corrections)])
    facts.append(['Index collected', '2026-08-20 (federalregister.gov API and GovInfo)'])
    links = [{'label': 'Open on federalregister.gov', 'url': _url(row)},
             {'label': 'Plain text (federalregister.gov)', 'url': 'https://www.federalregister.gov/documents/full_text/text/%s/%s/%s/%s.txt' % (year, month, day, row['number'])}]
    for reference in json.loads(row['cfr'] or '[]')[:6]:
        match = re.fullmatch(r'(\d+) CFR ([0-9A-Za-z.\-]+)', reference)
        if match:
            links.append({'label': 'Other documents citing %s' % reference, 'url': '#federal-register?cfr_title=%s&cfr_part=%s' % match.groups()})
    return {'title': row['title'] or row['number'], 'subtitle': ' · '.join(json.loads(row['agencies'] or '[]')[:3]), 'facts': facts, 'sections': [], 'links': links,
            'qualification': state['qualification']}


def _block(where, values, link, folder=None):
    state, _ = _state(folder)
    if state is None:
        return None
    try:
        connection = _connect(state)
        try:
            total = connection.execute(f'SELECT count(*) FROM docs d WHERE {where}', values).fetchone()[0]
            if not total:
                return None
            by_type = connection.execute(f'SELECT d.type, count(*) FROM docs d WHERE {where} GROUP BY d.type ORDER BY 2 DESC', values).fetchall()
            span = connection.execute(f'SELECT min(d.date), max(d.date) FROM docs d WHERE {where}', values).fetchone()
            rows = connection.execute(f'SELECT d.* FROM docs d WHERE {where} ORDER BY d.date DESC LIMIT 12', values).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return {'total': total, 'first_date': span[0], 'last_date': span[1], 'by_type': [{'value': value, 'count': count} for value, count in by_type],
            'results': [{'id': str(row['id']), 'title': row['title'] or row['number'], 'subtitle': '%s · %s · %s' % (row['date'], row['type'], row['citation'] or row['number'])} for row in rows],
            'link': link, 'qualification': state['qualification']}


def for_cfr(title, part, folder=None):
    if not re.fullmatch(r'\d{1,2}', str(title or '')) or not re.fullmatch(r'[0-9A-Za-z.\-]{1,12}', str(part or '')):
        return None
    return _block('d.id IN (SELECT doc_id FROM doc_cfr WHERE cfr_title=? AND cfr_part=?)', [int(title), str(part)], '#federal-register?cfr_title=%s&cfr_part=%s' % (title, part), folder)


def for_agency(agency_id, folder=None):
    if not re.fullmatch(r'\d{1,5}', str(agency_id or '')):
        return None
    return _block('d.id IN (SELECT doc_id FROM doc_agency WHERE agency_id=?)', [int(agency_id)], '#federal-register?agency=%s' % agency_id, folder)
