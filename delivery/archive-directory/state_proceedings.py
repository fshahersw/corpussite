"""Read-only, hash-gated adapter for state_coordinated_proceedings_20260919 (NJ MCL + CA JCCP).

Generic view contract: listing(params), detail(id). No `original()` -- this supplement has no bytes to serve.
Fail-closed: validation.json must carry the uniform envelope with status == "passed", ready == true and a
matching SHA-256 for every registered data file; otherwise every function reports unavailable / None.
Public dicts carry no filesystem paths, e-mail addresses or phone numbers, and no judge-entity/cl_person
link -- judge names are printed text only.

Embedding hooks: for_mdl(mdl_number) (state proceedings whose source names that federal MDL number) and
for_state(usps) (NJ or CA proceedings). for_judge / for_court are not implemented: neither state system
carries a native judge or court id this adapter may join on.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/state_coordinated_proceedings_20260919'
REQUIRED = ('proceedings.jsonl', 'edges.jsonl', 'unresolved.jsonl')
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
_LOCK = threading.Lock()
_CACHE: dict = {}

STATE_LABELS = {'NJ': 'New Jersey Multicounty Litigation (MCL)', 'CA': 'California JCCP (coordination proceeding)'}
TYPE_LABELS = {'nj_mcl': 'NJ MCL', 'ca_jccp': 'CA JCCP'}


# ----------------------------------------------------------------------------- loading (fail-closed gate)
def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(folder: Path):
    sig = []
    for name in ('validation.json',) + REQUIRED:
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
        seen[Path(entry['path']).name] = data
    missing = [name for name in REQUIRED if name not in seen]
    if missing:
        return None, 'data files not registered in validation.json: %s' % ', '.join(missing)
    try:
        proceedings = [json.loads(line) for line in seen['proceedings.jsonl'].decode('utf-8').splitlines() if line.strip()]
        edges = [json.loads(line) for line in seen['edges.jsonl'].decode('utf-8').splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError):
        return None, 'data file is not valid JSON lines'
    by_id = {}
    for row in proceedings:
        rid = row.get('id')
        if not isinstance(rid, str) or rid in by_id:
            return None, 'proceedings.jsonl identity problem (missing or duplicate id)'
        by_id[rid] = row
    mdl_edges_by_number: dict = {}
    for e in edges:
        if e.get('relation') != 'names_federal_mdl':
            continue
        to = e.get('to') or {}
        try:
            number = int(to.get('id'))
        except (TypeError, ValueError):
            continue
        mdl_edges_by_number.setdefault(number, []).append(e)
    state = {'gate': gate, 'rows': proceedings, 'by_id': by_id, 'edges': edges,
              'mdl_edges_by_number': mdl_edges_by_number,
              'qualification': _clean(gate.get('qualification') or '')}
    state['filters'] = _build_filters(state)
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
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE = re.compile(r'(?<![\d$.-])(?:\+?1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)')


def _clean(value):
    if isinstance(value, str):
        return _PHONE.sub('[phone removed]', _EMAIL.sub('[e-mail removed]', value))
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def _int(value, default, low, high):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < low:
        return default
    return min(number, high)


def _iso_date(value):
    value = (value or '').strip() if isinstance(value, str) else ''
    return value if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None


def _year(row):
    if row['type'] == 'ca_jccp':
        d = row.get('date_received_iso')
        return d[:4] if d else None
    return None  # NJ designation_date is free text, never parsed to a year


def _has_related_mdl(row):
    if row['type'] == 'nj_mcl':
        return bool(row.get('related_mdls'))
    return False


def _county_list(row):
    if row['type'] == 'nj_mcl':
        return [row['county']] if row.get('county') else []
    return row.get('counties') or []


def _system_value(row):
    return 'NJ_MCL' if row['type'] == 'nj_mcl' else 'CA_JCCP'


def _build_filters(state):
    rows = state['rows']
    system_counts = Counter(_system_value(r) for r in rows)
    county_counts = Counter(c for r in rows for c in _county_list(r))
    category_counts = Counter(r.get('category') for r in rows if r['type'] == 'ca_jccp' and r.get('category'))
    archived_counts = Counter(r.get('archived') for r in rows if r['type'] == 'nj_mcl')
    has_mdl = sum(1 for r in rows if _has_related_mdl(r))
    return [
        {'name': 'q', 'label': 'Search title, county, judge or drug', 'type': 'search'},
        {'name': 'system', 'label': 'State system', 'type': 'select',
         'options': [{'value': v, 'label': STATE_LABELS[v.split('_')[0]], 'count': system_counts[v]}
                     for v in ('NJ_MCL', 'CA_JCCP') if system_counts.get(v)]},
        {'name': 'county', 'label': 'County', 'type': 'select',
         'options': [{'value': c, 'label': c, 'count': n} for c, n in sorted(county_counts.items())]},
        {'name': 'category', 'label': 'Category (CA JCCP only)', 'type': 'select',
         'options': [{'value': c, 'label': c, 'count': n} for c, n in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))]},
        {'name': 'archived', 'label': 'Archived (NJ MCL only)', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Archived', 'count': archived_counts.get(True, 0)},
                     {'value': 'no', 'label': 'Active', 'count': archived_counts.get(False, 0)}]},
        {'name': 'has_related_mdl', 'label': 'Names a federal MDL number', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Names a federal MDL number', 'count': has_mdl},
                     {'value': 'no', 'label': 'No MDL number named', 'count': len(rows) - has_mdl}]},
        {'name': 'dfrom', 'label': 'Date received from (CA JCCP only)', 'type': 'date'},
        {'name': 'dto', 'label': 'Date received to (CA JCCP only)', 'type': 'date'},
    ]


def _matches(row, p):
    if p['system'] and _system_value(row) != p['system']:
        return False
    if p['county'] and p['county'] not in _county_list(row):
        return False
    if p['category'] and (row['type'] != 'ca_jccp' or row.get('category') != p['category']):
        return False
    if p['archived'] in ('yes', 'no'):
        if row['type'] != 'nj_mcl' or bool(row.get('archived')) != (p['archived'] == 'yes'):
            return False
    if p['has_related_mdl'] in ('yes', 'no') and _has_related_mdl(row) != (p['has_related_mdl'] == 'yes'):
        return False
    if p['dfrom'] or p['dto']:
        if row['type'] != 'ca_jccp':
            return False
        d = row.get('date_received_iso')
        if not d or (p['dfrom'] and d < p['dfrom']) or (p['dto'] and d > p['dto']):
            return False
    if p['q']:
        hay_parts = [row.get('title') or '']
        if row['type'] == 'nj_mcl':
            hay_parts += [row.get('county') or '', row.get('judge_name_text') or ''] + (row.get('drugs') or [])
        else:
            hay_parts += _county_list(row) + (row.get('case_numbers') or [])
        hay = ' '.join(hay_parts).lower()
        if not all(term in hay for term in p['q'].lower().split()):
            return False
    return True


def _subtitle(row):
    if row['type'] == 'nj_mcl':
        county = row.get('county') or 'county not stated in the source'
        judge = row.get('judge_name_text') or 'judge not stated in the source'
        status = 'archived' if row.get('archived') else 'active'
        return 'New Jersey MCL - %s - %s - presiding judge (text only, not linked): %s' % (county, status, judge)
    counties = ', '.join(row.get('counties') or []) or 'county not stated in the source'
    return 'California JCCP %s - %s - %s' % (row['jccp_no'], counties, row.get('category') or 'uncategorized')


def _cells(row):
    if row['type'] == 'nj_mcl':
        mdl_text = ', '.join('MDL %d (%s)' % (m['mdl_number'], m['registry_status']) for m in (row.get('related_mdls') or [])) or 'none named'
        return {'system': 'NJ MCL', 'county': row.get('county') or 'not stated in the source',
                'status': 'Archived' if row.get('archived') else 'Active', 'related_mdl': mdl_text}
    return {'system': 'CA JCCP', 'county': ', '.join(row.get('counties') or []) or 'not stated in the source',
            'status': row.get('category') or 'uncategorized',
            'related_mdl': row.get('date_received_text') or 'date not stated in the source'}


def _badges(row):
    badges = [TYPE_LABELS[row['type']]]
    if row['type'] == 'nj_mcl':
        if row.get('archived'):
            badges.append('Archived')
        if row.get('related_mdls'):
            pending = [m for m in row['related_mdls'] if m['registry_status'] == 'pending']
            badges.append('Names %d federal MDL number%s (%d in the local pending registry)' %
                          (len(row['related_mdls']), '' if len(row['related_mdls']) == 1 else 's', len(pending)))
    else:
        if row.get('category') == 'masstort_candidate':
            badges.append('Mass-tort candidate (source category)')
        if not row.get('date_received_iso') and row.get('date_received_text'):
            badges.append('date_received not well-formed in the source')
    return badges


def _links(row):
    if row['type'] == 'nj_mcl' and row.get('source_url'):
        return [{'label': 'njcourts.gov case information', 'url': row['source_url']}]
    return []


# ----------------------------------------------------------------------------- public API
def listing(params: dict) -> dict:
    state, reason = _load()
    if state is None:
        return {'available': False, 'reason': reason}
    params = params or {}

    def text(name):
        value = params.get(name)
        return value.strip() if isinstance(value, str) else ''

    p = {'q': text('q')[:200], 'system': text('system').upper(), 'county': text('county'),
         'category': text('category'), 'archived': text('archived').lower(),
         'has_related_mdl': text('has_related_mdl').lower(),
         'dfrom': _iso_date(params.get('dfrom')), 'dto': _iso_date(params.get('dto'))}
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)
    matched = [row for row in state['rows'] if _matches(row, p)]
    matched.sort(key=lambda r: (0 if r['type'] == 'nj_mcl' else 1, (r.get('title') or '').lower()))
    start = (page - 1) * limit
    results = []
    for row in matched[start:start + limit]:
        results.append({'id': row['id'], 'title': row.get('title') or row['id'], 'subtitle': _subtitle(row),
                         'cells': _cells(row), 'badges': _badges(row), 'links': _links(row)})
    return _clean({
        'available': True, 'total': len(matched), 'page': page, 'limit': limit,
        'qualification': state['qualification'],
        'filters': state['filters'],
        'columns': [{'key': 'system', 'label': 'System'}, {'key': 'county', 'label': 'County'},
                    {'key': 'status', 'label': 'Status / category'}, {'key': 'related_mdl', 'label': 'Related MDL / date received'}],
        'results': results})


def _nj_detail(row, state):
    facts = [
        ['State system', STATE_LABELS['NJ']],
        ['County', row.get('county') or 'not stated in the source'],
        ['Presiding judge (as printed; text only, no entity link)', row.get('judge_name_text') or 'not stated in the source'],
        ['Designation date (free text, as printed; not parsed to ISO)', row.get('designation_date_text') or 'not stated in the source'],
        ['Archived', 'Yes' if row.get('archived') else 'No'],
        ['Document count (as recorded by njcourts.gov at build time)', row.get('document_count') if row.get('document_count') is not None else 'not stated in the source'],
        ['Capture date', row.get('captured_at_basis') or 'unknown'],
    ]
    if row.get('presiding_judges_from_graph_edges'):
        facts.append(['Presiding judge(s) (njmcl_edges.csv, text only)', ', '.join(row['presiding_judges_from_graph_edges'])])
    sections = []
    if row.get('drugs'):
        sections.append({'heading': 'Products / drugs named in this proceeding (%d)' % len(row['drugs']),
                          'items': [{'title': d} for d in row['drugs']]})
    related = row.get('related_mdls') or []
    if related:
        sections.append({
            'heading': 'Federal MDL numbers named by the source (%d)' % len(related),
            'header': ['MDL number', 'Registry status', 'Raw field', 'Source'],
            'rows': [[str(m['mdl_number']), m['registry_status'], m['raw_field'], m['source']] for m in related],
        })
    else:
        sections.append({'heading': 'Federal MDL numbers named by the source', 'text': 'None named for this proceeding.'})
    sections.append({'heading': 'What this record is not', 'text': (
        'Not linked to any judge entity, cl_person_id or FJC id. NJ Multicounty Litigation judges are state '
        'judges, not FJC/CourtListener judges, and this build never merges people by name. The MDL numbers '
        'above are parsed from a draft, unmerged registry-suggestions file, not a JPML or CourtListener '
        'assertion; "registry status" only reflects whether that number is a pending MDL in this build\'s '
        'own local registry.')})
    return _clean({'id': row['id'], 'title': row.get('title') or row['id'], 'subtitle': _subtitle(row),
                   'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': _links(row)})


def _ca_detail(row, state):
    facts = [
        ['State system', STATE_LABELS['CA']],
        ['JCCP number', row['jccp_no']],
        ['Counties (as printed on the register)', ', '.join(row.get('counties') or []) or 'not stated in the source'],
        ['Case numbers', ', '.join(row.get('case_numbers') or []) or 'not stated in the source'],
        ['Category (source-assigned)', row.get('category') or 'not stated in the source'],
        ['Era (log field, source-assigned)', row.get('log') or 'not stated in the source'],
        ['Date received (as printed)', row.get('date_received_text') or 'not stated in the source'],
        ['Date received (parsed; %s)' % (row.get('date_received_basis') or 'not parsed'),
         row.get('date_received_iso') or 'not parseable from the source value'],
        ['Capture date', row.get('captured_at_basis') or 'unknown'],
    ]
    sections = []
    if row.get('title_is_extraction_noise') and row.get('title_raw'):
        sections.append({'heading': 'Raw extracted text, unedited', 'text': (
            'The source title field for this proceeding is raw PDF-extraction text, not a clean case name. '
            'The title shown above was cut down from it; the untouched source string is: %s' % row['title_raw'])})
    if row.get('raw_text_available'):
        sections.append({'heading': 'Raw extracted text, unedited', 'text': (
            'This record includes raw concatenated PDF-extraction text in the source; it is not shown here '
            'to avoid publishing unedited OCR/extraction noise as though it were a clean field.')})
    sections.append({'heading': 'What this record is not', 'text': (
        'The judges field recorded by the source for this registry is PDF-extraction noise (fragmentary '
        'names, misspellings) and is dropped entirely by this build -- it is never published and never '
        'name-matched to any judge entity. date_received is a filing-receipt date, not an effective date.')})
    return _clean({'id': row['id'], 'title': row.get('title') or row['id'], 'subtitle': _subtitle(row),
                   'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': _links(row)})


def detail(item_id: str):
    state, _reason = _load()
    if state is None or not isinstance(item_id, str):
        return None
    row = state['by_id'].get(item_id)
    if row is None:
        return None
    return _nj_detail(row, state) if row['type'] == 'nj_mcl' else _ca_detail(row, state)


# ----------------------------------------------------------------------------- embedding hooks
def for_mdl(mdl_number):
    """Compact hook for an MDL detail page: NJ MCL proceedings whose source names this MDL number, or None."""
    state, _reason = _load()
    if state is None:
        return None
    try:
        mdl_number = int(mdl_number)
    except (TypeError, ValueError):
        return None
    edges = state['mdl_edges_by_number'].get(mdl_number)
    if not edges:
        return None
    results = []
    for e in edges[:25]:
        njmcl_id = 'njmcl:%s' % e['from']['id']
        row = state['by_id'].get(njmcl_id)
        if row is None:
            continue
        results.append({'id': row['id'], 'title': row.get('title') or row['id'],
                         'registry_status': e.get('registry_status'), 'evidence': e.get('evidence')})
    if not results:
        return None
    return _clean({
        'total': len(edges), 'results': results,
        'qualification': ('New Jersey MCL proceedings whose entry in njmcl_registry_suggestions.json (a draft, '
                           'unmerged file) itself names this federal MDL number. Not a JPML or CourtListener '
                           'assertion; never derived from a name match.'),
        'link': '#state-proceedings?has_related_mdl=yes',
    })


def for_state(usps):
    """Compact hook for a state page (#state/NJ or #state/CA): up to 25 proceedings for that state, or None."""
    state, _reason = _load()
    if state is None or not isinstance(usps, str) or not re.fullmatch(r'[A-Za-z]{2}', usps):
        return None
    usps = usps.upper()
    if usps not in ('NJ', 'CA'):
        return None
    rows = [r for r in state['rows'] if r.get('state') == usps]
    if not rows:
        return None
    results = [{'id': r['id'], 'title': r.get('title') or r['id'], 'subtitle': _subtitle(r)} for r in rows[:25]]
    return _clean({
        'total': len(rows), 'results': results,
        'qualification': ('%s proceedings from state_coordinated_proceedings_20260919.' % STATE_LABELS[usps]),
        'link': '#state-proceedings?system=%s' % ('NJ_MCL' if usps == 'NJ' else 'CA_JCCP'),
    })
