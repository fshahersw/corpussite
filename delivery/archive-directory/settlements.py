"""Read-only, hash-gated adapter for the settlements supplement (sources/settlements_20260919).

Generic view contract: listing(params), detail(id), original(file_id).
Fail-closed: validation.json must carry the uniform envelope with status == "passed", ready == true and a
matching SHA-256 for every registered data file; otherwise every function reports unavailable / None.
Public dicts carry no filesystem paths, e-mail addresses or phone numbers. Saved originals are served only
from the registered raw folder and only when their bytes re-hash to the recorded SHA-256 at serve time.

What the data is: 848 references from one third-party consumer settlement aggregator (publisher assertions,
not verified) plus a few saved official pages. It is a discovery layer, not a mass-tort settlement corpus.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/settlements_20260919'
REQUIRED = ('settlements.jsonl', 'documents.jsonl', 'court_documents.jsonl')
COURT_LAYER = 'mdl_court_docket_activity'
_COURT_ID = re.compile(r'cldoc-\d+-(?:\d+|rd\d+)')
_CL_URL = 'https://www.courtlistener.com/'
RAW_ROOT = ('packet1', 'corpus', 'raw')
FILE_ROUTE = '/supplement-files/settlements/'
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
_FILE_ID = re.compile(r'stlfile-[0-9a-f]{20}')
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE = re.compile(r'(?<![\d$.-])(?:\+?1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)')
_LOCK = threading.Lock()
_CACHE: dict = {}

FAMILY_ORDER = ('mdl_court_docket', 'mdl_mass_tort', 'ag_government', 'class_consumer', 'data_breach', 'other')
DEADLINE_LABELS = {'within_30_days': 'Within 30 days', 'future': 'Future (more than 30 days)', 'passed': 'Passed',
                   'unknown': 'No deadline published'}
DEADLINE_RANK = {'within_30_days': 0, 'future': 1, 'unknown': 2, 'passed': 3}


# ----------------------------------------------------------------------------- loading
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
        rows = [json.loads(line) for line in seen['settlements.jsonl'].decode('utf-8').splitlines() if line.strip()]
        docs = [json.loads(line) for line in seen['documents.jsonl'].decode('utf-8').splitlines() if line.strip()]
        court = [json.loads(line) for line in seen['court_documents.jsonl'].decode('utf-8').splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError):
        return None, 'data file is not valid JSON lines'
    by_id, doc_by_id, court_by_id = {}, {}, {}
    for entry in court:
        cid = entry.get('court_document_id')
        url = entry.get('courtlistener_url')
        if not isinstance(cid, str) or not _COURT_ID.fullmatch(cid) or cid in court_by_id:
            return None, 'court_documents.jsonl identity problem (missing, malformed or duplicate court_document_id)'
        if not isinstance(url, str) or not url.startswith(_CL_URL):
            return None, 'court_documents.jsonl carries a link outside CourtListener'
        if not isinstance(entry.get('settlement_specific'), bool):
            return None, 'court_documents.jsonl lacks the settlement-specific phrase flag (stale build)'
        court_by_id[cid] = entry
    for row in rows:
        sid = row.get('settlement_id')
        if not isinstance(sid, str) or sid in by_id:
            return None, 'settlements.jsonl identity problem (missing or duplicate settlement_id)'
        by_id[sid] = row
    for doc in docs:
        fid = doc.get('file_id')
        if not isinstance(fid, str) or not _FILE_ID.fullmatch(fid) or fid in doc_by_id:
            return None, 'documents.jsonl identity problem (missing, malformed or duplicate file_id)'
        doc_by_id[fid] = doc
    as_of = next((r.get('deadline_state_as_of') for r in rows if r.get('deadline_state_as_of')), None)
    feed_as_of = next(((r.get('temporal') or {}).get('source_as_of') for r in rows if r.get('record_layer') == 'publisher_reference'), None)
    ordered = sorted(rows, key=_sort_key)
    state = {'gate': gate, 'rows': ordered, 'by_id': by_id, 'docs': doc_by_id, 'court': court_by_id, 'as_of': as_of, 'feed_as_of': feed_as_of,
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
def _clean(value):
    """Strip e-mail addresses and phone numbers from any public string (recursively)."""
    if isinstance(value, str):
        return _PHONE.sub('[phone removed]', _EMAIL.sub('[e-mail removed]', value))
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def _sort_key(row):
    rank = DEADLINE_RANK.get(row.get('deadline_state'), 9)
    deadline = row.get('claim_deadline') or ''
    if rank == 3:  # passed: most recent first
        inverted = ''.join(chr(ord('9') - ord(c) + ord('0')) if c.isdigit() else c for c in deadline)
        return (rank, inverted, row.get('title') or '')
    return (rank, deadline or '9999', (row.get('title') or '').lower())


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


def _deadline_text(row, as_of):
    deadline = row.get('claim_deadline')
    if not deadline:
        return 'No deadline published'
    label = {'passed': 'passed', 'within_30_days': 'within 30 days', 'future': 'future'}.get(row.get('deadline_state'), 'unknown')
    return '%s (%s as of %s; publisher-reported)' % (deadline, label, as_of)


def _is_court(row):
    return row.get('record_layer') == COURT_LAYER


def _court_entries(row, state):
    return [state['court'][cid] for cid in (row.get('court_document_ids') or []) if cid in state['court']]


def _court_counts(row, state):
    """(entries with a settlement-specific phrase, all entries, non-specific phrases matched by the rest), from the entries themselves."""
    entries = _court_entries(row, state)
    specific = sum(1 for e in entries if e.get('settlement_specific') is True)
    others = sorted({t for e in entries if e.get('settlement_specific') is not True for t in (e.get('matched_non_specific_terms') or [])})
    return specific, len(entries), others


def _specific_label(row, state):
    specific, total, _others = _court_counts(row, state)
    return 'entries matching a settlement-specific phrase: %d of %d' % (specific, total)


def _documents_text(row, state):
    if _is_court(row):
        specific, total, _others = _court_counts(row, state)
        return '%d court docket entries listed; %d match a settlement-specific phrase (CourtListener references; no document saved)' % (total, specific)
    count = len(row.get('document_ids') or [])
    if not count:
        return 'None saved'
    return '%d saved official page%s/file%s' % (count, '' if count == 1 else 's', '' if count == 1 else 's')


def _build_filters(state):
    rows = state['rows']
    family_counts = Counter(r.get('family') for r in rows)
    family_labels = {r.get('family'): r.get('family_label') for r in rows}
    deadline_counts = Counter(r.get('deadline_state') for r in rows)
    state_counts = Counter(code for r in rows for code in (r.get('states') or []))
    type_counts = Counter(t for r in rows for t in (r.get('document_types') or []))
    type_labels = {d.get('type'): d.get('type_label') for d in state['docs'].values()}
    mass = sum(1 for r in rows if r.get('mass_tort'))
    with_docs = sum(1 for r in rows if r.get('document_ids'))
    with_court = sum(1 for r in rows if r.get('court_document_ids'))
    return [
        {'name': 'q', 'label': 'Search title, caption or official host', 'type': 'search'},
        {'name': 'family', 'label': 'Family', 'type': 'select',
         'options': [{'value': f, 'label': family_labels.get(f) or f, 'count': family_counts[f]} for f in FAMILY_ORDER if family_counts.get(f)]},
        {'name': 'deadline_state', 'label': 'Claim deadline vs %s' % state['as_of'], 'type': 'select',
         'options': [{'value': k, 'label': DEADLINE_LABELS[k], 'count': deadline_counts[k]} for k in ('within_30_days', 'future', 'passed', 'unknown') if deadline_counts.get(k)]},
        {'name': 'mass_tort', 'label': 'Mass-tort keyword', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Keyword present (MDL, products liability, injury, drug/device/chemical)', 'count': mass},
                     {'value': 'no', 'label': 'No keyword', 'count': len(rows) - mass}]},
        {'name': 'has_documents', 'label': 'Saved documents', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Has a saved official page or file', 'count': with_docs},
                     {'value': 'no', 'label': 'Nothing saved', 'count': len(rows) - with_docs}]},
        {'name': 'has_court_documents', 'label': 'Court docket entries', 'type': 'select',
         'options': [{'value': 'yes', 'label': 'Has court docket entries (MDL master docket, CourtListener)', 'count': with_court},
                     {'value': 'no', 'label': 'No court docket entries', 'count': len(rows) - with_court}]},
        {'name': 'state', 'label': 'State (as published)', 'type': 'select',
         'options': [{'value': code, 'label': code, 'count': n} for code, n in sorted(state_counts.items())]},
        {'name': 'doc_type', 'label': 'Document type', 'type': 'select',
         'options': [{'value': t, 'label': type_labels.get(t) or t, 'count': n} for t, n in sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))]},
        {'name': 'dfrom', 'label': 'Claim deadline from', 'type': 'date'},
        {'name': 'dto', 'label': 'Claim deadline to', 'type': 'date'},
    ]


def _matches(row, p):
    if p['family'] and row.get('family') != p['family']:
        return False
    if p['deadline_state'] and row.get('deadline_state') != p['deadline_state']:
        return False
    if p['mass_tort'] in ('yes', 'no') and bool(row.get('mass_tort')) != (p['mass_tort'] == 'yes'):
        return False
    if p['has_documents'] in ('yes', 'no') and bool(row.get('document_ids')) != (p['has_documents'] == 'yes'):
        return False
    if p['has_court_documents'] in ('yes', 'no') and bool(row.get('court_document_ids')) != (p['has_court_documents'] == 'yes'):
        return False
    if p['state'] and p['state'] not in (row.get('states') or []):
        return False
    if p['doc_type'] and p['doc_type'] not in (row.get('document_types') or []):
        return False
    if p['dfrom'] or p['dto']:
        deadline = row.get('claim_deadline')
        if not deadline or (p['dfrom'] and deadline < p['dfrom']) or (p['dto'] and deadline > p['dto']):
            return False
    if p['q']:
        caption = (row.get('caption') or {}).get('as_printed') or ''
        hay = ' '.join([row.get('title') or '', caption, row.get('official_host') or '', row.get('mdl_title') or '']).lower()
        if not all(term in hay for term in p['q'].lower().split()):
            return False
    return True


def _badges(row, state):
    badges = [row.get('family_label') or row.get('family') or '']
    if _is_court(row):
        badges.append('Docket-entry evidence (not a settlement record or amount)')
        specific, total, others = _court_counts(row, state)
        if specific:
            badges.append('Settlement-specific phrase: %d of %d entries' % (specific, total))
        else:
            badges.append('No settlement-specific entry found (matched only: %s)' % (', '.join(others) or 'no literal phrase'))
        return badges
    if row.get('record_layer') == 'captured_official_page':
        badges.append('Saved official page (no catalog record)')
    else:
        badges.append('Publisher reference (unverified)')
    if row.get('mass_tort'):
        badges.append('Mass-tort keyword')
    if row.get('review'):
        badges.append('Bounded manual review')
    if row.get('document_ids'):
        badges.append('Saved document')
    return [b for b in badges if b]


def _row_links(row):
    links = []
    if _is_court(row):
        if isinstance(row.get('mdl_number'), int):
            links.append({'label': 'MDL %d page' % row['mdl_number'], 'url': '#mdl/%d' % row['mdl_number']})
        if (row.get('official_url') or '').startswith(_CL_URL):
            links.append({'label': 'Master docket on CourtListener', 'url': row['official_url']})
        return links
    if row.get('official_url'):
        links.append({'label': 'Official site', 'url': row['official_url']})
    return links


def _court_search_text(row):
    parts = []
    for search in ((row.get('court_search') or {}).get('searches') or []):
        parts.append('%s of %s matching RECAP documents captured%s' % (
            search.get('results_captured'), search.get('matches_reported_by_search'),
            '' if search.get('complete') else ' (first result page only)'))
    return '; '.join(parts)


def _subtitle(row, state):
    if _is_court(row):
        searched = ((row.get('temporal') or {}).get('captured_at') or '')[:10]
        specific, _total, others = _court_counts(row, state)
        tail = '' if specific else ' - no settlement-specific entry found; captured entries matched only: %s' % (', '.join(others) or 'no literal phrase')
        return '%s - docket %s (%s); CourtListener phrase search%s; docket-entry evidence only; %s%s' % (
            row.get('mdl_title') or '', row.get('docket_number') or '', row.get('court_id') or '', (' ' + searched) if searched else '',
            _specific_label(row, state), tail)
    if row.get('record_layer') == 'captured_official_page':
        captured = ((row.get('temporal') or {}).get('captured_at') or '')[:10]
        return 'Saved official page%s; no publisher record' % ((' (saved %s)' % captured) if captured else '')
    status = row.get('publisher_status') or 'No status published'
    checked = (row.get('verification') or {}).get('publisher_last_verified')
    return '%s - publisher-reported%s' % (status, (', publisher last checked %s' % checked) if checked else '')


# ----------------------------------------------------------------------------- public API
def listing(params: dict) -> dict:
    state, reason = _load()
    if state is None:
        return {'available': False, 'reason': reason}
    params = params or {}

    def text(name):
        value = params.get(name)
        return value.strip() if isinstance(value, str) else ''

    p = {'q': text('q')[:200], 'family': text('family'), 'deadline_state': text('deadline_state'),
         'mass_tort': text('mass_tort').lower(), 'has_documents': text('has_documents').lower(),
         'has_court_documents': text('has_court_documents').lower(),
         'state': text('state').upper(), 'doc_type': text('doc_type'),
         'dfrom': _iso_date(params.get('dfrom')), 'dto': _iso_date(params.get('dto'))}
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)
    matched = [row for row in state['rows'] if _matches(row, p)]
    start = (page - 1) * limit
    results = []
    for row in matched[start:start + limit]:
        results.append({
            'id': row['settlement_id'], 'title': row.get('title') or row['settlement_id'],
            'subtitle': _subtitle(row, state),
            'cells': {'settlement': row.get('title') or '', 'family': row.get('family_label') or '',
                      'deadline': _deadline_text(row, state['as_of']), 'documents': _documents_text(row, state)},
            'badges': _badges(row, state), 'links': _row_links(row)})
    return _clean({
        'available': True, 'total': len(matched), 'page': page, 'limit': limit,
        'qualification': state['qualification'],
        'filters': state['filters'],
        'columns': [{'key': 'settlement', 'label': 'Settlement'}, {'key': 'family', 'label': 'Family'},
                    {'key': 'deadline', 'label': 'Deadline'}, {'key': 'documents', 'label': 'Documents'}],
        'results': results})


def _document_item(doc):
    saved = ((doc.get('temporal') or {}).get('captured_at') or '')[:10]
    kind = 'saved page' if doc.get('record_kind') == 'saved_page' else 'downloaded file'
    basis = {'url_path': 'typed from the URL path', 'filename': 'typed from the file name', 'link_text': 'typed from link text',
             'first_page_text': 'typed from first-page text', 'none': 'no type rule matched; left unclassified'}.get(doc.get('type_basis'), doc.get('type_basis') or '')
    subtitle = '%s - %s; %s; saved %s; %s bytes; SHA-256 %s...' % (
        doc.get('type_label') or doc.get('type'), basis, kind, saved or 'date unknown', format(doc.get('byte_count') or 0, ','), (doc.get('sha256') or '')[:12])
    return {'title': doc.get('title_as_published') or doc.get('url'), 'subtitle': subtitle,
            'links': [{'label': 'Open saved copy (original bytes)', 'url': FILE_ROUTE + doc['file_id']},
                      {'label': 'Official URL', 'url': doc.get('url')}]}


def _phrase_text(entry):
    specific = entry.get('matched_settlement_specific_terms') or []
    other = entry.get('matched_non_specific_terms') or []
    if entry.get('settlement_specific') is True and specific:
        return 'settlement-specific phrase: %s%s' % (', '.join(specific), ('; also non-specific: ' + ', '.join(other)) if other else '')
    return 'non-specific phrase only: %s (also matches entries unrelated to any settlement)' % (', '.join(other) or 'none literal')


def _court_item(entry):
    number = entry.get('entry_number')
    typed = entry.get('type_label') or entry.get('type') or 'Unclassified'
    basis = ('typed from the docket description text only' if entry.get('type_basis') == 'docket_entry_description'
             else 'no type rule matched the description; left unclassified')
    attachments = entry.get('attachments') or []
    extra = ('; %d attachment%s on the captured page' % (len(attachments), '' if len(attachments) == 1 else 's')) if attachments else ''
    return {'title': 'Date filed %s - %s - %s' % (entry.get('date_filed') or 'unknown',
                                                  ('docket entry %s' % number) if number is not None else 'unnumbered docket entry', typed),
            'subtitle': '%s [%s; %s%s; %s]' % (
                entry.get('description_as_recorded') or '', basis, entry.get('availability_label') or '', extra, _phrase_text(entry)),
            'links': [{'label': 'CourtListener docket entry' if number is not None else 'CourtListener docket page',
                       'url': entry['courtlistener_url']}]}


def _court_detail(row, state):
    entries = _court_entries(row, state)
    temporal = row.get('temporal') or {}
    search = row.get('court_search') or {}
    in_recap = sum(1 for e in entries if e.get('is_available') is True)
    specific_entries = [e for e in entries if e.get('settlement_specific') is True]
    other_entries = [e for e in entries if e.get('settlement_specific') is not True]
    _specific, _total, others = _court_counts(row, state)
    facts = [['Record layer', 'Settlement-phrase docket search (docket-entry evidence; not a settlement record or amount)'],
             ['Entries matching a settlement-specific phrase', '%d of %d captured entries' % (len(specific_entries), len(entries))]]
    if not specific_entries:
        facts.append(['Finding', row.get('settlement_specific_finding') or (
            'No settlement-specific entry found among the captured docket entries; they matched only: %s.' % (', '.join(others) or 'no literal phrase'))])
    facts += [['MDL', 'MDL %s - %s' % (row.get('mdl_number'), row.get('mdl_title') or '')],
             ['Master docket', '%s (%s), CourtListener docket id %s' % (row.get('docket_number') or '', row.get('court_id') or '', row.get('cl_docket_id'))],
             ['Master docket basis', row.get('master_docket_basis') or ''],
             ['Source', row.get('publisher_label') or ''],
             ['Settlement-specific search phrases (docket description)', ', '.join(search.get('settlement_specific_terms') or [])],
             ['Non-specific search phrases (also match entries unrelated to any settlement)', ', '.join(search.get('non_specific_terms') or [])],
             ['Search coverage', _court_search_text(row)],
             ['Docket entries listed', '%d (main document in the free RECAP archive when searched: %d)' % (len(entries), in_recap)],
             ['Verification', (row.get('verification') or {}).get('label') or ''],
             ['Family', '%s - %s' % (row.get('family_label') or '', row.get('family_basis') or '')],
             ['Mass-tort keyword', row.get('mass_tort_basis') or 'none']]
    if temporal.get('captured_at'):
        facts.append(['CourtListener searched', '%s (%s)' % (temporal['captured_at'], temporal.get('captured_at_basis') or '')])
    first = {'heading': 'Court docket entries matching a settlement-specific phrase (%d; date filed = court filing date recorded by CourtListener)'
                        % len(specific_entries),
             'items': [_court_item(entry) for entry in specific_entries]}
    if not specific_entries:
        first['text'] = 'None among the captured entries. This row is not evidence of settlement activity in this MDL.'
    sections = [first]
    if other_entries:
        sections.append({'heading': 'Court docket entries matching only a non-specific phrase (%d; %s; not settlement evidence)'
                                    % (len(other_entries), ', '.join(others) or 'no literal phrase'),
                         'items': [_court_item(entry) for entry in other_entries]})
    sections.append({'heading': 'What this record is not', 'text': (
        'Not a settlement record, not an amount and not a finding that a settlement exists. Each line is a docket entry whose '
        'description matched a search phrase. Only the settlement-specific phrases name a settlement step; "common benefit" (fee and expense '
        'assessments, time-keeping protocols) and "order approving" (any approved schedule or protocol) also match entries unrelated to any '
        'settlement and are listed separately. No court document was opened, downloaded or purchased; availability is the RECAP flag at '
        'search time. Where coverage says first result page only, older matching entries are not listed.')})
    return _clean({'id': row['settlement_id'], 'title': row.get('title') or row['settlement_id'], 'subtitle': _subtitle(row, state),
                   'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': _row_links(row)})


def detail(id: str):
    state, _reason = _load()
    if state is None or not isinstance(id, str):
        return None
    row = state['by_id'].get(id)
    if row is None:
        return None
    if _is_court(row):
        return _court_detail(row, state)
    as_of = state['as_of']
    publisher = row.get('publisher') or {}
    verification = row.get('verification') or {}
    is_page = row.get('record_layer') == 'captured_official_page'
    facts = [['Record layer', 'Saved official page (no catalog record)' if is_page else 'Publisher reference (third-party aggregator)'],
             ['Source', row.get('publisher_label') or '']]
    if not is_page:
        facts.append(['Publisher status (publisher-reported)', row.get('publisher_status') or 'not published'])
        facts.append(['Claim deadline (publisher-reported)', _deadline_text(row, as_of)])
    amount = row.get('amount')
    facts.append(['Amount as printed in title', ', '.join(amount['as_printed']) + ' (title text; fund, per-person or fee not distinguished)' if amount else 'none printed in title'])
    caption = row.get('caption')
    if caption:
        facts.append(['Caption as printed in title', caption.get('as_printed') or ''])
        if caption.get('kind') == 'adversarial':
            note = ' (side text may include descriptive words)' if caption.get('side_text_may_include_descriptive_words') else ''
            facts.append(['Plaintiff side (title text)', (caption.get('plaintiff_side_as_printed') or '') + note])
            facts.append(['Defendant side (title text)', (caption.get('defendant_side_as_printed') or '') + note])
    if not is_page:
        states = row.get('states') or []
        facts.append(['States (as published)', ', '.join(states) if states else 'none listed by the publisher (not a class definition)'])
    facts.append(['Verification', verification.get('label') or ''])
    if not is_page:
        facts.append(['Publisher verification status', '%s (accepted official evidence: %s)' % (
            verification.get('publisher_verification_status') or 'not published', verification.get('publisher_accepted_official_evidence'))])
        facts.append(['Publisher last checked', verification.get('publisher_last_verified') or 'not published'])
        facts.append(['Proof required (publisher-reported)', publisher.get('proof_required') or 'not published'])
        facts.append(['Publisher type / category', '%s / %s' % (publisher.get('settlement_type') or '-', publisher.get('category') or '-')])
    facts.append(['Family', '%s - %s' % (row.get('family_label') or '', row.get('family_basis') or '')])
    facts.append(['Mass-tort keyword', row.get('mass_tort_basis') or 'none (keyword check only, never a legal characterisation)'])
    temporal = row.get('temporal') or {}
    if temporal.get('source_as_of'):
        facts.append(['Publisher feed as of', '%s (%s)' % (temporal['source_as_of'], temporal.get('source_as_of_basis') or '')])
    if temporal.get('captured_at'):
        facts.append(['Page saved', '%s (%s)' % (temporal['captured_at'], temporal.get('captured_at_basis') or '')])

    docs = [state['docs'][fid] for fid in (row.get('document_ids') or []) if fid in state['docs']]
    sections = [{'heading': 'Documents (%d saved)' % len(docs), 'items': [_document_item(doc) for doc in docs]}]
    if not docs:
        sections[0]['text'] = 'No official page or document has been saved for this reference.'
    if publisher.get('estimated_payout'):
        sections.append({'heading': 'Payout text (publisher, verbatim; never summed)', 'text': publisher['estimated_payout']})
    review = row.get('review')
    if review:
        rows = [[label, str(review.get(key))] for label, key in (('Court', 'court'), ('Case number', 'case_number'), ('Jurisdiction', 'jurisdiction'),
                ('Status at review', 'status'), ('Claim deadline at review', 'claim_deadline'), ('Review date', 'assessment_date'),
                ('Summary', 'summary'), ('Limitations', 'limitations')) if review.get(key)]
        sections.append({'heading': review.get('label') or 'Bounded manual review', 'header': ['Field', 'Value'], 'rows': rows})
    sections.append({'heading': 'What this record is not', 'text': (
        'Not a court record and not a verified settlement. The deadline state is arithmetic on the publisher date against %s and says nothing '
        'about eligibility. A saved page is a snapshot of an administrator or government site, not the settlement agreement or an order.' % as_of)})
    links = _row_links(row)
    if publisher.get('url'):
        links.append({'label': 'Publisher page (SettleSignal)', 'url': publisher['url']})
    return _clean({'id': row['settlement_id'], 'title': row.get('title') or row['settlement_id'], 'subtitle': _subtitle(row, state),
                   'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': links})


def original(file_id: str):
    """Return (bytes, mime, filename) for a saved original, re-hashed at serve time; else None."""
    state, _reason = _load()
    if state is None or not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
        return None
    doc = state['docs'].get(file_id)
    if doc is None or not isinstance(doc.get('raw_path'), str):
        return None
    folder = DATA.resolve()
    raw_root = folder.joinpath(*RAW_ROOT).resolve()
    target = (folder / doc['raw_path']).resolve()
    if not target.is_relative_to(raw_root) or not target.is_file():
        return None
    try:
        data = target.read_bytes()
    except OSError:
        return None
    if _digest(data) != doc.get('sha256') or not file_id.endswith(doc['sha256'][:20]):
        return None
    return data, doc.get('mime') or 'application/octet-stream', doc.get('filename') or file_id
