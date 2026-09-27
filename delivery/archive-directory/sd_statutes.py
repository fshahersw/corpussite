"""Read-only, hash-gated adapter for the SD statutes supplement (sources/sd_statutes_20260919).

Generic view contract: listing(params), detail(id). No `original()` -- there are no separate raw bytes to
serve (the title's own HTML page is not republished; only the parsed chapter/section index is shown).
Fail-closed: validation.json must carry the uniform envelope with status == "passed", ready == true and a
matching SHA-256 for every registered data file; otherwise every function reports unavailable / None.

What the data is: a title-level cache of the South Dakota Codified Laws (SDCL), captured 2026-08-20 from the
SD Legislature's public statutes API. Each of 71 titles carries its own chapter/section table of contents
(numbers + catchlines) -- not the statutory text of each section, which is not present in this cache.

Embedding hook: for_state('SD') -- a compact summary for the #state/SD page.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/sd_statutes_20260919'
REQUIRED = ('titles.jsonl', 'unresolved.jsonl')
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
_LOCK = threading.Lock()
_CACHE: dict = {}
_ID = re.compile(r'sdcl:\d+')


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
        rows = [json.loads(line) for line in seen['titles.jsonl'].decode('utf-8').splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError):
        return None, 'titles.jsonl is not valid JSON lines'
    by_id = {}
    for row in rows:
        rid = row.get('id')
        if not isinstance(rid, str) or not _ID.fullmatch(rid) or rid in by_id:
            return None, 'titles.jsonl identity problem (missing, malformed or duplicate id)'
        by_id[rid] = row
    state = {'gate': gate, 'rows': sorted(rows, key=_sort_key), 'by_id': by_id,
              'qualification': gate.get('qualification') or ''}
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
def _sort_key(row):
    number = row.get('title_number') or ''
    m = re.match(r'(\d+)', number)
    return (int(m.group(1)) if m else 10 ** 6, number)


def _int(value, default, low, high):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < low:
        return default
    return min(number, high)


def _title_label(row):
    return 'SDCL Title %s' % (row.get('title_number') or '?')


def _subtitle(row):
    tag = ' [repealed/transferred title]' if row.get('repealed') else ''
    return '%d chapters, %d sections indexed (as retrieved %s)%s' % (
        row.get('chapter_count') or 0, row.get('section_count') or 0, (row.get('retrieved_at') or '')[:10], tag)


def _matches(row, q):
    if not q:
        return True
    hay = ' '.join([row.get('title_number') or '', row.get('catch_line') or '',
                     ' '.join(c.get('chapter_title') or '' for c in (row.get('chapters') or []))]).lower()
    return all(term in hay for term in q.lower().split())


def _links(row):
    links = []
    if row.get('public_url'):
        links.append({'label': 'South Dakota Legislature (official page)', 'url': row['public_url']})
    return links


# ----------------------------------------------------------------------------- public API
def listing(params: dict) -> dict:
    state, reason = _load()
    if state is None:
        return {'available': False, 'reason': reason}
    params = params or {}

    def text(name):
        value = params.get(name)
        return value.strip() if isinstance(value, str) else ''

    q = text('q')[:200]
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)
    matched = [row for row in state['rows'] if _matches(row, q)]
    start = (page - 1) * limit
    results = []
    for row in matched[start:start + limit]:
        results.append({
            'id': row['id'], 'title': _title_label(row), 'subtitle': row.get('catch_line') or '',
            'cells': {'title': _title_label(row), 'caption': row.get('catch_line') or '',
                      'chapters': str(row.get('chapter_count') or 0), 'sections': str(row.get('section_count') or 0)},
            'badges': (['Repealed/transferred title'] if row.get('repealed') else []),
            'links': _links(row)})
    return {
        'available': True, 'total': len(matched), 'page': page, 'limit': limit,
        'qualification': state['qualification'],
        'filters': [{'name': 'q', 'label': 'Search title number or caption', 'type': 'search'}],
        'columns': [{'key': 'title', 'label': 'Title'}, {'key': 'caption', 'label': 'Caption'},
                    {'key': 'chapters', 'label': 'Chapters'}, {'key': 'sections', 'label': 'Sections'}],
        'results': results}


def detail(id: str):
    state, _reason = _load()
    if state is None or not isinstance(id, str):
        return None
    row = state['by_id'].get(id)
    if row is None:
        return None
    facts = [
        ['Title number', row.get('title_number') or ''],
        ['Native id', '%s (South Dakota StatuteId %s)' % (row.get('id'), row.get('statute_id'))],
        ['Retrieved', '%s (capture date; API returns no effective date)' % (row.get('retrieved_at') or 'unknown')],
        ['Source status at retrieval', 'HTTP %s' % row.get('http_status')],
        ['Chapters indexed', str(row.get('chapter_count') or 0)],
        ['Sections indexed', str(row.get('section_count') or 0)],
        ['Unparsed table-of-contents lines', str(row.get('unparsed_line_count') or 0)],
    ]
    sections = []
    for chapter in row.get('chapters') or []:
        rows = [[s.get('section_id') or '', s.get('text') or ''] for s in (chapter.get('sections') or [])]
        for note in chapter.get('unparsed') or []:
            rows.append(['(unparsed)', note])
        sections.append({
            'heading': 'Chapter %s -- %s' % (chapter.get('chapter_number') or '', chapter.get('chapter_title') or ''),
            'header': ['Section', 'Catchline'], 'rows': rows})
    if not sections:
        sections.append({'heading': 'No chapters parsed', 'text': 'This title carries no parseable chapter/section structure.'})
    sections.append({'heading': 'What this record is not', 'text': (
        'A chapter/section index (numbers and catchlines) as published by the South Dakota Legislature statutes API, '
        'as retrieved on %s, not verified current. Not the statutory text of any section -- full section text is not '
        'present in this cache.' % (row.get('retrieved_at') or 'unknown')[:10])})
    return {'id': row['id'], 'title': _title_label(row), 'subtitle': row.get('catch_line') or '',
            'qualification': state['qualification'], 'facts': facts, 'sections': sections, 'links': _links(row)}


def for_state(usps: str):
    """Compact hook for the #state/SD page: up to 25 titles, or None."""
    state, _reason = _load()
    if state is None or not isinstance(usps, str) or usps.upper() != 'SD':
        return None
    rows = state['rows']
    if not rows:
        return None
    results = [{'id': r['id'], 'title': _title_label(r), 'subtitle': r.get('catch_line') or ''} for r in rows[:25]]
    return {
        'total': len(rows), 'results': results,
        'qualification': state['qualification'],
        'link': '#sd-statutes'}
