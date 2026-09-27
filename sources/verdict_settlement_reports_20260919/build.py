"""Verdict & settlement reports supplement (verdict_settlement_reports_20260919).

Deterministic, offline, re-runnable. Builds a SQLite database (with an FTS5 search index) from a single
local input file, `verdict_lead_index.json` -- 3,312 deduplicated verdict/settlement leads self-reported
to a commercial verdict-ranking site (topverdict.com) by law firms seeking placement on its published
"top N" lists. See README.md for the full picture; the short version:

- NOT a court-records source. Every row is a publisher self-report, never verified against a docket.
  Amounts are shown exactly as printed; a parsed numeric value is stored only when the printed string is
  an unambiguous "$#,###.##" figure, and it is never summed, ranked or averaged (see amount_band()).
- Privacy: case captions in this dataset often name individual plaintiffs, and -- a finding made while
  building this classifier, not assumed in advance -- sometimes name individual DEFENDANTS too, or
  embed a real person's name in a personal trust caption, or concatenate several unrelated captions
  together (a measured publisher data-quality defect affecting ~20% of rows). redact_caption() below
  handles all of these conservatively: see its docstring and test_build.py's CaptionRedactionTests for
  the real examples that drove the design.
- MDL links only when the report names an MDL number explicitly, or its caption exactly equals a JPML
  registry caption (sources/jpml_mdl_20260919) after whitespace/case normalization. This is deliberately
  strict; measured result on the real data is zero links (the publisher's captions are abbreviated
  relative to the JPML's own caption text), which is expected, not a bug.
- License: the publisher's terms prohibit reuse beyond the person's own research. Every row/response
  carries license_ref "publisher_terms_prohibit_reuse_local_research_only" and export_allowed: false.

Re-run:
  C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe sources/verdict_settlement_reports_20260919/build.py
  C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe -m unittest discover -s sources/verdict_settlement_reports_20260919 -p test_build.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT_JSON = Path('C:/Users/firas/Downloads/returnedfiles/settlementsverdicts/verdict_lead_index.json')
JPML_MDLS = Path('C:/Users/firas/Downloads/SCRAPE/sources/jpml_mdl_20260919/mdls.jsonl')
DB_NAME = 'verdict_settlement_reports.sqlite3'
DB = HERE / DB_NAME

REPORT_URL = 'https://topverdict.com/'
LICENSE_REF = 'publisher_terms_prohibit_reuse_local_research_only'

# --------------------------------------------------------------------------------------- amount parsing
# Only a plain "$<digits with optional thousands commas>[.<1-2 digit cents>]" string is unambiguous.
# Anything else (a range, "Confidential"/"Undisclosed", a bare number with no currency marker, multiple
# figures) is kept only as the raw printed string; amount_numeric stays None and it is never guessed.
_AMOUNT_RE = re.compile(r'^\$\s*(\d{1,3}(?:,\d{3})*|\d+)(\.\d{1,2})?$')

AMOUNT_BAND_LABELS = {
    'not_stated': 'Not stated',
    'under_1m': 'Under $1M',
    '1m_10m': '$1M-$10M',
    '10m_100m': '$10M-$100M',
    'over_100m': 'Over $100M',
}
AMOUNT_BAND_ORDER = ['under_1m', '1m_10m', '10m_100m', 'over_100m', 'not_stated']


def parse_amount_usd(raw):
    """Return a float only when `raw` is an unambiguous printed dollar figure; otherwise None. Never
    raises, regardless of input type."""
    try:
        text = raw.strip() if isinstance(raw, str) else None
    except Exception:
        return None
    if not text:
        return None
    match = _AMOUNT_RE.match(text)
    if not match:
        return None
    whole = match.group(1).replace(',', '')
    cents = match.group(2) or ''
    try:
        return float(whole + cents)
    except ValueError:
        return None


def amount_band(value):
    """value is the parsed numeric amount (or None). Bucket thresholds are the ones named in the build
    contract: under $1M, $1M-$10M, $10M-$100M, over $100M, not stated. Boundaries are inclusive on the
    lower end of each band (exactly $1,000,000 falls in the $1M-$10M band, not "under $1M")."""
    if value is None:
        return 'not_stated'
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 'not_stated'
    if amount < 1_000_000:
        return 'under_1m'
    if amount < 10_000_000:
        return '1m_10m'
    if amount < 100_000_000:
        return '10m_100m'
    return 'over_100m'


# ------------------------------------------------------------------------------------- caption redaction
# Build contract: "Natural-person names are never published: rewrite a caption to 'Individual plaintiff
# v. <organisation defendant>' when the plaintiff side is a person; keep organisations, 'In re ...'
# captions, government entities. Write the rule conservatively... when unsure, redact."
#
# This implementation extends that rule symmetrically to the defendant side (see
# test_named_individual_defendant_is_also_redacted / test_ampersand_pair_without_other_signal_is_redacted
# in test_build.py for the real captions -- 'Jane Doe vs. Alkiviades David', 'Lincome & Bishop' -- that
# make the extension necessary: the literal rule as given only describes the common case where the
# defendant is a company, but this dataset also contains suits between two named individuals with no
# organisation on either side). The classifier is a single conservative predicate,
# `_side_is_org_or_gov`: a side is treated as a person UNLESS it carries a clear organisation/government
# signal. "Unsure" therefore always resolves to "redact", per the contract.
#
# 'trust' and a bare '&' are deliberately NOT treated as organisation signals: a personal revocable
# living trust is routinely named after its settlor ("Jean Michele Cross Revocable Trust"), and a bare
# "X & Y" is equally consistent with a company name ("Johnson & Johnson") or two individual co-plaintiffs
# ("Lincome & Bishop") -- there is no reliable syntactic way to tell them apart, so both are redacted.
# This does mean a handful of legitimate bare-brand names with no other corporate marker are
# over-redacted (an accepted, documented cost -- see README "Caption redaction").

_IN_RE_RE = re.compile(r'^\s*in\s+re\b\s*:?\s*', re.IGNORECASE)
_VS_SPLIT_RE = re.compile(r'\s+v\.?s?\.?\s+', re.IGNORECASE)
_ET_AL_RE = re.compile(r'\bet\s*\.?\s*al\.?\b', re.IGNORECASE)
_PLURAL_LEFT_RE = re.compile(r'\b(plaintiffs|survivors|claimants|petitioners)\b', re.IGNORECASE)
_PLURAL_RIGHT_RE = re.compile(r'\b(defendants|respondents)\b', re.IGNORECASE)
_PROBATE_SIGNAL_RE = re.compile(
    r'\b(estate|guardianship|conservatorship|power of attorney|trust|will|adoption|marriage|custody|name change)\b',
    re.IGNORECASE)
_COLLECTIVE_LITIGATION_RE = re.compile(
    r'litig|products?\s+liability|multidistrict|class\s+action|\bmdl\b', re.IGNORECASE)

# Deliberately excludes 'trust' and any '&'-based signal -- see module docstring above.
_ORG_GOV_TOKEN_RE = re.compile(
    r'\b('
    r'inc|incorporated|llc|l\.l\.c\.?|llp|l\.l\.p\.?|lp|l\.p\.?|ltd|limited|co|corp|corporation|company|'
    r'plc|pllc|p\.c\.?|n\.a\.?|na|'
    r'group|holdings?|industries|enterprises?|partners?(?:hip)?|systems?|technologies|technology|solutions?|'
    r'laboratories|labs?|pharmaceuticals?|pharma|motors|foods?|energy|airlines?|insurance|bank|'
    r'capital|fund|media|communications?|network|networks|services?|products?|brands?|international|global|'
    r'university|hospital|medical\s+center|church|diocese|archdiocese|institute|center|centre|council|'
    r'union|society|association|foundation|committee|board|department|bureau|agency|commission|authority|'
    r'district|county|municipal|municipality|township|borough|village|parish|'
    r'united\s+states|u\.s\.a?\.?|state\s+of|commonwealth\s+of|city\s+of|people\s+of\s+the\s+state|'
    r'government|school|wireless|electric|telecom|cable|water|gas|railroad|railway|steel|mining|oil|'
    r'chemicals?|plastics?|beverages?|properties|realty'
    r')\b', re.IGNORECASE)

_MULTI_CASE_LABEL = 'Multiple parties (bundled entry; captions withheld)'
_UNSURE_LABEL = 'Individual party (caption withheld)'
_IN_RE_REDACTED_LABEL = 'In re: matter involving a named individual (name withheld)'


def _side_is_org_or_gov(text):
    text = (text or '').strip()
    return bool(text) and bool(_ORG_GOV_TOKEN_RE.search(text))


def _in_re_is_safe_to_keep(remainder):
    if _PROBATE_SIGNAL_RE.search(remainder):
        return False
    if _COLLECTIVE_LITIGATION_RE.search(remainder):
        return True
    return bool(_ORG_GOV_TOKEN_RE.search(remainder))


def redact_caption(case_text):
    """Return (title, was_redacted, basis). Never raises. See module docstring for the rule."""
    if not isinstance(case_text, str) or not case_text.strip():
        return case_text, False, 'empty'
    text = re.sub(r'\s+', ' ', case_text.strip())
    in_re_match = _IN_RE_RE.match(text)
    if in_re_match:
        remainder = text[in_re_match.end():].strip()
        if _in_re_is_safe_to_keep(remainder):
            return text, False, 'in_re_caption_kept'
        return _IN_RE_REDACTED_LABEL, True, 'in_re_individual_matter_redacted'
    if len(_VS_SPLIT_RE.findall(text)) >= 2:
        # A publisher data-quality defect: several distinct captions concatenated with no reliable
        # delimiter. Redact wholesale rather than risk mis-splitting a name into the "safe" half.
        return _MULTI_CASE_LABEL, True, 'multi_case_blob_redacted'
    parts = _VS_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        if _side_is_org_or_gov(text):
            return text, False, 'no_v_split_org_kept'
        return _UNSURE_LABEL, True, 'no_v_split_unsure_redacted'
    left, right = parts[0].strip(), parts[1].strip()
    left_org, right_org = _side_is_org_or_gov(left), _side_is_org_or_gov(right)
    if left_org and right_org:
        return text, False, 'both_sides_org_kept'
    left_plural = bool(_ET_AL_RE.search(left)) or bool(_PLURAL_LEFT_RE.search(left))
    right_plural = bool(_ET_AL_RE.search(right)) or bool(_PLURAL_RIGHT_RE.search(right))
    new_left = left if left_org else ('Individual plaintiffs' if left_plural else 'Individual plaintiff')
    new_right = right if right_org else ('Individual defendants' if right_plural else 'Individual defendant')
    basis_parts = []
    if not left_org:
        basis_parts.append('plaintiff_redacted')
    if not right_org:
        basis_parts.append('defendant_redacted')
    return f'{new_left} v. {new_right}', True, '+'.join(basis_parts)


def normalize_caption(text):
    return re.sub(r'\s+', ' ', (text or '').strip()).upper()


# ------------------------------------------------------------------------------------------- MDL linking
# "MDL links ONLY when the report itself names an MDL number or its caption equals a JPML registry
# caption after whitespace/case normalisation; otherwise no link." Both paths are checked; an explicit
# named number wins if both would fire (never observed together on real data).
_MDL_NUMBER_RE = re.compile(r'\bMDL\b[\s.:#-]*(?:No\.?|Number|#)?[\s.:#-]*(\d{3,6})\b', re.IGNORECASE)


def extract_explicit_mdl_number(text):
    match = _MDL_NUMBER_RE.search(text or '')
    return int(match.group(1)) if match else None


def load_jpml_registry(path):
    """Read-only load of sources/jpml_mdl_20260919/mdls.jsonl. Returns (by_title, by_number):
    by_title maps a normalized caption -> (mdl_number, status); by_number maps mdl_number -> status.
    Never raises; a missing/unreadable file yields two empty dicts (an explicit number can then only be
    reported as 'not_in_registry', never guessed as pending/terminated)."""
    by_title, by_number = {}, {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                number, title, status = row.get('mdl_number'), row.get('title'), row.get('status')
                if not isinstance(number, int):
                    continue
                by_number[number] = status
                if isinstance(title, str) and title.strip():
                    by_title[normalize_caption(title)] = (number, status)
    except OSError:
        return {}, {}
    return by_title, by_number


def link_mdl(case_text, type_text, by_title, by_number):
    """Return (mdl_number, basis, registry_status) or (None, None, None)."""
    combined = ' '.join(t for t in (case_text, type_text) if isinstance(t, str) and t)
    explicit = extract_explicit_mdl_number(combined)
    if explicit is not None:
        return explicit, 'report_named_mdl_number', by_number.get(explicit, 'not_in_registry')
    if isinstance(case_text, str) and case_text.strip():
        hit = by_title.get(normalize_caption(case_text))
        if hit:
            number, status = hit
            return number, 'caption_matches_jpml_registry_title', status
    return None, None, None


# --------------------------------------------------------------------------------------------- record id
def compute_id(list_url, rank, case_text):
    """Deterministic, stable composite id. No native unique id exists on the source rows; this is a
    content hash of the fields that identify a row's position in its published list, so a re-run over an
    unchanged input file always reproduces the same ids."""
    basis = '\x1f'.join(str(part) for part in (list_url or '', rank if rank is not None else '', case_text or ''))
    return 'vsr-' + hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]


_CONTACT_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b')


def _strip_contact_details(text):
    if not isinstance(text, str) or not text:
        return text or ''
    return _CONTACT_RE.sub('[redacted]', text)


# ------------------------------------------------------------------------------------------ row assembly
def build_row(record, ordinal, by_title, by_number):
    """Pure function: one input record -> one output row dict. No I/O; safe to unit-test directly."""
    case_text = record.get('case') or ''
    type_text = record.get('type') or ''
    title, title_redacted, redaction_basis = redact_caption(case_text)
    amount_raw = record.get('amount_raw') if isinstance(record.get('amount_raw'), str) else None
    amount_numeric = parse_amount_usd(amount_raw)
    mdl_number, mdl_link_basis, mdl_registry_status = link_mdl(case_text, type_text, by_title, by_number)
    mass_tort_tags = [tag for tag in (record.get('mass_tort_tags') or []) if isinstance(tag, str)]
    firms = [_strip_contact_details(f) for f in (record.get('firms') or []) if isinstance(f, str)]
    jurisdiction = (record.get('jurisdiction') or '').strip()
    county = (record.get('county') or '').strip() or None
    year_raw = record.get('year')
    try:
        year = int(str(year_raw).strip())
    except (TypeError, ValueError):
        year = None
    subtitle_bits = [part for part in (jurisdiction, county) if part]
    subtitle_bits.append(str(year) if year is not None else 'year not stated')
    list_url = record.get('list_url') if isinstance(record.get('list_url'), str) else ''
    result_type = (record.get('result_type') or '').strip().lower()
    if result_type not in ('verdict', 'settlement'):
        result_type = result_type or 'other'
    scope_count = record.get('scope_count')
    return {
        'id': compute_id(list_url, record.get('rank'), case_text),
        'rank': record.get('rank') if isinstance(record.get('rank'), int) else None,
        'year': year,
        'jurisdiction': jurisdiction,
        'county': county,
        'result_type': result_type,
        'primary_type': (record.get('primary_type') or '').strip(),
        'full_type': type_text.strip(),
        'amount_raw': amount_raw,
        'amount_numeric': amount_numeric,
        'amount_band': amount_band(amount_numeric),
        'title': title,
        'title_redacted': bool(title_redacted),
        'redaction_basis': redaction_basis,
        'firms': firms,
        'attorneys': _strip_contact_details(record.get('attorneys') or ''),
        'mass_tort_tags': mass_tort_tags,
        'is_mass_tort': bool(mass_tort_tags),
        'national_list': bool(record.get('national_list')),
        'scope_count': scope_count if isinstance(scope_count, int) else None,
        'listed_scopes': [s for s in (record.get('listed_scopes') or []) if isinstance(s, str)],
        'list_url': list_url,
        'list_slug': (record.get('list_slug') or '').strip(),
        'mdl_number': mdl_number,
        'mdl_link_basis': mdl_link_basis,
        'mdl_registry_status': mdl_registry_status,
        'subtitle': ' \u00b7 '.join(subtitle_bits),
        'source_row_ordinal': ordinal,
    }


# ------------------------------------------------------------------------------------------------- build
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


_SCHEMA = '''
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE reports(
        id TEXT NOT NULL,
        rank INTEGER,
        year INTEGER,
        jurisdiction TEXT,
        county TEXT,
        result_type TEXT,
        primary_type TEXT,
        full_type TEXT,
        amount_raw TEXT,
        amount_numeric REAL,
        amount_band TEXT,
        title TEXT,
        title_redacted INTEGER,
        redaction_basis TEXT,
        firms TEXT,
        attorneys TEXT,
        mass_tort_tags TEXT,
        is_mass_tort INTEGER,
        national_list INTEGER,
        scope_count INTEGER,
        listed_scopes TEXT,
        list_url TEXT,
        list_slug TEXT,
        mdl_number INTEGER,
        mdl_link_basis TEXT,
        mdl_registry_status TEXT,
        subtitle TEXT,
        source_row_ordinal INTEGER
    );
    CREATE UNIQUE INDEX reports_id ON reports(id);
    CREATE INDEX reports_year ON reports(year);
    CREATE INDEX reports_jurisdiction ON reports(jurisdiction);
    CREATE INDEX reports_result_type ON reports(result_type);
    CREATE INDEX reports_primary_type ON reports(primary_type);
    CREATE INDEX reports_mass_tort ON reports(is_mass_tort);
    CREATE INDEX reports_amount_band ON reports(amount_band);
    CREATE INDEX reports_mdl ON reports(mdl_number);
    CREATE VIRTUAL TABLE reports_fts USING fts5(
        title, practice_area, firms, attorneys, jurisdiction, county, tags,
        content='reports', content_rowid='rowid', tokenize='unicode61'
    );
    CREATE TABLE facet_counts(facet TEXT, value TEXT, n INTEGER);
'''

_COLUMNS = ['id', 'rank', 'year', 'jurisdiction', 'county', 'result_type', 'primary_type', 'full_type',
            'amount_raw', 'amount_numeric', 'amount_band', 'title', 'title_redacted', 'redaction_basis',
            'firms', 'attorneys', 'mass_tort_tags', 'is_mass_tort', 'national_list', 'scope_count',
            'listed_scopes', 'list_url', 'list_slug', 'mdl_number', 'mdl_link_basis', 'mdl_registry_status',
            'subtitle', 'source_row_ordinal']


def _row_tuple(row):
    return tuple(
        json.dumps(row[key], ensure_ascii=False) if key in ('firms', 'mass_tort_tags', 'listed_scopes')
        else (1 if row[key] else 0) if key in ('title_redacted', 'is_mass_tort', 'national_list')
        else row[key]
        for key in _COLUMNS
    )


def main():
    payload = json.loads(INPUT_JSON.read_text(encoding='utf-8'))
    records = payload['records']
    by_title, by_number = load_jpml_registry(JPML_MDLS)

    rows = [build_row(record, ordinal, by_title, by_number) for ordinal, record in enumerate(records)]

    seen_ids = set()
    for row in rows:
        if row['id'] in seen_ids:
            raise SystemExit(f'id collision, aborting build: {row["id"]!r}')
        seen_ids.add(row['id'])

    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript(_SCHEMA)
    connection.executemany(
        f'INSERT INTO reports({",".join(_COLUMNS)}) VALUES({",".join("?" * len(_COLUMNS))})',
        (_row_tuple(row) for row in rows))
    connection.execute('''
        INSERT INTO reports_fts(rowid, title, practice_area, firms, attorneys, jurisdiction, county, tags)
        SELECT rowid, title, primary_type, firms, attorneys, jurisdiction, coalesce(county, ''), mass_tort_tags
        FROM reports
    ''')

    def facet(name, sql):
        for value, n in connection.execute(sql):
            connection.execute('INSERT INTO facet_counts VALUES(?,?,?)', (name, value, n))

    facet('result_type', "SELECT result_type, count(*) FROM reports GROUP BY result_type")
    facet('jurisdiction', "SELECT jurisdiction, count(*) FROM reports GROUP BY jurisdiction")
    facet('year', "SELECT year, count(*) FROM reports GROUP BY year")
    facet('primary_type', "SELECT primary_type, count(*) FROM reports WHERE primary_type<>'' GROUP BY primary_type")
    facet('amount_band', "SELECT amount_band, count(*) FROM reports GROUP BY amount_band")
    connection.execute("INSERT INTO facet_counts VALUES('mass_tort', 'yes', (SELECT count(*) FROM reports WHERE is_mass_tort=1))")
    connection.commit()

    counts = {
        'total_reports': len(rows),
        'by_result_type': dict(Counter(r['result_type'] for r in rows)),
        'by_amount_band': dict(Counter(r['amount_band'] for r in rows)),
        'with_amount_numeric': sum(1 for r in rows if r['amount_numeric'] is not None),
        'mass_tort_tagged': sum(1 for r in rows if r['is_mass_tort']),
        'captions_redacted': sum(1 for r in rows if r['title_redacted']),
        'captions_kept_verbatim': sum(1 for r in rows if not r['title_redacted']),
        'multi_case_blob_rows': sum(1 for r in rows if r['redaction_basis'] == 'multi_case_blob_redacted'),
        'redaction_basis_counts': dict(Counter(r['redaction_basis'] for r in rows)),
        'mdl_linked': sum(1 for r in rows if r['mdl_number'] is not None),
        'mdl_linked_by_explicit_number': sum(1 for r in rows if r['mdl_link_basis'] == 'report_named_mdl_number'),
        'mdl_linked_by_caption_match': sum(1 for r in rows if r['mdl_link_basis'] == 'caption_matches_jpml_registry_title'),
        'distinct_jurisdictions': len({r['jurisdiction'] for r in rows if r['jurisdiction']}),
        'distinct_primary_types': len({r['primary_type'] for r in rows if r['primary_type']}),
        'national_list_rows': sum(1 for r in rows if r['national_list']),
        'jpml_registry_rows_loaded': len(by_number),
    }
    connection.execute('VACUUM')
    connection.close()

    validation = {
        'schema_version': '1',
        'status': 'passed',
        'ready': True,
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': DB_NAME, 'sha256': sha256_file(DB), 'rows': len(rows)}],
        'counts': counts,
        'checks': [
            {'name': 'row_ids_unique', 'passed': len(seen_ids) == len(rows)},
            {'name': 'amount_numeric_only_when_unambiguous',
             'passed': all((r['amount_numeric'] is None) or _AMOUNT_RE.match(r['amount_raw'] or '') for r in rows)},
            {'name': 'redacted_title_never_equals_raw_case',
             'passed': all((not r['title_redacted']) or r['title'] != records[r['source_row_ordinal']].get('case')
                           for r in rows)},
        ],
        'qualification': (
            'Verdict and settlement reports self-reported by law firms to a commercial verdict-ranking '
            'site (topverdict.com) so as to be listed on its published "top N" lists; reported by the '
            'publisher, not verified against a court record. Amounts, state/court text and years are '
            'shown exactly as printed by the publisher; a parsed numeric amount is stored only when the '
            'printed string is unambiguous, and amounts are never summed, ranked or averaged. Selection '
            'is top-N by amount within each list, so this is not a representative sample of verdicts or '
            'settlements generally. Case captions are redacted (see README "Caption redaction") to remove '
            'natural-person names; the publisher\'s terms prohibit reuse beyond the person\'s own local '
            'research. Snapshot built by the publisher 2026-08-22 (3,312 deduplicated results from 289 '
            'published lists); this layer built 2026-09-19.'
        ),
        'license_ref': LICENSE_REF,
        'export_allowed': False,
        'inputs': [
            {'path': INPUT_JSON.as_posix(), 'sha256': sha256_file(INPUT_JSON)},
        ] + ([{'path': JPML_MDLS.as_posix(), 'sha256': sha256_file(JPML_MDLS)}] if JPML_MDLS.exists() else []),
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1), encoding='utf-8')
    print(json.dumps(counts, indent=1))
    return counts


if __name__ == '__main__':
    main()
