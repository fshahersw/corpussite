"""Read-only, hash-gated adapter for sources/expert_rulings_scan_20260919: a private firm's keyword/
category scan of MDL docket entries for expert-admissibility (Daubert / Federal Rule of Evidence 702)
relevance (2,035 rows).

SW-BULK-derived data: local personal-testing view only, export_allowed is false, every link goes OUT to
CourtListener (no RECAP bytes are re-hosted here). This is a scan of docket text, NOT a list of rulings --
it never states or infers a grant/deny outcome.

Caption privacy: an individual member-case caption naming a natural-person party (e.g. "Smith v. Jones")
is never displayed or linked with the name intact -- sources/expert_rulings_scan_20260919/build.py already
replaced case_name_public with NULL and scrubbed the CourtListener URL's slug for those rows; this adapter
falls back to "Docket <number> (<COURT>)" for the title and never reconstructs the original caption.

MDL identity: mdl_number_crosswalk is the ONLY field used for MDL filtering/linking (for_mdl below); it is
set exclusively from sources/mdl_docket_crosswalk_20260919's native-docket-id join. mdl_number_scanned is
shown in the detail view only, labelled as unverified.

Generic view contract: listing(params), detail(id), for_mdl(mdl_number).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/expert_rulings_scan_20260919'
DB_NAME = 'expert_rulings_scan.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
_LOCK = threading.Lock()
_CACHE: dict = {}

STOPWORDS = {'a', 'an', 'and', 'the', 'of', 'in', 'on', 'for', 'to', 'with', 'by', 'is', 'was', 'were',
             'this', 'that', 'it', 'as', 'at', 'or', 'be', 'are', 'from', 'not'}

DOC_CATEGORY_LABELS = {
    'daubert_expert': 'Daubert / expert admissibility',
    'case_management_order': 'Case management order',
    'master_complaint': 'Master complaint',
    'complaint': 'Complaint',
    'other': 'Other',
}

QUALIFICATION = (
    "Local personal-testing view; derived from a private firm dataset built from CourtListener/RECAP API "
    "data; not for redistribution. Keyword/category scan of docket entries for expert-admissibility "
    "(Daubert/FRE 702) relevance, not a list of rulings; grant/deny outcome is not determined. MDL shown "
    "only where verified via mdl_docket_crosswalk by native docket id."
)
assert len(QUALIFICATION) < 400


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
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)} if isinstance(gate, dict) else {}
            if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'supplement not published (status/ready gate closed)'
            elif gate.get('license_ref') != 'sw_bulk_private_firm_work_product' or gate.get('export_allowed') is not False:
                reason = 'license metadata missing or altered'
            elif not expected.get(DB_NAME) or _sha256(folder / DB_NAME) != expected[DB_NAME]:
                reason = 'database file does not match its recorded SHA-256'
            else:
                state = {'folder': folder, 'counts': gate.get('counts') or {}}
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


def _text(params, name, maxlen=160):
    value = (params or {}).get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:maxlen]


def _fts_query(text):
    tokens = [t for t in re.findall(r"[\w][\w.\-]*", text or '', re.UNICODE) if t.lower() not in STOPWORDS][:8]
    if not tokens:
        return None
    return ' '.join('"%s"' % t.replace('"', '') for t in tokens)


def _facet(connection, column, table='expert_scan_documents'):
    sql = f'SELECT {column} AS v, count(*) AS n FROM {table} WHERE {column} IS NOT NULL GROUP BY {column} ORDER BY {column}'
    return connection.execute(sql).fetchall()


def _display_title(row):
    if not row['case_name_redacted']:
        return row['case_name_public']
    return 'Docket %s (%s)' % (row['docket_number'] or 'unknown', (row['court'] or 'court not recorded').upper())


def _mdl_cell(row):
    if row['mdl_number_crosswalk'] is not None:
        return 'MDL %d' % row['mdl_number_crosswalk']
    if row['mdl_number_scanned']:
        return 'MDL %s (unverified)' % row['mdl_number_scanned']
    return '—'


def _result(row):
    title = _display_title(row)
    category_label = DOC_CATEGORY_LABELS.get(row['doc_category'], row['doc_category'] or 'Uncategorized')
    badges = [category_label]
    if row['case_name_redacted']:
        badges.append('Individual-case caption redacted')
    links = []
    if row['courtlistener_url']:
        links.append({'label': 'CourtListener docket entry', 'url': row['courtlistener_url']})
    if row['is_available'] and row['download_url']:
        links.append({'label': 'Document (RECAP via CourtListener)', 'url': row['download_url']})
    court = (row['court'] or '').upper() or 'court not recorded'
    return {
        'id': row['doc_uid'], 'title': title,
        'subtitle': '%s (%s) · Entered %s' % (row['docket_number'] or 'docket not recorded', court, row['date_filed'] or 'date not recorded'),
        'cells': {'date': row['date_filed'] or '—', 'docket': '%s (%s)' % (row['docket_number'] or '—', court),
                  'category': category_label, 'kind': row['kind'] or '—', 'mdl': _mdl_cell(row)},
        'badges': badges[:2],
        'links': links,
    }


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    clauses, args = [], []
    mdl = _text(params, 'mdl')
    if re.fullmatch(r'\d{1,6}', mdl):
        clauses.append('mdl_number_crosswalk=?'); args.append(int(mdl))
    court = _text(params, 'court')
    if court:
        clauses.append('lower(court)=?'); args.append(court.lower())
    year = _text(params, 'year')
    if re.fullmatch(r'\d{4}', year):
        clauses.append('year=?'); args.append(int(year))
    kind = _text(params, 'kind')
    if kind:
        clauses.append('doc_category=?'); args.append(kind)
    where_sql = ' AND '.join(clauses)
    page, limit = _int(params.get('page'), 1, 1, 10 ** 6), _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            if query:
                base = ('FROM expert_scan_fts f JOIN expert_scan_documents c ON c.rid=f.rowid WHERE expert_scan_fts MATCH ?'
                        + (' AND ' + where_sql if where_sql else ''))
                sql_args = [query] + args
            else:
                base = 'FROM expert_scan_documents c' + (' WHERE ' + where_sql if where_sql else '')
                sql_args = args
            total = connection.execute('SELECT count(*) ' + base, sql_args).fetchone()[0]
            rows = connection.execute(f'SELECT c.* {base} ORDER BY c.date_filed DESC, c.rid LIMIT ? OFFSET ?',
                                       sql_args + [limit, (page - 1) * limit]).fetchall()
            filters = [
                {'name': 'q', 'label': 'Search docket description / docket number', 'type': 'search'},
                {'name': 'mdl', 'label': 'MDL (verified)', 'type': 'select',
                 'options': [{'value': str(v), 'label': 'MDL %d' % v, 'count': n} for v, n in _facet(connection, 'mdl_number_crosswalk')]},
                {'name': 'court', 'label': 'Court', 'type': 'select',
                 'options': [{'value': v, 'label': v.upper(), 'count': n} for v, n in _facet(connection, 'court')]},
                {'name': 'year', 'label': 'Year filed', 'type': 'select',
                 'options': [{'value': str(v), 'label': str(v), 'count': n} for v, n in _facet(connection, 'year')]},
                {'name': 'kind', 'label': 'Document category (as classified by the scan)', 'type': 'select',
                 'options': [{'value': v, 'label': DOC_CATEGORY_LABELS.get(v, v), 'count': n} for v, n in _facet(connection, 'doc_category')]},
            ]
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('database could not be read: %s' % type(error).__name__)
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': QUALIFICATION,
            'filters': filters,
            'columns': [{'key': 'date', 'label': 'Entered'}, {'key': 'docket', 'label': 'Docket'}, {'key': 'category', 'label': 'Category'},
                        {'key': 'kind', 'label': 'Kind'}, {'key': 'mdl', 'label': 'MDL'}],
            'results': [_result(row) for row in rows]}


def _mdl_crosswalk_fact(mdl_number):
    """Best-effort enrichment from the sibling mdl_docket_crosswalk adapter; never raises, returns None
    if that module or its own supplement is unavailable."""
    try:
        if 'mdl_crosswalk' not in sys.modules:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mdl_crosswalk
        info = mdl_crosswalk.for_mdl(mdl_number)
    except Exception:  # noqa: BLE001 - this adapter must never fail because a sibling import misbehaved
        return None
    if not info:
        return None
    return info


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not isinstance(item_id, str) or not item_id:
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM expert_scan_documents WHERE doc_uid=?', (item_id,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    court = (row['court'] or '').upper() or 'court not recorded'
    facts = [
        ['Docket number', row['docket_number'] or 'not recorded'], ['Court', court],
        ['Case caption', row['case_name_public'] if not row['case_name_redacted'] else
         ('Redacted — %s' % (row['case_name_redacted_reason'] or 'individual-case caption'))],
        ['Entry number', str(row['entry_number']) if row['entry_number'] is not None else 'not recorded'],
    ]
    if row['attachment_number'] is not None:
        facts.append(['Attachment number', str(row['attachment_number'])])
    facts.append(['Date filed (docket entry date)', row['date_filed'] or 'not recorded'])
    facts.append(['Pages (as printed)', row['pages'] or 'not recorded'])
    facts.append(['Document category (as classified by the scan)', DOC_CATEGORY_LABELS.get(row['doc_category'], row['doc_category'] or 'not recorded')])
    facts.append(['Kind (docket entry type)', row['kind'] or 'not recorded'])
    facts.append(['Matched by (scan method)', row['matched_by'] or 'not recorded'])
    if row['mdl_number_crosswalk'] is not None:
        facts.append(['MDL number (verified via mdl_docket_crosswalk)', str(row['mdl_number_crosswalk'])])
        enrichment = _mdl_crosswalk_fact(row['mdl_number_crosswalk'])
        if enrichment:
            facts.append(['MDL status (JPML registry, via mdl_docket_crosswalk)', enrichment.get('mdl_status') or 'not recorded'])
    elif row['mdl_number_scanned']:
        facts.append(['MDL number (as scanned by the source; NOT verified via mdl_docket_crosswalk)', row['mdl_number_scanned']])
    facts.append(['High value (as flagged by the scan)', 'yes' if row['high_value'] else ('no' if row['high_value'] == 0 else 'not recorded')])
    facts.append(['Available on RECAP (as recorded by the scan)', 'yes' if row['is_available'] else ('no' if row['is_available'] == 0 else 'not recorded')])
    if row['is_free_on_pacer'] is not None:
        facts.append(['Free on PACER (as recorded)', 'yes' if row['is_free_on_pacer'] else 'no'])
    links = []
    if row['courtlistener_url']:
        links.append({'label': 'CourtListener docket entry', 'url': row['courtlistener_url']})
    if row['is_available'] and row['download_url']:
        links.append({'label': 'Document (RECAP via CourtListener)', 'url': row['download_url']})
    if row['mdl_number_crosswalk'] is not None:
        links.append({'label': 'MDL %d page' % row['mdl_number_crosswalk'], 'url': '#mdls?mdl=%d' % row['mdl_number_crosswalk']})
    return {
        'id': row['doc_uid'], 'title': _display_title(row), 'subtitle': '%s (%s)' % (row['docket_number'] or '', court),
        'qualification': QUALIFICATION, 'facts': facts,
        'sections': [{'heading': 'Docket entry description (as printed)', 'text': row['description'] or 'not recorded'}],
        'links': links,
    }


def for_mdl(mdl_number, folder=None):
    """Compact embed for an MDL page: up to 10 expert-admissibility scan hits verified to that MDL via
    mdl_docket_crosswalk, or None when the MDL has no such rows (never guessed from mdl_number_scanned)."""
    state, _ = _state(folder)
    if state is None:
        return None
    try:
        number = int(mdl_number)
    except (TypeError, ValueError):
        return None
    try:
        connection = _connect(state)
        try:
            total = connection.execute('SELECT count(*) FROM expert_scan_documents WHERE mdl_number_crosswalk=?', (number,)).fetchone()[0]
            if not total:
                return None
            rows = connection.execute('SELECT * FROM expert_scan_documents WHERE mdl_number_crosswalk=? ORDER BY date_filed DESC, rid LIMIT 10', (number,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return {'mdl_number': number, 'total': total, 'results': [_result(row) for row in rows],
            'link': '#expert-rulings?mdl=%d' % number, 'qualification': QUALIFICATION}
