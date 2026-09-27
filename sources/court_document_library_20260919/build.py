"""Build sources/court_document_library_20260919/index.sqlite3 from the two hash-manifested local
court-document stores under SW-BULK (never modified, never copied into SCRAPE). Deterministic,
streaming, re-runnable. See README.md for the counts, the doc_type rules and their limits.

Inputs (read-only):
  SW-BULK/publiclaw_registry_v2/staging/court_expansion_file_download_v2_4_2026-08-20/wave_*/court_file_download_results_v2.jsonl
  SW-BULK/publiclaw_registry_v2/staging/state_trial_court_acquisition_v1_2026-08-20/artifact_download_results_v1.jsonl
  SW-BULK/publiclaw_registry_v2/staging/court_expansion_corpus_v2_4_2026-08-20/court_expansion_file_url_corpus_v2.jsonl.gz  (source_families context)
  SW-BULK/publiclaw_registry_v2/staging/court_expansion_file_preflight_v2_4_2026-08-20/court_file_preflight_results_v2.jsonl (source_families context)
  SCRAPE/sources/court_spine_20260919/courts.jsonl  (court join)
  sources/court_document_library_20260919/titles.jsonl  (optional; written by extract_titles.py)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
SW_BULK = Path('C:/Users/firas/Downloads/SW-BULK')
STAGING = SW_BULK / 'publiclaw_registry_v2/staging'
COURT_EXPANSION_DIR = STAGING / 'court_expansion_file_download_v2_4_2026-08-20'
STATE_TRIAL_DIR = STAGING / 'state_trial_court_acquisition_v1_2026-08-20'
URL_CORPUS_FILE = STAGING / 'court_expansion_corpus_v2_4_2026-08-20/court_expansion_file_url_corpus_v2.jsonl.gz'
PREFLIGHT_FILE = STAGING / 'court_expansion_file_preflight_v2_4_2026-08-20/court_file_preflight_results_v2.jsonl'
COURT_SPINE_FILE = ROOT / 'sources/court_spine_20260919/courts.jsonl'

DATA_DIR = Path(__file__).resolve().parent
DB_PATH = DATA_DIR / 'index.sqlite3'
TITLES_PATH = DATA_DIR / 'titles.jsonl'
VALIDATION_PATH = DATA_DIR / 'validation.json'

MANIFEST_COURT_EXPANSION = 'court_expansion_file_download_v2_4_2026-08-20'
MANIFEST_STATE_TRIAL = 'state_trial_court_acquisition_v1_2026-08-20'

# ---------------------------------------------------------------------------
# doc_type: deterministic classification. Priority 1 is the source pipeline's
# own `source_families` classification (from the preflight / url-corpus files,
# joined by the artifact hash), mapped into our eight buckets. Priority 2 is a
# regex fallback over the URL + local filename when no usable family is present.
# Never look at PDF metadata author fields (BRIEF/contract rule); this module
# never opens a document byte.
# ---------------------------------------------------------------------------

COURT_FORM = 'court_form'
LOCAL_RULE = 'local_rule'
STANDING_OR_GENERAL_ORDER = 'standing_or_general_order'
FEE_SCHEDULE = 'fee_schedule'
CALENDAR_NOTICE = 'calendar_notice'
OPINION = 'opinion'
ADMINISTRATIVE_FINANCIAL = 'administrative_financial'
OTHER_UNKNOWN = 'other_unknown'

DOC_TYPES = (COURT_FORM, LOCAL_RULE, STANDING_OR_GENERAL_ORDER, FEE_SCHEDULE, CALENDAR_NOTICE,
             OPINION, ADMINISTRATIVE_FINANCIAL, OTHER_UNKNOWN)

# Source-pipeline family -> our bucket. Families not listed here carry no signal for our facet
# (e.g. "judges_justices_magistrates_and_chambers", "clerks_offices_staff_and_administration")
# and fall through to the regex fallback or other_unknown.
FAMILY_TO_TYPE = {
    'fees_costs_payments_and_fee_schedules': FEE_SCHEDULE,
    'orders_administrative_orders_and_notices': STANDING_OR_GENERAL_ORDER,
    'rules_local_rules_and_procedures': LOCAL_RULE,
    'forms_instructions_and_filing_packets': COURT_FORM,
    'opinions_decisions_and_tentative_rulings': OPINION,
    'calendars_dockets_hearings_and_oral_argument': CALENDAR_NOTICE,
    'news_closures_holidays_and_operational_status': CALENDAR_NOTICE,
    'procurement_budget_employment_and_governance': ADMINISTRATIVE_FINANCIAL,
    'statistics_reports_data_api_and_bulk': ADMINISTRATIVE_FINANCIAL,
}
# When a row carries more than one mapped family, resolve with this fixed priority
# (most specific / most useful to a litigator first).
FAMILY_PRIORITY = [FEE_SCHEDULE, STANDING_OR_GENERAL_ORDER, LOCAL_RULE, COURT_FORM, OPINION,
                    CALENDAR_NOTICE, ADMINISTRATIVE_FINANCIAL]

# Fallback regexes over "<url path> <filename>", lowercased, tried in this fixed order.
_FALLBACK_RULES = [
    (FEE_SCHEDULE, re.compile(r'fee[-_ ]?schedule|filing[-_ ]?fees?|cost[-_ ]?schedule')),
    (STANDING_OR_GENERAL_ORDER, re.compile(r'standing[-_ ]?order|general[-_ ]?order|administrative[-_ ]?order|\badmorder\b|\bgo[-_]?\d')),
    (LOCAL_RULE, re.compile(r'local[-_ ]?rules?|rules?[-_ ]?of[-_ ]?(procedure|court)|\block[-_ ]?rule|\blr[-_]?\d')),
    (COURT_FORM, re.compile(r'\bforms?\b|petition|instructions?|packet|\bao[-_]?0?\d{2,3}\b')),
    (OPINION, re.compile(r'\bopinion|decision|ruling|memorandum[-_ ]?opinion|tentative[-_ ]?ruling')),
    (CALENDAR_NOTICE, re.compile(r'calendar|docket|hearing|\bnotice\b|closure|holiday')),
    (ADMINISTRATIVE_FINANCIAL, re.compile(r'budget|procurement|annual[-_ ]?report|financial|statistics|employment')),
]


def classify_doc_type(url, filename, source_families):
    """Return (doc_type, basis). Pure and deterministic; no network, no metadata, never fabricates."""
    families = [f for f in (source_families or []) if isinstance(f, str)]
    mapped = {FAMILY_TO_TYPE[f] for f in families if f in FAMILY_TO_TYPE}
    if mapped:
        for candidate in FAMILY_PRIORITY:
            if candidate in mapped:
                return candidate, 'source_families:' + candidate
    haystack = ((url or '') + ' ' + (filename or '')).casefold()
    for doc_type, pattern in _FALLBACK_RULES:
        if pattern.search(haystack):
            return doc_type, 'filename_url_pattern:' + doc_type
    return OTHER_UNKNOWN, 'no_family_no_pattern_match'


# ---------------------------------------------------------------------------
# Court join: exact website host -> court_spine.id, ONLY when the host maps to
# exactly one court. Every multi-court host (the 17-ish shared *.uscourts.gov
# domains and every state-judiciary domain used by many courts) is 'ambiguous'
# with every candidate id listed. A host absent from court_spine, or the bare
# national uscourts.gov placeholder, is 'none'. Never guessed.
# ---------------------------------------------------------------------------

def _normalize_host(host):
    if not host:
        return None
    host = host.strip().lower()
    if host.startswith('www.'):
        host = host[4:]
    return host or None


def build_host_map(courts):
    """host -> sorted list of court_spine ids whose recorded website resolves to that host."""
    host_map = {}
    for court in courts:
        host = _normalize_host(urlsplit(court.get('website') or '').hostname)
        if not host or host == 'uscourts.gov':
            continue  # bare national placeholder is not evidence of any specific court (9 defunct rows)
        host_map.setdefault(host, set()).add(court['id'])
    return {host: sorted(ids) for host, ids in host_map.items()}


def build_id_to_state(courts):
    """court_spine id -> its own native `state` field (native id join; never name matching). Repair
    review defect: for_state was under-populated because it relied only on the manifest's
    jurisdiction_codes column, missing 8,099 rows that are link_status='matched' to a named court that
    itself carries a state. This gives normalized_row a second, independent basis for state."""
    out = {}
    for court in courts:
        state = court.get('state')
        if isinstance(state, str) and state.upper() in VALID_USPS:
            out[court['id']] = state.upper()
    return out


def classify_link(host_map, host):
    """Return (link_status, court_id_or_None, candidate_ids_list)."""
    host = _normalize_host(host)
    if not host:
        return 'none', None, []
    ids = host_map.get(host)
    if not ids:
        return 'none', None, []
    if len(ids) == 1:
        return 'matched', ids[0], []
    return 'ambiguous', None, ids


# ---------------------------------------------------------------------------
# Path handling: local_path values in the manifests are Windows paths, some
# relative to `staging/`, some absolute. We store only a forward-slash path
# relative to STAGING, and never store or expose an absolute filesystem path.
# ---------------------------------------------------------------------------

def relative_to_staging(local_path):
    if not isinstance(local_path, str) or not local_path:
        return None
    normalized = local_path.replace('\\', '/')
    staging_str = str(STAGING).replace('\\', '/')
    if normalized.lower().startswith(staging_str.lower() + '/'):
        return normalized[len(staging_str) + 1:]
    if normalized.lower().startswith('staging/'):
        return normalized[len('staging/'):]
    if ':' in normalized.split('/', 1)[0]:
        return None  # some other absolute path outside the declared root; refuse rather than guess
    return normalized.lstrip('/')


def filename_of(url_or_path):
    if not url_or_path:
        return ''
    tail = url_or_path.replace('\\', '/').rsplit('/', 1)[-1]
    tail = tail.split('?', 1)[0].split('#', 1)[0]
    try:
        tail = unquote(tail)
    except Exception:
        pass
    return unicodedata.normalize('NFC', tail)


def extension_of(filename):
    if not filename or '.' not in filename:
        return ''
    return filename.rsplit('.', 1)[-1].lower()[:12]


VALID_USPS = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY',
    'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND',
    'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC',
    'PR', 'GU', 'VI', 'AS', 'MP',
}


def state_of(jurisdiction_codes):
    codes = [c.upper() for c in (jurisdiction_codes or []) if isinstance(c, str) and c]
    distinct = sorted(set(codes))
    if len(distinct) == 1 and distinct[0] in VALID_USPS:
        return distinct[0]
    return None


# ---------------------------------------------------------------------------
# Loading small support files
# ---------------------------------------------------------------------------

def load_courts():
    rows = []
    with COURT_SPINE_FILE.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _hash_suffix(node_id):
    if not isinstance(node_id, str) or ':' not in node_id:
        return None
    return node_id.rsplit(':', 1)[-1]


def load_source_families():
    """hash suffix (shared by artifact_id / url_node_id) -> list[str] of source_families.
    Built from the url-corpus file first, then filled in from the preflight file for any
    hash the corpus does not carry. Both are source-pipeline evidence, never fetched here."""
    families = {}
    if URL_CORPUS_FILE.exists():
        with gzip.open(URL_CORPUS_FILE, 'rt', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = _hash_suffix(row.get('url_node_id'))
                fams = row.get('source_families')
                if key and fams:
                    families[key] = fams
    if PREFLIGHT_FILE.exists():
        with PREFLIGHT_FILE.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = _hash_suffix(row.get('artifact_id')) or _hash_suffix(row.get('url_node_id'))
                fams = row.get('source_families')
                if key and fams and key not in families:
                    families[key] = fams
    return families


def load_titles():
    """sha256 -> {'title':..., 'text_extracted': bool} from extract_titles.py's output, if present."""
    titles = {}
    if TITLES_PATH.exists():
        with TITLES_PATH.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                sha = row.get('sha256')
                if sha and row.get('title'):
                    titles[sha] = row
    return titles


# ---------------------------------------------------------------------------
# Row-level normalization
# ---------------------------------------------------------------------------

def iter_manifest_rows(path):
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def normalized_row(raw, manifest, wave, host_map, families, id_to_state=None):
    if raw.get('download_status') != 'downloaded':
        return None
    artifact_id = raw.get('artifact_id')
    sha256 = raw.get('sha256')
    if not artifact_id or not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', sha256 or ''):
        return None
    final_url = raw.get('final_url') or raw.get('source_url') or ''
    host = urlsplit(final_url).hostname or urlsplit(raw.get('source_url') or '').hostname
    rel_path = relative_to_staging(raw.get('local_path'))
    filename = filename_of(raw.get('source_url') or final_url)
    fam_key = _hash_suffix(artifact_id)
    fams = families.get(fam_key) or []
    doc_type, basis = classify_doc_type(raw.get('source_url') or final_url, filename, fams)
    link_status, court_id, candidates = classify_link(host_map, host)
    court_state = (id_to_state or {}).get(court_id) if link_status == 'matched' else None
    return {
        'id': artifact_id,
        'manifest': manifest,
        'wave': wave,
        'source_url': raw.get('source_url'),
        'final_url': final_url or None,
        'host': _normalize_host(host),
        'filename': filename or None,
        'local_rel_path': rel_path,
        'sha256': sha256,
        'bytes': raw.get('bytes'),
        'content_type': raw.get('content_type'),
        'extension': extension_of(filename) or (raw.get('file_extension') or '').lstrip('.').lower() or None,
        'download_status': raw.get('download_status'),
        'http_status': raw.get('http_status'),
        'started_at': raw.get('started_at'),
        'completed_at': raw.get('completed_at'),
        'jurisdiction_codes': json.dumps(raw.get('jurisdiction_codes') or []),
        'authority_lanes': json.dumps(raw.get('authority_lanes') or []),
        'state': state_of(raw.get('jurisdiction_codes')),
        'court_state': court_state,
        'doc_type': doc_type,
        'doc_type_basis': basis,
        'link_status': link_status,
        'court_id': court_id,
        'candidate_court_ids': json.dumps(candidates),
        'title': None,
        'text_extracted': 0,
    }


SCHEMA = """
CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  manifest TEXT NOT NULL,
  wave TEXT,
  source_url TEXT,
  final_url TEXT,
  host TEXT,
  filename TEXT,
  local_rel_path TEXT,
  sha256 TEXT NOT NULL,
  bytes INTEGER,
  content_type TEXT,
  extension TEXT,
  download_status TEXT,
  http_status INTEGER,
  started_at TEXT,
  completed_at TEXT,
  jurisdiction_codes TEXT NOT NULL,
  authority_lanes TEXT NOT NULL,
  state TEXT,
  court_state TEXT,
  doc_type TEXT NOT NULL,
  doc_type_basis TEXT NOT NULL,
  link_status TEXT NOT NULL,
  court_id TEXT,
  candidate_court_ids TEXT NOT NULL,
  title TEXT,
  text_extracted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_documents_sha256 ON documents(sha256);
CREATE INDEX idx_documents_state ON documents(state);
CREATE INDEX idx_documents_court_state ON documents(court_state);
CREATE INDEX idx_documents_court ON documents(court_id);
CREATE INDEX idx_documents_doc_type ON documents(doc_type);
CREATE INDEX idx_documents_link_status ON documents(link_status);
CREATE INDEX idx_documents_manifest ON documents(manifest);
CREATE INDEX idx_documents_extension ON documents(extension);
CREATE INDEX idx_documents_host ON documents(host);
"""


def build(db_path=DB_PATH):
    courts = load_courts()
    host_map = build_host_map(courts)
    families = load_source_families()
    titles = load_titles()

    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    counts = {
        'manifests': {},
        'doc_type': {},
        'link_status': {},
        'extension': {},
        'hosts': set(),
        'unique_sha256': set(),
        'rows_with_state': 0,
        'rows_with_court_state_only': 0,
    }
    seen_ids = set()
    id_to_state = build_id_to_state(courts)
    insert_sql = ('INSERT OR IGNORE INTO documents (id, manifest, wave, source_url, final_url, host, filename, '
                  'local_rel_path, sha256, bytes, content_type, extension, download_status, http_status, '
                  'started_at, completed_at, jurisdiction_codes, authority_lanes, state, court_state, doc_type, '
                  'doc_type_basis, link_status, court_id, candidate_court_ids, title, text_extracted) VALUES '
                  '(:id,:manifest,:wave,:source_url,:final_url,:host,:filename,:local_rel_path,:sha256,:bytes,'
                  ':content_type,:extension,:download_status,:http_status,:started_at,:completed_at,'
                  ':jurisdiction_codes,:authority_lanes,:state,:court_state,:doc_type,:doc_type_basis,:link_status,'
                  ':court_id,:candidate_court_ids,:title,:text_extracted)')

    def ingest(rows_iter, manifest, wave):
        batch = []
        for raw in rows_iter:
            row = normalized_row(raw, manifest, wave, host_map, families, id_to_state)
            if row is None or row['id'] in seen_ids:
                continue
            seen_ids.add(row['id'])
            title_row = titles.get(row['sha256'])
            if title_row:
                row['title'] = title_row.get('title')
                row['text_extracted'] = 1 if title_row.get('text_extracted') else 0
            batch.append(row)
            counts['manifests'][manifest] = counts['manifests'].get(manifest, 0) + 1
            counts['doc_type'][row['doc_type']] = counts['doc_type'].get(row['doc_type'], 0) + 1
            counts['link_status'][row['link_status']] = counts['link_status'].get(row['link_status'], 0) + 1
            if row['extension']:
                counts['extension'][row['extension']] = counts['extension'].get(row['extension'], 0) + 1
            if row['host']:
                counts['hosts'].add(row['host'])
            counts['unique_sha256'].add(row['sha256'])
            if row['state']:
                counts['rows_with_state'] += 1
            elif row['court_state']:
                counts['rows_with_court_state_only'] += 1
            if len(batch) >= 2000:
                con.executemany(insert_sql, batch)
                batch = []
        if batch:
            con.executemany(insert_sql, batch)

    wave_dirs = sorted(p for p in COURT_EXPANSION_DIR.glob('wave_*') if (p / 'court_file_download_results_v2.jsonl').exists())
    for wave_dir in wave_dirs:
        ingest(iter_manifest_rows(wave_dir / 'court_file_download_results_v2.jsonl'), MANIFEST_COURT_EXPANSION, wave_dir.name)
    ingest(iter_manifest_rows(STATE_TRIAL_DIR / 'artifact_download_results_v1.jsonl'), MANIFEST_STATE_TRIAL, None)

    con.commit()
    con.close()

    counts['hosts'] = sorted(counts['hosts'])
    counts['unique_sha256'] = len(counts['unique_sha256'])
    counts['distinct_hosts'] = len(counts['hosts'])
    del counts['hosts']
    counts['court_reaching_files'] = counts['link_status'].get('matched', 0)
    counts['court_reaching_files_matched_plus_ambiguous'] = counts['link_status'].get('matched', 0) + counts['link_status'].get('ambiguous', 0)
    counts['wave_folders_read'] = len(wave_dirs)
    return counts


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _input_entry(path):
    path = Path(path)
    stat = path.stat()
    if stat.st_size > 200 * 1024 * 1024:
        return {'path': str(path), 'size': stat.st_size, 'mtime': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()}
    return {'path': str(path), 'sha256': sha256_file(path)}


def write_validation(counts, db_path=DB_PATH, out_path=VALIDATION_PATH):
    db_sha = sha256_file(db_path)
    total = sum(counts['manifests'].values())
    inputs = [_input_entry(COURT_SPINE_FILE)]
    wave_dirs = sorted(p for p in COURT_EXPANSION_DIR.glob('wave_*') if (p / 'court_file_download_results_v2.jsonl').exists())
    for wave_dir in wave_dirs:
        inputs.append(_input_entry(wave_dir / 'court_file_download_results_v2.jsonl'))
    inputs.append(_input_entry(STATE_TRIAL_DIR / 'artifact_download_results_v1.jsonl'))
    if URL_CORPUS_FILE.exists():
        inputs.append(_input_entry(URL_CORPUS_FILE))
    if PREFLIGHT_FILE.exists():
        inputs.append(_input_entry(PREFLIGHT_FILE))
    validation = {
        'schema_version': '1',
        'status': 'passed',
        'ready': True,
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': 'index.sqlite3', 'sha256': db_sha, 'rows': total}],
        'counts': {
            'total_indexed_rows': total,
            'rows_by_manifest': counts['manifests'],
            'rows_by_doc_type': counts['doc_type'],
            'rows_by_link_status': counts['link_status'],
            'rows_by_extension_top': dict(sorted(counts['extension'].items(), key=lambda kv: -kv[1])[:10]),
            'unique_sha256': counts['unique_sha256'],
            'distinct_hosts': counts['distinct_hosts'],
            'court_reaching_files_matched': counts['court_reaching_files'],
            'court_reaching_files_matched_plus_ambiguous': counts['court_reaching_files_matched_plus_ambiguous'],
            'court_expansion_wave_folders_read': counts['wave_folders_read'],
            'titles_loaded': counts.get('titles_loaded', 0),
            'rows_with_state_from_manifest_jurisdiction_code': counts.get('rows_with_state', 0),
            'rows_with_court_state_only_from_matched_court': counts.get('rows_with_court_state_only', 0),
        },
        'checks': [
            'download_status == downloaded only (download_error / signature_rejected_body_removed rows excluded)',
            'sha256 must be a 64-char lowercase hex string or the row is dropped',
            'artifact_id must be present and unique or the row is dropped (INSERT OR IGNORE on id)',
            'court join is exact website-host match only; a host mapping to >1 court_spine row is link_status=ambiguous with every candidate id listed, never a guess',
            'the bare www.uscourts.gov / uscourts.gov host is excluded from the court join entirely (9 defunct placeholder court_spine rows)',
            'doc_type is derived only from source_families (preflight/url-corpus, joined by artifact hash) or URL/filename patterns; never from PDF metadata',
            'title is null unless sources/court_document_library_20260919/titles.jsonl supplies it (see extract_titles.py); the index is valid with or without titles',
            'state is set only from the manifest jurisdiction_codes column (single USPS code); court_state is set independently, only for link_status=matched rows, from the matched court_spine row\'s own native state field -- the two bases are never merged',
        ],
        'qualification': (
            'local personal-testing view; derived from a private firm dataset built from CourtListener/RECAP API data; '
            'not for redistribution. Index of %d downloaded court documents (manifest snapshot 2026-08-20) covering two '
            'local manifests: %s. Counts are as of this build; completed_at is a download timestamp, never a legal '
            'effective date. Court joins are exact website-host matches only; a host shared by multiple courts is '
            'listed as ambiguous with candidates, never guessed. No county field exists anywhere in these manifests; '
            'none is inferred. Titles are present only for files extract_titles.py has processed so far.'
        ) % (total, ', '.join(sorted(counts['manifests']))),
        'license_ref': 'sw_bulk_private_firm_work_product',
        'export_allowed': False,
        'inputs': inputs,
    }
    out_path.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding='utf-8')
    return validation


if __name__ == '__main__':
    measured = build()
    titles = load_titles()
    measured['titles_loaded'] = len(titles)
    result = write_validation(measured)
    print(json.dumps(result['counts'], indent=2, sort_keys=True))
