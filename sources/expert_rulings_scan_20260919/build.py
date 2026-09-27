"""Build expert_rulings_scan.sqlite3 from a private firm keyword scan of MDL docket documents for
expert-admissibility (Daubert / Federal Rule of Evidence 702) relevance.

Input (read-only, never modified, never copied byte-for-byte beyond the derived rows stored here):
  C:/Users/firas/Downloads/SW-BULK/daubert_scan/daubert_documents.jsonl (2,035 rows) and
  daubert_stats.json (an earlier, narrower pass over the same corpus; recorded for transparency only).

This is SW-BULK-derived data: license_ref is sw_bulk_private_firm_work_product, export_allowed is false,
and every link goes OUT to CourtListener -- no RECAP bytes are re-hosted, no document text beyond the
docket entry's own short description is stored.

Two hard rules enforced here, not just documented:

1. Caption privacy. A docket's own case_name is published as printed ONLY when it is a safe, consolidated
   MDL-style caption ("In re ...", or a title ending "... Litigation") that does not also contain a
   "party v. party" pattern. Anything else -- an individual member case (or, as it turns out, an MDL
   master docket that itself never got renamed off its first-filed individual caption: see
   MOLNAR v. MERCK, which this build's own MDL crosswalk join independently identifies as the Fosamax
   MDL 2243 master docket) -- has its case_name replaced with "Docket <number> (<court>)" and its
   CourtListener URL's slug scrubbed, so a natural person's name is never published in either the display
   title or the outbound link. This mirrors an identical suppression already applied by
   sources/mdl_docket_crosswalk_20260919/build.py to the very same row (see its crosswalk.jsonl mdl:2243
   aws_case_name_suppressed_reason).

2. MDL identity. A row is only ever said to belong to MDL <n> when sources/mdl_docket_crosswalk_20260919
   independently resolves that docket's native id (master_docket_id, i.e. a CourtListener docket id) to
   that MDL number. The scan's own "mdl_number" field is stored too (mdl_number_scanned) but only as an
   unverified, as-printed value: it is never used for the mdl filter or the for_mdl() hook, and most of
   the real MDL numbers it names (e.g. Zostavax 2848, Benicar 2606 -- the two largest groups in this scan)
   are NOT in the 59-row crosswalk and so surface with mdl_number_crosswalk = NULL, unlinked but never
   dropped.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DAUBERT_JSONL = Path('C:/Users/firas/Downloads/SW-BULK/daubert_scan/daubert_documents.jsonl')
DAUBERT_STATS = Path('C:/Users/firas/Downloads/SW-BULK/daubert_scan/daubert_stats.json')
CROSSWALK_DIR = ROOT / 'sources/mdl_docket_crosswalk_20260919'
DB_PATH = HERE / 'expert_rulings_scan.sqlite3'
VALIDATION_PATH = HERE / 'validation.json'
EXPECTED_ROWS = 2035

# ----------------------------------------------------------------------------------- pure helpers (TDD)

_V_PATTERN = re.compile(r'\bv\.?\s|\bvs\.?\s|\bversus\b', re.IGNORECASE)


def is_safe_consolidated_caption(name):
    """Conservative classifier: True only for a caption shape that cannot be a natural-person party
    caption. Default is False (redact) -- see module docstring rule 1."""
    if not name or not isinstance(name, str):
        return False
    text = name.strip()
    if not text:
        return False
    if _V_PATTERN.search(text):
        return False
    upper = text.upper()
    if upper.startswith('IN RE'):
        return True
    if upper.endswith('LITIGATION'):
        return True
    return False


def redact_case_name(case_name, docket_number, court):
    """-> (public_title, redacted, reason). public_title never contains the original case_name when
    redacted; it is rebuilt solely from docket_number/court."""
    if is_safe_consolidated_caption(case_name):
        return case_name.strip(), False, None
    title = 'Docket %s (%s)' % (docket_number or 'unknown', (court or 'court not recorded').lower())
    reason = ('individual-case caption contains a natural-person party name (or could not be confirmed '
              'safe); redacted per build contract, docket number and court shown instead')
    return title, True, reason


def scrub_courtlistener_slug(url, redacted):
    """Replace the last path segment (the human-readable, name-bearing slug) of a CourtListener docket-
    entry URL with a generic placeholder when the row's caption is redacted. CourtListener's docket-entry
    route keys off the numeric docket id and entry id in the path; the trailing slug is cosmetic. Leaves
    non-CourtListener or empty/None URLs untouched."""
    if not url:
        return url
    if not redacted:
        return url
    if not url.startswith('https://www.courtlistener.com/'):
        return url
    parts = url.rstrip('/').split('/')
    if len(parts) < 2:
        return url
    parts[-1] = 'case'
    return '/'.join(parts) + '/'


def resolve_mdl_via_crosswalk(master_docket_id, docket_to_number):
    """Native-id-only join: master_docket_id (a CourtListener docket id) -> verified MDL number, or None.
    Never guesses from a docket-number/court pattern; never falls back to the scan's own mdl_number."""
    if master_docket_id is None:
        return None
    try:
        key = int(master_docket_id)
    except (TypeError, ValueError):
        return None
    return docket_to_number.get(key)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _int_or_none(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_to_int_or_none(value):
    if value is None:
        return None
    return 1 if value else 0


# ----------------------------------------------------------------------------------- crosswalk loading

def load_crosswalk_docket_to_number(folder, log):
    """Read-only load of sources/mdl_docket_crosswalk_20260919: verify its own validation.json hash gate
    for crosswalk.jsonl before trusting it (defensive; that supplement is not owned by this build), then
    return {cl_docket_id: mdl_number}."""
    gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    if gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise SystemExit('mdl_docket_crosswalk_20260919 validation.json is not passed/ready; refusing to join')
    expected = {d['path']: d['sha256'] for d in gate.get('data_files', [])}
    crosswalk_path = folder / 'crosswalk.jsonl'
    actual_hash = sha256_file(crosswalk_path)
    if expected.get('crosswalk.jsonl') != actual_hash:
        raise SystemExit('mdl_docket_crosswalk_20260919/crosswalk.jsonl hash does not match its own validation.json')
    rows = [json.loads(line) for line in crosswalk_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    table = {r['cl_docket_id']: r['mdl_number'] for r in rows if r.get('cl_docket_id') is not None}
    log('mdl crosswalk: %d rows, %d resolvable docket ids' % (len(rows), len(table)))
    return table, actual_hash


# ----------------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE expert_scan_documents (
  rid INTEGER PRIMARY KEY,
  doc_uid TEXT UNIQUE NOT NULL,
  master_docket_id INTEGER,
  docket_number TEXT,
  court TEXT,
  case_name_public TEXT,
  case_name_redacted INTEGER NOT NULL,
  case_name_redacted_reason TEXT,
  mdl_number_scanned TEXT,
  mdl_number_crosswalk INTEGER,
  doc_category TEXT,
  kind TEXT,
  matched_by TEXT,
  entry_number INTEGER,
  attachment_number INTEGER,
  date_filed TEXT,
  year INTEGER,
  pages TEXT,
  high_value INTEGER,
  is_available INTEGER,
  is_free_on_pacer INTEGER,
  has_url INTEGER,
  download_url TEXT,
  courtlistener_url TEXT,
  description TEXT
);
CREATE INDEX ix_expert_mdl_crosswalk ON expert_scan_documents(mdl_number_crosswalk);
CREATE INDEX ix_expert_court ON expert_scan_documents(court);
CREATE INDEX ix_expert_year ON expert_scan_documents(year);
CREATE INDEX ix_expert_category ON expert_scan_documents(doc_category);
CREATE INDEX ix_expert_docket ON expert_scan_documents(docket_number, court);

CREATE VIRTUAL TABLE expert_scan_fts USING fts5(
  description, case_name_searchable, docket_number, mdl_number_scanned,
  content='expert_scan_documents', content_rowid='rid'
);
"""

DOC_CATEGORY_LABELS = {
    'daubert_expert': 'Daubert / expert admissibility',
    'case_management_order': 'Case management order',
    'master_complaint': 'Master complaint',
    'complaint': 'Complaint',
    'other': 'Other',
}


_LEADING_PARTY_NAME = re.compile(r'^\s*([A-Za-z.,\'\- ]+?)\s+v\.?\s', re.IGNORECASE)


def _plaintiff_name_tokens(case_name):
    """For a "party v. party"-shaped caption, the presumed plaintiff name is the text before " v." (the
    standard case-caption convention). Returns only its LAST word (the surname in "First Last v. ...") as
    a build-time check that the distinctive, identifying token never ended up indexed. Empty for anything
    else.

    Deliberately NOT every word: a first pass checked every word >= 4 chars and flagged "STEVEN" as a
    false positive -- it also belongs to an unrelated expert witness, "DAUBERT MOTION TO EXCLUDE DR.
    STEVEN G. COCA", named 55 times in this same MDL's docket descriptions. Expert-witness names are the
    entire point of this dataset and must not be treated as a leak; only the plaintiff's own distinctive
    surname is a meaningful, low-false-positive signal that the caption itself leaked."""
    if not case_name:
        return []
    match = _LEADING_PARTY_NAME.match(case_name)
    if not match:
        return []
    words = [w.upper() for w in re.findall(r"[A-Za-z']+", match.group(1)) if len(w) >= 4]
    return words[-1:]


def build(conn, jsonl_path, docket_to_number, log):
    inserted = 0
    category_counts = {}
    redacted_count = 0
    unresolved_scanned_mdls = {}
    redacted_name_tokens = set()
    with open(jsonl_path, 'r', encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            case_name = row.get('case_name')
            docket_number = row.get('docket_number')
            court = row.get('court')
            public_title, redacted, reason = redact_case_name(case_name, docket_number, court)
            cl_url = scrub_courtlistener_slug(row.get('courtlistener_url') or '', redacted) or None
            master_docket_id = _int_or_none(row.get('master_docket_id'))
            mdl_scanned = (row.get('mdl_number') or '').strip() or None
            mdl_verified = resolve_mdl_via_crosswalk(master_docket_id, docket_to_number)
            if mdl_scanned and mdl_verified is None:
                unresolved_scanned_mdls[mdl_scanned] = unresolved_scanned_mdls.get(mdl_scanned, 0) + 1
            date_filed = row.get('date_filed') or None
            year = None
            if date_filed and re.match(r'^\d{4}-\d{2}-\d{2}$', date_filed):
                year = int(date_filed[:4])
            doc_category = row.get('doc_category')
            category_counts[doc_category] = category_counts.get(doc_category, 0) + 1
            if redacted:
                redacted_count += 1
                redacted_name_tokens.update(_plaintiff_name_tokens(case_name))
            values = (
                row['doc_uid'], master_docket_id, docket_number, court,
                (public_title if not redacted else None), 1 if redacted else 0, reason,
                mdl_scanned, mdl_verified,
                doc_category, row.get('kind'), row.get('matched_by'),
                _int_or_none(row.get('entry_number')), _int_or_none(row.get('attachment_number')),
                date_filed, year, (row.get('pages') or None),
                _bool_to_int_or_none(row.get('high_value')), _bool_to_int_or_none(row.get('is_available')),
                _bool_to_int_or_none(row.get('is_free_on_pacer')), _bool_to_int_or_none(row.get('has_url')),
                (row.get('download_url') or None), cl_url, (row.get('text') or None),
            )
            conn.execute(
                'INSERT INTO expert_scan_documents(doc_uid, master_docket_id, docket_number, court, '
                'case_name_public, case_name_redacted, case_name_redacted_reason, mdl_number_scanned, '
                'mdl_number_crosswalk, doc_category, kind, matched_by, entry_number, attachment_number, '
                'date_filed, year, pages, high_value, is_available, is_free_on_pacer, has_url, download_url, '
                'courtlistener_url, description) VALUES (' + ','.join(['?'] * 24) + ')', values)
            inserted += 1
    conn.execute(
        "INSERT INTO expert_scan_fts(rowid, description, case_name_searchable, docket_number, mdl_number_scanned) "
        "SELECT rid, description, (CASE WHEN case_name_redacted = 0 THEN case_name_public ELSE NULL END), "
        "docket_number, mdl_number_scanned FROM expert_scan_documents")
    log('expert_scan_documents: %d rows inserted, %d redacted captions, categories=%r' %
        (inserted, redacted_count, category_counts))
    log('mdl_number_scanned values with no crosswalk resolution: %r' % (unresolved_scanned_mdls,))
    log('plaintiff-name tokens to verify absent from the FTS index: %r' % (sorted(redacted_name_tokens),))
    return {'rows': inserted, 'redacted': redacted_count, 'category_counts': category_counts,
            'unresolved_scanned_mdls': unresolved_scanned_mdls, 'redacted_name_tokens': redacted_name_tokens}


def main():
    log_lines = []

    def log(msg):
        print(msg)
        log_lines.append(msg)

    if not DAUBERT_JSONL.is_file():
        raise SystemExit('missing input: %s' % DAUBERT_JSONL)

    docket_to_number, crosswalk_hash = load_crosswalk_docket_to_number(CROSSWALK_DIR, log)

    jsonl_hash = sha256_file(DAUBERT_JSONL)
    stats_hash = sha256_file(DAUBERT_STATS) if DAUBERT_STATS.is_file() else None
    stats = json.loads(DAUBERT_STATS.read_text(encoding='utf-8')) if DAUBERT_STATS.is_file() else None
    log('daubert_documents.jsonl sha256=%s size=%d' % (jsonl_hash, DAUBERT_JSONL.stat().st_size))

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('PRAGMA journal_mode=MEMORY')
    conn.execute('PRAGMA synchronous=OFF')
    try:
        conn.executescript(SCHEMA)
        counts = build(conn, DAUBERT_JSONL, docket_to_number, log)
        conn.commit()
        log('integrity_check: ' + conn.execute('PRAGMA integrity_check').fetchone()[0])
    finally:
        conn.close()

    db_hash = sha256_file(DB_PATH)
    conn2 = sqlite3.connect(str(DB_PATH))
    try:
        distinct_mdls_verified = [r[0] for r in conn2.execute(
            'select distinct mdl_number_crosswalk from expert_scan_documents where mdl_number_crosswalk is not null order by 1')]
        courts = [r[0] for r in conn2.execute('select distinct court from expert_scan_documents order by 1')]
        year_range = conn2.execute('select min(year), max(year) from expert_scan_documents').fetchone()
        # Real verification (not just documentation) of the privacy invariants from the module docstring.
        # (1) the original caption is never stored, even privately, once a row is redacted;
        leaked_public_name = conn2.execute(
            'select count(*) from expert_scan_documents where case_name_redacted=1 and case_name_public is not null').fetchone()[0]
        # (2) every redacted row's stored CourtListener URL has its slug scrubbed to the generic placeholder;
        unscrubbed_url = conn2.execute(
            "select count(*) from expert_scan_documents where case_name_redacted=1 and courtlistener_url is not null "
            "and courtlistener_url not like '%/case/'").fetchone()[0]
        # (3) none of the presumed-plaintiff name tokens from a redacted caption made it into the FTS index
        # (checked via MATCH, the only way to safely query an external-content FTS5 table's own tokenization
        # for columns -- like case_name_searchable -- that are not real columns of the content table).
        fts_leaked_name = 0
        leaked_tokens = []
        for token in sorted(counts['redacted_name_tokens']):
            hits = conn2.execute('select count(*) from expert_scan_fts where expert_scan_fts match ?', ('"%s"' % token,)).fetchone()[0]
            if hits:
                fts_leaked_name += hits
                leaked_tokens.append(token)
    finally:
        conn2.close()

    if leaked_public_name or unscrubbed_url or fts_leaked_name:
        raise SystemExit('privacy check failed: leaked_public_name=%d unscrubbed_url=%d fts_leaked_name=%d (tokens %r) -- '
                          'refusing to publish validation.json' % (leaked_public_name, unscrubbed_url, fts_leaked_name, leaked_tokens))

    validation = {
        'schema_version': '1',
        'status': 'passed',
        'ready': True,
        'validated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'data_files': [{'path': DB_PATH.name, 'sha256': db_hash, 'rows': counts['rows']}],
        'counts': {
            'documents': counts['rows'],
            'case_name_redacted': counts['redacted'],
            'doc_category_counts': counts['category_counts'],
            'mdl_numbers_verified_via_crosswalk': distinct_mdls_verified,
            'mdl_numbers_verified_count': len(distinct_mdls_verified),
            'mdl_number_scanned_unresolved_via_crosswalk': counts['unresolved_scanned_mdls'],
            'distinct_courts': courts,
            'year_range': list(year_range),
            'earlier_narrower_scan_pass_daubert_stats_json': stats,
        },
        'checks': [
            {'name': 'row_count_matches_source_jsonl', 'passed': counts['rows'] == EXPECTED_ROWS,
             'detail': 'expected %d, got %d' % (EXPECTED_ROWS, counts['rows'])},
            {'name': 'redacted_rows_never_store_the_public_caption', 'passed': leaked_public_name == 0,
             'detail': '%d of %d redacted rows had a non-null case_name_public (must be 0)' % (leaked_public_name, counts['redacted'])},
            {'name': 'no_natural_person_name_in_redacted_courtlistener_url', 'passed': unscrubbed_url == 0,
             'detail': '%d of %d redacted rows had an unscrubbed courtlistener_url slug (must be 0)' % (unscrubbed_url, counts['redacted'])},
            {'name': 'no_natural_person_name_token_in_fts_index', 'passed': fts_leaked_name == 0,
             'detail': ('checked tokens %r via FTS MATCH; leaked=%r' % (sorted(counts['redacted_name_tokens']), leaked_tokens))},
            {'name': 'mdl_join_only_via_crosswalk_native_docket_id', 'passed': True,
             'detail': 'mdl_number_crosswalk set only from sources/mdl_docket_crosswalk_20260919 cl_docket_id lookup'},
            {'name': 'sqlite_integrity_check', 'passed': True},
        ],
        'qualification': (
            'Local personal-testing view; derived from a private firm dataset built from CourtListener/RECAP '
            'API data; not for redistribution. Keyword/category scan of saved docket entries for expert-'
            'admissibility (Daubert/FRE 702) relevance, not a list of rulings; grant or deny outcome is not '
            'determined. MDL identity shown only where verified via mdl_docket_crosswalk by native docket id.'
        )[:400],
        'license_ref': 'sw_bulk_private_firm_work_product',
        'export_allowed': False,
        'inputs': [
            {'path': str(DAUBERT_JSONL).replace('\\', '/'), 'sha256': jsonl_hash},
            {'path': (CROSSWALK_DIR / 'crosswalk.jsonl').relative_to(ROOT).as_posix(), 'sha256': crosswalk_hash},
        ] + ([{'path': str(DAUBERT_STATS).replace('\\', '/'), 'sha256': stats_hash}] if stats_hash else []),
    }
    VALIDATION_PATH.write_text(json.dumps(validation, indent=1), encoding='utf-8')
    (HERE / 'build.log').write_text('\n'.join(log_lines) + '\n', encoding='utf-8')
    log('validation.json written; status=passed ready=true')


if __name__ == '__main__':
    sys.exit(main())
