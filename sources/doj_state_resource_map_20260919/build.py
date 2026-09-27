"""Offline build of the DOJ JMD state legal resource map from saved captures.

No network. Inputs are read-only:
  * catalog/documents.sqlite3 (mode=ro): the 57 saved justice.gov/jmd/ls captures (index + 56 pages);
    every raw file is re-hashed and must equal versions.raw_sha256.
  * the scout's saved copy of /jmd/ls/federal (decoded bytes + receipt hash) when it exists and its
    hash equals the receipt; otherwise the federal page and circuits are simply absent.
  * sources/public_law_directory_20260919/catalog.json: only to mark which published links already
    exist in the 9,348-reference source directory. The directory is never changed.

All page text is treated as data. Labels are kept as published (whitespace collapsed only).
"""
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from lxml import html

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOCUMENTS_DB = ROOT / 'catalog/documents.sqlite3'
DIRECTORY = ROOT / 'sources/public_law_directory_20260919/catalog.json'
SCOUT = ROOT / 'reports/corpus_upgrade_20260919/understand/doj_findlaw_scout_evidence'
FEDERAL_CAPTURE = SCOUT / 'justice_jmd_ls_federal.html'
FEDERAL_RECEIPTS = SCOUT / 'receipts_2.json'
FEDERAL_URL = 'https://www.justice.gov/jmd/ls/federal'
INDEX_URL = 'https://www.justice.gov/jmd/ls/state'
PUBLISHER = 'U.S. Department of Justice, Justice Management Division, Library Staff'

# Published H1 label -> (USPS, state FIPS, class). Reference table; the label itself is the evidence.
JURISDICTIONS = {
    'Alabama': ('AL', '01'), 'Alaska': ('AK', '02'), 'Arizona': ('AZ', '04'), 'Arkansas': ('AR', '05'),
    'California': ('CA', '06'), 'Colorado': ('CO', '08'), 'Connecticut': ('CT', '09'), 'Delaware': ('DE', '10'),
    'District of Columbia': ('DC', '11'), 'Florida': ('FL', '12'), 'Georgia': ('GA', '13'), 'Hawaii': ('HI', '15'),
    'Idaho': ('ID', '16'), 'Illinois': ('IL', '17'), 'Indiana': ('IN', '18'), 'Iowa': ('IA', '19'),
    'Kansas': ('KS', '20'), 'Kentucky': ('KY', '21'), 'Louisiana': ('LA', '22'), 'Maine': ('ME', '23'),
    'Maryland': ('MD', '24'), 'Massachusetts': ('MA', '25'), 'Michigan': ('MI', '26'), 'Minnesota': ('MN', '27'),
    'Mississippi': ('MS', '28'), 'Missouri': ('MO', '29'), 'Montana': ('MT', '30'), 'Nebraska': ('NE', '31'),
    'Nevada': ('NV', '32'), 'New Hampshire': ('NH', '33'), 'New Jersey': ('NJ', '34'), 'New Mexico': ('NM', '35'),
    'New York': ('NY', '36'), 'North Carolina': ('NC', '37'), 'North Dakota': ('ND', '38'), 'Ohio': ('OH', '39'),
    'Oklahoma': ('OK', '40'), 'Oregon': ('OR', '41'), 'Pennsylvania': ('PA', '42'), 'Rhode Island': ('RI', '44'),
    'South Carolina': ('SC', '45'), 'South Dakota': ('SD', '46'), 'Tennessee': ('TN', '47'), 'Texas': ('TX', '48'),
    'Utah': ('UT', '49'), 'Vermont': ('VT', '50'), 'Virginia': ('VA', '51'), 'Washington': ('WA', '53'),
    'West Virginia': ('WV', '54'), 'Wisconsin': ('WI', '55'), 'Wyoming': ('WY', '56'),
    'American Samoa': ('AS', '60'), 'Guam': ('GU', '66'), 'Northern Mariana Islands': ('MP', '69'),
    'Puerto Rico': ('PR', '72'), 'Virgin Islands': ('VI', '78'),
}
TERRITORIES = {'AS', 'GU', 'MP', 'PR', 'VI'}

# DOJ published label -> proposed controlled value. The published label is never overwritten.
H2_NORM = {
    'u.s. appellate courts': 'courts_directory', 'u.s. district courts': 'courts_directory',
    'u.s. bankruptcy courts': 'courts_directory', 'u.s. district and bankruptcy courts': 'courts_directory',
    'state and local courts': 'courts_directory', 'local courts': 'courts_directory',
    'legal ethics and attorney regulation': 'attorney_admission_discipline',
    'legislature and laws': 'legislative_materials',
    'state executive and regulatory information': 'executive_regulatory',
    'executive and regulatory information': 'executive_regulatory',
    'local government': 'local_government', 'state and local': 'local_government',
    'state agencies & offices': 'state_agencies', 'agencies & offices': 'state_agencies',
    'general resources': 'general_reference',
}
H3_NORM = {
    'laws, codes and statutes': 'statutes_codes', 'legislative materials': 'legislative_materials',
    'regulations': 'regulations_register', 'office of the attorney general': 'attorney_general',
    "governor's office": 'executive_regulatory', "mayor's office": 'executive_regulatory',
    'county & municipal information': 'local_government', 'municipal codes': 'municipal_codes',
    'criminal information': 'criminal_records_corrections',
}
MONTHS = {m: i for i, m in enumerate(('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                                      'September', 'October', 'November', 'December'), 1)}
SHARE_HOSTS = ('facebook.com', 'twitter.com', 'x.com', 'linkedin.com')
ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
            'ninth': 9, 'tenth': 10, 'eleventh': 11}


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def clean(text):
    """Whitespace-only normalisation (NBSP and runs of whitespace become one space)."""
    return re.sub(r'\s+', ' ', (text or '').replace('\xa0', ' ')).strip()


def normalize_url(url, fold_case=False, drop_fragment=False):
    """Scheme-, www.- and trailing-slash-insensitive form. Path case is kept unless fold_case."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f'{host}:{parts.port}'
    path = parts.path.rstrip('/')
    fragment = '' if drop_fragment else parts.fragment
    tail = path + (('?' + parts.query) if parts.query else '') + (('#' + fragment) if fragment else '')
    return host + (tail.casefold() if fold_case else tail)


def section_norm(h2, h3, link_text=''):
    """Proposed controlled category for a published label; (value, basis) or (None, None)."""
    if not h2:
        return None, None
    key2, key3 = h2.casefold(), (h3 or '').casefold()
    if key3 == 'court forms and administration':
        text = link_text.casefold()
        if re.search(r'\bforms?\b', text):
            return 'court_forms', 'published sub-label plus link text containing "form"'
        if re.search(r'\brules?\b', text):
            return 'court_rules', 'published sub-label plus link text containing "rule"'
        return 'courts_directory', 'published section label'
    if key3 in H3_NORM:
        return H3_NORM[key3], 'published sub-label'
    if key2 in H2_NORM:
        return H2_NORM[key2], 'published section label'
    return None, None


def updated_label(text):
    match = re.search(r'Updated\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})', text)
    if not match or match.group(1) not in MONTHS:
        return None, None
    label = f'{match.group(1)} {int(match.group(2))}, {match.group(3)}'
    return label, f'{int(match.group(3)):04d}-{MONTHS[match.group(1)]:02d}-{int(match.group(2)):02d}'


def parse_page(payload, page_url):
    """Scout parser rules: first <article> (fallback <main>), document order, h2 resets h3, links only after
    the first h2; '#' anchors, mailto: and share links dropped; relative hrefs resolved against page_url."""
    doc = html.fromstring(payload)
    roots = doc.xpath('//article') or doc.xpath('//main')
    if not roots:
        raise ValueError(f'No article/main element in {page_url}')
    root = roots[0]
    h1 = clean(' '.join(x.text_content() for x in doc.xpath('//h1')[:1]))
    label, iso = updated_label(clean(root.text_content()))
    h2 = h3 = None
    links, per_section = [], {}
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag == 'h2':
            h2, h3 = clean(node.text_content()) or None, None
        elif node.tag == 'h3':
            text = clean(node.text_content())
            if text:
                h3 = text
        elif node.tag == 'a' and h2 and node.get('href'):
            href = node.get('href').strip()
            if not href or href.startswith('#') or href.lower().startswith(('mailto:', 'javascript:', 'tel:')):
                continue
            url = urljoin(page_url, href)
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname:
                continue
            host = parts.hostname.lower()
            if any(host == s or host.endswith('.' + s) for s in SHARE_HOSTS) and 'share' in url.lower():
                continue
            if url.split('#')[0] == page_url and parts.fragment:
                continue
            holder = node.getparent()
            while holder is not None and holder.tag not in ('li', 'p', 'td', 'div'):
                holder = holder.getparent()
            evidence = clean(holder.text_content() if holder is not None else node.text_content())[:240]
            key = (h2, h3)
            per_section[key] = per_section.get(key, 0) + 1
            links.append({'section_h2': h2, 'section_h3': h3, 'link_text': clean(node.text_content()), 'url': url,
                          'host': host, 'scheme': parts.scheme, 'evidence_line': evidence,
                          'position': len(links) + 1, 'position_in_section': per_section[key]})
    return {'h1': h1, 'page_updated_label': label, 'page_updated_date': iso, 'links': links}


def circuit_key(label):
    """'11th Circuit' / 'Eleventh Circuit' -> '11'; DC and Federal handled; None when unreadable."""
    text = label.casefold()
    if 'federal circuit' in text:
        return 'federal'
    if 'district of columbia' in text or re.search(r'\bd\.?c\.?\b', text):
        return 'dc'
    match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)\b', text)
    if match:
        return str(int(match.group(1)))
    for word, number in ORDINALS.items():
        if re.search(rf'\b{word}\b', text):
            return str(number)
    return None


def saved_versions():
    con = sqlite3.connect(f'file:{DOCUMENTS_DB.as_posix()}?mode=ro', uri=True)
    try:
        rows = con.execute(
            "select version_id, record_key, source_url, final_url, raw_path, raw_sha256, raw_bytes, retrieved_at, "
            "retrieval_time_basis, extraction_status, capture_kind from versions where collection='official_courts' "
            "and (source_url like 'https://www.justice.gov/jmd/ls%' or final_url like 'https://www.justice.gov/jmd/ls%')"
        ).fetchall()
    finally:
        con.close()
    latest = {}
    for row in rows:
        item = dict(zip(('version_id', 'record_key', 'source_url', 'final_url', 'raw_path', 'raw_sha256', 'raw_bytes',
                         'retrieved_at', 'retrieval_time_basis', 'extraction_status', 'capture_kind'), row))
        if item['record_key'] not in latest or (item['retrieved_at'] or '') > (latest[item['record_key']]['retrieved_at'] or ''):
            latest[item['record_key']] = item
    return sorted(latest.values(), key=lambda item: item['final_url'] or item['source_url'])


def federal_capture():
    """The scout copy is used only when the file and a receipt with the same SHA-256 both exist."""
    if not FEDERAL_CAPTURE.is_file() or not FEDERAL_RECEIPTS.is_file():
        return None
    payload = FEDERAL_CAPTURE.read_bytes()
    digest = sha256_bytes(payload)
    for receipt in json.loads(FEDERAL_RECEIPTS.read_text(encoding='utf-8')):
        if receipt.get('url') == FEDERAL_URL and receipt.get('sha256') == digest and str(receipt.get('curl_out', '')).startswith('200 '):
            return {'payload': payload, 'sha256': digest, 'captured_at': receipt['requested_at']}
    return None


def temporal(captured_at, captured_basis, updated_date):
    return {'captured_at': captured_at, 'captured_at_basis': captured_basis,
            'source_as_of': updated_date,
            'source_as_of_basis': ("publisher in-page 'Updated <date>' footer of the DOJ page; page-level, not the date "
                                   'this link was added or checked') if updated_date else None,
            'published_at': None, 'published_at_basis': None, 'effective_from': None, 'effective_from_basis': None,
            'effective_to': None, 'effective_to_basis': None}


def directory_index():
    payload = DIRECTORY.read_bytes()
    entries = json.loads(payload)['entries']
    exact, normal, folded, bare = {}, {}, {}, {}
    for entry in entries:
        exact.setdefault(entry['url'], []).append(entry['id'])
        normal.setdefault(normalize_url(entry['url']), []).append(entry['id'])
        folded.setdefault(normalize_url(entry['url'], True), []).append(entry['id'])
        bare.setdefault(normalize_url(entry['url'], True, True), []).append(entry['id'])
    return sha256_bytes(payload), len(entries), (exact, normal, folded, bare)


def match_directory(url, tables):
    exact, normal, folded, bare = tables
    if url in exact:
        return exact[url], 'exact'
    key = normalize_url(url)
    if key in normal:
        return normal[key], 'normalized'
    key = normalize_url(url, True)
    if key in folded:
        return folded[key], 'normalized_case_insensitive'
    key = normalize_url(url, True, True)
    if key in bare:
        return bare[key], 'normalized_fragment_ignored'
    return [], 'none'


def write_jsonl(path, rows):
    payload = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows).encode('utf-8')
    path.write_bytes(payload)
    return {'path': path.name, 'sha256': sha256_bytes(payload), 'rows': len(rows)}


def write_json(path, value, rows):
    payload = (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + '\n').encode('utf-8')
    path.write_bytes(payload)
    return {'path': path.name, 'sha256': sha256_bytes(payload), 'rows': rows}


def build():
    checks, inputs, pages, resources, unresolved = [], [], [], [], []
    catalog_sha, directory_total, tables = directory_index()
    inputs.append({'path': DIRECTORY.relative_to(ROOT).as_posix(), 'sha256': catalog_sha})
    versions = saved_versions()
    legacy_to_page, index_labels = {}, {}
    parsed = []
    for version in versions:
        raw = ROOT / version['raw_path']
        payload = raw.read_bytes()
        digest = sha256_bytes(payload)
        if digest != version['raw_sha256'] or len(payload) != version['raw_bytes']:
            raise SystemExit(f"Raw capture hash mismatch: {version['raw_path']}")
        inputs.append({'path': version['raw_path'], 'sha256': digest})
        page_url = version['final_url'] or version['source_url']
        legacy_to_page[version['source_url']] = page_url
        legacy_to_page[page_url] = page_url
        parsed.append((version, page_url, parse_page(payload, page_url)))
    federal = federal_capture()
    if federal:
        inputs.append({'path': FEDERAL_CAPTURE.relative_to(ROOT).as_posix(), 'sha256': federal['sha256']})
        inputs.append({'path': FEDERAL_RECEIPTS.relative_to(ROOT).as_posix(), 'sha256': sha256_bytes(FEDERAL_RECEIPTS.read_bytes())})
        version = {'version_id': None, 'source_url': FEDERAL_URL, 'final_url': FEDERAL_URL, 'raw_sha256': federal['sha256'],
                   'retrieved_at': federal['captured_at'], 'capture_kind': 'scout_capture_decoded_bytes'}
        parsed.append((version, FEDERAL_URL, parse_page(federal['payload'], FEDERAL_URL)))

    for version, page_url, page in parsed:
        if page_url == INDEX_URL:
            for link in page['links']:
                index_labels[link['url']] = link['link_text']
    state_pages = 0
    for version, page_url, page in parsed:
        is_index, is_federal = page_url == INDEX_URL, page_url == FEDERAL_URL
        usps = fips = jclass = None
        if is_index:
            kind = 'state_index'
        elif is_federal:
            kind, jclass = 'federal_resource_page', 'federal'
        else:
            kind = 'state_resource_page'
            if page['h1'] not in JURISDICTIONS:
                raise SystemExit(f"Unrecognised published jurisdiction label {page['h1']!r} on {page_url}")
            usps, fips = JURISDICTIONS[page['h1']]
            jclass = 'territory' if usps in TERRITORIES else 'federal_district' if usps == 'DC' else 'state'
            state_pages += 1
        catalog_capture = version['version_id'] is not None
        captured_basis = ('catalog/documents.sqlite3 versions.retrieved_at (retrieval_time_basis=source_reported)'
                          if catalog_capture else 'scout receipt requested_at; saved body is decoded bytes, not wire bytes')
        missing = 0
        rows = []
        for link in page['links']:
            internal = link['host'].endswith('justice.gov')
            ids, basis = match_directory(link['url'], tables)
            not_in = None if (internal and not ids) else not ids
            missing += 1 if not_in else 0
            norm, norm_basis = section_norm(link['section_h2'], link['section_h3'], link['link_text'])
            path = link['section_h2'] + (' > ' + link['section_h3'] if link['section_h3'] else '')
            identity = '|'.join((page_url, path, link['url'], str(link['position'])))
            rows.append({
                'record_id': 'slrm-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12],
                'page_kind': kind, 'jurisdiction_label': page['h1'] if not is_federal else 'Federal',
                'page_title_as_published': page['h1'], 'usps': usps, 'state_fips': fips, 'jurisdiction_class': jclass,
                'jurisdiction_basis': 'DOJ page H1 as published; never inferred from the outbound host',
                'directory_publisher': PUBLISHER, 'page_url': page_url, 'legacy_page_url': version['source_url'],
                'section_h2': link['section_h2'], 'section_h3': link['section_h3'], 'section_path': path,
                'section_norm': norm, 'section_norm_basis': norm_basis,
                'position': link['position'], 'position_in_section': link['position_in_section'],
                'link_text': link['link_text'], 'url': link['url'], 'url_normalized': normalize_url(link['url']),
                'host': link['host'], 'scheme': link['scheme'], 'evidence_line': link['evidence_line'],
                'doj_internal_link': internal,
                'page_updated_label': page['page_updated_label'], 'page_updated_date': page['page_updated_date'],
                'capture_version_id': version['version_id'], 'capture_raw_sha256': version['raw_sha256'],
                'capture_kind': 'catalog_version_original_bytes' if catalog_capture else 'scout_capture_decoded_bytes',
                'captured_at': version['retrieved_at'],
                'directory_ref_ids': ids, 'directory_match_basis': basis, 'not_in_source_directory': not_in,
                'link_status': 'not_checked',
                **{k: v for k, v in temporal(version['retrieved_at'], captured_basis, page['page_updated_date']).items() if k != 'captured_at'},
            })
        if not is_index:
            resources.extend(rows)
        pages.append({
            'page_url': page_url, 'legacy_page_url': version['source_url'], 'page_kind': kind,
            'page_title_as_published': page['h1'], 'index_link_text_as_published': index_labels.get(version['source_url']),
            'usps': usps, 'state_fips': fips, 'jurisdiction_class': jclass,
            'capture_version_id': version['version_id'], 'capture_raw_sha256': version['raw_sha256'],
            'capture_kind': 'catalog_version_original_bytes' if catalog_capture else 'scout_capture_decoded_bytes',
            'page_updated_label': page['page_updated_label'], 'page_updated_date': page['page_updated_date'],
            'link_count': len(rows), 'unique_url_count': len({r['url'] for r in rows}),
            'section_count': len({(r['section_h2'], r['section_h3']) for r in rows}),
            'not_in_source_directory_count': missing,
            **temporal(version['retrieved_at'], captured_basis, page['page_updated_date']),
        })

    # Circuits: the federal page publishes "<circuit> ... Serving <state page links>"; state pages publish
    # their own circuit label under "U.S. Appellate Courts". Both are recorded; disagreement is unresolved.
    page_by_url = {p['page_url']: p for p in pages}
    state_claims = {}
    for row in resources:
        if row['page_kind'] == 'state_resource_page' and row['section_h2'] == 'U.S. Appellate Courts' and row['doj_internal_link']:
            key = circuit_key(row['link_text'])
            if key:
                state_claims.setdefault(row['usps'], []).append({'label': row['link_text'], 'key': key, 'record_id': row['record_id']})
    circuits, edges = [], []
    if federal:
        by_label = {}
        for row in resources:
            if row['page_kind'] != 'federal_resource_page' or not row['section_h3'] or not re.search(r'Circuit$', row['section_h3']):
                continue
            entry = by_label.setdefault(row['section_h3'], {'circuit_label': row['section_h3'], 'key': circuit_key(row['section_h3']),
                                                           'court_link_text': None, 'court_url': None, 'cl_court_id': None, 'states': []})
            target = legacy_to_page.get(row['url'])
            if row['doj_internal_link'] and target in page_by_url and page_by_url[target]['usps']:
                state = page_by_url[target]
                claims = state_claims.get(state['usps'], [])
                entry['states'].append({'usps': state['usps'], 'label_as_published': row['link_text'],
                                        'state_page_url': target, 'federal_page_record_id': row['record_id'],
                                        'state_page_circuit_labels': [c['label'] for c in claims],
                                        'state_page_agrees': any(c['key'] == entry['key'] for c in claims) if claims else None})
            elif not row['doj_internal_link'] and entry['court_url'] is None:
                match = re.fullmatch(r'(?:www\.)?(ca(?:\d{1,2}|dc|fc))\.uscourts\.gov', row['host'])
                if match:
                    entry.update(court_link_text=row['link_text'], court_url=row['url'], cl_court_id=match.group(1))
        for entry in by_label.values():
            entry['cl_court_id_basis'] = ("host label of the court's own site as linked by DOJ under this heading; CourtListener uses "
                                          'the same identifier; not re-checked against the CourtListener API') if entry['cl_court_id'] else None
            circuits.append(entry)
            for state in entry['states']:
                if state['state_page_agrees'] is False:
                    unresolved.append({'kind': 'state_circuit_disagreement', 'usps': state['usps'], 'federal_page_circuit': entry['circuit_label'],
                                       'state_page_circuit_labels': state['state_page_circuit_labels'],
                                       'reason': 'The federal page and the state page name different circuits; no edge emitted.'})
                    continue
                if not entry['cl_court_id']:
                    unresolved.append({'kind': 'circuit_without_court_id', 'usps': state['usps'], 'federal_page_circuit': entry['circuit_label'],
                                       'reason': 'No caN.uscourts.gov link under the circuit heading.'})
                    continue
                edges.append({'from': {'type': 'state', 'id': f"state:{state['usps']}"}, 'to': {'type': 'cl_court', 'id': f"cl_court:{entry['cl_court_id']}"},
                              'relation': 'served_by_federal_circuit',
                              'basis': "DOJ JMD 'Guide to Federal Court Resources' lists the state page under this circuit heading ('Serving ...')",
                              'evidence': {'page_url': FEDERAL_URL, 'circuit_label_as_published': entry['circuit_label'],
                                           'state_label_as_published': state['label_as_published'], 'record_id': state['federal_page_record_id'],
                                           'capture_raw_sha256': federal['sha256'], 'capture_kind': 'scout_capture_decoded_bytes',
                                           'state_page_circuit_labels': state['state_page_circuit_labels'], 'state_page_agrees': state['state_page_agrees']}})
        listed = {s['usps'] for c in circuits for s in c['states']}
        for usps, claims in sorted(state_claims.items()):
            if usps not in listed:
                unresolved.append({'kind': 'state_page_circuit_not_on_federal_page', 'usps': usps, 'state_page_circuit_labels': [c['label'] for c in claims],
                                   'reason': 'The state page names a circuit but the federal page does not list this jurisdiction; no edge emitted.'})
    # source -> state edges: one per (directory reference, jurisdiction) with the strongest listing as evidence.
    rank = {'exact': 0, 'normalized': 1, 'normalized_case_insensitive': 2, 'normalized_fragment_ignored': 3}
    best = {}
    for row in resources:
        if row['page_kind'] != 'state_resource_page':
            continue
        for ref in row['directory_ref_ids']:
            key = (ref, row['usps'])
            if key not in best or (rank[row['directory_match_basis']], row['position']) < (rank[best[key]['directory_match_basis']], best[key]['position']):
                best[key] = row
    for (ref, usps), row in sorted(best.items()):
        edges.append({'from': {'type': 'source', 'id': f'source:{ref}'}, 'to': {'type': 'state', 'id': f'state:{usps}'},
                      'relation': 'listed_on_doj_state_resource_page',
                      'basis': f"DOJ JMD state page lists this URL ({row['directory_match_basis']} URL match); a listing, not a claim that the publisher belongs to the state",
                      'evidence': {'page_url': row['page_url'], 'section_path': row['section_path'], 'link_text': row['link_text'],
                                   'record_id': row['record_id'], 'capture_version_id': row['capture_version_id'],
                                   'capture_raw_sha256': row['capture_raw_sha256'], 'captured_at': row['captured_at']}})
    for row in resources:
        if row['not_in_source_directory']:
            unresolved.append({'kind': 'doj_url_not_in_source_directory', 'record_id': row['record_id'], 'usps': row['usps'], 'url': row['url'],
                               'reason': 'No exact or normalized URL match among the 9,348 directory references; published only in this map.'})

    state_rows = [r for r in resources if r['page_kind'] == 'state_resource_page']
    external = [r for r in state_rows if not r['doj_internal_link']]
    unique_external = {r['url'] for r in external}
    missing_urls = {r['url'] for r in external if r['not_in_source_directory']}
    matched_refs = {ref for r in state_rows for ref in r['directory_ref_ids']}
    counts = {
        'saved_catalog_captures': len(versions), 'state_and_territory_pages': state_pages,
        'federal_page_included': bool(federal), 'pages': len(pages), 'resources': len(resources),
        'state_page_links': len(state_rows), 'state_page_unique_urls': len({r['url'] for r in state_rows}),
        'state_page_external_unique_urls': len(unique_external),
        'state_page_hosts': len({r['host'] for r in state_rows}),
        'federal_page_links': sum(r['page_kind'] == 'federal_resource_page' for r in resources),
        'external_unique_urls_matched_exact': len({r['url'] for r in external if r['directory_match_basis'] == 'exact'}),
        'external_unique_urls_matched_any': len(unique_external - missing_urls),
        'external_unique_urls_not_in_source_directory': len(missing_urls),
        'rows_not_in_source_directory': sum(bool(r['not_in_source_directory']) for r in resources),
        'directory_references_matched_from_state_pages': len(matched_refs),
        'source_directory_references': directory_total,
        'pages_with_updated_label': sum(bool(p['page_updated_label']) for p in pages),
        'circuits': len(circuits), 'circuit_state_memberships': sum(len(c['states']) for c in circuits),
        'edges': len(edges), 'edges_source_to_state': sum(e['relation'] == 'listed_on_doj_state_resource_page' for e in edges),
        'edges_state_to_circuit': sum(e['relation'] == 'served_by_federal_circuit' for e in edges),
        'unresolved': len(unresolved),
    }
    ids = [r['record_id'] for r in resources]
    checks = [
        {'name': 'all_saved_raw_files_rehashed_equal_catalog', 'passed': True, 'detail': f'{len(versions)} files'},
        {'name': 'expected_57_saved_captures', 'passed': len(versions) == 57},
        {'name': 'expected_56_jurisdiction_pages', 'passed': state_pages == 56 and len({p['usps'] for p in pages if p['usps']}) == 56},
        {'name': 'record_ids_unique', 'passed': len(ids) == len(set(ids))},
        {'name': 'every_row_has_published_section_and_capture_hash', 'passed': all(r['section_h2'] and r['capture_raw_sha256'] and r['captured_at'] for r in resources)},
        {'name': 'source_directory_count_unchanged', 'passed': directory_total == 9348},
        {'name': 'federal_capture_hash_equals_receipt', 'passed': bool(federal), 'detail': 'scout copy; decoded bytes' if federal else 'no saved capture; federal page omitted'},
        {'name': 'circuit_disagreements_are_unresolved_not_edges', 'passed': True,
         'detail': f"{sum(u['kind'] == 'state_circuit_disagreement' for u in unresolved)} publisher inconsistencies recorded in unresolved.jsonl"},
    ]
    data_files = [write_jsonl(HERE / 'resources.jsonl', resources), write_jsonl(HERE / 'pages.jsonl', pages),
                  write_json(HERE / 'circuits.json', {
                      'available': bool(federal),
                      'basis': "Circuit headings and 'Serving' lists as published on the DOJ JMD federal page; state pages' own circuit labels are a cross-check",
                      'page_url': FEDERAL_URL if federal else None, 'capture_raw_sha256': federal['sha256'] if federal else None,
                      'capture_kind': 'scout_capture_decoded_bytes' if federal else None, 'captured_at': federal['captured_at'] if federal else None,
                      'page_updated_label': page_by_url[FEDERAL_URL]['page_updated_label'] if federal else None,
                      'circuits': circuits}, len(circuits)),
                  write_jsonl(HERE / 'edges.jsonl', edges), write_jsonl(HERE / 'unresolved.jsonl', unresolved)]
    hard = [c for c in checks if c['name'] != 'federal_capture_hash_equals_receipt']
    passed = all(c['passed'] for c in hard)
    validation = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': passed,
        'validated_at': datetime.now(timezone.utc).isoformat(), 'data_files': data_files, 'counts': counts, 'checks': checks,
        'qualification': ('Links as published by the DOJ JMD Library Staff on pages saved 2026-09-13 (federal page: scout copy of '
                          '2026-09-19, decoded bytes). Section labels are verbatim; section_norm is a proposed mapping. No outbound link '
                          'was requested: link_status is not_checked for every row. The page Updated date is page-level. A listing on a '
                          'state page is not a claim that the publisher belongs to that state.'),
        'license_ref': 'U.S. Government work (justice.gov page content); linked sites carry their own terms and are referenced by URL only.',
        'inputs': inputs,
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return validation


if __name__ == '__main__':
    result = build()
    print(json.dumps({'status': result['status'], 'counts': result['counts'],
                      'failed_checks': [c for c in result['checks'] if not c['passed']]}, indent=1))
    sys.exit(0 if result['ready'] else 1)
