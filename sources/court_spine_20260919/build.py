"""Court spine: courts + courthouse locations + raster identity marks, built from LOCAL data only (no network).

Re-runnable. Reads inputs read-only, writes courts.jsonl, logos.jsonl, unresolved.jsonl, assets/<sha256>.<ext>,
validation.json (uniform envelope). Nothing is inferred: state only from printed names, crosswalks only by exact id
or the small reviewed map below, FIPS only where the county is named by the source.
"""
import collections
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
CATALOG = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
LINKS = ROOT / 'sources/local_asset_discovery_20260918/court_links.jsonl'
ASSOC = ROOT / 'sources/local_asset_discovery_20260918/asset_associations.jsonl'
IMAGES = ROOT / 'sources/local_asset_discovery_20260918/image_files.jsonl'
REGISTRIES = ROOT / 'sources/local_library_presentation_20260918/court-registries.jsonl'
COUNTIES = ROOT / 'sources/county_registry_integration_20260919/counties.json'
COUNTIES_GATE = ROOT / 'sources/county_registry_integration_20260919/validation.json'
MDLS = ROOT / 'sources/jpml_mdl_20260919/mdls.jsonl'
MDLS_GATE = ROOT / 'sources/jpml_mdl_20260919/validation.json'

RASTER = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
MARK_KINDS = ('court_seal', 'court_logo', 'judiciary_logo')

# CourtListener jurisdiction codes (publisher's code list). System is a direct function of the recorded code.
JURISDICTIONS = {
    'F': ('Federal appellate', 'federal'), 'FD': ('Federal district', 'federal'), 'FB': ('Federal bankruptcy', 'federal'),
    'FBP': ('Federal bankruptcy appellate panel', 'federal'), 'FS': ('Federal special', 'federal'),
    'MA': ('Military appellate', 'federal'),
    'S': ('State supreme', 'state'), 'SA': ('State appellate', 'state'), 'ST': ('State trial', 'state'),
    'SS': ('State special', 'state'), 'SAG': ('State attorney general', 'state'),
    'TS': ('Territory supreme', 'territory'), 'TA': ('Territory appellate', 'territory'),
    'TT': ('Territory trial', 'territory'), 'T': ('Territory (code T as recorded)', 'territory'),
    'TRS': ('Tribal supreme', 'tribal'), 'TRA': ('Tribal appellate', 'tribal'), 'TRT': ('Tribal trial', 'tribal'),
    'TRX': ('Tribal special', 'tribal'),
    'C': ('Committee', 'other'), 'I': ('International', 'other'),
}
STATE_FROM_NAME_CODES = {'FD', 'FB', 'S', 'SA', 'ST', 'SS', 'SAG', 'TS', 'TA', 'TT'}

USPS = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA', 'Colorado': 'CO',
    'Connecticut': 'CT', 'Delaware': 'DE', 'District of Columbia': 'DC', 'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI',
    'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA',
    'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
    'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ',
    'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
    'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
    'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA',
    'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY', 'Puerto Rico': 'PR', 'Guam': 'GU',
    'Virgin Islands': 'VI', 'American Samoa': 'AS', 'Northern Mariana Islands': 'MP',
}
TERRITORIES = {'PR', 'GU', 'VI', 'AS', 'MP'}
STATE_RE = re.compile(r'\b(' + '|'.join(sorted((re.escape(n) for n in USPS), key=len, reverse=True)) + r')\b(?!\s+(?:County|Parish|City|Territory)\b)')

# Reviewed crosswalk (registry key -> CourtListener id). Each pair was read side by side; the build re-checks the
# CourtListener name against `expect`. Anything not listed here and without an exact id stays unresolved.
REVIEWED = {
    'FB:azb': ('arb', 'United States Bankruptcy Court, D. Arizona', 'CourtListener uses id "arb" for the Arizona bankruptcy court; its recorded url is on the azb.uscourts.gov host named by the registry entry.'),
    'ST:as_state': ('amsamoa', 'High Court of American Samoa', 'Registry entry and CourtListener court carry the same institution name.'),
    'LC:ca_alameda': ('calsuppctala', 'Superior Court of California, County of Alameda', 'Registry entry is the Civil Division of this named court.'),
    'LC:ca_los_angeles': ('calsuppctla', 'Superior Court of California, County of Los Angeles', 'Registry entry is the Civil Division of this named court.'),
    'LC:ca_san_francisco': ('calsuppctsf', 'Superior Court of California, County of San Francisco', 'Registry entry is the Civil Division of this named court.'),
    'LC:ny_new_york': ('nysupctnewyork', 'New York Supreme Court, New York County', 'Registry entry is the Civil Term of this named court.'),
    'LC:pa_philadelphia': ('pactcomplphilad', 'Pennsylvania Court of Common Pleas, Philadelphia County', 'Registry entry is the Trial Division (Civil) of this named court.'),
}
# County named in the local-court registry title -> Census county FIPS (reviewed, 6 rows).
LC_FIPS = {
    'LC:ca_alameda': ('06001', 'Alameda County, California'), 'LC:ca_los_angeles': ('06037', 'Los Angeles County, California'),
    'LC:ca_san_francisco': ('06075', 'San Francisco County, California'), 'LC:mo_st_louis': ('29510', 'St. Louis city, Missouri'),
    'LC:ny_new_york': ('36061', 'New York County, New York'), 'LC:pa_philadelphia': ('42101', 'Philadelphia County, Pennsylvania'),
}
STATEWIDE_NUMBERED = {
    'TX': re.compile(r'^\d+(st|nd|rd|th) District Court$'),
    'KY': re.compile(r'^\d+(st|nd|rd|th) Judicial (Circuit|District)$'),
}
COUNTY_TYPE_LABEL = {
    'jp': 'Texas justice court (county registry)', 'district': 'Texas district court (county registry)',
    'ccl': 'Texas county court at law (county registry)', 'county': 'Texas constitutional county court (county registry)',
    'Circuit Court': 'Kentucky circuit court (county registry)', 'District Court': 'Kentucky district court (county registry)',
    'Family Court': 'Kentucky family court (county registry)',
}
FORBIDDEN = re.compile(r'[A-Za-z]:[\\/]|Users/|mailto:|\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b')


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def jsonl(path):
    return [json.loads(line) for line in open(path, encoding='utf-8') if line.strip()]


def fix(text):
    return (text or '').strip()


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.casefold()).strip('-')


def state_from(name):
    found = {USPS[m.group(1)] for m in STATE_RE.finditer(name or '')}
    return found.pop() if len(found) == 1 else None


def magic_ok(data, mime):
    if mime == 'image/png':
        return data[:8] == b'\x89PNG\r\n\x1a\n'
    if mime == 'image/jpeg':
        return data[:3] == b'\xff\xd8\xff'
    if mime == 'image/gif':
        return data[:6] in (b'GIF87a', b'GIF89a')
    if mime == 'image/webp':
        return data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    return False


def temporal(**kw):
    block = {}
    for key in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to'):
        block[key] = kw.get(key)
        basis = kw.get(key + '_basis')
        block[key + '_basis'] = basis if (kw.get(key) or basis == 'not recorded') else None
    return block


def main():
    checks, unresolved = [], []
    # ---- gates on sibling inputs -------------------------------------------------------------------------------
    cgate = json.loads(COUNTIES_GATE.read_text(encoding='utf-8'))
    if cgate.get('status') != 'passed' or cgate.get('counties_sha256') != sha(COUNTIES):
        raise SystemExit('county registry input failed its own hash gate')
    mgate = json.loads(MDLS_GATE.read_text(encoding='utf-8'))
    mhash = {d['path']: d['sha256'] for d in mgate.get('data_files', [])}
    if mgate.get('status') != 'passed' or mgate.get('ready') is not True or mhash.get('mdls.jsonl') != sha(MDLS):
        raise SystemExit('JPML MDL input failed its own hash gate')

    # ---- CourtListener courts ----------------------------------------------------------------------------------
    db = sqlite3.connect('file:' + CATALOG.as_posix() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    snapshot = json.loads(db.execute("select value_json from import_metadata where key='source_snapshot'").fetchone()[0])
    cl = {r['id']: dict(r) for r in db.execute('select * from courts')}
    db.close()
    courts = {}
    for cid, r in cl.items():
        code = r['jurisdiction']
        label, system = JURISDICTIONS.get(code.upper() if code else '', ('Not recorded' if not code else 'Code ' + code, 'unknown'))
        state = basis = None
        if code.upper() in STATE_FROM_NAME_CODES:
            state = state_from(r['full_name'])
            basis = 'state name printed in the CourtListener court name' if state else None
            parent_state = state_from(cl[r['parent_court_id']]['full_name']) if r['parent_court_id'] in cl else None
            if state and parent_state and state != parent_state:
                # e.g. "Justice Court of Village of Port Washington" under "New York Justice Courts": a place name, not a state.
                unresolved.append({'kind': 'state_conflict', 'key': cid, 'name': r['full_name'].strip(),
                                   'reason': 'court name prints %s but parent court name prints %s; state left unknown' % (state, parent_state)})
                state = basis = None
            elif not state and parent_state:
                state = parent_state
                basis = 'state name printed in the CourtListener parent court name (%s)' % r['parent_court_id']
        parent = cl.get(r['parent_court_id'])
        courts[cid] = {
            'id': cid, 'id_kind': 'courtlistener', 'cl_court_id': cid,
            'name': r['full_name'].strip(), 'short_name': r['short_name'].strip(), 'citation_string': r['citation_string'].strip() or None,
            'jurisdiction_type': code or None, 'jurisdiction_type_label': label, 'system': system,
            'state': state, 'state_basis': basis,
            'start_date': r['start_date'] or None, 'end_date': r['end_date'] or None,
            'in_use': {'t': True, 'f': False}.get(r['in_use']),
            'parent_court_id': r['parent_court_id'] or None, 'parent_court_name': parent['full_name'].strip() if parent else None,
            'fjc_court_id': r['fjc_court_id'] or None, 'pacer_court_id': r['pacer_court_id'] or None,
            'website': r['url'].strip() or None, 'website_basis': 'url as recorded by CourtListener, not re-checked' if r['url'].strip() else None,
            'registry': [], 'county_fips': None, 'county_fips_basis': None,
            'has_logo': False, 'logo_file_ids': [],
            'pending_mdl_count': 0, 'listed_mdl_count': 0, 'mdl_as_of': None,
            'locations': [],
            'temporal': temporal(source_as_of=snapshot, source_as_of_basis='CourtListener bulk courts snapshot date',
                                 captured_at_basis='not recorded',
                                 effective_from=r['start_date'] or None, effective_from_basis='CourtListener start_date (court start as recorded by the publisher)',
                                 effective_to=r['end_date'] or None, effective_to_basis='CourtListener end_date (court end as recorded by the publisher)'),
        }
    checks.append({'check': 'CourtListener courts rows', 'expected': 3361, 'actual': len(courts), 'ok': len(courts) == 3361})

    # ---- local registry crosswalk ------------------------------------------------------------------------------
    links = jsonl(LINKS)
    regs = {r['metadata']['source_record']['collection_id']: r for r in jsonl(REGISTRIES)}
    key_to_court = {}
    kinds = collections.Counter()
    for link in links:
        key = link['court_id']
        fam, tail = key.split(':', 1)
        src = (regs.get(key) or {}).get('metadata', {}).get('source_record', {})
        entry = {
            'key': key, 'family': fam, 'name': fix(link['name']), 'homepage': link.get('homepage') or None,
            'forms_pages': [u for u in src.get('forms_pages', []) if isinstance(u, str) and u.startswith('http')][:8],
            'level': src.get('level'), 'jurisdiction_as_recorded': src.get('jurisdiction'), 'notes': src.get('notes') or None,
            'retrieved_at': link.get('last_retrieved_at'), 'visual_status': link.get('visual_status'),
        }
        if fam in ('F', 'FD', 'FB', 'FS') and tail in cl:
            target, entry['crosswalk_kind'] = tail, 'exact_id'
            entry['crosswalk_basis'] = 'registry key suffix equals the CourtListener court id'
        elif key in REVIEWED:
            target, expect, why = REVIEWED[key]
            if cl.get(target, {}).get('full_name', '').strip() != expect:
                raise SystemExit('reviewed crosswalk target changed: ' + key)
            entry['crosswalk_kind'], entry['crosswalk_basis'] = 'reviewed_institution_match', why
        else:
            target = None
            entry['crosswalk_kind'] = 'unresolved'
            entry['crosswalk_basis'] = ('statewide judiciary system entry; not a single court, so it is not a CourtListener court'
                                        if fam == 'ST' else 'no CourtListener court with this institution name was found')
        kinds[entry['crosswalk_kind']] += 1
        if target is None:
            rid = 'reg-%s-%s' % (fam, tail)
            state = USPS.get((regs.get(key) or {}).get('state') or '')
            courts[rid] = {
                'id': rid, 'id_kind': 'local_registry', 'cl_court_id': None, 'name': entry['name'], 'short_name': entry['name'],
                'citation_string': None,
                'jurisdiction_type': 'REG:' + fam,
                'jurisdiction_type_label': 'State judiciary system (local registry entry)' if fam == 'ST' else 'Local court (local registry entry, no CourtListener id)',
                'system': 'territory' if state in TERRITORIES else 'state',
                'state': state, 'state_basis': 'state field of the local court registry entry' if state else None,
                'start_date': None, 'end_date': None, 'in_use': None, 'parent_court_id': None, 'parent_court_name': None,
                'fjc_court_id': None, 'pacer_court_id': None,
                'website': entry['homepage'], 'website_basis': 'homepage as recorded in the local court registry, not re-checked' if entry['homepage'] else None,
                'registry': [], 'county_fips': None, 'county_fips_basis': None, 'has_logo': False, 'logo_file_ids': [],
                'pending_mdl_count': 0, 'listed_mdl_count': 0, 'mdl_as_of': None, 'locations': [],
                'temporal': temporal(captured_at=entry['retrieved_at'], captured_at_basis='last_retrieved_at of the local asset registry entry',
                                     source_as_of_basis='not recorded'),
            }
            target = rid
            unresolved.append({'kind': 'registry_key_without_cl_court', 'key': key, 'name': entry['name'], 'reason': entry['crosswalk_basis']})
        courts[target]['registry'].append(entry)
        if key in LC_FIPS:
            courts[target]['county_fips'] = LC_FIPS[key][0]
            courts[target]['county_fips_basis'] = 'county named in the local registry title (%s); FIPS from the reviewed 6-row map in build.py' % LC_FIPS[key][1]
        key_to_court[key] = target
    checks.append({'check': 'registry keys placed', 'expected': 269, 'actual': len(key_to_court), 'ok': len(key_to_court) == 269})

    # ---- raster identity marks ---------------------------------------------------------------------------------
    images = {i['asset_id']: i for i in jsonl(IMAGES)}
    assoc = collections.defaultdict(list)
    for row in jsonl(ASSOC):
        if row.get('court_id') and row.get('kind') in MARK_KINDS and row.get('mime_type') in RASTER and row.get('file_verified'):
            assoc[row['court_id']].append(row)
    (OUT / 'assets').mkdir(exist_ok=True)
    logos, skipped = {}, collections.Counter()
    for link in links:
        key = link['court_id']
        rows = assoc.get(key, [])
        chosen = next((r for r in rows if r['asset_id'] == link.get('primary_asset_id')), None)
        role = 'registry primary mark'
        if chosen is None and rows:
            chosen = max(rows, key=lambda r: (r.get('candidate_score') or 0, r['sha256']))
            role = 'best raster mark (registry primary was not a servable raster)'
        if chosen is None:
            skipped['no raster court mark'] += 1
            continue
        image = images.get(chosen['asset_id'])
        digest, mime = chosen['sha256'], chosen['mime_type']
        stored = OUT / 'assets' / ('%s.%s' % (digest, RASTER[mime]))
        data = None
        source = Path(image['absolute_path']) if image else None
        if source and source.is_file():
            data = source.read_bytes()
        elif stored.is_file():
            data = stored.read_bytes()
        if data is None or hashlib.sha256(data).hexdigest() != digest or len(data) != chosen['bytes'] or not magic_ok(data, mime):
            skipped['bytes missing or not matching manifest'] += 1
            unresolved.append({'kind': 'logo_not_registered', 'key': key, 'reason': 'local bytes missing, hash/size mismatch or wrong file signature'})
            continue
        if not stored.is_file() or stored.read_bytes() != data:
            stored.write_bytes(data)
        logo = logos.setdefault(digest, {
            'file_id': 'logo_' + digest[:16], 'sha256': digest, 'bytes': len(data), 'mime': mime,
            'width': chosen.get('width'), 'height': chosen.get('height'), 'kind': chosen['kind'],
            'identity_scope': chosen.get('identity_scope'), 'recommended_background': chosen.get('recommended_background'),
            'source_page': chosen.get('source_page'), 'asset_url': chosen.get('asset_url'),
            'retrieved_at': chosen.get('retrieved_at'), 'permission_status': 'not_established',
            'reuse_note': chosen.get('reuse_note'), 'court_ids': [], 'registry_keys': [], 'roles': {},
        })
        if chosen.get('permission_status') != 'not_established':
            raise SystemExit('unexpected permission status')
        court_id = key_to_court[key]
        if court_id not in logo['court_ids']:
            logo['court_ids'].append(court_id)
        logo['registry_keys'].append(key)
        logo['roles'][key] = role
        courts[court_id]['has_logo'] = True
        if logo['file_id'] not in courts[court_id]['logo_file_ids']:
            courts[court_id]['logo_file_ids'].append(logo['file_id'])
    for logo in logos.values():
        scope = logo['identity_scope'] or ''
        logo['shared_mark'] = len(logo['registry_keys']) > 1 or scope.startswith('shared_') or scope in ('statewide_judiciary_mark', 'state_government_mark')
        logo['shared_basis'] = ('same bytes observed on %d registry entries in this pack' % len(logo['registry_keys']) if len(logo['registry_keys']) > 1
                                else ('identity scope recorded as ' + scope if logo['shared_mark'] else 'observed on one registry entry in this pack; no exclusivity is implied'))
        logo['temporal'] = temporal(captured_at=logo['retrieved_at'], captured_at_basis='retrieved_at of the asset association record')
    if len({l['file_id'] for l in logos.values()}) != len(logos):
        raise SystemExit('file_id collision')
    checks.append({'check': 'logos are raster only', 'ok': all(l['mime'] in RASTER for l in logos.values()), 'actual': dict(collections.Counter(l['mime'] for l in logos.values()))})

    # ---- pending MDLs (JPML report) ----------------------------------------------------------------------------
    mdls = jsonl(MDLS)
    pending_total = 0
    for m in mdls:
        cid = m.get('cl_court_id')
        if not cid:
            continue
        if cid not in courts:
            unresolved.append({'kind': 'mdl_court_not_in_spine', 'key': m['id'], 'reason': 'cl_court_id %s is not a CourtListener court id' % cid})
            continue
        courts[cid]['listed_mdl_count'] += 1
        courts[cid]['mdl_as_of'] = max(filter(None, [courts[cid]['mdl_as_of'], m['temporal']['source_as_of']]))
        if m.get('status') == 'pending':
            courts[cid]['pending_mdl_count'] += 1
            pending_total += 1
    checks.append({'check': 'pending MDLs joined on cl_court_id', 'expected': sum(1 for m in mdls if m.get('status') == 'pending' and m.get('cl_court_id')),
                   'actual': pending_total, 'ok': pending_total == sum(1 for m in mdls if m.get('status') == 'pending' and m.get('cl_court_id'))})

    # ---- KY/TX county registry courts -> locations[] -----------------------------------------------------------
    counties = json.loads(COUNTIES.read_text(encoding='utf-8'))
    listed = 0
    for fips, county in sorted(counties.items()):
        if not re.fullmatch(r'\d{5}', fips) or fips != county.get('geoid'):
            raise SystemExit('bad county key ' + fips)
        st = USPS[county['state']]
        for ct in county['courts']:
            listed += 1
            name, ctype = ct['name'].strip(), ct['type']
            statewide = bool(STATEWIDE_NUMBERED[st].match(name))
            if statewide:
                rid = 'cty-%s-%s-%s' % (st, slug(ctype), slug(name))
                title = '%s \u2014 %s' % (name, 'Texas' if st == 'TX' else 'Kentucky ' + ctype)
            else:
                rid = 'cty-%s-%s-%s' % (fips, slug(ctype), slug(name))
                title = '%s — %s, %s' % (name, county['county'], county['state'])
            row = courts.get(rid)
            if row is None:
                row = courts[rid] = {
                    'id': rid, 'id_kind': 'county_registry', 'cl_court_id': None, 'name': title, 'short_name': name, 'citation_string': None,
                    'jurisdiction_type': 'CR:' + ctype, 'jurisdiction_type_label': COUNTY_TYPE_LABEL[ctype], 'system': 'state',
                    'state': st, 'state_basis': 'state of the county registry record that lists this court',
                    'start_date': None, 'end_date': None, 'in_use': None, 'parent_court_id': None, 'parent_court_name': None,
                    'fjc_court_id': None, 'pacer_court_id': None, 'website': None, 'website_basis': None,
                    'registry': [], 'county_fips': None, 'county_fips_basis': None, 'has_logo': False, 'logo_file_ids': [],
                    'pending_mdl_count': 0, 'listed_mdl_count': 0, 'mdl_as_of': None, 'locations': [],
                    'grouping_basis': ('one record per recorded name and type within the state: numbered %s are numbered statewide, so the same number listed under several counties is one court'
                                       % ('Texas district courts' if st == 'TX' else 'Kentucky judicial circuits/districts')) if statewide
                                      else 'one record per county listing; the same generic name in another county is a different court',
                    'temporal': temporal(source_as_of=county.get('source_as_of'), source_as_of_basis='source_as_of of the county registry record',
                                         captured_at_basis='not recorded'),
                }
            loc = next((l for l in row['locations'] if l['county_fips'] == fips), None)
            if loc is None:
                page = ((ct.get('provenance') or {}).get('source_page') or {}).get('value')
                row['locations'].append({'county_fips': fips, 'county': county['county'], 'state': st, 'source_url': ct.get('url'),
                                         'source_page': page, 'source_as_of': county.get('source_as_of'), 'times_listed': 1,
                                         'address': None, 'address_basis': 'no court address is recorded in the county registry'})
            else:
                loc['times_listed'] += 1
    located = sum(l['times_listed'] for c in courts.values() for l in c['locations'])
    checks.append({'check': 'county registry court listings carried into locations[]', 'expected': 2358, 'actual': located, 'ok': located == listed == 2358})

    # ---- write + validate --------------------------------------------------------------------------------------
    order = sorted(courts.values(), key=lambda c: (c['id_kind'] != 'courtlistener', c['id_kind'], c['id']))
    text = ''.join(json.dumps(c, ensure_ascii=False, sort_keys=True) + '\n' for c in order)
    logo_text = ''.join(json.dumps(l, ensure_ascii=False, sort_keys=True) + '\n' for l in sorted(logos.values(), key=lambda l: l['file_id']))
    unresolved_text = ''.join(json.dumps(u, ensure_ascii=False, sort_keys=True) + '\n' for u in unresolved)
    public = re.sub(r'"(asset_url|source_page|source_url|website|homepage)": "[^"]*"', '', text + logo_text)
    hit = FORBIDDEN.search(re.sub(r'"forms_pages": \[[^\]]*\]', '', public))
    checks.append({'check': 'no filesystem paths, mailto or phone-shaped strings outside recorded URLs', 'ok': hit is None, 'actual': hit.group(0) if hit else None})
    checks.append({'check': 'ids unique and well formed', 'ok': all(re.fullmatch(r'[A-Za-z0-9_.\-]{1,120}', c['id']) for c in order)})
    checks.append({'check': 'FIPS are 5-char strings', 'ok': all(re.fullmatch(r'\d{5}', l['county_fips']) for c in order for l in c['locations'])
                   and all(c['county_fips'] is None or re.fullmatch(r'\d{5}', c['county_fips']) for c in order)})
    checks.append({'check': 'every logo court id exists', 'ok': all(i in courts for l in logos.values() for i in l['court_ids'])})
    (OUT / 'courts.jsonl').write_text(text, encoding='utf-8', newline='\n')
    (OUT / 'logos.jsonl').write_text(logo_text, encoding='utf-8', newline='\n')
    (OUT / 'unresolved.jsonl').write_text(unresolved_text, encoding='utf-8', newline='\n')
    wanted = {'%s.%s' % (l['sha256'], RASTER[l['mime']]) for l in logos.values()}
    stray = sorted(p.name for p in (OUT / 'assets').iterdir() if p.name not in wanted)
    checks.append({'check': 'assets folder holds only registered marks', 'ok': not stray, 'actual': stray[:5]})

    kinds_of = collections.Counter(c['id_kind'] for c in order)
    counts = {
        'courts_total': len(order), 'courtlistener_courts': kinds_of['courtlistener'], 'local_registry_only_entries': kinds_of['local_registry'],
        'county_registry_courts': kinds_of['county_registry'], 'county_registry_listings': listed,
        'county_registry_locations': sum(len(c['locations']) for c in order),
        'registry_keys': len(key_to_court), 'crosswalk': dict(kinds),
        'courts_with_state': sum(1 for c in order if c['state']), 'courtlistener_courts_with_state': sum(1 for c in order if c['state'] and c['id_kind'] == 'courtlistener'),
        'courts_with_logo': sum(1 for c in order if c['has_logo']), 'logo_files': len(logos), 'shared_logo_files': sum(1 for l in logos.values() if l['shared_mark']),
        'logo_bytes': sum(l['bytes'] for l in logos.values()), 'logos_skipped': dict(skipped),
        'courts_with_pending_mdls': sum(1 for c in order if c['pending_mdl_count']), 'pending_mdls_joined': pending_total,
        'by_system': dict(collections.Counter(c['system'] for c in order)), 'unresolved': len(unresolved),
    }
    ok = all(c['ok'] for c in checks)
    validation = {
        'schema_version': '1', 'status': 'passed' if ok else 'failed', 'ready': ok,
        'validated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'data_files': [{'path': n, 'sha256': sha(OUT / n), 'rows': r} for n, r in
                       (('courts.jsonl', len(order)), ('logos.jsonl', len(logos)), ('unresolved.jsonl', len(unresolved)))],
        'counts': counts, 'checks': checks,
        'qualification': ('Court spine built offline on 2026-09-19 from the CourtListener bulk courts table (snapshot %s), the local court registry and '
                          'identity marks retrieved 2026-09-12, the Kentucky/Texas county registry (as of 2026-08-22) and the JPML MDL report dated 2026-09-01. '
                          'It is not a census of current courts, court addresses are not recorded, and image reuse permission is not established.') % snapshot,
        'license_ref': 'CourtListener bulk data (Free Law Project, public domain dedication as published); court identity marks: permission_status not_established; county registry and JPML facts from official public pages.',
        'inputs': [{'path': p.relative_to(ROOT).as_posix(), 'sha256': sha(p)} for p in (CATALOG, LINKS, ASSOC, IMAGES, REGISTRIES, COUNTIES, MDLS)],
    }
    (OUT / 'validation.json').write_text(json.dumps(validation, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'failed': [c for c in checks if not c['ok']]}, indent=1))


if __name__ == '__main__':
    main()
