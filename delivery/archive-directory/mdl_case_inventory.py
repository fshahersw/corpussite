"""Read-only, hash-gated member-case inventory and relationship graph for the MDL slice.

Source: sources/mdl_case_inventory_20260919 (cases.jsonl, graph_edges.jsonl, unresolved.jsonl,
mdl_coverage.json). Generic view contract: listing(params), detail(id). Embedding hooks:
for_mdl(mdl_number), for_judge(entity_id), for_court(court_id).

Fails closed: if validation.json is missing, is not status=passed/ready, or any data-file hash or row
count differs, listing() returns the "not available" shape and every other function returns None.
Nothing raises. Public dicts carry no filesystem paths. No original() — this slice serves no files.

Every number shown is a count of what this corpus holds as of the dataset build date. The corpus is a
firm-focused docket sample, never an MDL's full member-case list; the JPML report's own action counts
are shown beside it as the denominator.
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/mdl_case_inventory_20260919'
FILES = ('cases.jsonl', 'graph_edges.jsonl', 'unresolved.jsonl', 'mdl_coverage.json')
JSONL = ('cases.jsonl', 'graph_edges.jsonl', 'unresolved.jsonl')
MDL_ID_RE = re.compile(r'mdl:(\d+)')
_CACHE = {}


def _load(folder=None):
    folder = Path(folder or DATA)
    gate_bytes = (folder / 'validation.json').read_bytes()
    gate = json.loads(gate_bytes)
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('mdl case inventory validation is not passed/ready')
    listed = {d.get('path'): d for d in gate.get('data_files', [])}
    if set(listed) != set(FILES):
        raise ValueError('mdl case inventory data file list mismatch')
    blobs, digests = {}, [hashlib.sha256(gate_bytes).hexdigest()]
    for name in FILES:
        blobs[name] = (folder / name).read_bytes()
        digest = hashlib.sha256(blobs[name]).hexdigest()
        if digest != listed[name].get('sha256'):
            raise ValueError('mdl case inventory hash gate failed for ' + name)
        digests.append(digest)
    key = (str(folder), tuple(digests))
    if key in _CACHE:
        return _CACHE[key]

    rows = {}
    for name in JSONL:
        rows[name] = [json.loads(line) for line in blobs[name].decode('utf-8').splitlines() if line.strip()]
        if len(rows[name]) != listed[name].get('rows'):
            raise ValueError('mdl case inventory row count mismatch for ' + name)
    coverage = json.loads(blobs['mdl_coverage.json'].decode('utf-8'))
    if len(coverage) != listed['mdl_coverage.json'].get('rows'):
        raise ValueError('mdl case inventory row count mismatch for mdl_coverage.json')

    cases = rows['cases.jsonl']
    by_id = {r['id']: r for r in cases}
    by_mdl, by_court, by_entity, by_person = {}, {}, {}, {}
    for r in cases:
        if r.get('mdl'):
            by_mdl.setdefault(r['mdl']['mdl_number'], []).append(r)
        cid = (r.get('court') or {}).get('court_spine_id')
        if cid:
            by_court.setdefault(cid, []).append(r)
        for j in r.get('judges') or []:
            if j.get('judge_entity_id'):
                by_entity.setdefault(j['judge_entity_id'], []).append((r, j))
            if j.get('cl_person_id'):
                by_person.setdefault(str(j['cl_person_id']), []).append((r, j))

    data = {
        'cases': cases, 'by_id': by_id, 'by_mdl': by_mdl, 'by_court': by_court,
        'by_entity': by_entity, 'by_person': by_person, 'coverage': coverage,
        'edges': rows['graph_edges.jsonl'], 'unresolved': rows['unresolved.jsonl'],
        'qualification': gate.get('qualification') or '',
        'qualification_short': gate.get('qualification_short') or gate.get('qualification') or '',
        'counts': gate.get('counts') or {},
        'court_options': Counter(r['court']['court_spine_id'] for r in cases
                                 if r['court'].get('court_spine_id')),
        'status_options': Counter(r['status'].get('aws_case_status') or 'unknown' for r in cases),
        'year_options': Counter(str(r['filed_year']) for r in cases if r.get('filed_year')),
    }
    _CACHE.clear()
    _CACHE[key] = data
    return data


# ----------------------------------------------------------------- params

def _one(params, name, default=''):
    value = (params or {}).get(name, default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return value.strip() if isinstance(value, str) else default


def _int(params, name, default, low, high):
    try:
        return max(low, min(high, int(_one(params, name, str(default)))))
    except (ValueError, TypeError):
        return default


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        m = MDL_ID_RE.fullmatch(s)
        if m:
            return int(m.group(1))
        if s.isdigit():
            return int(s)
    return None


# ----------------------------------------------------------------- shaping

def _display_title(r):
    if r.get('case_name'):
        return r['case_name']
    return '%s (%s)' % (r.get('docket_number') or 'docket number not recorded',
                        (r.get('court') or {}).get('native_court_id') or 'court not recorded')


def _subtitle(r):
    court = (r.get('court') or {})
    bits = [court.get('court_name') or court.get('native_court_id') or 'court not linked']
    if r.get('dates', {}).get('aws_date_filed'):
        bits.append('filed ' + r['dates']['aws_date_filed'])
    if r.get('catalog_defendant'):
        bits.append('defendant as recorded: ' + str(r['catalog_defendant']))
    return ' · '.join(bits)


def _result(r):
    badges = []
    if r.get('mdl'):
        badges.append('MDL %d' % r['mdl']['mdl_number'])
    else:
        badges.append('No MDL link')
    badges.append(r['node_role'])
    if r.get('case_name') is None:
        badges.append('Caption withheld')
    links = []
    if r.get('courtlistener_docket_url'):
        links.append({'label': 'CourtListener docket', 'url': r['courtlistener_docket_url']})
    if r.get('mdl'):
        links.append({'label': 'MDL %d' % r['mdl']['mdl_number'],
                      'url': '#mdls?mdl=%d' % r['mdl']['mdl_number']})
    return {
        'id': r['id'],
        'title': _display_title(r),
        'subtitle': _subtitle(r),
        'cells': {
            'docket': r.get('docket_number') or '—',
            'court': (r.get('court') or {}).get('court_spine_id') or '—',
            'filed': (r.get('dates') or {}).get('aws_date_filed') or '—',
            'status': (r.get('status') or {}).get('aws_case_status') or '—',
            'mdl': str(r['mdl']['mdl_number']) if r.get('mdl') else '—',
        },
        'badges': badges,
        'links': links,
    }


def _compact(r):
    return {'id': r['id'], 'title': _display_title(r),
            'docket_number': r.get('docket_number'),
            'court_id': (r.get('court') or {}).get('court_spine_id'),
            'date_filed': (r.get('dates') or {}).get('aws_date_filed'),
            'status': (r.get('status') or {}).get('aws_case_status'),
            'mdl_number': r['mdl']['mdl_number'] if r.get('mdl') else None}


def _options(counter, limit=40):
    return [{'value': k, 'label': k, 'count': n}
            for k, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


# ----------------------------------------------------------------- views

def listing(params=None):
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        return {'available': False,
                'reason': 'MDL case inventory supplement is unavailable or failed its hash gate (%s).'
                          % type(error).__name__,
                'total': 0, 'page': 1, 'limit': 0, 'results': []}
    params = params if isinstance(params, dict) else {}
    rows = data['cases']

    q = _one(params, 'q').casefold()
    if q:
        rows = [r for r in rows
                if q in (r.get('docket_number') or '').casefold()
                or q in (r.get('case_name') or '').casefold()
                or q in str(r.get('cl_docket_id') or '')
                or q in (r.get('catalog_defendant') or '').casefold()]
    mdl = _as_int(_one(params, 'mdl'))
    if mdl is not None:
        rows = [r for r in rows if r.get('mdl') and r['mdl']['mdl_number'] == mdl]
    court = _one(params, 'court')
    if court:
        rows = [r for r in rows if (r.get('court') or {}).get('court_spine_id') == court]
    judge = _one(params, 'judge')
    if judge:
        rows = [r for r in rows
                if any(j.get('judge_entity_id') == judge or str(j.get('cl_person_id')) == judge
                       for j in r.get('judges') or [])]
    year = _one(params, 'year')
    if year:
        rows = [r for r in rows if str(r.get('filed_year') or '') == year]
    status = _one(params, 'status')
    if status:
        rows = [r for r in rows if (r.get('status') or {}).get('aws_case_status') == status]

    limit, page = _int(params, 'limit', 25, 1, 100), _int(params, 'page', 1, 1, 10 ** 6)
    start = (page - 1) * limit
    mdl_counts = Counter(r['mdl']['mdl_number'] for r in data['cases'] if r.get('mdl'))
    return {
        'available': True, 'total': len(rows), 'page': page, 'limit': limit,
        'qualification': data['qualification'],
        'filters': [
            {'name': 'q', 'label': 'Search docket / caption / defendant', 'type': 'search'},
            {'name': 'mdl', 'label': 'MDL', 'type': 'select',
             'options': [{'value': str(n), 'label': 'MDL %d' % n, 'count': c}
                         for n, c in sorted(mdl_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:40]]},
            {'name': 'court', 'label': 'Court', 'type': 'select', 'options': _options(data['court_options'])},
            {'name': 'year', 'label': 'Year filed', 'type': 'select',
             'options': [{'value': k, 'label': k, 'count': n}
                         for k, n in sorted(data['year_options'].items(), reverse=True)[:40]]},
            {'name': 'status', 'label': 'Case status (as recorded)', 'type': 'select',
             'options': _options(data['status_options'])},
        ],
        'columns': [{'key': 'docket', 'label': 'Docket'}, {'key': 'court', 'label': 'Court'},
                    {'key': 'filed', 'label': 'Filed'}, {'key': 'status', 'label': 'Status'},
                    {'key': 'mdl', 'label': 'MDL'}],
        'results': [_result(r) for r in rows[start:start + limit]],
    }


def detail(item_id):
    if not isinstance(item_id, str) or not item_id.strip():
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    r = data['by_id'].get(item_id.strip())
    if r is None:
        return None
    court = r.get('court') or {}
    dates = r.get('dates') or {}
    facts = [
        ['Record id', r['id']],
        ['Docket number (AWS release)', r.get('docket_number') or 'not recorded'],
        ['CourtListener docket id', str(r['cl_docket_id']) if r.get('cl_docket_id') else 'not recorded'],
        ['AWS release matter id', r.get('aws_matter_id') or 'not recorded'],
        ['Role in the release', r.get('node_role') or 'not recorded'],
        ['Court (CourtListener id)', court.get('native_court_id') or 'not recorded'],
        ['Court (court spine)', court.get('court_spine_id') or ('not linked: ' + (court.get('court_unlinked_reason') or 'reason not recorded'))],
        ['Date filed (docket filing date, AWS release)', dates.get('aws_date_filed') or 'not recorded'],
        ['Date terminated (AWS release)', dates.get('aws_date_terminated') or 'not recorded'],
        ['Case status (as recorded by the AWS release)', (r.get('status') or {}).get('aws_case_status') or 'not recorded'],
        ['Nature of suit', 'not present in either source; not inferred'],
        ['Source as of', (r.get('temporal') or {}).get('source_as_of') or 'not recorded'],
    ]
    if r.get('case_name') is None:
        facts.append(['Caption', 'withheld — ' + (r.get('case_name_suppressed_reason') or '')])
    if r.get('sw_catalog_docket_id'):
        facts.append(['SW catalog docket id', str(r['sw_catalog_docket_id'])])
        if dates.get('catalog_date_filed'):
            facts.append(['Date filed (SW catalog)', dates['catalog_date_filed']])
        if (r.get('status') or {}).get('catalog_status'):
            facts.append(['Case status (SW catalog)', r['status']['catalog_status']])
    if r.get('catalog_defendant'):
        facts.append(['Defendant as recorded', str(r['catalog_defendant'])])
    if r.get('mdl'):
        facts.append(['MDL', 'MDL %d — %s' % (r['mdl']['mdl_number'], r['mdl'].get('mdl_title') or '')])
        facts.append(['MDL membership basis', r['mdl'].get('membership_kind') or 'not recorded'])
        facts.append(['MDL membership evidence', r['mdl'].get('evidence') or 'not recorded'])
    else:
        facts.append(['MDL', 'no MDL link — ' + (r.get('mdl_unlinked_reason') or 'reason not recorded')])
    for j in r.get('judges') or []:
        if j.get('judge_entity_id'):
            facts.append(['Judge entity (%s)' % j['role'], j['judge_entity_id']])

    sections = []
    jrows = []
    for j in r.get('judges') or []:
        jrows.append([
            j.get('role') or '—',
            j.get('name_as_recorded_by_source') or '—',
            str(j.get('cl_person_id') or '—'),
            j.get('judge_entity_id') or ('not linked: ' + (j.get('judge_entity_unlinked_reason') or '')),
        ])
    if jrows:
        sections.append({'heading': 'Judicial assignments recorded by the release',
                         'header': ['Role', 'Name as recorded', 'CourtListener person id', 'MVP judge entity'],
                         'rows': jrows})
    else:
        sections.append({'heading': 'Judicial assignments recorded by the release',
                         'text': 'No judicial assignment row for this matter in the release.'})
    if r.get('catalog_firms_text'):
        sections.append({'heading': 'Firms as recorded by the SW catalog',
                         'text': ', '.join(str(x) for x in r['catalog_firms_text'])
                                 + ' — ' + (r.get('catalog_firms_note') or '')})
    if r.get('catalog_judge_text'):
        sections.append({'heading': 'Judge text in the SW catalog',
                         'text': '%s — %s' % (r['catalog_judge_text'], r.get('catalog_judge_text_note') or '')})

    links = []
    if r.get('courtlistener_docket_url'):
        links.append({'label': 'CourtListener docket', 'url': r['courtlistener_docket_url']})
    if r.get('mdl'):
        links.append({'label': 'MDL %d' % r['mdl']['mdl_number'], 'url': '#mdls?mdl=%d' % r['mdl']['mdl_number']})
    if (r.get('court') or {}).get('court_spine_id'):
        links.append({'label': 'Court', 'url': '#courts?court=' + r['court']['court_spine_id']})

    return {'id': r['id'], 'title': _display_title(r), 'subtitle': _subtitle(r),
            'qualification': data['qualification'], 'facts': facts, 'sections': sections, 'links': links}


# ----------------------------------------------------------------- hooks

def for_mdl(mdl_number):
    """Compact block for an MDL detail page: counts by court / filed year / status recomputed from
    the rows, the JPML denominator, and up to 25 sample cases. None when this MDL has no cases here."""
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    num = _as_int(mdl_number)
    if num is None:
        return None
    rows = data['by_mdl'].get(num)
    if not rows:
        return None
    cov = data['coverage'].get('mdl:%d' % num, {})
    return {
        'mdl_number': num,
        'total': len(rows),
        'by_court': dict(Counter(r['court'].get('court_spine_id') or 'unlinked' for r in rows)),
        'by_filed_year': dict(Counter(str(r['filed_year']) if r.get('filed_year') else 'unknown' for r in rows)),
        'by_status': dict(Counter((r['status'].get('aws_case_status') or 'unknown') for r in rows)),
        'by_membership_kind': dict(Counter(r['mdl']['membership_kind'] for r in rows)),
        'jpml_actions_pending': cov.get('jpml_actions_pending'),
        'jpml_total_actions': cov.get('jpml_total_actions'),
        'jpml_counts_label': cov.get('jpml_counts_label'),
        'sample': [_compact(r) for r in rows[:25]],
        'link': '#mdl-cases?mdl=%d' % num,
        'qualification': data['qualification_short'],
    }


def for_judge(entity_id):
    """Compact block for a judge profile: this judge's matters in this corpus grouped by MDL.
    Joins only on the MVP judge entity id (bridged from cl_person_id) or on a CourtListener person
    id. Never on a name. None when nothing joins."""
    if not isinstance(entity_id, str) or not entity_id.strip():
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    key = entity_id.strip()
    pairs = data['by_entity'].get(key) or data['by_person'].get(key)
    if not pairs:
        return None
    rows = [r for r, _ in pairs]
    by_mdl = Counter(('mdl:%d' % r['mdl']['mdl_number']) if r.get('mdl') else 'no MDL link' for r in rows)
    return {
        'entity_id': key,
        'total': len(rows),
        'by_mdl': dict(sorted(by_mdl.items())),
        'by_role': dict(Counter(j['role'] for _, j in pairs)),
        'by_court': dict(Counter(r['court'].get('court_spine_id') or 'unlinked' for r in rows)),
        'sample': [dict(_compact(r), role=j['role']) for r, j in pairs[:25]],
        'link': '#mdl-cases?judge=' + key,
        'qualification': data['qualification_short'],
        'join_basis': ('matched on the MVP judge entity bridged from the CourtListener person id '
                       'carried by the release (judges.native_judge_id); never matched by name'),
    }


def for_court(court_id):
    """Compact block for a court detail page: matters held locally in this court. None when none."""
    if not isinstance(court_id, str) or not court_id.strip():
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    rows = data['by_court'].get(court_id.strip())
    if not rows:
        return None
    return {
        'court_id': court_id.strip(),
        'total': len(rows),
        'by_mdl': dict(Counter(('mdl:%d' % r['mdl']['mdl_number']) if r.get('mdl') else 'no MDL link'
                               for r in rows)),
        'by_filed_year': dict(Counter(str(r['filed_year']) if r.get('filed_year') else 'unknown'
                                      for r in rows)),
        'by_status': dict(Counter((r['status'].get('aws_case_status') or 'unknown') for r in rows)),
        'sample': [_compact(r) for r in rows[:25]],
        'link': '#mdl-cases?court=' + court_id.strip(),
        'qualification': data['qualification_short'],
    }


def edges_for_case(item_id):
    """Typed relationship edges for one case (docket-judge, docket-court, docket-MDL), or None."""
    if not isinstance(item_id, str) or not item_id.strip():
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    r = data['by_id'].get(item_id.strip())
    if r is None:
        return None
    key = r['cl_docket_id'] if r.get('cl_docket_id') else r['aws_matter_id']
    out = [e for e in data['edges'] if e['from']['id'] == key]
    return {'id': r['id'], 'total': len(out), 'edges': out[:25],
            'qualification': data['qualification']}
