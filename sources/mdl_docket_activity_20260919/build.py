"""Build sources/mdl_docket_activity_20260919 (plan slice 7) — master-docket entries from the AWS
release, joined to the MDL registry via the mdl_docket_crosswalk_20260919 spine.

Deterministic, offline, re-runnable. Streams the two gzip inputs line by line; never loads the raw
release files fully into memory. No network. Text inside data files (docket descriptions) is untrusted
data, published verbatim, never parsed into a person entity, never used as instructions.

Inputs (read-only, main tree only):
  C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc/docket_entries.jsonl.gz
  C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc/documents.jsonl.gz
    (only to compute a has_verified_document flag + count per docket entry; s3_bucket/s3_key/sha256 are
    read but never written to any output file)
  C:/Users/firas/Downloads/SCRAPE/sources/mdl_docket_crosswalk_20260919/{crosswalk.jsonl,edges.jsonl}
    (the id spine; matter_id -> mdl_number, for both the 59 master matters and the 40 member matters)

Outputs (this folder):
  entries.jsonl      one row per docket entry that resolves to a registry MDL (matter_id -> mdl_number)
  unresolved.jsonl   one row per docket entry whose matter has no MDL link (parked, never dropped, never guessed)
  mdl_summary.jsonl  one row per distinct matched MDL number: per-type counts, date range, a measured
                     "entries_capped" flag per contributing matter, and the latest 25 entries
  validation.json, README.md (README is static; validation.json is written by this script)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = ROOT.parent / 'SW-BULK' / 'AWS-BATCH1-DOCKETS' / 'releases' / 'b2b-cdbb8d040b95c7b65cfc'
CROSSWALK_DIR = ROOT / 'sources' / 'mdl_docket_crosswalk_20260919'
OUT_DIR = Path(__file__).resolve().parent

DOCKET_ENTRIES_FILE = RELEASE_DIR / 'docket_entries.jsonl.gz'
DOCUMENTS_FILE = RELEASE_DIR / 'documents.jsonl.gz'
MATTERS_FILE = RELEASE_DIR / 'matters.jsonl.gz'
MATTER_ALIASES_FILE = RELEASE_DIR / 'matter_aliases.jsonl.gz'

# The six MDLs this slice reaches that mdl_docket_documents_20260919 (plan slice 2, built from the
# SW-BULK catalog/documents.json) does not reach at all (per INTEGRATION_PLAN.md section 7).
UNIQUE_MDLS = (2323, 3060, 2243, 1570, 2904, 2921)

# Deterministic entry-type classifier. First pattern to match a description wins (priority order below);
# unmatched text is "other". This assigns exactly one label per entry, so per-category counts here are not
# the same as the plan's overlapping keyword-hit counts (an entry can contain more than one keyword).
CATEGORY_PATTERNS = (
    ('settlement', re.compile(r'settlement', re.IGNORECASE)),
    ('bellwether', re.compile(r'bellwether', re.IGNORECASE)),
    ('daubert', re.compile(r'daubert', re.IGNORECASE)),
    ('common_benefit', re.compile(r'common benefit', re.IGNORECASE)),
    ('steering_committee', re.compile(r'steering committee', re.IGNORECASE)),
    ('leadership', re.compile(r'leadership', re.IGNORECASE)),
    ('pretrial_order', re.compile(r'pretrial order', re.IGNORECASE)),
    ('case_management_order', re.compile(r'case management order', re.IGNORECASE)),
)
ENTRY_TYPES = tuple(name for name, _ in CATEGORY_PATTERNS) + ('other',)

ENTERED_RE = re.compile(r'\(Entered:\s*(\d{1,2})/(\d{1,2})/(\d{4})\)\s*$')

# A gap of this many or more between the highest observed entry_number and the row count is treated as
# evidence of truncation (rows beyond that number were not captured in this release). A smaller gap is
# ordinary docket noise (stricken/renumbered entries) and is not labelled "capped".
CAP_GAP_THRESHOLD = 50

# --------------------------------------------------------------------------- party-name redaction
# Build contract: "natural-person plaintiffs are never listed; show counts." Applied to every
# description before it is written to any output file (entries.jsonl; unresolved.jsonl already
# carries no description). The pre-redaction text is used only in memory to build the redacted
# string and is never itself written anywhere.
REDACTION_BASIS = 'individual member-case plaintiff caption withheld per the party-name rule'

# Shape 1: 'Short Form Complaint[s] [-] <caption> by PLAINTIFF(S)' (member-case short-form
# complaints; the overwhelming majority are single natural-person plaintiffs, e.g. MDL 2323).
SFC_CAPTION_RE = re.compile(
    r'\b(?:Amended\s+)?Short Form Complaints?\s*-?\s*(.+?)\s+by PLAINTIFF\(S\)', re.IGNORECASE)
# Shape 2: 'Plaintiff/Decedent <First Last>' narrative references.
DECEDENT_CAPTION_RE = re.compile(
    r'\bPlaintiff/Decedent\s+([A-Z][A-Za-z.\'-]+(?:\s+[A-Z][A-Za-z.\'-]+){0,3})', re.IGNORECASE)
# Shape 3: an 'X v. Y' party-style caption (only the plaintiff-side span before ' v. ' is
# redacted; the defendant side, organisations and government parties are left intact). Deliberately
# conservative: it also redacts non-person plaintiff captions (e.g. a state or company name) rather
# than risk leaving a natural-person plaintiff caption unredacted.
CASE_CAPTION_RE = re.compile(
    r'(?:(?<=^)|(?<=[-:(])|(?<=\bin )|(?<=\bIn ))\s*((?:[A-Z][A-Za-z.\'&-]*\.?,?\s*){1,5}?)\bv\.\s+')

CAPTION_REDACTION_PATTERNS = (SFC_CAPTION_RE, DECEDENT_CAPTION_RE, CASE_CAPTION_RE)


def _redact_group1(match, placeholder):
    start, end = match.span(1)
    mstart = match.start()
    full = match.group(0)
    return full[:start - mstart] + placeholder + full[end - mstart:]


def redact_description(text):
    """Return (redacted_text, was_redacted). Replaces a captured member-case plaintiff caption
    (shapes 1-2) or the plaintiff-side of a party-v-party caption (shape 3) with
    '[plaintiff name withheld]'. Attorney/filer parentheticals (e.g. '(SEEGER, CHRISTOPHER)'),
    organisation names and named defendants are left intact; never touches text outside a match."""
    if not text:
        return text, False
    redacted = text
    total = 0
    for pattern in CAPTION_REDACTION_PATTERNS:
        redacted, n = pattern.subn(lambda m: _redact_group1(m, REDACTION_PLACEHOLDER), redacted)
        total += n
    return redacted, total > 0


REDACTION_PLACEHOLDER = '[plaintiff name withheld]'


def description_has_unredacted_caption(text):
    """True if any of the caption-redaction patterns still matches a captured span that is not
    already the redaction placeholder (used as a build-time and test-time invariant check on the
    text actually written to entries.jsonl -- the patterns legitimately still match a description
    that HAS been redacted, since the placeholder occupies the captured group's position)."""
    text = text or ''
    for pattern in CAPTION_REDACTION_PATTERNS:
        for match in pattern.finditer(text):
            if match.group(1).strip() != REDACTION_PLACEHOLDER:
                return True
    return False


def classify_entry_type(description):
    text = description or ''
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.search(text):
            return name
    return 'other'


def parse_entered_date(description):
    """Return (iso_date_or_None, basis_or_None). Only ever reads the trailing '(Entered: MM/DD/YYYY)'
    text; never infers a date any other way."""
    if not description:
        return None, None
    match = ENTERED_RE.search(description)
    if not match:
        return None, None
    month, day, year = (int(g) for g in match.groups())
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None, None
    return parsed.isoformat(), "entered date parsed from the trailing '(Entered: ...)' text of the docket entry"


def normalize_entry_number(value):
    """Return (text, int_or_None). entry_number is kept as a string always; the int form is only used
    internally for sorting and the cap-detection contiguity check, and is null when not purely numeric."""
    if value is None:
        return '', None
    text = str(value).strip()
    if text.isdigit():
        return text, int(text)
    return text, None


def compute_capped(min_en, max_en, count):
    """Return (capped_bool_or_None, basis_str). None means there were no numeric entry_number values to
    test. A gap >= CAP_GAP_THRESHOLD between the observed range and the row count is labelled capped."""
    if min_en is None or max_en is None or count == 0:
        return None, 'no numeric entry_number values were present on this matter\u2019s entries to test contiguity'
    span = max_en - min_en + 1
    missing = span - count
    if missing >= CAP_GAP_THRESHOLD:
        return True, (f'entry_number runs from {min_en} to {max_en} ({span} possible entry numbers) but only '
                       f'{count} rows are present in this release ({missing} missing) \u2014 rows beyond what this '
                       'release captured were not included; treat the total as a lower bound, not the full docket.')
    return False, (f'entry_number runs from {min_en} to {max_en} with only {max(missing, 0)} missing number(s) out of '
                    f'{span} \u2014 consistent with an ordinarily complete docket (stricken/renumbered entries), not a capture cap.')


def _iter_jsonl_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _iter_jsonl(path):
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load_crosswalk():
    validation = json.loads((CROSSWALK_DIR / 'validation.json').read_text(encoding='utf-8'))
    if validation.get('status') != 'passed' or validation.get('ready') is not True:
        raise ValueError('mdl_docket_crosswalk_20260919 validation is not passed/ready; refusing to build on it')
    crosswalk = list(_iter_jsonl(CROSSWALK_DIR / 'crosswalk.jsonl'))
    edges = list(_iter_jsonl(CROSSWALK_DIR / 'edges.jsonl'))

    by_number = {row['mdl_number']: row for row in crosswalk}
    matter_to_mdl = {}
    for row in crosswalk:
        if row.get('aws_matter_id'):
            matter_to_mdl[row['aws_matter_id']] = row['mdl_number']
    member_matters_by_mdl = {}
    for edge in edges:
        if edge.get('relation') == 'member_of_mdl' and edge.get('from', {}).get('type') == 'aws_matter':
            matter_id, mdl_number = edge['from']['id'], edge.get('to', {}).get('id')
            if mdl_number is not None:
                matter_to_mdl.setdefault(matter_id, mdl_number)
                member_matters_by_mdl.setdefault(mdl_number, []).append(matter_id)
    return {
        'by_number': by_number, 'matter_to_mdl': matter_to_mdl,
        'member_matters_by_mdl': member_matters_by_mdl,
        'crosswalk_inputs': [CROSSWALK_DIR / 'validation.json', CROSSWALK_DIR / 'crosswalk.jsonl', CROSSWALK_DIR / 'edges.jsonl'],
    }


def _load_verified_document_counts():
    """docket_entry_id -> verified document count. Reads s3_bucket/s3_key/sha256 only to confirm the
    row shape; those fields are never written to any output of this slice (s3 pointers are never exposed)."""
    counts = {}
    total = 0
    for row in _iter_jsonl_gz(DOCUMENTS_FILE):
        total += 1
        if row.get('verification_status') != 'verified':
            continue
        entry_id = row.get('docket_entry_id')
        if entry_id:
            counts[entry_id] = counts.get(entry_id, 0) + 1
    return counts, total


def _load_matter_lookup():
    """matter_id -> {'docket_number', 'court_id', 'cl_docket_id', 'cl_docket_id_basis'}, for every
    matter (master and member) in this release, never joined by name. docket_number/court_id come
    from matters.jsonl.gz's own fields; cl_docket_id comes from matter_aliases.jsonl.gz
    (namespace courtlistener_docket_id, else courtlistener_master_docket_id; record_status active
    only) -- the same join rule mdl_docket_crosswalk_20260919/build.py uses for its master matters,
    applied here to every matter_id (including member matters) so each docket entry's own matter can
    carry its member docket number, court and CourtListener docket id."""
    lookup = {}
    for row in _iter_jsonl_gz(MATTERS_FILE):
        matter_id = row.get('matter_id')
        if matter_id:
            lookup[matter_id] = {
                'docket_number': row.get('docket_number'), 'court_id': row.get('court_id'),
                'cl_docket_id': None, 'cl_docket_id_basis': None,
            }
    for row in _iter_jsonl_gz(MATTER_ALIASES_FILE):
        if row.get('record_status') != 'active':
            continue
        namespace = row.get('namespace')
        if namespace not in ('courtlistener_docket_id', 'courtlistener_master_docket_id'):
            continue
        entry = lookup.get(row.get('matter_id'))
        if entry is None:
            continue
        try:
            value = int(row['value'])
        except (TypeError, ValueError, KeyError):
            continue
        if namespace == 'courtlistener_docket_id':
            entry['cl_docket_id'] = value
            entry['cl_docket_id_basis'] = 'matter_aliases.courtlistener_docket_id'
        elif entry['cl_docket_id'] is None:
            entry['cl_docket_id'] = value
            entry['cl_docket_id_basis'] = 'matter_aliases.courtlistener_master_docket_id'
    return lookup


def _clean_text(value):
    if not isinstance(value, str):
        return ''
    return unicodedata.normalize('NFC', value)


def run(out_dir=None):
    out_dir = Path(out_dir) if out_dir else OUT_DIR
    cw = _load_crosswalk()
    matter_to_mdl = cw['matter_to_mdl']

    verified_counts, documents_total = _load_verified_document_counts()
    matter_lookup = _load_matter_lookup()

    entries = []
    unresolved = []
    docket_entries_total = 0
    for row in _iter_jsonl_gz(DOCKET_ENTRIES_FILE):
        docket_entries_total += 1
        matter_id = row.get('matter_id')
        mdl_number = matter_to_mdl.get(matter_id)
        entry_number_text, entry_number_int = normalize_entry_number(row.get('entry_number'))
        raw_description = _clean_text(row.get('description'))
        docket_entry_id = row.get('docket_entry_id')
        if mdl_number is None:
            unresolved.append({
                'id': docket_entry_id, 'matter_id': matter_id, 'entry_number': entry_number_text,
                'reason': 'matter_id has no MDL link in mdl_docket_crosswalk_20260919 (neither a master-matter row nor a member_of_mdl edge)',
            })
            continue
        # published_at and entry_type are classified/parsed BEFORE redaction: the '(Entered: ...)'
        # date and the keyword categories never fall inside a redacted plaintiff-name span.
        entry_type = classify_entry_type(raw_description)
        published_at, published_at_basis = parse_entered_date(raw_description)
        description, description_redacted = redact_description(raw_description)
        matter_info = matter_lookup.get(matter_id) or {}
        entries.append({
            'id': docket_entry_id,
            'mdl_number': mdl_number,
            'matter_id': matter_id,
            'entry_number': entry_number_text,
            'entry_number_int': entry_number_int,
            'description': description,
            'description_redacted': description_redacted,
            'redaction_basis': REDACTION_BASIS if description_redacted else None,
            'member_docket_number': matter_info.get('docket_number'),
            'court_id': matter_info.get('court_id'),
            'cl_docket_id': matter_info.get('cl_docket_id'),
            'cl_docket_id_basis': matter_info.get('cl_docket_id_basis'),
            'entry_type': entry_type,
            'published_at': published_at,
            'published_at_basis': published_at_basis,
            'has_verified_document': verified_counts.get(docket_entry_id, 0) > 0,
            'verified_document_count': verified_counts.get(docket_entry_id, 0),
            'record_status': row.get('record_status'),
            'schema_version_source': row.get('schema_version'),
        })

    entries.sort(key=lambda r: (r['mdl_number'], r['matter_id'], r['entry_number_int'] if r['entry_number_int'] is not None else -1))
    unresolved.sort(key=lambda r: (r['matter_id'], r['entry_number']))

    # Per-matter stats, for the capped-flag and for mdl_summary.jsonl.
    matter_stats = {}
    for row in entries:
        stats = matter_stats.setdefault(row['matter_id'], {
            'mdl_number': row['mdl_number'], 'count': 0, 'min_en': None, 'max_en': None,
            'counts_by_type': {t: 0 for t in ENTRY_TYPES}, 'dates': [],
        })
        stats['count'] += 1
        stats['counts_by_type'][row['entry_type']] += 1
        if row['entry_number_int'] is not None:
            stats['min_en'] = row['entry_number_int'] if stats['min_en'] is None else min(stats['min_en'], row['entry_number_int'])
            stats['max_en'] = row['entry_number_int'] if stats['max_en'] is None else max(stats['max_en'], row['entry_number_int'])
        if row['published_at']:
            stats['dates'].append(row['published_at'])

    mdls_matched = sorted({row['mdl_number'] for row in entries})
    mdl_summaries = []
    for mdl_number in mdls_matched:
        cw_row = cw['by_number'].get(mdl_number, {})
        matter_ids = sorted(mid for mid, st in matter_stats.items() if st['mdl_number'] == mdl_number)
        matters_out = []
        for matter_id in matter_ids:
            st = matter_stats[matter_id]
            capped, basis = compute_capped(st['min_en'], st['max_en'], st['count'])
            matters_out.append({
                'matter_id': matter_id, 'entries_count': st['count'],
                'min_entry_number': st['min_en'], 'max_entry_number': st['max_en'],
                'entries_capped': capped, 'entries_capped_basis': basis,
            })
        matters_out.sort(key=lambda m: -m['entries_count'])
        primary = matters_out[0] if matters_out else None
        total_by_type = {t: 0 for t in ENTRY_TYPES}
        all_dates = []
        for matter_id in matter_ids:
            st = matter_stats[matter_id]
            for t in ENTRY_TYPES:
                total_by_type[t] += st['counts_by_type'][t]
            all_dates.extend(st['dates'])
        total_entries = sum(matter_stats[m]['count'] for m in matter_ids)
        latest = sorted(
            (row for row in entries if row['mdl_number'] == mdl_number),
            key=lambda r: (r['published_at'] or '', r['entry_number_int'] if r['entry_number_int'] is not None else -1),
            reverse=True,
        )[:25]
        mdl_summaries.append({
            'mdl_number': mdl_number,
            'mdl_title': cw_row.get('mdl_title'),
            'mdl_status': cw_row.get('mdl_status'),
            'is_unique_to_this_slice': mdl_number in UNIQUE_MDLS,
            'total_entries': total_entries,
            'counts_by_type': total_by_type,
            'published_at_min': min(all_dates) if all_dates else None,
            'published_at_max': max(all_dates) if all_dates else None,
            'entries_with_no_parsed_date': total_entries - len(all_dates),
            'matters': matters_out,
            'primary_matter_id': primary['matter_id'] if primary else None,
            'entries_capped': primary['entries_capped'] if primary else None,
            'entries_capped_basis': primary['entries_capped_basis'] if primary else None,
            'latest_25_ids': [row['id'] for row in latest],
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    files = {'entries.jsonl': entries, 'unresolved.jsonl': unresolved, 'mdl_summary.jsonl': mdl_summaries}
    data_files = []
    for name, rows in files.items():
        path = out_dir / name
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        data_files.append({'path': name, 'sha256': _sha256(path), 'rows': len(rows)})

    unique_mdl_counts = {mdl: next((s['total_entries'] for s in mdl_summaries if s['mdl_number'] == mdl), 0) for mdl in UNIQUE_MDLS}
    capped_by_unique_mdl = {mdl: next((s['entries_capped'] for s in mdl_summaries if s['mdl_number'] == mdl), None) for mdl in UNIQUE_MDLS}

    matched_with_date = sum(1 for r in entries if r['published_at'])
    entries_with_redacted_caption = sum(1 for r in entries if r['description_redacted'])
    entries_with_cl_docket_id = sum(1 for r in entries if r.get('cl_docket_id') is not None)
    entries_with_member_docket_number = sum(1 for r in entries if r.get('member_docket_number'))

    counts = {
        'docket_entries_total': docket_entries_total,
        'docket_entries_matched_to_mdl': len(entries),
        'docket_entries_unresolved': len(unresolved),
        'distinct_mdl_numbers': len(mdls_matched),
        'documents_total_verified_status_only': documents_total,
        'docket_entry_ids_with_a_verified_document': sum(1 for c in verified_counts.values() if c > 0),
        'unique_mdl_entry_counts': unique_mdl_counts,
        'unique_mdl_entries_capped': capped_by_unique_mdl,
        'entry_type_counts_matched_only': {t: sum(1 for r in entries if r['entry_type'] == t) for t in ENTRY_TYPES},
        'entries_with_parsed_published_at': matched_with_date,
        'entries_with_redacted_caption': entries_with_redacted_caption,
        'entries_with_cl_docket_id': entries_with_cl_docket_id,
        'entries_with_member_docket_number': entries_with_member_docket_number,
    }

    checks = [
        {'name': 'docket_entries_total_41946', 'passed': docket_entries_total == 41946},
        {'name': 'matched_to_mdl_26549', 'passed': len(entries) == 26549},
        {'name': 'distinct_mdl_numbers_36', 'passed': len(mdls_matched) == 36},
        {'name': 'unique_six_mdl_counts_match_plan', 'passed': unique_mdl_counts == {2323: 2000, 3060: 1980, 2243: 1920, 1570: 1835, 2904: 936, 2921: 764}},
        {'name': 'entries_ids_unique', 'passed': len({r['id'] for r in entries}) == len(entries)},
        {'name': 'unresolved_plus_matched_equals_total', 'passed': len(entries) + len(unresolved) == docket_entries_total},
        {'name': 'no_s3_pointer_fields_in_entries_output', 'passed': all(('s3_key' not in r and 's3_bucket' not in r) for r in entries)},
        {'name': 'no_published_description_matches_plaintiff_caption_regexes',
         'passed': all(not description_has_unredacted_caption(r['description']) for r in entries)},
        {'name': 'brett_basanez_short_form_complaint_is_redacted',
         'passed': not any('Brett Basanez' in (r['description'] or '') for r in entries)},
    ]

    inputs = []
    for path in (DOCKET_ENTRIES_FILE, DOCUMENTS_FILE, MATTERS_FILE, MATTER_ALIASES_FILE, *cw['crosswalk_inputs']):
        inputs.append({'path': str(path), 'sha256': _sha256(path)})
    # qualification_short length is asserted once it is built, below.

    qualification = (
        'Local personal-testing view; derived from a private firm dataset (SW-BULK AWS release '
        'b2b-cdbb8d040b95c7b65cfc, built 2026-08-24) assembled from CourtListener/RECAP API data; not for '
        f'redistribution. {docket_entries_total:,} master/member-docket entries from docket_entries.jsonl.gz; '
        f'{len(entries):,} join to {len(mdls_matched)} of 176 JPML-registry MDLs via '
        'sources/mdl_docket_crosswalk_20260919 (matter_id -> mdl_number, master and member matters); '
        f'{len(unresolved):,} entries whose matter carries no MDL link are parked in unresolved.jsonl, never '
        'dropped or guessed. Unique contribution versus the separately-built mdl_docket_documents_20260919 '
        'slice: the 6 MDLs it does not reach at all (2323, 3060, 2243, 1570, 2904, 2921); for the 12 MDLs '
        'both slices cover, keep the two sources separate and labelled, never merged. There is no structured '
        'date field on a docket entry; published_at is parsed only from the trailing \u201c(Entered: '
        'MM/DD/YYYY)\u201d text of the entry description and left null when that pattern is absent '
        f'({matched_with_date:,} of the {len(entries):,} MDL-matched entries carry that text). Counts are as of this build '
        'date; "entries_capped" is measured per contributing matter by comparing the highest observed '
        'entry_number to the row count (a gap of 50 or more is treated as evidence rows beyond that number '
        'were not captured in this release) \u2014 several MDLs cluster near 2,000 entries, but only some of '
        'them (2323, 1570, plus 2873 and 2804 among the shared MDLs) show that gap; 2243, 2904, 2921 and 3060 '
        'are internally complete (contiguous entry numbers) and simply have that many entries. '
        'has_verified_document / verified_document_count come from documents.jsonl.gz filtered to '
        'verification_status == "verified" only; s3_bucket/s3_key/sha256 from that file are read but never '
        'published. Entry text is the court\u2019s own docket description; a deterministic redactor withholds '
        'the caption of an individual member-case plaintiff (short-form-complaint captions such as '
        '"Short Form Complaint - <Name> by PLAINTIFF(S)", "Plaintiff/Decedent <Name>" references, and the '
        'plaintiff side of an "X v. Y" party-style caption), replacing it with "[plaintiff name withheld]" '
        f'({entries_with_redacted_caption:,} of {len(entries):,} entries carry a redacted caption; the row\u2019s '
        '"description_redacted" and "redaction_basis" fields record this) \u2014 attorney/filer parentheticals '
        '(e.g. "(SEEGER, CHRISTOPHER)"), organisation names and named defendants stay verbatim, and text is '
        'never parsed into a person entity. Each entry also carries its own matter\u2019s '
        f'"member_docket_number"/"court_id" ({entries_with_member_docket_number:,} of {len(entries):,} entries) and '
        f'"cl_docket_id" ({entries_with_cl_docket_id:,} of {len(entries):,} entries, from matter_aliases.jsonl.gz, '
        'never guessed) so a redacted entry still identifies its underlying case and links out to CourtListener.'
    )

    qualification_short = (
        'Local personal-testing view of a private firm dataset (SW-BULK AWS release '
        'b2b-cdbb8d040b95c7b65cfc, built 2026-08-24) assembled from CourtListener/RECAP API data; not for '
        f'redistribution. Counts as of this build; {len(mdls_matched)} of 176 JPML MDLs covered. Where '
        'capped=true, this release did not capture every entry for that matter \u2014 treat the total as a '
        'measured lower bound, not the full docket.'
    )
    checks.append({'name': 'qualification_short_under_480_chars', 'passed': len(qualification_short) < 480})

    validation = {
        'schema_version': '1', 'status': 'passed', 'ready': True,
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': data_files, 'counts': counts, 'checks': checks,
        'qualification': qualification,
        'qualification_short': qualification_short,
        'license_ref': 'sw_bulk_private_firm_work_product', 'export_allowed': False,
        'inputs': inputs,
    }
    if not all(c['passed'] for c in checks):
        validation['status'] = 'failed'
        validation['ready'] = False
    (out_dir / 'validation.json').write_text(json.dumps(validation, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    return {'counts': counts, 'checks': checks, 'validation': validation}


if __name__ == '__main__':
    result = run()
    print(json.dumps(result['counts'], indent=2, sort_keys=True))
    print('checks:', all(c['passed'] for c in result['checks']))
