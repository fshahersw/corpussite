"""Read-only, hash-gated derived-facet sidecar over the directory records (sources/record_facets_20260919).

The sidecar never overwrites kind, category, title, state or payload in directory.sqlite3. It adds, per record id,
a derived category with its basis, a review state, document subtype, record type, file type, jurisdiction level,
normalised state, typed dates with bases, a display title with basis and a capture-validity flag.

Fail-closed: every call re-checks validation.json (uniform envelope, status passed, ready true) and the SHA-256
of facets.sqlite3 whenever the file signature changes. Public dicts carry no paths. Filters are validated against
allowlists; an invalid value yields no rows rather than an error.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/record_facets_20260919'
DB_NAME = 'facets.sqlite3'
SCHEMA_VERSION = '1'
MAX_LIMIT = 5000

QUALIFICATION = ('Derived facets are rule-based labels over retained source fields, saved-file evidence and explicit '
                 'payload dates; every value carries its basis. They do not certify legal currency, completeness or '
                 'content correctness. Original kind, category, title and state stay in the directory unchanged.')

LABELS = {
    'category': {'statutes': 'Statutes & codes', 'rules': 'Rules & orders', 'constitutions': 'Constitutions',
                 'regulations': 'Regulations', 'forms': 'Court documents & forms', 'guidance': 'Guides & references',
                 'directories': 'Courts & directories', 'judges': 'Judge profiles & observations', 'other': 'Other saved resources'},
    'review': {'reviewed': 'Reviewed', 'needs_content_review': 'Needs content review',
               'title_evidence_only': 'Title evidence only', 'not_applicable': 'Not applicable'},
    'rtype': {'law_provision': 'Law provision', 'law_document': 'Law document', 'court_rule': 'Court rule', 'order': 'Order',
              'form': 'Form', 'court_document': 'Court document', 'directory_page': 'Directory page', 'county_page': 'County page',
              'judge_observation': 'Judge observation', 'judge_profile': 'Judge profile', 'source_reference': 'Source reference',
              'guidance': 'Guide or reference', 'notice': 'Notice', 'other': 'Other'},
    'ftype': {'pdf': 'PDF', 'html': 'HTML page', 'docx': 'Word (DOCX)', 'doc': 'Word (DOC)', 'xml': 'XML', 'txt': 'Plain text',
              'json': 'JSON', 'jsonl': 'JSON lines', 'csv': 'CSV', 'xlsx': 'Excel', 'zip': 'ZIP', 'gz': 'GZIP', 'rtf': 'RTF',
              'other': 'Other', 'none': 'No saved file'},
    'jur_level': {'federal': 'Federal', 'state': 'State', 'county': 'County', 'territory': 'Territory', 'multi': 'Multi-state (flagged)',
                  'unknown': 'Unknown'},
    'validity': {'ok': 'Capture OK', 'parked_redirect': 'Parked domain / redirect', 'redirect_stub': 'Redirect stub',
                 'spa_shell': 'Script shell (no content)', 'error_page': 'Error page', 'challenge': 'Access challenge',
                 'empty': 'Empty capture', 'no_capture': 'No capture (link only)'},
    'dtype': {'original_document': 'Original document', 'saved_page': 'Saved page', 'structured_provision': 'Structured provision',
              'source_observation': 'Source observation', 'profile': 'Profile', 'observed_link': 'Observed link',
              'dataset_row': 'Dataset row', 'provider_capture': 'Provider capture'},
    'subtype': {'form': 'Form', 'order': 'Order', 'rule': 'Rule', 'instructions_guide': 'Instructions / guide',
                'fee_schedule': 'Fee schedule', 'jury_instruction': 'Jury instruction', 'notice': 'Notice', 'other': 'Other'},
}
DATE_TYPES = {'saved': ('saved_lo', 'saved_hi', 'saved_at'), 'captured': ('saved_lo', 'saved_hi', 'captured_at'),
              'source_as_of': ('source_lo', 'source_hi', 'source_as_of'), 'published': ('published_lo', 'published_hi', 'published_at'),
              'effective': ('effective_lo', 'effective_hi', 'effective_from')}
COLUMN_OF = {'ftype': 'file_type', 'rtype': 'record_type', 'review': 'review_state', 'jur_level': 'jurisdiction_level',
             'validity': 'validity', 'dtype': 'representation', 'subtype': 'doc_subtype'}
_ALIAS = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_DATASET = re.compile(r'[a-z0-9_]{1,40}')
_LOCK = threading.Lock()
_CACHE = {}


def reset_cache():
    with _LOCK:
        _CACHE.clear()


def _digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(path):
    try:
        info = path.stat()
        return info.st_size, info.st_mtime_ns
    except OSError:
        return None


def _verify(folder):
    """Return (db path, gate dict) or (None, reason). Hash is recomputed when either file signature changes."""
    gate_file, db_file = folder / 'validation.json', folder / DB_NAME
    signature = (str(folder), _signature(gate_file), _signature(db_file))
    with _LOCK:
        cached = _CACHE.get('verify')
        if cached and cached[0] == signature:
            return cached[1]
    try:
        gate = json.loads(gate_file.read_text(encoding='utf-8'))
        result = (None, 'validation_gate_failed')
        if isinstance(gate, dict) and gate.get('status') == 'passed' and gate.get('ready') is True and gate.get('schema_version') == '1':
            pins = [x for x in gate.get('data_files') or [] if isinstance(x, dict) and x.get('path') == DB_NAME]
            if len(pins) == 1 and isinstance(pins[0].get('sha256'), str) and db_file.is_file():
                if _digest_file(db_file) == pins[0]['sha256']:
                    con = sqlite3.connect(db_file.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
                    try:
                        meta = dict(con.execute('SELECT key, value FROM meta'))
                        rows = con.execute('SELECT count(*) FROM facets').fetchone()[0]
                    finally:
                        con.close()
                    if meta.get('schema_version') == SCHEMA_VERSION and int(meta.get('records', -1)) == rows == int(pins[0].get('rows', -2)):
                        result = (db_file, {'gate': gate, 'meta': meta, 'rows': rows})
                    else:
                        result = (None, 'sidecar_identity_mismatch')
                else:
                    result = (None, 'sidecar_hash_mismatch')
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, UnicodeError):
        result = (None, 'sidecar_unreadable_or_malformed')
    with _LOCK:
        _CACHE['verify'] = (signature, result)
    return result


def _open():
    path, info = _verify(DATA)
    if path is None:
        return None, None
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    return con, info


def status():
    """Path-free readiness report."""
    path, info = _verify(DATA)
    if path is None:
        return {'ready': False, 'reason': info, 'records': 0, 'built_at': None, 'schema_version': None, 'qualification': QUALIFICATION}
    meta = info['meta']
    return {'ready': True, 'reason': None, 'records': info['rows'], 'built_at': meta.get('built_at'),
            'schema_version': meta.get('schema_version'), 'directory_records': meta.get('directory_records'),
            'validated_at': info['gate'].get('validated_at'), 'counts': info['gate'].get('counts') or {},
            'qualification': QUALIFICATION}


def db_path():
    """Verified sqlite path for a read-only ATTACH by the server, or None when the gate fails. Never expose to clients."""
    path, _ = _verify(DATA)
    return str(path) if path else None


def _number(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _date(value):
    if not isinstance(value, str) or not _DATE.fullmatch(value.strip()):
        return None
    try:
        dt.date.fromisoformat(value.strip())
    except ValueError:
        return None
    return value.strip()


def where_clause(filters, alias='f', skip=None):
    """(sql fragment, args) over the facets table. '1' with no filters; '0' when any value is invalid.
    Category uses multi-membership (facet_categories). `skip` omits one filter key (for except-self facet counts)."""
    if alias is not None and not _ALIAS.fullmatch(alias):
        raise ValueError('Invalid SQL alias')
    prefix = alias + '.' if alias else ''
    filters = filters if isinstance(filters, dict) else {}
    parts, args = [], []
    for key, column in COLUMN_OF.items():
        value = filters.get(key)
        if key == skip or value in (None, ''):
            continue
        if not isinstance(value, str) or value not in LABELS[key]:
            return '0', []
        parts.append(f'{prefix}{column}=?'); args.append(value)
    category = filters.get('category')
    if category not in (None, '') and skip != 'category':
        if not isinstance(category, str) or category not in LABELS['category']:
            return '0', []
        parts.append(f'{prefix}record_id IN (SELECT record_id FROM facet_categories WHERE category=?)'); args.append(category)
    state = filters.get('state')
    if state not in (None, '') and skip != 'state':
        if not isinstance(state, str) or len(state) > 60:
            return '0', []
        parts.append(f'{prefix}state=? COLLATE NOCASE'); args.append(state)
    dataset = filters.get('dataset')
    if dataset not in (None, '') and skip != 'dataset':
        if not isinstance(dataset, str) or not _DATASET.fullmatch(dataset):
            return '0', []
        parts.append(f'{prefix}dataset=?'); args.append(dataset)
    date_type = filters.get('date_type')
    if date_type not in (None, '') and skip != 'date':
        if date_type not in DATE_TYPES:
            return '0', []
        lo, hi, value_column = DATE_TYPES[date_type]
        undated = str(filters.get('undated', '1')) != '0'
        dfrom, dto = filters.get('dfrom'), filters.get('dto')
        if (dfrom not in (None, '') and not _date(dfrom)) or (dto not in (None, '') and not _date(dto)):
            return '0', []
        known = [f'{prefix}{value_column} IS NOT NULL']
        if dfrom not in (None, ''):
            known.append(f'{prefix}{hi}>=?'); args.append(_date(dfrom))
        if dto not in (None, ''):
            known.append(f'{prefix}{lo}<=?'); args.append(_date(dto))
        clause = '(' + ' AND '.join(known) + ')'
        parts.append(f'({clause} OR {prefix}{value_column} IS NULL)' if undated else clause)
    return (' AND '.join(parts) if parts else '1'), args


def ids_for(filters, limit=None, offset=0):
    """Record ids matching the filters, ordered by record id. limit None returns every match (bounded by MAX_LIMIT pages)."""
    con, _ = _open()
    if con is None:
        return []
    try:
        sql, args = where_clause(filters, alias='f')
        if sql == '0':
            return []
        query = 'SELECT f.record_id FROM facets f WHERE ' + sql + ' ORDER BY f.record_id'
        if limit is not None:
            query += ' LIMIT ? OFFSET ?'; args = args + [_number(limit, 50, 1, MAX_LIMIT), _number(offset, 0, 0, 10 ** 9)]
        return [row[0] for row in con.execute(query, args)]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _facet(con, column, filters, skip, multi=False):
    sql, args = where_clause(filters, alias='f', skip=skip)
    if sql == '0':
        return []
    if multi:
        rows = con.execute('SELECT c.category AS v, count(*) AS n FROM facet_categories c JOIN facets f ON f.record_id=c.record_id '
                           'WHERE ' + sql + ' GROUP BY c.category', args)
    else:
        rows = con.execute(f'SELECT f.{column} AS v, count(*) AS n FROM facets f WHERE ' + sql + f' AND f.{column} IS NOT NULL GROUP BY f.{column}', args)
    return [(r['v'], r['n']) for r in rows]


def facet_counts(filters):
    """Counts per facet value computed with every filter except the facet's own (users never select into zero rows)."""
    con, _ = _open()
    if con is None:
        return {'ready': False, 'total': 0, 'facets': {}, 'dates_coverage': {}, 'qualification': QUALIFICATION}
    try:
        sql, args = where_clause(filters, alias='f')
        total = 0 if sql == '0' else con.execute('SELECT count(*) FROM facets f WHERE ' + sql, args).fetchone()[0]
        result = {}
        for key in ('category', 'ftype', 'rtype', 'review', 'jur_level', 'validity', 'dtype', 'subtype'):
            column = COLUMN_OF.get(key, 'derived_category')
            labels = LABELS[key]
            counts = dict(_facet(con, column, filters, key, multi=(key == 'category')))
            result[key] = [{'value': v, 'label': labels.get(v, v), 'count': counts[v]} for v in labels if v in counts]
        result['state'] = [{'value': v, 'label': v, 'count': n} for v, n in sorted(_facet(con, 'state', filters, 'state'))]
        result['dataset'] = [{'value': v, 'label': v, 'count': n} for v, n in sorted(_facet(con, 'dataset', filters, 'dataset'))]
        coverage = {}
        base_sql, base_args = where_clause(filters, alias='f', skip='date')
        for name, (_, _, column) in DATE_TYPES.items():
            if base_sql == '0':
                coverage[name] = {'known': 0, 'unknown': 0}; continue
            known, unknown = con.execute(f'SELECT sum(f.{column} IS NOT NULL), sum(f.{column} IS NULL) FROM facets f WHERE ' + base_sql, base_args).fetchone()
            coverage[name] = {'known': known or 0, 'unknown': unknown or 0}
        return {'ready': True, 'total': total, 'facets': result, 'dates_coverage': coverage, 'qualification': QUALIFICATION}
    except sqlite3.Error:
        return {'ready': False, 'total': 0, 'facets': {}, 'dates_coverage': {}, 'qualification': QUALIFICATION}
    finally:
        con.close()


def _json(value, default):
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def _public(row):
    d = dict(row)
    dates = {}
    for name, (value_key, basis_key) in {'saved_at': ('saved_at', 'saved_at_basis'), 'captured_at': ('captured_at', 'captured_at_basis'),
                                         'source_as_of': ('source_as_of', 'source_as_of_basis'), 'published_at': ('published_at', 'published_at_basis'),
                                         'effective_from': ('effective_from', 'effective_from_basis'), 'effective_to': ('effective_to', 'effective_to_basis')}.items():
        dates[name] = {'value': d.get(value_key), 'basis': d.get(basis_key) if d.get(value_key) is not None else None}
    dates['saved_at']['field'] = d.get('saved_at_field') if d.get('saved_at') else None
    category = d.get('derived_category')
    return {
        'record_id': d['record_id'], 'dataset': d.get('dataset'), 'group': d.get('group_name'),
        'original_kind': d.get('original_kind'), 'base_kind': d.get('base_kind'), 'kind_flags': _json(d.get('kind_flags'), []),
        'derived_category': category, 'category_label': LABELS['category'].get(category, category),
        'categories': _json(d.get('categories'), []), 'category_basis': d.get('category_basis'),
        'review_state': d.get('review_state'), 'review_label': LABELS['review'].get(d.get('review_state'), d.get('review_state')),
        'review_basis': d.get('review_basis'),
        'doc_subtype': d.get('doc_subtype'), 'subtype_basis': d.get('subtype_basis'),
        'record_type': d.get('record_type'), 'record_type_label': LABELS['rtype'].get(d.get('record_type'), d.get('record_type')),
        'record_type_basis': d.get('record_type_basis'),
        'representation': d.get('representation'), 'representation_basis': d.get('representation_basis'),
        'file_type': d.get('file_type'), 'file_type_label': LABELS['ftype'].get(d.get('file_type'), d.get('file_type')),
        'file_type_basis': d.get('file_type_basis'),
        'has_original': bool(d.get('has_original')), 'has_text': bool(d.get('has_text')), 'text_chars': d.get('text_chars'),
        'bytes': d.get('bytes'), 'bytes_basis': d.get('bytes_basis'), 'original_sha256': d.get('original_sha256'),
        'jurisdiction_level': d.get('jurisdiction_level'), 'jurisdiction_basis': d.get('jurisdiction_basis'),
        'state': d.get('state'), 'states': _json(d.get('states'), []), 'multi_state': bool(d.get('multi_state')),
        'court_label_as_published': d.get('court_label_as_published'),
        'dates': dates, 'publisher_status': d.get('publisher_status'), 'publisher_status_basis': d.get('publisher_status_basis'),
        'title': d.get('title'), 'display_title': d.get('display_title'), 'title_basis': d.get('title_basis'),
        'title_issue': d.get('title_issue'), 'title_evidence': d.get('title_evidence'),
        'validity': d.get('validity'), 'validity_label': LABELS['validity'].get(d.get('validity'), d.get('validity')),
        'validity_reason': d.get('validity_reason'), 'capture_flags': _json(d.get('capture_flags'), []),
        'labels': {'source': d.get('label_source'), 'court_id': d.get('court_id'), 'county_geoid': d.get('label_county_geoid'),
                   'law_body_class': d.get('law_body_class'), 'rule_set': d.get('rule_set'), 'topics': _json(d.get('topics'), []),
                   'doc_subtype': d.get('label_doc_subtype')},
        'qualification': QUALIFICATION,
    }


def facets_for(record_id):
    """Path-free facet dict for one directory record id, or None."""
    if not isinstance(record_id, str) or not record_id:
        return None
    con, _ = _open()
    if con is None:
        return None
    try:
        row = con.execute('SELECT * FROM facets WHERE record_id=?', (record_id,)).fetchone()
        return _public(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def excluded_records(validity=None, limit=None, offset=0):
    """Records whose capture validity is not ok (shell, parked, empty, link-only), ordered by record id."""
    con, _ = _open()
    if con is None:
        return []
    try:
        sql, args = "validity!='ok'", []
        if validity not in (None, ''):
            if validity not in LABELS['validity'] or validity == 'ok':
                return []
            sql, args = 'validity=?', [validity]
        query = 'SELECT record_id, dataset, validity, validity_reason, capture_flags, original_sha256, state, display_title FROM facets WHERE ' + sql + ' ORDER BY record_id'
        if limit is not None:
            query += ' LIMIT ? OFFSET ?'; args = args + [_number(limit, 50, 1, MAX_LIMIT), _number(offset, 0, 0, 10 ** 9)]
        return [{'record_id': r['record_id'], 'dataset': r['dataset'], 'validity': r['validity'],
                 'validity_label': LABELS['validity'].get(r['validity'], r['validity']), 'reason': r['validity_reason'],
                 'capture_flags': _json(r['capture_flags'], []), 'original_sha256': r['original_sha256'], 'state': r['state'],
                 'display_title': r['display_title']} for r in con.execute(query, args)]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def excluded_ids():
    """Tuple of record ids that must leave eligible results and county joins."""
    return tuple(x['record_id'] for x in excluded_records())


def wrong_county_joins(geoid=None):
    """record_counties rows that should not be trusted (shell captures joined through shared raw-file hashes)."""
    con, _ = _open()
    if con is None:
        return []
    try:
        if geoid not in (None, ''):
            if not isinstance(geoid, str) or not re.fullmatch(r'\d{5}', geoid):
                return []
            rows = con.execute('SELECT * FROM wrong_county_joins WHERE geoid=? ORDER BY record_id', (geoid,))
        else:
            rows = con.execute('SELECT * FROM wrong_county_joins ORDER BY record_id, geoid')
        return [{'record_id': r['record_id'], 'geoid': r['geoid'], 'reason': r['reason'], 'original_sha256': r['original_sha256']} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()
