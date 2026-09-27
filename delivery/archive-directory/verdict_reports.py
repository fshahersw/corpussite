"""Read-only, hash-gated adapter for verdict & settlement reports (sources/verdict_settlement_reports_20260919).

listing(params), detail(id), for_mdl(mdl_number), summary(). Rows are publisher self-reports from a
commercial verdict-ranking site (topverdict.com), never verified against a court record; case captions
are already redacted at build time (natural-person names are never stored in the data file -- see that
supplement's build.py/README for the redaction rule). Fail-closed on a missing/failed validation or hash
mismatch; never raises on bad parameters. Modeled on federal_register_history.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/verdict_settlement_reports_20260919'
DB_NAME = 'verdict_settlement_reports.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
_LOCK = threading.Lock()
_CACHE: dict = {}

RESULT_TYPE_LABELS = {'verdict': 'Verdict', 'settlement': 'Settlement', 'other': 'Other'}
AMOUNT_BAND_LABELS = {
    'not_stated': 'Not stated', 'under_1m': 'Under $1M', '1m_10m': '$1M-$10M',
    '10m_100m': '$10M-$100M', 'over_100m': 'Over $100M',
}
AMOUNT_BAND_ORDER = ['under_1m', '1m_10m', '10m_100m', 'over_100m', 'not_stated']

_ID_RE = re.compile(r'^vsr-[0-9a-fA-F]+$')

_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'he', 'in', 'is', 'it', 'its',
    'of', 'on', 'or', 'that', 'the', 'their', 'there', 'these', 'this', 'those', 'to', 'was', 'were',
    'will', 'with', 'v', 'vs', 'et', 'al', 're',
}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}


def _summary_unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'by_type': {}, 'by_state': {}, 'by_year': {}}


def _sha256(path: Path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _state(folder=None):
    folder = Path(folder) if folder else DATA
    try:
        signature = tuple((name, (folder / name).stat().st_size, (folder / name).stat().st_mtime_ns)
                           for name in ('validation.json', DB_NAME))
    except OSError:
        return None, 'supplement files missing'
    with _LOCK:
        cached = _CACHE.get(str(folder))
        if cached and cached['signature'] == signature:
            return cached['state'], cached['reason']
        state, reason = None, None
        try:
            gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
            expected = ({item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)}
                        if isinstance(gate, dict) else {})
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
    value = params.get(name) if isinstance(params, dict) else None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:160]


def _truthy(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip().lower() in ('yes', 'true', '1', 'on')


def _fts_terms(text):
    tokens = re.findall(r"[\w][\w.\-']*", text or '', re.UNICODE)
    return [t for t in tokens if len(t) > 1 and t.lower() not in _STOPWORDS][:8]


def _fts_query(text):
    terms = _fts_terms(text)
    return ' '.join('"%s"' % term.replace('"', '') for term in terms)


def _badges(row):
    label = RESULT_TYPE_LABELS.get(row['result_type'], (row['result_type'] or 'Other').capitalize() or 'Other')
    badges = [label]
    if row['mdl_number'] is not None:
        badges.append('MDL %s' % row['mdl_number'])
    else:
        try:
            tags = json.loads(row['mass_tort_tags'] or '[]')
        except (TypeError, ValueError):
            tags = []
        if tags:
            badges.append(str(tags[0]))
    return badges[:2]


def _place(value):
    """Publisher place slugs ('los-angeles') shown as words ('Los Angeles'); the stored value is unchanged."""
    small = {'of', 'and', 'the'}
    words = [w for w in str(value or '').replace('_', '-').split('-') if w]
    return ' '.join(w if (w in small and i) else w.capitalize() for i, w in enumerate(words))


def _subtitle(row):
    keys = row.keys()
    parts = [_place(row['jurisdiction']), _place(row['county']) if 'county' in keys else '', str(row['year']) if row['year'] is not None else '']
    return ' · '.join(p for p in parts if p) or row['subtitle']


def _row_links(row):
    links = []
    if row['list_url']:
        links.append({'label': 'Open the published list', 'url': row['list_url']})
    return links


def _result(row):
    return {
        'id': row['id'],
        'title': row['title'],
        'subtitle': _subtitle(row),
        'cells': {
            'amount': row['amount_raw'] if row['amount_raw'] else 'Not stated',
            'type': str(row['result_type'] or '').capitalize(),
            'area': row['primary_type'] or '',
            'state': _place(row['jurisdiction']),
            'year': row['year'],
        },
        'badges': _badges(row),
        'links': _row_links(row),
    }


def _select_options(facets, name, sort_numeric=False):
    values = facets.get(name, [])
    key = (lambda pair: int(pair[0])) if sort_numeric else (lambda pair: -pair[1])
    return [{'value': value, 'label': _place(value) if name in ('jurisdiction', 'result_type') else value, 'count': n} for value, n in sorted(values, key=key)]


def listing(params: dict, folder=None) -> dict:
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    result_type = _text(params, 'type')
    if result_type:
        clauses.append('r.result_type=?'); values.append(result_type)
    state_val = _text(params, 'state')
    if state_val:
        clauses.append('r.jurisdiction=?'); values.append(state_val)
    year_val = _text(params, 'year')
    if re.fullmatch(r'\d{4}', year_val):
        clauses.append('r.year=?'); values.append(int(year_val))
    area_val = _text(params, 'area')
    if area_val:
        clauses.append('r.primary_type=?'); values.append(area_val)
    if _truthy(params.get('mass_tort')):
        clauses.append('r.is_mass_tort=1')
    band_val = _text(params, 'amount_band')
    if band_val:
        clauses.append('r.amount_band=?'); values.append(band_val)
    mdl_val = _text(params, 'mdl')
    if re.fullmatch(r'\d{1,6}', mdl_val):
        clauses.append('r.mdl_number=?'); values.append(int(mdl_val))
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            if query:
                base = 'FROM reports_fts f JOIN reports r ON r.rowid=f.rowid WHERE reports_fts MATCH ?' + ''.join(' AND ' + c for c in clauses)
                arguments = [query] + values
                order = 'r.rowid'
            else:
                base = 'FROM reports r' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments = values
                order = 'r.amount_numeric IS NULL, r.amount_numeric DESC, r.rowid'
            total = connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT r.* {base} ORDER BY {order} LIMIT ? OFFSET ?',
                                       arguments + [limit, (page - 1) * limit]).fetchall()
            facets = {}
            for facet, value, n in connection.execute('SELECT facet, value, n FROM facet_counts'):
                facets.setdefault(facet, []).append((value, n))
            mdl_options = connection.execute(
                'SELECT mdl_number, count(*) FROM reports WHERE mdl_number IS NOT NULL GROUP BY mdl_number ORDER BY 2 DESC LIMIT 50').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('index could not be read: %s' % type(error).__name__)
    mass_tort_count = next((n for v, n in facets.get('mass_tort', []) if v == 'yes'), 0)
    filters = [
        {'name': 'q', 'label': 'Search title, firm, attorney, practice area', 'type': 'search',
         'placeholder': 'e.g. Monsanto, talc, Smith Law'},
        {'name': 'type', 'label': 'Type', 'type': 'select', 'options': _select_options(facets, 'result_type')},
        {'name': 'state', 'label': 'State', 'type': 'select',
         'options': _select_options(facets, 'jurisdiction')},
        {'name': 'year', 'label': 'Year', 'type': 'select', 'options': _select_options(facets, 'year', sort_numeric=True)},
        {'name': 'area', 'label': 'Practice area', 'type': 'select', 'options': _select_options(facets, 'primary_type')},
        {'name': 'mass_tort', 'label': 'Mass tort tagged', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Mass tort tagged only', 'count': mass_tort_count}]},
        {'name': 'amount_band', 'label': 'Amount', 'type': 'select',
         'options': [{'value': band, 'label': AMOUNT_BAND_LABELS[band],
                      'count': next((n for v, n in facets.get('amount_band', []) if v == band), 0)}
                     for band in AMOUNT_BAND_ORDER]},
        {'name': 'mdl', 'label': 'Linked MDL number', 'type': 'select',
         'options': [{'value': str(number), 'label': 'MDL %s' % number, 'count': count} for number, count in mdl_options]},
    ]
    columns = [{'key': 'amount', 'label': 'Amount'}, {'key': 'type', 'label': 'Type'},
               {'key': 'area', 'label': 'Practice area'}, {'key': 'state', 'label': 'State'},
               {'key': 'year', 'label': 'Year'}]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'],
            'filters': filters, 'columns': columns, 'results': [_result(row) for row in rows]}


def detail(item_id: str, folder=None):
    state, _ = _state(folder)
    if state is None:
        return None
    item_id = str(item_id or '').strip()
    if not _ID_RE.match(item_id):
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM reports WHERE id=?', (item_id,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    facts = [
        ['Result type', RESULT_TYPE_LABELS.get(row['result_type'], row['result_type'] or 'Not stated')],
        ['Practice area', row['primary_type'] or 'Not stated'],
    ]
    if row['full_type'] and row['full_type'] != row['primary_type']:
        facts.append(['All listed categories', row['full_type']])
    facts.append(['Jurisdiction', _place(row['jurisdiction']) or 'Not stated'])
    if row['county']:
        facts.append(['County', _place(row['county'])])
    facts.append(['Year', row['year'] if row['year'] is not None else 'Not stated'])
    facts.append(['Amount as published', row['amount_raw'] or 'Not stated'])
    try:
        tags = json.loads(row['mass_tort_tags'] or '[]')
    except (TypeError, ValueError):
        tags = []
    if tags:
        facts.append(['Mass tort tags (keyword match, not a legal characterisation)', ', '.join(tags)])
    facts.append(['National list', 'Yes' if row['national_list'] else 'No'])
    if row['scope_count']:
        facts.append(['Published list appearances (scope count)', row['scope_count']])
    try:
        firms = json.loads(row['firms'] or '[]')
    except (TypeError, ValueError):
        firms = []
    if firms:
        facts.append(['Reported by (firm)', '; '.join(firms)])
    if row['attorneys']:
        facts.append(['Reported by (attorney)', row['attorneys']])
    if row['mdl_number'] is not None:
        facts.append(['Related MDL', 'MDL %s (%s)' % (row['mdl_number'], row['mdl_registry_status'] or 'status unknown')])
    facts.append(['Status', 'Reported by the publisher; not verified against a court record'])
    links = []
    if row['list_url']:
        links.append({'label': 'Open the published list', 'url': row['list_url']})
    if row['mdl_number'] is not None:
        links.append({'label': 'Related MDL %s' % row['mdl_number'], 'url': '#mdl/%s' % row['mdl_number']})
    return {'title': row['title'], 'subtitle': _subtitle(row), 'facts': facts, 'sections': [], 'links': links,
            'qualification': state['qualification']}


def for_mdl(mdl_number, folder=None):
    state, _ = _state(folder)
    if state is None:
        return None
    try:
        number = int(mdl_number)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    try:
        connection = _connect(state)
        try:
            total = connection.execute('SELECT count(*) FROM reports WHERE mdl_number=?', (number,)).fetchone()[0]
            if not total:
                return None
            rows = connection.execute(
                'SELECT * FROM reports WHERE mdl_number=? ORDER BY amount_numeric IS NULL, amount_numeric DESC LIMIT 10',
                (number,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return {'mdl_number': number, 'total': total, 'results': [_result(row) for row in rows],
            'link': '#verdict-reports?mdl=%s' % number, 'qualification': state['qualification']}


def summary(folder=None):
    state, reason = _state(folder)
    if state is None:
        return _summary_unavailable(reason)
    try:
        connection = _connect(state)
        try:
            total = connection.execute('SELECT count(*) FROM reports').fetchone()[0]
            by_type = {row[0]: row[1] for row in connection.execute('SELECT result_type, count(*) FROM reports GROUP BY result_type')}
            by_state = {row[0]: row[1] for row in connection.execute('SELECT jurisdiction, count(*) FROM reports GROUP BY jurisdiction')}
            by_year = {str(row[0]): row[1] for row in connection.execute('SELECT year, count(*) FROM reports GROUP BY year')}
            mass_tort_total = connection.execute('SELECT count(*) FROM reports WHERE is_mass_tort=1').fetchone()[0]
            mdl_linked_total = connection.execute('SELECT count(*) FROM reports WHERE mdl_number IS NOT NULL').fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _summary_unavailable('index could not be read: %s' % type(error).__name__)
    return {'available': True, 'total': total, 'by_type': by_type, 'by_state': by_state, 'by_year': by_year,
            'mass_tort_total': mass_tort_total, 'mdl_linked_total': mdl_linked_total, 'qualification': state['qualification']}
