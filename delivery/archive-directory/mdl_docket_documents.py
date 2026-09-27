"""Read-only, hash-gated adapter for the MDL master-docket document paper trail
(sources/mdl_docket_documents_20260919).

Generic view contract: listing(params), detail(id). Embedding hook: for_mdl(mdl_number). No
original() — no document bytes are stored or served here; every link goes OUT to
CourtListener/RECAP (source of the underlying dataset per SW-BULK/README.md).

Fails closed: if validation.json is missing, is not status=="passed"/ready==true, or any data-file
hash or row count differs, listing() returns the "not available" shape and detail()/for_mdl() return
None. Nothing raises. Public dicts carry no filesystem paths.

License: derived from a private firm dataset (sw_bulk_private_firm_work_product); local
personal-testing view only, not for redistribution. See sources/mdl_docket_documents_20260919/README.md.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/mdl_docket_documents_20260919'
FILES = ('documents.jsonl', 'unresolved.jsonl')
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
_LOCK = threading.Lock()
_CACHE: dict = {}

DOC_TYPE_LABELS = {
    'settlement': 'Settlement', 'bellwether': 'Bellwether', 'leadership_appointment': 'Leadership appointment',
    'daubert_expert': 'Daubert / expert', 'dispositive': 'Dispositive motion', 'pretrial_order': 'Pretrial order',
    'case_management_order': 'Case management order', 'other': 'Other',
}
DOC_TYPE_ORDER = ('settlement', 'bellwether', 'leadership_appointment', 'daubert_expert', 'dispositive',
                   'pretrial_order', 'case_management_order', 'other')

# for_mdl() is an embedding block (spec: qualification under 500 chars); the full slice
# qualification is 594 chars and belongs on listing()/detail() only. 292 chars.
FOR_MDL_QUALIFICATION = (
    'Master-docket documents from a private firm dataset built on CourtListener/RECAP (captured '
    '2026-08-07); local testing view, not for redistribution. doc_type is a classifier over raw '
    'docket text. Dates are docket entry filing dates. Links go out to CourtListener; no document '
    'bytes are served.'
)


# ----------------------------------------------------------------------------- loading
def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(folder: Path):
    sig = []
    for name in ('validation.json',) + FILES:
        try:
            st = (folder / name).stat()
        except OSError:
            return None
        sig.append((name, st.st_size, st.st_mtime_ns))
    return (str(folder),) + tuple(sig)


def _verify(folder: Path):
    """Return (state, None) when the gate is open, else (None, reason). Never raises."""
    try:
        gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'validation.json missing or unreadable'
    if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
        return None, 'supplement not published (status/ready gate closed)'
    files = gate.get('data_files')
    if not isinstance(files, list):
        return None, 'validation.json has no data_files list'
    base = folder.resolve()
    seen = {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str):
            return None, 'malformed data_files entry'
        file = (folder / entry['path']).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return None, 'data file outside the supplement or missing'
        try:
            data = file.read_bytes()
        except OSError:
            return None, 'data file unreadable'
        if _digest(data) != entry['sha256']:
            return None, 'hash mismatch: %s' % Path(entry['path']).name
        rows_expected = entry.get('rows')
        try:
            lines = [line for line in data.decode('utf-8').splitlines() if line.strip()]
        except UnicodeDecodeError:
            return None, 'data file is not valid utf-8'
        if isinstance(rows_expected, int) and len(lines) != rows_expected:
            return None, 'row count mismatch: %s' % Path(entry['path']).name
        seen[Path(entry['path']).name] = lines
    missing = [name for name in FILES if name not in seen]
    if missing:
        return None, 'data files not registered in validation.json: %s' % ', '.join(missing)
    try:
        docs = [json.loads(line) for line in seen['documents.jsonl']]
    except ValueError:
        return None, 'documents.jsonl is not valid JSON lines'
    by_id = {}
    for row in docs:
        rid = row.get('id')
        if not isinstance(rid, str) or rid in by_id:
            return None, 'documents.jsonl identity problem (missing or duplicate id)'
        by_id[rid] = row
    by_mdl: dict = {}
    for row in docs:
        if row.get('resolved') and isinstance(row.get('mdl_number'), int):
            by_mdl.setdefault(row['mdl_number'], []).append(row)
    for rows in by_mdl.values():
        rows.sort(key=lambda r: r.get('entry_date_filed') or '', reverse=True)
    state = {
        'gate': gate, 'rows': docs, 'by_id': by_id, 'by_mdl': by_mdl,
        'qualification': gate.get('qualification') or '',
        'filters': _build_filters(docs),
    }
    return state, None


def _load():
    with _LOCK:
        folder = DATA
        sig = _signature(folder)
        cached = _CACHE.get('entry')
        if sig is not None and cached and cached[0] == sig:
            return cached[1], cached[2]
        state, reason = _verify(folder)
        _CACHE['entry'] = (sig, state, reason)
        return state, reason


# ----------------------------------------------------------------------------- helpers
def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _int(value, default, low, high):
    number = _as_int(value)
    if number is None or number < low:
        return default
    return min(number, high)


def _year(row):
    d = row.get('entry_date_filed') or ''
    return d[:4] if len(d) >= 4 and d[:4].isdigit() else None


def _build_filters(rows):
    mdl_counts = Counter(r['mdl_number'] for r in rows if r.get('resolved') and isinstance(r.get('mdl_number'), int))
    mdl_titles = {r['mdl_number']: r.get('mdl_title') for r in rows if r.get('resolved')}
    doc_type_counts = Counter(r.get('doc_type') for r in rows)
    year_counts = Counter(y for r in rows if (y := _year(r)))
    has_free_yes = sum(1 for r in rows if r.get('download_url') and r.get('sha1'))
    return [
        {'name': 'q', 'label': 'Search docket number / court / MDL', 'type': 'search'},
        {'name': 'mdl', 'label': 'MDL', 'type': 'select',
         'options': [{'value': str(n), 'label': '%d - %s' % (n, mdl_titles.get(n) or ''), 'count': c}
                     for n, c in sorted(mdl_counts.items())]},
        {'name': 'doc_type', 'label': 'Document type', 'type': 'select',
         'options': [{'value': t, 'label': DOC_TYPE_LABELS.get(t, t), 'count': doc_type_counts[t]}
                     for t in DOC_TYPE_ORDER if doc_type_counts.get(t)]},
        {'name': 'year', 'label': 'Entry filed year', 'type': 'select',
         'options': [{'value': y, 'label': y, 'count': n} for y, n in sorted(year_counts.items(), reverse=True)]},
        {'name': 'has_free_document', 'label': 'Free document available', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Download link + hash recorded (RECAP, as captured)', 'count': has_free_yes},
                     {'value': 'no', 'label': 'No download link', 'count': len(rows) - has_free_yes}]},
        {'name': 'dfrom', 'label': 'Entry filed from', 'type': 'date'},
        {'name': 'dto', 'label': 'Entry filed to', 'type': 'date'},
    ]


def _matches(row, p):
    if p['mdl'] is not None and row.get('mdl_number') != p['mdl']:
        return False
    if p['doc_type'] and row.get('doc_type') != p['doc_type']:
        return False
    if p['year'] and _year(row) != p['year']:
        return False
    if p['has_free_document'] in ('yes', 'no'):
        has = bool(row.get('download_url') and row.get('sha1'))
        if has != (p['has_free_document'] == 'yes'):
            return False
    if p['dfrom'] or p['dto']:
        filed = row.get('entry_date_filed') or ''
        if not filed or (p['dfrom'] and filed < p['dfrom']) or (p['dto'] and filed > p['dto']):
            return False
    if p['q']:
        # Party-name rule: the search index never includes case_name or the verbatim docket text
        # (both routinely name individual plaintiffs) -- only docket identity and classification,
        # so this filter cannot be used as a person lookup. See README.md "Dates and labels".
        hay = ' '.join([
            row.get('docket_number') or '', row.get('court') or '',
            str(row.get('mdl_number') or ''), row.get('mdl_title') or '',
            DOC_TYPE_LABELS.get(row.get('doc_type'), row.get('doc_type') or ''),
        ]).lower()
        if not all(term in hay for term in p['q'].lower().split()):
            return False
    return True


def _badges(row):
    badges = [DOC_TYPE_LABELS.get(row.get('doc_type'), row.get('doc_type') or '')]
    if not row.get('resolved'):
        badges.append('Not linked to a registered MDL')
    if row.get('mdl_status') == 'terminated':
        badges.append('MDL terminated')
    if row.get('is_sealed'):
        badges.append('Sealed')
    if row.get('is_exact_duplicate'):
        badges.append('Exact duplicate of another catalog row')
    return [b for b in badges if b]


def _links(row):
    links = []
    if row.get('download_url'):
        links.append({'label': 'Document on CourtListener/RECAP (external)', 'url': row['download_url']})
    if row.get('courtlistener_url'):
        links.append({'label': 'Docket entry on CourtListener (external)', 'url': row['courtlistener_url']})
    if row.get('resolved') and isinstance(row.get('mdl_number'), int):
        links.append({'label': 'MDL %d page' % row['mdl_number'], 'url': '#mdl/%d' % row['mdl_number']})
    return links


def _title(row):
    """Display title: docket identity only, never a party name (party-name rule -- individual
    member case captions must never surface as a title; case_name itself is already suppressed
    at build time for anything but an 'In re' master-docket caption)."""
    base = '%s - %s' % (row.get('docket_number') or '', row.get('court') or '')
    base = base.strip(' -') or row['id']
    if row.get('resolved') and row.get('mdl_title'):
        return '%s (%s)' % (base, row['mdl_title'])
    return base


def _subtitle(row):
    parts = [row.get('docket_number') or '', row.get('court') or '']
    if row.get('resolved'):
        parts.append('MDL %s (%s)' % (row.get('mdl_number'), row.get('mdl_status') or 'unknown status'))
    else:
        parts.append('not linked to a registered MDL')
    parts.append('entry filed %s' % (row.get('entry_date_filed') or 'unknown'))
    return ' - '.join(p for p in parts if p)


def _compact(row):
    return {
        'id': row['id'], 'entry_date_filed': row.get('entry_date_filed'), 'doc_type': row.get('doc_type'),
        'has_free_document': bool(row.get('download_url') and row.get('sha1')),
    }


# ----------------------------------------------------------------------------- public API
def listing(params: dict) -> dict:
    state, reason = _load()
    if state is None:
        return {'available': False, 'reason': reason}
    params = params or {}

    def text(name):
        value = params.get(name)
        return value.strip() if isinstance(value, str) else ''

    def iso_date(value):
        value = value.strip() if isinstance(value, str) else ''
        return value if len(value) == 10 and value[4] == '-' and value[7] == '-' else None

    p = {'q': text('q')[:200], 'mdl': _as_int(params.get('mdl')), 'doc_type': text('doc_type'),
         'year': text('year'), 'has_free_document': text('has_free_document').lower(),
         'dfrom': iso_date(params.get('dfrom')), 'dto': iso_date(params.get('dto'))}
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)
    matched = [row for row in state['rows'] if _matches(row, p)]
    matched.sort(key=lambda r: r.get('entry_date_filed') or '', reverse=True)
    start = (page - 1) * limit
    results = []
    for row in matched[start:start + limit]:
        results.append({
            'id': row['id'], 'title': _title(row), 'subtitle': _subtitle(row),
            'cells': {'doc_type': DOC_TYPE_LABELS.get(row.get('doc_type'), row.get('doc_type') or ''),
                      'entry_date_filed': row.get('entry_date_filed') or '', 'mdl': str(row.get('mdl_number') or ''),
                      'description': (row.get('raw_document_description') or row.get('raw_entry_description') or '')[:200]},
            'badges': _badges(row), 'links': _links(row)})
    return {
        'available': True, 'total': len(matched), 'page': page, 'limit': limit,
        'qualification': state['qualification'],
        'filters': state['filters'],
        'columns': [{'key': 'entry_date_filed', 'label': 'Entry filed'}, {'key': 'doc_type', 'label': 'Type'},
                    {'key': 'mdl', 'label': 'MDL'}, {'key': 'description', 'label': 'Description'}],
        'results': results}


def detail(item_id: str):
    state, _reason = _load()
    if state is None or not isinstance(item_id, str):
        return None
    row = state['by_id'].get(item_id)
    if row is None:
        return None
    facts = [
        ['Docket number', row.get('docket_number') or ''],
        ['Court', row.get('court') or ''],
        ['MDL', ('%s - %s (%s)' % (row.get('mdl_number'), row.get('mdl_title') or '', row.get('mdl_status') or 'unknown')
                 if row.get('resolved') else 'Not linked to a registered MDL: %s' % (row.get('unresolved_reason') or ''))],
        ['Entry filed (docket entry filing date, not an effective date)', row.get('entry_date_filed') or 'unknown'],
        ['Entry number', str(row.get('entry_number')) if row.get('entry_number') is not None else 'none recorded'],
        ['Document number', row.get('document_number') or 'none recorded'],
        ['Document type (deterministic classifier over the raw docket text)',
         DOC_TYPE_LABELS.get(row.get('doc_type'), row.get('doc_type') or '')],
        ['Source pipeline category (categorised by the source pipeline, not by the court)', row.get('source_doc_category') or 'none'],
        ['High value (source pipeline flag)', 'yes' if row.get('high_value') else 'no'],
        ['Page count', str(row.get('page_count')) if row.get('page_count') is not None else 'unknown'],
        ['Sealed', 'yes' if row.get('is_sealed') else 'no'],
        ['Free document recorded (RECAP, as captured; not re-checked over the network)',
         'yes' if (row.get('download_url') and row.get('sha1')) else 'no'],
        ['SHA-1 (as recorded by the source)', row.get('sha1') or 'none recorded'],
    ]
    if row.get('is_exact_duplicate'):
        facts.append(['Exact duplicate of', row.get('exact_duplicate_of') or ''])
    sections = [{'heading': 'Docket text (verbatim from the source)', 'text': (
        (row.get('raw_entry_description') or '') + ('\n\n' + row['raw_document_description'] if row.get('raw_document_description') else ''))
                 or 'No docket text recorded.'}]
    return {
        'id': row['id'], 'title': _title(row), 'subtitle': _subtitle(row),
        'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': _links(row)}


def for_mdl(mdl_number):
    """Compact block for an MDL detail page: counts by doc_type + up to 25 most-recent documents.
    None when this MDL has no resolved rows in this slice."""
    state, _reason = _load()
    if state is None:
        return None
    num = _as_int(mdl_number)
    if num is None:
        return None
    rows = state['by_mdl'].get(num)
    if not rows:
        return None
    return {
        'mdl_number': num, 'total': len(rows),
        'mdl_status': rows[0].get('mdl_status'),
        'by_doc_type': dict(Counter(r.get('doc_type') for r in rows)),
        'qualification': FOR_MDL_QUALIFICATION,
        'route': '#area/mdl_docket_documents?mdl=%d' % num,
        'latest_25': [_compact(r) for r in rows[:25]],
    }
