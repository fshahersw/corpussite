"""Validated, read-only jurisdiction coverage layer (states, DC, territories; law families; source tiers).

Data is produced offline by sources/law_tier_20260919/build.py. This adapter performs no acquisition,
opens no request-supplied path, serves no files and fails closed when validation.json or any listed
data file is missing or altered. Public dictionaries carry no filesystem paths.

Counts are saved records or exported rows, never unique laws. Class labels on the unreviewed official
tier are automated structural triage; they never assert legal currency. Topic results are
search-derived candidates, not legal advice or a complete survey.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
import trellis_coverage

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/law_tier_20260919'
REQUIRED = ('coverage.json', 'topics.json', 'topic_index.jsonl', 'record_labels.jsonl', 'oul_rule_sets.jsonl')
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
HEX = re.compile(r'[a-f0-9]{64}')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,80}')
STATE_TOKEN = re.compile(r"[A-Za-z .'-]{2,40}")
ROW_ID = re.compile(r'[A-Za-z0-9_:.-]{1,160}')
TOPIC_LABEL = 'search-derived candidates, not legal advice or a complete survey'
MATCH_ORDER = {'title': 0, 'text': 1}
_CACHE = {}


def _read_gate(folder):
    gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    if gate.get('status') != 'passed' or gate.get('ready') is not True: return None
    listed = gate.get('data_files')
    if not isinstance(listed, list): return None
    entries = {}
    for item in listed:
        name, digest = item.get('path'), item.get('sha256')
        if not (isinstance(name, str) and NAME.fullmatch(name) and '..' not in name and isinstance(digest, str) and HEX.fullmatch(digest)):
            return None
        entries[name] = digest
    if any(name not in entries for name in REQUIRED): return None
    return entries


def _jsonl(payload):
    return [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]


def _index(coverage, catalog, provisions, labels, rule_sets):
    names = {}
    for abbr, row in coverage['jurisdictions'].items():
        names[abbr.lower()] = abbr
        names[str(row.get('name', '')).lower()] = abbr
    by_state_topic, topic_totals, topic_by_state = {}, {}, {}
    for position, row in enumerate(provisions):
        for tag in row['topics']:
            by_state_topic.setdefault((row['state'], tag['topic']), []).append((MATCH_ORDER.get(tag.get('match'), 9), position))
            topic_totals[tag['topic']] = topic_totals.get(tag['topic'], 0) + 1
            per = topic_by_state.setdefault(row['state'], {})
            per[tag['topic']] = per.get(tag['topic'], 0) + 1
    label_stats = {}
    for row in labels:
        per = label_stats.setdefault(row.get('state'), {'by_class': {}, 'by_rule_set': {}, 'by_confidence': {}})
        per['by_class'][row['law_body_class']] = per['by_class'].get(row['law_body_class'], 0) + 1
        per['by_confidence'][row.get('confidence')] = per['by_confidence'].get(row.get('confidence'), 0) + 1
        if row.get('rule_set'): per['by_rule_set'][row['rule_set']] = per['by_rule_set'].get(row['rule_set'], 0) + 1
    oul_sets, oul_by_state = {}, {}
    for row in rule_sets:
        oul_sets[row['id']] = row
        per = oul_by_state.setdefault(row.get('state'), {})
        per[row['rule_set']] = per.get(row['rule_set'], 0) + 1
    return {'coverage': coverage, 'catalog': catalog, 'provisions': provisions, 'labels': labels, 'names': names,
            'by_state_topic': by_state_topic, 'topic_totals': topic_totals, 'topic_by_state': topic_by_state,
            'label_stats': label_stats, 'oul_sets': oul_sets, 'oul_by_state': oul_by_state}


def load(folder=DATA):
    """Return the decoded, indexed supplement, or None (fail closed). Re-hashes whenever a file's size or mtime changes."""
    try:
        folder = Path(folder)
        entries = _read_gate(folder)
        if entries is None: return None
        stamp = [(folder / 'validation.json').stat().st_mtime_ns]
        for name in sorted(entries):
            info = (folder / name).stat()
            stamp.append((name, entries[name], info.st_size, info.st_mtime_ns))
        key, stamp = str(folder.resolve()), tuple(stamp)
        cached = _CACHE.get(key)
        if cached and cached[0] == stamp: return cached[1]
        payloads = {}
        for name, digest in entries.items():
            payload = (folder / name).read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                _CACHE.pop(key, None)
                return None
            if name in REQUIRED: payloads[name] = payload
        coverage = json.loads(payloads['coverage.json'].decode('utf-8'))
        catalog = json.loads(payloads['topics.json'].decode('utf-8'))
        if not isinstance(coverage.get('jurisdictions'), dict) or not isinstance(catalog.get('topics'), dict): return None
        state = _index(coverage, catalog, _jsonl(payloads['topic_index.jsonl']), _jsonl(payloads['record_labels.jsonl']),
                       _jsonl(payloads['oul_rule_sets.jsonl']))
        _CACHE[key] = (stamp, state)
        return state
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        return None


def _page(page, limit):
    try: page = int(page)
    except (TypeError, ValueError): page = 1
    try: limit = int(limit)
    except (TypeError, ValueError): limit = DEFAULT_LIMIT
    return max(1, page), max(1, min(MAX_LIMIT, limit))


def _resolve(state, token):
    if not isinstance(token, str) or not STATE_TOKEN.fullmatch(token.strip()): return None
    return state['names'].get(token.strip().lower())


def _sort_key(row):
    return (row.get('jurisdiction_kind') == 'territory', str(row.get('name', '')))


def _matrix_row(row):
    return {k: row.get(k) for k in ('name', 'abbr', 'jurisdiction_kind', 'families', 'reviewed_tier', 'gaps',
                                    'county_layer', 'trellis_county_profiles')}


def _refresh_trellis(row, progress):
    """Overlay validated sidecar availability while retaining law snapshot facts."""
    row=copy.deepcopy(row)
    if not progress:return row
    row['trellis_county_profiles']={'observed':progress['observed_county_urls'], 'saved':progress['saved_county_urls'],
        'unsaved':progress['remaining_county_urls'], 'basis':'Distinct observed county-profile URLs; not all Census counties or case documents'}
    row['trellis_unsaved_profiles']={'count':progress['remaining_county_urls'],'urls':progress['remaining_urls']}
    row['gaps']=[g for g in row.get('gaps',[]) if g not in {'trellis_county_profiles_partial','trellis_no_county_profiles_observed'}]
    if progress['remaining_county_urls']:
        row['gaps'].append('trellis_county_profiles_partial')
    if isinstance(row.get('county_layer'),dict):
        row['county_layer']['with_saved_trellis_profile']=progress['unique_resolved_fips']
        row['county_layer']['trellis_count_basis']='Distinct resolved county FIPS with validated profile details across the archive and refreshed sidecar'
    return row


def matrix(folder=DATA):
    """One row per state, DC and territory: counts by law family and source tier, plus the explicit gap lists."""
    state = load(folder)
    if state is None: return {'available': False, 'rows': [], 'gaps': {}, 'totals': {}, 'qualification': ''}
    coverage = state['coverage']
    rows = sorted(coverage['jurisdictions'].values(), key=_sort_key)
    result=copy.deepcopy({'available': True, 'generated_at': coverage.get('generated_at'), 'qualification': coverage.get('qualification', ''),
                          'definitions': coverage.get('definitions', {}), 'rows': [_matrix_row(r) for r in rows],
                          'gaps': coverage.get('gaps', {}), 'totals': coverage.get('totals', {})})
    fresh=trellis_coverage.progress() if Path(folder).resolve()==DATA.resolve() else {}
    if fresh and fresh.get('available'):
        by_state={r['state']:r for r in fresh['states']}
        result['rows']=[_refresh_trellis(r,by_state.get(r['abbr'])) for r in result['rows']]
        result['totals']['trellis_unsaved_county_profiles']=fresh['remaining_county_urls']
        result['gaps']['trellis_county_profiles_partial']=[r['state'] for r in fresh['states'] if r['remaining_county_urls']>0]
        result['trellis_refreshed_at']=fresh['as_of']
        result['trellis_progress']={k:v for k,v in fresh.items() if k not in {'states','remaining_urls'}}
    return result


def state_detail(state_token, folder=DATA):
    """Full coverage row for one jurisdiction (USPS abbreviation or name) with label, rule-set, topic and venue counts."""
    state = load(folder)
    if state is None: return None
    abbr = _resolve(state, state_token)
    if abbr is None: return None
    coverage = state['coverage']
    detail = dict(coverage['jurisdictions'][abbr])
    detail['topic_counts'] = dict(sorted(state['topic_by_state'].get(abbr, {}).items()))
    stats = state['label_stats'].get(abbr, {'by_class': {}, 'by_rule_set': {}, 'by_confidence': {}})
    detail['reviewed_labels'] = {k: dict(sorted(v.items(), key=lambda kv: str(kv[0]))) for k, v in stats.items()}
    detail['open_us_law_rule_sets'] = dict(sorted(state['oul_by_state'].get(abbr, {}).items()))
    unsaved = coverage.get('trellis_unsaved_profiles', {}).get('by_state', {}).get(abbr, {'count': 0, 'urls': []})
    detail['trellis_unsaved_profiles'] = unsaved
    detail['venues'] = [v for v in coverage.get('venues', []) if v.get('state') == abbr]
    detail['topic_label'] = TOPIC_LABEL
    detail['qualification'] = coverage.get('qualification', '')
    fresh=trellis_coverage.progress(abbr) if Path(folder).resolve()==DATA.resolve() else {}
    if fresh and fresh.get('available'):
        detail=_refresh_trellis(detail,fresh)
        detail['trellis_refreshed_at']=fresh.get('as_of')
    return copy.deepcopy(detail)


def _public_provision(row, topic):
    tags = row['topics']
    match = next((t.get('match') for t in tags if t['topic'] == topic), None) if topic else min(
        (t.get('match') for t in tags), key=lambda m: MATCH_ORDER.get(m, 9), default=None)
    out = {k: row.get(k) for k in ('provision_id', 'source_dataset', 'source_tier', 'row_id', 'record_id', 'state', 'family',
                                   'citation', 'title', 'source_url', 'publisher_status', 'record_class', 'temporal')}
    out['topics'] = [t['topic'] for t in tags]
    out['match'] = match
    out['query_ids'] = sorted({t.get('query_id') for t in tags if t.get('query_id') and (not topic or t['topic'] == topic)})
    return out


def topics(state=None, topic=None, page=1, limit=DEFAULT_LIMIT, folder=DATA):
    """Citation-anchored topic candidates. Filters: state (abbr or name), topic id. Title matches sort first."""
    page, limit = _page(page, limit)
    closed = {'available': False, 'label': TOPIC_LABEL, 'items': [], 'total': 0, 'page': page, 'pages': 0, 'limit': limit,
              'topic_catalog': {}, 'by_source_tier': {}}
    data = load(folder)
    if data is None: return closed
    catalog = {k: dict(v, provisions=data['topic_totals'].get(k, 0)) for k, v in data['catalog']['topics'].items()}
    result = dict(closed, available=True, label=data['catalog'].get('label', TOPIC_LABEL), topic_catalog=catalog,
                  state=None, topic=None)
    abbr = None
    if state is not None:
        abbr = _resolve(data, state)
        if abbr is None: return copy.deepcopy(result)
        result['state'] = abbr
    if topic is not None:
        if topic not in data['catalog']['topics']: return copy.deepcopy(result)
        result['topic'] = topic
    provisions = data['provisions']
    if topic is not None:
        keys = [(abbr, topic)] if abbr else [k for k in data['by_state_topic'] if k[1] == topic]
        ranked = sorted(entry for key in keys for entry in data['by_state_topic'].get(key, []))
        positions = [position for _, position in ranked]
    else:
        positions = [i for i, row in enumerate(provisions) if abbr is None or row['state'] == abbr]
    tiers = {}
    for position in positions:
        tier = provisions[position].get('source_tier')
        tiers[tier] = tiers.get(tier, 0) + 1
    start = (page - 1) * limit
    result.update(total=len(positions), pages=(len(positions) + limit - 1) // limit, by_source_tier=tiers,
                  items=[_public_provision(provisions[p], topic) for p in positions[start:start + limit]])
    return copy.deepcopy(result)


def venues(folder=DATA):
    """Coverage of the high-volume mass-tort venues (county FIPS as 5-character strings)."""
    data = load(folder)
    if data is None: return {'available': False, 'items': [], 'total': 0, 'qualification': ''}
    coverage = data['coverage']
    items = coverage.get('venues', [])
    return copy.deepcopy({'available': True, 'items': items, 'total': len(items),
                          'qualification': coverage.get('venue_qualification', coverage.get('qualification', ''))})


def labels(state=None, law_body_class=None, rule_set=None, confidence=None, page=1, limit=DEFAULT_LIMIT, folder=DATA):
    """Structural class labels for the formerly unreviewed official law tier. Labels never assert legal currency."""
    page, limit = _page(page, limit)
    data = load(folder)
    if data is None: return {'available': False, 'items': [], 'total': 0, 'page': page, 'pages': 0, 'limit': limit}
    abbr = _resolve(data, state) if state is not None else None
    rows = [] if (state is not None and abbr is None) else [
        r for r in data['labels']
        if (abbr is None or r.get('state') == abbr) and (law_body_class is None or r.get('law_body_class') == law_body_class)
        and (rule_set is None or r.get('rule_set') == rule_set) and (confidence is None or r.get('confidence') == confidence)]
    start = (page - 1) * limit
    return copy.deepcopy({'available': True, 'items': rows[start:start + limit], 'total': len(rows), 'page': page,
                          'pages': (len(rows) + limit - 1) // limit, 'limit': limit,
                          'qualification': 'Automated structural triage of saved text; not a legal review and no statement of legal currency.'})


def rule_set(row_id, folder=DATA):
    """Rule-set type for one Open US Law court-rule row id, or None when the row was not typed."""
    data = load(folder)
    if data is None or not isinstance(row_id, str) or not ROW_ID.fullmatch(row_id): return None
    row = data['oul_sets'].get(row_id)
    return copy.deepcopy(row) if row else None
