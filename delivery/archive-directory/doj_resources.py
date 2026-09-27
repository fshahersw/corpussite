"""Read-only, hash-gated DOJ JMD state legal resource map (links as published on saved DOJ pages).

Nothing here touches the network or the 9,348-reference source directory. Every call re-hashes the data
files against validation.json; a missing, failed or altered publication yields empty payloads. Public
dicts carry no filesystem paths and no file bytes are served.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/doj_state_resource_map_20260919'
FILES = ('resources.jsonl', 'pages.jsonl', 'circuits.json', 'edges.jsonl', 'unresolved.jsonl')
QUALIFICATION = ('Links exactly as published by the U.S. Department of Justice (JMD Library Staff) on pages saved in this archive. '
                 'Section labels are the publisher\'s; no outbound link was requested, so every link_status is not_checked. '
                 'The page "Updated" date is page-level and is not the date of a linked document. A listing on a state page '
                 'does not mean the linked publisher belongs to that state.')
FEDERAL_KEYS = ('federal', 'us', 'united states', 'guide to federal court resources')
_CACHE = {}


def _rows(payload):
    return [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]


def _load(folder=None):
    """Fail closed unless the uniform envelope passed and every listed data file hash matches."""
    folder = Path(folder or DATA)
    gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('DOJ resource map is not validated')
    listed = {item.get('path'): item for item in gate.get('data_files') or [] if isinstance(item, dict)}
    payloads, digests = {}, []
    for name in FILES:
        if name not in listed:
            raise ValueError('DOJ resource map validation does not list ' + name)
        payloads[name] = (folder / name).read_bytes()
        digest = hashlib.sha256(payloads[name]).hexdigest()
        if digest != listed[name].get('sha256'):
            raise ValueError('DOJ resource map failed its publication hash gate')
        digests.append(digest)
    key = tuple(digests)
    if key not in _CACHE:
        resources = _rows(payloads['resources.jsonl'])
        if len(resources) != listed['resources.jsonl'].get('rows'):
            raise ValueError('DOJ resource map row count mismatch')
        pages = _rows(payloads['pages.jsonl'])
        circuits = json.loads(payloads['circuits.json'])
        by_state, by_source = {}, {}
        for row in resources:
            group = row['usps'] if row['page_kind'] == 'state_resource_page' else 'FEDERAL'
            by_state.setdefault(group, []).append(row)
            for ref in row.get('directory_ref_ids') or ():
                by_source.setdefault(ref, []).append(row)
        for rows in by_state.values():
            rows.sort(key=lambda r: r['position'])
        aliases = {}
        for page in pages:
            if page.get('usps'):
                aliases[page['usps'].casefold()] = page['usps']
                aliases[page['page_title_as_published'].casefold()] = page['usps']
        if 'FEDERAL' in by_state:
            aliases.update({name: 'FEDERAL' for name in FEDERAL_KEYS})
        membership = {}
        for circuit in circuits.get('circuits') or ():
            for state in circuit.get('states') or ():
                membership[state['usps']] = {'circuit_label': circuit['circuit_label'], 'cl_court_id': circuit.get('cl_court_id'),
                                             'cl_court_id_basis': circuit.get('cl_court_id_basis'),
                                             'state_page_agrees': state.get('state_page_agrees'),
                                             'state_page_circuit_labels': state.get('state_page_circuit_labels') or [],
                                             'basis': circuits.get('basis')}
        _CACHE.clear()
        _CACHE[key] = {
            'resources': resources, 'by_id': {r['record_id']: r for r in resources}, 'by_state': by_state,
            'by_source': by_source, 'pages': pages, 'circuits': circuits, 'aliases': aliases, 'membership': membership,
            'edges': _rows(payloads['edges.jsonl']), 'unresolved': _rows(payloads['unresolved.jsonl']),
            'counts': gate.get('counts') or {}, 'validated_at': gate.get('validated_at'),
            'search': {r['record_id']: ' '.join(str(r.get(k) or '') for k in ('link_text', 'url', 'host', 'section_path', 'evidence_line')).casefold()
                       for r in resources},
        }
    return _CACHE[key]


def _try():
    try:
        return _load()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _param(params, key, default=''):
    value = (params or {}).get(key, default)
    if isinstance(value, list):
        value = value[0] if value else default
    return str(value if value is not None else default)


def _number(params, key, default, maximum):
    try:
        return min(maximum, max(1, int(_param(params, key, str(default)))))
    except (ValueError, TypeError):
        return default


def _text(value):
    if isinstance(value, list):
        value = value[0] if value else ''
    return re.sub(r'\s+', ' ', value).strip().casefold() if isinstance(value, str) else ''


def _page_public(page, data):
    rows = data['by_state'].get(page['usps'] or 'FEDERAL', [])
    return {'usps': page['usps'], 'label': page['page_title_as_published'], 'state_fips': page['state_fips'],
            'jurisdiction_class': page['jurisdiction_class'], 'page_url': page['page_url'], 'legacy_page_url': page['legacy_page_url'],
            'index_link_text_as_published': page.get('index_link_text_as_published'),
            'page_updated_label': page['page_updated_label'], 'page_updated_date': page['page_updated_date'],
            'capture_version_id': page['capture_version_id'], 'capture_raw_sha256': page['capture_raw_sha256'],
            'capture_kind': page['capture_kind'], 'resource_count': len(rows),
            'unique_url_count': page['unique_url_count'], 'section_count': page['section_count'],
            'not_in_source_directory_count': page['not_in_source_directory_count'],
            'circuit': copy.deepcopy(data['membership'].get(page['usps'])) if page['usps'] else None,
            **{k: page.get(k) for base in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to')
               for k in (base, base + '_basis')}}


def _section_facet(rows):
    order, facet = [], {}
    for row in rows:
        entry = facet.get(row['section_h2'])
        if entry is None:
            entry = facet[row['section_h2']] = {'value': row['section_h2'], 'label': row['section_h2'], 'count': 0,
                                                '_states': set(), '_sub': {}}
            order.append(entry)
        entry['count'] += 1
        entry['_states'].add(row['usps'] or 'FEDERAL')
        sub = row['section_h3'] or ''
        entry['_sub'][sub] = entry['_sub'].get(sub, 0) + 1
    result = []
    for entry in sorted(order, key=lambda e: (-e['count'], e['label'])):
        result.append({'value': entry['value'], 'label': entry['label'], 'count': entry['count'], 'states': len(entry['_states']),
                       'subsections': [{'value': value, 'label': value or 'No sub-label published', 'count': count}
                                       for value, count in sorted(entry['_sub'].items(), key=lambda kv: (-kv[1], kv[0]))]})
    return result


def states():
    """The 56 published jurisdiction pages (50 states, DC, five territories); the federal page is reported apart."""
    data = _try()
    if data is None:
        return {'ready': False, 'total': 0, 'items': [], 'federal_page': None, 'summary': {}, 'qualification': QUALIFICATION}
    items = [_page_public(p, data) for p in data['pages'] if p['page_kind'] == 'state_resource_page']
    items.sort(key=lambda s: s['label'])
    federal = next((_page_public(p, data) for p in data['pages'] if p['page_kind'] == 'federal_resource_page'), None)
    return {'ready': True, 'total': len(items), 'items': items, 'federal_page': federal,
            'summary': {**copy.deepcopy(data['counts']), 'validated_at': data['validated_at']}, 'qualification': QUALIFICATION}


def state_resources(state, section=None, params=None):
    """Links published on one jurisdiction page, in page order.

    state: USPS code or the published page label (case-insensitive); 'federal' selects the federal page.
    section: published H2 label, or 'H2 > H3'. params: q, in_directory (yes|no), section, page, limit (max 100).
    """
    params = params if isinstance(params, dict) else {}
    page, limit = _number(params, 'page', 1, 100000), _number(params, 'limit', 30, 100)
    empty = {'ready': False, 'found': False, 'state': None, 'total': 0, 'items': [], 'page': page, 'limit': limit,
             'facets': {'sections': []}, 'qualification': QUALIFICATION}
    data = _try()
    if data is None:
        return empty
    empty['ready'] = True
    group = data['aliases'].get(_text(state))
    if not group:
        return empty
    rows = data['by_state'].get(group, [])
    kind = 'federal_resource_page' if group == 'FEDERAL' else 'state_resource_page'
    meta = next(_page_public(p, data) for p in data['pages'] if p['page_kind'] == kind and (group == 'FEDERAL' or p['usps'] == group))
    terms = _param(params, 'q').casefold().split()
    wanted = _param(params, 'in_directory').casefold()
    if terms:
        rows = [r for r in rows if all(t in data['search'][r['record_id']] for t in terms)]
    if wanted == 'yes':
        rows = [r for r in rows if r['directory_ref_ids']]
    elif wanted == 'no':
        rows = [r for r in rows if r['not_in_source_directory'] is True]
    facet = _section_facet(rows)
    label = _text(section) or _text(params.get('section'))
    if label:
        rows = [r for r in rows if label in (r['section_h2'].casefold(), r['section_path'].casefold())]
    offset = (page - 1) * limit
    return {'ready': True, 'found': True, 'state': meta, 'total': len(rows), 'items': copy.deepcopy(rows[offset:offset + limit]),
            'page': page, 'limit': limit, 'facets': {'sections': facet}, 'qualification': QUALIFICATION}


def sections(state=None):
    """Published section labels with counts (and sub-labels); all state pages, or one jurisdiction."""
    data = _try()
    if data is None:
        return {'ready': False, 'items': [], 'qualification': QUALIFICATION}
    if state is None or _text(state) == '':
        rows = [r for r in data['resources'] if r['page_kind'] == 'state_resource_page']
    else:
        rows = data['by_state'].get(data['aliases'].get(_text(state)), [])
    return {'ready': True, 'items': _section_facet(rows), 'qualification': QUALIFICATION}


def circuits():
    """Circuit -> jurisdictions as published on the DOJ federal page; publisher inconsistencies stay visible."""
    data = _try()
    if data is None:
        return {'ready': False, 'available': False, 'items': [], 'unresolved': []}
    source = data['circuits']
    unresolved = [u for u in data['unresolved'] if u.get('kind') != 'doj_url_not_in_source_directory']
    return {'ready': True, 'available': bool(source.get('available')), 'basis': source.get('basis'), 'page_url': source.get('page_url'),
            'page_updated_label': source.get('page_updated_label'), 'captured_at': source.get('captured_at'),
            'capture_kind': source.get('capture_kind'), 'capture_raw_sha256': source.get('capture_raw_sha256'),
            'items': copy.deepcopy(source.get('circuits') or []), 'unresolved': copy.deepcopy(unresolved)}


def resource(record_id):
    if not isinstance(record_id, str) or not re.fullmatch(r'slrm-[0-9a-f]{12}', record_id):
        return None
    data = _try()
    row = data['by_id'].get(record_id) if data else None
    return {'ready': True, **copy.deepcopy(row)} if row else None


def source_listings(source_id):
    """DOJ listings for one source-directory reference id (pld-...); [] when none or unavailable."""
    if not isinstance(source_id, str) or not re.fullmatch(r'pld-[0-9a-f]{12}', source_id):
        return []
    data = _try()
    return copy.deepcopy(data['by_source'].get(source_id, [])) if data else []


def edges(params=None):
    """Evidence-backed relationships: source -> state (listing) and state -> federal circuit."""
    params = params if isinstance(params, dict) else {}
    page, limit = _number(params, 'page', 1, 100000), _number(params, 'limit', 30, 100)
    data = _try()
    if data is None:
        return {'ready': False, 'total': 0, 'items': [], 'page': page, 'limit': limit}
    relation = _param(params, 'relation')
    rows = [e for e in data['edges'] if not relation or e.get('relation') == relation]
    offset = (page - 1) * limit
    return {'ready': True, 'total': len(rows), 'items': copy.deepcopy(rows[offset:offset + limit]), 'page': page, 'limit': limit}
