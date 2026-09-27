#!/usr/bin/env python3
"""Judge evidence blocks keyed by judge entity id (CJRA counts, saved-docket assignments, CourtListener education/positions).

Layer: sources/judge_evidence_20260919 (evidence.jsonl + unresolved.jsonl + validation.json, uniform envelope).
Fail-closed: nothing is served unless validation.json says status == "passed" and ready is true and every declared data file
re-hashes to its recorded SHA-256. Rows are parsed only from the bytes that were hashed. Public dicts carry no filesystem
paths. The layer holds publisher-reported counts and dataset counts only: no rates, predictions or rankings.
"""
from __future__ import annotations
import hashlib
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'sources/judge_evidence_20260919'
DATA_FILES = ('evidence.jsonl', 'unresolved.jsonl')
BLOCKS = (('cjra', 'CJRA counts (publisher-reported)'), ('assignments', 'Matters in the saved docket dataset'),
          ('education', 'Education (CourtListener)'), ('positions', 'Positions (CourtListener)'))
_CACHE = {}
_LOCK = threading.Lock()


def _signature(folder: Path):
    out = []
    for name in ('validation.json',) + DATA_FILES:
        stat = (folder / name).stat()
        out.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(out)


def _load(folder: Path):
    try:
        validation = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'validation_missing_or_unreadable'
    if not isinstance(validation, dict) or validation.get('status') != 'passed' or validation.get('ready') is not True:
        return None, 'validation_not_passed'
    declared = {f.get('path'): f for f in validation.get('data_files') or [] if isinstance(f, dict)}
    if set(declared) != set(DATA_FILES):
        return None, 'data_files_not_declared_exactly'
    parsed = {}
    for name in DATA_FILES:
        try:
            data = (folder / name).read_bytes()
        except OSError:
            return None, 'data_file_missing:' + name
        if hashlib.sha256(data).hexdigest() != declared[name].get('sha256'):
            return None, 'data_file_hash_mismatch:' + name
        try:
            rows = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
        except ValueError:
            return None, 'data_file_unreadable:' + name
        if len(rows) != declared[name].get('rows'):
            return None, 'data_file_row_count_mismatch:' + name
        parsed[name] = rows
    by_id = {}
    for row in parsed['evidence.jsonl']:
        entity_id = row.get('entity_id') if isinstance(row, dict) else None
        if not isinstance(entity_id, str) or not entity_id.startswith('judge-entity-') or entity_id in by_id:
            return None, 'evidence_row_without_unique_entity_id'
        by_id[entity_id] = row
    return {'by_id': by_id, 'order': sorted(by_id.values(), key=lambda r: (str(r.get('name') or '').casefold(), r['entity_id'])),
            'unresolved': parsed['unresolved.jsonl'], 'counts': validation.get('counts') or {},
            'validated_at': validation.get('validated_at'), 'qualification': validation.get('qualification') or ''}, None


def _state(folder=None):
    folder = Path(folder) if folder else FOLDER
    try:
        signature = _signature(folder)
    except OSError:
        return None, 'layer_missing'
    key = str(folder)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == signature:
            return cached[1], cached[2]
        state, reason = _load(folder)
        _CACHE[key] = (signature, state, reason)
        return state, reason


def _closed(reason):
    return {'available': False, 'reason': reason}


def _page(params, default_limit=25):
    def number(name, default):
        try:
            return int(str((params or {}).get(name) or default))
        except ValueError:
            return default
    return max(1, number('page', 1)), max(1, min(100, number('limit', default_limit)))


def _copy(value):
    return json.loads(json.dumps(value))


def evidence_for(entity_id, folder=None):
    """All evidence blocks for one judge entity, or None (unknown entity, no evidence, or the gate is closed)."""
    state, _ = _state(folder)
    if not state or not isinstance(entity_id, str):
        return None
    row = state['by_id'].get(entity_id)
    return _copy(row) if row else None


def summary(folder=None):
    state, reason = _state(folder)
    if not state:
        return _closed(reason)
    return {'available': True, 'validated_at': state['validated_at'], 'qualification': state['qualification'],
            'counts': dict(state['counts']),
            'blocks': [{'block': b, 'label': label, 'entities': state['counts'].get('entities_with_' + b)} for b, label in BLOCKS]}


def cjra_unresolved(page=1, limit=50, reason=None, q=None, folder=None):
    """CJRA printed judges that were NOT attached to any entity, with the reason (never guessed)."""
    state, why = _state(folder)
    if not state:
        return _closed(why)
    page, limit = _page({'page': page, 'limit': limit}, 50)
    rows = [r for r in state['unresolved'] if r.get('kind') == 'cjra']
    reasons = {}
    for r in rows:
        reasons[r['reason_code']] = reasons.get(r['reason_code'], 0) + 1
    if reason:
        rows = [r for r in rows if r.get('reason_code') == reason]
    if q:
        needle = str(q).casefold()
        rows = [r for r in rows if needle in (' '.join(r.get('names_as_printed') or []) + ' ' + str(r.get('court_as_printed'))).casefold()]
    start = (page - 1) * limit
    return {'available': True, 'total': len(rows), 'page': page, 'limit': limit,
            'qualification': 'CJRA printed judges (as of 2026-03-31) that are not attached to a saved judge profile, with the reason. '
                             'Counts are publisher-reported; a name alone is never used to attach a row.',
            'reasons': [{'value': k, 'count': v} for k, v in sorted(reasons.items())],
            'results': _copy(rows[start:start + limit])}


# ------------------------------------------------------------------------------------------------ generic view contract
def _courts(row):
    names = []
    if row.get('cjra'):
        names += [b['match_basis']['fjc_court'] for b in [row['cjra']] + (row['cjra'].get('other_courts') or [])]
    if row.get('assignments'):
        names += [c.get('court_name') or c.get('court_id') for c in row['assignments']['by_court'][:2]]
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def _cjra_cell(cjra):
    """Counts as printed. A judge printed in more than one court gets one court-labelled segment per court; never summed."""
    if not cjra:
        return ''
    blocks = [cjra] + (cjra.get('other_courts') or [])
    texts = ['; '.join('%s: %s' % (t['table'], format(t['count'], ',')) for t in b['tables']) for b in blocks]
    if len(blocks) == 1:
        return texts[0]
    labels = [str(b.get('court_as_printed') or '').replace('U.S. District Court for ', '', 1) or 'Court not printed' for b in blocks]
    return ' | '.join('%s - %s' % (label, text) for label, text in zip(labels, texts))


def listing(params=None, folder=None):
    state, reason = _state(folder)
    if not state:
        return _closed(reason)
    params = params or {}
    page, limit = _page(params)
    q = str(params.get('q') or '').strip().casefold()
    block = str(params.get('block') or '').strip()
    rows = state['order']
    if block in dict(BLOCKS):
        rows = [r for r in rows if block in (r.get('blocks') or [])]
    if q:
        rows = [r for r in rows if q in (str(r.get('name')) + ' ' + ' '.join(_courts(r))).casefold()]
    start = (page - 1) * limit
    results = []
    for r in rows[start:start + limit]:
        cjra, assignments = r.get('cjra'), r.get('assignments')
        results.append({
            'id': r['entity_id'], 'title': r.get('name') or r['entity_id'], 'subtitle': '; '.join(_courts(r)[:2]),
            'cells': {'cjra': _cjra_cell(cjra),
                      'matters': assignments['dataset_label'].split('; ')[-1].rstrip(')') if assignments else '',
                      'education': str(len(r.get('education') or [])) if r.get('education') else '',
                      'positions': str(len(r.get('positions') or [])) if r.get('positions') else ''},
            'badges': [dict(BLOCKS)[b] for b in r.get('blocks') or []],
            'links': [{'label': 'Judge profile', 'url': '#judge/' + r['entity_id']}]})
    return {'available': True, 'total': len(rows), 'page': page, 'limit': limit,
            'qualification': state['qualification'],
            'filters': [{'name': 'q', 'label': 'Search judge or court', 'type': 'search'},
                        {'name': 'block', 'label': 'Evidence block', 'type': 'select',
                         'options': [{'value': b, 'label': label, 'count': state['counts'].get('entities_with_' + b)} for b, label in BLOCKS]}],
            'columns': [{'key': 'cjra', 'label': 'CJRA counts as printed (as of 2026-03-31)'},
                        {'key': 'matters', 'label': 'Matters in saved docket dataset'},
                        {'key': 'education', 'label': 'Education rows (CourtListener)'},
                        {'key': 'positions', 'label': 'Position rows (CourtListener)'}],
            'results': results}


def detail(entity_id, folder=None):
    row = evidence_for(entity_id, folder)
    if not row:
        return None
    state, _ = _state(folder)
    facts, sections = [], []
    for label, key in (('CourtListener person id', 'cl_person_id'), ('FJC nid', 'fjc_nid'), ('FJC jid', 'fjc_jid')):
        if row['ids'].get(key):
            facts.append([label, row['ids'][key]])
    cjra = row.get('cjra')
    for block in ([cjra] + cjra['other_courts']) if cjra else []:
        facts.append(['CJRA counts as of', block['as_of']])
        sections.append({'heading': 'CJRA counts as printed for %s, %s (as of %s)' % (block['judge_name_as_printed'], block['court_as_printed'], block['as_of']),
                         'header': ['Table', 'What is counted', 'Count as printed'],
                         'rows': [[t['table'], t['label'], format(t['count'], ',')] for t in block['tables']]})
        sections.append({'heading': 'How this CJRA row was attached',
                         'text': '%s. %s FJC name: %s; FJC court: %s. Not independently verified.'
                                 % (block['caveat'].capitalize(), block['match_basis']['rule'].capitalize() + '.',
                                    block['match_basis']['fjc_name'], block['match_basis']['fjc_court'])})
    assignments = row.get('assignments')
    if assignments:
        facts.append(['Docket dataset release built', str(assignments['release_built_at'])[:10]])
        facts.append(['Assigned / referred matters in the dataset', '%s / %s' % (assignments['assigned_count'], assignments['referred_count'])])
        if assignments.get('date_filed_first'):
            facts.append(['Filing dates in the dataset', '%s to %s' % (assignments['date_filed_first'], assignments['date_filed_last'])])
        sections.append({'heading': assignments['dataset_label'][0].upper() + assignments['dataset_label'][1:], 'text': assignments['dataset_note']})
        sections.append({'heading': 'By court (dataset counts)', 'header': ['Court', 'Matters'],
                         'rows': [[c.get('court_name') or c.get('court_id'), str(c['matters'])] for c in assignments['by_court']]})
        if assignments['by_nature_of_suit']:
            sections.append({'heading': 'By nature of suit as recorded by CourtListener (top 8; dataset counts)', 'header': ['Nature of suit', 'Matters'],
                             'rows': [[n['nature_of_suit'], str(n['matters'])] for n in assignments['by_nature_of_suit']]})
        if assignments['mdl_parents']:
            sections.append({'heading': 'MDL parent dockets linked in the dataset', 'header': ['Docket', 'Case', 'Court', 'Member matters of this judge'],
                             'rows': [[p['docket_number'], p['case_name'], p.get('court_name') or p.get('court_id'), str(p['member_matters_of_this_judge'])]
                                      for p in assignments['mdl_parents']]})
        sections.append({'heading': 'Most recent matters in the dataset (up to 25, by filing date)',
                         'header': ['Docket number', 'Case name', 'Court', 'Filed', 'Terminated'],
                         'rows': [[m['docket_number'], m['case_name'], m.get('court_name') or m.get('court_id'), m['date_filed'] or '', m['date_terminated'] or '']
                                  for m in assignments['recent_matters']]})
    source = row.get('courtlistener_source') or {}
    if source.get('source_snapshot'):
        facts.append(['CourtListener bulk snapshot', source['source_snapshot']])
    if row.get('education'):
        sections.append({'heading': 'Education as recorded by CourtListener', 'header': ['School', 'Degree', 'Year'],
                         'rows': [[e.get('school') or '', e.get('degree_detail') or e.get('degree_level') or '', e.get('degree_year') or ''] for e in row['education']]})
    if row.get('positions'):
        sections.append({'heading': 'Positions as recorded by CourtListener (dates at its recorded granularity)', 'header': ['Position', 'Where', 'From', 'To'],
                         'rows': [[p.get('job_title') or p.get('position_type') or '', p.get('court_name') or p.get('organization_name') or p.get('school') or '',
                                   p.get('date_start_display') or '', p.get('date_termination_display') or ''] for p in row['positions']]})
    return {'id': row['entity_id'], 'title': row.get('name') or row['entity_id'], 'subtitle': '; '.join(_courts(row)[:2]),
            'qualification': state['qualification'], 'facts': facts, 'sections': sections,
            'links': [{'label': 'Judge profile', 'url': '#judge/' + row['entity_id']}]}
