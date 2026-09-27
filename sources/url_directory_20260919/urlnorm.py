"""Shared URL normalisation and row schema for the unified URL directory (2026-09-19).

Every normaliser imports this module so that de-duplication keys, document kinds and noise flags are
identical across source lists. Deterministic, offline, stdlib only. URLs are data, never fetched here.
"""
from __future__ import annotations

import re
import urllib.parse

# Fields every normalised row must carry (None allowed unless noted). Keep source wording; never invent.
FIELDS = (
    'url',              # exact URL as the source list printed it (required)
    'url_key',          # de-duplication key from url_key() (required)
    'host',             # lower-case host without port (required)
    'source_list',      # short stable name of the list this row came from (required)
    'source_file',      # path of the input file (required)
    'layer',            # one of LAYERS (required)
    'jurisdiction_level',  # federal | state | county | municipal | tribal | unknown
    'state',            # USPS code only when the source row states or tags the state
    'county_fips',      # 5-digit FIPS only when the source row gives county + state that map exactly
    'org_name',         # agency / court / publisher name exactly as the source gives it
    'doc_kind',         # from doc_kind()
    'title',            # link text / page title from the source, else None
    'topics',           # list of the source's own tags (may be empty)
    'parent_url',       # page the URL was found on, when the source records it
    'source_date',      # ISO date the source list recorded for this row (crawl / discovery date), else None
    'source_status',    # HTTP status or fetch status recorded by the source, else None
    'noise',            # list of noise flags from noise_flags()
)
LAYERS = (
    'federal_agency', 'state_agency', 'federal_court', 'state_court', 'county_court', 'local_government',
    'doj_directory', 'science_toxicology', 'legislature_law', 'regulatory_text', 'other',
)
_TRACKING = re.compile(r'^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|_ga|_gl|ref|source)$', re.I)
_DOC = {
    'pdf': 'pdf', 'doc': 'word', 'docx': 'word', 'rtf': 'word', 'odt': 'word', 'xls': 'spreadsheet', 'xlsx': 'spreadsheet',
    'csv': 'data', 'json': 'data', 'xml': 'data', 'zip': 'archive', 'gz': 'archive', 'ppt': 'slides', 'pptx': 'slides',
    'txt': 'text', 'jpg': 'image', 'jpeg': 'image', 'png': 'image', 'gif': 'image', 'mp4': 'media', 'mp3': 'media',
}
_SOCIAL = re.compile(r'(^|\.)(facebook|twitter|x|linkedin|instagram|youtube|youtu|pinterest|reddit|tiktok|addtoany|sharethis)\.(com|be)$', re.I)
_CDN = re.compile(r'(akamai|akamaitech|cloudfront|edgekey|edgesuite|fastly|azureedge|googleusercontent|gstatic|doubleclick)\.', re.I)
_ACTION = re.compile(r'(^|/)(login|logout|signin|sign-in|register|cart|checkout|subscribe|unsubscribe|share|print|email)(/|$|\.)', re.I)
_CALENDAR = re.compile(r'(calendar\.aspx|/calendar/|/events?/|[?&](eid|month|year)=)', re.I)


def split(url: str):
    """Return urlsplit result for an http(s) URL, else None. Never raises."""
    try:
        parts = urllib.parse.urlsplit((url or '').strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return None
    return parts


def host(url: str):
    parts = split(url)
    return parts.hostname.lower().rstrip('.') if parts else None


def url_key(url: str):
    """De-duplication key: scheme-insensitive, lower-case host without leading www., no fragment, no tracking
    parameters, remaining query parameters kept in their original order, single trailing slash removed.
    Paths stay case-sensitive. Two URLs with the same key are treated as the same address, nothing more."""
    parts = split(url)
    if not parts:
        return None
    name = parts.hostname.lower().rstrip('.')
    if name.startswith('www.'):
        name = name[4:]
    port = f':{parts.port}' if parts.port and parts.port not in (80, 443) else ''
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = parts.path or '/'
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    return name + port + path + (('?' + urllib.parse.urlencode(query)) if query else '')


def doc_kind(url: str, hinted: str | None = None):
    """'page' unless the path extension (or an explicit source hint such as a MIME type) says otherwise."""
    hint = (hinted or '').lower()
    for token, kind in (('pdf', 'pdf'), ('msword', 'word'), ('wordprocessing', 'word'), ('spreadsheet', 'spreadsheet'), ('excel', 'spreadsheet')):
        if token in hint:
            return kind
    parts = split(url)
    if not parts:
        return 'other'
    match = re.search(r'\.([A-Za-z0-9]{2,5})$', parts.path)
    if match:
        return _DOC.get(match.group(1).lower(), 'page')
    return 'page'


def noise_flags(url: str):
    parts = split(url)
    if not parts:
        return ['not_http_url']
    flags = []
    name = parts.hostname.lower()
    if _SOCIAL.search(name):
        flags.append('social_share')
    if _CDN.search(name + '.'):
        flags.append('cdn_or_tracker_host')
    if _ACTION.search(parts.path):
        flags.append('action_page')
    if _CALENDAR.search(parts.path + ('?' + parts.query if parts.query else '')):
        flags.append('calendar_or_event')
    if any(_TRACKING.match(k) for k, _ in urllib.parse.parse_qsl(parts.query)):
        flags.append('tracking_parameters')
    return flags


def make_row(**values):
    """Build a schema-complete row; raises ValueError on a missing required field or unknown layer."""
    row = {field: values.get(field) for field in FIELDS}
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise ValueError('unknown fields: ' + ', '.join(sorted(unknown)))
    row['host'] = row['host'] or host(row['url'])
    row['url_key'] = row['url_key'] or url_key(row['url'])
    row['doc_kind'] = row['doc_kind'] or doc_kind(row['url'])
    row['noise'] = row['noise'] if row['noise'] is not None else noise_flags(row['url'])
    row['topics'] = list(row['topics'] or [])
    for field in ('url', 'url_key', 'host', 'source_list', 'source_file', 'layer'):
        if not row[field]:
            raise ValueError('missing ' + field)
    if row['layer'] not in LAYERS:
        raise ValueError('unknown layer ' + str(row['layer']))
    if row['state'] is not None and not re.fullmatch(r'[A-Z]{2}', row['state']):
        raise ValueError('state must be a USPS code')
    if row['county_fips'] is not None and not re.fullmatch(r'\d{5}', row['county_fips']):
        raise ValueError('county_fips must be 5 digits')
    return row
