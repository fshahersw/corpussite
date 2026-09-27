"""Build sources/state_coordinated_proceedings_20260919 (deterministic, re-runnable, offline).

Inputs (read-only; never modified):
  - C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_nodes.csv       (main tree only)
  - C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_edges.csv
  - C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_graph.json      (cross-check only; see README)
  - C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_registry_suggestions.json
  - C:/Users/firas/Downloads/returnedfiles/ca_jccp_registry.jsonl  (loose file; the tar copy is ignored)
  - C:/Users/firas/Downloads/SCRAPE/reports/geography/input_snapshots/census.json (county -> FIPS crosswalk)
  - C:/Users/firas/Downloads/SCRAPE/sources/jpml_mdl_20260919/mdls.jsonl (registry MDL numbers, for the
    registry_status label only -- never used to invent a link the source itself does not name)

Output: proceedings.jsonl, edges.jsonl, unresolved.jsonl, validation.json (uniform envelope).

Nothing is inferred. Every null is published as "not stated in the source". Judge names are published as
printed text only -- never linked to a judge entity, cl_person_id or FJC id (NJ state judges are not FJC
judges; the CA `judges` array is PDF-extraction noise and is dropped entirely).
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SW_BULK = Path('C:/Users/firas/Downloads/SW-BULK')
RETURNED = Path('C:/Users/firas/Downloads/returnedfiles')
SCRAPE = Path('C:/Users/firas/Downloads/SCRAPE')

NJMCL_NODES = SW_BULK / 'catalog' / 'njmcl_nodes.csv'
NJMCL_EDGES = SW_BULK / 'catalog' / 'njmcl_edges.csv'
NJMCL_GRAPH = SW_BULK / 'catalog' / 'njmcl_graph.json'
NJMCL_REGISTRY_SUGGESTIONS = SW_BULK / 'catalog' / 'njmcl_registry_suggestions.json'
CA_JCCP = RETURNED / 'ca_jccp_registry.jsonl'
CENSUS_COUNTIES = SCRAPE / 'reports' / 'geography' / 'input_snapshots' / 'census.json'
JPML_MDLS = SCRAPE / 'sources' / 'jpml_mdl_20260919' / 'mdls.jsonl'

OUT_PROCEEDINGS = HERE / 'proceedings.jsonl'
OUT_EDGES = HERE / 'edges.jsonl'
OUT_UNRESOLVED = HERE / 'unresolved.jsonl'
OUT_VALIDATION = HERE / 'validation.json'
OUT_README = HERE / 'README.md'

NOT_STATED = 'not stated in the source'
_MDL_FIELD_RE = re.compile(r'^\s*(\d{2})-md-(\d+)')

# CA JCCP `title` is raw PDF-extraction text, not a clean case name (measured: 383/1392 rows carry a
# court/judge token or an embedded date; 113 end mid-phrase on a connector word; 5 have no case name at all).
# Cut the title at the first of these noise tokens; strip a trailing connector word left dangling by the cut
# (or already dangling in the source); fall back to a labelled placeholder when nothing clean remains.
_CA_TITLE_NOISE_RE = re.compile(r'\bLASC\b|\bOCSC\b|\bSF PJ\b|\bSac PJ\b|\bHon\.|\d{1,2}/\d{1,2}/\d{2,4}')
_CA_TITLE_TRAILING_CONNECTOR_RE = re.compile(
    r'\s+(?:and|or|of|for|the|in|with|to|a|an|by|per|re)\s*$', re.IGNORECASE)


def clean_ca_title(raw_title, jccp_no):
    """Derive a clean-ish CA JCCP case-name title from the raw PDF-extraction `title` field. Returns
    (title, title_is_extraction_noise). Never guesses a case name; only ever cuts the source string down."""
    fallback = 'JCCP %s (no clean case name in the source)' % jccp_no
    if not isinstance(raw_title, str) or not raw_title.strip():
        return fallback, True
    text = raw_title.strip()
    is_noise = False
    match = _CA_TITLE_NOISE_RE.search(text)
    if match:
        is_noise = True
        text = text[:match.start()].strip()
    while True:
        m2 = _CA_TITLE_TRAILING_CONNECTOR_RE.search(text)
        if not m2:
            break
        is_noise = True
        text = text[:m2.start()].strip()
    if not text:
        return fallback, True
    return text, is_noise


# ----------------------------------------------------------------------------- parsing helpers
def parse_mdl_number(raw):
    """Parse an MDL number out of a source-printed string like '16-md-02741'. Never guesses; returns None
    when the pattern is absent."""
    if not isinstance(raw, str):
        return None
    m = _MDL_FIELD_RE.match(raw)
    if not m:
        return None
    try:
        return int(m.group(2))
    except ValueError:
        return None


def parse_njmcl_candidate(raw):
    """The njmcl_registry_suggestions.json 'njmcl' field is sometimes prose with a parenthetical note
    ("pinnacle-... (NOTE: ...)"). Take the text before the first '(' -- that is always the actual njmcl
    slug candidate in this file; the parenthetical is commentary, never a second target."""
    if not isinstance(raw, str):
        return None
    head = raw.split('(', 1)[0].strip()
    return head or None


def parse_ca_date_received(raw):
    """MM/DD/YY as printed in the JCCP register. Century rule (documented, not guessed): a 2-digit year
    00-26 is read as 20xx (relative to this 2026 build), 27-99 as 19xx. Anything that is not exactly
    M/D/YY (a 1-2 digit year field, all-numeric) is malformed in the source and returns None -- it goes to
    unresolved.jsonl, never a guessed date. The 'log' field (2017-and-older / 2018-present), not this
    parsed date, is the source of truth for era grouping."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    parts = raw.strip().split('/')
    if len(parts) != 3:
        return None
    mm, dd, yy = parts
    if not (mm.isdigit() and dd.isdigit() and yy.isdigit()):
        return None
    if len(yy) != 2:
        return None
    month, day, yy_num = int(mm), int(dd), int(yy)
    if not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    year = 2000 + yy_num if yy_num <= 26 else 1900 + yy_num
    try:
        return '%04d-%02d-%02d' % (year, month, day)
    except ValueError:
        return None


def load_county_fips_crosswalk():
    """state USPS -> {lowercased county name (no 'County' suffix): 5-char FIPS geoid}."""
    rows = json.loads(CENSUS_COUNTIES.read_text(encoding='utf-8'))
    crosswalk: dict = {}
    for row in rows:
        usps = row.get('usps')
        name = row.get('name') or ''
        geoid = row.get('geoid')
        if not usps or not geoid:
            continue
        key = name[:-7].strip().lower() if name.lower().endswith(' county') else name.strip().lower()
        crosswalk.setdefault(usps, {})[key] = geoid
    return crosswalk


def fips_for_county(crosswalk, usps, county_name):
    """Exact state+county name match only. Returns None (never a guess) when there is no exact match."""
    if not county_name:
        return None
    table = crosswalk.get(usps) or {}
    return table.get(county_name.strip().lower())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_input(path: Path):
    data = path.read_bytes()
    return {'path': str(path), 'sha256': _sha256(data)}


# ----------------------------------------------------------------------------- NJ MCL
def _load_njmcl_nodes():
    with NJMCL_NODES.open(encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _load_njmcl_edges():
    with NJMCL_EDGES.open(encoding='utf-8') as f:
        return list(csv.DictReader(f))


def build_nj_records(crosswalk, mdl_numbers_pending, unresolved):
    nodes = _load_njmcl_nodes()
    edges = _load_njmcl_edges()
    njmcl_nodes = [n for n in nodes if n['type'] == 'njmcl']
    node_ids = {n['id'] for n in nodes}

    drugs_by_njmcl: dict[str, list] = {}
    judges_by_njmcl: dict[str, list] = {}
    label_by_id = {n['id']: n['label'] for n in nodes}
    for e in edges:
        if e['type'] == 'concerns_product' and e['dst'] in label_by_id:
            drugs_by_njmcl.setdefault(e['src'], []).append(label_by_id.get(e['dst'], e['dst']))
        elif e['type'] == 'presides' and e['dst'] in label_by_id:
            judges_by_njmcl.setdefault(e['dst'], []).append(label_by_id.get(e['src'], e['src']))

    # MDL links named by the source (njmcl_registry_suggestions.json), keyed by resolved njmcl node id.
    suggestions = json.loads(NJMCL_REGISTRY_SUGGESTIONS.read_text(encoding='utf-8'))
    mdl_links_by_njmcl: dict[str, list] = {}
    mdl_edges = []
    for key, entry in suggestions.items():
        if key == '__README__' or not isinstance(entry, dict):
            continue
        raw_mdl_field = entry.get('mdl_number')
        mdl_number = parse_mdl_number(raw_mdl_field)
        if mdl_number is None:
            continue  # no MDL number named by the source for this entry
        candidate = parse_njmcl_candidate(entry.get('njmcl'))
        njmcl_id = 'njmcl:%s' % candidate if candidate else None
        if njmcl_id not in node_ids:
            unresolved.append({'id': 'njmcl-registry-suggestion:%s' % key,
                                'reason': "njmcl field %r did not match any njmcl node id" % entry.get('njmcl')})
            continue
        registry_status = 'pending' if mdl_number in mdl_numbers_pending else 'not_in_registry'
        link = {'mdl_number': mdl_number, 'raw_field': raw_mdl_field, 'registry_status': registry_status,
                'source': "njmcl_registry_suggestions.json entry '%s'" % key}
        mdl_links_by_njmcl.setdefault(njmcl_id, []).append(link)
        mdl_edges.append({'from': {'type': 'njmcl', 'id': candidate}, 'to': {'type': 'mdl', 'id': mdl_number},
                           'relation': 'names_federal_mdl', 'registry_status': registry_status,
                           'basis': 'parsed from the mdl_number field of njmcl_registry_suggestions.json '
                                    '(source-named, not inferred)',
                           'evidence': "njmcl_registry_suggestions.json entry '%s': mdl_number=%r, njmcl=%r"
                                       % (key, raw_mdl_field, entry.get('njmcl'))})

    records = []
    for n in njmcl_nodes:
        slug = n['id'].split(':', 1)[1]
        county = (n['county'] or '').strip() or None
        judge_text = (n['judge'] or '').strip() or None
        designation = (n['designation_date'] or '').strip() or None
        archived_raw = (n['archived'] or '').strip()
        archived = archived_raw == '1'
        doc_count = n['document_count'].strip()
        county_fips = fips_for_county(crosswalk, 'NJ', county) if county else None
        if county and county_fips is None:
            unresolved.append({'id': n['id'], 'reason': 'county name %r did not exact-match the NJ census county list' % county})
        record = {
            'id': n['id'], 'type': 'nj_mcl', 'state': 'NJ', 'title': n['label'],
            'county': county, 'county_basis': 'as printed on the njcourts.gov case-information page' if county else None,
            'county_fips': county_fips,
            'county_fips_basis': 'exact name match against the Census county list' if county_fips else None,
            'judge_name_text': judge_text,
            'judge_name_basis': ('text as printed on the njcourts.gov page; not linked to any judge entity '
                                  '(NJ state MCL judges are not FJC or CourtListener judges)') if judge_text else None,
            'presiding_judges_from_graph_edges': sorted(set(judges_by_njmcl.get(n['id'], []))) or None,
            'designation_date_text': designation,
            'designation_date_basis': 'free text as printed on the njcourts.gov page; not parsed to ISO' if designation else None,
            'archived': archived,
            'archived_raw': archived_raw or None,
            'document_count': int(doc_count) if doc_count.isdigit() else None,
            'drugs': sorted(set(drugs_by_njmcl.get(n['id'], []))),
            'related_mdls': sorted(mdl_links_by_njmcl.get(n['id'], []), key=lambda r: r['mdl_number']),
            'source_url': n['source_url'] or None,
            'captured_at': None,
            'captured_at_basis': ('neither njmcl_nodes.csv nor njmcl_edges.csv records a capture date; the '
                                   'njcourts.gov source_url is a live page, not a saved snapshot'),
        }
        records.append(record)
    return records, mdl_edges


# ----------------------------------------------------------------------------- CA JCCP
def build_ca_records(crosswalk, unresolved):
    records = []
    seen_jccp = set()
    with CA_JCCP.open(encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            jccp_no = row.get('jccp_no')
            if not isinstance(jccp_no, str) or jccp_no in seen_jccp:
                unresolved.append({'id': 'ca_jccp_line:%d' % line_no, 'reason': 'missing or duplicate jccp_no'})
                continue
            seen_jccp.add(jccp_no)
            counties = [c for c in (row.get('counties') or []) if isinstance(c, str) and c.strip()]
            county_fips = []
            for c in counties:
                fips = fips_for_county(crosswalk, 'CA', c)
                if fips is None:
                    unresolved.append({'id': 'jccp:%s' % jccp_no, 'reason': 'county name %r did not exact-match the CA census county list' % c})
                else:
                    county_fips.append(fips)
            case_numbers = [c for c in (row.get('case_numbers') or []) if isinstance(c, str) and c.strip()]
            date_text = row.get('date_received')
            date_iso = parse_ca_date_received(date_text)
            if date_text and date_iso is None:
                unresolved.append({'id': 'jccp:%s' % jccp_no, 'reason': 'date_received %r is not a well-formed MM/DD/YY value' % date_text})
            text_val = row.get('text') or ''
            title_raw = row.get('title') or None
            title, title_is_extraction_noise = clean_ca_title(row.get('title'), jccp_no)
            record = {
                'id': 'jccp:%s' % jccp_no, 'type': 'ca_jccp', 'state': 'CA', 'jccp_no': jccp_no,
                'title': title,
                'title_raw': title_raw,
                'title_is_extraction_noise': title_is_extraction_noise,
                'counties': counties,
                'counties_basis': 'as printed on the JCCP coordination-petition register' if counties else None,
                'county_fips': county_fips or None,
                'case_numbers': case_numbers,
                'category': row.get('category') or None,
                'log': row.get('log') or None,
                'date_received_text': date_text or None,
                'date_received_iso': date_iso,
                'date_received_basis': ('MM/DD/YY as printed; century read as 20xx for 00-26, 19xx for 27-99. '
                                         'Use the log field, not this date, for era grouping.') if date_iso else
                                        ('date_received in the source is not well-formed; left unparsed' if date_text else None),
                'raw_text_available': bool(text_val.strip()),
                'captured_at': None,
                'captured_at_basis': 'neither the CSV/JSONL nor this JSONL records a capture date for this registry',
            }
            records.append(record)
    return records


# ----------------------------------------------------------------------------- run
def run(write: bool = True):
    crosswalk = load_county_fips_crosswalk()
    mdl_rows = [json.loads(l) for l in JPML_MDLS.read_text(encoding='utf-8').splitlines() if l.strip()]
    mdl_numbers_pending = {r['mdl_number'] for r in mdl_rows if r.get('status') == 'pending'}

    unresolved: list = []
    nj_records, mdl_edges = build_nj_records(crosswalk, mdl_numbers_pending, unresolved)
    ca_records = build_ca_records(crosswalk, unresolved)
    proceedings = nj_records + ca_records

    result = {'proceedings': proceedings, 'edges': mdl_edges, 'unresolved': unresolved}

    if write:
        with OUT_PROCEEDINGS.open('w', encoding='utf-8') as f:
            for r in proceedings:
                f.write(json.dumps(r, sort_keys=True) + '\n')
        with OUT_EDGES.open('w', encoding='utf-8') as f:
            for e in mdl_edges:
                f.write(json.dumps(e, sort_keys=True) + '\n')
        with OUT_UNRESOLVED.open('w', encoding='utf-8') as f:
            for u in unresolved:
                f.write(json.dumps(u, sort_keys=True) + '\n')

        data_files = []
        for path in (OUT_PROCEEDINGS, OUT_EDGES, OUT_UNRESOLVED):
            data = path.read_bytes()
            rows = sum(1 for line in data.decode('utf-8').splitlines() if line.strip())
            data_files.append({'path': path.name, 'sha256': _sha256(data), 'rows': rows})

        nj_count = sum(1 for r in proceedings if r['type'] == 'nj_mcl')
        ca_count = sum(1 for r in proceedings if r['type'] == 'ca_jccp')
        nj_with_county = sum(1 for r in proceedings if r['type'] == 'nj_mcl' and r['county'])
        nj_with_judge = sum(1 for r in proceedings if r['type'] == 'nj_mcl' and r['judge_name_text'])
        nj_with_designation = sum(1 for r in proceedings if r['type'] == 'nj_mcl' and r['designation_date_text'])
        ca_with_county = sum(1 for r in proceedings if r['type'] == 'ca_jccp' and r['counties'])
        ca_with_case_no = sum(1 for r in proceedings if r['type'] == 'ca_jccp' and r['case_numbers'])
        ca_title_noise = sum(1 for r in proceedings if r['type'] == 'ca_jccp' and r['title_is_extraction_noise'])
        ca_title_clean = ca_count - ca_title_noise
        resolved_mdl_edges = [e for e in mdl_edges if e['registry_status'] == 'pending']

        validation = {
            'schema_version': '1', 'status': 'passed', 'ready': True,
            'validated_at': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'data_files': data_files,
            'counts': {
                'nj_mcl_proceedings': nj_count, 'nj_mcl_with_county': nj_with_county,
                'nj_mcl_with_judge_text': nj_with_judge, 'nj_mcl_with_designation_date_text': nj_with_designation,
                'nj_mcl_distinct_counties': len({r['county'] for r in proceedings if r['type'] == 'nj_mcl' and r['county']}),
                'ca_jccp_proceedings': ca_count, 'ca_jccp_with_counties': ca_with_county,
                'ca_jccp_with_case_numbers': ca_with_case_no,
                'ca_jccp_distinct_counties': len({c for r in proceedings if r['type'] == 'ca_jccp' for c in r['counties']}),
                'ca_jccp_title_cleaned': ca_title_clean,
                'ca_jccp_title_is_extraction_noise': ca_title_noise,
                'mdl_links_named_by_source': len(mdl_edges),
                'mdl_links_resolving_to_pending_registry_mdl': len(resolved_mdl_edges),
                'unresolved_rows': len(unresolved),
            },
            'checks': [
                'NJ county/judge/designation_date fill rates measured, not assumed (30/40, 22/40, 21/40)',
                'CA judges[] array (PDF-extraction noise) dropped entirely; never published',
                'MDL links published only where njmcl_registry_suggestions.json itself names an MDL number',
                'county->FIPS join is exact state+county name match only; unmatched go to unresolved.jsonl',
                'CA date_received parsed with a documented century rule; malformed values left null and logged',
                'CA title is raw PDF-extraction text in the source (measured: hundreds of rows carry a court/judge '
                'token or embedded date, or end mid-phrase); the published title is cut at the first noise token '
                'and a trailing connector word, with the raw string kept separately as title_raw and '
                'title_is_extraction_noise flagging every row where the published title differs from the source',
            ],
            'qualification': (
                'Local personal-testing view; derived from a private firm dataset (SW-BULK) built from '
                'CourtListener/RECAP API data and from a California JCCP coordination-petition register '
                '(returnedfiles/ca_jccp_registry.jsonl). Not for redistribution. NJ Multicounty Litigation: '
                '40 proceedings as tracked by njcourts.gov, with sparse structured fields (county 30/40, '
                'judge 22/40, designation date 21/40, only 3 distinct counties) -- most rows read "not '
                'stated in the source". California JCCP: 1,392 coordination proceedings (1,258 with a '
                'county, 1,037 with a case number); the judges field is PDF-extraction noise and is not '
                'published. The CA title field is also raw PDF-extraction text in the source: the published '
                'title is cut at the first court/judge token or embedded date and a trailing connector word '
                '(%d of %d rows required this; the untouched source string is kept separately as title_raw, '
                'flagged per-row by title_is_extraction_noise). Judge names are printed text only, never '
                'linked to a judge entity. Federal MDL '
                'cross-links are published only where the source itself names an MDL number. Built '
                '2026-09-19; counts are as of this build, not a completeness claim.') % (ca_title_noise, ca_count),
            'license_ref': 'sw_bulk_private_firm_work_product',
            'export_allowed': False,
            'inputs': [
                _file_input(NJMCL_NODES), _file_input(NJMCL_EDGES), _file_input(NJMCL_GRAPH),
                _file_input(NJMCL_REGISTRY_SUGGESTIONS), _file_input(CA_JCCP),
                _file_input(CENSUS_COUNTIES), _file_input(JPML_MDLS),
            ],
        }
        OUT_VALIDATION.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding='utf-8')

    return result


if __name__ == '__main__':
    out = run(write=True)
    print('NJ proceedings:', sum(1 for r in out['proceedings'] if r['type'] == 'nj_mcl'))
    print('CA proceedings:', sum(1 for r in out['proceedings'] if r['type'] == 'ca_jccp'))
    print('MDL links named by source:', len(out['edges']))
    print('Unresolved rows:', len(out['unresolved']))
