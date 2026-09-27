"""Read-only, hash-gated adapter for Public Laws and their U.S. Code effects (sources/public_law_uscode_20260919).

Generic view contract: listing(params), detail(id), plus for_usc(title, section). One row per Public Law from GovInfo PLAW
bulk XML, with its classified U.S. Code effects (amends/adds/repeals/redesignates/transfers/appropriates, or
reference_only when the citation is not itself a code change) and candidate temporal/effective notes. Fail-closed on a
missing/failed validation or hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/public_law_uscode_20260919'
DB_NAME = 'public_law_uscode.sqlite3'
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
_LOCK = threading.Lock()
_CACHE: dict = {}

ACTION_LABELS = {
    'amends': 'Amends', 'adds': 'Adds', 'repeals': 'Repeals', 'redesignates': 'Redesignates',
    'transfers': 'Transfers', 'appropriates': 'Appropriates', 'mixed_direct_actions': 'Multiple actions',
    'mixed_contextual_actions': 'Contextual (candidate)', 'reference_only': 'References (no code change)',
    'unclassified': 'Unclassified',
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
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)} if isinstance(gate, dict) else {}
            if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
                reason = 'supplement not published (status/ready gate closed)'
            elif not expected.get(DB_NAME) or _sha256(folder / DB_NAME) != expected[DB_NAME]:
                reason = 'index file does not match its recorded SHA-256'
            else:
                state = {'folder': folder, 'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}}
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


def _text(params, name):
    value = params.get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value or '').strip()[:160]


_STOP_WORDS = {'a', 'an', 'the', 'of', 'to', 'and', 'or', 'for', 'in', 'on', 'act'}


def _fts_query(text):
    terms = [t for t in re.findall(r'[\w][\w.\-]*', text, re.UNICODE)[:8] if t.lower() not in _STOP_WORDS]
    return ' '.join('"%s"' % term for term in terms)


def _url(row):
    return row['source_url'] or ('https://www.govinfo.gov/app/details/%s' % row['package_id'])


def _law_label(row):
    return row['law_citation'] or row['package_id']


def _result(row, effect_counts=None):
    badges = []
    if row['public_private'] and row['public_private'] != 'public':
        badges.append(row['public_private'].capitalize())
    if effect_counts:
        badges.append('%d U.S. Code effect(s)' % effect_counts)
    return {'id': row['id'], 'title': row['title'] or _law_label(row), 'subtitle': _law_label(row),
            'cells': {'congress': row['congress'], 'approved': row['approved_date'] or 'not recorded',
                      'citation': row['law_citation'] or '', 'statutes': row['statutes_citation'] or ''},
            'badges': badges, 'links': [{'label': 'Open on GovInfo', 'url': _url(row)}]}


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    state, reason = _state(folder)
    if state is None:
        return _unavailable(reason)
    page = _int(params.get('page'), 1, 1, 100000)
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    clauses, values = [], []
    congress = _text(params, 'congress')
    if re.fullmatch(r'\d{1,3}', congress):
        clauses.append('l.congress=?'); values.append(int(congress))
    year = _text(params, 'year')
    if re.fullmatch(r'\d{4}', year):
        clauses.append("l.approved_date LIKE ?"); values.append(year + '-%')
    usc_title = _text(params, 'usc_title')
    action = _text(params, 'action')
    if re.fullmatch(r'\d{1,3}', usc_title) or action:
        sub_clauses, sub_values = [], []
        if re.fullmatch(r'\d{1,3}', usc_title):
            sub_clauses.append('usc_title=?'); sub_values.append(int(usc_title))
        if action:
            sub_clauses.append('action_type=?'); sub_values.append(action)
        clauses.append('l.id IN (SELECT law_id FROM usc_effects WHERE ' + ' AND '.join(sub_clauses) + ')')
        values += sub_values
    query = _fts_query(_text(params, 'q'))
    try:
        connection = _connect(state)
        try:
            if query:
                base = 'FROM laws_fts f JOIN laws l ON l.id=f.law_id WHERE laws_fts MATCH ?' + ''.join(' AND ' + clause for clause in clauses)
                arguments = [query] + values
            else:
                base = 'FROM laws l' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                arguments = values
            total = state['counts'].get('laws', 0) if not query and not clauses else connection.execute('SELECT count(*) ' + base, arguments).fetchone()[0]
            rows = connection.execute(f'SELECT l.* {base} ORDER BY l.congress DESC, l.approved_date DESC, l.id LIMIT ? OFFSET ?',
                                       arguments + [limit, (page - 1) * limit]).fetchall()
            law_ids = [row['id'] for row in rows]
            effect_counts = {}
            if law_ids:
                placeholders = ','.join('?' for _ in law_ids)
                for law_id, n in connection.execute(
                        f'SELECT law_id, count(*) FROM usc_effects WHERE law_id IN ({placeholders}) AND is_action=1 GROUP BY law_id', law_ids):
                    effect_counts[law_id] = n
            congresses = connection.execute('SELECT congress, count(*) FROM laws WHERE congress IS NOT NULL GROUP BY congress ORDER BY congress DESC').fetchall()
            action_types = connection.execute('SELECT action_type, count(*) FROM usc_effects GROUP BY action_type ORDER BY 2 DESC').fetchall()
            titles = connection.execute('SELECT usc_title, count(*) FROM usc_effects GROUP BY usc_title ORDER BY usc_title').fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return _unavailable('index could not be read: %s' % type(error).__name__)
    filters = [
        {'name': 'q', 'label': 'Search law titles and citations', 'type': 'search', 'placeholder': 'e.g. flood insurance, Public Law 113-1, 127 Stat. 3'},
        {'name': 'congress', 'label': 'Congress', 'type': 'select', 'options': [{'value': str(c), 'label': '%sth Congress' % c, 'count': n} for c, n in congresses]},
        {'name': 'usc_title', 'label': 'U.S. Code title affected', 'type': 'select', 'options': [{'value': str(t), 'label': 'Title %s' % t, 'count': n} for t, n in titles]},
        {'name': 'action', 'label': 'U.S. Code effect type', 'type': 'select',
         'options': [{'value': a, 'label': ACTION_LABELS.get(a, a), 'count': n} for a, n in action_types]},
        {'name': 'year', 'label': 'Year approved', 'type': 'search', 'placeholder': 'e.g. 2013'},
    ]
    return {'available': True, 'total': total, 'page': page, 'limit': limit, 'qualification': state['qualification'], 'filters': filters,
            'columns': [{'key': 'title', 'label': 'Public Law'}, {'key': 'congress', 'label': 'Congress'}, {'key': 'approved', 'label': 'Approved'},
                        {'key': 'citation', 'label': 'Citation'}, {'key': 'statutes', 'label': 'Statutes at Large'}],
            'results': [_result(row, effect_counts.get(row['id'])) for row in rows]}


def detail(item_id, folder=None):
    state, _ = _state(folder)
    if state is None or not item_id or len(str(item_id)) > 64:
        return None
    try:
        connection = _connect(state)
        try:
            row = connection.execute('SELECT * FROM laws WHERE id=?', (str(item_id),)).fetchone()
            if row is None:
                return None
            effects = connection.execute(
                'SELECT * FROM usc_effects WHERE law_id=? ORDER BY usc_title, usc_section, action_type', (row['id'],)).fetchall()
            notes = connection.execute(
                'SELECT * FROM temporal_notes WHERE law_id=? ORDER BY usc_title, usc_section', (row['id'],)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    facts = [['Congress', row['congress']], ['Law number', row['law_number']], ['Public/private law', row['public_private'] or 'public'],
             ['Approved (enacted)', row['approved_date'] or 'not recorded in the source'],
             ['Citation', row['law_citation'] or 'not recorded'], ['Statutes at Large', row['statutes_citation'] or 'not recorded'],
             ['GovInfo package', row['package_id']]]
    effects_section = {'label': 'U.S. Code effects (%d)' % len(effects), 'type': 'table',
                        'columns': ['U.S. Code cite', 'Effect', 'Occurrences', 'Basis'],
                        'rows': [['%s U.S.C. %s%s' % (e['usc_title'], e['usc_section'], (' ' + e['pinpoint']) if e['pinpoint'] else ''),
                                  ACTION_LABELS.get(e['action_type'], e['action_type']), e['occurrence_count'],
                                  e['confidence_basis'] or e['evidence_status'] or ''] for e in effects]}
    sections = [effects_section]
    if notes:
        sections.append({'label': 'Temporal / effective-date notes (candidate, %d)' % len(notes), 'type': 'table',
                          'columns': ['U.S. Code cite', 'Type', 'Confidence', 'Resolved date', 'Text'],
                          'rows': [['%s U.S.C. %s' % (n['usc_title'], n['usc_section']), n['temporal_type'], n['confidence'],
                                    n['resolved_date'] or 'unresolved', (n['note_text'] or '')[:240]] for n in notes]})
    links = [{'label': 'Open on GovInfo', 'url': _url(row)}]
    for title in sorted({e['usc_title'] for e in effects})[:12]:
        links.append({'label': 'Other Public Laws touching U.S.C. Title %s' % title, 'url': '#public-laws?usc_title=%s' % title})
    return {'title': row['title'] or _law_label(row), 'subtitle': _law_label(row), 'facts': facts, 'sections': sections,
            'links': links, 'qualification': state['qualification']}


def for_usc(title, section, folder=None):
    """Public Laws whose classified U.S. Code effects touch usc_title/usc_section. Compact block, capped at 25 rows."""
    if not re.fullmatch(r'\d{1,3}', str(title or '')) or not re.fullmatch(r'[0-9A-Za-z.\-]{1,24}', str(section or '')):
        return None
    state, _ = _state(folder)
    if state is None:
        return None
    try:
        connection = _connect(state)
        try:
            where = 'e.usc_title=? AND e.usc_section=?'
            values = [int(title), str(section)]
            total = connection.execute(f'SELECT count(DISTINCT e.law_id) FROM usc_effects e WHERE {where}', values).fetchone()[0]
            if not total:
                return None
            by_action = connection.execute(
                f'SELECT e.action_type, count(*) FROM usc_effects e WHERE {where} GROUP BY e.action_type ORDER BY 2 DESC', values).fetchall()
            rows = connection.execute(
                f'SELECT l.*, e.action_type, e.occurrence_count FROM usc_effects e JOIN laws l ON l.id=e.law_id '
                f'WHERE {where} ORDER BY l.approved_date DESC LIMIT 25', values).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return {'total': total, 'by_action': [{'value': a, 'count': n} for a, n in by_action],
            'results': [{'id': row['id'], 'title': row['title'] or _law_label(row),
                         'subtitle': '%s · %s · %s' % (_law_label(row), row['approved_date'] or 'date not recorded',
                                                        ACTION_LABELS.get(row['action_type'], row['action_type']))} for row in rows],
            'link': '#public-laws?usc_title=%s' % title, 'qualification': state['qualification']}
