"""Read-only, hash-gated adapter for the unified counsel directory (sources/counsel_directory_20260919).

Generic view contract: listing(params), detail(id), plus the embedding hook for_mdl(mdl_number). Rows come
from a private firm-focused docket sample (CourtListener parties-by-docket data, one AWS release cohort of
12 plaintiff firms, a 6-MDL CourtListener search-index scrape, and the Philadelphia Complex Litigation
Center mass-tort liaison-counsel list) -- never a market-share or ranking claim, and never a natural-person
litigant name. Fail-closed on a missing/failed validation or hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/counsel_directory_20260919'
DB_NAME = 'counsel_directory.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
KINDS = ('firm', 'attorney', 'philadelphia_liaison')
_STOPWORDS = {'the', 'of', 'and', 'a', 'an', 'in', 'for', 'llp', 'llc', 'pllc', 'pc', 'pa', 'ltd', 'law',
              'firm', 'office', 'offices', 'group', 'associates', 'llp.', 'p.c.', 'p.a.'}
_LOCK = threading.Lock()
_CACHE: dict = {}


# Shown to readers; the build's full technical statement stays in validation.json and the README.
DISPLAY_QUALIFICATION = ('Firms and attorneys as they appear in the saved dockets, the saved MDL appearance records and the Philadelphia mass-tort '
                         'liaison list. Firm names are grouped only when they normalise to the same spelling; attorneys are kept apart by their '
                         'docket-system id. Counts describe the saved dockets only and are not rankings or market share. Names of individual '
                         'litigants are never shown.')


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
            elif not expected.get(DB_NAME) or _sha256(folder / DB_NAME) != expected[DB_NAME]:
                reason = 'counsel directory database does not match its recorded SHA-256'
            else:
                state = {'folder': folder, 'qualification': DISPLAY_QUALIFICATION, 'build_qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}}
        except (OSError, ValueError):
            reason = 'validation.json missing or unreadable'
        _CACHE[str(folder)] = {'signature': signature, 'state': state, 'reason': reason}
        return state, reason


def _connect(state):
    connection = sqlite3.connect(f"file:{(state['folder'] / DB_NAME).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


# ------------------------------------------------------------------------------------------------- param helpers
def _int(value, default, low, high):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _text(params, name, limit=160):
    value = (params or {}).get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:limit]


def _mdl_int(value):
    text = str(value or '').strip()
    return int(text) if re.fullmatch(r'\d{1,6}', text) else None


def _fts_query(text):
    terms = re.findall(r"[\w][\w.\-']*", text, re.UNICODE)[:8]
    filtered = [t for t in terms if t.casefold() not in _STOPWORDS]
    use = filtered or terms
    return ' '.join('"%s"' % t.replace('"', '') for t in use if t)


def _mdl_link(url_template, number):
    return url_template % number


# ------------------------------------------------------------------------------------------------------ firms
def _firm_result(row):
    roles = json.loads(row['roles_seen'] or '[]')
    badges = []
    if row['mdl_count']:
        badges.append('%d MDL%s' % (row['mdl_count'], '' if row['mdl_count'] == 1 else 's'))
    if row['variant_count'] > 1:
        badges.append('%d printed variants' % row['variant_count'])
    return {
        'id': row['id'], 'title': row['display_name'],
        'subtitle': '%d saved docket%s · %d attorney%s · %d MDL%s' % (
            row['docket_count'], '' if row['docket_count'] == 1 else 's',
            row['attorney_count'], '' if row['attorney_count'] == 1 else 's',
            row['mdl_count'], '' if row['mdl_count'] == 1 else 's'),
        'cells': {'firm': row['display_name'], 'mdls': str(row['mdl_count']), 'dockets': str(row['docket_count']),
                  'attorneys': str(row['attorney_count']), 'roles': ', '.join(roles) or 'Not stated'},
        'badges': badges[:2], 'links': [],
    }


_ID_KIND_LABEL = {'courtlistener': 'Native CourtListener id', 'aws_release_uuid': 'AWS release id (not a CourtListener id)',
                   'name_only': 'Name as printed (no native id)'}


def _attorney_result(row):
    badges = [_ID_KIND_LABEL.get(row['id_kind'], row['id_kind'])]
    if row['mdl_count']:
        badges.append('%d MDL%s' % (row['mdl_count'], '' if row['mdl_count'] == 1 else 's'))
    firm_ids = json.loads(row['firm_ids'] or '[]')
    return {
        'id': row['id'], 'title': row['display_name'],
        'subtitle': '%d appearance%s · %d saved docket%s · %d firm%s' % (
            row['appearance_count'], '' if row['appearance_count'] == 1 else 's',
            row['docket_count'], '' if row['docket_count'] == 1 else 's',
            len(firm_ids), '' if len(firm_ids) == 1 else 's'),
        'cells': {'attorney': row['display_name'], 'firms': str(len(firm_ids)), 'mdls': str(row['mdl_count']),
                  'dockets': str(row['docket_count']), 'identity': _ID_KIND_LABEL.get(row['id_kind'], row['id_kind'])},
        'badges': badges[:2], 'links': [],
    }


_SIDE_LABEL = {'plaintiff': 'Plaintiff side', 'defendant': 'Defendant side', 'unspecified': 'Side not stated'}


def _phila_result(row):
    badges = [_SIDE_LABEL.get(row['side'], row['side'] or 'Side not stated')]
    if row['program_code']:
        badges.append(row['program_code'])
    return {
        'id': row['id'], 'title': row['person_name'],
        'subtitle': '%s — %s (program %d)' % (row['role_raw'], row['program_name'], row['program_number']),
        'cells': {'program': row['program_name'], 'role': row['role_raw'], 'liaison': row['person_name'],
                  'firm': row['firm_raw'] or 'not printed'},
        'badges': badges[:2],
        'links': ([{'label': 'Source PDF (Philadelphia courts)', 'url': row['source_url']}] if row['source_url'] else []),
    }


# ---------------------------------------------------------------------------------------------------- listing
def _firm_filters(connection, values):
    role_options = connection.execute(
        "SELECT role_normalized, count(DISTINCT firm_id) n FROM appearances WHERE firm_id IS NOT NULL AND role_normalized IS NOT NULL GROUP BY role_normalized ORDER BY n DESC LIMIT 15").fetchall()
    side_options = connection.execute(
        "SELECT side, count(DISTINCT firm_id) n FROM appearances WHERE firm_id IS NOT NULL GROUP BY side ORDER BY n DESC").fetchall()
    return [
        {'name': 'q', 'label': 'Search firm names', 'type': 'search'},
        {'name': 'kind', 'label': 'Kind', 'type': 'select', 'options': [{'value': k, 'label': k.replace('_', ' ').title(), 'count': None} for k in KINDS]},
        {'name': 'mdl', 'label': 'MDL number', 'type': 'search', 'placeholder': 'e.g. 2789'},
        {'name': 'role', 'label': 'Role seen', 'type': 'select', 'options': [{'value': r['role_normalized'], 'label': r['role_normalized'], 'count': r['n']} for r in role_options]},
        {'name': 'side', 'label': 'Side', 'type': 'select', 'options': [{'value': r['side'], 'label': _SIDE_LABEL.get(r['side'], r['side']), 'count': r['n']} for r in side_options]},
    ]


def _listing_firms(connection, p, qualification):
    clauses, args = [], []
    if p['q']:
        query = _fts_query(p['q'])
        if query:
            clauses.append("firms.id IN (SELECT entity_id FROM search_fts WHERE kind='firm' AND search_fts MATCH ?)")
            args.append(query)
    if p['mdl'] is not None:
        clauses.append('firms.id IN (SELECT firm_id FROM appearances WHERE mdl_number=? AND firm_id IS NOT NULL)')
        args.append(p['mdl'])
    if p['court']:
        clauses.append('firms.id IN (SELECT ap.firm_id FROM appearances ap JOIN dockets dk ON dk.id=ap.docket_id '
                        'WHERE ap.firm_id IS NOT NULL AND dk.court=?)')
        args.append(p['court'])
    if p['role']:
        clauses.append('firms.id IN (SELECT firm_id FROM appearances WHERE firm_id IS NOT NULL AND role_normalized=?)')
        args.append(p['role'])
    if p['side']:
        clauses.append('firms.id IN (SELECT firm_id FROM appearances WHERE firm_id IS NOT NULL AND side=?)')
        args.append(p['side'])
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    total = connection.execute('SELECT count(*) FROM firms' + where, args).fetchone()[0]
    rows = connection.execute('SELECT * FROM firms' + where + ' ORDER BY docket_count DESC, appearance_count DESC, id LIMIT ? OFFSET ?',
                               args + [p['limit'], (p['page'] - 1) * p['limit']]).fetchall()
    return total, rows, [{'key': 'firm', 'label': 'Firm'}, {'key': 'mdls', 'label': 'MDLs'}, {'key': 'dockets', 'label': 'Saved dockets'},
                          {'key': 'attorneys', 'label': 'Attorneys'}, {'key': 'roles', 'label': 'Roles seen'}], [_firm_result(r) for r in rows]


def _listing_attorneys(connection, p, qualification):
    clauses, args = [], []
    if p['q']:
        query = _fts_query(p['q'])
        if query:
            clauses.append("attorneys.id IN (SELECT entity_id FROM search_fts WHERE kind='attorney' AND search_fts MATCH ?)")
            args.append(query)
    if p['mdl'] is not None:
        clauses.append('attorneys.id IN (SELECT attorney_id FROM appearances WHERE mdl_number=? AND attorney_id IS NOT NULL)')
        args.append(p['mdl'])
    if p['court']:
        clauses.append('attorneys.id IN (SELECT ap.attorney_id FROM appearances ap JOIN dockets dk ON dk.id=ap.docket_id '
                        'WHERE ap.attorney_id IS NOT NULL AND dk.court=?)')
        args.append(p['court'])
    if p['role']:
        clauses.append('attorneys.id IN (SELECT attorney_id FROM appearances WHERE attorney_id IS NOT NULL AND role_normalized=?)')
        args.append(p['role'])
    if p['side']:
        clauses.append('attorneys.id IN (SELECT attorney_id FROM appearances WHERE attorney_id IS NOT NULL AND side=?)')
        args.append(p['side'])
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    total = connection.execute('SELECT count(*) FROM attorneys' + where, args).fetchone()[0]
    rows = connection.execute('SELECT * FROM attorneys' + where + ' ORDER BY appearance_count DESC, id LIMIT ? OFFSET ?',
                               args + [p['limit'], (p['page'] - 1) * p['limit']]).fetchall()
    return total, rows, [{'key': 'attorney', 'label': 'Attorney'}, {'key': 'firms', 'label': 'Firms'}, {'key': 'mdls', 'label': 'MDLs'},
                          {'key': 'dockets', 'label': 'Saved dockets'}, {'key': 'identity', 'label': 'Identity'}], [_attorney_result(r) for r in rows]


def _listing_phila(connection, p, qualification):
    clauses, args = [], []
    if p['q']:
        clauses.append('(person_name LIKE ? OR firm_raw LIKE ? OR program_name LIKE ?)')
        like = '%' + p['q'].replace('%', '') + '%'
        args += [like, like, like]
    if p['side']:
        clauses.append('side=?')
        args.append(p['side'])
    if p['role']:
        clauses.append('role_raw LIKE ?')
        args.append('%' + p['role'].replace('%', '') + '%')
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    total = connection.execute('SELECT count(*) FROM phila_liaison' + where, args).fetchone()[0]
    rows = connection.execute('SELECT * FROM phila_liaison' + where + ' ORDER BY program_number, side, person_name LIMIT ? OFFSET ?',
                               args + [p['limit'], (p['page'] - 1) * p['limit']]).fetchall()
    return total, rows, [{'key': 'program', 'label': 'Program'}, {'key': 'role', 'label': 'Role'}, {'key': 'liaison', 'label': 'Liaison counsel'},
                          {'key': 'firm', 'label': 'Firm'}], [_phila_result(r) for r in rows]


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    kind = _text(params, 'kind').casefold() or 'firm'
    if kind not in KINDS:
        kind = 'firm'
    p = {'q': _text(params, 'q', 200), 'mdl': _mdl_int(params.get('mdl')), 'court': _text(params, 'court', 20),
         'role': _text(params, 'role', 80), 'side': _text(params, 'side', 20).casefold(),
         'page': _int(params.get('page'), 1, 1, 100000), 'limit': _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)}
    try:
        connection = _connect(state)
        try:
            if kind == 'attorney':
                total, rows, columns, results = _listing_attorneys(connection, p, state['qualification'])
            elif kind == 'philadelphia_liaison':
                total, rows, columns, results = _listing_phila(connection, p, state['qualification'])
            else:
                total, rows, columns, results = _listing_firms(connection, p, state['qualification'])
            filters = _firm_filters(connection, p)
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('counsel directory could not be read: %s' % type(error).__name__)
    return {'available': True, 'total': total, 'page': p['page'], 'limit': p['limit'],
            'qualification': state['qualification'], 'filters': filters, 'columns': columns, 'results': results}


# ----------------------------------------------------------------------------------------------------- detail
_MDL_LINK = '#mdl/%d'


def _firm_detail(connection, state, row):
    variants = connection.execute('SELECT variant_text, source, count FROM firm_variants WHERE firm_id=? ORDER BY count DESC, variant_text',
                                   (row['id'],)).fetchall()
    attorneys = connection.execute(
        'SELECT DISTINCT a.id, a.display_name, a.id_kind FROM appearances ap JOIN attorneys a ON a.id=ap.attorney_id '
        'WHERE ap.firm_id=? ORDER BY a.display_name LIMIT 100', (row['id'],)).fetchall()
    mdls = connection.execute(
        'SELECT ap.mdl_number n, count(*) c, max(ml.mdl_title) t FROM appearances ap LEFT JOIN mdl_links ml ON ml.mdl_number=ap.mdl_number '
        'WHERE ap.firm_id=? AND ap.mdl_number IS NOT NULL GROUP BY ap.mdl_number ORDER BY c DESC', (row['id'],)).fetchall()
    dockets = connection.execute(
        'SELECT DISTINCT dk.docket_number, dk.court, dk.courtlistener_url FROM appearances ap JOIN dockets dk ON dk.id=ap.docket_id '
        'WHERE ap.firm_id=? ORDER BY dk.docket_number LIMIT 100', (row['id'],)).fetchall()
    mdl_numbers = [m['n'] for m in mdls]
    leadership = []
    if mdl_numbers:
        qmarks = ','.join('?' * len(mdl_numbers))
        leadership = connection.execute(
            'SELECT mdl_number, label, date, courtlistener_url FROM leadership_links WHERE mdl_number IN (%s) '
            'ORDER BY date DESC LIMIT 15' % qmarks, mdl_numbers).fetchall()
    facts = [['Firm', row['display_name']], ['Printed variants', str(row['variant_count'])],
              ['Distinct MDLs reached', str(row['mdl_count'])], ['Saved dockets', str(row['docket_count'])],
              ['Attorneys linked (native id or AWS release id)', str(row['attorney_count'])],
              ['Roles seen', ', '.join(json.loads(row['roles_seen'] or '[]')) or 'Not stated']]
    sections = [
        {'heading': 'Printed variants (%d, grouped by identical name after removing punctuation and entity suffix)' % len(variants),
         'header': ['As printed', 'Source', 'Count'], 'rows': [[v['variant_text'], v['source'], str(v['count'])] for v in variants]},
        {'heading': 'Attorneys (%d, native CourtListener id or AWS release id where the source gives one)' % len(attorneys),
         'items': [{'title': a['display_name'], 'subtitle': _ID_KIND_LABEL.get(a['id_kind'], a['id_kind']),
                    'links': [{'label': 'Attorney detail', 'url': '#counsel?id=%s' % a['id']}]} for a in attorneys]},
        {'heading': 'MDLs (%d)' % len(mdls),
         'items': [{'title': 'MDL %d — %s' % (m['n'], m['t'] or 'title not resolved'),
                    'subtitle': '%d appearance%s' % (m['c'], '' if m['c'] == 1 else 's'),
                    'links': [{'label': 'MDL detail', 'url': _MDL_LINK % m['n']}]} for m in mdls]},
        {'heading': 'Dockets (%d saved)' % len(dockets),
         'items': [{'title': '%s (%s)' % (d['docket_number'] or 'docket number not recorded', d['court'] or 'court not recorded'),
                    'subtitle': 'Saved docket in this build',
                    'links': ([{'label': 'CourtListener docket', 'url': d['courtlistener_url']}] if d['courtlistener_url'] else [])}
                   for d in dockets]},
    ]
    if leadership:
        sections.append({'heading': 'Leadership orders on these MDLs (%d, linked only; no name is parsed from the order text)' % len(leadership),
                          'items': [{'title': '%s — MDL %d (%s)' % (l['label'], l['mdl_number'], l['date'] or 'date not recorded'),
                                     'subtitle': 'CourtListener docket entry',
                                     'links': [{'label': 'CourtListener link', 'url': l['courtlistener_url']}] if l['courtlistener_url'] else []}
                                    for l in leadership]})
    return {'id': row['id'], 'title': row['display_name'], 'subtitle': _firm_result(row)['subtitle'],
            'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': []}


def _attorney_detail(connection, state, row):
    appearances = connection.execute(
        'SELECT firm_raw, firm_id, role_raw, role_normalized, side, mdl_number, docket_id, source FROM appearances WHERE attorney_id=? LIMIT 500',
        (row['id'],)).fetchall()
    firm_seen = {}
    for a in appearances:
        if a['firm_raw']:
            firm_seen.setdefault((a['firm_raw'], a['firm_id']), 0)
            firm_seen[(a['firm_raw'], a['firm_id'])] += 1
    roles_seen = {}
    for a in appearances:
        key = (a['role_raw'], a['role_normalized'])
        roles_seen[key] = roles_seen.get(key, 0) + 1
    docket_ids = sorted({a['docket_id'] for a in appearances if a['docket_id']})
    dockets = []
    if docket_ids:
        qmarks = ','.join('?' * len(docket_ids))
        dockets = connection.execute('SELECT docket_number, court, courtlistener_url, mdl_number FROM dockets WHERE id IN (%s)' % qmarks,
                                      docket_ids).fetchall()
    facts = [['Attorney', row['display_name']], ['Identity', _ID_KIND_LABEL.get(row['id_kind'], row['id_kind'])],
              ['Appearances (this build)', str(row['appearance_count'])], ['Saved dockets', str(row['docket_count'])],
              ['Distinct MDLs', str(row['mdl_count'])]]
    sections = [
        {'heading': 'Firm(s) as printed', 'items': [
            {'title': text, 'subtitle': '%d appearance%s' % (n, '' if n == 1 else 's'),
             'links': ([{'label': 'Firm detail', 'url': '#counsel?id=%s' % fid}] if fid else [])}
            for (text, fid), n in sorted(firm_seen.items(), key=lambda kv: -kv[1])]},
        {'heading': 'Roles seen', 'header': ['Raw (as printed)', 'Normalised', 'Count'],
         'rows': [[str(raw), norm, str(n)] for (raw, norm), n in sorted(roles_seen.items(), key=lambda kv: -kv[1])]},
        {'heading': 'Dockets (%d)' % len(dockets),
         'items': [{'title': '%s (%s)' % (d['docket_number'] or 'docket number not recorded', d['court'] or 'court not recorded'),
                    'subtitle': ('MDL %d' % d['mdl_number']) if d['mdl_number'] else 'Not linked to an MDL',
                    'links': ([{'label': 'CourtListener docket', 'url': d['courtlistener_url']}] if d['courtlistener_url'] else [])}
                   for d in dockets]},
    ]
    return {'id': row['id'], 'title': row['display_name'], 'subtitle': _attorney_result(row)['subtitle'],
            'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': []}


_PHILA_QUALIFICATION = (
    'Philadelphia Complex Litigation Center Mass Tort Program liaison-counsel list, parsed from a single '
    'saved court PDF (returnedfiles). A Pennsylvania state-court list, not a federal MDL. Only the program, '
    'role, liaison name and firm name as printed are published; no address, phone or e-mail.'
)


def _phila_detail(connection, state, row):
    facts = [['Program', '%d — %s%s' % (row['program_number'], row['program_name'], ' (%s)' % row['program_code'] if row['program_code'] else '')],
              ['Role (as printed)', row['role_raw']], ['Side', _SIDE_LABEL.get(row['side'], row['side'])],
              ['Liaison counsel', row['person_name']], ['Firm (as printed)', row['firm_raw'] or 'not printed in the source'],
              ['Captured', row['captured_at'] or 'not recorded']]
    sections = []
    if row['firm_id']:
        firm = connection.execute('SELECT display_name FROM firms WHERE id=?', (row['firm_id'],)).fetchone()
        if firm:
            sections.append({'heading': 'Grouped firm', 'items': [{'title': firm['display_name'], 'subtitle': 'Same deterministic firm grouping as the rest of this directory',
                                                                     'links': [{'label': 'Firm detail', 'url': '#counsel?id=%s' % row['firm_id']}]}]})
    links = [{'label': 'Source PDF (Philadelphia courts)', 'url': row['source_url']}] if row['source_url'] else []
    return {'id': row['id'], 'title': row['person_name'], 'subtitle': _phila_result(row)['subtitle'],
            'qualification': _PHILA_QUALIFICATION, 'facts': facts, 'sections': sections, 'links': links}


_ID_RE = re.compile(r'[A-Za-z0-9_:.\-]{1,160}')


def detail(item_id, folder=None):
    state, _reason = _state(folder)
    if state is None or not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id):
        return None
    try:
        connection = _connect(state)
        try:
            if item_id.startswith('firm:'):
                row = connection.execute('SELECT * FROM firms WHERE id=?', (item_id,)).fetchone()
                return _firm_detail(connection, state, row) if row else None
            if item_id.startswith('phila:'):
                row = connection.execute('SELECT * FROM phila_liaison WHERE id=?', (item_id,)).fetchone()
                return _phila_detail(connection, state, row) if row else None
            row = connection.execute('SELECT * FROM attorneys WHERE id=?', (item_id,)).fetchone()
            return _attorney_detail(connection, state, row) if row else None
        finally:
            connection.close()
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------------------------- for_mdl hook
def for_mdl(mdl_number, folder=None):
    """Compact embedding hook (<=25 rows + total + link) for one MDL, or None. Never mixes an unqualified
    ranking claim in: counts are 'in the saved dockets as of this build', firms/attorneys are capped and
    the qualification says so."""
    state, _reason = _state(folder)
    if state is None:
        return None
    if not isinstance(mdl_number, int):
        try:
            mdl_number = int(str(mdl_number).strip())
        except (TypeError, ValueError):
            return None
    try:
        connection = _connect(state)
        try:
            link_row = connection.execute('SELECT * FROM mdl_links WHERE mdl_number=?', (mdl_number,)).fetchone()
            if link_row is None:
                return None
            firms = connection.execute(
                'SELECT f.id, f.display_name, count(*) c FROM appearances ap JOIN firms f ON f.id=ap.firm_id '
                'WHERE ap.mdl_number=? GROUP BY f.id ORDER BY c DESC LIMIT 15', (mdl_number,)).fetchall()
            by_side = connection.execute('SELECT side, count(*) c FROM appearances WHERE mdl_number=? GROUP BY side',
                                          (mdl_number,)).fetchall()
            leadership = connection.execute(
                'SELECT label, date, courtlistener_url FROM leadership_links WHERE mdl_number=? ORDER BY date DESC LIMIT 8',
                (mdl_number,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    qualification = (
        'Counsel seen for MDL %d in the saved firm-focused docket sample as of this build (%s); not a '
        'complete appearance list and never a market-share or ranking claim.' % (
            mdl_number, (link_row['resolution_basis'] or '')[:180])
    )[:400]
    return {
        'mdl_number': mdl_number, 'total_firms': link_row['firms_count'], 'total_attorneys': link_row['attorneys_count'],
        'by_side': {r['side']: r['c'] for r in by_side},
        'firms': [{'id': f['id'], 'title': f['display_name'], 'subtitle': '%s appearance%s in the saved dockets' % (f['c'], '' if f['c'] == 1 else 's'),
                   'appearance_count': f['c']} for f in firms],
        'leadership_orders': [{'title': l['label'], 'date': l['date'], 'url': l['courtlistener_url']} for l in leadership],
        'link': '#counsel?mdl=%d' % mdl_number, 'qualification': qualification,
    }
