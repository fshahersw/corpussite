"""Read-only, hash-gated adapter for the JPML MDL registry (sources/jpml_mdl_20260919).

Loads the supplement fail-closed: validation.json must carry the uniform envelope with
status == "passed", ready == true and a matching SHA-256 for every data file. Public
functions return path-free dicts; original report files are served only as freshly
re-hashed bytes from the registered raw/ folder. Every count is labelled with the report
date it was listed under ("as listed in the JPML report dated <as of>").
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/jpml_mdl_20260919'
DATA_FILES = ('mdls.jsonl', 'documents.jsonl', 'court_map.json', 'edges.jsonl', 'unresolved.jsonl', 'qa.json')
MAX_LIMIT = 200
DEFAULT_LIMIT = 25
FILE_ROUTE = '/mdl-files/'
SORTS = ('pending_desc', 'mdl_number', 'title')
STATUSES = ('pending', 'terminated', 'all')
_DOC_ID = re.compile(r'jpmldoc-[0-9a-f]{3,64}')
_PATH_KEYS = {'raw_path', 'text_path', 'file', 'connector_file', 'source_path', 'path'}
_LOCK = threading.Lock()
_CACHE: dict = {}


# ----------------------------------------------------------------------------- loading
def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(folder: Path):
    sig = []
    for name in ('validation.json',) + DATA_FILES:
        p = folder / name
        try:
            st = p.stat()
        except OSError:
            return None
        sig.append((name, st.st_size, st.st_mtime_ns))
    return tuple(sig)


def _read_jsonl(path: Path):
    rows = []
    for line in path.read_bytes().decode('utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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
    base = folder.resolve()
    seen = {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str):
            return None, 'malformed data_files entry'
        file = (folder / entry['path']).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            return None, 'data file outside the supplement or missing: %s' % entry['path']
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
        mdls = [json.loads(l) for l in seen['mdls.jsonl'].decode('utf-8').splitlines() if l.strip()]
        docs = [json.loads(l) for l in seen['documents.jsonl'].decode('utf-8').splitlines() if l.strip()]
        edges = [json.loads(l) for l in seen['edges.jsonl'].decode('utf-8').splitlines() if l.strip()]
        unresolved = [json.loads(l) for l in seen['unresolved.jsonl'].decode('utf-8').splitlines() if l.strip()]
        court_map = json.loads(seen['court_map.json'].decode('utf-8'))
        qa = json.loads(seen['qa.json'].decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None, 'data file is not valid JSON'
    by_num = {}
    for row in mdls:
        num = row.get('mdl_number')
        if not isinstance(num, int) or num in by_num:
            return None, 'mdls.jsonl identity problem (missing or duplicate mdl_number)'
        by_num[num] = row
    pending = [r for r in mdls if r.get('status') == 'pending']
    as_of = None
    for r in pending:
        as_of = (r.get('temporal') or {}).get('source_as_of')
        if as_of:
            break
    state = {
        'gate': gate, 'mdls': by_num, 'docs': {d['document_id']: d for d in docs if isinstance(d.get('document_id'), str)},
        'edges': edges, 'unresolved': unresolved, 'court_map': court_map if isinstance(court_map, dict) else {}, 'qa': qa if isinstance(qa, dict) else {},
        'as_of': as_of, 'counts_label': ('as listed in the JPML report dated %s' % as_of) if as_of else None,
    }
    return state, None


def _load(folder=None):
    folder = Path(folder) if folder else DATA
    key = str(folder.resolve()) if folder.exists() else str(folder)
    sig = _signature(folder)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and sig is not None and cached['sig'] == sig:
            return cached['state'], cached['reason']
        if sig is None:
            state, reason = None, 'supplement folder or a data file is missing'
        else:
            state, reason = _verify(folder)
        _CACHE[key] = {'sig': sig, 'state': state, 'reason': reason}
        return state, reason


# ----------------------------------------------------------------------------- shaping
def _scrub(obj):
    """Deep copy without any key that names a file-system path."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k not in _PATH_KEYS}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _doc_public(doc):
    out = _scrub(doc)
    out['url'] = FILE_ROUTE + doc['document_id']
    return out


def _summary_row(row):
    judge = row.get('transferee_judge') or {}
    links = row.get('judge_links') or []
    cl = row.get('cl_links') or {}
    return {
        'id': row.get('id'), 'mdl_number': row.get('mdl_number'), 'title': row.get('title'), 'status': row.get('status'),
        'district_code': row.get('district_code'), 'cl_court_id': row.get('cl_court_id'), 'court_name': row.get('court_name'), 'circuit': row.get('circuit'),
        'judge_name_as_printed': judge.get('name_as_printed'), 'judge_title_as_printed': judge.get('title_as_printed'),
        'judge_resolved': bool(links), 'judge_entity_id': links[0]['entity_id'] if links else None,
        'judge_match_kind': links[0].get('match_kind') if links else None,
        'judge_link_basis': links[0].get('basis') if links else None, 'judge_relation': (links[0].get('relation') or 'transferee_judge') if links else None,
        'cl_docket_id': cl.get('docket_id'), 'cl_assigned_to_id': cl.get('assigned_to_id'),
        'actions_pending': row.get('actions_pending'), 'total_actions': row.get('total_actions'), 'counts_label': row.get('counts_label'),
        'litigation_type': row.get('litigation_type'), 'master_docket': row.get('master_docket'),
        'date_transferred': row.get('date_transferred'), 'date_closed': row.get('date_closed'),
        'as_of': (row.get('temporal') or {}).get('source_as_of'), 'has_local_collection': bool(row.get('local_collections')),
    }


def _unavailable(reason, extra=None):
    out = {'available': False, 'reason': reason, 'as_of': None, 'counts_label': None}
    out.update(extra or {})
    return out


def _empty_listing(reason, limit=DEFAULT_LIMIT):
    return _unavailable(reason, {'total': 0, 'page': 1, 'limit': limit, 'pages': 0, 'results': [], 'facets': {}, 'filters': {}})


# ----------------------------------------------------------------------------- public API
def summary(folder=None):
    state, reason = _load(folder)
    if not state:
        return _unavailable(reason)
    rows = list(state['mdls'].values())
    pending = [r for r in rows if r.get('status') == 'pending']
    gate = state['gate']
    qa = state['qa']
    return {
        'available': True, 'as_of': state['as_of'], 'counts_label': state['counts_label'],
        'validated_at': gate.get('validated_at'), 'qualification': gate.get('qualification'), 'license_ref': gate.get('license_ref'),
        'counts': {
            'mdls_pending': len(pending), 'mdls_terminated': sum(1 for r in rows if r.get('status') == 'terminated'),
            'actions_pending': sum(r.get('actions_pending') or 0 for r in pending), 'total_actions': sum(r.get('total_actions') or 0 for r in pending),
            'transferee_districts': len({r.get('district_code') for r in pending if r.get('district_code')}),
            'judges_resolved_mdls': sum(1 for r in pending if r.get('judge_links')),
            'judges_unresolved_mdls': sum(1 for r in pending if not r.get('judge_links')),
            'cl_docket_links': sum(1 for r in rows if (r.get('cl_links') or {}).get('docket_id') is not None),
            'litigation_types': dict(sorted(Counter(r.get('litigation_type') for r in pending if r.get('litigation_type')).items())),
            'documents': len(state['docs']), 'edges': len(state['edges']), 'unresolved': len(state['unresolved']),
        },
        'judge_join': (gate.get('counts') or {}).get('judge_join'),
        'qa': {'status': qa.get('status'), 'checks': len(qa.get('checks') or []), 'mismatches': len(qa.get('mismatches') or []),
               'mismatch_names': [m.get('check') for m in (qa.get('mismatches') or [])]},
        'snapshots': (gate.get('counts') or {}).get('snapshots_quarterly'),
    }


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ('true', '1', 'yes'):
            return True
        if v in ('false', '0', 'no'):
            return False
    return None


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _apply_filters(rows, params):
    status = str(params.get('status') or 'pending').lower()
    if status not in STATUSES:
        status = 'pending'
    if status != 'all':
        rows = [r for r in rows if r.get('status') == status]
    scoped = rows
    q = str(params.get('q') or '').strip().lower()
    if q:
        def hay(r):
            j = r.get('transferee_judge') or {}
            return ' '.join(str(x) for x in (r.get('title'), j.get('name_as_printed'), j.get('name_by_number_report'), r.get('master_docket'),
                                            r.get('mdl_number'), 'mdl-%s' % r.get('mdl_number'), 'mdl %s' % r.get('mdl_number')) if x).lower()
        rows = [r for r in rows if q in hay(r)]
    court = str(params.get('court') or '').strip().lower()
    if court:
        rows = [r for r in rows if court in ((r.get('cl_court_id') or '').lower(), (r.get('district_code') or '').lower())]
    circuit = str(params.get('circuit') or '').strip().lower()
    if circuit:
        rows = [r for r in rows if (r.get('circuit') or '').lower() == circuit]
    ltype = str(params.get('litigation_type') or '').strip().lower()
    if ltype:
        rows = [r for r in rows if (r.get('litigation_type') or '').lower() == ltype]
    resolved = _bool(params.get('judge_resolved'))
    if resolved is not None:
        rows = [r for r in rows if bool(r.get('judge_links')) == resolved]
    min_pending = _int(params.get('min_pending'), None) if params.get('min_pending') not in (None, '') else None
    if min_pending is not None:
        rows = [r for r in rows if (r.get('actions_pending') or 0) >= min_pending]
    applied = {'status': status, 'q': q or None, 'court': court or None, 'circuit': circuit or None, 'litigation_type': ltype or None,
               'judge_resolved': resolved, 'min_pending': min_pending}
    return rows, scoped, applied


def _sort(rows, sort):
    if sort == 'mdl_number':
        return sorted(rows, key=lambda r: r.get('mdl_number') or 0)
    if sort == 'title':
        return sorted(rows, key=lambda r: ((r.get('title') or '').lower(), r.get('mdl_number') or 0))
    return sorted(rows, key=lambda r: (r.get('actions_pending') is None, -(r.get('actions_pending') or 0), r.get('mdl_number') or 0))


def listing(params=None, folder=None):
    params = params if isinstance(params, dict) else {}
    limit = max(1, min(MAX_LIMIT, _int(params.get('limit'), DEFAULT_LIMIT)))
    state, reason = _load(folder)
    if not state:
        return _empty_listing(reason, limit)
    rows, scoped, applied = _apply_filters(list(state['mdls'].values()), params)
    sort = str(params.get('sort') or 'pending_desc')
    if sort not in SORTS:
        sort = 'pending_desc'
    rows = _sort(rows, sort)
    total = len(rows)
    pages = max(1, (total + limit - 1) // limit) if total else 0
    page = max(1, _int(params.get('page'), 1))
    if pages and page > pages:
        page = pages
    start = (page - 1) * limit
    facets = {
        'litigation_type': sorted(Counter(r.get('litigation_type') for r in scoped if r.get('litigation_type')).items(), key=lambda kv: (-kv[1], kv[0])),
        'circuit': sorted(Counter(r.get('circuit') for r in scoped if r.get('circuit')).items(), key=lambda kv: (-kv[1], kv[0])),
        'court': sorted(Counter(r.get('cl_court_id') or r.get('district_code') for r in scoped).items(), key=lambda kv: (-kv[1], str(kv[0]))),
        'judge_resolved': sorted(Counter('true' if r.get('judge_links') else 'false' for r in scoped).items()),
    }
    return {
        'available': True, 'as_of': state['as_of'], 'counts_label': state['counts_label'], 'total': total, 'page': page, 'limit': limit, 'pages': pages,
        'sort': sort, 'filters': applied, 'facets': facets, 'results': [_summary_row(r) for r in rows[start:start + limit]],
        'qualification': state['gate'].get('qualification'),
    }


def detail(mdl_number, folder=None):
    state, _ = _load(folder)
    if not state:
        return None
    num = _int(mdl_number, None)
    if num is None:
        return None
    row = state['mdls'].get(num)
    if not row:
        return None
    out = _scrub(row)
    doc_ids = []
    for ref in (row.get('reports') or []) + (row.get('snapshots') or []):
        did = ref.get('document_id')
        if did in state['docs'] and did not in doc_ids:
            doc_ids.append(did)
    out['documents'] = [_doc_public(state['docs'][d]) for d in doc_ids]
    out['court'] = _scrub(state['court_map'].get(row.get('district_code')) or {})
    out['edges'] = _scrub([e for e in state['edges'] if (e.get('from') or {}).get('id') == row.get('id')])
    out['provenance'] = {
        'source_as_of': (row.get('temporal') or {}).get('source_as_of'), 'captured_at': (row.get('temporal') or {}).get('captured_at'),
        'reports': _scrub(row.get('reports') or []), 'temporal': _scrub(row.get('temporal') or {}),
        'publisher': 'United States Judicial Panel on Multidistrict Litigation (CM/ECF statistics reports)',
        'connector_note': ((row.get('cl_links') or {}).get('label')) if row.get('cl_links') else None,
    }
    out['summary'] = _summary_row(row)
    out['counts_label'] = row.get('counts_label')
    return out


def _by_predicate(pred, folder):
    state, reason = _load(folder)
    if not state:
        return _unavailable(reason, {'total': 0, 'results': []})
    rows = _sort([r for r in state['mdls'].values() if pred(r)], 'pending_desc')
    return {'available': True, 'as_of': state['as_of'], 'counts_label': state['counts_label'], 'total': len(rows), 'results': [_summary_row(r) for r in rows]}


def for_judge(entity_id, folder=None):
    eid = str(entity_id or '').strip()
    out = _by_predicate(lambda r: any(l.get('entity_id') == eid for l in (r.get('judge_links') or [])) if eid else False, folder)
    out['entity_id'] = eid or None
    # jpml_name_court = printed name + court join; cl_person_native_bridge = CourtListener person on the master docket == FJC-id bridge of the profile
    bases = sorted({b for b in (r.get('judge_link_basis') for r in out.get('results') or []) if b})
    out['basis'] = '+'.join(bases) if bases else 'jpml_name_court'
    return out


def for_person(cl_person_id, folder=None):
    pid = _int(cl_person_id, None)
    out = _by_predicate(lambda r: pid is not None and (r.get('cl_links') or {}).get('assigned_to_id') == pid, folder)
    out['cl_person_id'] = pid
    out['basis'] = 'cl_assigned_to_id'
    return out


def documents(folder=None):
    state, _ = _load(folder)
    if not state:
        return []
    return [_doc_public(d) for d in sorted(state['docs'].values(), key=lambda d: d.get('seq') or 0)]


def original(document_id, folder=None):
    """(bytes, mime, filename) for a registered original, only after re-hashing; None otherwise."""
    if not isinstance(document_id, str) or not _DOC_ID.fullmatch(document_id):
        return None
    state, _ = _load(folder)
    if not state:
        return None
    doc = state['docs'].get(document_id)
    if not doc or not isinstance(doc.get('raw_path'), str) or not isinstance(doc.get('sha256'), str):
        return None
    base = (Path(folder) if folder else DATA).resolve()
    file = (base / doc['raw_path']).resolve()
    if not file.is_relative_to(base / 'raw') or not file.is_file():
        return None
    try:
        data = file.read_bytes()
    except OSError:
        return None
    if _digest(data) != doc['sha256'] or (isinstance(doc.get('bytes'), int) and len(data) != doc['bytes']):
        return None
    return data, doc.get('mime') or 'application/octet-stream', doc.get('filename') or file.name
