"""Read-only, hash-gated adapter for the unified URL directory (sources/url_directory_20260919).

Generic view contract: listing(params), detail(id), plus for_state / for_agency / for_court / summary hooks. The index is a
list of addresses exactly as locally saved discovery lists printed them; nothing here was fetched, and "not saved" means only
that the exact address is not among the records this app holds. Fail-closed: a missing or failed validation, or a data file
whose SHA-256 differs, makes every function return the "not available" shape. Never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/url_directory_20260919'
DB_NAME = 'url_directory.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
_LOCK = threading.Lock()
_CACHE: dict = {}

LAYER_LABELS = {
    'federal_agency': 'Federal agencies', 'state_agency': 'State agencies', 'regulatory_text': 'Regulations & administrative codes',
    'legislature_law': 'Legislatures & statutes', 'federal_court': 'Federal courts', 'state_court': 'State courts', 'county_court': 'County courts',
    'local_government': 'Local government', 'doj_directory': 'DOJ state resource pages', 'science_toxicology': 'Toxicology & health science', 'other': 'Other',
}
KIND_LABELS = {'page': 'Web page', 'pdf': 'PDF', 'word': 'Word / RTF', 'spreadsheet': 'Spreadsheet', 'data': 'Data / API', 'archive': 'Archive',
               'slides': 'Slides', 'text': 'Plain text', 'image': 'Image', 'media': 'Audio / video', 'other': 'Other'}
AGENCY_LABELS = {
    'fda': 'FDA', 'epa': 'EPA', 'cpsc': 'CPSC', 'nhtsa': 'NHTSA', 'osha': 'OSHA', 'cms': 'CMS', 'cdc': 'CDC', 'atsdr': 'ATSDR', 'nih': 'NIH', 'ftc': 'FTC',
    'sec': 'SEC', 'cfpb': 'CFPB', 'doj': 'Department of Justice', 'dol': 'Department of Labor', 'hhs': 'HHS', 'hhs_oig': 'HHS Inspector General',
    'usda': 'USDA', 'nlrb': 'NLRB', 'ussc': 'U.S. Sentencing Commission', 'fjc': 'Federal Judicial Center', 'uscourts': 'U.S. Courts (uscourts.gov sites)',
    'scotus': 'Supreme Court', 'jpml': 'JPML', 'pacer': 'PACER', 'congress': 'Congress.gov', 'govinfo': 'GovInfo / GPO', 'federal_register': 'Federal Register',
    'ecfr': 'eCFR', 'regulations_gov': 'Regulations.gov', 'dot': 'DOT', 'faa': 'FAA', 'fmcsa': 'FMCSA', 'phmsa': 'PHMSA', 'msha': 'MSHA', 'eeoc': 'EEOC',
}
LEVEL_LABELS = {'federal': 'Federal', 'state': 'State', 'county': 'County', 'municipal': 'Municipal', 'tribal': 'Tribal'}
SAVED_LABELS = {'saved': 'Already saved in this archive', 'not_saved': 'Not saved yet'}
LIST_LABELS = {
    'deep_crawl': 'Deep crawl (agencies and courts)', 'state_admin_agency': 'Agency and state administrative-code crawl', 'pdf_directory': 'Document directory (PDF crawl)',
    'court_crawl_records': 'Court site maps and crawl logs', 'doj_state_sweep': 'DOJ state-page sweep', 'doj_state_registry': 'DOJ state resource pages',
    'state_court_url_map': 'State court URL map', 'tier1_toxicology': 'Toxicology directory', 'uscourts_crawl_1404': 'uscourts.gov crawl (saved pages)',
    'uscourts_external_links': 'Links cited by uscourts.gov',
}


def _unavailable(reason):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}


def _sha256(path: Path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _state(folder=None):
    """(state, None) when the gate is open, else (None, reason). The database is re-hashed only when its size or mtime changes."""
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
            if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'supplement not published (status/ready gate closed)'
            else:
                expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)}
                if not expected.get(DB_NAME) or _sha256(folder / DB_NAME) != expected[DB_NAME]:
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


def _text(params, name, limit=120):
    value = params.get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:limit]


def _fts_query(text):
    terms = re.findall(r'[\w][\w.\-]*', text, re.UNICODE)[:8]
    return ' '.join('"%s"' % term.replace('"', '') for term in terms)


def _facets(connection, include_noise):
    out = {}
    for facet, value, count in connection.execute('SELECT facet, value, n FROM facet_counts WHERE include_noise=?', (1 if include_noise else 0,)):
        out.setdefault(facet, []).append((value, count))
    return out


def _options(pairs, labels=None, limit=80):
    rows = sorted(((value, count) for value, count in pairs if value), key=lambda pair: (-pair[1], pair[0]))[:limit]
    return [{'value': value, 'label': (labels or {}).get(value, value), 'count': count} for value, count in rows]


def _where(params):
    clauses, values = [], []
    if _text(params, 'include_noise') not in ('1', 'true', 'yes'):
        clauses.append('u.is_noise=0')
    for name, column in (('layer', 'u.layer'), ('state', 'u.state'), ('agency', 'u.agency_key'), ('court', 'u.court_id'), ('kind', 'u.doc_kind'),
                         ('saved', 'u.saved_status'), ('level', 'u.jurisdiction_level'), ('host', 'u.host'), ('content', 'u.content_type')):
        value = _text(params, name)
        if value:
            clauses.append(f'{column}=?')
            values.append(value.lower() if name == 'host' else value)
    source_list = _text(params, 'list')
    if source_list:
        clauses.append('u.url_key IN (SELECT url_key FROM memberships WHERE source_list=?)')
        values.append(source_list)
    return clauses, values


def _result(row):
    badges = [KIND_LABELS.get(row['doc_kind'], row['doc_kind'])]
    if row['saved_status'] == 'saved':
        badges.append('Saved in archive')
    if row['content_saved_locally']:
        badges.append('Text saved locally')
    if row['n_lists'] > 1:
        badges.append('In %d lists' % row['n_lists'])
    if row['conflict']:
        badges.append('Lists disagree')
    if row['is_noise']:
        badges.append('Flagged as noise')
    place = row['state'] or ('Federal' if row['jurisdiction_level'] == 'federal' else '—')
    publisher = AGENCY_LABELS.get(row['agency_key']) or row['org_name'] or row['host']
    label = (row['label'] if 'label' in row.keys() else None) or row['title'] or row['url']
    return {'id': str(row['id']), 'title': label, 'subtitle': row['url'],
            'cells': {'layer': LAYER_LABELS.get(row['layer'], row['layer']), 'content': (row['content_type'] if 'content_type' in row.keys() else None) or '', 'place': place, 'publisher': publisher,
                      'kind': KIND_LABELS.get(row['doc_kind'], row['doc_kind'])},
            'badges': badges, 'links': [{'label': 'Open address', 'url': row['url']}]}


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    include_noise = _text(params, 'include_noise') in ('1', 'true', 'yes')
    clauses, values = _where(params)
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            if query:
                base = 'FROM urls_fts f JOIN urls u ON u.id=f.rowid WHERE urls_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments = [query] + values
                order = 'ORDER BY bm25(urls_fts), u.frontier_rank, u.host'
            else:
                base = 'FROM urls u' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments = values
                order = 'ORDER BY u.frontier_rank, u.host, u.url'
            only_noise_clause = clauses == ['u.is_noise=0'] or not clauses
            if not query and only_noise_clause:
                total = state['counts'].get('distinct_urls' if include_noise else 'distinct_urls_without_noise') or 0
            else:
                total = connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT u.* {base} {order} LIMIT ? OFFSET ?', arguments + [limit, (page - 1) * limit]).fetchall()
            facets = _facets(connection, include_noise)
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('index could not be read: %s' % type(error).__name__)
    filters = [
        {'name': 'q', 'label': 'Search titles, addresses and publishers', 'type': 'search', 'placeholder': 'e.g. PFAS, local rules, talc, fee schedule'},
        {'name': 'layer', 'label': 'Category', 'type': 'select', 'options': _options(facets.get('layer', []), LAYER_LABELS)},
        {'name': 'state', 'label': 'State', 'type': 'select', 'options': sorted(_options(facets.get('state', [])), key=lambda option: option['value'])},
        {'name': 'agency', 'label': 'Federal publisher', 'type': 'select', 'options': _options(facets.get('agency_key', []), AGENCY_LABELS)},
        {'name': 'content', 'label': 'Content', 'type': 'select', 'options': _options(facets.get('content_type', []))},
        {'name': 'level', 'label': 'Level', 'type': 'select', 'options': _options(facets.get('jurisdiction_level', []), LEVEL_LABELS)},
        {'name': 'kind', 'label': 'File type', 'type': 'select', 'options': _options(facets.get('doc_kind', []), KIND_LABELS)},
        {'name': 'saved', 'label': 'In this archive', 'type': 'select', 'options': _options(facets.get('saved_status', []), SAVED_LABELS)},
        {'name': 'list', 'label': 'Discovery list', 'type': 'select', 'options': _options(facets.get('source_list', []), LIST_LABELS)},
        {'name': 'include_noise', 'label': 'Noise', 'type': 'select', 'options': [{'value': '1', 'label': 'Include share links, trackers, calendars'}]},
    ]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'title', 'label': 'Address'}, {'key': 'layer', 'label': 'Category'}, {'key': 'content', 'label': 'Content'}, {'key': 'place', 'label': 'Place'}, {'key': 'publisher', 'label': 'Publisher'}, {'key': 'kind', 'label': 'Type'}],
            'results': [_result(row) for row in rows]}


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not re.fullmatch(r'\d{1,12}', str(item_id or '')):
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM urls WHERE id=?', (int(item_id),)).fetchone()
            if row is None:
                return None
            members = connection.execute('SELECT * FROM memberships WHERE url_key=? ORDER BY source_list LIMIT 40', (row['url_key'],)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    facts = [['Address', row['url']], ['Host', row['host']], ['Category', LAYER_LABELS.get(row['layer'], row['layer'])],
             ['File type (from the address or the list)', KIND_LABELS.get(row['doc_kind'], row['doc_kind'])]]
    if row['state']:
        facts.append(['State', '%s [%s]' % (row['state'], row['state_basis'])])
    if row['agency_key']:
        facts.append(['Federal publisher', '%s [by host]' % AGENCY_LABELS.get(row['agency_key'], row['agency_key'])])
    if row['org_name']:
        facts.append(['Publisher or list tag (as the list gives it)', row['org_name']])
    if row['court_id']:
        facts.append(['Court', '%s [%s]' % (row['court_id'], row['court_link'])])
    elif row['court_candidates']:
        facts.append(['Court', 'Not assigned: this host is the recorded website of several courts (%s)' % ', '.join(json.loads(row['court_candidates'])[:12])])
    facts.append(['In this archive', ('Saved (%s)' % row['saved_ref']) if row['saved_status'] == 'saved' else 'No record with this exact address'])
    if row['first_date']:
        facts.append(['Date recorded by the list(s)', row['first_date'] if row['first_date'] == row['last_date'] else '%s to %s' % (row['first_date'], row['last_date'])])
    noise = json.loads(row['noise'] or '[]')
    if noise:
        facts.append(['Noise flags', ', '.join(flag.replace('_', ' ') for flag in noise)])
    topics = [topic for topic in json.loads(row['topics'] or '[]')]
    sections = [{'heading': 'Discovery lists naming this address', 'header': ['List', 'Category in that list', 'State', 'Title or link text', 'Found on', 'List date'],
                 'rows': [[LIST_LABELS.get(m['source_list'], m['source_list']), LAYER_LABELS.get(m['layer'], m['layer'] or ''), m['state'] or '', (m['title'] or '')[:160],
                           m['parent_url'] or '', m['source_date'] or ''] for m in members]}]
    if topics:
        sections.append({'heading': 'Tags carried from the lists', 'items': topics[:40]})
    links = [{'label': 'Open address', 'url': row['url']}]
    if row['court_id']:
        links.append({'label': 'Court page in this archive', 'url': '#courts/' + row['court_id']})
    label = (row['label'] if 'label' in row.keys() else None) or row['title'] or row['url']
    if 'label_basis' in row.keys() and row['label_basis']:
        facts.insert(1, ['Label basis', row['label_basis']])
    return {'title': label, 'subtitle': row['host'], 'facts': facts, 'sections': sections, 'links': links, 'qualification': state['qualification']}


def _block(where, values, link, folder=None, label=None):
    state, _ = _state(folder)
    if state is None:
        return None
    try:
        connection = _connect(state)
        try:
            total = connection.execute(f'SELECT count(*) FROM urls u WHERE u.is_noise=0 AND {where}', values).fetchone()[0]
            if not total:
                return None
            layers = connection.execute(f'SELECT layer, count(*) FROM urls u WHERE u.is_noise=0 AND {where} GROUP BY layer ORDER BY 2 DESC', values).fetchall()
            kinds = connection.execute(f'SELECT doc_kind, count(*) FROM urls u WHERE u.is_noise=0 AND {where} GROUP BY doc_kind ORDER BY 2 DESC', values).fetchall()
            hosts = connection.execute(f'SELECT host, count(*) FROM urls u WHERE u.is_noise=0 AND {where} GROUP BY host ORDER BY 2 DESC LIMIT 15', values).fetchall()
            saved = connection.execute(f"SELECT count(*) FROM urls u WHERE u.is_noise=0 AND u.saved_status='saved' AND {where}", values).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return {'label': label, 'total': total, 'saved': saved,
            'by_layer': [{'value': value, 'label': LAYER_LABELS.get(value, value), 'count': count} for value, count in layers],
            'by_kind': [{'value': value, 'label': KIND_LABELS.get(value, value), 'count': count} for value, count in kinds],
            'top_hosts': [{'host': host, 'count': count} for host, count in hosts], 'link': link, 'qualification': state['qualification']}


def for_state(usps, folder=None):
    if not re.fullmatch(r'[A-Za-z]{2}', str(usps or '')):
        return None
    code = str(usps).upper()
    return _block('u.state=?', [code], '#urls?state=' + code, folder, label=code)


def for_agency(agency_key, folder=None):
    if not re.fullmatch(r'[a-z_]{2,24}', str(agency_key or '')):
        return None
    return _block('u.agency_key=?', [agency_key], '#urls?agency=' + agency_key, folder, label=AGENCY_LABELS.get(agency_key, agency_key))


def for_court(court_id, folder=None):
    if not re.fullmatch(r'[A-Za-z0-9_.:\-]{1,64}', str(court_id or '')):
        return None
    return _block('u.court_id=?', [court_id], '#urls?court=' + court_id, folder, label=court_id)


def summary(folder=None):
    state, reason = _state(folder)
    if state is None:
        return {'available': False, 'reason': reason}
    counts = state['counts']
    return {'available': True, 'validated_at': state['validated_at'], 'distinct_urls': counts.get('distinct_urls_without_noise'), 'hosts': counts.get('distinct_hosts'),
            'documents': counts.get('documents_not_page'), 'already_saved': counts.get('already_saved_in_mvp'), 'by_layer': counts.get('by_layer'),
            'frontier': counts.get('frontier'), 'qualification': state['qualification']}
