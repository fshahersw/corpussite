"""Read-only, hash-gated adapter for MDL master/member-docket activity
(sources/mdl_docket_activity_20260919).

Generic view contract: listing(params), detail(id). Embedding hook: for_mdl(mdl_number). No
original() — no document bytes are stored or served here; this slice never touches document
bytes, only docket-entry metadata rows. Where a row's matter carries a CourtListener docket id
(cl_docket_id, from matter_aliases.jsonl.gz), detail()/listing() link out to
https://www.courtlistener.com/docket/<id>/ ; when it is absent, no link is added and a badge
states the reason instead ("no CourtListener docket id for this matter") — never guessed.

Party names: an individual member-case plaintiff's caption is redacted at build time
(build.py's redact_description) before it ever reaches entries.jsonl; description_redacted /
redaction_basis flag this on the row. A redacted row's subtitle and detail facts substitute its
own matter's member_docket_number + court_id for the withheld caption.

Fails closed: if validation.json is missing, is not status=="passed"/ready==true, or any data-file
hash or row count differs, listing() returns the "not available" shape and detail()/for_mdl()
return None. Nothing raises. Public dicts carry no filesystem paths.

License: derived from a private firm dataset (sw_bulk_private_firm_work_product); local
personal-testing view only, not for redistribution. See
sources/mdl_docket_activity_20260919/README.md.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/mdl_docket_activity_20260919'
FILES = ('entries.jsonl', 'unresolved.jsonl', 'mdl_summary.jsonl')
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
_LOCK = threading.Lock()
_CACHE: dict = {}

ENTRY_TYPE_LABELS = {
    'settlement': 'Settlement', 'bellwether': 'Bellwether', 'daubert': 'Daubert / expert',
    'common_benefit': 'Common benefit', 'steering_committee': 'Steering committee',
    'leadership': 'Leadership appointment', 'pretrial_order': 'Pretrial order',
    'case_management_order': 'Case management order', 'other': 'Other',
}
ENTRY_TYPE_ORDER = ('settlement', 'bellwether', 'daubert', 'common_benefit', 'steering_committee',
                     'leadership', 'pretrial_order', 'case_management_order', 'other')


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
        rows = [json.loads(line) for line in seen['entries.jsonl']]
    except ValueError:
        return None, 'entries.jsonl is not valid JSON lines'
    try:
        summary_rows = [json.loads(line) for line in seen['mdl_summary.jsonl']]
    except ValueError:
        return None, 'mdl_summary.jsonl is not valid JSON lines'
    by_id = {}
    for row in rows:
        rid = row.get('id')
        if not isinstance(rid, str) or rid in by_id:
            return None, 'entries.jsonl identity problem (missing or duplicate id)'
        by_id[rid] = row
    by_summary = {}
    for row in summary_rows:
        num = row.get('mdl_number')
        if isinstance(num, int):
            by_summary[num] = row
    qualification_short = gate.get('qualification_short')
    state = {
        'gate': gate, 'rows': rows, 'by_id': by_id, 'by_summary': by_summary,
        'qualification': gate.get('qualification') or '',
        'qualification_short': qualification_short if isinstance(qualification_short, str) else '',
        'filters': _build_filters(rows, summary_rows),
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
    d = row.get('published_at') or ''
    return d[:4] if len(d) >= 4 and d[:4].isdigit() else None


def _build_filters(rows, summary_rows):
    mdl_counts = Counter(r['mdl_number'] for r in rows if isinstance(r.get('mdl_number'), int))
    mdl_titles = {r['mdl_number']: r.get('mdl_title') for r in summary_rows if isinstance(r.get('mdl_number'), int)}
    entry_type_counts = Counter(r.get('entry_type') for r in rows)
    year_counts = Counter(y for r in rows if (y := _year(r)))
    return [
        {'name': 'q', 'label': 'Search docket text', 'type': 'search'},
        {'name': 'mdl', 'label': 'MDL', 'type': 'select',
         'options': [{'value': str(n), 'label': '%d - %s' % (n, mdl_titles.get(n) or ''), 'count': c}
                     for n, c in sorted(mdl_counts.items())]},
        {'name': 'entry_type', 'label': 'Entry type', 'type': 'select',
         'options': [{'value': t, 'label': ENTRY_TYPE_LABELS.get(t, t), 'count': entry_type_counts[t]}
                     for t in ENTRY_TYPE_ORDER if entry_type_counts.get(t)]},
        {'name': 'year', 'label': 'Published year', 'type': 'select',
         'options': [{'value': y, 'label': y, 'count': n} for y, n in sorted(year_counts.items(), reverse=True)]},
    ]


def _matches(row, p):
    if p['mdl'] is not None and row.get('mdl_number') != p['mdl']:
        return False
    if p['entry_type'] and row.get('entry_type') != p['entry_type']:
        return False
    if p['year'] and _year(row) != p['year']:
        return False
    if p['q']:
        hay = (row.get('description') or '').lower()
        if not all(term in hay for term in p['q'].lower().split()):
            return False
    return True


def _title(row):
    num = row.get('entry_number') or '?'
    mdl = row.get('mdl_number')
    return 'MDL %s entry %s' % (mdl, num) if isinstance(mdl, int) else 'Entry %s' % num


def _subtitle(row):
    # When this row's plaintiff caption was redacted (party-name rule), there is nothing left in
    # the docket text worth truncating for a caption -- show the entry's own member docket number
    # and court instead, when known.
    if row.get('description_redacted') and row.get('member_docket_number') and row.get('court_id'):
        return '%s (%s)' % (row['member_docket_number'], row['court_id'])
    desc = (row.get('description') or '').strip()
    return (desc[:150] + '...') if len(desc) > 150 else (desc or 'no docket text recorded')


def _badges(row):
    badges = [ENTRY_TYPE_LABELS.get(row.get('entry_type'), row.get('entry_type') or '')]
    if row.get('has_verified_document'):
        badges.append('%d verified document(s)' % (row.get('verified_document_count') or 0))
    if row.get('record_status') and row.get('record_status') != 'active':
        badges.append(str(row['record_status']))
    if not isinstance(row.get('cl_docket_id'), int):
        badges.append('no CourtListener docket id for this matter')
    return [b for b in badges if b]


def _links(row):
    links = []
    if isinstance(row.get('mdl_number'), int):
        links.append({'label': 'MDL %d page' % row['mdl_number'], 'url': '#mdl-activity?mdl=%d' % row['mdl_number']})
    if isinstance(row.get('cl_docket_id'), int):
        links.append({'label': 'Docket on CourtListener',
                       'url': 'https://www.courtlistener.com/docket/%d/' % row['cl_docket_id']})
    return links


def _compact(row):
    return {
        'id': row['id'], 'title': _title(row), 'date': row.get('published_at'),
        'entry_type': row.get('entry_type'),
    }


# ----------------------------------------------------------------------------- public API
def listing(params: dict) -> dict:
    state, reason = _load()
    if state is None:
        return {'available': False, 'reason': reason}
    params = params if isinstance(params, dict) else {}

    def text(name):
        value = params.get(name)
        return value.strip() if isinstance(value, str) else ''

    p = {'q': text('q')[:200], 'mdl': _as_int(params.get('mdl')), 'entry_type': text('entry_type'),
         'year': text('year')}
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)
    matched = [row for row in state['rows'] if _matches(row, p)]
    matched.sort(key=lambda r: (r.get('published_at') or '', r.get('entry_number_int') or 0), reverse=True)
    start = (page - 1) * limit
    results = []
    for row in matched[start:start + limit]:
        results.append({
            'id': row['id'], 'title': _title(row), 'subtitle': _subtitle(row),
            'cells': {'entry_type': ENTRY_TYPE_LABELS.get(row.get('entry_type'), row.get('entry_type') or ''),
                      'published_at': row.get('published_at') or '', 'mdl': str(row.get('mdl_number') or ''),
                      'entry_number': row.get('entry_number') or ''},
            'badges': _badges(row), 'links': _links(row)})
    return {
        'available': True, 'total': len(matched), 'page': page, 'limit': limit,
        'qualification': state['qualification'],
        'filters': state['filters'],
        'columns': [{'key': 'published_at', 'label': 'Published'}, {'key': 'entry_type', 'label': 'Type'},
                    {'key': 'mdl', 'label': 'MDL'}, {'key': 'entry_number', 'label': 'Entry #'}],
        'results': results}


def detail(item_id: str):
    state, _reason = _load()
    if state is None or not isinstance(item_id, str):
        return None
    row = state['by_id'].get(item_id)
    if row is None:
        return None
    summary = state['by_summary'].get(row.get('mdl_number')) if isinstance(row.get('mdl_number'), int) else None
    facts = [
        ['MDL', ('%s - %s (%s)' % (row.get('mdl_number'), summary.get('mdl_title') if summary else '',
                                    summary.get('mdl_status') if summary else 'unknown')
                 if isinstance(row.get('mdl_number'), int) else 'none')],
        ['Matter id (native AWS matter identifier)', row.get('matter_id') or 'unknown'],
        ['Member docket (this entry\'s own matter; native docket number and court, not the MDL master docket)',
         '%s (%s)' % (row['member_docket_number'], row['court_id'])
         if row.get('member_docket_number') and row.get('court_id') else 'unknown'],
        ['Entry number', row.get('entry_number') or 'none recorded'],
        ['Published (entered date parsed from the trailing "(Entered: ...)" text, not an effective date)',
         row.get('published_at') or 'unknown'],
        ['Entry type (deterministic classifier over the raw docket text)',
         ENTRY_TYPE_LABELS.get(row.get('entry_type'), row.get('entry_type') or '')],
        ['Verified document(s) attached (RECAP, as captured; not re-checked over the network)',
         '%d' % (row.get('verified_document_count') or 0) if row.get('has_verified_document') else 'none recorded'],
        ['Record status', row.get('record_status') or 'unknown'],
        ['Plaintiff name redaction (party-name rule: natural-person plaintiffs are never listed)',
         (row.get('redaction_basis') or 'individual member-case plaintiff caption withheld per the party-name rule')
         if row.get('description_redacted') else 'no plaintiff caption pattern matched this entry’s text'],
    ]
    docket_heading = ('Docket text (verbatim from the source, except a redacted plaintiff caption)'
                       if row.get('description_redacted') else 'Docket text (verbatim from the source)')
    sections = [{'heading': docket_heading,
                 'text': row.get('description') or 'No docket text recorded.'}]
    return {
        'id': row['id'], 'title': _title(row), 'subtitle': _subtitle(row),
        'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': _links(row)}


def for_mdl(mdl_number):
    """Compact block for an MDL detail page: total, counts by entry_type, date range, whether
    this release's row count for this MDL is a measured lower bound (capped), and up to 25
    most-recent entries. None when this MDL has no rows in this slice."""
    state, _reason = _load()
    if state is None:
        return None
    num = _as_int(mdl_number)
    if num is None:
        return None
    summary = state['by_summary'].get(num)
    if not summary:
        return None
    latest_ids = summary.get('latest_25_ids') or []
    latest = []
    for rid in latest_ids[:25]:
        row = state['by_id'].get(rid)
        if row is not None:
            latest.append(_compact(row))
    return {
        'total': summary.get('total_entries'),
        'by_entry_type': dict(summary.get('counts_by_type') or {}),
        'date_first': summary.get('published_at_min'),
        'date_last': summary.get('published_at_max'),
        'capped': bool(summary.get('entries_capped')),
        'latest': latest,
        'link': '#mdl-activity?mdl=%d' % num,
        'qualification': (state['qualification_short'] or state['qualification'] or '')[:499],
    }
