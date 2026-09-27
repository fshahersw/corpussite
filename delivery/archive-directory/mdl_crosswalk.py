"""Read-only, hash-gated MDL <-> master-matter <-> CourtListener-docket <-> member-matter id spine.

Generic view contract: listing(params), detail(id). Fails closed when validation.json or any data file
hash does not match. No `original()` — this slice serves no files. Public dicts carry no filesystem
paths. Nothing is inferred here; every label repeats a basis written by
sources/mdl_docket_crosswalk_20260919/build.py.

This slice has no UI route of its own (the crosswalk is a build-time/adapter-time input for other MDL
slices). listing()/detail() are still implemented per the generic view contract. The lookup helpers
(`for_mdl`, `mdl_for_docket`, `mdl_for_matter`, `members`) are what other adapters import.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/mdl_docket_crosswalk_20260919'
FILES = ('crosswalk.jsonl', 'edges.jsonl', 'unresolved.jsonl')
MDL_ID_RE = re.compile(r'mdl:(\d+)')
_CACHE = {}


def _load(folder=None):
    """Return the parsed supplement or raise. Hashes are re-checked on every call; parsing is cached
    per hash set (same pattern as court_spine.py / settlements.py)."""
    folder = Path(folder or DATA)
    gate_bytes = (folder / 'validation.json').read_bytes()
    gate = json.loads(gate_bytes)
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('mdl crosswalk validation is not passed/ready')
    listed = {d.get('path'): d for d in gate.get('data_files', [])}
    if set(listed) != set(FILES):
        raise ValueError('mdl crosswalk data file list mismatch')
    blobs, digests = {}, [hashlib.sha256(gate_bytes).hexdigest()]
    for name in FILES:
        blobs[name] = (folder / name).read_bytes()
        digest = hashlib.sha256(blobs[name]).hexdigest()
        if digest != listed[name].get('sha256'):
            raise ValueError('mdl crosswalk hash gate failed for ' + name)
        digests.append(digest)
    key = (str(folder), tuple(digests))
    if key not in _CACHE:
        rows = {n: [json.loads(line) for line in blobs[n].decode('utf-8').splitlines() if line.strip()] for n in FILES}
        for n in FILES:
            if len(rows[n]) != listed[n].get('rows'):
                raise ValueError('mdl crosswalk row count mismatch for ' + n)
        crosswalk = sorted(rows['crosswalk.jsonl'], key=lambda r: r['mdl_number'])
        by_number = {r['mdl_number']: r for r in crosswalk}
        members_by_number = {}
        for e in rows['edges.jsonl']:
            if e.get('relation') == 'member_of_mdl' and e.get('to', {}).get('type') == 'mdl':
                members_by_number.setdefault(e['to']['id'], []).append(e)
        docket_to_number = {r['cl_docket_id']: r['mdl_number'] for r in crosswalk if r.get('cl_docket_id') is not None}
        matter_to_number = {r['aws_matter_id']: r['mdl_number'] for r in crosswalk if r.get('aws_matter_id')}
        for e in rows['edges.jsonl']:
            if e.get('relation') == 'member_of_mdl' and e.get('from', {}).get('type') == 'aws_matter':
                matter_to_number.setdefault(e['from']['id'], e['to']['id'])
        _CACHE.clear()
        _CACHE[key] = {
            'crosswalk': crosswalk, 'by_number': by_number, 'members_by_number': members_by_number,
            'docket_to_number': docket_to_number, 'matter_to_number': matter_to_number,
            'unresolved': rows['unresolved.jsonl'],
            'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {},
        }
    return _CACHE[key]


def _one(params, name, default=''):
    value = (params or {}).get(name, default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return value.strip() if isinstance(value, str) else default


def _int(params, name, default, low, high):
    try:
        return max(low, min(high, int(_one(params, name, str(default)))))
    except ValueError:
        return default


def _parse_mdl_id(value):
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    m = MDL_ID_RE.fullmatch(value.strip())
    if m:
        return int(m.group(1))
    if value.strip().isdigit():
        return int(value.strip())
    return None


def _result(r):
    badges = [r['mdl_status']]
    if r.get('cl_docket_id'):
        badges.append('CourtListener docket')
    if r.get('catalog_master_docket_id'):
        badges.append('Catalog master')
    return {
        'id': r['id'], 'title': r['mdl_title'],
        'subtitle': '%s · %s' % (r['cl_court_id'] or 'court not recorded', r.get('master_docket_number_registry') or 'docket not recorded'),
        'cells': {'mdl': str(r['mdl_number']), 'status': r['mdl_status'], 'court': r['cl_court_id'] or '—',
                  'docket': r.get('master_docket_number_registry') or '—'},
        'badges': badges,
        'links': [{'label': 'MDL registry detail', 'url': '#mdls?mdl=' + str(r['mdl_number'])}],
    }


def listing(params=None):
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {'available': False, 'reason': 'MDL crosswalk supplement is unavailable or failed its hash gate (%s).' % type(error).__name__,
                'total': 0, 'page': 1, 'limit': 0, 'results': []}
    params = params if isinstance(params, dict) else {}
    rows = data['crosswalk']
    q, status = _one(params, 'q').casefold(), _one(params, 'status').casefold()
    if q:
        rows = [r for r in rows if q in r['mdl_title'].casefold() or q in str(r['mdl_number'])]
    if status in ('pending', 'terminated'):
        rows = [r for r in rows if r['mdl_status'] == status]
    limit, page = _int(params, 'limit', 25, 1, 100), _int(params, 'page', 1, 1, 10 ** 6)
    pending_count = sum(1 for r in data['crosswalk'] if r['mdl_status'] == 'pending')
    start = (page - 1) * limit
    return {'available': True, 'total': len(rows), 'page': page, 'limit': limit, 'qualification': data['qualification'],
            'filters': [
                {'name': 'q', 'label': 'Search', 'type': 'search'},
                {'name': 'status', 'label': 'Status', 'type': 'select', 'options': [
                    {'value': 'pending', 'label': 'Pending', 'count': pending_count},
                    {'value': 'terminated', 'label': 'Terminated', 'count': len(data['crosswalk']) - pending_count}]},
            ],
            'columns': [{'key': 'mdl', 'label': 'MDL'}, {'key': 'status', 'label': 'Status'},
                        {'key': 'court', 'label': 'Court'}, {'key': 'docket', 'label': 'Master docket'}],
            'results': [_result(r) for r in rows[start:start + limit]]}


def detail(mdl_id):
    num = _parse_mdl_id(mdl_id)
    if num is None:
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    r = data['by_number'].get(num)
    if r is None:
        return None
    facts = [
        ['MDL number', str(r['mdl_number'])], ['Status (JPML registry)', r['mdl_status']],
        ['Court (CourtListener id)', r['cl_court_id'] or 'not recorded'],
        ['Master docket (registry printing)', r.get('master_docket_number_registry') or 'not recorded'],
    ]
    if r.get('master_docket_number_aws'):
        facts.append(['Master docket (AWS release printing)', r['master_docket_number_aws']])
    if r.get('cl_docket_id'):
        facts.append(['CourtListener docket id (%s)' % (r.get('cl_docket_id_basis') or 'basis not recorded'), str(r['cl_docket_id'])])
    if r.get('aws_matter_id'):
        facts.append(['AWS release matter id', r['aws_matter_id']])
        facts.append(['AWS case status', r.get('aws_case_status') or 'not recorded'])
    if r.get('catalog_master_docket_id'):
        facts.append(['Catalog master docket id (SW-BULK catalog)', str(r['catalog_master_docket_id'])])
        if r.get('catalog_master_document_count') is not None:
            facts.append(['Catalog document count (SW-BULK catalog, private)', str(r['catalog_master_document_count'])])
    facts.append(['Join basis', r.get('basis') or 'not recorded'])

    members = data['members_by_number'].get(num, [])
    sections = []
    if members:
        sections.append({
            'heading': 'Member matters via member_of_mdl edges (%d)' % len(members),
            'header': ['Member matter id', 'Docket number', 'Court'],
            'rows': [[e['from']['id'], (e.get('evidence') or {}).get('member_docket_number') or '—',
                      (e.get('evidence') or {}).get('member_court_id') or '—'] for e in members[:25]],
        })
    return {
        'id': r['id'], 'title': r['mdl_title'], 'subtitle': _result(r)['subtitle'],
        'qualification': data['qualification'], 'facts': facts, 'sections': sections,
        'links': [{'label': 'MDL registry detail', 'url': '#mdls?mdl=' + str(num)}],
    }


def for_mdl(mdl_number):
    """Compact hook other adapters may embed: ids and coverage-depth flags for one MDL, or None."""
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(mdl_number, int):
        try:
            mdl_number = int(mdl_number)
        except (TypeError, ValueError):
            return None
    r = data['by_number'].get(mdl_number)
    if r is None:
        return None
    return {
        'mdl_number': r['mdl_number'], 'mdl_status': r['mdl_status'], 'cl_court_id': r['cl_court_id'],
        'master_docket_number': r.get('master_docket_number_registry'), 'cl_docket_id': r.get('cl_docket_id'),
        'has_aws_matter': bool(r.get('aws_matter_id')), 'has_catalog_master': bool(r.get('catalog_master_docket_id')),
        'member_matter_count': len(data['members_by_number'].get(mdl_number, [])),
        'qualification': data['qualification'],
    }


def mdl_for_docket(cl_docket_id):
    """Lookup helper for other adapters: CourtListener docket id -> MDL number, or None."""
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(cl_docket_id, int):
        try:
            cl_docket_id = int(cl_docket_id)
        except (TypeError, ValueError):
            return None
    return data['docket_to_number'].get(cl_docket_id)


def mdl_for_matter(matter_id):
    """Lookup helper for other adapters: an AWS matter id (master or member, via edges.jsonl) -> MDL
    number, or None."""
    if not isinstance(matter_id, str) or not matter_id:
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return data['matter_to_number'].get(matter_id)


def members(mdl_number):
    """Compact (<=25 rows + total): member matters of one MDL via edges.jsonl, plus the master matter
    itself. None if the MDL is not in this crosswalk."""
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(mdl_number, int):
        try:
            mdl_number = int(mdl_number)
        except (TypeError, ValueError):
            return None
    r = data['by_number'].get(mdl_number)
    if r is None:
        return None
    edges = data['members_by_number'].get(mdl_number, [])
    rows = [{'matter_id': e['from']['id'], 'docket_number': (e.get('evidence') or {}).get('member_docket_number'),
             'court_id': (e.get('evidence') or {}).get('member_court_id'), 'role': 'member'} for e in edges[:25]]
    master = {'matter_id': r.get('aws_matter_id'), 'docket_number': r.get('master_docket_number_aws') or r.get('master_docket_number_registry'),
              'court_id': r['cl_court_id'], 'role': 'master'} if r.get('aws_matter_id') else None
    return {'mdl_number': mdl_number, 'master': master, 'total_members': len(edges), 'members': rows,
            'qualification': data['qualification'], 'link': '#mdls?mdl=' + str(mdl_number)}
