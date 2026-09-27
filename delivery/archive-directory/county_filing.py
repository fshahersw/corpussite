"""Read-only, hash-gated adapter for county filing sources (sources/county_filing_sources_20260919).

for_county(fips) and for_state(usps) feed the county and state pages; coverage() feeds the coverage view. Official sources
are links to the live official page; statewide sources are labelled statewide and circuit/district sources carry the unit
name. Fail-closed on a missing/failed validation or hash mismatch; never raises on bad parameters.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/county_filing_sources_20260919'
FILES = ('county_sources.jsonl', 'state_summary.json')
_LOCK = threading.Lock()
_CACHE: dict = {}
LEVEL_TEXT = {'rules_and_filing': 'Local rules and filing sources found', 'statewide_rules_and_filing': 'Statewide court rules and filing sources found; no local rules source recorded',
              'filing_only': 'Filing sources found; no rules source yet', 'rules_only': 'Rules source found; no filing source yet', 'none': 'No official source recorded yet'}


def _state(folder=None):
    folder = Path(folder) if folder else DATA
    try:
        signature = tuple((name, (folder / name).stat().st_size, (folder / name).stat().st_mtime_ns) for name in ('validation.json',) + FILES)
    except OSError:
        return None
    with _LOCK:
        cached = _CACHE.get(str(folder))
        if cached and cached['signature'] == signature:
            return cached['state']
        state = None
        try:
            gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
            expected = {item.get('path'): item.get('sha256') for item in gate.get('data_files') or [] if isinstance(item, dict)} if isinstance(gate, dict) else {}
            ok = isinstance(gate, dict) and gate.get('status') == 'passed' and gate.get('ready') is True
            ok = ok and all(expected.get(name) and hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected[name] for name in FILES)
            if ok:
                counties = {}
                for line in (folder / 'county_sources.jsonl').read_text(encoding='utf-8').splitlines():
                    if line.strip():
                        row = json.loads(line)
                        counties[row['geoid']] = row
                state = {'counties': counties, 'states': json.loads((folder / 'state_summary.json').read_text(encoding='utf-8')), 'qualification': gate.get('qualification') or '',
                         'counts': gate.get('counts') or {}, 'validated_at': gate.get('validated_at')}
        except (OSError, ValueError, KeyError):
            state = None
        _CACHE[str(folder)] = {'signature': signature, 'state': state}
        return state


def for_county(fips, folder=None):
    state = _state(folder)
    if state is None or not re.fullmatch(r'\d{5}', str(fips or '')):
        return {'available': False}
    row = state['counties'].get(str(fips))
    if row is None:
        return {'available': False}
    groups, order = {}, []
    for item in row['items']:
        label = item['scope_label']
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append({key: item.get(key) for key in ('kind', 'kind_label', 'title', 'url', 'publisher', 'as_of', 'as_of_basis', 'origin', 'resource_id')})
    info = state['states'].get(row['state'], {})
    return {'available': True, 'county': {'name': row['name'], 'state': row['state'], 'fips': row['geoid']}, 'coverage_level': row['coverage_level'],
            'summary': LEVEL_TEXT.get(row['coverage_level'], ''), 'structure': info.get('structure'), 'groups': [{'scope_label': label, 'items': groups[label]} for label in order],
            'qualification': state['qualification']}


def for_state(usps, folder=None):
    state = _state(folder)
    if state is None or not re.fullmatch(r'[A-Za-z]{2}', str(usps or '')):
        return {'available': False}
    info = state['states'].get(str(usps).upper())
    if not info:
        return {'available': False}
    return {'available': True, 'state': str(usps).upper(), 'counties_total': info.get('total', 0), 'counties_with_local_rules': info.get('with_local_rules', 0),
            'counties_with_any': info.get('with_any', 0), 'counties_with_rules_and_filing': info.get('with_rules_any_scope_and_filing', 0), 'structure': info.get('structure'), 'has_local_rules': info.get('has_local_rules'), 'gaps': info.get('gaps') or [],
            'qualification': state['qualification']}


def counties_for_state(usps, folder=None):
    """Per-county coverage levels of one state, for the county map."""
    state = _state(folder)
    if state is None or not re.fullmatch(r'[A-Za-z]{2}', str(usps or '')):
        return {'available': False}
    code = str(usps).upper()
    rows = [{'fips': geoid, 'name': row['name'], 'level': row['coverage_level'], 'sources': len(row['items']),
             'county_specific': sum(1 for item in row['items'] if item.get('scope') == 'county'), 'unit': next((item['scope_label'] for item in row['items'] if item.get('scope') == 'unit'), None)}
            for geoid, row in state['counties'].items() if row['state'] == code]
    return {'available': bool(rows), 'state': code, 'counties': sorted(rows, key=lambda r: r['name'])}


def coverage(folder=None):
    state = _state(folder)
    if state is None:
        return {'available': False}
    rows = [{'state': usps, 'counties_total': info.get('total', 0), 'with_local_rules': info.get('with_local_rules', 0), 'with_any': info.get('with_any', 0), 'with_rules_and_filing': info.get('with_rules_any_scope_and_filing', 0),
             'has_local_rules': info.get('has_local_rules'), 'structure': info.get('structure')} for usps, info in sorted(state['states'].items())]
    return {'available': True, 'validated_at': state['validated_at'], 'counts': state['counts'], 'states': rows, 'qualification': state['qualification']}
