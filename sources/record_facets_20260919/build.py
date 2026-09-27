"""Build facets.sqlite3: one derived-facet row per directory record (sources/record_facets_20260919).

Offline and re-runnable. Reads delivery/archive-directory/directory.sqlite3 strictly read-only (file:...?mode=ro),
reads saved originals/text through the directory's `files` table, and never modifies the directory.
Every derived value carries a basis column. Nothing here rewrites kind, category, title, state or payload.

Defect mapping: D01 (review states are not categories), D03 (jurisdiction verbatim from record metadata),
D06 (shell captures and wrong county joins), D07 (display titles from saved evidence), D08 (file/record types),
D09 (Seeger catch-all subtypes), D10 (judge datasets -> judges), D11 (Trellis URL path segment before payload category).
"""
from __future__ import annotations

import calendar
import collections
import datetime as dt
import hashlib
import html as html_lib
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ADAPTER_DIR = ROOT / 'delivery/archive-directory'
sys.path.insert(0, str(ADAPTER_DIR))
sys.path.insert(0, str(HERE))
import schema  # noqa: E402
from categories import classify  # noqa: E402
from evidence_dates import document_dates  # noqa: E402

DIRECTORY = ADAPTER_DIR / 'directory.sqlite3'
RECEIPT = ADAPTER_DIR / 'build_receipt.json'
LAW_TIER = ROOT / 'sources/law_tier_20260919'
OUT_DB = HERE / 'facets.sqlite3'
QUALIFICATION = ('Derived facets are rule-based labels over retained source fields, saved-file evidence and explicit '
                 'payload dates; every value carries its basis. They do not certify legal currency, completeness or '
                 'content correctness. Original kind, category, title and state stay in the directory unchanged.')
LICENSE_REF = ('Derived labels over records already published in the directory; each record keeps the terms of its own '
               'source. No new third-party content is stored (only short evidence excerpts of at most 120 characters).')

CATEGORY_VALUES = ('statutes', 'rules', 'constitutions', 'regulations', 'forms', 'guidance', 'directories', 'judges', 'other')
REVIEW_VALUES = ('reviewed', 'needs_content_review', 'title_evidence_only', 'not_applicable')
RTYPE_VALUES = ('law_provision', 'law_document', 'court_rule', 'order', 'form', 'court_document', 'directory_page', 'county_page',
                'judge_observation', 'judge_profile', 'source_reference', 'guidance', 'notice', 'other')
FTYPE_VALUES = ('pdf', 'html', 'docx', 'doc', 'xml', 'txt', 'json', 'jsonl', 'csv', 'xlsx', 'zip', 'gz', 'rtf', 'other', 'none')
JUR_VALUES = ('federal', 'state', 'county', 'territory', 'multi', 'unknown')
VALIDITY_VALUES = ('ok', 'parked_redirect', 'redirect_stub', 'spa_shell', 'error_page', 'challenge', 'empty', 'no_capture')
DTYPE_VALUES = ('original_document', 'saved_page', 'structured_provision', 'source_observation', 'profile', 'observed_link',
                'dataset_row', 'provider_capture')
SUBTYPE_VALUES = ('form', 'order', 'rule', 'instructions_guide', 'fee_schedule', 'jury_instruction', 'notice', 'other')

JUDGE_DATASETS = {'judge_enrichment', 'judge_entities', 'judge_vendor'}
TERRITORIES = {'Guam', 'Puerto Rico', 'Virgin Islands', 'Northern Mariana Islands', 'American Samoa'}
MODIFIER_TOKENS = {'document_text', 'ocr_text', 'law_text'}
JUDGE_DIRECTORY_TOKENS = {'judge_directory', 'judge_roster_directory', 'judge_profile_or_directory', 'judicial_directory_download',
                          'judge_roster_download'}
FOCUSED_TOKENS = {
    'statutes': 'statutes', 'local_laws_codes': 'statutes', 'home_rule_act': 'statutes', 'county_ordinance_text': 'statutes',
    'municipal_code_of_ordinances_pdf': 'statutes', 'unsigned_county_ordinance_text': 'statutes',
    'constitution': 'constitutions',
    'state_rule': 'rules', 'court_rules': 'rules', 'local_rules': 'rules', 'legislative_procedural_rules': 'rules',
    'court_administrative_orders_local_rules_index': 'rules', 'juvenile_probate_local_rules_text': 'rules',
    'court_local_rules_download_landing': 'rules',
    'administrative_code': 'regulations',
    'court_forms_filing_documents': 'forms', 'document': 'forms', 'judicial_records_request_form': 'forms',
    'court_filing_fee_schedule': 'forms', 'county_probate_clerk_fee_schedule_index': 'forms',
    'court_local_resources': 'guidance',
    'coverage_county': 'directories', 'county_government_entry': 'directories', 'county_government_entry_unreviewed': 'directories',
    'county_government_website_to_verify': 'directories', 'county_government': 'directories', 'court_clerk_office': 'directories',
    'judge_directory': 'directories', 'judge_roster_directory': 'directories', 'judge_profile_or_directory': 'directories',
    'judicial_directory_download': 'directories', 'judge_roster_download': 'directories', 'court_directory': 'directories',
    'court_or_county_directory': 'directories', 'court_directory_or_case_access': 'directories', 'state_judiciary_portal': 'directories',
    'law_directory': 'directories', 'legal_inventory_navigation': 'directories', 'county_facility_listing': 'directories',
    'county_permit_application_directory': 'directories', 'mixed_county_meeting_and_court_docket_directory': 'directories',
}
# D11: Trellis state-rules URL path segment (third segment) decides the top category before the payload category.
TRELLIS_SEGMENT = {
    'constitution': 'constitutions',
    'code': 'statutes', 'statutes': 'statutes', 'general-laws': 'statutes', 'unconsolidated': 'statutes', 'revised-statues': 'statutes',
    'consolidated-statutes': 'statutes', 'general-statutes': 'statutes', 'revised-code': 'statutes', 'compiled-statutes': 'statutes',
    'laws': 'statutes', 'consolidated-law': 'statutes', 'unconsolidated-statutes': 'statutes', 'court-acts': 'statutes',
    'administrative-code': 'regulations', 'code-of-regulations': 'regulations', 'administrative-rules': 'regulations',
    'regulation': 'regulations', 'codes,-rules-and-regulations': 'regulations', 'rules-&-regulations': 'regulations',
    'court-rules': 'rules', 'rules-of-civil-procedure': 'rules', 'rules': 'rules', 'rules-of-court': 'rules',
    'rules-+-civil-procedure': 'rules', 'rules-+-criminal-procedure': 'rules', 'rules-+-family-law-procedure': 'rules',
    'rules-+-probate-procedure': 'rules',
}
SEEGER_KIND = {'statutory_provision': 'statutes', 'rule_provision': 'rules', 'regulatory_provision': 'regulations',
               'court_rule_or_order': 'rules', 'court_form_or_other_document': 'forms', 'reference_original': 'guidance',
               'administrative_document': 'other'}
# D09: only these URL path tokens move a Seeger catch-all document out of 'forms'.
SEEGER_MOVE_TOKENS = (('judge-orders', 'rules'), ('general-orders', 'rules'), ('local-rules', 'rules'), ('local_rules', 'rules'))

# D09 keyword table (first match wins; applied to the title, then to the URL filename).
SUBTYPE_RULES = [
    ('jury_instruction', re.compile(r'jury\s+instruction', re.I)),
    ('fee_schedule', re.compile(r'fee\s+schedule|schedule\s+of\s+(?:court\s+)?fees|filing\s+fees?\b|\bfees?\s+(?:list|table|chart)\b|\bcost\s+schedule', re.I)),
    ('instructions_guide', re.compile(r'\binstructions?\b|\bguide\b|\bguidance\b|\bhandbook\b|\bmanual\b|\bhow\s+to\b|\bfaqs?\b|\bprocedures?\b|\bchecklist\b', re.I)),
    ('order', re.compile(r'\borders?\b', re.I)),
    ('rule', re.compile(r'\brules?\b', re.I)),
    ('form', re.compile(r'\bforms?\b|\baffidavit\b|\bapplication\b|\bpetition\b|\bworksheet\b|\bmotion\b|\bdeclaration\b|\bsummons\b|'
                        r'\bsubpoena\b|\bwaiver\b|\bcertificate\b|\brequest\b|\bcomplaint\b|\bstipulation\b|\bwrit\b|\bconsent\b|'
                        r'\bcertification\b|\bquestionnaire\b|\bproof\s+of\s+service\b|\bcover\s+sheet\b', re.I)),
    ('notice', re.compile(r'\bnotice\b', re.I)),
]
SUBTYPE_URL_PATHS = (('/forms/', 'form'), ('judge-orders', 'order'), ('general-orders', 'order'), ('standing-orders', 'order'),
                     ('/orders/', 'order'), ('local-rules', 'rule'), ('local_rules', 'rule'), ('/rules/', 'rule'))

# D06: exact shell hashes (sha256 of the saved original) gathered in _inspect/explore_shells.json.
SHELL_HASHES = {
    '6dc9c7fc93bb488bb0520a6c780a8d3c0fb5486a4711aca49b4c53fac7393023': ('parked_redirect', 'JavaScript redirect to /lander (parked-domain lander page); 114 bytes'),
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855': ('empty', 'zero-byte capture (sha256 of empty input)'),
    'fd0f44200e44f6943538a3f8baa3d72f41f14d81696ee4dc3d41a47550bbec49': ('parked_redirect', 'easyDNS "Parked Domain" page; 1,141 bytes'),
    'c59becd922dafd1a4199fa8c70461c8a1b0464eb2de43962354ca1bd2d4d7051': ('error_page', 'IIS Windows Server default page; 704 bytes'),
    '2c089b72b9d50fcae4e361ab3cfbb3f24397dcde1f58832a824e47029be0c0a3': ('spa_shell', 'South Dakota Legislature "Loading..." script shell; 5,982 bytes; no legal text'),
    '8f2bfe0930e83fac5390c0423d979f5ddf50a09d9646524f57809e68b067219f': ('error_page', '"Error. Page cannot be displayed" host error page; 122 bytes'),
}
# The same list identified these single-record shells by their 16-hex prefix; resolved to the full hash at build time.
SHELL_PREFIXES = {
    '6711785054151632': ('redirect_stub', 'META refresh redirect stub; 530 bytes'),
    '36b6c417fde16675': ('redirect_stub', 'frameset shell (content lives in frames that were not captured); 291 bytes'),
    'ee284a3efaf5d468': ('empty', '6-byte body "Live 2"; no page content'),
    '185a8940cc582ae9': ('empty', 'head-only page (site-verification meta, no body); 476 bytes'),
    '3aae334beee35fa5': ('error_page', '"Welcome to WildFly" application-server default page; 1,496 bytes'),
}
CHALLENGE_TITLES = {'just a moment...', 'attention required! | cloudflare', 'access denied', 'access denied.', 'please wait...',
                    'security check', 'human verification'}
GENERIC_TITLES = {'home', 'pdf', 'title', 'untitled', 'untitled record', 'untitled document', 'document', 'page', 'index', 'default',
                  'unknown', 'download', 'view', 'doc', 'file', 'new page 1', 'homepage', 'home page', 'welcome'}
NAV_LINES = {'menu', 'home', 'skip to content', 'skip to the content', 'skip to main content', 'primary menu', 'main menu', 'search',
             'login', 'log in', 'sign in', 'contact', 'contact us', 'about', 'about us', 'previous', 'next', 'print', 'share', 'close',
             'english', 'espanol', 'translate', 'back to top', 'navigation', 'toggle navigation', 'read on...', 'loading...'}
PLACEHOLDER_TITLE = re.compile(r'^\s*\[(DOCX text|Word 2003 XML text|OCR|PDF text|HTML text|EPUB text|RTF text|XML text)\]\s*$', re.I)
URL_TITLE = re.compile(r'^(?:https?://|[a-z]:[\\/]|\\\\)', re.I)
PRODUCER_PREFIX = re.compile(r'^Microsoft (?:Word|Excel|PowerPoint)\s*[-–]\s*\S', re.I)
CITATION_LINE = re.compile(r'^(?:§+\s*\d|(?:Sec(?:tion)?|Chapter|Title|Article|Art\.|Rule|Part|Subchapter|Division|Ordinance|Resolution)\s*'
                           r'(?:No\.?\s*)?[\dIVXLC]+\S*\s+\S|\d{1,4}[-.:]\d{1,4}\S*\s+[A-Za-z]|(?:Local\s+(?:Court\s+)?Rules|Rules\s+of\s|Code\s+of\s|'
                           r'Constitution\s+of\s|Ordinance\s+No|Administrative\s+Order|General\s+Order|Standing\s+Order)\b)', re.I)
SKIP_LINE = re.compile(r'^(?:[=-]+\s*(?:DOCX PART|OCR PAGE|PAGE)\b.*|\W+|\d[\d\s.,()-]*|\(\d.*|https?://\S+)$', re.I)
WORD = re.compile(r'[A-Za-zÀ-ɏ]{2,}')
DATETIME_FIX = re.compile(r'(\.\d{6})\d+')
FRACTION7 = re.compile(r'(\.\d{6})\d+(?=[Z+-]|$)')


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def loads(value, default=None):
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def as_list(value):
    value = loads(value, value)
    if value is None or value == '':
        return []
    if isinstance(value, list):
        return [v for v in value if v not in (None, '')]
    return [value]


def bounds(value):
    """(lo, hi) comparable YYYY-MM-DD keys for a full, month or year precision value; timestamps use their UTC date."""
    if not value:
        return None, None
    v = value.strip()
    if re.fullmatch(r'\d{4}', v):
        return v + '-01-01', v + '-12-31'
    if re.fullmatch(r'\d{4}-\d{2}', v):
        last = calendar.monthrange(int(v[:4]), int(v[5:7]))[1]
        return v + '-01', f'{v}-{last:02d}'
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
        return v, v
    try:
        parsed = dt.datetime.fromisoformat(FRACTION7.sub(r'\1', v).replace('Z', '+00:00'))
    except ValueError:
        return v[:10], v[:10]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc)
    day = parsed.date().isoformat()
    return day, day


def normalise_timestamps(payload):
    """Shallow copy whose captured_at/retrieved_at carry at most 6 fractional digits (document_dates rejects 7)."""
    def fix(value):
        if isinstance(value, str):
            return FRACTION7.sub(r'\1', value)
        if isinstance(value, list):
            return [fix(v) for v in value]
        return value
    copy = dict(payload)
    for key in ('captured_at', 'retrieved_at'):
        if key in copy:
            copy[key] = fix(copy[key])
    meta = copy.get('metadata')
    if isinstance(meta, dict):
        meta = dict(meta)
        for key in ('captured_at', 'retrieved_at'):
            if key in meta:
                meta[key] = fix(meta[key])
        copy['metadata'] = meta
    return copy


def host_of(url):
    try:
        host = urlsplit(url or '').netloc.lower()
    except ValueError:
        return ''
    return host[4:] if host.startswith('www.') else host


def path_of(url):
    try:
        return urlsplit(url or '').path
    except ValueError:
        return ''


def deslug(url):
    name = unquote(path_of(url).rstrip('/').rsplit('/', 1)[-1])
    stem = re.sub(r'\.(?:pdf|html?|docx?|xml|txt|aspx?|php|jsp|cfm|zip|rtf|htm)$', '', name, flags=re.I)
    if len(stem) <= 3 or stem.casefold() in GENERIC_TITLES or re.fullmatch(r'[0-9a-f]{12,}', stem, re.I) or re.fullmatch(r'[\d\W_]+', stem):
        return None
    return re.sub(r'\s+', ' ', re.sub(r'(?<!\d)-|-(?!\d)|_+|\++', ' ', stem)).strip()


def host_path(url):
    host = host_of(url)
    try:
        parts = urlsplit(url or '')
    except ValueError:
        return host or (url or '')[:140], 'source URL host'
    path = unquote(parts.path).rstrip('/')
    if not path or path == '/':
        return host, 'source URL host'
    value = host + path + ('?' + parts.query if parts.query else '')
    return value[:140], 'source URL host + path'


def clean_line(text):
    text = html_lib.unescape(text or '').replace('\xad', '').replace('﻿', '')
    text = re.sub(r'^\s*(?:-->|<!--|\*|•|>)+\s*', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def acceptable_line(line, avoid):
    if not (4 <= len(line) <= 160) or SKIP_LINE.match(line) or URL_TITLE.match(line):
        return False
    low = line.casefold().strip(' :|-')
    if low in NAV_LINES or low in GENERIC_TITLES or low in avoid:
        return False
    words = WORD.findall(line)
    return len(words) >= 2 or (len(words) == 1 and len(line) >= 8)


def expand_heading(lines, index, avoid):
    """Join a short heading with the short lines that follow it (PDF extraction often yields one word per line)."""
    number, text = lines[index]
    joined, added = text, 0
    while added < 8 and len(joined.split()) < 6 and not re.search(r'[.:;]$', joined) and index + added + 1 < len(lines):
        nxt = lines[index + added + 1][1]
        if re.fullmatch(r'\d{1,4}[A-Za-z]?', nxt):  # "TITLE" / "1" / "COURTS" ... one token per line
            joined += ' ' + nxt
            added += 1
            continue
        if len(nxt) > 30 or len(nxt.split()) > 3 or SKIP_LINE.match(nxt) or nxt.casefold() in NAV_LINES or nxt.casefold() in avoid:
            break
        joined += ' ' + nxt
        added += 1
    return joined if added else text


def text_lines(text):
    """First 80 non-empty cleaned lines as (line_no, text)."""
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = clean_line(raw)
        if line:
            lines.append((number, line))
        if len(lines) >= 80:
            break
    return lines


def text_heading(text, avoid):
    """First citation line, else first acceptable heading line, of extracted text. Returns (line, line_no, kind)."""
    lines = text_lines(text)
    def candidates():
        for index, (number, line) in enumerate(lines):
            low = line.casefold().strip(' :|-')
            if low in avoid or low in NAV_LINES or SKIP_LINE.match(line) or len(line) < 4:
                continue
            yield number, expand_heading(lines, index, avoid)
    for number, candidate in candidates():
        if CITATION_LINE.match(candidate) and acceptable_line(candidate, avoid):
            return candidate, number, 'citation line'
    for number, candidate in candidates():
        if acceptable_line(candidate, avoid):
            return candidate, number, 'first heading line'
    return None, None, None


def html_heading(raw, avoid):
    """<title> (or an XML document title), else the first h1/h2, of a saved html/xml original."""
    head = raw[:262144].decode('utf-8', 'replace')
    for pattern, basis in ((r'<title[^>]*>(.*?)</title>', 'saved page <title>'), (r'<o:Title[^>]*>(.*?)</o:Title>', 'saved document <o:Title>'),
                           (r'<dc:title[^>]*>(.*?)</dc:title>', 'saved document <dc:title>')):
        match = re.search(pattern, head, re.I | re.S)
        if match:
            title = clean_line(re.sub(r'<[^>]+>', ' ', match.group(1)))
            if acceptable_line(title, avoid) and title.casefold() not in CHALLENGE_TITLES:
                return title, basis
    for match in re.finditer(r'<h[12][^>]*>(.*?)</h[12]>', head, re.I | re.S):
        heading = clean_line(re.sub(r'<[^>]+>', ' ', match.group(1)))
        if acceptable_line(heading, avoid):
            return heading, 'saved page first heading'
    return None, None


def magic_type(head):
    if not head:
        return 'other', 'empty file'
    if head.startswith(b'%PDF'):
        return 'pdf', 'magic bytes %PDF'
    if head.startswith(b'PK\x03\x04'):
        if b'word/' in head:
            return 'docx', 'magic bytes PK + word/ entry'
        if b'xl/' in head:
            return 'xlsx', 'magic bytes PK + xl/ entry'
        return 'zip', 'magic bytes PK'
    if head.startswith(b'\x1f\x8b'):
        return 'gz', 'magic bytes 1f8b'
    if head.startswith(b'\xd0\xcf\x11\xe0'):
        return 'doc', 'magic bytes OLE2'
    if head.startswith(b'{\\rtf'):
        return 'rtf', 'magic bytes {\\rtf'
    low = head[:4096].lower()
    if b'<html' in low or b'<!doctype html' in low or b'<body' in low:
        return 'html', 'magic bytes html tag'
    if low.lstrip().startswith(b'<?xml') or low.lstrip().startswith(b'<'):
        return 'xml', 'magic bytes xml/markup'
    stripped = head.lstrip()
    if stripped[:1] in (b'{', b'['):
        return 'json', 'magic bytes json'
    try:
        head.decode('utf-8')
        return 'txt', 'decodes as utf-8 text'
    except UnicodeDecodeError:
        return 'other', 'unrecognised magic bytes'


SUFFIX_TYPE = {'.html': 'html', '.htm': 'html', '.pdf': 'pdf', '.json': 'json', '.jsonl': 'jsonl', '.csv': 'csv', '.txt': 'txt', '.docx': 'docx',
               '.doc': 'doc', '.xml': 'xml', '.rtf': 'rtf', '.xlsx': 'xlsx', '.zip': 'zip', '.gz': 'gz', '.md': 'txt'}


def subtype_from(title, url, default=None, default_basis=None):
    for name, pattern in SUBTYPE_RULES:
        match = pattern.search(title or '')
        if match:
            return name, f'keyword "{match.group(0).strip().lower()}" in title (D09 keyword table)'
    filename = unquote(path_of(url).rstrip('/').rsplit('/', 1)[-1]).replace('_', ' ').replace('-', ' ')
    for name, pattern in SUBTYPE_RULES:
        match = pattern.search(filename)
        if match:
            return name, f'keyword "{match.group(0).strip().lower()}" in source filename (D09 keyword table)'
    low = (url or '').lower()
    for token, name in SUBTYPE_URL_PATHS:
        if token in low:
            return name, f'source URL path segment "{token.strip("/")}"'
    if default:
        return default, default_basis
    return 'other', 'no keyword match in title, filename or URL path'


class Builder:
    def __init__(self):
        self.con = sqlite3.connect('file:' + DIRECTORY.as_posix() + '?mode=ro', uri=True)
        self.con.row_factory = sqlite3.Row
        self.files = dict(self.con.execute('SELECT id, path FROM files'))
        self.counties = collections.defaultdict(list)
        for record_id, geoid in self.con.execute('SELECT record_id, geoid FROM record_counties ORDER BY record_id, geoid'):
            self.counties[record_id].append(geoid)
        self.original_share = collections.Counter(r[0] for r in self.con.execute('SELECT original_id FROM records WHERE original_id IS NOT NULL'))
        self.boilerplate = {(r[0], r[1]) for r in self.con.execute(
            "SELECT dataset, title FROM records WHERE dataset IN ('focused','pending_publication') GROUP BY dataset, title HAVING count(*)>=5")}
        self.hash_hosts, self.hash_records = collections.defaultdict(set), collections.Counter()
        for url, sha in self.con.execute("SELECT source_url, json_extract(payload,'$.raw_sha256') FROM records WHERE dataset='focused'"):
            if sha:
                self.hash_hosts[sha].add(host_of(url)); self.hash_records[sha] += 1
        self.shell_full = dict(SHELL_HASHES)
        for sha in self.hash_records:
            if sha[:16] in SHELL_PREFIXES:
                self.shell_full[sha] = SHELL_PREFIXES[sha[:16]]
        self.labels = self.load_labels()
        self.stats = collections.Counter()
        self.wrong_joins = []
        self.repaired = {}
        self.site_lines = self.preload_site_lines()

    def title_needs_repair(self, dataset, title, url):
        t = re.sub(r'\s+', ' ', (title or '').strip())
        return (not t or t == (url or '').strip() or bool(URL_TITLE.match(t)) or bool(PLACEHOLDER_TITLE.match(t)) or len(t) <= 2
                or t.casefold() in GENERIC_TITLES or (dataset, title) in self.boilerplate)

    def preload_site_lines(self):
        """Per host: text lines that recur across that host's repair candidates (site navigation) so titles never use them."""
        pages, lines_by_host = collections.Counter(), collections.defaultdict(collections.Counter)
        query = "SELECT dataset, title, source_url, text_id, inline_text, original_id FROM records WHERE dataset IN ('focused','pending_publication')"
        for dataset, title, url, text_id, inline, original_id in self.con.execute(query):
            # Site navigation is an html phenomenon; PDF/DOCX text from one host must keep its shared headings and stop-words.
            if not self.title_needs_repair(dataset, title, url) or not (self.files.get(original_id) or '').lower().endswith(('.html', '.htm')):
                continue
            path = self.file_path(text_id)
            text = inline[:16384] if inline else (self.read_head(path, 16384).decode('utf-8', 'replace') if path else '')
            if not text:
                continue
            host = host_of(url)
            pages[host] += 1
            lines_by_host[host].update({line.casefold().strip(' :|-') for _, line in text_lines(text)})
        site = {}
        for host, counter in lines_by_host.items():
            if pages[host] < 3:
                continue
            threshold = max(3, -(-pages[host] // 4))  # ceil(25%)
            shared = {line for line, n in counter.items() if n >= threshold}
            if shared:
                site[host] = shared
        self.stats['site_line_hosts'] = len(site)
        self.stats['site_lines'] = sum(len(v) for v in site.values())
        return site

    # ---------- inputs ----------
    def load_labels(self):
        """law_tier record_labels.jsonl folded in only when its validation envelope has passed and is ready."""
        gate_file = LAW_TIER / 'validation.json'
        self.law_tier = {'folded': False, 'reason': 'validation.json missing', 'path': None, 'sha256': None}
        if not gate_file.is_file():
            return {}
        try:
            gate = json.loads(gate_file.read_text(encoding='utf-8'))
        except ValueError:
            self.law_tier['reason'] = 'validation.json malformed'; return {}
        if not (gate.get('status') == 'passed' and gate.get('ready') is True):
            self.law_tier['reason'] = f"status={gate.get('status')!r} ready={gate.get('ready')!r}"; return {}
        pins = [x for x in gate.get('data_files') or [] if isinstance(x, dict) and str(x.get('path', '')).endswith('record_labels.jsonl')]
        data = LAW_TIER / 'record_labels.jsonl'
        if not pins or not data.is_file():
            self.law_tier['reason'] = 'record_labels.jsonl not pinned or missing'; return {}
        digest = sha256_file(data)
        if digest != pins[0].get('sha256'):
            self.law_tier['reason'] = 'record_labels.jsonl hash mismatch'; return {}
        labels = {}
        with data.open(encoding='utf-8') as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    if row.get('record_id'):
                        labels[row['record_id']] = row
        # Topics live in topic_index.jsonl; only rows keyed by a directory record_id are folded (Open US Law rows are not directory records).
        topics_file = LAW_TIER / 'topic_index.jsonl'
        topic_pins = [x for x in gate.get('data_files') or [] if isinstance(x, dict) and str(x.get('path', '')).endswith('topic_index.jsonl')]
        topics_sha, topic_rows = None, 0
        if topic_pins and topics_file.is_file() and sha256_file(topics_file) == topic_pins[0].get('sha256'):
            topics_sha = topic_pins[0]['sha256']
            with topics_file.open(encoding='utf-8') as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    record_id = row.get('record_id')
                    names = sorted({t.get('topic') for t in (row.get('topics') or []) if isinstance(t, dict) and t.get('topic')})
                    if record_id and names:
                        labels.setdefault(record_id, {'record_id': record_id})['topics'] = names
                        topic_rows += 1
        self.law_tier = {'folded': True, 'reason': None, 'path': 'sources/law_tier_20260919/record_labels.jsonl', 'sha256': digest,
                         'validation_sha256': sha256_file(gate_file), 'topics_path': 'sources/law_tier_20260919/topic_index.jsonl' if topics_sha else None,
                         'topics_sha256': topics_sha, 'topic_rows_folded': topic_rows}
        return labels

    def file_path(self, file_id):
        rel = self.files.get(file_id) if file_id else None
        return (ROOT / rel) if rel else None

    def read_head(self, path, size):
        try:
            with open(path, 'rb') as stream:
                return stream.read(size)
        except OSError:
            return b''

    # ---------- per-record derivations ----------
    def categories_for(self, dataset, base_kind, payload, url, doc_subtype):
        if dataset in JUDGE_DATASETS:
            return ['judges'], 'dataset rule: judge_* datasets -> judges (D10)'
        if dataset == 'focused':
            tokens = as_list(payload.get('categories_json')) or [t.strip() for t in (payload.get('category') or '').split(';') if t.strip()]
            collection = payload.get('collection') or ''
            if host_of(url) == 'trellis.law' and collection in ('trellis_public', 'corpus/trellis_browser_laws'):
                parts = [p for p in path_of(url).split('/') if p]
                if len(parts) >= 3 and parts[0] == 'state-rules' and parts[2] in TRELLIS_SEGMENT:
                    return [TRELLIS_SEGMENT[parts[2]]], f'publisher URL path segment "{parts[2]}" (Trellis state-rules, D11)'
            found, basis_tokens = [], []
            for token in tokens:
                if token in MODIFIER_TOKENS:
                    continue
                category = FOCUSED_TOKENS.get(token) or classify(token)
                if category == 'other':
                    continue
                if category not in found:
                    found.append(category); basis_tokens.append(token)
                if token in JUDGE_DIRECTORY_TOKENS and 'judges' not in found:
                    found.append('judges')
            if found:
                return found, 'retained source category field (payload.categories_json: ' + ', '.join(basis_tokens) + ')'
            fallback = classify(base_kind)
            return [fallback], f'classify(kind) fallback; no mappable retained category (kind={base_kind})'
        if dataset == 'seeger':
            category = SEEGER_KIND.get(base_kind) or classify(base_kind)
            if base_kind == 'court_form_or_other_document':
                low = (url or '').lower()
                for token, moved in SEEGER_MOVE_TOKENS:
                    if token in low:
                        return [moved], f'source URL path segment "{token}" (D09 explicit move out of forms)'
                return [category], 'retained resource_kind court_form_or_other_document (source catalog hint); subtype carries the keyword evidence (D09)'
            return [category], f'retained resource_kind {base_kind}'
        if dataset == 'pending_publication':
            return [classify(payload.get('resource_kind') or base_kind)], f'retained resource_kind {payload.get("resource_kind") or base_kind}'
        category = classify(base_kind)
        return [category], f'classify(resource_kind={base_kind})'

    def review_for(self, dataset, kind, base_kind, payload, category):
        if dataset in JUDGE_DATASETS:
            return 'not_applicable', 'judge observation/profile; document content review not applicable'
        if dataset == 'focused':
            content_kind = payload.get('content_kind') or ''
            if content_kind == 'needs_content_review':
                return 'needs_content_review', 'payload.content_kind=needs_content_review (review state, not a document type; D01)'
            if content_kind == 'law_document_title_evidence_needs_review':
                return 'title_evidence_only', 'payload.content_kind=law_document_title_evidence_needs_review (D01)'
            if 'unreviewed' in kind or 'to_verify' in kind:
                return 'needs_content_review', f'retained kind flags unreviewed/to_verify ({base_kind})'
            body = payload.get('body_status') or ''
            if body.startswith(('verified', 'observed')):
                return 'reviewed', f'payload.body_status={body}'
            if payload.get('county_reviewed_resource_kind'):
                return 'reviewed', 'county semantic review recorded (payload.county_reviewed_resource_kind)'
            if category == 'directories' or body == 'directory_navigation':
                return 'not_applicable', 'directory/navigation page; content review not applicable'
            if content_kind in ('historical_recodification_notice', 'historical_repeal_notice', 'historical_expiration_notice', 'ancillary',
                                'reconciled_duplicate_archive', 'captured_law_representation'):
                return 'reviewed', f'payload.content_kind={content_kind}'
            return 'needs_content_review', 'no retained review evidence in payload'
        if dataset == 'seeger':
            meta = payload.get('metadata') or {}
            if meta.get('record_type') == 'structured_provision':
                status = meta.get('source_status')
                return 'reviewed', 'structured provision parsed from the source text' + (f'; metadata.source_status={status}' if status else '')
            return 'title_evidence_only', 'metadata.kind_basis: source catalog/title organizing hint, not an independent content review'
        if dataset == 'pending_publication':
            return 'needs_content_review', 'payload.quality=Awaiting publication validation'
        if dataset == 'federal':
            return 'not_applicable', 'observed link or supplementary reference capture; content review not applicable'
        return 'not_applicable', f'{dataset}: publisher profile/provider capture; content review not applicable'

    def subtype_for(self, dataset, base_kind, category, title, url, payload):
        if dataset == 'seeger':
            if base_kind == 'court_form_or_other_document':
                return subtype_from(title, url)
            if base_kind == 'court_rule_or_order':
                return subtype_from(title, url, 'rule', 'resource_kind=court_rule_or_order without an order keyword')
            if base_kind == 'rule_provision':
                return 'rule', 'resource_kind=rule_provision'
            if base_kind == 'administrative_document':
                return subtype_from(title, url)
            return None, None
        if dataset == 'focused':
            if base_kind in ('local_rules', 'juvenile_probate_local_rules_text', 'court_local_rules_download_landing'):
                return 'rule', f'retained kind {base_kind}'
            if base_kind in ('court_filing_fee_schedule', 'county_probate_clerk_fee_schedule_index'):
                return 'fee_schedule', f'retained kind {base_kind}'
            if base_kind == 'judicial_records_request_form':
                return 'form', 'retained kind judicial_records_request_form'
            if base_kind == 'court_forms_filing_documents':
                return subtype_from(title, url, 'form', 'retained kind court_forms_filing_documents')
            if base_kind == 'court_local_resources':
                return subtype_from(title, url)
            if category == 'rules':
                return subtype_from(title, url, 'rule', 'retained source category rules (no order keyword)')
            return None, None
        if dataset == 'federal':
            if base_kind == 'federal_order_document_link':
                return 'order', 'resource_kind=federal_order_document_link'
            if base_kind in ('federal_rule_or_practice_resource', 'federal_archived_rule_index'):
                return 'rule', f'resource_kind={base_kind}'
            return None, None
        if dataset == 'pending_publication':
            rk = payload.get('resource_kind')
            if rk == 'local_rules':
                return 'rule', 'resource_kind=local_rules'
            if rk == 'court_forms_filing_documents':
                return subtype_from(title, url, 'form', 'resource_kind=court_forms_filing_documents')
        return None, None

    def record_type_for(self, dataset, base_kind, payload, category, subtype):
        by_subtype = {'form': 'form', 'order': 'order', 'rule': 'court_rule', 'instructions_guide': 'guidance', 'notice': 'notice',
                      'fee_schedule': 'court_document', 'jury_instruction': 'court_document', 'other': 'court_document'}
        if dataset == 'judge_entities':
            return 'judge_profile', 'dataset judge_entities (merged entity profile)'
        if dataset in ('judge_enrichment', 'judge_vendor'):
            return 'judge_observation', f'dataset {dataset} (source observation)'
        if dataset == 'federal':
            if payload.get('record_type') == 'observed_link':
                return 'source_reference', 'payload.record_type=observed_link'
            return {'rules': 'court_rule', 'guidance': 'guidance', 'directories': 'directory_page'}.get(category, 'other'), f'payload.record_type=capture; category {category}'
        if dataset == 'seeger':
            if base_kind in ('statutory_provision', 'regulatory_provision'):
                return 'law_provision', f'resource_kind={base_kind}'
            if base_kind == 'rule_provision':
                return 'court_rule', 'resource_kind=rule_provision'
            if base_kind == 'court_rule_or_order':
                return ('order' if subtype == 'order' else 'court_rule'), f'resource_kind=court_rule_or_order; subtype {subtype}'
            if base_kind == 'court_form_or_other_document':
                return by_subtype.get(subtype, 'court_document'), f'resource_kind=court_form_or_other_document; subtype {subtype}'
            if base_kind == 'reference_original':
                return 'source_reference', 'resource_kind=reference_original'
            return 'court_document', f'resource_kind={base_kind}'
        if dataset == 'pending_publication':
            rk = payload.get('resource_kind') or base_kind
            return {'local_laws_codes': 'law_document', 'statutes': 'law_document', 'local_rules': 'court_rule'}.get(rk, by_subtype.get(subtype, 'form')), f'resource_kind={rk}'
        if dataset == 'trellis_browser_counties':
            return 'county_page', 'dataset trellis_browser_counties (publisher county profile)'
        if dataset == 'provider_laws':
            return 'directory_page', 'resource_kind=law_directory'
        # focused
        content_kind = payload.get('content_kind') or ''
        if content_kind.startswith('historical_'):
            return 'notice', f'payload.content_kind={content_kind}'
        if category in ('statutes', 'constitutions', 'regulations'):
            return 'law_document', f'derived category {category} (retained category field)'
        if category == 'rules':
            return ('order' if subtype == 'order' else 'court_rule'), f'derived category rules; subtype {subtype}'
        if category == 'forms':
            return by_subtype.get(subtype, 'form'), f'derived category forms; subtype {subtype}'
        if category == 'guidance':
            return 'guidance', 'derived category guidance'
        if category == 'directories':
            if base_kind.startswith('county_') or base_kind == 'coverage_county' or payload.get('package_group') == 'counties':
                return 'county_page', f'retained kind {base_kind} (county page)'
            return 'directory_page', f'retained kind {base_kind} (directory page)'
        return 'other', f'no record-type rule for kind {base_kind}'

    def representation_for(self, dataset, payload, file_type):
        if dataset == 'seeger':
            value = (payload.get('metadata') or {}).get('record_type')
            return (value if value in DTYPE_VALUES else 'original_document'), 'metadata.record_type'
        if dataset == 'federal':
            return ('observed_link', 'payload.record_type=observed_link') if payload.get('record_type') == 'observed_link' else ('provider_capture', f"payload.capture_kind={payload.get('capture_kind')}")
        if dataset == 'judge_enrichment':
            if payload.get('source_class') == 'federal_biographies':
                return 'dataset_row', 'payload.source_class=federal_biographies (FJC csv row)'
            return 'source_observation', f"payload.source_class={payload.get('source_class')}"
        if dataset == 'judge_entities':
            return 'profile', 'merged entity profile (payload.entity_id)'
        if dataset == 'judge_vendor':
            return 'provider_capture', f"payload.capture_kind={payload.get('capture_kind')}"
        if dataset == 'trellis_browser_counties':
            return 'provider_capture', f"payload.raw_representation_kind={payload.get('raw_representation_kind')}"
        if dataset == 'provider_laws':
            return 'provider_capture', 'payload.provider_response_is_original_http_bytes=false'
        if dataset == 'pending_publication':
            return ('saved_page' if file_type == 'html' else 'original_document'), f'saved original file type {file_type}'
        capture_kind = payload.get('capture_kind') or ''
        if capture_kind == 'provider_rendered_public_page':
            return 'provider_capture', 'payload.capture_kind=provider_rendered_public_page'
        if capture_kind in ('document_text_derivative', 'ocr_derivative'):
            return 'original_document', f'payload.capture_kind={capture_kind} (derived text of a saved document)'
        if file_type == 'html':
            return 'saved_page', f'payload.capture_kind={capture_kind or "direct_public_capture"}; html original'
        return 'original_document', f'payload.capture_kind={capture_kind or "direct_public_capture"}; {file_type} original'

    def jurisdiction_for(self, dataset, state_value, payload):
        states = [s.strip() for s in (state_value or '').split(';') if s.strip()]
        meta = payload.get('metadata') or {}
        if len(states) > 1:
            return 'multi', f'records.state lists {len(states)} states (multi-valued source association; not resolved)', None, states, 1, None
        state = states[0] if states else None
        def level_for_state(level):
            return 'territory' if state in TERRITORIES and level == 'state' else level
        if dataset == 'seeger':
            jur = as_list(meta.get('source_jurisdictions'))
            court = '; '.join(str(c) for c in as_list(meta.get('source_courts'))) or None
            if jur == ['Federal']:
                return 'federal', 'metadata.source_jurisdictions=["Federal"] (verbatim)', 'Federal', ['Federal'], 0, court
            if len(jur) == 1:
                return level_for_state('state'), f'metadata.source_jurisdictions={json.dumps(jur)} (verbatim)', jur[0], jur, 0, court
            if len(jur) > 1:
                return 'multi', f'metadata.source_jurisdictions lists {len(jur)} jurisdictions (verbatim, not resolved)', None, jur, 1, court
            if state:
                return level_for_state('state'), 'payload.state (structured provision; no metadata.source_jurisdictions)', state, [state], 0, court
            return 'unknown', 'no jurisdiction evidence in record metadata', None, [], 0, court
        if dataset == 'federal':
            if state:
                return 'federal', 'payload.authority=federal, scope=federal; state from payload.state_if_explicit', state, [state], 0, None
            return 'federal', 'payload.authority=federal, scope=federal; no explicit state label', 'Federal', ['Federal'], 0, None
        if dataset in ('judge_enrichment', 'judge_vendor'):
            system = payload.get('judge_system') or 'unknown'
            court = '; '.join(str(c) for c in as_list(payload.get('courts'))) or None
            if system == 'federal':
                return 'federal', 'payload.judge_system=federal', state or 'Federal', [state or 'Federal'], 0, court
            if system == 'state':
                return level_for_state('state'), 'payload.judge_system=state; payload.state_code', state, [state] if state else [], 0, court
            return 'unknown', f'payload.judge_system={system}', state, [state] if state else [], 0, court
        if dataset == 'judge_entities':
            systems = as_list(payload.get('judge_systems'))
            court = '; '.join(str(c) for c in as_list(payload.get('courts'))) or None
            if systems == ['federal']:
                return 'federal', 'payload.judge_systems=["federal"]', state or 'Federal', [state or 'Federal'], 0, court
            if systems == ['state']:
                return level_for_state('state'), 'payload.judge_systems=["state"]; payload.state_labels', state, [state] if state else [], 0, court
            if len(systems) > 1:
                return 'multi', f'payload.judge_systems={json.dumps(systems)}', state, [state] if state else [], 1, court
            return 'unknown', f'payload.judge_systems={json.dumps(systems)}', state, [state] if state else [], 0, court
        if dataset == 'pending_publication':
            geoids = as_list(payload.get('county_geoids'))
            if geoids:
                return 'county', f'payload.county_geoids={json.dumps(geoids)}; metadata.jurisdiction_basis', state, [state] if state else [], 0, None
            return (level_for_state('state') if state else 'unknown'), 'payload.state; metadata.jurisdiction_basis', state, [state] if state else [], 0, None
        if dataset == 'trellis_browser_counties':
            return 'county', 'metadata.county_geoid (publisher county profile)', state, [state] if state else [], 0, None
        if dataset == 'provider_laws':
            return (level_for_state('state') if state else 'unknown'), 'payload.state (official state agency page)', state, [state] if state else [], 0, None
        # focused
        group = payload.get('package_group') or ''
        jurisdictions = loads(payload.get('jurisdictions_json'), []) or []
        first = jurisdictions[0] if isinstance(jurisdictions, list) and jurisdictions and isinstance(jurisdictions[0], dict) else {}
        labels = ', '.join(str(c) for c in (loads(payload.get('county_court_labels_json'), []) or [])[:3]) or None
        if not state:
            return 'unknown', f"payload.jurisdiction={payload.get('jurisdiction')!r}; county association {first.get('county_name_association', 'n/a')}", None, [], 0, labels
        if group == 'counties':
            return 'county', f'payload.package_group=counties; payload.jurisdictions_json state={first.get("state") or state}', state, [state], 0, labels
        return level_for_state('state'), f'payload.jurisdictions_json state={first.get("state") or state}', state, [state], 0, labels

    def dates_for(self, dataset, payload):
        normal = normalise_timestamps(payload)
        dd = document_dates(normal)
        evidence = dd['date_evidence']
        meta = payload.get('metadata') or {}
        out = {}
        saved = dd['saved_at']
        field = evidence['saved_at_field']
        if saved:
            basis = f'payload.{field}'
            if field == 'retrieved_at':
                basis += f" (retrieval_time_basis={evidence['saved_at_basis'] or payload.get('retrieval_time_basis') or 'unstated'})"
            elif isinstance(payload.get('captured_at'), list):
                basis += ' (latest of the listed capture timestamps)'
            out.update(saved_at=saved, saved_at_field=field, saved_at_basis=basis, captured_at=saved, captured_at_basis=basis)
        else:
            out.update(saved_at=None, saved_at_field=None, saved_at_basis=None, captured_at=None, captured_at_basis=None)
        out['saved_lo'], out['saved_hi'] = bounds(saved)
        source = dd['source_as_of']
        source_field = evidence['source_as_of_field']
        kind = meta.get('source_date_kind') if source_field == 'source_date' else None
        effective, effective_basis = dd['effective_date'], ('payload.effective_date' if dd['effective_date'] else None)
        if source and kind and 'effective' in str(kind):
            effective, effective_basis, source, source_field = source, f'metadata.source_date (source_date_kind={kind})', None, None
        if source:
            where = 'metadata' if source_field in meta and payload.get(source_field) is None else 'payload'
            out.update(source_as_of=source, source_as_of_basis=f'{where}.{source_field}' + (f' (source_date_kind={kind})' if kind else ''))
        else:
            out.update(source_as_of=None, source_as_of_basis=None)
        out['source_lo'], out['source_hi'] = bounds(source)
        published = payload.get('source_published_at') if dataset == 'judge_vendor' else None
        published = published if isinstance(published, str) and re.match(r'\d{4}', published) else None
        out.update(published_at=published, published_at_basis='payload.source_published_at' if published else None)
        out['published_lo'], out['published_hi'] = bounds(published)
        out.update(effective_from=effective, effective_from_basis=effective_basis)
        out['effective_lo'], out['effective_hi'] = bounds(effective)
        out.update(effective_to=None, effective_to_basis=None)
        return out

    def title_for(self, dataset, title, url, text_path, inline_text, original_path, file_type, boilerplate, original_sha, text_sha, extra_avoid=frozenset()):
        title = (title or '').strip()
        collapsed = re.sub(r'\s+', ' ', title)
        issue = 'none'
        if not collapsed:
            issue = 'empty'
        elif collapsed == (url or '').strip() or URL_TITLE.match(collapsed):
            issue = 'url'
        elif PLACEHOLDER_TITLE.match(collapsed):
            issue = 'placeholder'
        elif len(collapsed) <= 2 or collapsed.casefold() in GENERIC_TITLES:
            issue = 'generic'
        elif PRODUCER_PREFIX.match(collapsed):
            return re.sub(r'^Microsoft (?:Word|Excel|PowerPoint)\s*[-–]\s*', '', collapsed, flags=re.I), 'retained record title (producer prefix removed)', 'producer_prefix', None
        elif boilerplate:
            issue = 'boilerplate'
        if issue == 'none':
            return collapsed, 'retained record title', 'none', None
        avoid = {collapsed.casefold(), host_of(url)} | set(extra_avoid)
        if file_type == 'html':
            avoid |= self.site_lines.get(host_of(url), set())
        text = None
        if inline_text:
            text = inline_text[:16384]
        elif text_path:
            text = self.read_head(text_path, 16384).decode('utf-8', 'replace')
        markup = self.read_head(original_path, 262144) if original_path and file_type in ('html', 'xml') else b''

        def from_markup():
            heading, basis = html_heading(markup, avoid) if markup else (None, None)
            if heading:
                return heading[:200], basis, json.dumps({'source': 'saved original markup', 'sha256': original_sha, 'excerpt': heading[:120]}, ensure_ascii=False)
            return None

        def from_text(want):
            if not text:
                return None
            line, number, kind = text_heading(text, avoid)
            if line and (want == 'any' or kind == want):
                return line[:200], f'saved text {kind}', json.dumps({'source': 'saved text', 'line': number, 'sha256': text_sha, 'excerpt': line[:120]}, ensure_ascii=False)
            return None

        # Boilerplate (site-wide) titles: the page's own citation/heading line beats the site's <title>/<h1>.
        order = (lambda: from_text('citation line'), from_markup, lambda: from_text('any')) if issue == 'boilerplate' else (from_markup, lambda: from_text('any'))
        for step in order:
            found = step()
            if found:
                value, basis, evidence = found
                return value, basis, issue, evidence
        slug = deslug(url)
        if slug and slug.casefold() not in avoid:
            return slug[:200], 'de-slugged source filename', issue, json.dumps({'source': 'source URL filename', 'excerpt': slug[:120]}, ensure_ascii=False)
        value, basis = host_path(url)
        return value, basis, issue, json.dumps({'source': 'source URL', 'excerpt': value[:120]}, ensure_ascii=False)

    def repair_shared_titles(self, out):
        """Post-pass: a derived display title shared by >= 5 repaired records of a dataset is site boilerplate; re-derive it."""
        avoid = set()
        for _ in range(6):
            counter = collections.Counter((item['row']['dataset'], item['row']['display_title'].casefold()) for item in self.repaired.values())
            shared = {key for key, n in counter.items() if n >= 5}
            if not shared:
                break
            changed = 0
            for record_id, item in self.repaired.items():
                row = item['row']
                key = (row['dataset'], row['display_title'].casefold())
                if key not in shared:
                    continue
                avoid.add(key[1])
                display, basis, issue, evidence = self.title_for(*item['inputs'], extra_avoid=avoid)
                if display != row['display_title']:
                    row.update(display_title=display, title_basis=basis, title_evidence=evidence)
                    out.execute('UPDATE facets SET display_title=?, title_basis=?, title_evidence=? WHERE record_id=?', (display, basis, evidence, record_id))
                    changed += 1
            self.stats['shared_title_repairs'] += changed
            if not changed:
                break
        self.stats['shared_title_avoided'] = len(avoid)

    def validity_for(self, dataset, payload, title, original_sha, original_path, file_type, size, text_chars, has_original):
        if dataset == 'federal' and (payload.get('capture_status') == 'observed_link_only' or not has_original):
            return 'no_capture', 'observed link only; destination not captured'
        if not has_original:
            return 'no_capture', 'no saved original for this record'
        if original_sha in self.shell_full:
            validity, reason = self.shell_full[original_sha]
            share = self.hash_records.get(original_sha, 1)
            hosts = len(self.hash_hosts.get(original_sha, ()))
            return validity, f'{reason}; exact shell hash shared by {share} records across {hosts} hosts'
        if (title or '').strip().casefold() in CHALLENGE_TITLES:
            return 'challenge', f'saved title "{title.strip()}" is an access-challenge page'
        if size == 0:
            return 'empty', 'zero-byte saved original'
        if file_type == 'html' and size is not None and size < 8192 and original_path:
            head = self.read_head(original_path, 8192).decode('utf-8', 'replace')
            low = head.lower()
            if 'parked domain' in low or 'domain is parked' in low or 'this domain may be for sale' in low or 'sedoparking' in low or 'parkingcrew' in low:
                return 'parked_redirect', f'parked-domain page text; {size} bytes'
            if size < 2048 and ('window.location.href' in low or 'window.location.replace' in low or 'http-equiv="refresh"' in low or "http-equiv='refresh'" in low):
                return 'redirect_stub', f'script/meta redirect stub; {size} bytes'
            if '<frameset' in low:
                return 'redirect_stub', f'frameset shell (frame content not captured); {size} bytes'
            if 'just a moment' in low or 'attention required' in low or 'captcha' in low or 'verify you are a human' in low:
                return 'challenge', f'access-challenge markup; {size} bytes'
            for marker in ('iis windows server', 'welcome to wildfly', 'apache2 ubuntu default page', 'welcome to nginx', 'test page for the apache',
                           'error. page cannot be displayed', '404 not found', 'page not found', '403 forbidden', 'service unavailable', 'site not found'):
                if marker in low:
                    return 'error_page', f'default-server or error markup "{marker}"; {size} bytes'
            if '<title>loading' in low and text_chars < 200:
                return 'spa_shell', f'"Loading..." script shell with {text_chars} text chars; {size} bytes'
            if size < 512 and text_chars < 40:
                return 'empty', f'{size}-byte html with {text_chars} text chars'
        return 'ok', f'no shell/parked/empty indicator; {size if size is not None else "unknown"} bytes, {text_chars} text chars'

    def build_row(self, record):
        payload = loads(record['payload'], {}) or {}
        dataset, kind, url = record['dataset'], record['kind'] or '', record['source_url'] or ''
        tokens = [t.strip() for t in kind.split(';') if t.strip()]
        base_kind = next((t for t in tokens if t not in MODIFIER_TOKENS), tokens[0] if tokens else '')
        flags = [t for t in tokens if t in MODIFIER_TOKENS]
        if len([t for t in tokens if t not in MODIFIER_TOKENS]) > 1:
            flags.append('multi_kind')
        meta = payload.get('metadata') or {}
        original_path = self.file_path(record['original_id'])
        text_path = self.file_path(record['text_id'])
        has_original = 1 if record['original_id'] else 0
        inline_text = record['inline_text'] or ''
        # file type (D08)
        file_type, file_basis = 'none', 'no saved original file'
        if original_path:
            suffix = original_path.suffix.lower()
            if suffix in SUFFIX_TYPE:
                file_type, file_basis = SUFFIX_TYPE[suffix], f'saved original suffix {suffix}'
            else:
                file_type, file_basis = magic_type(self.read_head(original_path, 4096))
                file_basis = f'saved original suffix {suffix or "(none)"} not typed; {file_basis}'
        else:
            declared = meta.get('format') or payload.get('format') or payload.get('content_type') or meta.get('content_type')
            if isinstance(declared, str) and declared:
                token = declared.split(';')[0].strip().lower().rsplit('/', 1)[-1]
                token = {'plain': 'txt', 'vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx', 'msword': 'doc'}.get(token, token)
                if token in FTYPE_VALUES:
                    file_type, file_basis = token, f'payload format/content_type "{declared}" (no saved original)'
        # sizes and text
        size, bytes_basis = None, None
        if dataset == 'focused' and payload.get('raw_bytes') not in (None, ''):
            size, bytes_basis = int(payload['raw_bytes']), 'payload.raw_bytes'
        elif dataset == 'seeger' and str(meta.get('bytes') or '').isdigit():
            size, bytes_basis = int(meta['bytes']), 'metadata.bytes'
        elif dataset == 'judge_vendor' and str(payload.get('source_bytes') or '').isdigit():
            size, bytes_basis = int(payload['source_bytes']), 'payload.source_bytes'
        elif dataset == 'federal' and str(payload.get('original_http_bytes') or '').isdigit():
            size, bytes_basis = int(payload['original_http_bytes']), 'payload.original_http_bytes'
        elif original_path:
            try:
                size = os.path.getsize(original_path)
                bytes_basis = 'stat of saved original' + (' (source file shared by %d records)' % self.original_share[record['original_id']] if self.original_share[record['original_id']] > 1 else '')
            except OSError:
                size, bytes_basis = None, None
        if inline_text:
            text_chars = len(inline_text)
        elif text_path:
            try:
                text_chars = os.path.getsize(text_path)
            except OSError:
                text_chars = 0
        else:
            text_chars = 0
        has_text = 1 if text_chars > 0 else 0
        original_sha = payload.get('raw_sha256') or payload.get('sha256') or payload.get('source_sha256') or payload.get('provider_sha256') or None
        original_sha = original_sha if isinstance(original_sha, str) and re.fullmatch(r'[0-9a-f]{64}', original_sha) else None
        text_sha = payload.get('text_file_sha256') or payload.get('text_sha256') or None
        # subtype needs the URL and title; category needs the subtype only for reporting
        title = record['title'] or ''
        derived_list, category_basis = self.categories_for(dataset, base_kind, payload, url, None)
        derived = derived_list[0]
        doc_subtype, subtype_basis = self.subtype_for(dataset, base_kind, derived, title, url, payload)
        review, review_basis = self.review_for(dataset, kind, base_kind, payload, derived)
        record_type, rtype_basis = self.record_type_for(dataset, base_kind, payload, derived, doc_subtype)
        representation, repr_basis = self.representation_for(dataset, payload, file_type)
        level, jur_basis, state, states, multi, court = self.jurisdiction_for(dataset, record['state'], payload)
        dates = self.dates_for(dataset, payload)
        boilerplate = (dataset, record['title']) in self.boilerplate
        title_inputs = (dataset, title, url, text_path, inline_text, original_path, file_type, boilerplate, original_sha, text_sha)
        display_title, title_basis, title_issue, title_evidence = self.title_for(*title_inputs)
        validity, validity_reason = self.validity_for(dataset, payload, title, original_sha, original_path, file_type, size, text_chars, has_original)
        capture_flags = []
        if size == 0:
            capture_flags.append('zero_bytes')
        elif size is not None and size < 1024 and has_original:
            capture_flags.append('under_1kb')
        if has_original and not has_text:
            capture_flags.append('no_text')
        if original_sha and len(self.hash_hosts.get(original_sha, ())) > 1:
            capture_flags.append('shared_hash_multi_host')
        if dataset in ('focused', 'pending_publication') and self.original_share[record['original_id']] > 1:
            capture_flags.append('shared_original')
        if 'ocr_text' in flags or payload.get('capture_kind') == 'ocr_derivative':
            capture_flags.append('ocr')
        if representation == 'provider_capture':
            capture_flags.append('provider_capture')
        if not record['eligible']:
            capture_flags.append('ineligible')
        if multi:
            capture_flags.append('multi_state')
        publisher_status, status_basis = None, None
        if isinstance(meta.get('source_status'), str) and meta.get('source_status'):
            publisher_status, status_basis = meta['source_status'], 'metadata.source_status'
        elif str(payload.get('content_kind') or '').startswith('historical_'):
            publisher_status, status_basis = payload['content_kind'], 'payload.content_kind'
        label = self.labels.get(record['id']) or {}
        topics = label.get('topics')
        row = {
            'record_id': record['id'], 'dataset': dataset, 'group_name': record['group_name'],
            'original_kind': kind, 'base_kind': base_kind, 'kind_flags': json.dumps(flags),
            'derived_category': derived, 'categories': json.dumps(derived_list), 'category_basis': category_basis,
            'review_state': review, 'review_basis': review_basis,
            'doc_subtype': doc_subtype, 'subtype_basis': subtype_basis,
            'record_type': record_type, 'record_type_basis': rtype_basis,
            'representation': representation, 'representation_basis': repr_basis,
            'file_type': file_type, 'file_type_basis': file_basis,
            'has_original': has_original, 'has_text': has_text, 'text_chars': text_chars,
            'bytes': size, 'bytes_basis': bytes_basis, 'original_sha256': original_sha,
            'jurisdiction_level': level, 'jurisdiction_basis': jur_basis,
            'state': state, 'states': json.dumps(states, ensure_ascii=False), 'multi_state': multi, 'court_label_as_published': court,
            'publisher_status': publisher_status, 'publisher_status_basis': status_basis,
            'title': title, 'display_title': display_title, 'title_basis': title_basis, 'title_issue': title_issue, 'title_evidence': title_evidence,
            'validity': validity, 'validity_reason': validity_reason, 'capture_flags': json.dumps(capture_flags),
            'label_source': ('law_tier_20260919' if label else None), 'court_id': label.get('court_id'),
            'label_county_geoid': label.get('county_geoid'), 'law_body_class': label.get('law_body_class'),
            'rule_set': label.get('rule_set'), 'topics': (json.dumps(topics, ensure_ascii=False) if topics else None),
            'label_doc_subtype': label.get('doc_subtype'),
        }
        row.update(dates)
        if title_issue not in ('none', 'producer_prefix'):
            self.repaired[record['id']] = {'row': row, 'inputs': title_inputs}
        if validity != 'ok' and validity != 'no_capture' and record['id'] in self.counties:
            share, hosts = self.hash_records.get(original_sha, 1), len(self.hash_hosts.get(original_sha, ()))
            for geoid in self.counties[record['id']]:
                self.wrong_joins.append({'record_id': record['id'], 'geoid': geoid, 'original_sha256': original_sha,
                                         'reason': f'{validity}: {validity_reason.split(";")[0]}; raw hash shared by {share} records across {hosts} hosts'})
        return row

    # ---------- outputs ----------
    def run(self):
        started = stamp()
        tmp = HERE / 'facets.sqlite3.tmp'
        for stale in (tmp, OUT_DB):
            if stale.exists():
                stale.unlink()
        out = sqlite3.connect(tmp)
        out.executescript(schema.ddl())
        insert = schema.insert_sql()
        rows_before = collections.Counter()
        report = collections.defaultdict(collections.Counter)
        sample_pool = collections.defaultdict(list)
        total, batch = 0, []
        query = ('SELECT r.id, r.title, r.group_name, r.dataset, r.state, r.kind, r.source_url, r.payload, r.inline_text, r.original_id, r.text_id, '
                 'b.eligible FROM records r JOIN browse b ON b.id=r.id ORDER BY r.id')
        for record in self.con.execute(query):
            row = self.build_row(record)
            before = classify(record['kind'])
            rows_before[(row['dataset'], before)] += 1
            for key in ('derived_category', 'review_state', 'validity', 'file_type', 'record_type', 'jurisdiction_level', 'doc_subtype',
                        'representation', 'title_issue'):
                report[key][(row['dataset'], row[key])] += 1
            for category in json.loads(row['categories']):
                report['category_membership'][(row['dataset'], category)] += 1
            batch.append([row[name] for name in schema.COLUMN_NAMES])
            out.executemany('INSERT INTO facet_categories VALUES(?,?)', [(row['record_id'], c) for c in json.loads(row['categories'])])
            stratum = (row['dataset'], row['derived_category'], row['validity'], row['title_issue'] != 'none')
            sample_pool[stratum].append(row)
            total += 1
            if len(batch) >= 2000:
                out.executemany(insert, batch); batch = []
                print(f'  {total} rows', flush=True)
        if batch:
            out.executemany(insert, batch)
        self.repair_shared_titles(out)
        print('  shared-title post-pass:', dict(self.stats), flush=True)
        out.executemany('INSERT INTO wrong_county_joins VALUES(?,?,?,?)', [(w['record_id'], w['geoid'], w['reason'], w['original_sha256']) for w in self.wrong_joins])
        receipt_sha = sha256_file(RECEIPT) if RECEIPT.is_file() else None
        directory_records = self.con.execute('SELECT count(*) FROM records').fetchone()[0]
        meta = [('schema_version', schema.SCHEMA_VERSION), ('records', str(total)), ('built_at', stamp()), ('directory_records', str(directory_records)),
                ('directory_receipt_sha256', receipt_sha or ''), ('law_tier_folded', '1' if self.law_tier['folded'] else '0'),
                ('builder', 'sources/record_facets_20260919/build.py'), ('started_at', started)]
        out.executemany('INSERT INTO meta VALUES(?,?)', meta)
        out.commit(); out.close()
        os.replace(tmp, OUT_DB)
        self.write_side_files(rows_before, report, sample_pool, total, directory_records, receipt_sha)

    def write_side_files(self, rows_before, report, sample_pool, total, directory_records, receipt_sha):
        con = sqlite3.connect('file:' + OUT_DB.as_posix() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        # wrong joins + excluded records
        with (HERE / 'wrong_county_joins.jsonl').open('w', encoding='utf-8') as stream:
            for w in self.wrong_joins:
                stream.write(json.dumps(w, ensure_ascii=False) + '\n')
        with (HERE / 'excluded_records.jsonl').open('w', encoding='utf-8') as stream:
            for r in con.execute("SELECT record_id, dataset, validity, validity_reason, capture_flags, original_sha256, state, display_title FROM facets WHERE validity!='ok' ORDER BY record_id"):
                stream.write(json.dumps(dict(r), ensure_ascii=False) + '\n')
        # category report
        datasets = sorted({d for d, _ in rows_before} | {d for d, _ in report['derived_category']})
        def table(counter, values):
            return {d: {v: counter.get((d, v), 0) for v in values if counter.get((d, v), 0)} for d in datasets}
        category_report = {
            'built_at': stamp(), 'records': total,
            'before_classify_kind_by_dataset': table(rows_before, CATEGORY_VALUES),
            'after_derived_category_by_dataset': table(report['derived_category'], CATEGORY_VALUES),
            'after_category_membership_by_dataset': table(report['category_membership'], CATEGORY_VALUES),
            'before_total': {v: sum(n for (d, c), n in rows_before.items() if c == v) for v in CATEGORY_VALUES},
            'after_total': {v: sum(n for (d, c), n in report['derived_category'].items() if c == v) for v in CATEGORY_VALUES},
            'review_state_by_dataset': table(report['review_state'], REVIEW_VALUES),
            'validity_by_dataset': table(report['validity'], VALIDITY_VALUES),
            'file_type_by_dataset': table(report['file_type'], FTYPE_VALUES),
            'record_type_by_dataset': table(report['record_type'], RTYPE_VALUES),
            'jurisdiction_level_by_dataset': table(report['jurisdiction_level'], JUR_VALUES),
            'doc_subtype_by_dataset': table(report['doc_subtype'], SUBTYPE_VALUES),
            'representation_by_dataset': table(report['representation'], DTYPE_VALUES),
            'title_issue_by_dataset': table(report['title_issue'], ('none', 'url', 'placeholder', 'generic', 'boilerplate', 'producer_prefix', 'empty')),
            'wrong_county_joins': len(self.wrong_joins),
            'seeger_catchall_subtypes': dict(con.execute("SELECT doc_subtype, count(*) FROM facets WHERE dataset='seeger' AND original_kind='court_form_or_other_document' GROUP BY 1").fetchall()),
            'seeger_catchall_moved_to_rules': con.execute("SELECT count(*) FROM facets WHERE dataset='seeger' AND original_kind='court_form_or_other_document' AND derived_category='rules'").fetchone()[0],
            'trellis_state_rules_by_category': dict(con.execute("SELECT derived_category, count(*) FROM facets WHERE dataset='focused' AND category_basis LIKE '%Trellis state-rules%' GROUP BY 1").fetchall()),
            'law_tier': self.law_tier,
        }
        (HERE / 'category_report.json').write_text(json.dumps(category_report, ensure_ascii=False, indent=1), encoding='utf-8')
        lines = ['# Category report - record_facets_20260919', '', f'Records: {total}. Built {category_report["built_at"]}.', '',
                 '## Before (classify(records.kind), what the server shows today) vs after (derived_category) per dataset', '',
                 '| dataset | category | before | after | membership |', '|---|---|---:|---:|---:|']
        for d in datasets:
            for v in CATEGORY_VALUES:
                b, a, m = rows_before.get((d, v), 0), report['derived_category'].get((d, v), 0), report['category_membership'].get((d, v), 0)
                if b or a or m:
                    lines.append(f'| {d} | {v} | {b} | {a} | {m} |')
        lines += ['', '## Totals', '', '| category | before | after |', '|---|---:|---:|']
        for v in CATEGORY_VALUES:
            lines.append(f'| {v} | {category_report["before_total"][v]} | {category_report["after_total"][v]} |')
        for key, values in (('review_state', REVIEW_VALUES), ('validity', VALIDITY_VALUES), ('jurisdiction_level', JUR_VALUES), ('file_type', FTYPE_VALUES),
                            ('record_type', RTYPE_VALUES), ('doc_subtype', SUBTYPE_VALUES)):
            lines += ['', f'## {key} per dataset', '', '| dataset | value | count |', '|---|---|---:|']
            for d in datasets:
                for v in values:
                    n = report[key].get((d, v), 0)
                    if n:
                        lines.append(f'| {d} | {v} | {n} |')
        lines += ['', f'Wrong county joins (shell captures): {len(self.wrong_joins)}',
                  f'Seeger catch-all moved to rules by URL path evidence: {category_report["seeger_catchall_moved_to_rules"]}',
                  f'Trellis state-rules by URL segment: {category_report["trellis_state_rules_by_category"]}',
                  f'law_tier labels folded: {self.law_tier["folded"]} ({self.law_tier["reason"] or "ok"})', '']
        (HERE / 'category_report.md').write_text('\n'.join(lines), encoding='utf-8')
        # stratified sample of 200
        rng = random.Random(20260919)
        strata = sorted(sample_pool, key=lambda k: (-len(sample_pool[k]), k))
        picks = []
        quota = {k: 1 for k in strata}
        remaining = 200 - len(strata)
        weights = sum(len(sample_pool[k]) for k in strata)
        for k in strata:
            if remaining <= 0:
                break
            extra = min(len(sample_pool[k]) - 1, max(0, round(remaining * len(sample_pool[k]) / weights)))
            quota[k] += extra
        chosen = {k: rng.sample(sample_pool[k], min(quota[k], len(sample_pool[k]))) for k in strata}
        picks = [row for k in strata for row in chosen[k]]
        for k in strata:  # top up from the largest strata so the sample is exactly 200 rows
            while len(picks) < 200 and len(chosen[k]) < len(sample_pool[k]):
                extra = rng.choice([r for r in sample_pool[k] if r not in chosen[k]])
                chosen[k].append(extra); picks.append(extra)
        picks = picks[:200]
        keep = ('record_id', 'dataset', 'original_kind', 'derived_category', 'categories', 'category_basis', 'review_state', 'review_basis', 'doc_subtype',
                'subtype_basis', 'record_type', 'record_type_basis', 'representation', 'file_type', 'file_type_basis', 'jurisdiction_level', 'jurisdiction_basis',
                'state', 'states', 'multi_state', 'court_label_as_published', 'saved_at', 'saved_at_basis', 'source_as_of', 'source_as_of_basis', 'effective_from',
                'effective_from_basis', 'publisher_status', 'title', 'display_title', 'title_basis', 'title_issue', 'title_evidence', 'validity', 'validity_reason',
                'capture_flags', 'bytes', 'text_chars')
        with (HERE / 'sample_200.jsonl').open('w', encoding='utf-8') as stream:
            for row in sorted(picks, key=lambda r: (r['dataset'], r['derived_category'], r['record_id'])):
                stream.write(json.dumps({k: row[k] for k in keep}, ensure_ascii=False) + '\n')
        # validation
        checks = self.checks(con, total, directory_records)
        counts = {
            'records': total, 'directory_records': directory_records, 'facet_categories': con.execute('SELECT count(*) FROM facet_categories').fetchone()[0],
            'wrong_county_joins': len(self.wrong_joins), 'excluded_records': con.execute("SELECT count(*) FROM facets WHERE validity!='ok'").fetchone()[0],
            'derived_category': dict(con.execute('SELECT derived_category, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'review_state': dict(con.execute('SELECT review_state, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'validity': dict(con.execute('SELECT validity, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'file_type': dict(con.execute('SELECT file_type, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'record_type': dict(con.execute('SELECT record_type, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'jurisdiction_level': dict(con.execute('SELECT jurisdiction_level, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'multi_state': con.execute('SELECT count(*) FROM facets WHERE multi_state=1').fetchone()[0],
            'title_issue': dict(con.execute('SELECT title_issue, count(*) FROM facets GROUP BY 1 ORDER BY 2 DESC').fetchall()),
            'dates_known': {name: con.execute(f'SELECT count(*) FROM facets WHERE {column} IS NOT NULL').fetchone()[0]
                            for name, (_, _, column) in schema.DATE_TYPES.items()},
            'label_rows': con.execute('SELECT count(*) FROM facets WHERE label_source IS NOT NULL').fetchone()[0],
            'sample_rows': len(picks),
        }
        con.close()
        passed = all(c['passed'] for c in checks)
        inputs = [{'path': 'delivery/archive-directory/build_receipt.json', 'sha256': receipt_sha, 'note': 'receipt of the 1.3 GB directory.sqlite3 (records=58310); the database itself is read-only input and not hashed'},
                  {'path': 'sources/record_facets_20260919/schema.py', 'sha256': sha256_file(HERE / 'schema.py')},
                  {'path': 'sources/record_facets_20260919/build.py', 'sha256': sha256_file(Path(__file__))},
                  {'path': 'delivery/archive-directory/categories.py', 'sha256': sha256_file(ADAPTER_DIR / 'categories.py')},
                  {'path': 'delivery/archive-directory/evidence_dates.py', 'sha256': sha256_file(ADAPTER_DIR / 'evidence_dates.py')}]
        if self.law_tier['folded']:
            inputs.append({'path': self.law_tier['path'], 'sha256': self.law_tier['sha256']})
            if self.law_tier.get('topics_sha256'):
                inputs.append({'path': self.law_tier['topics_path'], 'sha256': self.law_tier['topics_sha256']})
            inputs.append({'path': 'sources/law_tier_20260919/validation.json', 'sha256': self.law_tier['validation_sha256']})
        validation = {
            'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': passed, 'validated_at': stamp(),
            'data_files': [{'path': 'facets.sqlite3', 'sha256': sha256_file(OUT_DB), 'rows': total}],
            'counts': counts, 'checks': checks, 'qualification': QUALIFICATION, 'license_ref': LICENSE_REF, 'inputs': inputs,
            'law_tier': self.law_tier,
        }
        (HERE / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=1), encoding='utf-8')
        print('status', validation['status'], 'ready', validation['ready'], 'rows', total)
        for c in checks:
            if not c['passed']:
                print('FAILED CHECK', c)

    def checks(self, con, total, directory_records):
        def one(sql):
            return con.execute(sql).fetchone()[0]
        checks = []
        def add(name, passed, detail):
            checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
        add('row_count_matches_directory', total == directory_records == 58310, f'facets {total}, directory {directory_records}')
        add('meta_records_matches_rows', int(dict(con.execute('SELECT key, value FROM meta'))['records']) == total, 'meta.records == count(*)')
        add('record_ids_unique', one('SELECT count(DISTINCT record_id) FROM facets') == total, 'primary key')
        for column, values in (('derived_category', CATEGORY_VALUES), ('review_state', REVIEW_VALUES), ('record_type', RTYPE_VALUES), ('file_type', FTYPE_VALUES),
                               ('jurisdiction_level', JUR_VALUES), ('validity', VALIDITY_VALUES), ('representation', DTYPE_VALUES)):
            bad = one(f"SELECT count(*) FROM facets WHERE {column} IS NULL OR {column} NOT IN ({','.join(repr(v) for v in values)})")
            add(f'{column}_in_allowlist', bad == 0, f'{bad} rows outside allowlist')
        bad = one(f"SELECT count(*) FROM facets WHERE doc_subtype IS NOT NULL AND doc_subtype NOT IN ({','.join(repr(v) for v in SUBTYPE_VALUES)})")
        add('doc_subtype_in_allowlist', bad == 0, f'{bad} rows outside allowlist')
        for value, basis in (('derived_category', 'category_basis'), ('review_state', 'review_basis'), ('doc_subtype', 'subtype_basis'), ('record_type', 'record_type_basis'),
                             ('representation', 'representation_basis'), ('file_type', 'file_type_basis'), ('jurisdiction_level', 'jurisdiction_basis'),
                             ('saved_at', 'saved_at_basis'), ('captured_at', 'captured_at_basis'), ('source_as_of', 'source_as_of_basis'), ('published_at', 'published_at_basis'),
                             ('effective_from', 'effective_from_basis'), ('publisher_status', 'publisher_status_basis'), ('display_title', 'title_basis'),
                             ('validity', 'validity_reason'), ('bytes', 'bytes_basis')):
            bad = one(f"SELECT count(*) FROM facets WHERE {value} IS NOT NULL AND ({basis} IS NULL OR {basis}='')")
            add(f'{value}_has_basis', bad == 0, f'{bad} rows without basis')
        bad = one("SELECT count(*) FROM facets WHERE multi_state=1 AND (state IS NOT NULL OR json_array_length(states)<2)")
        add('multi_state_rows_have_no_single_state', bad == 0, f'{bad} multi-state rows with a single state')
        bad = one("SELECT count(*) FROM facets WHERE state='Federal' AND jurisdiction_basis NOT LIKE '%ederal%'")
        add('state_federal_only_from_record_metadata', bad == 0, f'{bad} rows labelled Federal without federal evidence in basis')
        for lo, hi, value in schema.DATE_TYPES.values():
            bad = one(f"SELECT count(*) FROM facets WHERE ({value} IS NULL) != ({lo} IS NULL) OR ({value} IS NULL) != ({hi} IS NULL) OR {lo}>{hi}")
            add(f'{value}_bounds_consistent', bad == 0, f'{bad} rows with inconsistent lo/hi')
        bad = one("SELECT count(*) FROM facets WHERE saved_at_basis LIKE '%Last-Modified%' OR source_as_of_basis LIKE '%Last-Modified%' OR effective_from_basis LIKE '%Last-Modified%'")
        add('http_last_modified_never_used', bad == 0, f'{bad} rows')
        bad = one("SELECT count(*) FROM facets f WHERE NOT EXISTS (SELECT 1 FROM facet_categories c WHERE c.record_id=f.record_id AND c.category=f.derived_category)")
        add('facet_categories_contains_primary', bad == 0, f'{bad} rows missing their primary membership')
        root = ROOT.as_posix()
        bad = one(f"SELECT count(*) FROM facets WHERE title_evidence LIKE '%{root}%' OR title_evidence LIKE '%/raw/%' OR title_evidence LIKE '%/text/%' OR category_basis LIKE '%sqlite3%'")
        add('public_columns_path_free', bad == 0, f'{bad} rows with path-like evidence')
        bad = one("SELECT count(*) FROM facets WHERE display_title IS NULL OR trim(display_title)=''")
        add('display_title_never_empty', bad == 0, f'{bad} empty display titles')
        bad = one("SELECT count(*) FROM facets WHERE title_issue IN ('url','placeholder','generic','boilerplate','empty') AND title_evidence IS NULL")
        add('repaired_titles_carry_evidence', bad == 0, f'{bad} repaired titles without evidence')
        bad = one("SELECT count(*) FROM facets WHERE derived_category='other' AND dataset='focused' AND original_kind IN ('needs_content_review','law_document_title_evidence_needs_review') AND category_basis NOT LIKE 'classify(kind) fallback%'")
        blank = one("SELECT count(*) FROM facets WHERE derived_category='other' AND dataset='focused' AND original_kind IN ('needs_content_review','law_document_title_evidence_needs_review')")
        add('review_states_not_used_as_category', bad == 0, f'{bad} review-state records categorised as other without an explicit fallback basis (D01); {blank} records have a blank retained category and stay other by the documented fallback')
        bad = one("SELECT count(*) FROM facets WHERE dataset LIKE 'judge%' AND derived_category!='judges'")
        add('judge_datasets_in_judges', bad == 0, f'{bad} judge records outside judges (D10)')
        wrong = con.execute('SELECT record_id, geoid FROM wrong_county_joins').fetchall()
        missing = [w for w in wrong if w[1] not in self.counties.get(w[0], [])]
        add('wrong_joins_exist_in_record_counties', not missing, f'{len(missing)} rows not in record_counties')
        bad = one("SELECT count(*) FROM wrong_county_joins w JOIN facets f ON f.record_id=w.record_id WHERE f.validity='ok'")
        add('wrong_joins_only_for_invalid_captures', bad == 0, f'{bad} joins on valid captures')
        shell = one("SELECT count(*) FROM facets WHERE original_sha256='6dc9c7fc93bb488bb0520a6c780a8d3c0fb5486a4711aca49b4c53fac7393023' AND validity='parked_redirect'")
        add('lander_shell_hash_marked_parked', shell >= 89, f'{shell} records with the /lander shell hash marked parked_redirect (D06)')
        add('law_tier_labels', True, ('folded: ' + self.law_tier['path']) if self.law_tier['folded'] else f"not folded ({self.law_tier['reason']}); label_* columns null")
        return checks


def main():
    if not DIRECTORY.is_file():
        raise SystemExit(f'missing {DIRECTORY}')
    builder = Builder()
    print('law_tier:', builder.law_tier)
    builder.run()
    builder.con.close()


if __name__ == '__main__':
    main()
