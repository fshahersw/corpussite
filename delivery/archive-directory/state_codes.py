"""Full-text state codes behind one listing (generic view contract), so single-state codes never need their own page.

Dispatches to the per-state adapters by the `state` filter: IN -> indiana_code (2026 Indiana Code), SD -> sd_statutes (South
Dakota Codified Laws cache). Without a state it lists the codes that are available. Record ids are "<USPS>:<native id>".
A missing or failing per-state layer simply is not offered. Never raises on bad parameters.
"""
from __future__ import annotations

import importlib
import re

CODES = {
    'IN': {'module': 'indiana_code', 'name': 'Indiana Code', 'edition': '2026 edition (General Assembly HTML, local copy)', 'state_name': 'Indiana'},
    'SD': {'module': 'sd_statutes', 'name': 'South Dakota Codified Laws', 'edition': 'as retrieved 2026-08-20 (title-level cache)', 'state_name': 'South Dakota'},
}


def _adapter(usps):
    try:
        module = importlib.import_module(CODES[usps]['module'])
        return module if hasattr(module, 'listing') and hasattr(module, 'detail') else None
    except Exception:
        return None


def _available():
    out = []
    for usps, info in CODES.items():
        adapter = _adapter(usps)
        if adapter is None:
            continue
        try:
            first = adapter.listing({'limit': 1})
        except Exception:
            continue
        if first.get('available'):
            out.append((usps, info, first))
    return out


def available_states():
    """[{usps, name, edition, records}] for the Laws & rules page."""
    return [{'usps': usps, 'state': info['state_name'], 'name': info['name'], 'edition': info['edition'], 'records': first.get('total', 0)} for usps, info, first in _available()]


def listing(params=None):
    params = params if isinstance(params, dict) else {}
    chosen = str(params.get('state') or '').strip().upper()[:2]
    codes = _available()
    state_filter = {'name': 'state', 'label': 'State code', 'type': 'select',
                    'options': [{'value': usps, 'label': '%s — %s' % (info['state_name'], info['name']), 'count': first.get('total', 0)} for usps, info, first in codes]}
    if chosen not in CODES or _adapter(chosen) is None:
        results = [{'id': 'code:' + usps, 'title': info['name'], 'subtitle': info['edition'], 'cells': {'state': info['state_name'], 'records': '{:,}'.format(first.get('total', 0))},
                    'badges': [], 'links': [{'label': 'Search this code', 'url': '#state-codes?state=' + usps}]} for usps, info, first in codes]
        return {'available': bool(codes), 'reason': None if codes else 'No full-text state code is published yet.', 'total': len(results), 'page': 1, 'limit': 50,
                'qualification': 'Full-text state codes saved locally. Each is an edition as of its stated date, not verified against the current code.',
                'filters': [{'name': 'q', 'label': 'Search', 'type': 'search', 'placeholder': 'Choose a state code first'}, state_filter],
                'columns': [{'key': 'title', 'label': 'Code'}, {'key': 'state', 'label': 'State'}, {'key': 'records', 'label': 'Sections or titles'}], 'results': results}
    inner = dict(params)
    inner.pop('state', None)
    data = _adapter(chosen).listing(inner)
    if not data.get('available'):
        return data
    for row in data.get('results') or []:
        row['id'] = '%s:%s' % (chosen, row['id'])
    data['filters'] = [f for f in (data.get('filters') or []) if f.get('name') == 'q'] + [state_filter] + [f for f in (data.get('filters') or []) if f.get('name') not in ('q', 'state')]
    data['code'] = {'usps': chosen, **{k: CODES[chosen][k] for k in ('name', 'edition', 'state_name')}}
    return data


def detail(item_id):
    match = re.fullmatch(r'([A-Za-z]{2}|code):(.{1,80})', str(item_id or ''))
    if not match:
        return None
    prefix, native = match.group(1), match.group(2)
    if prefix == 'code':
        usps = native.upper()
        info = CODES.get(usps)
        if not info or _adapter(usps) is None:
            return None
        first = _adapter(usps).listing({'limit': 1})
        return {'title': info['name'], 'subtitle': info['state_name'], 'facts': [['Edition', info['edition']], ['Records', '{:,}'.format(first.get('total', 0))]], 'sections': [],
                'links': [{'label': 'Search this code', 'url': '#state-codes?state=' + usps}], 'qualification': first.get('qualification') or ''}
    usps = prefix.upper()
    if usps not in CODES or _adapter(usps) is None:
        return None
    try:
        return _adapter(usps).detail(native)
    except Exception:
        return None
