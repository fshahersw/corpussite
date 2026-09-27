"""Read-only, hash-gated adapter for official federal court statistics (sources/federal_court_statistics_20260919).

Generic view contract: listing(params), detail(id), original(file_id). The supplement is loaded fail-closed: validation.json
must carry the uniform envelope with status == "passed", ready == true and a matching SHA-256 for every data file. Public
dicts carry no filesystem paths. Original files are served only from raw/ and only when their bytes re-hash, at serve time,
to the SHA-256 recorded in tables.jsonl. Every figure is a publisher-reported count for the stated period or as-of date;
this module computes no rates, and CJRA judge names stay as printed (never linked to a person by name).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter, OrderedDict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/federal_court_statistics_20260919'
DATA_FILES = ('tables.jsonl', 'cjra_rows.jsonl')
FILE_ROUTE = '/supplement-files/court_statistics/'
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
CJRA_ROWS_IN_TABLE_VIEW = 60
MIME = {'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'pdf': 'application/pdf',
        'html': 'text/html; charset=utf-8'}
CAVEATS = ('Figures are counts reported by the Administrative Office of the U.S. Courts for the stated period or as-of date. '
           'This archive computes no rates; any percent column in a preview is the publisher\'s own cell, shown as stored in the file. '
           'Civil counts include MDL member cases, which can dominate a district\'s totals.')
_FILE_ID = re.compile(r'^[a-z][0-9]{3}-[0-9a-f]{12}$')
_LOCK = threading.Lock()
_CACHE: dict = {}


# ----------------------------------------------------------------------------- gate
def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(folder: Path):
    sig = []
    for name in ('validation.json',) + DATA_FILES:
        try:
            st = (folder / name).stat()
        except OSError:
            return None
        sig.append((name, st.st_size, st.st_mtime_ns))
    return tuple(sig)


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
    base, seen = folder.resolve(), {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str):
            return None, 'malformed data_files entry'
        file = (folder / entry['path']).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return None, 'data file missing or outside the supplement: %s' % entry['path']
        try:
            data = file.read_bytes()
        except OSError:
            return None, 'data file unreadable: %s' % entry['path']
        if _digest(data) != entry['sha256']:
            return None, 'hash mismatch: %s' % entry['path']
        seen[Path(entry['path']).name] = data
    missing = [n for n in DATA_FILES if n not in seen]
    if missing:
        return None, 'data files not registered in validation.json: %s' % ', '.join(missing)
    try:
        tables = [json.loads(l) for l in seen['tables.jsonl'].decode('utf-8').splitlines() if l.strip()]
        cjra = [json.loads(l) for l in seen['cjra_rows.jsonl'].decode('utf-8').splitlines() if l.strip()]
    except (ValueError, UnicodeDecodeError):
        return None, 'data file is not valid JSON lines'
    by_id, by_file = OrderedDict(), {}
    for t in tables:
        tid, fid = t.get('table_id'), t.get('file_id')
        if not isinstance(tid, str) or not tid or tid in by_id or not isinstance(fid, str) or not _FILE_ID.match(fid) or fid in by_file:
            return None, 'tables.jsonl identity problem (missing or duplicate table_id / file_id)'
        if not isinstance(t.get('sha256'), str) or not isinstance(t.get('raw_path'), str):
            return None, 'tables.jsonl row without sha256 or registered original'
        by_id[tid], by_file[fid] = t, t
    rows_by_table = {}
    for r in cjra:
        if r.get('table_id') not in by_id:
            return None, 'cjra_rows.jsonl row points at an unknown table'
        rows_by_table.setdefault(r['table_id'], []).append(r)
    ordered = sorted(by_id.values(), key=lambda t: (-(date.fromisoformat(t['period_end']).toordinal() if t.get('period_end') else 0),
                                                    t.get('series_code') or '', _natural(t.get('table_number') or '~'), t['table_id']))
    return {'gate': gate, 'tables': by_id, 'files': by_file, 'ordered': ordered, 'cjra': rows_by_table,
            'qualification': _qualification(gate, len(by_id))}, None


def _natural(text: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', text)]


def _qualification(gate, n):
    text = str(gate.get('qualification') or '').strip()
    if 'never a rate' not in text:
        text = (text + ' ' if text else '') + 'Every figure is a publisher-reported count for the stated period, never a rate computed here.'
    return text


def _state(folder=None):
    folder = Path(folder) if folder else DATA
    sig = _signature(folder)
    if sig is None:
        return None, 'supplement files are missing'
    key = str(folder)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
        state, reason = _verify(folder)
        _CACHE[key] = (sig, state, reason)
        return state, reason


# ----------------------------------------------------------------------------- formatting
def _fmt_date(iso):
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return '%s %d, %d' % (d.strftime('%B'), d.day, d.year)


def _cell_text(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, int):
        return '{:,}'.format(v) if abs(v) >= 10000 or v < 0 or not (1900 <= v <= 2100) else str(v)
    if isinstance(v, float):
        return '{:,.2f}'.format(v).rstrip('0').rstrip('.')
    return str(v)


def _period_text(t):
    end = _fmt_date(t.get('period_end'))
    if not end:
        return 'Not stated'
    label = t.get('period_label') or ''
    return label if (label and len(label) <= 70 and 'listed' not in label.lower() and 'reporting period' not in label.lower()) else 'Period ending %s' % end


def _links(t):
    links = [{'label': 'Original file (%s, as saved)' % t['format'].upper(), 'url': FILE_ROUTE + t['file_id']}]
    if t.get('source_page'):
        links.append({'label': 'Publisher page (uscourts.gov)', 'url': t['source_page']})
    return links


def _result(t, state):
    badges = [t['format'].upper()]
    if t['table_id'] in state['cjra']:
        badges.append('%s per-judge rows parsed' % '{:,}'.format(len(state['cjra'][t['table_id']])))
    if not t.get('period_end'):
        badges.append('Period not stated')
    number = t.get('table_number')
    return {'id': t['table_id'], 'title': t['title'],
            'subtitle': '%s%s' % (t.get('publication_as_listed') or t['series'], (' · Table %s' % number) if number else ''),
            'cells': {'table': number or '—', 'series': t['series'], 'period': _period_text(t), 'format': t['format'].upper()},
            'badges': badges, 'links': _links(t)}


def _facet(name, label, pairs):
    return {'name': name, 'label': label, 'type': 'select', 'options': [{'value': v, 'label': l, 'count': c} for v, l, c in pairs]}


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------- public API
def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'filters': [], 'columns': [], 'results': []}
    tables = state['ordered']
    q = str(params.get('q') or '').strip().lower()
    wanted = {k: str(params.get(k) or '').strip() for k in ('series', 'topic', 'format', 'period')}

    def keep(t):
        if wanted['series'] and t.get('series_code') != wanted['series']:
            return False
        if wanted['topic'] and t.get('topic') != wanted['topic']:
            return False
        if wanted['format'] and t.get('format') != wanted['format'].lower():
            return False
        if wanted['period'] and (t.get('period_end') or 'undated') != wanted['period']:
            return False
        if q:
            hay = ' '.join(str(t.get(k) or '') for k in ('title', 'title_in_file', 'table_number', 'series', 'publication_as_listed', 'topic', 'period_label')).lower()
            hay += ' table ' + str(t.get('table_number') or '').lower() + ' ' + ' '.join(t.get('sheet_names') or []).lower()
            return all(term in hay for term in q.split())
        return True

    matched = [t for t in tables if keep(t)]
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)
    series = Counter((t['series_code'], t['series']) for t in tables)
    topics = Counter(t.get('topic') or 'Other' for t in tables)
    formats = Counter(t['format'] for t in tables)
    periods = Counter(t.get('period_end') or 'undated' for t in tables)
    filters = [
        {'name': 'q', 'label': 'Search', 'type': 'search'},
        _facet('series', 'Series', [(c, l, n) for (c, l), n in sorted(series.items(), key=lambda kv: (-kv[1], kv[0][1]))]),
        _facet('topic', 'Topic', [(k, k, n) for k, n in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))]),
        _facet('format', 'Format', [(k, k.upper(), n) for k, n in sorted(formats.items(), key=lambda kv: (-kv[1], kv[0]))]),
        _facet('period', 'Period', [(k, 'Ending / as of %s' % _fmt_date(k), n) for k, n in sorted(periods.items(), reverse=True) if k != 'undated']
               + ([('undated', 'Not stated', periods['undated'])] if periods.get('undated') else [])),
    ]
    return {'available': True, 'total': len(matched), 'page': page, 'limit': limit, 'qualification': state['qualification'],
            'filters': filters,
            'columns': [{'key': 'table', 'label': 'Table'}, {'key': 'series', 'label': 'Series'}, {'key': 'period', 'label': 'Period'},
                        {'key': 'format', 'label': 'Format'}],
            'results': [_result(t, state) for t in matched[(page - 1) * limit:(page - 1) * limit + limit]]}


def _court_slug(court):
    return re.sub(r'[^a-z0-9]+', '-', str(court or 'unknown').lower()).strip('-')


def _preview_section(p):
    header_rows, data = p.get('header_rows') or [], p.get('data_rows') or []
    width = max([len(r) for r in header_rows + data] or [0])
    header = []
    for i in range(width):
        parts = []
        for r in header_rows:
            v = _cell_text(r[i]) if i < len(r) else ''
            if v and v not in parts:
                parts.append(v)
        header.append(' / '.join(parts))
    rows = [[_cell_text(r[i]) if i < len(r) else '' for i in range(width)] for r in data]
    note = 'first %d of %s data rows%s; header cells joined top to bottom; numbers shown to at most 2 decimals' % (
        len(rows), '{:,}'.format(p.get('data_rows_in_sheet') or len(rows)), ', columns cut at 40' if p.get('columns_truncated') else '')
    return {'heading': 'Preview: sheet "%s" (%s)' % (str(p.get('sheet') or '').strip(), note), 'header': header, 'rows': rows}


def _cjra_sections(t, rows, court=None):
    sections = []
    info = t.get('cjra_per_judge') or {}
    label = rows[0].get('count_label') if rows else 'count'
    if court is None:
        recon = info.get('reconciliation_vs_cjra_table_2') or []
        if recon:
            sections.append({'heading': 'Reconciliation: sum of printed per-judge totals against CJRA Table 2 (same report, %s)' % (t.get('period_label') or ''),
                             'header': ['Circuit', 'Sum of per-judge totals', 'CJRA Table 2 printed', 'Result'],
                             'rows': [[x['circuit'], _cell_text(x['sum_of_per_judge_totals']), _cell_text(x['cjra_table_2_printed']),
                                       'matches' if x['match'] else 'differs'] for x in recon]})
    shown = rows if court is not None else rows[:CJRA_ROWS_IN_TABLE_VIEW]
    sections.append({'heading': 'Per-judge totals as printed (%s; %s) - %s of %s rows, in document order' % (
        label, t.get('period_label') or 'period not stated', '{:,}'.format(len(shown)), '{:,}'.format(len(rows))),
        'header': ['Court (as printed)', 'Judge type (as printed)', 'Judge name (as printed)', 'Count', 'PDF pages'],
        'rows': [[r.get('court_as_printed') or '', r.get('judge_type_as_printed') or '', r.get('judge_name_as_printed') or '', _cell_text(r.get('count')),
                  '-'.join(str(p) for p in OrderedDict.fromkeys(r.get('pdf_pages') or []))] for r in shown]})
    if court is None:
        courts = OrderedDict()
        for r in rows:
            courts.setdefault(r.get('court_as_printed') or 'Court not printed', []).append(r)
        sections.append({'heading': 'Courts in this table (open one with the listed id to see all of its rows)',
                         'items': [{'id': '%s@%s' % (t['table_id'], _court_slug(name)), 'title': name,
                                    'subtitle': '%d judge rows; detail id %s@%s' % (len(rs), t['table_id'], _court_slug(name)), 'links': []}
                                   for name, rs in courts.items()]})
    return sections


def detail(ident, folder=None):
    state, _ = _state(folder)
    if state is None or not isinstance(ident, str) or not ident:
        return None
    table_id, _, court = ident.partition('@')
    t = state['tables'].get(table_id)
    if t is None:
        return None
    cjra_rows = state['cjra'].get(table_id) or []
    if court:
        cjra_rows = [r for r in cjra_rows if _court_slug(r.get('court_as_printed')) == court]
        if not cjra_rows:
            return None
    end = _fmt_date(t.get('period_end'))
    facts = [['Table', t.get('table_number') or 'No table number on the listing'], ['Series', t['series']],
             ['Publication (as listed)', t.get('publication_as_listed') or ''], ['Topic', t.get('topic') or '']]
    if t.get('title_in_file'):
        facts.append(['Title inside the file', t['title_in_file']])
    facts.append(['Reporting period (as printed)', '%s [source: %s]' % (t['period_label'], t.get('period_basis')) if t.get('period_label') else 'Not stated in the file or the listing'])
    if end:
        facts.append(['Period end / as-of date', end])
    if t.get('period_start'):
        facts.append(['Period start', '%s [%s]' % (_fmt_date(t['period_start']), t.get('period_start_basis') or 'computed')])
    facts += [['Published', 'Not stated by the publisher in the saved material'],
              ['Saved (capture time, from the fetch receipt)', t.get('captured_at') or 'unknown'],
              ['Format', t['format'].upper()], ['Size', '%d bytes' % t['bytes']], ['SHA-256 (prefix)', t['sha256'][:16] + '…'],
              ['Source page', t.get('source_page') or ''], ['File URL', t.get('source_url') or '']]
    if t.get('sheet_names'):
        facts.append(['Sheets', ', '.join(s.strip() for s in t['sheet_names'])])
    if t.get('page_count'):
        facts.append(['PDF pages', '{:,}'.format(t['page_count'])])
    sections = [{'heading': 'How to read these figures', 'text': CAVEATS}]
    for p in (t.get('preview') or []) if not court else []:
        sections.append(_preview_section(p))
    if cjra_rows:
        sections += _cjra_sections(t, cjra_rows, court=court or None)
    elif t['format'] == 'pdf':
        sections.append({'heading': 'Contents', 'text': 'Registered original PDF. No rows were extracted from this file; open the original.'})
    elif t['format'] == 'html':
        sections.append({'heading': 'Contents', 'text': 'Saved copy of a live publisher page. No rows were extracted; the page may have changed since it was saved.'})
    qualification = state['qualification']
    if cjra_rows:
        qualification += ' Judge names are as printed in the CJRA report and are not linked to judge profiles; people are never merged by name alone.'
    title = t['title'] if not court else '%s - %s' % (t['title'], cjra_rows[0].get('court_as_printed') or court)
    return {'id': ident, 'title': title, 'subtitle': '%s · %s' % (t['series'], _period_text(t)), 'qualification': qualification,
            'facts': [[str(a), str(b)] for a, b in facts], 'sections': sections, 'links': _links(t)}


def original(file_id, folder=None):
    """(bytes, mime, filename) for a registered original, re-hashed at serve time and confined to raw/; else None."""
    if not isinstance(file_id, str) or not _FILE_ID.match(file_id):
        return None
    state, _ = _state(folder)
    if state is None:
        return None
    t = state['files'].get(file_id)
    if t is None:
        return None
    base = Path(folder) if folder else DATA
    raw_root = (base / 'raw').resolve()
    try:
        path = (base / t['raw_path']).resolve()
        if not path.is_relative_to(raw_root) or not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) != t.get('bytes') or _digest(data) != t['sha256']:
        return None
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(t.get('download_name') or file_id))
    return data, MIME.get(t['format'], 'application/octet-stream'), name
