"""Read-only, hash-gated adapter for sources/mdl_counsel_appearances_20260919 (AWS release
b2b-cdbb8d040b95c7b65cfc counsel-appearance tables -- a different, more complete pipeline output than
sources/mdl_counsel_20260919's CourtListener search-index scrape; the two are never merged).

Generic view contract: listing(params), detail(id). No original() -- this slice serves no files.
Fails closed (returns {'available': False, 'reason': ...} / None) when validation.json is missing, not
passed/ready, or any data-file hash does not match. Public dicts carry no filesystem paths, emails or
phone numbers -- none exist in this dataset.

listing() lists individual counsel appearances (878 rows), filterable by mdl / firm / role / side.
detail(id) dispatches on the id's native-id prefix: 'firm:<firm_id>' -> firm roster detail,
'attorney:<uuid>' -> attorney detail, otherwise a bare appearance_id -> single-appearance detail.
for_mdl(mdl_number) is the compact embedding hook for the MDL detail page.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/mdl_counsel_appearances_20260919'
FILES = ('attorneys.jsonl', 'firms.jsonl', 'appearances.jsonl', 'parties.jsonl',
          'party_counts_by_matter.jsonl', 'edges.jsonl', 'unresolved.jsonl')
_CACHE = {}


def _load(folder=None):
    folder = Path(folder or DATA)
    gate_bytes = (folder / 'validation.json').read_bytes()
    gate = json.loads(gate_bytes)
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('mdl counsel appearances validation is not passed/ready')
    listed = {d.get('path'): d for d in gate.get('data_files', [])}
    if set(listed) != set(FILES):
        raise ValueError('mdl counsel appearances data file list mismatch')
    blobs, digests = {}, [hashlib.sha256(gate_bytes).hexdigest()]
    for name in FILES:
        blobs[name] = (folder / name).read_bytes()
        digest = hashlib.sha256(blobs[name]).hexdigest()
        if digest != listed[name].get('sha256'):
            raise ValueError('mdl counsel appearances hash gate failed for ' + name)
        digests.append(digest)
    key = (str(folder), tuple(digests))
    if key not in _CACHE:
        rows = {n: [json.loads(line) for line in blobs[n].decode('utf-8').splitlines() if line.strip()] for n in FILES}
        for n in FILES:
            if len(rows[n]) != listed[n].get('rows'):
                raise ValueError('mdl counsel appearances row count mismatch for ' + n)
        attorneys_by_id = {a['attorney_id']: a for a in rows['attorneys.jsonl']}
        firms_by_id = {f['firm_id']: f for f in rows['firms.jsonl']}
        appearances = rows['appearances.jsonl']
        parties_by_id = {p['party_id']: p for p in rows['parties.jsonl']}
        party_counts_by_matter = {r['matter_id']: r for r in rows['party_counts_by_matter.jsonl']}
        appearances_by_firm_mdl, appearances_by_mdl = {}, {}
        for a in appearances:
            mdl = a.get('mdl_number_extended')
            if mdl is None:
                continue
            appearances_by_mdl.setdefault(mdl, []).append(a)
            if a.get('firm_id'):
                appearances_by_firm_mdl.setdefault((a['firm_id'], mdl), 0)
                appearances_by_firm_mdl[(a['firm_id'], mdl)] += 1
        parties_by_mdl = {}
        for p in rows['parties.jsonl']:
            mdl = p.get('mdl_number_extended')
            if mdl is not None:
                parties_by_mdl.setdefault(mdl, []).append(p)
        _CACHE.clear()
        _CACHE[key] = {
            'attorneys_by_id': attorneys_by_id, 'firms_by_id': firms_by_id, 'appearances': appearances,
            'parties_by_id': parties_by_id, 'party_counts_by_matter': party_counts_by_matter,
            'appearances_by_mdl': appearances_by_mdl, 'appearances_by_firm_mdl': appearances_by_firm_mdl,
            'parties_by_mdl': parties_by_mdl,
            'qualification': gate.get('qualification') or '', 'qualification_short': gate.get('qualification_short') or '',
            'counts': gate.get('counts') or {},
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


def _facet_options(rows, key, label_fn=None):
    counts = {}
    for r in rows:
        v = r.get(key)
        if v is None or v == '':
            continue
        counts[v] = counts.get(v, 0) + 1
    return [{'value': str(v), 'label': (label_fn(v) if label_fn else str(v)), 'count': c}
            for v, c in sorted(counts.items(), key=lambda kv: -kv[1])]


_LINKAGE_CELL_LABEL = {'direct': 'Direct', 'member_of_mdl_edge': 'Member-of-MDL edge'}
_LINKAGE_BADGE_LABEL = {'direct': 'Direct MDL linkage', 'member_of_mdl_edge': 'Member-of-MDL linkage'}


def _appearance_result(a, data):
    firm_label = a.get('firm_canonical_name') or a.get('raw_firm_name') or a.get('firm_id') or 'firm not recorded'
    badges = [a['role_normalized']]
    mdl = a.get('mdl_number_extended')
    basis = a.get('mdl_match_basis')
    if mdl is not None:
        badges.append('MDL %s' % mdl)
        badges.append(_LINKAGE_BADGE_LABEL.get(basis, 'Linkage basis unrecorded'))
    links = [{'label': 'MDL registry detail', 'url': '#mdls?mdl=' + str(mdl)}] if mdl is not None else []
    return {
        'id': a['appearance_id'], 'title': a.get('attorney_name') or 'attorney not recorded', 'subtitle': firm_label,
        'cells': {'role': a['role_normalized'], 'mdl': str(mdl) if mdl is not None else '—',
                  'side': a.get('party_side') or '—', 'firm': firm_label,
                  'linkage': _LINKAGE_CELL_LABEL.get(basis, '—')},
        'badges': badges, 'links': links,
    }


def listing(params=None):
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {'available': False, 'reason': 'MDL counsel appearances supplement is unavailable or failed its hash gate (%s).' % type(error).__name__,
                'total': 0, 'page': 1, 'limit': 0, 'results': []}
    params = params if isinstance(params, dict) else {}
    rows = data['appearances']
    q = _one(params, 'q').casefold()
    mdl_filter = _one(params, 'mdl')
    firm_filter = _one(params, 'firm')
    role_filter = _one(params, 'role')
    side_filter = _one(params, 'side').casefold()
    if q:
        rows = [r for r in rows if q in (r.get('attorney_name') or '').casefold()
                or q in (r.get('firm_canonical_name') or '').casefold()
                or q in (r.get('raw_firm_name') or '').casefold()]
    if mdl_filter:
        try:
            mdl_num = int(mdl_filter)
            rows = [r for r in rows if r.get('mdl_number_extended') == mdl_num]
        except ValueError:
            rows = []
    if firm_filter:
        rows = [r for r in rows if r.get('firm_id') == firm_filter]
    if role_filter:
        rows = [r for r in rows if r.get('role_normalized') == role_filter]
    if side_filter in ('plaintiff', 'defendant'):
        rows = [r for r in rows if (r.get('party_side') or '').casefold() == side_filter]

    limit, page = _int(params, 'limit', 25, 1, 100), _int(params, 'page', 1, 1, 10 ** 6)
    start = (page - 1) * limit
    firm_options = [{'value': f['firm_id'], 'label': f['canonical_name'], 'count': f['appearance_count']}
                     for f in sorted(data['firms_by_id'].values(), key=lambda f: -f['appearance_count'])]
    role_options = _facet_options(data['appearances'], 'role_normalized')
    mdl_options = _facet_options(data['appearances'], 'mdl_number_extended')
    side_options = _facet_options(data['appearances'], 'party_side')
    return {'available': True, 'total': len(rows), 'page': page, 'limit': limit, 'qualification': data['qualification'],
            'filters': [
                {'name': 'q', 'label': 'Search', 'type': 'search'},
                {'name': 'mdl', 'label': 'MDL', 'type': 'select', 'options': mdl_options},
                {'name': 'firm', 'label': 'Firm', 'type': 'select', 'options': firm_options},
                {'name': 'role', 'label': 'Role', 'type': 'select', 'options': role_options},
                {'name': 'side', 'label': 'Party side', 'type': 'select', 'options': side_options},
            ],
            'columns': [{'key': 'role', 'label': 'Role'}, {'key': 'firm', 'label': 'Firm'},
                        {'key': 'mdl', 'label': 'MDL'}, {'key': 'side', 'label': 'Side'},
                        {'key': 'linkage', 'label': 'Linkage'}],
            'results': [_appearance_result(r, data) for r in rows[start:start + limit]]}


def _firm_detail(firm_id, data):
    firm = data['firms_by_id'].get(firm_id)
    if firm is None:
        return None
    per_mdl = sorted(((mdl, cnt) for (fid, mdl), cnt in data['appearances_by_firm_mdl'].items() if fid == firm_id),
                      key=lambda kv: -kv[1])
    lead_count = sum(1 for a in data['appearances'] if a.get('firm_id') == firm_id and a['role_normalized'] == 'Lead attorney')
    facts = [['Native firm id', firm['firm_id']], ['Canonical name', firm['canonical_name']],
             ['Total appearances', str(firm['appearance_count'])], ['Lead-attorney appearances', str(lead_count)],
             ['MDLs reached (crosswalk-extended linkage)', str(len(firm['mdl_numbers_reached_extended']))]]
    sections = []
    if per_mdl:
        sections.append({'heading': 'Appearance count per MDL (%d)' % len(per_mdl),
                          'header': ['MDL', 'Appearances'],
                          'rows': [[str(mdl), str(cnt)] for mdl, cnt in per_mdl[:25]]})
    return {
        'id': 'firm:' + firm_id, 'title': firm['canonical_name'], 'subtitle': firm_id,
        'qualification': data['qualification'], 'facts': facts, 'sections': sections,
        'links': [],
    }


def _attorney_detail(attorney_id, data):
    attorney = data['attorneys_by_id'].get(attorney_id)
    if attorney is None:
        return None
    apps = [a for a in data['appearances'] if a['attorney_id'] == attorney_id]
    roles = sorted({a['role_normalized'] for a in apps})
    facts = [['Native attorney id (AWS release UUID, not a CourtListener attorney id)', attorney['attorney_id']],
             ['Name', attorney['name']], ['Total appearances', str(attorney['appearance_count'])],
             ['Firms', ', '.join(attorney['firm_ids']) or 'not recorded'],
             ['MDLs reached (crosswalk-extended linkage)', str(len(attorney['mdl_numbers_reached_extended']))],
             ['Roles seen', ', '.join(roles) or 'not recorded'],
             ['Record scope', 'One source UUID; the same person may appear under other UUIDs in this '
                               'dataset — records are never merged by name']]
    sections = [{'heading': 'Appearances (%d)' % len(apps), 'header': ['Matter', 'Firm', 'Role', 'MDL'],
                 'rows': [[a['matter_id'], a.get('firm_canonical_name') or a.get('firm_id') or '—',
                           a['role_normalized'], str(a.get('mdl_number_extended') or '—')] for a in apps[:25]]}] if apps else []
    return {
        'id': 'attorney:' + attorney_id, 'title': attorney['name'], 'subtitle': 'AWS release counsel appearance record',
        'qualification': data['qualification'], 'facts': facts, 'sections': sections, 'links': [],
    }


def detail(item_id):
    if not isinstance(item_id, str) or not item_id:
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if item_id.startswith('firm:'):
        return _firm_detail(item_id[len('firm:'):], data)
    if item_id.startswith('attorney:'):
        return _attorney_detail(item_id[len('attorney:'):], data)
    a = next((r for r in data['appearances'] if r['appearance_id'] == item_id), None)
    if a is None:
        return None
    result = _appearance_result(a, data)
    facts = [['Attorney', a.get('attorney_name') or 'not recorded'],
             ['Firm', a.get('firm_canonical_name') or a.get('raw_firm_name') or 'not recorded'],
             ['Raw firm name (as recorded)', a.get('raw_firm_name') or 'not recorded'],
             ['Role (raw)', a.get('role_raw') or 'not recorded'], ['Role (normalised)', a['role_normalized']],
             ['Matter id (AWS release)', a['matter_id']],
             ['MDL (direct crosswalk linkage)', str(a.get('mdl_number_direct')) if a.get('mdl_number_direct') is not None else 'not linked'],
             ['MDL (crosswalk-extended linkage)', str(a.get('mdl_number_extended')) if a.get('mdl_number_extended') is not None else 'not linked'],
             ['Party side', a.get('party_side') or 'not recorded']]
    if a.get('terminated_date_raw'):
        facts.append(['Terminated date (as printed in the raw role string)', a['terminated_date_raw']])
    return {'id': a['appearance_id'], 'title': result['title'], 'subtitle': result['subtitle'],
            'qualification': data['qualification'], 'facts': facts, 'sections': [], 'links': result['links']}


def for_mdl(mdl_number):
    """Compact embedding hook for the MDL detail page: firms with appearance counts, lead-attorney
    appearance count, and party counts by side/category for this MDL. Reports BOTH direct-linkage and
    crosswalk-extended-linkage counts side by side (never the extended figure alone) plus a
    linkage-basis breakdown ('direct' vs 'member_of_mdl_edge'), per BUILD_CONTRACT. Uses the short
    (<500 char) qualification, since this is a compact embedding block. None when the gate fails or the
    MDL is not reached by this dataset."""
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(mdl_number, int):
        try:
            mdl_number = int(mdl_number)
        except (TypeError, ValueError):
            return None
    apps = data['appearances_by_mdl'].get(mdl_number)
    if not apps:
        return None
    firm_counts = {}
    for a in apps:
        if a.get('firm_id'):
            firm_counts[a['firm_id']] = firm_counts.get(a['firm_id'], 0) + 1
    firms = [{'firm_id': fid, 'canonical_name': data['firms_by_id'].get(fid, {}).get('canonical_name', fid), 'appearance_count': cnt}
              for fid, cnt in sorted(firm_counts.items(), key=lambda kv: -kv[1])][:25]
    lead_count = sum(1 for a in apps if a['role_normalized'] == 'Lead attorney')
    appearance_direct_count = sum(1 for a in apps if a.get('mdl_number_direct') == mdl_number)
    appearance_basis_counts = {}
    for a in apps:
        basis = a.get('mdl_match_basis') or 'unrecorded'
        appearance_basis_counts[basis] = appearance_basis_counts.get(basis, 0) + 1
    parties = data['parties_by_mdl'].get(mdl_number, [])
    party_counts = {'organization': 0, 'named_defendant': 0, 'natural_person_count_only': 0}
    for p in parties:
        party_counts[p['category']] = party_counts.get(p['category'], 0) + 1
    party_direct_count = sum(1 for p in parties if p.get('mdl_number_direct') == mdl_number)
    party_basis_counts = {}
    for p in parties:
        basis = 'direct' if p.get('mdl_number_direct') == mdl_number else 'member_of_mdl_edge'
        party_basis_counts[basis] = party_basis_counts.get(basis, 0) + 1
    return {
        'mdl_number': mdl_number,
        'total_appearances_extended_linkage': len(apps), 'total_appearances_direct_linkage': appearance_direct_count,
        'appearance_linkage_basis_counts': appearance_basis_counts,
        'firms': firms,
        'lead_attorney_appearance_count': lead_count,
        'liaison_note': "no row in this source states a 'liaison counsel' role; only 'Lead attorney' is reported when present",
        'party_counts_by_category': party_counts,
        'total_parties': len(parties), 'total_parties_direct_linkage': party_direct_count,
        'party_linkage_basis_counts': party_basis_counts,
        'qualification': data['qualification_short'] or data['qualification'], 'link': '#mdls?mdl=' + str(mdl_number),
    }
