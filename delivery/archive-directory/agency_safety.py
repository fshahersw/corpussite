"""Fail-closed adapter for the agency safety / enforcement supplement
(``sources/agency_safety_20260919``: openFDA bulk exports, FDA.gov list exports and a
local CPSC file).

Gate: ``validation.json`` must say ``status == "passed"`` and ``ready == true`` and every
listed data file (the SQLite database, ``firm_names.jsonl``, ``edges.jsonl``,
``unresolved.jsonl``, ``files.json``) must hash to the recorded value. The database is
opened read-only for every call. Originals are served only as freshly hash-verified
bytes from ``raw/`` inside the supplement folder. Public dicts carry no file-system
paths. Firm lookups are exact normalised string equality: no entity resolution.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import unicodedata
import zlib
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/agency_safety_20260919'
DB_NAME = 'agency_safety.sqlite3'
FILES_ROUTE = '/agency-safety/files/'
REQUIRED_DATA_FILES = (DB_NAME, 'firm_names.jsonl', 'edges.jsonl', 'unresolved.jsonl', 'files.json')
TEMPORAL_FIELDS = ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to')
SERVED_MIMES = {'application/zip', 'application/json', 'text/csv',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
_SHA = re.compile(r'[a-f0-9]{64}')
_FILE_ID = re.compile(r'[A-Za-z0-9_.-]{1,80}')
_TABLE = re.compile(r'[a-z_]{1,40}')
_ISO = re.compile(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?')
_CFR = re.compile(r'^(?:cfr:(?P<t1>\d{1,2}):|(?P<t2>\d{1,2})\s*C\.?\s*F\.?\s*R\.?\s*(?:part\s*|§\s*|§\s*|section\s*)?)?(?P<part>\d{3,4})(?:\.(?P<section>\d{1,5}[a-z]?))?$', re.I)
_LOCK = threading.Lock()
_CACHE = {}


# ----------------------------------------------------------------------------- helpers
def norm_firm(name):
    """Identical to build.py: NFKC, upper, & -> AND, punctuation removed, whitespace collapsed."""
    if not isinstance(name, str):
        return ''
    text = unicodedata.normalize('NFKC', name).upper().replace('&', ' AND ')
    text = re.sub(r"['’`]", '', text)
    text = re.sub(r'[^\w\s]|_', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _stat(path):
    try:
        info = Path(path).stat()
        return info.st_size, info.st_mtime_ns
    except OSError:
        return None


def _number(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _iso_bound(value, end=False):
    """YYYY, YYYY-MM or YYYY-MM-DD -> inclusive ISO bound; None when absent; False when malformed."""
    if value is None or value == '':
        return None
    match = _ISO.fullmatch(str(value).strip())
    if not match:
        return False
    year, month, day = match.groups()
    if day:
        return '%s-%s-%s' % (year, month, day)
    if month:
        return '%s-%s-%s' % (year, month, '31' if end else '01')
    return year + ('-12-31' if end else '-01-01')


def _fts_query(q):
    tokens = [t for t in re.findall(r'[^\W_]+', q or '') if t][:16]
    if not tokens:
        return None
    return ' '.join('"%s"*' % token for token in tokens)


# ----------------------------------------------------------------------------- gate and cache
def _gate(folder):
    """Return (state dict, None) when every hash matches, else (None, reason). Never raises."""
    base = folder.resolve()
    try:
        gate_bytes = (base / 'validation.json').read_bytes()
        gate = json.loads(gate_bytes.decode('utf-8-sig'))
    except (OSError, ValueError):
        return None, 'validation_unreadable'
    if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
        return None, 'validation_not_passed'
    files = gate.get('data_files')
    if not isinstance(files, list):
        return None, 'validation_malformed'
    watched, seen = [], set()
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str):
            return None, 'validation_malformed'
        if not _SHA.fullmatch(entry['sha256']):
            return None, 'validation_malformed'
        file = (base / entry['path']).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return None, 'data_file_missing:' + entry['path']
        try:
            if _digest_file(file) != entry['sha256']:
                return None, 'data_file_hash_mismatch:' + entry['path']
        except OSError:
            return None, 'data_file_unreadable:' + entry['path']
        seen.add(file.name)
        watched.append((file, _stat(file)))
    missing = [name for name in REQUIRED_DATA_FILES if name not in seen]
    if missing:
        return None, 'data_file_not_gated:' + ','.join(missing)
    try:
        registry = json.loads((base / 'files.json').read_text(encoding='utf-8-sig'))['files']
        originals = {}
        for item in registry:
            if not isinstance(item, dict) or not _FILE_ID.fullmatch(str(item.get('file_id', ''))) or not _SHA.fullmatch(str(item.get('sha256', ''))):
                return None, 'files_registry_malformed'
            if not isinstance(item.get('path'), str) or item['file_id'] in originals:
                return None, 'files_registry_malformed'
            originals[item['file_id']] = item
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'files_registry_unreadable'
    return {'base': base, 'gate': gate, 'gate_sha256': hashlib.sha256(gate_bytes).hexdigest(), 'watched': watched, 'originals': originals}, None


def _state(folder):
    base = Path(folder if folder is not None else DATA).resolve()
    control = _stat(base / 'validation.json')
    with _LOCK:
        cached = _CACHE.get(base)
        if cached and cached['control'] == control and cached.get('state') and all(_stat(f) == s for f, s in cached['state']['watched']):
            return cached['state'], None
        state, reason = _gate(base)
        _CACHE[base] = {'control': control, 'state': state, 'reason': reason}
        return state, reason


def _connect(state):
    """Read-only connection that is closed (not merely committed) on exit."""
    return closing(sqlite3.connect((state['base'] / DB_NAME).as_uri() + '?mode=ro', uri=True))


def _rows(db, sql, params=()):
    cursor = db.execute(sql, params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


# ----------------------------------------------------------------------------- shaping
def _file_public(item):
    return {'file_id': item['file_id'], 'filename': item.get('filename'), 'sha256': item['sha256'], 'bytes': item.get('bytes'),
            'mime': item.get('mime'), 'kind': item.get('kind'), 'representation': item.get('representation'),
            'publisher_url': item.get('publisher_url'), 'final_url': item.get('final_url'), 'http_status': item.get('http_status'),
            'captured_at': item.get('captured_at'), 'url': FILES_ROUTE + item['file_id']}


def _dataset_public(row, originals):
    files = [f for f in originals.values() if f['file_id'] == row.get('file_id')]
    files += [f for f in originals.values() if row['dataset'] in (f.get('datasets') or []) and f['file_id'] != row.get('file_id')]
    try:
        date_types = json.loads(row.get('date_types') or '[]')
    except ValueError:
        date_types = []
    return {'dataset': row['dataset'], 'label': row['label'], 'publisher': row['publisher'], 'group': row['grp'], 'endpoint': row.get('endpoint'),
            'id_prefix': row['id_prefix'], 'rows': row['rows'], 'captured_at': row['captured_at'], 'captured_at_basis': row['captured_at_basis'],
            'source_as_of': row['source_as_of'], 'source_as_of_basis': row['source_as_of_basis'], 'published_at_basis': row['published_at_basis'],
            'publisher_last_updated': row['publisher_last_updated'], 'date_types': date_types, 'qualification': row['qualification'],
            'original_files': [_file_public(f) for f in files]}


def _datasets_by_id(db):
    return {row['dataset']: row for row in _rows(db, 'select * from datasets order by position')}


def _dates_for(db, rids, datasets_by_id, rows):
    found = {}
    if rids:
        marks = ','.join('?' * len(rids))
        for rid, date_type, value in db.execute('select rid, date_type, date from record_dates where rid in (%s)' % marks, rids):
            found.setdefault(rid, {})[date_type] = value
    out = {}
    for row in rows:
        try:
            declared = json.loads(datasets_by_id.get(row['dataset'], {}).get('date_types') or '[]')
        except ValueError:
            declared = []
        dates = {key: None for key in declared}
        dates.update(found.get(row['rid'], {}))
        out[row['rid']] = dates
    return out


def _summary(row, dates, datasets_by_id):
    spec = datasets_by_id.get(row['dataset'], {})
    return {'id': row['id'], 'dataset': row['dataset'], 'group': spec.get('grp'), 'native_id': row['native_id'], 'title': row['title'],
            'firm': row['firm'], 'firm_norm': row['firm_norm'], 'firm_id': ('firm:' + row['firm_norm'].lower().replace(' ', '-')) if row['firm_norm'] else None,
            'classification': row['classification'], 'status': row['status'], 'product_code': row['product_code'], 'state': row['state'],
            'country': row['country'], 'published_at': row['published_at'], 'sort_date': row['sort_date'], 'snippet': row['snippet'], 'dates': dates}


def _temporal(row, spec):
    published = row.get('published_at')
    return {'captured_at': spec.get('captured_at'), 'captured_at_basis': spec.get('captured_at_basis'),
            'source_as_of': spec.get('source_as_of'), 'source_as_of_basis': spec.get('source_as_of_basis'),
            'published_at': published, 'published_at_basis': spec.get('published_at_basis') if published else None,
            'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None}


def _closed_search(reason=None):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'pages': 0, 'results': [], 'filters': {}}


# ----------------------------------------------------------------------------- public API
def status(folder=None):
    """Path-free load report: whether the gate is open and why not."""
    state, reason = _state(folder)
    if not state:
        return {'available': False, 'reason': reason, 'datasets': 0, 'records': 0}
    gate = state['gate']
    try:
        with _connect(state) as db:
            datasets_n = db.execute('select count(*) from datasets').fetchone()[0]
            records_n = db.execute('select count(*) from record_index').fetchone()[0]
    except sqlite3.Error as error:
        return {'available': False, 'reason': 'database_error:' + type(error).__name__, 'datasets': 0, 'records': 0}
    return {'available': True, 'reason': None, 'validated_at': gate.get('validated_at'), 'datasets': datasets_n, 'records': records_n,
            'qualification': gate.get('qualification'), 'license_ref': gate.get('license_ref'), 'deviations': gate.get('deviations') or [],
            'counts': gate.get('counts') or {}}


def datasets(folder=None):
    """Every dataset with its provenance, temporal bases and original files (path-free). [] when closed."""
    state, _ = _state(folder)
    if not state:
        return []
    try:
        with _connect(state) as db:
            return [_dataset_public(row, state['originals']) for row in _rows(db, 'select * from datasets order by position')]
    except sqlite3.Error:
        return []


def search(dataset=None, q=None, firm=None, classification=None, status=None, product_code=None, date_type=None, dfrom=None, dto=None,
           page=1, limit=DEFAULT_LIMIT, folder=None):
    """Filtered, paginated, path-free search across all datasets.

    dataset: a dataset id or a group name (enforcement, applications, devices, supply, warning_letters, press_recalls, cpsc).
    q: full-text (title, firm, body); each word is a prefix; malformed input never raises.
    firm: case-insensitive substring of the normalised firm string. classification/status: case-insensitive exact.
    product_code: exact. date_type + dfrom/dto: inclusive range on that publisher date (YYYY, YYYY-MM or YYYY-MM-DD);
    without date_type the range applies to sort_date. Unknown date types match nothing.
    """
    state, reason = _state(folder)
    if not state:
        return _closed_search(reason)
    page, limit = _number(page, 1, 1, 10 ** 6), _number(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    filters = {'dataset': dataset or None, 'q': q or None, 'firm': firm or None, 'classification': classification or None, 'status': status or None,
               'product_code': product_code or None, 'date_type': date_type or None, 'dfrom': dfrom or None, 'dto': dto or None}
    low, high = _iso_bound(dfrom), _iso_bound(dto, end=True)
    if low is False or high is False:
        return dict(_closed_search(), available=True, page=page, limit=limit, filters=filters, error='dfrom/dto must be YYYY, YYYY-MM or YYYY-MM-DD')
    where, params = [], []
    try:
        with _connect(state) as db:
            specs = _datasets_by_id(db)
            if dataset:
                ids = [d for d in specs if d == dataset or specs[d]['grp'] == dataset]
                if not ids:
                    return dict(_closed_search(), available=True, page=page, limit=limit, filters=filters)
                where.append('i.dataset in (%s)' % ','.join('?' * len(ids))); params += ids
            if q:
                match = _fts_query(q)
                if not match:
                    return dict(_closed_search(), available=True, page=page, limit=limit, filters=filters)
                where.append('i.rid in (select rowid from record_fts where record_fts match ?)'); params.append(match)
            if firm:
                needle = norm_firm(firm)
                if not needle:
                    return dict(_closed_search(), available=True, page=page, limit=limit, filters=filters)
                where.append("i.firm_norm like ? escape '\\'"); params.append('%' + needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
            if classification:
                where.append('lower(i.classification) = lower(?)'); params.append(classification)
            if status:
                where.append('lower(i.status) = lower(?)'); params.append(status)
            if product_code:
                where.append('i.product_code = ?'); params.append(str(product_code).strip().upper())
            if date_type:
                where.append('i.rid in (select rid from record_dates where date_type = ? and date >= ? and date <= ?)')
                params += [date_type, low or '0000-00-00', high or '9999-99-99']
            elif low or high:
                where.append('i.sort_date >= ? and i.sort_date <= ?'); params += [low or '0000-00-00', high or '9999-99-99']
            clause = (' where ' + ' and '.join(where)) if where else ''
            total = db.execute('select count(*) from record_index i' + clause, params).fetchone()[0]
            rows = _rows(db, 'select i.* from record_index i' + clause + ' order by (i.sort_date is null), i.sort_date desc, i.rid limit ? offset ?',
                         params + [limit, (page - 1) * limit])
            dates = _dates_for(db, [r['rid'] for r in rows], specs, rows)
            results = [_summary(r, dates[r['rid']], specs) for r in rows]
    except sqlite3.OperationalError as error:
        if 'fts5' in str(error).lower() or 'syntax' in str(error).lower():
            return dict(_closed_search(), available=True, page=page, limit=limit, filters=filters)
        return _closed_search('database_error:' + type(error).__name__)
    except sqlite3.Error as error:
        return _closed_search('database_error:' + type(error).__name__)
    return {'available': True, 'reason': None, 'total': total, 'page': page, 'limit': limit, 'pages': math.ceil(total / limit) if total else 0,
            'results': results, 'filters': filters}


def record(dataset, id, folder=None):
    """One record: normalised fields, verbatim publisher record, separate dates, temporal block, children. None when absent."""
    state, _ = _state(folder)
    if not state or not isinstance(dataset, str) or not isinstance(id, str) or not id:
        return None
    try:
        with _connect(state) as db:
            specs = _datasets_by_id(db)
            spec = specs.get(dataset)
            if not spec or not _TABLE.fullmatch(spec['table_name'] or ''):
                return None
            full = id if id.startswith(spec['id_prefix']) else spec['id_prefix'] + id
            rows = _rows(db, 'select * from record_index where id = ? and dataset = ?', (full, dataset))
            if not rows:
                return None
            row = rows[0]
            fields = _rows(db, 'select * from %s where rid = ?' % spec['table_name'], (row['rid'],))
            fields = {k: v for k, v in (fields[0] if fields else {}).items() if k not in ('rid', 'dataset')}
            raw = db.execute('select raw_z from raw_records where rid = ?', (row['rid'],)).fetchone()
            publisher_record = json.loads(zlib.decompress(raw[0]).decode('utf-8')) if raw else None
            dates = _dates_for(db, [row['rid']], specs, [row])[row['rid']]
            out = dict(_summary(row, dates, specs), fields=fields, publisher_record=publisher_record, temporal=_temporal(row, spec),
                       qualification=spec['qualification'], publisher=spec['publisher'], source_as_of=spec['source_as_of'])
            if dataset == 'openfda_drugsfda':
                out['products'] = [{k: v for k, v in r.items() if k != 'parent_rid'} for r in _rows(db, 'select * from drugsfda_products where parent_rid = ? order by product_number', (row['rid'],))]
                out['submissions'] = [{k: v for k, v in r.items() if k != 'parent_rid'} for r in _rows(db, 'select * from drugsfda_submissions where parent_rid = ? order by submission_status_date, submission_type, submission_number', (row['rid'],))]
            links = {}
            if row['firm_norm']:
                links['firm_id'] = 'firm:' + row['firm_norm'].lower().replace(' ', '-')
            if fields.get('cfr_id'):
                links['cfr_id'] = fields['cfr_id']
            if fields.get('openfda_regulation_number') and re.fullmatch(r'\d{3}\.\d{1,4}[a-z]?', fields['openfda_regulation_number']):
                links['cfr_id'] = 'cfr:21:' + fields['openfda_regulation_number']
            out['links'] = links
            original = state['originals'].get(spec.get('file_id') or '')
            out['original_file'] = _file_public(original) if original else None
            return out
    except (sqlite3.Error, zlib.error, ValueError, UnicodeError):
        return None


def firm(name, folder=None, page=1, limit=DEFAULT_LIMIT):
    """Records whose normalised firm string equals norm(name) exactly. No merging beyond that."""
    page, limit = _number(page, 1, 1, 10 ** 6), _number(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    needle = norm_firm(name)
    empty = {'available': True, 'query': name, 'firm_norm': needle or None, 'firm_id': None, 'names': [], 'datasets': {}, 'total': 0, 'page': page,
             'limit': limit, 'pages': 0, 'results': [], 'merge_rule': 'exact normalised string equality only'}
    state, reason = _state(folder)
    if not state:
        return dict(empty, available=False, reason=reason)
    if not needle:
        return empty
    try:
        with _connect(state) as db:
            rows = _rows(db, 'select * from firm_names where firm_norm = ?', (needle,))
            if not rows:
                return empty
            entry = rows[0]
            specs = _datasets_by_id(db)
            total = db.execute('select count(*) from record_index where firm_norm = ?', (needle,)).fetchone()[0]
            hits = _rows(db, 'select * from record_index where firm_norm = ? order by (sort_date is null), sort_date desc, rid limit ? offset ?',
                         (needle, limit, (page - 1) * limit))
            dates = _dates_for(db, [r['rid'] for r in hits], specs, hits)
            return dict(empty, firm_id=entry['firm_id'], names=json.loads(entry['names']), datasets=json.loads(entry['datasets']), total=total,
                        pages=math.ceil(total / limit) if total else 0, results=[_summary(r, dates[r['rid']], specs) for r in hits])
    except (sqlite3.Error, ValueError):
        return dict(empty, available=False, reason='database_error')


def parse_cfr(citation):
    """'cfr:21:870.3610', '21 CFR 870.3610', '870.3610', 'cfr:21:870' or '870' -> (title, part, section|None); None if not Title 21."""
    if not isinstance(citation, str):
        return None
    match = _CFR.match(citation.strip())
    if not match:
        return None
    title = match.group('t1') or match.group('t2') or '21'
    if title != '21':
        return None
    return '21', match.group('part'), match.group('section')


def for_cfr(citation, folder=None, page=1, limit=MAX_LIMIT):
    """Device product codes the publisher classifies under a 21 CFR section (or any section of a part)."""
    page, limit = _number(page, 1, 1, 10 ** 6), _number(limit, MAX_LIMIT, 1, MAX_LIMIT)
    parsed = parse_cfr(citation)
    empty = {'available': True, 'query': citation, 'citation': None, 'title': None, 'part': None, 'section': None, 'total': 0, 'page': page, 'limit': limit,
             'pages': 0, 'classifications': [], 'product_codes': [], 'related_pma_rows': 0,
             'basis': 'publisher field regulation_number on openFDA device/classification; related PMA rows share the product code'}
    if not parsed:
        return empty
    title, part, section = parsed
    citation_id = 'cfr:21:' + (part + '.' + section if section else part)
    empty.update(citation=citation_id, title=title, part=part, section=(part + '.' + section) if section else None)
    state, reason = _state(folder)
    if not state:
        return dict(empty, available=False, reason=reason)
    try:
        with _connect(state) as db:
            specs = _datasets_by_id(db)
            clause, params = ('regulation_number = ?', [part + '.' + section]) if section else ('cfr_part = ?', [part])
            total = db.execute('select count(*) from device_classification where ' + clause, params).fetchone()[0]
            codes = [r[0] for r in db.execute('select product_code from device_classification where ' + clause + ' and product_code is not null', params)]
            rows = _rows(db, 'select i.*, c.regulation_number, c.device_class, c.device_name, c.medical_specialty_description, c.cfr_id from device_classification c '
                             'join record_index i on i.rid = c.rid where ' + clause + ' order by c.regulation_number, c.product_code limit ? offset ?',
                         params + [limit, (page - 1) * limit])
            dates = _dates_for(db, [r['rid'] for r in rows], specs, rows)
            items = []
            for r in rows:
                item = _summary(r, dates[r['rid']], specs)
                item.update(regulation_number=r['regulation_number'], device_class=r['device_class'], device_name=r['device_name'],
                            medical_specialty_description=r['medical_specialty_description'], cfr_id=r['cfr_id'])
                items.append(item)
            pma = 0
            if codes:
                pma = db.execute('select count(*) from pma where product_code in (%s)' % ','.join('?' * len(codes)), codes).fetchone()[0]
            return dict(empty, total=total, pages=math.ceil(total / limit) if total else 0, classifications=items, product_codes=sorted(set(codes)),
                        related_pma_rows=pma)
    except sqlite3.Error:
        return dict(empty, available=False, reason='database_error')


def original(file_id, folder=None):
    """(bytes, mime, filename) for a registered original, re-hashed at serve time; None otherwise."""
    state, _ = _state(folder)
    if not state or not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
        return None
    item = state['originals'].get(file_id)
    if not item:
        return None
    raw_root = (state['base'] / 'raw').resolve()
    file = (state['base'] / item['path']).resolve()
    if not file.is_relative_to(raw_root) or not file.is_file():
        return None
    try:
        payload = file.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(payload).hexdigest() != item['sha256']:
        return None
    mime = item.get('mime') if item.get('mime') in SERVED_MIMES else 'application/octet-stream'
    return payload, mime, item.get('filename') or file.name
