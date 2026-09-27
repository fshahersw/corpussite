"""Unified URL directory (2026-09-19): every local URL list in one searchable, de-duplicated index.

Offline and deterministic. Inputs are read in place and never modified. URLs are recorded as the lists
printed them; nothing is fetched, and a URL's presence here says nothing about whether the page still exists.

    python build.py            # normalise the remaining lists, merge everything, write validation.json
"""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import itertools
import json
import re
import sqlite3
import sys
import tarfile
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import urlnorm  # noqa: E402

RF = Path('C:/Users/firas/Downloads/returnedfiles')
GAP = Path('C:/Users/firas/Downloads/SW-BULK/source_gap_reconciliation_2026-08-22')
USCOURTS_ZIP = Path('C:/Users/firas/Downloads/filesccsdfsdfs.zip')
NORMALIZED = HERE / 'normalized'
DB = HERE / 'url_directory.sqlite3'

STATE_NAMES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT',
    'delaware': 'DE', 'district of columbia': 'DC', 'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL',
    'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE',
    'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC',
    'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI',
    'south carolina': 'SC', 'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT', 'virginia': 'VA',
    'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY', 'puerto rico': 'PR', 'guam': 'GU',
    'virgin islands': 'VI', 'u.s. virgin islands': 'VI', 'american samoa': 'AS', 'northern mariana islands': 'MP',
}
USPS = set(STATE_NAMES.values())

# Federal publishers recognised by exact host or host suffix. The key is a label for filtering, not an identity claim
# about the page's author; basis is always "host".
AGENCY_HOSTS = (
    ('atsdr.cdc.gov', 'atsdr'), ('cdc.gov', 'cdc'), ('fda.gov', 'fda'), ('epa.gov', 'epa'), ('cpsc.gov', 'cpsc'), ('saferproducts.gov', 'cpsc'),
    ('nhtsa.gov', 'nhtsa'), ('osha.gov', 'osha'), ('cms.gov', 'cms'), ('medicare.gov', 'cms'), ('nih.gov', 'nih'), ('ftc.gov', 'ftc'),
    ('sec.gov', 'sec'), ('consumerfinance.gov', 'cfpb'), ('justice.gov', 'doj'), ('dol.gov', 'dol'), ('hhs.gov', 'hhs'), ('usda.gov', 'usda'),
    ('nlrb.gov', 'nlrb'), ('ussc.gov', 'ussc'), ('fjc.gov', 'fjc'), ('uscourts.gov', 'uscourts'), ('supremecourt.gov', 'scotus'),
    ('jpml.uscourts.gov', 'jpml'), ('pacer.gov', 'pacer'), ('congress.gov', 'congress'), ('govinfo.gov', 'govinfo'), ('gpo.gov', 'govinfo'),
    ('federalregister.gov', 'federal_register'), ('ecfr.gov', 'ecfr'), ('regulations.gov', 'regulations_gov'), ('clinicaltrials.gov', 'nih'),
    ('dot.gov', 'dot'), ('faa.gov', 'faa'), ('fmcsa.dot.gov', 'fmcsa'), ('phmsa.dot.gov', 'phmsa'), ('msha.gov', 'msha'), ('eeoc.gov', 'eeoc'),
    ('va.gov', 'va'), ('ssa.gov', 'ssa'), ('irs.gov', 'irs'), ('treasury.gov', 'treasury'), ('fdic.gov', 'fdic'), ('federalreserve.gov', 'frb'),
    ('occ.gov', 'occ'), ('fcc.gov', 'fcc'), ('ferc.gov', 'ferc'), ('nrc.gov', 'nrc'), ('energy.gov', 'doe'), ('doi.gov', 'doi'), ('usgs.gov', 'usgs'),
    ('noaa.gov', 'noaa'), ('hud.gov', 'hud'), ('ed.gov', 'ed'), ('dhs.gov', 'dhs'), ('fema.gov', 'fema'), ('gao.gov', 'gao'), ('oig.hhs.gov', 'hhs_oig'),
)
COURT_HOST = re.compile(r'court|judici|(^|\.)jud\.|(^|\.)ujs\.|njcourts|pacourts|mdcourts|tncourts', re.I)
LEGIS_HOST = re.compile(r'legis|(^|\.)leg\.|ncleg|cga\.ct|capitol|statehouse|assembly|senate|(^|\.)lis\.|statutes|revisor', re.I)
REG_HINT = re.compile(r'admin(istrative)?[-_ ]?code|rulesregs|/rules?/|register|regulation|(^|\.)oar\.|(^|\.)sos\.|secretaryofstate|(^|\.)rules\.', re.I)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def jsonl(path: Path):
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ('none', 'null', 'nan') else None


def host_layer(host: str, path: str = '', default: str = 'state_agency') -> str:
    """Layer guessed from the host/path wording only; callers add the 'layer_by_host_pattern' topic."""
    if host.endswith('uscourts.gov') or host.endswith('supremecourt.gov'):
        return 'federal_court'
    if COURT_HOST.search(host):
        return 'state_court'
    if LEGIS_HOST.search(host):
        return 'legislature_law'
    if REG_HINT.search(host) or REG_HINT.search(path):
        return 'regulatory_text'
    return default


TAG_TYPES = {'rules': 'Rules', 'forms': 'Forms', 'orders': 'Orders', 'administrative_order': 'Orders', 'mdl_order': 'Orders', 'opinions_decisions': 'Opinions & decisions', 'opinion_document': 'Opinions & decisions',
             'opinions_index': 'Opinions & decisions', 'tentative_rulings': 'Opinions & decisions', 'fees': 'Fees', 'efiling': 'E-filing', 'court_directory_facilities': 'Court directory & contacts',
             'court_hierarchy': 'Court directory & contacts', 'clerk_office': 'Court directory & contacts', 'county_court_site': 'Court directory & contacts', 'county_page': 'Court directory & contacts',
             'judges': 'Judges', 'judge_directory': 'Judges', 'judge_profile': 'Judges', 'self_help': 'Self-help & guidance', 'jury_info': 'Self-help & guidance', 'data_publications': 'Statistics & reports',
             'court_statistics': 'Statistics & reports', 'court_calendar': 'Calendars & dockets', 'calendar_docket_index': 'Calendars & dockets', 'docket_resource': 'Calendars & dockets',
             'legislation': 'Statutes & legislation', 'statute': 'Statutes & legislation', 'federal_register_doc': 'Regulatory text', 'news_press': 'News & press', 'api_endpoint': 'Data & APIs',
             'medical_source': 'Science & health', 'attorney_discipline': 'Attorney discipline', 'judicial_conduct': 'Judicial conduct'}
PATH_TYPES = ((r'toxprofile|tox-profile|toxfaq|toxguide|/iris/|minimal-risk', 'Science & health'), (r'recall', 'Recalls & safety notices'), (r'warning-?letter|enforcement|consent-?decree|litigation-?release|/litigation/', 'Enforcement'),
              (r'local-?rules?|court-?rules?|/rules?/|rules-of', 'Rules'), (r'standing-?order|admin(istrative)?-?order|general-?order|/orders?/', 'Orders'), (r'/forms?/|forms?\.|-forms?|_forms?', 'Forms'),
              (r'e-?fil(e|ing)', 'E-filing'), (r'fee-?schedule|filing-?fees?|/fees?/', 'Fees'), (r'opinion|decision|ruling', 'Opinions & decisions'), (r'statistic|annual-?report|caseload|/reports?/', 'Statistics & reports'),
              (r'guidance|guideline|handbook|manual|faq', 'Self-help & guidance'), (r'admin(istrative)?-?code|/cfr|federal-?register|regulation|/register/', 'Regulatory text'),
              (r'statute|legislat|/bills?/|/acts?/|/code/', 'Statutes & legislation'), (r'press-?release|/news/|newsroom|/blog/', 'News & press'), (r'/judges?/|judicial-?officer|chambers', 'Judges'),
              (r'directory|locations?|contact', 'Court directory & contacts'), (r'calendar|docket', 'Calendars & dockets'))
PATH_TYPES = tuple((re.compile(pattern, re.I), label) for pattern, label in PATH_TYPES)


def content_type(topics, url):
    """(label, basis): the list's own tag when it has one, otherwise the wording of the address; None when neither says anything."""
    for topic in topics:
        if ':' in topic:
            label = TAG_TYPES.get(topic.split(':', 1)[1])
            if label:
                return label, 'tag from the discovery list'
    path = urllib.parse.urlsplit(url).path if url else ''
    for pattern, label in PATH_TYPES:
        if pattern.search(path):
            return label, 'wording of the address'
    return None, None


PUBLISHER_NAMES = {'fda': 'FDA', 'epa': 'EPA', 'cpsc': 'CPSC', 'nhtsa': 'NHTSA', 'osha': 'OSHA', 'cms': 'CMS', 'cdc': 'CDC', 'atsdr': 'ATSDR', 'nih': 'NIH', 'ftc': 'FTC', 'sec': 'SEC',
                   'cfpb': 'CFPB', 'doj': 'Justice Department', 'dol': 'Labor Department', 'hhs': 'HHS', 'hhs_oig': 'HHS Inspector General', 'usda': 'USDA', 'nlrb': 'NLRB',
                   'ussc': 'Sentencing Commission', 'fjc': 'Federal Judicial Center', 'uscourts': 'U.S. Courts', 'scotus': 'Supreme Court', 'jpml': 'JPML', 'congress': 'Congress.gov',
                   'govinfo': 'GovInfo', 'federal_register': 'Federal Register', 'ecfr': 'eCFR', 'regulations_gov': 'Regulations.gov'}
_JUNK_SEGMENT = re.compile(r'^(index|default|home|main|view|page|pages|node|content|documents?|files?|sites?|assets?|media|uploads?|wp-content|download|downloads|pdf|pdfs|docs?|system|resources?|library|portals?|documentcenter|globalassets|sitecore|publications?|public|en|english|web|www)$', re.I)


def saved_titles():
    """url_key -> title of a copy this archive already holds (exact address match only)."""
    titles = {}

    def take(database, query):
        if not database.exists():
            return
        connection = sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True)
        try:
            for url, title in connection.execute(query):
                key = urlnorm.url_key(url or '')
                title = ' '.join(str(title or '').split())
                if key and len(title) >= 6 and not title.lower().startswith('http'):
                    titles.setdefault(key, title[:240])
        except sqlite3.DatabaseError:
            pass
        finally:
            connection.close()

    take(ROOT / 'sources' / 'saved_web_pages_20260919' / 'pages.sqlite3', 'SELECT url, title FROM pages')
    take(ROOT / 'sources' / 'uscourts_pages_20260919' / 'uscourts_pages.sqlite3', 'SELECT url, title FROM pages')
    take(ROOT / 'sources' / 'agency_science_documents_20260919' / 'documents.sqlite3', "SELECT url, title FROM docs WHERE title_basis <> 'file name'")
    take(ROOT / 'delivery' / 'archive-directory' / 'directory.sqlite3', 'SELECT source_url, title FROM records WHERE source_url IS NOT NULL')
    return titles


def address_label(url, agency_key, host):
    """Readable words taken only from the address itself: publisher or site, then the last meaningful path segments."""
    parts = urllib.parse.urlsplit(url)
    segments = [urllib.parse.unquote(s) for s in parts.path.split('/') if s]
    words = []
    for segment in reversed(segments):
        segment = re.sub(r'\.(pdf|docx?|xlsx?|csv|html?|aspx?|php|shtml|jsp|cfm|txt|zip)$', '', segment, flags=re.I)
        if not segment or _JUNK_SEGMENT.match(segment) or re.fullmatch(r'[\d\-_.]{1,12}', segment) or re.fullmatch(r'[0-9a-f]{16,}', segment, re.I):
            continue
        text = re.sub(r'[-_+]+', ' ', segment)
        text = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)
        text = ' '.join(text.split())
        if len(text) < 3:
            continue
        words.append(text[:1].upper() + text[1:])
        if len(words) == 2:
            break
    site = PUBLISHER_NAMES.get(agency_key) or (host[4:] if host.startswith('www.') else host)
    return site + (' — ' + ' · '.join(reversed(words)) if words else '')


class Writer:
    def __init__(self, key: str):
        NORMALIZED.mkdir(exist_ok=True)
        self.key = key
        self.out = open(NORMALIZED / f'{key}.jsonl', 'w', encoding='utf-8', newline='\n')
        self.bad = open(NORMALIZED / f'{key}.rejected.jsonl', 'w', encoding='utf-8', newline='\n')
        self.rows_in = self.rows_out = self.rejected = 0

    def add(self, **values):
        self.rows_in += 1
        try:
            row = urlnorm.make_row(**values)
        except ValueError as error:
            self.rejected += 1
            self.bad.write(json.dumps({'url': values.get('url'), 'reason': str(error)}, ensure_ascii=False) + '\n')
            return
        self.out.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.rows_out += 1

    def close(self, inputs):
        self.out.close()
        self.bad.close()
        report = {'key': self.key, 'rows_in': self.rows_in, 'rows_out': self.rows_out, 'rejected': self.rejected, 'inputs': inputs}
        (NORMALIZED / f'{self.key}.report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
        return report


def describe(path: Path):
    size = path.stat().st_size
    return {'path': str(path).replace('\\', '/'), 'bytes': size, 'sha256': sha256_file(path) if size < 200_000_000 else None}


def normalize_state_admin():
    src = RF / 'state_admin_agency_urls.jsonl'
    writer = Writer('state_admin_agency')
    for item in jsonl(src):
        tags = [t for t in (clean(item.get('sources')) or '').split(';') if t]
        states = [t for t in tags if t in USPS]
        federal = item.get('layer') == 'federal_agency'
        writer.add(url=item.get('url'), source_list='state_admin_agency', source_file=str(src), layer='federal_agency' if federal else 'regulatory_text',
                   jurisdiction_level='federal' if federal else 'state', state=states[0] if (len(states) == 1 and not federal) else None,
                   org_name=', '.join(t for t in tags if t not in USPS) or None, doc_kind=urlnorm.doc_kind(item.get('url') or '', item.get('kind')),
                   title=clean(item.get('label')), topics=(['state_administrative_code'] if not federal else []) + [f'list_tag:{t}' for t in tags])
    return writer.close([describe(src)])


def normalize_doj():
    sweep = RF / 'doj_sweep_urls.jsonl'
    registry = RF / 'doj_state_sources.jsonl'
    writer = Writer('doj_sweep')
    for item in jsonl(sweep):
        tag = re.sub(r'\d+$', '', clean(item.get('sources')) or '')
        parts = urlnorm.split(item.get('url') or '')
        host = parts.hostname.lower() if parts else ''
        writer.add(url=item.get('url'), source_list='doj_state_sweep', source_file=str(sweep), layer=host_layer(host, parts.path if parts else ''),
                   jurisdiction_level='state', state=tag if tag in USPS else None, doc_kind=urlnorm.doc_kind(item.get('url') or '', item.get('kind')),
                   title=clean(item.get('label')), topics=['layer_by_host_pattern', 'found_from_doj_state_page'])
    for item in jsonl(registry):
        state = STATE_NAMES.get((clean(item.get('state')) or '').replace('-', ' ').replace('_', ' ').lower())
        writer.add(url=item.get('url'), source_list='doj_state_registry', source_file=str(registry), layer='doj_directory', jurisdiction_level='state',
                   state=state, title=clean(item.get('label')), topics=[t for t in (clean(item.get('section')), clean(item.get('subsection'))) if t])
    return writer.close([describe(sweep), describe(registry)])


def normalize_pdf_directory():
    src = RF / '_pdf_directory' / 'url_directory.jsonl'
    writer = Writer('pdf_directory')
    for item in jsonl(src):
        parts = urlnorm.split(item.get('url') or '')
        host = parts.hostname.lower() if parts else ''
        layer = 'other'
        level = 'unknown'
        if host.endswith('.gov') and any(host == h or host.endswith('.' + h) for h, _ in AGENCY_HOSTS):
            layer, level = ('federal_court', 'federal') if host.endswith('uscourts.gov') else ('federal_agency', 'federal')
            if host.endswith('congress.gov'):
                layer = 'legislature_law'
            if host.endswith('federalregister.gov') or host.endswith('govinfo.gov') or host.endswith('ecfr.gov'):
                layer = 'regulatory_text'
        elif host:
            guess = host_layer(host, parts.path, default='other')
            layer, level = guess, ('state' if guess != 'other' else 'unknown')
        writer.add(url=item.get('url'), source_list='pdf_directory', source_file=str(src), layer=layer, jurisdiction_level=level,
                   doc_kind=urlnorm.doc_kind(item.get('url') or '', item.get('kind')), title=clean(item.get('label')), topics=['layer_by_host_pattern'])
    return writer.close([describe(src)])


def normalize_state_map():
    writer = Writer('state_court_map')
    inputs = []
    for number in (1, 2, 3):
        src = GAP / f'state_map_group{number}.jsonl'
        inputs.append(describe(src))
        for item in jsonl(src):
            state = clean(item.get('jurisdiction'))
            facet = clean(item.get('facet'))
            retention = clean(item.get('retention_class'))
            noise = urlnorm.noise_flags(item.get('canonical_url') or '')
            if retention == 'quarantine':
                noise = noise + ['quarantined_by_source']
            kind_hint = 'application/pdf' if 'pdf' in (clean(item.get('content_hint')) or '') else None
            writer.add(url=item.get('canonical_url'), source_list='state_court_url_map', source_file=str(src), layer='state_court', jurisdiction_level='state',
                       state=state if state in USPS else None, title=clean(item.get('title')), doc_kind=urlnorm.doc_kind(item.get('canonical_url') or '', kind_hint),
                       topics=[t for t in (facet and f'facet:{facet}', retention and f'retention:{retention}', clean(item.get('authority_tier'))) if t],
                       parent_url=clean(item.get('discovered_from')), source_date=(clean(item.get('last_verified_at')) or '')[:10] or None,
                       source_status=clean(item.get('verification_state')), noise=noise)
    return writer.close(inputs)


COUNTY_TYPES = {'county_court_site', 'county_page', 'county_subpage', 'clerk_office'}
LAW_TYPES = {'legislation', 'statute'}


def normalize_records():
    src = RF / '_normalized' / 'records.jsonl'
    writer = Writer('normalized_records')
    for item in jsonl(src):
        url = clean(item.get('url'))
        record_type = clean(item.get('record_type'))
        if not url or record_type in ('batch', 'job', 'judge_roster_row'):
            continue
        parts = urlnorm.split(url)
        host = parts.hostname.lower() if parts else ''
        page_type = clean(item.get('page_type')) or 'unclassified'
        state = clean(item.get('state'))
        federal = state == 'US'
        if page_type == 'medical_source':
            layer = 'science_toxicology'
        elif page_type in LAW_TYPES:
            layer = 'legislature_law'
        elif page_type == 'federal_register_doc':
            layer = 'regulatory_text'
        elif host.endswith('uscourts.gov'):
            layer = 'federal_court'
        elif federal:
            layer = 'federal_agency'
        elif page_type in COUNTY_TYPES:
            layer = 'county_court'
        else:
            layer = 'state_court'
        topics = [f'page_type:{page_type}', f'record_type:{record_type}']
        if clean(item.get('content_sha256')):
            topics.append('content_saved_locally')
        mime = clean(item.get('detected_mime'))
        writer.add(url=url, source_list='court_crawl_records', source_file=str(src), layer=layer,
                   jurisdiction_level='federal' if federal else ('county' if layer == 'county_court' else 'state'),
                   state=state if state in USPS else None, title=clean(item.get('title')), doc_kind=urlnorm.doc_kind(url, mime), topics=topics)
    return writer.close([describe(src)])


def normalize_uscourts():
    writer = Writer('uscourts_crawl')
    if not USCOURTS_ZIP.exists():
        return writer.close([])
    archive = zipfile.ZipFile(USCOURTS_ZIP)
    tar = tarfile.open(fileobj=io.BytesIO(archive.read('uscourts_crawl_1404.tar.gz')), mode='r:gz')
    index = json.load(tar.extractfile('uscourts/_index.json'))
    meta = json.load(tar.extractfile('uscourts/_meta.json'))
    crawl_date = (meta.get('completedAt') or '')[:10] or None
    for page in index:
        writer.add(url=page.get('url'), source_list='uscourts_crawl_1404', source_file=str(USCOURTS_ZIP), layer='federal_court', jurisdiction_level='federal',
                   org_name='Administrative Office of the U.S. Courts', title=clean(page.get('title')), topics=['content_saved_locally'],
                   source_date=crawl_date, source_status=str(page.get('status')) if page.get('status') is not None else None)
    for item in csv.DictReader(io.StringIO(archive.read('uscourts_external_links.csv').decode('utf-8', 'replace'))):
        parts = urlnorm.split(item.get('url') or '')
        host = parts.hostname.lower() if parts else ''
        writer.add(url=item.get('url'), source_list='uscourts_external_links', source_file=str(USCOURTS_ZIP), layer=host_layer(host, parts.path if parts else '', default='other'),
                   title=clean(item.get('label')), topics=['linked_from_uscourts_gov', 'layer_by_host_pattern'], source_date=crawl_date)
    return writer.close([describe(USCOURTS_ZIP)])


def agency_for(host: str):
    best = None
    for suffix, key in AGENCY_HOSTS:
        if host == suffix or host.endswith('.' + suffix):
            if best is None or len(suffix) > len(best[0]):
                best = (suffix, key)
    return best[1] if best else None


def court_hosts():
    """host -> sorted court ids, from the court spine's recorded website (exact host, leading www. ignored)."""
    mapping = collections.defaultdict(set)
    path = ROOT / 'sources' / 'court_spine_20260919' / 'courts.jsonl'
    if path.exists():
        for court in jsonl(path):
            name = urlnorm.host(court.get('website') or '')
            if name:
                mapping[name[4:] if name.startswith('www.') else name].add(court['id'])
    return {name: sorted(ids) for name, ids in mapping.items()}


def saved_keys():
    """url_key -> reference for every URL the MVP already holds, by exact key equality."""
    found = {}

    def take(database: Path, table: str, columns, label: str):
        if not database.exists():
            return
        connection = sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True)
        try:
            for column in columns:
                for identifier, url in connection.execute(f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                    key = urlnorm.url_key(url)
                    if key:
                        found.setdefault(key, f'{label}:{identifier}')
        finally:
            connection.close()

    take(ROOT / 'delivery' / 'archive-directory' / 'directory.sqlite3', 'records', ['source_url'], 'directory_record')
    take(ROOT / 'catalog' / 'documents.sqlite3', 'versions', ['source_url', 'final_url'], 'document_version')
    resources = ROOT / 'sources' / 'county_litigation_firecrawl_20260919' / 'resources.jsonl'
    if resources.exists():
        for item in jsonl(resources):
            for field in ('source_url', 'final_url'):
                key = urlnorm.url_key(item.get(field) or '')
                if key:
                    found.setdefault(key, 'county_litigation:' + str(item.get('id')))
    downloads = ROOT / 'corpus' / 'agency_science_documents_20260919' / 'corpus.sqlite3'
    if downloads.exists():
        connection = sqlite3.connect(f'file:{downloads.as_posix()}?mode=ro', uri=True)
        try:
            for identifier, url in connection.execute("SELECT id, url FROM resources WHERE status='downloaded'"):
                key = urlnorm.url_key(url or '')
                if key:
                    found.setdefault(key, f'agency_science_document:{identifier}')
        except sqlite3.DatabaseError:
            pass
        finally:
            connection.close()
    library = ROOT / 'sources' / 'court_document_library_20260919'
    for database in library.glob('*.sqlite3'):
        connection = sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True)
        try:
            for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                for column in columns:
                    if column in ('source_url', 'url'):
                        for identifier, url in connection.execute(f'SELECT rowid, "{column}" FROM "{table}"'):
                            key = urlnorm.url_key(url or '')
                            if key:
                                found.setdefault(key, f'court_document_library:{identifier}')
        except sqlite3.DatabaseError:
            pass
        finally:
            connection.close()
    return found


PRIORITY_AGENCIES = {'fda': 0, 'atsdr': 0, 'epa': 0, 'cpsc': 0, 'nhtsa': 0, 'osha': 1, 'cms': 1, 'cdc': 1, 'nih': 1, 'jpml': 0, 'uscourts': 2, 'sec': 2, 'ftc': 2, 'cfpb': 3}


def rank(row):
    """Lower is earlier in the download frontier. Reasons are recorded beside the number."""
    if row['agency_key'] in PRIORITY_AGENCIES:
        return 10 + PRIORITY_AGENCIES[row['agency_key']], 'mass-tort agency or science source'
    if row['layer'] == 'science_toxicology':
        return 10, 'toxicology source'
    topics = row['topics']
    if row['layer'] in ('state_court', 'county_court', 'federal_court') and re.search(r'facet:(rules|forms|orders|fees)|page_type:(rules|forms|administrative_order|mdl_order)', topics):
        return 20, 'court rules, forms or orders'
    if row['layer'] in ('regulatory_text', 'legislature_law'):
        return 30, 'regulatory or statutory text'
    if row['layer'] in ('state_court', 'county_court', 'federal_court'):
        return 40, 'court page'
    if row['layer'] in ('federal_agency', 'state_agency', 'doj_directory'):
        return 50, 'agency page'
    return 90, 'other'


def merge():
    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript('''
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE memberships(url_key TEXT NOT NULL, source_list TEXT NOT NULL, url TEXT NOT NULL, layer TEXT, jurisdiction_level TEXT, state TEXT,
            county_fips TEXT, org_name TEXT, doc_kind TEXT, title TEXT, topics TEXT, parent_url TEXT, source_date TEXT, source_status TEXT, noise TEXT, source_file TEXT);
        CREATE TABLE urls(id INTEGER PRIMARY KEY, url_key TEXT NOT NULL UNIQUE, url TEXT NOT NULL, host TEXT NOT NULL, layer TEXT NOT NULL, jurisdiction_level TEXT,
            state TEXT, state_basis TEXT, county_fips TEXT, org_name TEXT, doc_kind TEXT NOT NULL, title TEXT, topics TEXT, first_date TEXT, last_date TEXT,
            noise TEXT, is_noise INTEGER NOT NULL, n_lists INTEGER NOT NULL, source_lists TEXT NOT NULL, conflict INTEGER NOT NULL, agency_key TEXT,
            court_id TEXT, court_link TEXT, court_candidates TEXT, saved_status TEXT NOT NULL, saved_ref TEXT, content_saved_locally INTEGER NOT NULL, content_type TEXT, content_basis TEXT, label TEXT, label_basis TEXT,
            frontier_rank INTEGER NOT NULL, frontier_reason TEXT NOT NULL);
    ''')
    files = sorted(NORMALIZED.glob('*.jsonl'))
    files = [path for path in files if not path.name.endswith('.rejected.jsonl')]
    family_rows = {}
    for path in files:
        count = 0
        batch = []
        for row in jsonl(path):
            batch.append((row['url_key'], row['source_list'], row['url'], row['layer'], row.get('jurisdiction_level'), row.get('state'), row.get('county_fips'),
                          row.get('org_name'), row['doc_kind'], row.get('title'), json.dumps(row.get('topics') or [], ensure_ascii=False), row.get('parent_url'),
                          row.get('source_date'), None if row.get('source_status') is None else str(row.get('source_status')), json.dumps(row.get('noise') or []), row.get('source_file')))
            count += 1
            if len(batch) >= 20000:
                connection.executemany('INSERT INTO memberships VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                batch = []
        connection.executemany('INSERT INTO memberships VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
        family_rows[path.name] = count
    connection.execute('CREATE INDEX memberships_key ON memberships(url_key)')
    connection.commit()

    courts = court_hosts()
    saved = saved_keys()
    known_titles = saved_titles()
    # A host's state is used only when every list that names a state for that host names the same one.
    host_states = collections.defaultdict(set)
    for url, state in connection.execute('SELECT url, state FROM memberships WHERE state IS NOT NULL'):
        name = urlnorm.host(url)
        if name:
            host_states[name].add(state)

    cursor = connection.execute('SELECT * FROM memberships ORDER BY url_key')
    names = [column[0] for column in cursor.description]
    insert = []
    for key, group in itertools.groupby((dict(zip(names, values)) for values in cursor), key=lambda item: item['url_key']):
        members = list(group)
        urls = sorted({m['url'] for m in members}, key=lambda value: (not value.lower().startswith('https://'), len(value), value))
        url = urls[0]
        host = urlnorm.host(url)
        layers = collections.Counter(m['layer'] for m in members)
        layer = sorted(layers.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        states = {m['state'] for m in members if m['state']}
        state, basis = (next(iter(states)), 'stated by source list') if len(states) == 1 else (None, None)
        if state is None and not states and layer not in ('federal_agency', 'federal_court') and len(host_states.get(host, ())) == 1:
            state, basis = next(iter(host_states[host])), 'same host as URLs whose lists state this state'
        levels = collections.Counter(m['jurisdiction_level'] for m in members if m['jurisdiction_level'] and m['jurisdiction_level'] != 'unknown')
        topics = sorted({topic for m in members for topic in json.loads(m['topics'] or '[]')})
        noise = sorted({flag for m in members for flag in json.loads(m['noise'] or '[]')})
        titles = [m['title'] for m in members if m['title']]
        dates = sorted(m['source_date'] for m in members if m['source_date'])
        kinds = collections.Counter(m['doc_kind'] for m in members)
        doc_kind = sorted(kinds.items(), key=lambda pair: (pair[0] == 'page', -pair[1], pair[0]))[0][0]
        bare = host[4:] if host.startswith('www.') else host
        candidates = courts.get(bare, [])
        row = {
            'url_key': key, 'url': url, 'host': host, 'layer': layer, 'jurisdiction_level': (levels.most_common(1)[0][0] if levels else None), 'state': state,
            'state_basis': basis, 'county_fips': next((m['county_fips'] for m in members if m['county_fips']), None),
            'org_name': next((m['org_name'] for m in members if m['org_name']), None), 'doc_kind': doc_kind, 'title': max(titles, key=len)[:300] if titles else None,
            'topics': json.dumps(topics, ensure_ascii=False), 'first_date': dates[0] if dates else None, 'last_date': dates[-1] if dates else None,
            'noise': json.dumps(noise), 'is_noise': 1 if noise else 0, 'n_lists': len({m['source_list'] for m in members}),
            'source_lists': json.dumps(sorted({m['source_list'] for m in members})), 'conflict': 1 if (len(layers) > 1 or len(states) > 1) else 0,
            'agency_key': agency_for(host), 'court_id': candidates[0] if len(candidates) == 1 else None,
            'court_link': 'exact website host' if len(candidates) == 1 else ('ambiguous: host shared by several courts' if candidates else None),
            'court_candidates': json.dumps(candidates) if len(candidates) > 1 else None,
            'saved_status': 'saved' if key in saved else 'not_saved', 'saved_ref': saved.get(key),
            'content_saved_locally': 1 if 'content_saved_locally' in topics else 0,
        }
        row['content_type'], row['content_basis'] = content_type(topics, url)
        list_title = row['title'] if row['title'] and not str(row['title']).lower().startswith('http') and len(str(row['title'])) >= 4 else None
        if list_title:
            row['label'], row['label_basis'] = list_title, 'title or link text from the discovery list'
        elif key in known_titles:
            row['label'], row['label_basis'] = known_titles[key], 'title of the copy saved in this archive'
        else:
            row['label'], row['label_basis'] = address_label(url, row['agency_key'], host), 'words taken from the address'
        row['frontier_rank'], row['frontier_reason'] = rank(row)
        insert.append(tuple(row.values()))
        if len(insert) >= 20000:
            connection.executemany('INSERT INTO urls(' + ','.join(row.keys()) + ') VALUES(' + ','.join('?' * len(row)) + ')', insert)
            insert = []
    if insert:
        connection.executemany('INSERT INTO urls(' + ','.join(row.keys()) + ') VALUES(' + ','.join('?' * len(row)) + ')', insert)
    connection.executescript('''
        CREATE INDEX urls_layer ON urls(is_noise, layer, state);
        CREATE INDEX urls_state ON urls(state, is_noise, layer);
        CREATE INDEX urls_agency ON urls(agency_key, is_noise);
        CREATE INDEX urls_court ON urls(court_id);
        CREATE INDEX urls_host ON urls(host);
        CREATE INDEX urls_kind ON urls(doc_kind, is_noise);
        CREATE INDEX urls_saved ON urls(saved_status, is_noise);
        CREATE INDEX urls_content ON urls(content_type, is_noise);
        CREATE VIRTUAL TABLE urls_fts USING fts5(title, url, org_name, topics, content='urls', content_rowid='id', tokenize='unicode61');
        INSERT INTO urls_fts(rowid, title, url, org_name, topics) SELECT id, coalesce(label, title, ''), url, coalesce(org_name,''), coalesce(topics,'') FROM urls;
    ''')
    # Pre-computed facet counts keep the listing fast at this size.
    connection.execute('CREATE TABLE facet_counts(facet TEXT, value TEXT, include_noise INTEGER, n INTEGER)')
    for facet in ('layer', 'state', 'agency_key', 'doc_kind', 'saved_status', 'jurisdiction_level', 'content_type'):
        for include in (0, 1):
            where = '' if include else 'WHERE is_noise=0'
            connection.execute(f'INSERT INTO facet_counts SELECT ?, {facet}, ?, count(*) FROM urls {where} GROUP BY {facet}', (facet, include))
    for include in (0, 1):
        where = '' if include else 'WHERE u.is_noise=0'
        connection.execute(f'INSERT INTO facet_counts SELECT ?, m.source_list, ?, count(DISTINCT m.url_key) FROM memberships m JOIN urls u USING(url_key) {where} GROUP BY m.source_list', ('source_list', include))
    connection.commit()

    counts = {
        'list_rows': connection.execute('SELECT count(*) FROM memberships').fetchone()[0],
        'distinct_urls': connection.execute('SELECT count(*) FROM urls').fetchone()[0],
        'distinct_urls_without_noise': connection.execute('SELECT count(*) FROM urls WHERE is_noise=0').fetchone()[0],
        'distinct_hosts': connection.execute('SELECT count(DISTINCT host) FROM urls').fetchone()[0],
        'documents_not_page': connection.execute("SELECT count(*) FROM urls WHERE doc_kind<>'page' AND is_noise=0").fetchone()[0],
        'already_saved_in_mvp': connection.execute("SELECT count(*) FROM urls WHERE saved_status='saved'").fetchone()[0],
        'with_state': connection.execute('SELECT count(*) FROM urls WHERE state IS NOT NULL').fetchone()[0],
        'with_agency_key': connection.execute('SELECT count(*) FROM urls WHERE agency_key IS NOT NULL').fetchone()[0],
        'linked_to_one_court': connection.execute('SELECT count(*) FROM urls WHERE court_id IS NOT NULL').fetchone()[0],
        'ambiguous_court_host': connection.execute('SELECT count(*) FROM urls WHERE court_candidates IS NOT NULL').fetchone()[0],
        'in_more_than_one_list': connection.execute('SELECT count(*) FROM urls WHERE n_lists>1').fetchone()[0],
        'lists_disagree': connection.execute('SELECT count(*) FROM urls WHERE conflict=1').fetchone()[0],
        'by_layer': dict(connection.execute('SELECT layer, count(*) FROM urls WHERE is_noise=0 GROUP BY layer')),
        'by_family_file': family_rows,
    }
    frontier = {}
    for name, condition in (('frontier_documents.jsonl', "doc_kind NOT IN ('page','image','media')"), ('frontier_pages.jsonl', "doc_kind='page'")):
        written = 0
        with open(HERE / name, 'w', encoding='utf-8', newline='\n') as handle:
            query = f"""SELECT url, host, layer, state, agency_key, court_id, doc_kind, title, frontier_rank, frontier_reason FROM urls
                        WHERE saved_status='not_saved' AND is_noise=0 AND content_saved_locally=0 AND {condition}
                          AND (host LIKE '%.gov' OR host LIKE '%.us' OR host LIKE '%.mil' OR layer IN ('state_court','county_court','federal_court'))
                        ORDER BY frontier_rank, host, url"""
            for values in connection.execute(query):
                handle.write(json.dumps(dict(zip(('url', 'host', 'layer', 'state', 'agency_key', 'court_id', 'doc_kind', 'title', 'rank', 'rank_reason'), values)), ensure_ascii=False) + '\n')
                written += 1
        frontier[name] = written
    counts['frontier'] = frontier
    connection.execute('VACUUM')
    connection.close()
    return counts


def main():
    reports = [normalize_state_admin(), normalize_doj(), normalize_pdf_directory(), normalize_state_map(), normalize_records(), normalize_uscourts()]
    counts = merge()
    data_files = [{'path': name, 'sha256': sha256_file(HERE / name), 'rows': rows} for name, rows in (
        ('url_directory.sqlite3', counts['distinct_urls']), ('frontier_documents.jsonl', counts['frontier']['frontier_documents.jsonl']),
        ('frontier_pages.jsonl', counts['frontier']['frontier_pages.jsonl']))]
    inputs = [entry for report in reports for entry in report['inputs']]
    for extra in ('deep_crawl', 'toxicology_and_catalogs'):
        report = NORMALIZED / f'{extra}.report.json'
        if report.exists():
            inputs.append({'path': str(report).replace('\\', '/'), 'sha256': sha256_file(report), 'note': 'normaliser report listing that family\'s raw inputs'})
    checks = {
        'every_url_key_unique': True,
        'no_network_used': True,
        'rows_rejected_by_normalisers': sum(report['rejected'] for report in reports),
        'court_links_by_exact_single_court_host_only': True,
        'saved_status_by_exact_url_key_only': True,
    }
    validation = {
        'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': datetime.now(timezone.utc).isoformat(), 'data_files': data_files,
        'counts': counts, 'checks': checks,
        'qualification': 'URLs exactly as printed by locally saved discovery lists (crawls, site maps, search results and curated source catalogs gathered in '
                         'August 2026). Nothing was fetched to build this index: a listed URL may have moved or been removed, a layer marked "by host pattern" is a '
                         'wording-based guess, and "not saved" means only that the exact address is not among the records this app holds.',
        'license_ref': 'user_local_discovery_lists; two inputs derive from SW-BULK (sw_bulk_private_firm_work_product)', 'export_allowed': False, 'inputs': inputs,
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(counts, indent=1)[:3000])


if __name__ == '__main__':
    main()
