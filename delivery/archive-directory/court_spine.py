"""Read-only, hash-gated court spine (courts, KY/TX courthouse-level locations, raster identity marks).

Generic view contract: listing(params), detail(id), original(file_id). Fails closed when validation.json or any data
file hash does not match. Public dicts carry no filesystem paths, emails or phone numbers. Nothing is inferred here;
every label repeats a basis written by sources/court_spine_20260919/build.py.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/court_spine_20260919'
ADAPTER = 'court_spine'
FILES = ('courts.jsonl', 'logos.jsonl', 'unresolved.jsonl')
RASTER = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
SYSTEM_LABEL = {'federal': 'Federal', 'state': 'State', 'territory': 'Territory', 'tribal': 'Tribal', 'other': 'Other', 'unknown': 'Unknown'}
KIND_ORDER = {'courtlistener': 0, 'local_registry': 1, 'county_registry': 2}
ID_RE = re.compile(r'[A-Za-z0-9_.\-]{1,120}')
FILE_RE = re.compile(r'logo_[0-9a-f]{16}')
_CACHE = {}


def _load(folder=None):
    """Return the parsed supplement or raise. Hashes are re-checked on every call; parsing is cached per hash set."""
    folder = Path(folder or DATA)
    gate_bytes = (folder / 'validation.json').read_bytes()
    gate = json.loads(gate_bytes)
    if gate.get('schema_version') != '1' or gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('court spine validation is not passed/ready')
    listed = {d.get('path'): d for d in gate.get('data_files', [])}
    if set(listed) != set(FILES):
        raise ValueError('court spine data file list mismatch')
    blobs, digests = {}, [hashlib.sha256(gate_bytes).hexdigest()]
    for name in FILES:
        blobs[name] = (folder / name).read_bytes()
        digest = hashlib.sha256(blobs[name]).hexdigest()
        if digest != listed[name].get('sha256'):
            raise ValueError('court spine hash gate failed for ' + name)
        digests.append(digest)
    key = (str(folder), tuple(digests))
    if key not in _CACHE:
        rows = {n: [json.loads(line) for line in blobs[n].decode('utf-8').splitlines() if line.strip()] for n in FILES}
        if any(len(rows[n]) != listed[n].get('rows') for n in FILES):
            raise ValueError('court spine row count mismatch')
        courts = sorted(rows['courts.jsonl'], key=lambda c: (-c['pending_mdl_count'], not c['has_logo'], KIND_ORDER.get(c['id_kind'], 9), c['name'].casefold(), c['id']))
        for c in courts:
            c['_hay'] = ' '.join(filter(None, [c['id'], c['name'], c['short_name'], c.get('citation_string')]
                                        + [e['key'] + ' ' + e['name'] for e in c['registry']]
                                        + [l['county'] for l in c['locations']])).casefold()
        _CACHE.clear()
        _CACHE[key] = {'courts': courts, 'by_id': {c['id']: c for c in courts}, 'logos': {l['file_id']: l for l in rows['logos.jsonl']},
                       'qualification': gate.get('qualification') or '', 'counts': gate.get('counts') or {}}
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


def _options(courts, getter, labeller, order=None):
    counts = {}
    for c in courts:
        value = getter(c)
        if value:
            counts[value] = counts.get(value, 0) + 1
    keys = sorted(counts, key=order or (lambda v: v))
    return [{'value': v, 'label': labeller(v), 'count': counts[v]} for v in keys]


def _mdl_label(c):
    return 'JPML report dated %s' % c['mdl_as_of'] if c.get('mdl_as_of') else 'JPML report'


def _links(c, data):
    links = []
    if c['listed_mdl_count'] and c['cl_court_id']:
        links.append({'label': 'MDLs in this court (%d pending, %s)' % (c['pending_mdl_count'], _mdl_label(c)), 'url': '#mdls?court=' + quote(c['cl_court_id'], safe='')})
    search = c['short_name'] if c['id_kind'] == 'county_registry' else c['name']
    links.append({'label': 'Search documents for this court name', 'url': '#documents?q=' + quote(search, safe='')})
    seen = set()
    if c.get('website'):
        seen.add(c['website'])
        links.append({'label': 'Court website (%s)' % (c.get('website_basis') or 'as recorded'), 'url': c['website']})
    for e in c['registry']:
        if e.get('homepage') and e['homepage'] not in seen:
            seen.add(e['homepage'])
            links.append({'label': 'Court homepage (local registry %s, not re-checked)' % e['key'], 'url': e['homepage']})
    for fid in c['logo_file_ids']:
        if fid in data['logos']:
            links.append({'label': 'Identity mark image (reuse permission not established)', 'url': '/supplement-files/%s/%s' % (ADAPTER, fid)})
    return links


def _result(c, data):
    bits = [c['jurisdiction_type_label']]
    if c.get('citation_string'):
        bits.append(c['citation_string'])
    bits.append(('CourtListener id ' + c['id']) if c['id_kind'] == 'courtlistener' else
                ('local registry entry, no CourtListener id' if c['id_kind'] == 'local_registry' else
                 'KY/TX county registry, %d count%s' % (len(c['locations']), 'y' if len(c['locations']) == 1 else 'ies')))
    badges = []
    if c['pending_mdl_count']:
        badges.append('%d pending MDL%s' % (c['pending_mdl_count'], '' if c['pending_mdl_count'] == 1 else 's'))
    if c['has_logo']:
        badges.append('Identity mark')
    if c.get('end_date'):
        badges.append('Ended ' + c['end_date'])
    if c['registry']:
        badges.append('Local registry')
    return {'id': c['id'], 'title': c['name'], 'subtitle': ' · '.join(bits),
            'cells': {'court': c['name'], 'system': SYSTEM_LABEL.get(c['system'], c['system']), 'state': c['state'] or '—',
                      'mdls': ('%d pending' % c['pending_mdl_count']) if c['pending_mdl_count'] else '0'},
            'badges': badges, 'links': [l for l in _links(c, data) if not l['url'].startswith('/supplement-files/')][:3]}


def listing(params=None):
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {'available': False, 'reason': 'Court spine supplement is unavailable or failed its hash gate (%s).' % type(error).__name__,
                'total': 0, 'page': 1, 'limit': 0, 'results': []}
    params = params if isinstance(params, dict) else {}
    courts = data['courts']
    q, system, ctype = _one(params, 'q').casefold(), _one(params, 'system').casefold(), _one(params, 'type')
    state, has_logo, has_mdls = _one(params, 'state').upper(), _one(params, 'has_logo').casefold(), _one(params, 'has_mdls').casefold()
    rows = courts
    if q:
        terms = q.split()
        rows = [c for c in rows if all(t in c['_hay'] for t in terms)]
    if system:
        rows = [c for c in rows if c['system'] == system]
    if ctype:
        rows = [c for c in rows if c['jurisdiction_type'] == ctype]
    if state:
        rows = [c for c in rows if c['state'] == state]
    if has_logo in ('yes', 'no'):
        rows = [c for c in rows if c['has_logo'] == (has_logo == 'yes')]
    if has_mdls in ('yes', 'no'):
        rows = [c for c in rows if bool(c['pending_mdl_count']) == (has_mdls == 'yes')]
    limit, page = _int(params, 'limit', 25, 1, 100), _int(params, 'page', 1, 1, 10 ** 6)
    type_labels = {c['jurisdiction_type']: c['jurisdiction_type_label'] for c in courts if c['jurisdiction_type']}
    with_logo, with_mdls = sum(1 for c in courts if c['has_logo']), sum(1 for c in courts if c['pending_mdl_count'])
    filters = [
        {'name': 'q', 'label': 'Search', 'type': 'search'},
        {'name': 'system', 'label': 'System', 'type': 'select', 'options': _options(courts, lambda c: c['system'], lambda v: SYSTEM_LABEL.get(v, v), lambda v: list(SYSTEM_LABEL).index(v) if v in SYSTEM_LABEL else 99)},
        {'name': 'type', 'label': 'Court type', 'type': 'select', 'options': _options(courts, lambda c: c['jurisdiction_type'], lambda v: '%s (%s)' % (type_labels[v], v), lambda v: (':' in v, v))},
        {'name': 'state', 'label': 'State (only where explicit)', 'type': 'select', 'options': _options(courts, lambda c: c['state'], lambda v: v)},
        {'name': 'has_logo', 'label': 'Identity mark', 'type': 'select', 'options': [{'value': 'yes', 'label': 'Has a saved raster mark', 'count': with_logo}, {'value': 'no', 'label': 'No saved mark', 'count': len(courts) - with_logo}]},
        {'name': 'has_mdls', 'label': 'Pending MDLs', 'type': 'select', 'options': [{'value': 'yes', 'label': 'Has pending MDLs (JPML report)', 'count': with_mdls}, {'value': 'no', 'label': 'None listed', 'count': len(courts) - with_mdls}]},
    ]
    start = (page - 1) * limit
    return {'available': True, 'total': len(rows), 'page': page, 'limit': limit, 'qualification': data['qualification'], 'filters': filters,
            'columns': [{'key': 'court', 'label': 'Court'}, {'key': 'system', 'label': 'System'}, {'key': 'state', 'label': 'State'}, {'key': 'mdls', 'label': 'MDLs'}],
            'results': [_result(c, data) for c in rows[start:start + limit]]}


def _qualification(c):
    if c['id_kind'] == 'courtlistener':
        return ('Court record from the CourtListener bulk courts table, snapshot %s (publisher-recorded names, dates, url and flags; not re-checked). '
                'It is not a statement that the court currently sits, and MDL counts are those printed in the %s.') % (c['temporal']['source_as_of'], _mdl_label(c))
    if c['id_kind'] == 'local_registry':
        return ('Entry from the local court registry (retrieved %s); it is not a CourtListener court and no CourtListener id was matched. '
                'A statewide judiciary entry describes a court system, not any single court.') % ((c['temporal']['captured_at'] or 'date not recorded')[:10])
    return ('Court listed in the Kentucky/Texas county registry as of %s; it is not a CourtListener court, no address is recorded, and current service was not verified. %s.'
            % (c['temporal']['source_as_of'], (c.get('grouping_basis') or '')[:1].upper() + (c.get('grouping_basis') or '')[1:]))


def detail(court_id):
    if not isinstance(court_id, str) or not ID_RE.fullmatch(court_id):
        return None
    try:
        data = _load()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    c = data['by_id'].get(court_id)
    if c is None:
        return None
    t = c['temporal']
    facts = [['CourtListener court id' if c['id_kind'] == 'courtlistener' else 'Record id (not a CourtListener id)', c['id']], ['Name', c['name']]]
    if c['short_name'] and c['short_name'] != c['name']:
        facts.append(['Short name', c['short_name']])
    if c.get('citation_string'):
        facts.append(['Citation string', c['citation_string']])
    facts.append(['Court type', '%s (%s)' % (c['jurisdiction_type_label'], c['jurisdiction_type'] or 'code not recorded')])
    facts.append(['System', SYSTEM_LABEL.get(c['system'], c['system'])])
    if c['state']:
        facts.append(['State', '%s — %s' % (c['state'], c['state_basis'])])
    if c.get('start_date'):
        facts.append(['Established (CourtListener start_date)', c['start_date']])
    if c.get('end_date'):
        facts.append(['Ended (CourtListener end_date)', c['end_date']])
    if c.get('in_use') is not None:
        facts.append(['In use (CourtListener flag)', 'yes' if c['in_use'] else 'no'])
    if c.get('parent_court_id'):
        facts.append(['Parent court (CourtListener)', '%s (%s)' % (c.get('parent_court_name') or 'name not recorded', c['parent_court_id'])])
    if c.get('fjc_court_id'):
        facts.append(['FJC court id (as recorded by CourtListener)', c['fjc_court_id']])
    if c.get('pacer_court_id'):
        facts.append(['PACER court id (as recorded by CourtListener)', c['pacer_court_id']])
    if c.get('county_fips'):
        facts.append(['County FIPS', '%s — %s' % (c['county_fips'], c['county_fips_basis'])])
    if c['registry']:
        facts.append(['Local registry key', '; '.join('%s (%s)' % (e['key'], e['crosswalk_kind'].replace('_', ' ')) for e in c['registry'])])
    if c['id_kind'] == 'courtlistener' and c['system'] == 'federal' or c['listed_mdl_count']:
        facts.append(['Pending MDLs (%s)' % _mdl_label(c), str(c['pending_mdl_count'])])
        if c['listed_mdl_count'] != c['pending_mdl_count']:
            facts.append(['MDLs listed, pending and terminated (%s)' % _mdl_label(c), str(c['listed_mdl_count'])])
    facts.append(['Saved raster identity marks', str(len(c['logo_file_ids']))])
    if t.get('source_as_of'):
        facts.append(['Source as of (%s)' % t['source_as_of_basis'], t['source_as_of']])
    if t.get('captured_at'):
        facts.append(['Saved (%s)' % t['captured_at_basis'], t['captured_at'][:10]])

    sections = []
    if c['locations']:
        sections.append({'heading': 'Locations (Kentucky/Texas county registry, as of %s)' % (c['locations'][0].get('source_as_of') or 'date not recorded'),
                         'header': ['County FIPS', 'County', 'State', 'Times listed', 'Address'],
                         'rows': [[l['county_fips'], l['county'], l['state'], str(l['times_listed']), l['address'] or 'not recorded'] for l in c['locations']]})
        sources = []
        for l in c['locations']:
            if l.get('source_url') and l['source_url'] not in [s['links'][0]['url'] for s in sources]:
                sources.append({'title': 'Official listing for %s' % l['county'], 'subtitle': 'Source reference recorded by the county registry; not re-checked',
                                'links': [{'label': 'Open source page', 'url': l['source_url']}]})
        if sources:
            sections.append({'heading': 'Location sources', 'items': sources[:12]})
    marks = [data['logos'][f] for f in c['logo_file_ids'] if f in data['logos']]
    if marks:
        sections.append({'heading': 'Identity marks (reuse permission not established)', 'items': [{
            'title': '%s, %s, %s bytes%s' % (m['kind'].replace('_', ' ').capitalize(), m['mime'], format(m['bytes'], ','),
                                             (', %sx%s px' % (m['width'], m['height'])) if m.get('width') and m.get('height') else ''),
            'subtitle': 'Observed on the official site on %s. %s: %s. Scope recorded as %s. SHA-256 %s.' % (
                (m.get('retrieved_at') or 'date not recorded')[:10], 'Shared mark' if m['shared_mark'] else 'Single-entry mark', m['shared_basis'],
                (m.get('identity_scope') or 'not recorded').replace('_', ' '), m['sha256']),
            'links': [{'label': 'Open image', 'url': '/supplement-files/%s/%s' % (ADAPTER, m['file_id'])}]
                     + ([{'label': 'Page where it was observed', 'url': m['source_page']}] if m.get('source_page') else [])} for m in marks]})
    if c['registry']:
        sections.append({'heading': 'Local court registry entry', 'items': [{
            'title': '%s — %s' % (e['key'], e['name']),
            'subtitle': 'Crosswalk: %s. %s.%s' % (e['crosswalk_kind'].replace('_', ' '), e['crosswalk_basis'].rstrip('.'), (' ' + e['notes']) if e.get('notes') else ''),
            'links': ([{'label': 'Homepage', 'url': e['homepage']}] if e.get('homepage') else [])
                     + [{'label': 'Forms and court resources', 'url': u} for u in e.get('forms_pages', [])]} for e in c['registry']]})
    return {'id': c['id'], 'title': c['name'], 'subtitle': _result(c, data)['subtitle'], 'qualification': _qualification(c),
            'facts': facts, 'sections': sections, 'links': _links(c, data)}


def original(file_id):
    """Serve one registered raster mark, only when the stored bytes still match the registered hash, size and type."""
    if not isinstance(file_id, str) or not FILE_RE.fullmatch(file_id):
        return None
    try:
        logo = _load()['logos'].get(file_id)
        if not logo or logo.get('mime') not in RASTER or not re.fullmatch(r'[0-9a-f]{64}', logo.get('sha256', '')):
            return None
        ext = RASTER[logo['mime']]
        blob = (Path(DATA) / 'assets' / ('%s.%s' % (logo['sha256'], ext))).read_bytes()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if len(blob) != logo.get('bytes') or hashlib.sha256(blob).hexdigest() != logo['sha256'] or not _magic(blob, logo['mime']):
        return None
    return blob, logo['mime'], '%s.%s' % (file_id, ext)


def _magic(data, mime):
    if mime == 'image/png':
        return data[:8] == b'\x89PNG\r\n\x1a\n'
    if mime == 'image/jpeg':
        return data[:3] == b'\xff\xd8\xff'
    if mime == 'image/gif':
        return data[:6] in (b'GIF87a', b'GIF89a')
    return mime == 'image/webp' and data[:4] == b'RIFF' and data[8:12] == b'WEBP'
