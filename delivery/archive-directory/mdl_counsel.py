"""MDL counsel layer adapter (firms / attorneys / parties on MDL master dockets) - generic view contract.

Source: sources/mdl_counsel_20260919 (built offline from saved CourtListener connector receipts).
Fail-closed: nothing is served unless validation.json has status == "passed", ready is true and every listed
data file re-hashes to the recorded SHA-256. Public dicts carry no filesystem paths, receipt file names, e-mail
addresses, telephone numbers or street addresses (those exist only in the builder's receipts and are never read here).
No originals are served, so there is no original() function.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parents[2] / 'sources' / 'mdl_counsel_20260919'
REQUIRED = ('attorneys.jsonl', 'firms.jsonl', 'parties.jsonl', 'coverage.json')
KINDS = ('firm', 'attorney', 'party')
KIND_LABELS = {'firm': 'Firms (exact text)', 'attorney': 'Attorneys', 'party': 'Parties'}
COLUMNS = [{'key': 'name', 'label': 'Name'}, {'key': 'mdl', 'label': 'MDL'},
           {'key': 'role', 'label': 'Role or Type'}, {'key': 'count', 'label': 'Count'}]
MAX_LIMIT = 100
DEFAULT_LIMIT = 25

_CACHE: dict = {}
_LOCK = threading.Lock()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(root: Path, names):
    sig = []
    for name in names:
        stat = (root / name).stat()
        sig.append((name, stat.st_size, stat.st_mtime_ns))
    return tuple(sig)


def _load(source_dir=None):
    """Return (state, None) or (None, reason). Re-hashes whenever any file's size/mtime changes."""
    root = Path(source_dir) if source_dir else SOURCE_DIR
    try:
        val_path = root / 'validation.json'
        if not val_path.is_file():
            return None, 'validation.json is missing; the MDL counsel supplement has not been built'
        validation = json.loads(val_path.read_text(encoding='utf-8'))
        if validation.get('status') != 'passed' or validation.get('ready') is not True:
            return None, 'validation status is not passed/ready'
        listed = {item.get('path'): item for item in validation.get('data_files') or []}
        missing = [name for name in REQUIRED if name not in listed]
        if missing:
            return None, 'validation.json does not list required data file(s): ' + ', '.join(missing)
        for name in listed:
            if not name or '/' in name or '\\' in name or '..' in name or not (root / name).is_file():
                return None, f'data file {name} is missing or not a plain file name'
        signature = _signature(root, ['validation.json', *sorted(listed)])
        key = str(root)
        with _LOCK:
            cached = _CACHE.get(key)
            if cached and cached['signature'] == signature:
                return cached['state'], None
        for name, item in sorted(listed.items()):
            if _sha256(root / name) != item.get('sha256'):
                return None, f'hash mismatch for {name}; refusing to serve'
        state = _index(root, validation)
        with _LOCK:
            _CACHE[key] = {'signature': signature, 'state': state}
        return state, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f'supplement could not be loaded ({type(exc).__name__})'


def _read_jsonl(path: Path):
    with path.open(encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _index(root: Path, validation):
    coverage = json.loads((root / 'coverage.json').read_text(encoding='utf-8'))
    rows = {'firm': _read_jsonl(root / 'firms.jsonl'), 'attorney': _read_jsonl(root / 'attorneys.jsonl'),
            'party': _read_jsonl(root / 'parties.jsonl')}
    rows['firm'].sort(key=lambda r: (-int(r.get('attorney_count_total') or 0), (r.get('name') or '').casefold()))
    rows['attorney'].sort(key=lambda r: (r.get('detail_level') != 'attorney_record', (r.get('name') or '').casefold(), r['id']))
    rows['party'].sort(key=lambda r: (r.get('detail_level') != 'party_record', (r.get('name') or '').casefold(), r['id']))
    by_id = {}
    for kind in KINDS:
        for row in rows[kind]:
            by_id[row['id']] = row
    party_attorneys = {}
    for att in rows['attorney']:
        for role in att.get('roles') or []:
            if role.get('cl_party_id') is not None:
                party_attorneys.setdefault((role['cl_party_id'], att['mdl_number']), {}).setdefault(att['id'], []).append(role)
    as_of = (validation.get('validated_at') or '')[:10]
    captured = sorted({(m.get('captured_at') or '')[:10] for m in (coverage.get('mdls') or {}).values() if m.get('captured_at')})
    return {'rows': rows, 'by_id': by_id, 'coverage': coverage, 'validation': validation, 'party_attorneys': party_attorneys,
            'captured_date': captured[-1] if captured else as_of}


def _mdls_of(row):
    if row.get('kind') == 'firm':
        return [int(m) for m in row.get('mdl_numbers') or []]
    return [int(row['mdl_number'])]


def _mdl_links(numbers):
    return [{'label': f'MDL {n}', 'url': f'#mdl/{n}'} for n in numbers]


def _role_text(att):
    labels = []
    for role in att.get('roles') or []:
        label = role.get('role_label') or f"role code {role.get('role_code')}"
        if label not in labels:
            labels.append(label)
    if labels:
        return '; '.join(labels)
    return 'not retrieved (record fetched by id without roles)' if att.get('detail_level') == 'attorney_record' else 'not retrieved (name-only row)'


def _firm_sentence(state):
    counts = state['validation'].get('counts') or {}
    stats = counts.get('search_index_firm_text_filter') or {}
    dropped = int(stats.get('not_published_only_admission_note') or 0) + int(stats.get('not_published_address_or_litigant_line') or 0)
    return (f"{len(state['rows']['firm']):,} firm rows are firm text as recorded (spellings never merged), after removing bar-admission notes about "
            f"individual attorneys from {int(stats.get('published_after_admission_note_removed') or 0):,} source texts and leaving out {dropped:,} "
            'firm-field texts that were only a note or an address line.')


def _qualification(state):
    return ('Counsel, firm-text and party rows for 6 products-liability MDL master dockets, from CourtListener connector responses captured '
            f"{state['captured_date']} (not original court records). Partial: only MDL 2666 has attorney records with roles; the other five MDLs "
            'have 6 attorney records each (id, name, firm line; roles not retrieved; not a leadership roster) and are otherwise name-only '
            f"search-index sets. {_firm_sentence(state)} Member-case counsel are absent.")


def _result(state, row):
    kind = row['kind']
    numbers = _mdls_of(row)
    if kind == 'firm':
        total = int(row.get('attorney_count_total') or 0)
        evidence = row.get('evidence') or []
        role = ' + '.join(filter(None, ['Firm line in attorney contact block' if 'attorney_contact_firm_line' in evidence else '',
                                        'Firm name in CourtListener search index' if 'search_index_firm_field' in evidence else '']))
        count = f"{total} attorney record{'s' if total != 1 else ''}" if total else 'no linked attorney records'
        subtitle = 'Firm text as recorded' + (' (bar-admission note removed)' if row.get('admission_note_source_texts') else '') + '; spellings are not merged'
        badges = ['firm text'] + (['linked attorneys'] if total else ['name only'])
    elif kind == 'attorney':
        role = _role_text(row)
        parties = {r.get('cl_party_id') for r in row.get('roles') or [] if r.get('cl_party_id') is not None}
        count = f"{len(parties)} part{'ies' if len(parties) != 1 else 'y'} represented" if row.get('roles') else 'not retrieved'
        place = ', '.join(filter(None, [row.get('city'), row.get('state')]))
        subtitle = ' - '.join(filter(None, [row.get('firm_text'), place])) or 'Name as listed in the CourtListener search index for the master docket'
        badges = ['attorney record' if row.get('detail_level') == 'attorney_record' else 'name only']
    else:
        types = row.get('party_types') or []
        role = '; '.join(types) if types else 'not returned'
        linked = len(state['party_attorneys'].get((row.get('cl_party_id'), row['mdl_number']), {}))
        count = f"{linked} attorney record{'s' if linked != 1 else ''}" if row.get('detail_level') == 'party_record' else 'not retrieved'
        subtitle = 'Party name as recorded on the master docket'
        badges = ['party record' if row.get('detail_level') == 'party_record' else 'name only']
    return {'id': row['id'], 'title': row.get('name') or '(no name recorded)', 'subtitle': subtitle,
            'cells': {'name': row.get('name') or '', 'mdl': '; '.join(str(n) for n in numbers), 'role': role, 'count': count},
            'badges': badges, 'links': _mdl_links(numbers)}


def _int(value, default, low, high):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def listing(params: dict, source_dir=None) -> dict:
    state, reason = _load(source_dir)
    if state is None:
        return {'available': False, 'reason': reason}
    params = params or {}
    kind = str(params.get('kind') or 'firm').strip().lower()
    if kind not in KINDS:
        kind = 'firm'
    query = ' '.join(str(params.get('q') or '').casefold().split())
    mdl = _int(params.get('mdl'), None, 1, 99999) if str(params.get('mdl') or '').strip() else None
    limit = _int(params.get('limit'), DEFAULT_LIMIT, 1, MAX_LIMIT)
    page = _int(params.get('page'), 1, 1, 10 ** 6)

    def text_of(row):
        parts = [row.get('name') or '']
        if row['kind'] == 'attorney':
            parts += [row.get('firm_text') or '', row.get('city') or '', row.get('state') or '']
        elif row['kind'] == 'firm':
            parts += row.get('name_variants') or []
        return ' '.join(parts).casefold()

    def matches(row, use_mdl=True):
        if use_mdl and mdl is not None and mdl not in _mdls_of(row):
            return False
        return not query or all(token in text_of(row) for token in query.split())

    pool = state['rows'][kind]
    selected = [row for row in pool if matches(row)]
    mdl_counts = {}
    for row in pool:
        if matches(row, use_mdl=False):
            for number in _mdls_of(row):
                mdl_counts[number] = mdl_counts.get(number, 0) + 1
    titles = {int(k): v.get('title') or '' for k, v in (state['coverage'].get('mdls') or {}).items()}
    mdl_options = [{'value': str(n), 'label': f'MDL {n} - {titles.get(n, "")[:60]}'.rstrip(' -'), 'count': mdl_counts.get(n, 0)}
                   for n in sorted(titles)]
    kind_options = [{'value': k, 'label': KIND_LABELS[k],
                     'count': sum(1 for row in state['rows'][k] if (mdl is None or mdl in _mdls_of(row)))} for k in KINDS]
    start = (page - 1) * limit
    return {
        'available': True, 'total': len(selected), 'page': page, 'limit': limit,
        'qualification': _qualification(state),
        'filters': [{'name': 'q', 'label': 'Search', 'type': 'search'},
                    {'name': 'kind', 'label': 'Show', 'type': 'select', 'options': kind_options},
                    {'name': 'mdl', 'label': 'MDL', 'type': 'select', 'options': mdl_options}],
        'columns': COLUMNS,
        'results': [_result(state, row) for row in selected[start:start + limit]],
    }


def _coverage_facts(state, number):
    cov = (state['coverage'].get('mdls') or {}).get(str(number)) or {}
    facts = []
    if cov.get('title'):
        facts.append([f'MDL {number}', cov['title']])
    if cov.get('attorney_coverage_label'):
        facts.append([f'MDL {number} attorney coverage (CourtListener, as captured)', cov['attorney_coverage_label']])
    return facts


def _cl_docket_link(state, number):
    cov = (state['coverage'].get('mdls') or {}).get(str(number)) or {}
    if cov.get('cl_docket_id'):
        return [{'label': f'CourtListener docket for MDL {number}', 'url': f"https://www.courtlistener.com/docket/{int(cov['cl_docket_id'])}/"}]
    return []


def _dates(row):
    temporal = row.get('temporal') or {}
    facts = []
    if temporal.get('source_as_of'):
        facts.append(['CourtListener record last modified (source as of)', str(temporal['source_as_of'])[:10]])
    if temporal.get('captured_at'):
        facts.append(['Connector response saved (UTC)', str(temporal['captured_at'])[:19].replace('T', ' ')])
    return facts


def detail(item_id: str, source_dir=None):
    state, _reason = _load(source_dir)
    if state is None or not item_id:
        return None
    row = state['by_id'].get(str(item_id))
    if row is None:
        return None
    kind, numbers = row['kind'], _mdls_of(row)
    links = _mdl_links(numbers)
    for number in numbers:
        links += _cl_docket_link(state, number)
    sections, facts = [], []
    if kind == 'firm':
        noted, sources = int(row.get('admission_note_source_texts') or 0), len(row.get('source_texts') or [])
        facts = [['Firm text (as recorded)', row.get('name') or ''],
                 ['Exact-text variants sharing this key', '; '.join(row.get('name_variants') or [])],
                 ['Bar-admission note removed by rule', f"{noted} of {sources} source text{'s' if sources != 1 else ''} for this row carried a clerk note about an "
                  "individual attorney's bar admission next to the firm text; the note is not part of the firm name and is not shown" if noted else ''],
                 ['MDLs', '; '.join(str(n) for n in numbers)],
                 ['Linked attorney records', str(int(row.get('attorney_count_total') or 0))],
                 ['How the count is made', (state['coverage'].get('attorney_count_basis') or '').replace('attorneys.jsonl', 'this supplement')]] + _dates(row)
        for slot in row.get('mdls') or []:
            number = int(slot['mdl_number'])
            heading = f"MDL {number}: {int(slot.get('attorney_count') or 0)} linked attorney record(s)" + \
                      ('; firm name also listed in the CourtListener search index' if slot.get('in_search_index') else '')
            items = [{'title': a.get('name') or '', 'subtitle': '; '.join(a.get('role_labels') or []) or 'roles not retrieved',
                      'links': [{'label': f'MDL {number}', 'url': f'#mdl/{number}'}]} for a in slot.get('attorneys') or []]
            if items:
                sections.append({'heading': heading, 'items': items})
            else:
                sections.append({'heading': heading, 'text': 'The source lists this firm name for the master docket but does not link it to '
                                                             'individual attorneys; no attorney records were retrieved for it.'})
        qualification = ('Firm rows are the firm text recorded by the source (firm line of an attorney contact block, or the firm field of the '
                         'CourtListener search index). Different spellings of one firm are separate rows; nothing is merged or inferred. Two '
                         'rule-based edits apply: a bar-admission note about an individual attorney is removed from the firm text, and firm-field '
                         'texts that are only that note, or that are address lines or address fragments, are not published.')
    elif kind == 'attorney':
        number = numbers[0]
        facts = [['Name (as recorded)', row.get('name') or ''], ['MDL', str(number)]]
        if row.get('detail_level') == 'attorney_record':
            facts += [['Firm line (as recorded in contact block)', row.get('firm_text') or 'not parsed'],
                      ['City / state (as printed)', ', '.join(filter(None, [row.get('city'), row.get('state')])) or 'not printed'],
                      ['CourtListener attorney id', str(row.get('cl_attorney_id'))],
                      ['Roles (as recorded)', _role_text(row)]]
            if row.get('mdl_link_basis'):
                facts.append(['Basis of the MDL link', row['mdl_link_basis']])
            rows = [[r.get('party_name') or f"party id {r.get('cl_party_id')} (record not retrieved)",
                     '; '.join(r.get('party_types') or []) or 'not returned',
                     str(r.get('role_code')), r.get('role_label') or '', r.get('date_action') or '']
                    for r in row.get('roles') or []]
            if rows:
                sections.append({'heading': 'Parties represented on the master docket (as recorded)',
                                 'header': ['Party', 'Party type', 'Role code', 'Role label', 'Date of action'], 'rows': rows})
            else:
                sections.append({'heading': 'Roles and parties represented',
                                 'text': 'Not retrieved. ' + (row.get('roles_note') or '') + '. Whether this attorney is lead, liaison, '
                                         'plaintiff-side or defence counsel is not recorded here.'})
            sections.append({'heading': 'How to read this', 'text': (state['coverage'].get('role_label_basis') or '') + '. ' +
                             (state['coverage'].get('firm_rule') or '').replace('contact_raw', 'the attorney contact block')})
        else:
            facts += [['Detail level', 'Name only: listed in the CourtListener search index for the master docket; id, roles, firm and parties were not retrieved'],
                      ['CourtListener attorney id', 'not paired by the source (names and ids are separate unordered sets)']]
        facts += _dates(row) + _coverage_facts(state, number)
        qualification = ('One row per attorney per MDL master docket as recorded by CourtListener/RECAP. People are never merged by name; the same '
                         'name in two MDLs is two rows. Contact details are not published.')
    else:
        number = numbers[0]
        facts = [['Party name (as recorded)', row.get('name') or ''], ['MDL', str(number)],
                 ['Party type (as recorded)', '; '.join(row.get('party_types') or []) or (row.get('party_type_note') or 'not returned')]]
        if row.get('cl_party_id') is not None:
            facts.append(['CourtListener party id', str(row['cl_party_id'])])
        linked = state['party_attorneys'].get((row.get('cl_party_id'), number), {})
        if linked:
            items = []
            for att_id, roles in linked.items():
                att = state['by_id'].get(att_id) or {}
                labels = []
                for role in roles:
                    label = role.get('role_label') or f"role code {role.get('role_code')}"
                    if label not in labels:
                        labels.append(label)
                items.append({'title': att.get('name') or '', 'subtitle': ' - '.join(filter(None, [att.get('firm_text'), '; '.join(labels)])),
                              'links': [{'label': f'MDL {number}', 'url': f'#mdl/{number}'}]})
            items.sort(key=lambda i: i['title'].casefold())
            sections.append({'heading': f'Attorney records naming this party ({len(items)})', 'items': items})
        cov = (state['coverage'].get('mdls') or {}).get(str(number)) or {}
        facts += _dates(row)
        if cov.get('party_coverage_label'):
            facts.append([f'MDL {number} party coverage (CourtListener, as captured)', cov['party_coverage_label']])
        qualification = ('Party rows come from the MDL master docket as held by CourtListener/RECAP. On MDL master dockets many "parties" are '
                         'leadership designations (for example a steering committee). Party names are withheld for dockets with thousands of parties.')
    return {'id': row['id'], 'title': row.get('name') or '(no name recorded)',
            'subtitle': {'firm': 'Firm text on MDL master docket(s)', 'attorney': f'Attorney on MDL {numbers[0]} master docket',
                         'party': f'Party on MDL {numbers[0]} master docket'}[kind],
            'qualification': qualification + ' ' + _qualification(state), 'facts': [f for f in facts if f[1] != ''],
            'sections': sections, 'links': links}
