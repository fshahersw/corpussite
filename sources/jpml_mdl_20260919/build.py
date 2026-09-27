"""Offline, re-runnable build of the JPML MDL registry (sources/jpml_mdl_20260919).

Inputs (all local, verified by hash before use):
  receipts.jsonl + raw/*          original bytes saved by fetch.py (16 receipts)
  text/*.txt + text/manifest.json pypdf reading copies of the 11 PDFs
  court_map.json                  curated JPML district code -> CourtListener / FJC court crosswalk (written by this
                                  script from CURATED_COURTS, then verified against read-only catalog tables)
  delivery/judge_enrichment_20260914/federal_biographies/{observations,facts}.jsonl   FJC names + appointments
  sources/judge_entities_20260918/entities.jsonl                                       fjc nid -> judge entity id
  sources/judge_presentation_20260918/profiles.sqlite3 (read-only)                     Judges UI entity ids
  sources/courtlistener_people_20260918/catalog.sqlite3 (read-only)                    CL court ids / people.fjc_id
  sources/local_library_presentation_20260918/mdl-3080.jsonl                           local MDL-3080 collection
  connector/*.json (optional)     saved CourtListener MCP responses (labelled connector responses)

Outputs: mdls.jsonl, documents.jsonl, court_map.json, edges.jsonl, unresolved.jsonl, qa.json, validation.json.
No network access. Nothing outside this folder is written.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RAW = HERE / 'raw'
TEXT = HERE / 'text'
CONNECTOR = HERE / 'connector'

ENRICH = ROOT / 'delivery/judge_enrichment_20260914/federal_biographies'
ENTITIES = ROOT / 'sources/judge_entities_20260918/entities.jsonl'
PROFILES_DB = ROOT / 'sources/judge_presentation_20260918/profiles.sqlite3'
CL_DB = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
MDL3080 = ROOT / 'sources/local_library_presentation_20260918/mdl-3080.jsonl'
ALIASES = ROOT / 'sources/judge_aliases_20260919'  # printed-name aliases resolved by native ids (CourtListener person == FJC jid bridge)
NATIVE_BRIDGE = 'cl_person_native_bridge'
DESIGNATION = 'sitting_by_designation_or_intercircuit_assignment'

REPORT_KINDS = {6: 'by_district', 7: 'by_mdl_number', 8: 'by_mdl_type', 9: 'by_actions_pending', 10: 'recently_terminated',
                11: 'by_circuit', 12: 'by_circuit', 13: 'panel_roster', 14: 'cy_statistics',
                15: 'fy_terminated_cumulative', 16: 'fy_statistical_analysis'}
DOC_TITLES = {13: 'Roster of Current and Former Judges of the United States Judicial Panel on Multidistrict Litigation',
              14: 'JPML Calendar Year Statistics, January through December 2025',
              15: 'JPML Fiscal Year 2025 Terminated Litigations Report (cumulative)',
              16: 'JPML Fiscal Year 2025 Statistical Analysis of Multidistrict Litigation'}
SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}
CIRCUITS = ('District of Columbia', 'First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth', 'Seventh', 'Eighth', 'Ninth',
            'Tenth', 'Eleventh', 'Federal')

# JPML CM/ECF district code -> (CourtListener court id, FJC court name as printed in the FJC biographical export).
# Curated crosswalk by the builder; every entry is verified below against the read-only CourtListener courts table and
# the Judges-UI court strings. Entries that fail verification are published with null ids (conservative).
CURATED_COURTS = {
    'ALN': ('alnd', 'Northern District of Alabama'), 'ALM': ('almd', 'Middle District of Alabama'), 'ALS': ('alsd', 'Southern District of Alabama'),
    'AK': ('akd', 'District of Alaska'), 'AZ': ('azd', 'District of Arizona'), 'ARE': ('ared', 'Eastern District of Arkansas'),
    'ARW': ('arwd', 'Western District of Arkansas'), 'CAN': ('cand', 'Northern District of California'), 'CAE': ('caed', 'Eastern District of California'),
    'CAC': ('cacd', 'Central District of California'), 'CAS': ('casd', 'Southern District of California'), 'CO': ('cod', 'District of Colorado'),
    'CT': ('ctd', 'District of Connecticut'), 'DE': ('ded', 'District of Delaware'), 'DC': ('dcd', 'District of Columbia'),
    'FLN': ('flnd', 'Northern District of Florida'), 'FLM': ('flmd', 'Middle District of Florida'), 'FLS': ('flsd', 'Southern District of Florida'),
    'GAN': ('gand', 'Northern District of Georgia'), 'GAM': ('gamd', 'Middle District of Georgia'), 'GAS': ('gasd', 'Southern District of Georgia'),
    'HI': ('hid', 'District of Hawaii'), 'ID': ('idd', 'District of Idaho'), 'ILN': ('ilnd', 'Northern District of Illinois'),
    'ILC': ('ilcd', 'Central District of Illinois'), 'ILS': ('ilsd', 'Southern District of Illinois'), 'INN': ('innd', 'Northern District of Indiana'),
    'INS': ('insd', 'Southern District of Indiana'), 'IAN': ('iand', 'Northern District of Iowa'), 'IAS': ('iasd', 'Southern District of Iowa'),
    'KS': ('ksd', 'District of Kansas'), 'KYE': ('kyed', 'Eastern District of Kentucky'), 'KYW': ('kywd', 'Western District of Kentucky'),
    'LAE': ('laed', 'Eastern District of Louisiana'), 'LAM': ('lamd', 'Middle District of Louisiana'), 'LAW': ('lawd', 'Western District of Louisiana'),
    'ME': ('med', 'District of Maine'), 'MD': ('mdd', 'District of Maryland'), 'MA': ('mad', 'District of Massachusetts'),
    'MIE': ('mied', 'Eastern District of Michigan'), 'MIW': ('miwd', 'Western District of Michigan'), 'MN': ('mnd', 'District of Minnesota'),
    'MSN': ('msnd', 'Northern District of Mississippi'), 'MSS': ('mssd', 'Southern District of Mississippi'), 'MOE': ('moed', 'Eastern District of Missouri'),
    'MOW': ('mowd', 'Western District of Missouri'), 'MT': ('mtd', 'District of Montana'), 'NE': ('ned', 'District of Nebraska'),
    'NV': ('nvd', 'District of Nevada'), 'NH': ('nhd', 'District of New Hampshire'), 'NJ': ('njd', 'District of New Jersey'),
    'NM': ('nmd', 'District of New Mexico'), 'NYN': ('nynd', 'Northern District of New York'), 'NYS': ('nysd', 'Southern District of New York'),
    'NYE': ('nyed', 'Eastern District of New York'), 'NYW': ('nywd', 'Western District of New York'), 'NCE': ('nced', 'Eastern District of North Carolina'),
    'NCM': ('ncmd', 'Middle District of North Carolina'), 'NCW': ('ncwd', 'Western District of North Carolina'), 'ND': ('ndd', 'District of North Dakota'),
    'OHN': ('ohnd', 'Northern District of Ohio'), 'OHS': ('ohsd', 'Southern District of Ohio'), 'OKN': ('oknd', 'Northern District of Oklahoma'),
    'OKE': ('oked', 'Eastern District of Oklahoma'), 'OKW': ('okwd', 'Western District of Oklahoma'), 'OR': ('ord', 'District of Oregon'),
    'PAE': ('paed', 'Eastern District of Pennsylvania'), 'PAM': ('pamd', 'Middle District of Pennsylvania'), 'PAW': ('pawd', 'Western District of Pennsylvania'),
    'PR': ('prd', 'District of Puerto Rico'), 'RI': ('rid', 'District of Rhode Island'), 'SC': ('scd', 'District of South Carolina'),
    'SD': ('sdd', 'District of South Dakota'), 'TNE': ('tned', 'Eastern District of Tennessee'), 'TNM': ('tnmd', 'Middle District of Tennessee'),
    'TNW': ('tnwd', 'Western District of Tennessee'), 'TXN': ('txnd', 'Northern District of Texas'), 'TXE': ('txed', 'Eastern District of Texas'),
    'TXS': ('txsd', 'Southern District of Texas'), 'TXW': ('txwd', 'Western District of Texas'), 'UT': ('utd', 'District of Utah'),
    'VT': ('vtd', 'District of Vermont'), 'VAE': ('vaed', 'Eastern District of Virginia'), 'VAW': ('vawd', 'Western District of Virginia'),
    'WAE': ('waed', 'Eastern District of Washington'), 'WAW': ('wawd', 'Western District of Washington'), 'WVN': ('wvnd', 'Northern District of West Virginia'),
    'WVS': ('wvsd', 'Southern District of West Virginia'), 'WIE': ('wied', 'Eastern District of Wisconsin'), 'WIW': ('wiwd', 'Western District of Wisconsin'),
    'WY': ('wyd', 'District of Wyoming'), 'GU': ('gud', 'Guam'), 'VI': ('vid', 'Virgin Islands'), 'NMI': ('nmid', 'Northern Mariana Islands'),
}


# ----------------------------------------------------------------------------- helpers
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def iso_date(mdy: str | None) -> str | None:
    """'9/1/2026' or '09/01/2026' -> '2026-09-01'."""
    if not mdy:
        return None
    m = re.fullmatch(r'\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*', mdy)
    if not m:
        return None
    return '%s-%02d-%02d' % (m.group(3), int(m.group(1)), int(m.group(2)))


STRICT_NUM = re.compile(r'\d{1,3}(?:,\d{3})*')
PLAIN_NUM = re.compile(r'\d+')


def to_int(token: str) -> int:
    return int(token.replace(',', ''))


def split_glued_pair(token: str):
    """Split '69,25071,935' into (69250, 71935) using the thousands-separator grammar.
    Returns (left, right, note); values are None when the split is not unique."""
    if not token:
        return None, None, 'empty'
    if ',' not in token:
        return None, None, 'ambiguous_no_thousands_separator'
    cands = []
    for i in range(1, len(token)):
        left, right = token[:i], token[i:]
        if STRICT_NUM.fullmatch(left) and STRICT_NUM.fullmatch(right) and to_int(left) <= to_int(right):
            cands.append((to_int(left), to_int(right)))
    if len(cands) == 1:
        return cands[0][0], cands[0][1], 'split_by_thousands_grammar'
    return None, None, 'ambiguous_split:%d' % len(cands)


def parse_pair(numtext: str):
    """'3,734 3,980' | '5 466' | '69,25071,935' -> (pending, total, note)."""
    toks = numtext.split()
    if len(toks) == 2 and all(PLAIN_NUM.fullmatch(t.replace(',', '')) for t in toks):
        return to_int(toks[0]), to_int(toks[1]), 'two_tokens'
    if len(toks) == 1:
        return split_glued_pair(toks[0])
    return None, None, 'unparsed_numbers:%r' % numtext


def split_count_pct(run: str):
    """'191,91394.11' -> (191913, '94.11'); '157' + '0.08' arrive separately."""
    for i in range(1, len(run)):
        left, right = run[:i], run[i:]
        if STRICT_NUM.fullmatch(left) and re.fullmatch(r'\d{1,3}(?:\.\d+)?', right) and float(right) <= 100:
            return to_int(left), right
    return None, None


def norm_ws(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def nospace(text: str) -> str:
    return re.sub(r'\s+', '', text or '')


def strip_title_prefix(chunk: str, title_ns: str):
    """Consume chunk (ignoring whitespace) until the whitespace-free title is matched; return (title_part, rest)."""
    i = j = 0
    while i < len(chunk) and j < len(title_ns):
        c = chunk[i]
        if c.isspace():
            i += 1
            continue
        if c != title_ns[j]:
            return None
        i += 1
        j += 1
    if j < len(title_ns):
        return None
    return chunk[:i], chunk[i:]


def name_tokens(name: str):
    s = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    s = s.replace('.', ' ').replace(',', ' ')
    toks = [t.lower() for t in s.split()]
    return [t for t in toks if t not in SUFFIXES]


def name_suffix(name: str):
    s = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().replace('.', ' ').replace(',', ' ')
    found = [t.lower() for t in s.split() if t.lower() in SUFFIXES]
    return found[-1] if found else None


def name_match_kind(jpml_toks, fjc_toks, jpml_suffix=None, fjc_suffix=None):
    """Conservative name comparison on normalized tokens (case/punctuation/accents folded).
    exact          : identical tokens
    middle_initial : first + last identical; each middle token identical or one side is the other's initial
    middle_omitted : JPML prints no middle name, FJC does (first + last identical)
    first_initial  : JPML prints a first initial ('K. Michael Moore'); middle name(s) and last identical
    Generational suffixes must agree when both sides print one (Orrick Jr. vs Orrick III)."""
    if not jpml_toks or not fjc_toks:
        return None
    if jpml_suffix and fjc_suffix and jpml_suffix != fjc_suffix:
        return None
    if jpml_toks == fjc_toks:
        return 'exact'
    if len(jpml_toks) < 2 or len(fjc_toks) < 2:
        return None
    if jpml_toks[-1] != fjc_toks[-1]:
        return None
    jm, fm = jpml_toks[1:-1], fjc_toks[1:-1]
    if jpml_toks[0] == fjc_toks[0]:
        if len(jm) == len(fm):
            ok = all(a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b)) for a, b in zip(jm, fm))
            return 'middle_initial' if ok else None
        if not jm and fm:
            return 'middle_omitted'
        return None
    if len(jpml_toks[0]) == 1 and fjc_toks[0].startswith(jpml_toks[0]) and jm and jm == fm:
        return 'first_initial'
    return None


def last_name_candidates(printed_name: str):
    """'Haywood S. Gilliam, Jr' -> ['S. Gilliam', 'Gilliam'] style candidates for the 'Last, First' form (longest first)."""
    base = re.sub(r',\s*(Jr|Sr|II|III|IV)\.?\s*$', '', printed_name.strip())
    toks = base.split()
    out = []
    for k in (3, 2, 1):
        if len(toks) > k:
            out.append(' '.join(toks[-k:]))
    if len(toks) >= 1 and ' '.join(toks[-1:]) not in out:
        out.append(toks[-1])
    return out


# ----------------------------------------------------------------------------- receipts / documents
def load_receipts():
    rows = []
    for line in (HERE / 'receipts.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_seeds():
    out = {}
    for line in (HERE / 'seeds.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            s = json.loads(line)
            out[s['seq']] = s
    return out


def verify_inputs(receipts, manifest):
    """Every raw file must match its receipt; every text file must match the manifest. Raises on mismatch."""
    inputs = []
    for r in receipts:
        p = HERE / r['raw_path']
        data = p.read_bytes()
        if sha256_bytes(data) != r['sha256'] or len(data) != r['bytes']:
            raise SystemExit('raw file does not match its receipt: %s' % r['raw_path'])
        inputs.append({'path': 'sources/jpml_mdl_20260919/' + r['raw_path'], 'sha256': r['sha256']})
    for m in manifest:
        p = HERE / m['text']
        data = p.read_bytes()
        on_disk = sha256_bytes(data)
        # extract_text.py hashed the LF string before write_text() stored it with CRLF on Windows; accept either form.
        if on_disk != m['text_sha256'] and sha256_bytes(data.replace(b'\r\n', b'\n')) != m['text_sha256']:
            raise SystemExit('text file does not match text/manifest.json: %s' % m['text'])
        if sha256_file(HERE / m['raw']) != m['raw_sha256']:
            raise SystemExit('manifest raw hash mismatch: %s' % m['raw'])
        inputs.append({'path': 'sources/jpml_mdl_20260919/' + m['text'], 'sha256': on_disk,
                       'note': None if on_disk == m['text_sha256'] else 'manifest text_sha256 is of the LF form; on-disk bytes are CRLF'})
    for name in ('receipts.jsonl', 'seeds.jsonl', 'text/manifest.json'):
        inputs.append({'path': 'sources/jpml_mdl_20260919/' + name, 'sha256': sha256_file(HERE / name)})
    return inputs


def text_for(seq: int) -> str:
    matches = sorted(TEXT.glob('%02d_*.txt' % seq))
    return matches[0].read_text(encoding='utf-8') if matches else ''


def html_title(data: bytes) -> str | None:
    m = re.search(rb'<title>(.*?)</title>', data, re.S | re.I)
    if not m:
        return None
    t = norm_ws(m.group(1).decode('utf-8', 'replace'))
    return t.replace('&amp;', '&').replace('&#039;', "'") or None


def build_documents(receipts, seeds, manifest):
    by_raw = {m['raw']: m for m in manifest}
    docs = []
    for r in receipts:
        seq = r['seq']
        seed = seeds.get(seq, {})
        raw_path = r['raw_path']
        p = HERE / raw_path
        kind = r['kind']
        mime = {'robots': 'text/plain', 'landing_page': 'text/html', 'report_pdf': 'application/pdf'}.get(kind, 'application/octet-stream')
        report_kind = REPORT_KINDS.get(seq) if kind == 'report_pdf' else ('landing_page' if kind == 'landing_page' else 'robots')
        text = text_for(seq) if kind == 'report_pdf' else ''
        title, report_date, basis = None, None, None
        if kind == 'report_pdf':
            m = re.search(r'Report Date: (\d{1,2}/\d{1,2}/\d{4})', text)
            if m:
                report_date, basis = iso_date(m.group(1)), 'printed "Report Date" in the CM/ECF report header'
                m2 = re.search(r'MDL Statistics Report - ([^\n]+)', text)
                title = 'JPML MDL Statistics Report - %s' % norm_ws(m2.group(1)) if m2 else None
            else:
                title = DOC_TITLES.get(seq)
                fm = re.search(r'-(\d{1,2})-(\d{1,2})-(\d{2,4})\.pdf$', raw_path) or re.search(r'-([A-Z][a-z]+)-(\d{1,2})-(\d{4})\.pdf$', raw_path)
                if seq == 13:
                    report_date, basis = '2026-06-03', 'date in the publisher file name (Panel Judges Roster-6-3-2026); not a printed report date'
                elif seq == 14:
                    report_date, basis = '2026-01-16', 'date in the publisher file name (1-16-26-Final); covers calendar year 2025'
                else:
                    report_date, basis = None, 'no report date printed in the extracted text; period from the publisher label only'
        elif kind == 'landing_page':
            title = html_title(p.read_bytes())
        else:
            title = 'robots.txt'
        man = by_raw.get(raw_path)
        docs.append({
            'document_id': 'jpmldoc-' + r['sha256'][:16],
            'seq': seq, 'kind': kind, 'report_kind': report_kind,
            'title': title or seed.get('report') or raw_path,
            'filename': Path(raw_path).name, 'mime': mime, 'bytes': r['bytes'], 'sha256': r['sha256'],
            'seed_url': r['url'], 'final_url': r.get('final_url'), 'http_status': r.get('status'),
            'parent_page': seed.get('parent_page'), 'period_label': seed.get('period_label'),
            'report_date': report_date, 'report_date_basis': basis,
            'http_last_modified': (r.get('headers') or {}).get('Last-Modified'),
            'captured_at': r.get('completed_at') or r.get('requested_at'),
            'captured_at_basis': 'fetch receipt completed_at (UTC)',
            'raw_path': raw_path, 'pages': man['pages'] if man else None,
            'text_chars': man['chars'] if man else None,
            'why': seed.get('why'),
        })
    return docs


# ----------------------------------------------------------------------------- report parsers
FOOTER_RE = re.compile(r'\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2} [AP]M CM/ECF for JPML \(LIVE\)\s*')
URL_RE = re.compile(r'https://jpml-ecf\.sso\.dcn/\S+ \d+/\d+')
PAGE_HEADER_RE = re.compile(r'United States Judicial Panel on Multidistrict Litigation Report Date:.*?Actions\s*\(Historical\)', re.S)
ROW_RE = re.compile(r'^\s*(?P<num>\d{4})\s+IN RE:\s*(?P<rest>.+?)\s*(?<![A-Z])(?P<dist>[A-Z]{2,3})\s+'
                    r'(?P<master>\d:\d{2}-[a-z]{2}-\d+?)\s*(?P<filed>\d{2}/\d{2}/\d{4})\s+(?P<transferred>\d{2}/\d{2}/\d{4})'
                    r'(?:\s+(?P<closed>\d{2}/\d{2}/\d{4}))?\s*$')
TYPE_CLOSE_RE = re.compile(r'^\s*Number of (?P<type>.+?) Litigations Listed:\s*(?P<n>\d+)\s*$')
TYPE_HEADER_RE = re.compile(r'^\s*(?P<type>[A-Z][A-Za-z ,&/\-]+?)\s*$')
LISTED_RE = re.compile(r'MDLs Listed on this Report:\s*(\d+)')
BOILER_PREFIXES = ('United States Judicial Panel', 'MDL Statistics Report', 'MDL Filters', 'Status:', 'Limited to', 'Docket Summary',
                   'Docket Type Summary', 'Closed Between', 'Grouped by', 'MDL Number', 'DOCKET', '____', 'MDLs Listed on this Report')
ANCHOR_RE = re.compile(r'MDL -(?P<num>\d{4})\s+IN RE:\s*')
JTITLE_RE = re.compile(r'\((?P<jt>[^()]*)\)\s*$')
NAME_RE = re.compile(r"(?P<name>[A-Z][A-Za-z\.'\u2019\-]*(?: [A-Za-z\.'\u2019\-]+)*(?:, (?:Jr|Sr|III|II|IV)\.?)?)\s*$")
TRAIL_RE = re.compile(r'^(?P<title>.*?)\s*(?P<n1>[\d,]+)(?:\s+(?P<n2>[\d,]+))?\s*$')
CIRCUIT_RE = re.compile(r'(?P<circuit>(?:%s) Circuit)' % '|'.join(CIRCUITS))
CIRCUIT_SUMMARY_RE = re.compile(r'Circuit Summary: Districts: (?P<d>\d+), MDLs: (?P<m>\d+)\s*(?P<nums>[\d,]+(?:\s+[\d,]+)?)\s*')
BUCKET_RE = re.compile(r'(?P<count>\d+)\s+MDLs with (?:between )?(?P<lo>[\d,]+) (?:and (?P<hi>[\d,]+)|or more) (?:Pending )?Actions '
                       r'(?P<pct_dockets>[\d.]+)%\s*(?P<run>[\d,]+\s*[\d.]*)%')  # run = "157 0.08" or glued "191,91394.11"


def split_judge_from_rest(rest: str, printed_name: str | None):
    """rest = '<title><Last, First M.>' possibly glued. Returns (title, judge_last_first, method)."""
    if printed_name:
        for cand in last_name_candidates(printed_name):
            idx = rest.rfind(cand + ', ')
            if idx > 0:
                return norm_ws(rest[:idx]), norm_ws(rest[idx:]), 'printed_name_from_by_district_report'
    hits = list(re.finditer(r'Litigation(?: \(No\. [IVX]+\))?', rest))
    if hits:
        cut = hits[-1].end()
        judge = rest[cut:].strip(' ,')
        if re.fullmatch(r"[A-Z][A-Za-z'\u2019\-]+(?: [A-Z][A-Za-z'\u2019\-]+)?, [A-Z].*", judge):
            return norm_ws(rest[:cut]), norm_ws(judge), 'caption_ends_with_Litigation'
    return norm_ws(rest), None, 'unsplit'


def parse_listing(text: str, printed_names: dict | None = None, with_types: bool = False):
    """Docket Summary Listing / Docket Type Summary (reports 07, 08, 10): one row per line."""
    text = text.replace('\f', '\n')
    as_of = None
    m = re.search(r'Report Date: (\d{1,2}/\d{1,2}/\d{4})', text)
    if m:
        as_of = iso_date(m.group(1))
    listed = LISTED_RE.search(text)
    rows, unparsed, sections, pending_rows, header = {}, [], [], [], None
    for raw_line in text.split('\n'):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if FOOTER_RE.match(line.strip()) or URL_RE.match(line.strip()) or line.strip().startswith(BOILER_PREFIXES) or line.strip().startswith('DOCKET'):
            continue
        rm = ROW_RE.match(line)
        if rm:
            num = int(rm.group('num'))
            printed = (printed_names or {}).get(num)
            title, judge, method = split_judge_from_rest(rm.group('rest'), printed)
            row = {'mdl_number': num, 'title': title, 'judge_last_first': judge, 'judge_split_method': method,
                   'district_code': rm.group('dist'), 'master_docket': rm.group('master'),
                   'date_filed': iso_date(rm.group('filed')), 'date_transferred': iso_date(rm.group('transferred')),
                   'date_closed': iso_date(rm.group('closed')), 'line': norm_ws(line)}
            if num in rows:
                unparsed.append({'reason': 'duplicate_mdl_number', 'line': norm_ws(line)})
            rows[num] = row
            pending_rows.append(row)
            continue
        if with_types:
            cm = TYPE_CLOSE_RE.match(line)
            if cm:
                ltype, n = norm_ws(cm.group('type')), int(cm.group('n'))
                for row in pending_rows:
                    row['litigation_type'] = ltype
                sections.append({'litigation_type': ltype, 'printed_count': n, 'parsed_count': len(pending_rows),
                                 'header_seen': header, 'header_matches': (header == ltype)})
                pending_rows, header = [], None
                continue
            hm = TYPE_HEADER_RE.match(line)
            if hm and not pending_rows:
                header = norm_ws(hm.group('type'))
                continue
        unparsed.append({'reason': 'no_row_match', 'line': norm_ws(line)})
    return {'as_of': as_of, 'rows': rows, 'printed_count': int(listed.group(1)) if listed else None,
            'unparsed': unparsed, 'sections': sections}


def parse_distribution(text: str, titles_by_number: dict | None = None, known_codes: set | None = None):
    """Distribution of Pending MDL Dockets by District / Actions Pending / Circuit (reports 06, 09, 11, 12)."""
    text = text.replace('\f', '\n')
    as_of = None
    m = re.search(r'Report Date: (\d{1,2}/\d{1,2}/\d{4})', text)
    if m:
        as_of = iso_date(m.group(1))
    text = FOOTER_RE.sub(' ', text)
    text = URL_RE.sub(' ', text)
    head_end = re.search(r'Actions\s*\(Historical\)', text)
    if not head_end:
        raise SystemExit('column header not found in distribution report')
    body = text[head_end.end():]
    body = PAGE_HEADER_RE.sub(' ', body)
    tot_idx = body.find('Report Totals:')
    tail = body[tot_idx:] if tot_idx >= 0 else ''
    body = body[:tot_idx] if tot_idx >= 0 else body
    body = norm_ws(body)
    anchors = list(ANCHOR_RE.finditer(body))
    rows, issues, circuit_summaries = {}, [], []
    current_circuit, current_dist = None, None

    def parse_header(seg: str):
        """Return (district_or_None, judge_name, judge_title, circuit_name_or_None, summary_or_None, leftover)."""
        nonlocal current_circuit
        seg = seg.strip()
        summary = None
        sm = CIRCUIT_SUMMARY_RE.search(seg)
        if sm:
            p, t, note = parse_pair(sm.group('nums'))
            summary = {'circuit': current_circuit, 'districts': int(sm.group('d')), 'mdls': int(sm.group('m')),
                       'actions_pending': p, 'total_actions': t, 'note': note, 'printed_numbers': norm_ws(sm.group('nums'))}
            seg = (seg[:sm.start()] + ' ' + seg[sm.end():]).strip()
        circuit = None
        cm = CIRCUIT_RE.search(seg)
        if cm:
            circuit = cm.group('circuit')
            seg = (seg[:cm.start()] + ' ' + seg[cm.end():]).strip()
        jt = JTITLE_RE.search(seg)
        if not jt:
            return None, None, None, circuit, summary, seg
        jtitle = norm_ws(jt.group('jt'))
        seg2 = seg[:jt.start()].rstrip()
        nm = NAME_RE.search(seg2)
        if not nm:
            return None, None, jtitle, circuit, summary, seg2
        name = nm.group('name').strip()
        leftover = seg2[:nm.start()]
        dist = None
        toks = name.split(' ', 1)
        if len(toks) == 2 and re.fullmatch(r'[A-Z]{2,3}', toks[0]):
            dist, name = toks[0], toks[1]
        return dist, name, jtitle, circuit, summary, leftover

    pre = body[:anchors[0].start()] if anchors else body
    dist, name, jtitle, circuit, summary, leftover = parse_header(pre)
    if circuit:
        current_circuit = circuit
    if leftover.strip():
        issues.append({'kind': 'leftover_before_first_row', 'text': leftover.strip()[:200]})
    next_header = (dist, name, jtitle)
    for i, a in enumerate(anchors):
        num = int(a.group('num'))
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(body)
        chunk = body[a.end():end]
        dist, name, jtitle = next_header
        if dist:
            current_dist = dist
        row = {'mdl_number': num, 'district_code': current_dist, 'district_printed': bool(dist), 'circuit': current_circuit,
               'judge_name_printed': name, 'judge_title_printed': jtitle}
        # split the chunk into this row's "title p t" and the next row's header
        ndist, nname, njtitle, ncircuit, nsummary, leftover = parse_header(chunk)
        if nsummary:
            nsummary['circuit'] = current_circuit
            circuit_summaries.append(nsummary)
        title_nums = leftover.strip()
        title07 = (titles_by_number or {}).get(num)
        title, numtext, method = None, None, None
        if title07:
            sp = strip_title_prefix(title_nums, nospace(title07))
            if sp:
                title, numtext, method = norm_ws(sp[0]), sp[1].strip(), 'title_from_by_number_report'
        if title is None:
            tm = TRAIL_RE.match(title_nums)
            if tm:
                title, method = norm_ws(tm.group('title')), 'trailing_numbers_regex'
                numtext = (tm.group('n1') + (' ' + tm.group('n2') if tm.group('n2') else '')).strip()
        if numtext is None:
            issues.append({'kind': 'row_unparsed', 'mdl_number': num, 'text': title_nums[:200]})
            row.update({'title': title_nums, 'actions_pending': None, 'total_actions': None, 'number_note': 'unparsed'})
        else:
            p, t, note = parse_pair(numtext)
            if p is None:
                issues.append({'kind': 'numbers_ambiguous', 'mdl_number': num, 'text': numtext, 'note': note})
            row.update({'title': title, 'actions_pending': p, 'total_actions': t, 'number_note': note, 'title_method': method})
        if num in rows:
            issues.append({'kind': 'duplicate_mdl_number', 'mdl_number': num})
        rows[num] = row
        if ncircuit:
            current_circuit = ncircuit
            current_dist = None
        next_header = (ndist, nname, njtitle)
        if known_codes is not None and ndist and ndist not in known_codes:
            issues.append({'kind': 'unknown_district_code', 'code': ndist, 'after_mdl': num})
    totals = parse_totals(tail)
    return {'as_of': as_of, 'rows': rows, 'issues': issues, 'totals': totals, 'circuit_summaries': circuit_summaries}


def parse_totals(tail: str):
    tail = norm_ws(tail)
    out = {'report_totals': None, 'mdl_dockets': None, 'transferee_districts': None, 'transferee_judges': None,
           'judge_titles': None, 'buckets_pending': [], 'buckets_total': [], 'notes': []}
    m = re.search(r'Report Totals:\s*(\d+)\s+(?P<nums>[\d,]+(?:\s+[\d,]+)?)', tail)
    if m:
        p, t, note = parse_pair(m.group('nums'))
        out['report_totals'] = {'mdls': int(m.group(1)), 'actions_pending': p, 'total_actions': t, 'note': note}
    m = re.search(r'Total Number of MDL Dockets:\s*(\d+)', tail)
    if m:
        out['mdl_dockets'] = int(m.group(1))
    m = re.search(r'Total Number of Transferee Districts:\s*(\d+)', tail)
    if m:
        out['transferee_districts'] = int(m.group(1))
    m = re.search(r'Total Number of Transferee Judges:\s*(\d+)\s*Chief Judge, USDC\s*(\d+)\s*Sr\. District Judge\s*(\d+)\s*U\.S\. District Judge', tail)
    if m:
        glued, sr, usdj = m.group(1), int(m.group(2)), int(m.group(3))
        cands = [(int(glued[:i]), int(glued[i:])) for i in range(1, len(glued)) if int(glued[:i]) == int(glued[i:]) + sr + usdj]
        if len(cands) == 1:
            out['transferee_judges'] = cands[0][0]
            out['judge_titles'] = {'Chief Judge, USDC': cands[0][1], 'Sr. District Judge': sr, 'U.S. District Judge': usdj}
        else:
            out['notes'].append('transferee judge count not uniquely splittable: %s' % glued)
    p_idx = tail.find('Actions PENDING')
    t_idx = re.search(r'T\s*OT\s*AL\s+Actions in a Docket', tail)
    t_pos = t_idx.start() if t_idx else -1
    for label, start, stop in (('buckets_pending', p_idx, t_pos if t_pos > p_idx else len(tail)),
                               ('buckets_total', t_pos, len(tail))):
        if start < 0:
            continue
        for bm in BUCKET_RE.finditer(tail[start:stop]):
            run = bm.group('run')
            if ' ' in run:
                cnt, pct = run.split()[0], run.split()[1]
                cnt = to_int(cnt)
            else:
                cnt, pct = split_count_pct(run)
            out[label].append({'dockets': int(bm.group('count')), 'range_low': to_int(bm.group('lo')),
                               'range_high': to_int(bm.group('hi')) if bm.group('hi') else None,
                               'pct_dockets': bm.group('pct_dockets'), 'actions': cnt, 'pct_actions': pct})
    return out


# ----------------------------------------------------------------------------- court map
def ro(path: Path):
    return sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True)


def build_court_map(codes_seen: set, district_circuits: dict):
    cl_rows, ui_courts = {}, set()
    with ro(CL_DB) as con:
        for cid, full, short, fjc in con.execute("select id, full_name, short_name, fjc_court_id from courts where jurisdiction='FD'"):
            cl_rows[cid] = {'full_name': full, 'short_name': short, 'fjc_court_id': fjc}
    with ro(PROFILES_DB) as con:
        ui_courts = {r[0] for r in con.execute('select distinct court from courts')}
    out = {}
    for code in sorted(codes_seen | set(CURATED_COURTS)):
        cur = CURATED_COURTS.get(code)
        entry = {'jpml_code': code, 'seen_in_reports': code in codes_seen, 'cl_court_id': None, 'cl_full_name': None, 'cl_short_name': None,
                 'cl_fjc_court_id': None, 'fjc_court_name': None, 'circuit': district_circuits.get(code),
                 'circuit_basis': 'district grouped under this circuit heading in the JPML "Pending MDL Dockets by Circuit" reports' if code in district_circuits else None,
                 'basis': None, 'notes': []}
        if not cur:
            entry['notes'].append('code printed in a JPML report but not in the curated crosswalk; left unmapped')
            out[code] = entry
            continue
        cl_id, fjc_tail = cur
        if fjc_tail in ('District of Columbia',):
            fjc_name = 'U.S. District Court for the District of Columbia'
        elif fjc_tail in ('Guam', 'Virgin Islands', 'Northern Mariana Islands'):
            fjc_name = None
        else:
            fjc_name = 'U.S. District Court for the %s' % fjc_tail
        if cl_id in cl_rows:
            entry.update({'cl_court_id': cl_id, 'cl_full_name': cl_rows[cl_id]['full_name'], 'cl_short_name': cl_rows[cl_id]['short_name'],
                          'cl_fjc_court_id': cl_rows[cl_id]['fjc_court_id'],
                          'basis': 'curated crosswalk (JPML CM/ECF district code -> CourtListener court id); CourtListener court row verified in courtlistener_people_20260918/catalog.sqlite3'})
        else:
            entry['notes'].append('CourtListener court id %s not present in the local courts table; left null' % cl_id)
        if fjc_name and fjc_name in ui_courts:
            entry['fjc_court_name'] = fjc_name
        elif fjc_name:
            entry['notes'].append('FJC court string not present among Judges-UI court strings; judge join disabled for this district')
        else:
            entry['notes'].append('territorial court: FJC court string not curated; judge join disabled')
        out[code] = entry
    return out


# ----------------------------------------------------------------------------- judge join
def load_fjc():
    names, appts = {}, defaultdict(list)
    with (ENRICH / 'observations.jsonl').open(encoding='utf-8') as fh:
        for line in fh:
            o = json.loads(line)
            parts = o.get('name_parts') or {}
            toks = name_tokens(' '.join(x for x in (parts.get('First Name'), parts.get('Middle Name'), parts.get('Last Name')) if x))
            names[o['fjc_nid']] = {'name': o.get('name'), 'tokens': toks, 'fjc_jid': o.get('fjc_jid'), 'last': name_tokens(parts.get('Last Name') or ''),
                                   'suffix': name_suffix(parts.get('Suffix') or '')}
    with (ENRICH / 'facts.jsonl').open(encoding='utf-8') as fh:
        for line in fh:
            f = json.loads(line)
            if f.get('category') != 'appointment':
                continue
            v = f.get('value') or {}
            fields = v.get('fields') or {}
            appts[f['fjc_nid']].append({'court': v.get('court'), 'title': v.get('appointment_title'), 'service_category': v.get('service_category'),
                                        'commission_date': fields.get('Commission Date'), 'senior_status_date': fields.get('Senior Status Date'),
                                        'termination_date': fields.get('Termination Date'), 'fact_id': f.get('fact_id')})
    return names, appts


def load_entity_map():
    nid_to_entities = defaultdict(set)
    with ENTITIES.open(encoding='utf-8') as fh:
        for line in fh:
            e = json.loads(line)
            for mem in e.get('members') or []:
                key = mem.get('member_key') if isinstance(mem, dict) else str(mem)
                m = re.search(r'fjc:nid:(\d+)', key or '')
                if m:
                    nid_to_entities[m.group(1)].add(e['entity_id'])
    ui = {}
    with ro(PROFILES_DB) as con:
        for pid, eid, name in con.execute('select id, entity_id, name from profiles'):
            ui[eid] = {'profile_id': pid, 'name': name}
    return nid_to_entities, ui


def join_judges(judge_keys, court_map, fjc_names, fjc_appts, nid_to_entities, ui):
    """judge_keys: {(printed_name, district_code): [mdl numbers]}. Returns (links, unresolved)."""
    by_court = defaultdict(list)
    for nid, rows in fjc_appts.items():
        for a in rows:
            if a.get('service_category') == 'main_federal_judicial_service' and a.get('court'):
                by_court[a['court']].append(nid)
    links, unresolved = {}, []
    for (printed, code), mdls in sorted(judge_keys.items(), key=lambda kv: (kv[0][1] or '', kv[0][0] or '')):
        entry = court_map.get(code) or {}
        fjc_court = entry.get('fjc_court_name')
        base = {'judge_name_as_printed': printed, 'district_code': code, 'mdls': sorted(mdls)}
        if not printed:
            unresolved.append(dict(base, kind='judge', reason='no_judge_name_printed'))
            continue
        if not fjc_court:
            unresolved.append(dict(base, kind='judge', reason='district_not_joinable', detail=entry.get('notes')))
            continue
        jt, js = name_tokens(printed), name_suffix(printed)
        cands = []
        for nid in sorted(set(by_court.get(fjc_court, []))):
            info = fjc_names.get(nid, {})
            kind = name_match_kind(jt, info.get('tokens'), js, info.get('suffix'))
            if kind:
                cands.append((nid, kind))
        if len(cands) == 1:
            nid, kind = cands[0]
            ents = sorted(nid_to_entities.get(nid, ()))
            ents_ui = [e for e in ents if e in ui]
            if len(ents_ui) == 1:
                eid = ents_ui[0]
                links[(printed, code)] = {'entity_id': eid, 'display_name': ui[eid]['name'], 'fjc_nid': nid, 'fjc_name': fjc_names[nid]['name'],
                                         'fjc_court': fjc_court, 'match_kind': kind, 'basis': 'jpml_name_court'}
            elif not ents:
                unresolved.append(dict(base, kind='judge', reason='fjc_match_without_judge_entity', fjc_nid=nid, match_kind=kind))
            elif not ents_ui:
                unresolved.append(dict(base, kind='judge', reason='judge_entity_not_in_judges_ui', fjc_nid=nid, entity_ids=ents, match_kind=kind))
            else:
                unresolved.append(dict(base, kind='judge', reason='fjc_nid_maps_to_multiple_ui_entities', fjc_nid=nid, entity_ids=ents_ui))
        elif len(cands) > 1:
            unresolved.append(dict(base, kind='judge', reason='ambiguous_name_at_court', candidates=[{'fjc_nid': n, 'match_kind': k, 'fjc_name': fjc_names[n]['name']} for n, k in cands]))
        else:
            elsewhere = []
            for nid, info in fjc_names.items():
                if name_match_kind(jt, info['tokens'], js, info.get('suffix')):
                    elsewhere.append({'fjc_nid': nid, 'fjc_name': info['name'], 'courts': sorted({a['court'] for a in fjc_appts.get(nid, []) if a.get('court')})})
            same_last = [{'fjc_nid': nid, 'fjc_name': fjc_names[nid]['name']} for nid in sorted(set(by_court.get(fjc_court, [])))
                         if fjc_names.get(nid, {}).get('last') == jt[-1:]]
            reason = 'name_matches_fjc_judge_at_other_court_only' if elsewhere else 'no_fjc_name_match_at_court'
            unresolved.append(dict(base, kind='judge', reason=reason, fjc_court=fjc_court, name_matches_elsewhere=elsewhere[:5], same_last_name_at_court=same_last[:5]))
    return links, unresolved


# ----------------------------------------------------------------------------- native-id aliases (optional layer)
def load_aliases():
    """Alias rows of sources/judge_aliases_20260919, fail-closed: validation passed + file hash + native-id evidence on every row."""
    try:
        validation = json.loads((ALIASES / 'validation.json').read_text(encoding='utf-8'))
        data = (ALIASES / 'aliases.jsonl').read_bytes()
    except (OSError, ValueError):
        return [], None
    entry = next((f for f in validation.get('data_files') or [] if f.get('path') == 'aliases.jsonl'), None)
    if validation.get('status') != 'passed' or validation.get('ready') is not True or not entry or entry.get('sha256') != sha256_bytes(data):
        return [], None
    rows = [json.loads(l) for l in data.decode('utf-8').splitlines() if l.strip()]
    for r in rows:
        ev = r.get('evidence') or {}
        if not (str(ev.get('cl_person_id') or '').isdigit() and str(ev.get('fjc_jid') or '').isdigit() and str(ev.get('fjc_nid') or '').isdigit()
                and ev.get('surname_agrees') is True and ev.get('dockets')):
            return [], None
        if ev.get('court_agrees') is not True and r.get('relation') != DESIGNATION:
            return [], None
    return rows, {'path': 'sources/judge_aliases_20260919/aliases.jsonl', 'sha256': sha256_bytes(data)}


def apply_aliases(links, unresolved, alias_rows, judge_keys, court_map, fjc_names, nid_to_entities, ui):
    """Resolve still-unresolved printed names through native-id aliases. A key is resolved only when EVERY one of its MDLs
    has its own master-docket evidence in the alias row and the entity is a UI entity of that FJC nid. Never by name."""
    by_key = {(r.get('alias'), r.get('district_code')): r for r in alias_rows}
    kept = []
    for u in unresolved:
        key = (u.get('judge_name_as_printed'), u.get('district_code'))
        r = by_key.get(key)
        ev = (r or {}).get('evidence') or {}
        nid = str(ev.get('fjc_nid') or '')
        ok = (r is not None and u.get('kind') == 'judge' and key[0]
              and set('mdl:%d' % n for n in judge_keys.get(key, [])) <= set(r.get('mdls') or [])
              and r.get('entity_id') in ui and r.get('entity_id') in nid_to_entities.get(nid, ()) and nid in fjc_names)
        if not ok:
            kept.append(u)
            continue
        link = {'entity_id': r['entity_id'], 'display_name': ui[r['entity_id']]['name'], 'fjc_nid': nid, 'fjc_name': fjc_names[nid]['name'],
                'fjc_court': (court_map.get(key[1]) or {}).get('fjc_court_name'), 'match_kind': NATIVE_BRIDGE, 'basis': NATIVE_BRIDGE,
                'relation': r.get('relation'), 'cl_person_id': int(ev['cl_person_id']), 'fjc_jid': str(ev['fjc_jid']),
                'entity_fjc_courts': ev.get('entity_fjc_courts'), 'court_agrees': ev.get('court_agrees'), 'surname_agrees': ev.get('surname_agrees'),
                'first_initial_agrees': ev.get('first_initial_agrees'), 'alias_basis': r.get('basis'),
                'dockets': {d['mdl']: {'docket_id': d.get('docket_id'), 'docket_number': d.get('docket_number'), 'receipt_file': d.get('receipt_file'),
                                       'receipt_sha256': d.get('receipt_sha256')} for d in ev.get('dockets') or []}}
        if link['relation'] == DESIGNATION:
            link['relation_note'] = 'FJC court of this judge differs from the transferee court; kept only as sitting by designation or intercircuit assignment.'
        links[key] = link
    return links, kept


def judge_link_public(link, num):
    out = {'entity_id': link['entity_id'], 'display_name': link['display_name'], 'basis': link.get('basis') or 'jpml_name_court', 'match_kind': link['match_kind'],
           'fjc_nid': link['fjc_nid'], 'fjc_name': link['fjc_name'], 'fjc_court': link['fjc_court']}
    if link.get('basis') == NATIVE_BRIDGE:
        docket = (link.get('dockets') or {}).get('mdl:%d' % num) or {}
        out.update({'relation': link.get('relation'), 'cl_person_id': link.get('cl_person_id'), 'fjc_jid': link.get('fjc_jid'),
                    'entity_fjc_courts': link.get('entity_fjc_courts'), 'court_agrees': link.get('court_agrees'), 'surname_agrees': link.get('surname_agrees'),
                    'first_initial_agrees': link.get('first_initial_agrees'), 'alias_basis': link.get('alias_basis'),
                    'cl_docket_id': docket.get('docket_id'), 'connector_file': docket.get('receipt_file'), 'connector_file_sha256': docket.get('receipt_sha256'),
                    'label': 'native-id bridge from a saved CourtListener connector response, not original HTTP bytes. The printed name was not used to make the link.'})
        if link.get('relation_note'):
            out['relation_note'] = link['relation_note']
    return out


# ----------------------------------------------------------------------------- connector responses
def norm_docket(number: str | None) -> str | None:
    if not number:
        return None
    parts = number.lower().split('-')
    if len(parts) < 3:
        return number.lower()
    office_year, kind, seq = parts[0], parts[1], parts[2]
    seq = re.sub(r'^0+', '', re.match(r'\d*', seq).group(0)) or '0'
    return '%s-%s-%s' % (office_year, kind, seq)


def load_connector_files():
    out = []
    if not CONNECTOR.is_dir():
        return out
    for p in sorted(CONNECTOR.glob('*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        d['_file'] = 'connector/' + p.name
        d['_sha256'] = sha256_file(p)
        d['_response_sha256'] = sha256_bytes(json.dumps(d.get('response'), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
        d['_saved_at_utc'] = dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).replace(microsecond=0).isoformat()
        out.append(d)
    return out


def connector_dockets(conn_files, mdl_rows):
    """Match saved CourtListener docket-search responses to MDL rows by court id + master docket number."""
    out = {}
    for d in conn_files:
        num = d.get('mdl_number')
        row = mdl_rows.get(num)
        if not row or d.get('exploratory'):
            continue
        resp = d.get('response') or {}
        results = resp.get('results') if isinstance(resp, dict) else None
        if results is None and isinstance(resp, dict):
            results = resp.get('data') or []
        if not isinstance(results, list):
            results = []
        want = norm_docket(row.get('master_docket'))
        hits = [r for r in results if isinstance(r, dict) and r.get('court_id') == row.get('cl_court_id') and norm_docket(r.get('docketNumber') or r.get('docket_number')) == want]
        mdl_hits = [r for r in results if isinstance(r, dict) and r.get('court_id') == row.get('cl_court_id')
                    and re.search(r'-md-0*%d\b' % num, (r.get('docketNumber') or r.get('docket_number') or '').lower())]
        chosen, basis = None, None
        if len(hits) == 1:
            chosen, basis = hits[0], 'cl_docket_search: court id and master docket number match'
        elif not hits and len(mdl_hits) == 1:
            chosen, basis = mdl_hits[0], 'cl_docket_search: court id match and docket number carries -md-%d' % num
        out[num] = {'file': d['_file'], 'file_sha256': d['_sha256'], 'response_sha256': d['_response_sha256'], 'tool': d.get('tool'),
                    'arguments': d.get('arguments'), 'requested_after_utc': d.get('requested_after_utc'), 'saved_at_utc': d['_saved_at_utc'],
                    'result_count': len(results), 'candidates_court_match': len([r for r in results if isinstance(r, dict) and r.get('court_id') == row.get('cl_court_id')]),
                    'chosen': ({k: chosen.get(k) for k in ('docket_id', 'court_id', 'assigned_to_id', 'referred_to_id', 'date_filed', 'date_terminated', 'docketNumber', 'caseName')} if chosen else None),
                    'basis': basis}
    return out


# ----------------------------------------------------------------------------- main build
def main():
    receipts = load_receipts()
    seeds = load_seeds()
    manifest = json.loads((TEXT / 'manifest.json').read_text(encoding='utf-8'))
    inputs = verify_inputs(receipts, manifest)
    docs = build_documents(receipts, seeds, manifest)
    doc_by_seq = {d['seq']: d for d in docs}
    receipt_by_seq = {r['seq']: r for r in receipts}

    # --- parse the four Sept 1 pending reports + terminated + two quarterly circuit reports
    by_number = parse_listing(text_for(7))
    codes_seen = {r['district_code'] for r in by_number['rows'].values()}
    terminated = parse_listing(text_for(10))
    codes_seen |= {r['district_code'] for r in terminated['rows'].values()}
    titles07 = {n: r['title'] for n, r in by_number['rows'].items()}
    by_district = parse_distribution(text_for(6), titles07, codes_seen)
    # re-split 07 rows now that printed judge names are known (exact last-name anchoring)
    printed = {n: r['judge_name_printed'] for n, r in by_district['rows'].items()}
    by_number = parse_listing(text_for(7), printed)
    terminated = parse_listing(text_for(10), {})
    by_type = parse_listing(text_for(8), printed, with_types=True)
    titles07 = {n: r['title'] for n, r in by_number['rows'].items()}
    by_district = parse_distribution(text_for(6), titles07, codes_seen)
    by_actions = parse_distribution(text_for(9), titles07, codes_seen)
    circ_march = parse_distribution(text_for(11), titles07, codes_seen)
    circ_june = parse_distribution(text_for(12), titles07, codes_seen)
    for rep in (by_district, by_actions, circ_march, circ_june):
        codes_seen |= {r['district_code'] for r in rep['rows'].values() if r['district_code']}
    district_circuits = {}
    for rep in (circ_june, circ_march):
        for r in rep['rows'].values():
            if r['district_code'] and r['circuit']:
                district_circuits.setdefault(r['district_code'], r['circuit'])
    court_map = build_court_map(codes_seen, district_circuits)

    # --- QA reconciliation
    qa = {'built_at': now_utc(), 'checks': [], 'mismatches': [], 'report_rows': {}, 'printed_totals': {}}

    def check(name, ok, detail, blocking=True):
        qa['checks'].append({'name': name, 'status': 'passed' if ok else 'failed', 'blocking': blocking, 'detail': detail})
        if not ok:
            qa['mismatches'].append({'check': name, 'detail': detail, 'blocking': blocking})
        return ok

    reports = {'by_district': by_district, 'by_mdl_number': by_number, 'by_mdl_type': by_type, 'by_actions_pending': by_actions,
               'recently_terminated': terminated, 'by_circuit_2026-03-31': circ_march, 'by_circuit_2026-06-30': circ_june}
    for name, rep in reports.items():
        printed_n = rep.get('printed_count') or (rep.get('totals') or {}).get('mdl_dockets')
        qa['report_rows'][name] = {'parsed': len(rep['rows']), 'printed': printed_n, 'as_of': rep['as_of'],
                                   'unparsed_lines': rep.get('unparsed', []), 'issues': rep.get('issues', [])}
        check('rows_vs_printed_count:%s' % name, printed_n is not None and len(rep['rows']) == printed_n,
              {'parsed': len(rep['rows']), 'printed': printed_n})
        check('no_unparsed_lines:%s' % name, not rep.get('unparsed') and not [i for i in rep.get('issues', []) if i['kind'] in ('row_unparsed', 'numbers_ambiguous', 'duplicate_mdl_number')],
              {'unparsed': rep.get('unparsed', [])[:20], 'issues': rep.get('issues', [])[:20]})
    sept_sets = {k: set(reports[k]['rows']) for k in ('by_district', 'by_mdl_number', 'by_mdl_type', 'by_actions_pending')}
    check('same_mdl_set_across_sept_reports', all(s == sept_sets['by_mdl_number'] for s in sept_sets.values()),
          {k: sorted(s ^ sept_sets['by_mdl_number']) for k, s in sept_sets.items()})
    tot = by_district['totals']
    qa['printed_totals'] = {'by_district': by_district['totals'], 'by_actions_pending': by_actions['totals'],
                            'by_circuit_2026-06-30': circ_june['totals'], 'by_circuit_2026-03-31': circ_march['totals']}
    sum_p = sum(r['actions_pending'] or 0 for r in by_district['rows'].values())
    sum_t = sum(r['total_actions'] or 0 for r in by_district['rows'].values())
    rt = tot.get('report_totals') or {}
    check('sum_actions_pending_vs_report_totals', rt.get('actions_pending') == sum_p, {'parsed_sum': sum_p, 'printed': rt.get('actions_pending')})
    check('sum_total_actions_vs_report_totals', rt.get('total_actions') == sum_t, {'parsed_sum': sum_t, 'printed': rt.get('total_actions')})
    check('by_actions_pending_sums_match', sum(r['actions_pending'] or 0 for r in by_actions['rows'].values()) == sum_p
          and sum(r['total_actions'] or 0 for r in by_actions['rows'].values()) == sum_t, {})
    for label, key in (('buckets_pending', 'actions_pending'), ('buckets_total', 'total_actions')):
        for b in tot.get(label, []):
            lo, hi = b['range_low'], b['range_high'] if b['range_high'] is not None else 10 ** 12
            rows_in = [r for r in by_district['rows'].values() if r[key] is not None and lo <= r[key] <= hi]
            ok = len(rows_in) == b['dockets'] and sum(r[key] for r in rows_in) == b['actions']
            check('bucket:%s:%s-%s' % (key, lo, b['range_high'] if b['range_high'] is not None else 'more'), ok,
                  {'printed_dockets': b['dockets'], 'parsed_dockets': len(rows_in), 'printed_actions': b['actions'], 'parsed_actions': sum(r[key] for r in rows_in)}, blocking=False)
    districts = {r['district_code'] for r in by_district['rows'].values()}
    check('transferee_districts_vs_printed', tot.get('transferee_districts') == len(districts), {'parsed': len(districts), 'printed': tot.get('transferee_districts')})
    judges = {(r['judge_name_printed'], r['district_code']) for r in by_district['rows'].values()}
    judge_names = {r['judge_name_printed'] for r in by_district['rows'].values()}
    check('transferee_judges_vs_printed', tot.get('transferee_judges') in (len(judges), len(judge_names)),
          {'parsed_name_district_pairs': len(judges), 'parsed_distinct_names': len(judge_names), 'printed': tot.get('transferee_judges')}, blocking=False)
    name_variants = defaultdict(set)
    for r in by_district['rows'].values():
        name_variants[r['judge_name_printed']].add((r['district_code'], r['judge_title_printed']))
    multi = {n: sorted(v) for n, v in name_variants.items() if len(v) > 1}
    first_by_name = {}
    for num in sorted(by_district['rows']):
        r = by_district['rows'][num]
        first_by_name.setdefault(r['judge_name_printed'], r['judge_title_printed'])
    title_counts = Counter(first_by_name.values())
    check('judge_title_counts_vs_printed', tot.get('judge_titles') == dict(title_counts),
          {'parsed_by_distinct_printed_name': dict(title_counts), 'printed': tot.get('judge_titles'), 'names_printed_in_more_than_one_district_or_title': multi,
           'note': 'the JPML counts each transferee judge once even when printed under two districts (sitting by designation); counted by distinct printed name here'}, blocking=False)
    # per-MDL cross-report field agreement
    field_mismatches = []
    for num in sorted(sept_sets['by_mdl_number']):
        a, b, c, d = by_district['rows'].get(num), by_number['rows'].get(num), by_type['rows'].get(num), by_actions['rows'].get(num)
        if not (a and b and c and d):
            continue
        if not (a['district_code'] == b['district_code'] == c['district_code'] == d['district_code']):
            field_mismatches.append({'mdl_number': num, 'field': 'district_code', 'values': [a['district_code'], b['district_code'], c['district_code'], d['district_code']]})
        if (a['actions_pending'], a['total_actions']) != (d['actions_pending'], d['total_actions']):
            field_mismatches.append({'mdl_number': num, 'field': 'actions', 'values': [[a['actions_pending'], a['total_actions']], [d['actions_pending'], d['total_actions']]]})
        titles_ns = {nospace(x['title']) for x in (a, b, c, d) if x.get('title')}
        if len(titles_ns) != 1:
            field_mismatches.append({'mdl_number': num, 'field': 'title', 'values': [a.get('title'), b.get('title'), c.get('title'), d.get('title')]})
        if a['judge_name_printed'] != d['judge_name_printed']:
            field_mismatches.append({'mdl_number': num, 'field': 'judge_name_printed', 'values': [a['judge_name_printed'], d['judge_name_printed']]})
        if (b['judge_last_first'], b['master_docket'], b['date_filed'], b['date_transferred']) != (c['judge_last_first'], c['master_docket'], c['date_filed'], c['date_transferred']):
            field_mismatches.append({'mdl_number': num, 'field': 'by_number_vs_by_type', 'values': [[b['judge_last_first'], b['master_docket'], b['date_filed'], b['date_transferred']], [c['judge_last_first'], c['master_docket'], c['date_filed'], c['date_transferred']]]})
        if b.get('judge_last_first') and a.get('judge_name_printed'):
            if name_tokens(a['judge_name_printed'])[-1:] != name_tokens(b['judge_last_first'].split(',')[0])[-1:]:
                field_mismatches.append({'mdl_number': num, 'field': 'judge_last_name', 'values': [a['judge_name_printed'], b['judge_last_first']]})
    check('cross_report_field_agreement', not field_mismatches, {'mismatches': field_mismatches})
    for sec in by_type['sections']:
        check('type_section_count:%s' % sec['litigation_type'], sec['printed_count'] == sec['parsed_count'] and sec['header_matches'], sec, blocking=False)
    for name, rep in (('by_circuit_2026-06-30', circ_june), ('by_circuit_2026-03-31', circ_march)):
        for cs in rep['circuit_summaries']:
            rows_in = [r for r in rep['rows'].values() if r['circuit'] == cs['circuit']]
            sp, st = sum(r['actions_pending'] or 0 for r in rows_in), sum(r['total_actions'] or 0 for r in rows_in)
            if cs['actions_pending'] is None and cs['printed_numbers'].replace(' ', '') == '%d%d' % (sp, st):
                # the circuit summary prints both totals without thousands separators and glued together;
                # the printed string is accepted only when it equals the concatenation of the parsed sums
                nums_ok, cs['note'] = True, 'glued_totals_equal_concatenation_of_parsed_sums'
            else:
                nums_ok = sp == cs['actions_pending'] and st == cs['total_actions']
            ok = len(rows_in) == cs['mdls'] and len({r['district_code'] for r in rows_in}) == cs['districts'] and nums_ok
            check('circuit_summary:%s:%s' % (name, cs['circuit']), ok, {'printed': cs, 'parsed': {'mdls': len(rows_in), 'districts': len({r['district_code'] for r in rows_in}),
                  'actions_pending': sp, 'total_actions': st}}, blocking=False)
        crt = (rep['totals'] or {}).get('report_totals') or {}
        check('circuit_report_totals:%s' % name, crt.get('actions_pending') == sum(r['actions_pending'] or 0 for r in rep['rows'].values())
              and crt.get('total_actions') == sum(r['total_actions'] or 0 for r in rep['rows'].values()), {'printed': crt}, blocking=False)
    june_only = sorted(set(circ_june['rows']) - sept_sets['by_mdl_number'])
    sept_only = sorted(sept_sets['by_mdl_number'] - set(circ_june['rows']))
    closed_after_june = [n for n in june_only if n in terminated['rows'] and (terminated['rows'][n]['date_closed'] or '') > '2026-06-30']
    transferred_after_june = [n for n in sept_only if (by_number['rows'][n]['date_transferred'] or '') > '2026-06-30']
    unexplained = {'in_june_not_sept_and_not_terminated': sorted(set(june_only) - set(closed_after_june)),
                   'in_sept_not_june_but_transferred_before_july': sorted(set(sept_only) - set(transferred_after_june))}
    check('june_vs_sept_inventory_reconciles', not any(unexplained.values()),
          {'in_june_not_sept': june_only, 'closed_after_june': closed_after_june, 'in_sept_not_june': sept_only,
           'transferred_after_june': transferred_after_june, 'unexplained': unexplained,
           'note': 'an MDL absent from the June "Limited to Active Litigations" report but present in September may have been inactive in June; listed, not hidden'}, blocking=False)
    unmapped = sorted(c for c in codes_seen if not (court_map.get(c) or {}).get('cl_court_id'))
    check('district_codes_mapped', not unmapped, {'unmapped_codes_seen': unmapped}, blocking=False)

    # --- judge join
    fjc_names, fjc_appts = load_fjc()
    nid_to_entities, ui = load_entity_map()
    judge_keys = defaultdict(list)
    for num, r in by_district['rows'].items():
        judge_keys[(r['judge_name_printed'], r['district_code'])].append(num)
    term_names = {}
    for num, r in terminated['rows'].items():
        jl = r.get('judge_last_first')
        if jl and ',' in jl:
            last, first = jl.split(',', 1)
            term_names[num] = norm_ws(first) + ' ' + norm_ws(last)
            judge_keys[(term_names[num], r['district_code'])].append(num)
        else:
            judge_keys[(None, r['district_code'])].append(num)
    links, unresolved = join_judges(judge_keys, court_map, fjc_names, fjc_appts, nid_to_entities, ui)
    name_join_unresolved = len(unresolved)
    alias_rows, alias_input = load_aliases()
    links, unresolved = apply_aliases(links, unresolved, alias_rows, judge_keys, court_map, fjc_names, nid_to_entities, ui)

    # --- local MDL-3080 collection
    local = {}
    if MDL3080.is_file():
        recs = [json.loads(l) for l in MDL3080.read_text(encoding='utf-8').splitlines() if l.strip()]
        labels = {r.get('court_label') for r in recs}
        local[3080] = {'collection_id': 'mdl-3080', 'label': 'Local MDL 3080 document collection (local_library_presentation_20260918)',
                       'records': len(recs), 'basis': 'mdl_number_only', 'evidence': {'file': 'sources/local_library_presentation_20260918/mdl-3080.jsonl',
                       'court_labels': sorted(l for l in labels if l), 'sha256': sha256_file(MDL3080)}}

    # --- assemble MDL rows
    conn_files = load_connector_files()
    mdls = {}
    label_for = lambda as_of: 'as listed in the JPML report dated %s' % as_of
    rec7, rec6 = receipt_by_seq[7], receipt_by_seq[6]
    for num in sorted(sept_sets['by_mdl_number'] | set(terminated['rows'])):
        b = by_number['rows'].get(num) or terminated['rows'].get(num)
        a = by_district['rows'].get(num)
        c = by_type['rows'].get(num)
        status = 'pending' if num in by_number['rows'] else 'terminated'
        code = b['district_code']
        cm = court_map.get(code) or {}
        titles = [x.get('title') for x in (a, b, c, by_actions['rows'].get(num)) if x and x.get('title')]
        title = max(titles, key=lambda t: t.count(' ')) if titles else b.get('title')
        title = 'IN RE: ' + title if title else title  # the reports print every caption with the "IN RE:" prefix
        as_of = by_number['as_of'] if status == 'pending' else terminated['as_of']
        printed_name = a['judge_name_printed'] if a else term_names.get(num)
        link = links.get((printed_name, code))
        unres = [dict(u) for u in unresolved if num in u.get('mdls', [])]
        for u in unres:
            u.pop('mdls', None)
        snapshots = []
        for rep, kind, seq in ((circ_march, 'by_circuit', 11), (circ_june, 'by_circuit', 12), (by_district, 'by_district', 6)):
            r = rep['rows'].get(num)
            if r:
                snapshots.append({'as_of': rep['as_of'], 'actions_pending': r['actions_pending'], 'total_actions': r['total_actions'],
                                  'report_kind': kind, 'document_id': doc_by_seq[seq]['document_id'], 'counts_label': label_for(rep['as_of'])})
        reports_used = []
        for seq, rep in ((7, by_number), (6, by_district), (8, by_type), (9, by_actions), (10, terminated), (11, circ_march), (12, circ_june)):
            if num in rep['rows']:
                reports_used.append({'report_kind': REPORT_KINDS[seq], 'document_id': doc_by_seq[seq]['document_id'], 'as_of': rep['as_of'],
                                     'captured_at': receipt_by_seq[seq].get('completed_at')})
        row = {
            'id': 'mdl:%d' % num, 'mdl_number': num, 'title': title,
            'title_basis': 'caption as printed in the JPML report(s); where a PDF line wrap dropped a space the variant with the most spaces was kept',
            'status': status, 'district_code': code, 'cl_court_id': cm.get('cl_court_id'), 'court_name': cm.get('cl_short_name'),
            'fjc_court_name': cm.get('fjc_court_name'), 'circuit': cm.get('circuit'),
            'transferee_judge': {'name_as_printed': printed_name, 'title_as_printed': a['judge_title_printed'] if a else None,
                                 'name_by_number_report': b.get('judge_last_first'), 'name_basis': 'Judge (Title) column of the by-district report' if a else 'Transferee Judge column of the terminated-litigations listing (Last, First)'},
            'actions_pending': a['actions_pending'] if a else None, 'total_actions': a['total_actions'] if a else None,
            'counts_label': label_for(as_of),
            'litigation_type': c.get('litigation_type') if c else None,
            'master_docket': b.get('master_docket'), 'date_filed': b.get('date_filed'), 'date_transferred': b.get('date_transferred'),
            'date_closed': b.get('date_closed'), 'snapshots': snapshots,
            'judge_links': ([judge_link_public(link, num)] if link else []),
            'cl_links': None, 'local_collections': [local[num]] if num in local else [], 'reports': reports_used,
            'temporal': {'captured_at': (rec7 if status == 'pending' else receipt_by_seq[10]).get('completed_at'), 'captured_at_basis': 'fetch receipt completed_at of the by-MDL-number listing (UTC)' if status == 'pending' else 'fetch receipt completed_at of the recently-terminated listing (UTC)',
                         'source_as_of': as_of, 'source_as_of_basis': 'printed "Report Date" in the JPML CM/ECF report header',
                         'published_at': None, 'published_at_basis': None,
                         'effective_from': b.get('date_transferred'), 'effective_from_basis': 'DateTransferred column (Panel transfer order date) of the JPML listing' if b.get('date_transferred') else None,
                         'effective_to': b.get('date_closed'), 'effective_to_basis': 'DATE CLOSED column of the JPML recently-terminated listing' if b.get('date_closed') else None},
            'unresolved': unres,
        }
        mdls[num] = row
    # connector cross-check (offline, from saved responses only)
    conn = connector_dockets(conn_files, mdls)
    cl_people_fjc = {}
    if conn:
        with ro(CL_DB) as con:
            for pid, fjc_id in con.execute('select id, fjc_id from people where fjc_id is not null and fjc_id != ""'):
                cl_people_fjc[int(pid)] = str(fjc_id)
    jid_to_nid = {info['fjc_jid']: nid for nid, info in fjc_names.items() if info.get('fjc_jid')}
    agreement = Counter()
    for num, c in conn.items():
        row = mdls[num]
        chosen = c['chosen']
        agree = None
        if chosen:
            pid = chosen.get('assigned_to_id')
            nid = jid_to_nid.get(cl_people_fjc.get(pid)) if pid is not None else None
            name_links = [l for l in row['judge_links'] if l.get('basis') == 'jpml_name_court']
            link_nid = name_links[0]['fjc_nid'] if name_links else None
            if row['judge_links'] and not name_links:
                agree = 'judge_link_is_the_native_bridge_itself'  # nothing independent to compare, so no agreement is claimed
            elif not link_nid:
                agree = 'no_name_join_to_compare'
            elif pid is None:
                agree = 'cl_docket_has_no_assigned_to_id'
            elif nid is None:
                agree = 'cl_person_has_no_fjc_id_locally'
            else:
                agree = 'agree' if nid == link_nid else 'disagree'
            agreement[agree] += 1
        row['cl_links'] = {'docket_id': chosen.get('docket_id') if chosen else None, 'court_id': chosen.get('court_id') if chosen else None,
                           'assigned_to_id': chosen.get('assigned_to_id') if chosen else None, 'referred_to_id': chosen.get('referred_to_id') if chosen else None,
                           'date_filed': chosen.get('date_filed') if chosen else None, 'date_terminated': chosen.get('date_terminated') if chosen else None,
                           'docket_number': chosen.get('docketNumber') if chosen else None, 'case_name': chosen.get('caseName') if chosen else None,
                           'basis': c['basis'], 'agreement_with_name_join': agree, 'connector_file': c['file'], 'connector_file_sha256': c['file_sha256'],
                           'response_sha256': c['response_sha256'], 'tool': c['tool'], 'arguments': c['arguments'],
                           'requested_after_utc': c['requested_after_utc'], 'saved_at_utc': c['saved_at_utc'], 'result_count': c['result_count'],
                           'label': 'connector response (CourtListener MCP), not original HTTP bytes'}
    qa['connector'] = {'files': len(conn_files), 'exploratory_files': sum(1 for d in conn_files if d.get('exploratory')),
                       'matched_dockets': sum(1 for c in conn.values() if c['chosen']), 'agreement': dict(agreement),
                       'unmatched': [num for num, c in conn.items() if not c['chosen']],
                       'registry': [{'file': d['_file'], 'file_sha256': d['_sha256'], 'response_sha256': d['_response_sha256'], 'tool': d.get('tool'),
                                     'arguments': d.get('arguments'), 'mdl_number': d.get('mdl_number'), 'exploratory': bool(d.get('exploratory')),
                                     'requested_after_utc': d.get('requested_after_utc'), 'saved_at_utc': d['_saved_at_utc'],
                                     'label': d.get('label')} for d in conn_files],
                       'per_mdl': {str(num): {'file': c['file'], 'chosen_docket_id': (c['chosen'] or {}).get('docket_id'), 'basis': c['basis'],
                                              'agreement_with_name_join': mdls[num]['cl_links']['agreement_with_name_join'] if mdls[num].get('cl_links') else None}
                                   for num, c in conn.items()}}
    if conn:
        check('connector_name_join_agreement', agreement.get('disagree', 0) == 0, dict(agreement), blocking=False)

    # --- edges
    edges = []
    for num, row in mdls.items():
        src = {'type': 'mdl', 'id': row['id']}
        if row['cl_court_id']:
            edges.append({'from': src, 'to': {'type': 'cl_court', 'id': 'cl_court:%s' % row['cl_court_id']}, 'relation': 'transferee_district',
                          'basis': 'jpml_district_code_crosswalk', 'evidence': {'district_code': row['district_code'], 'report_kind': 'by_mdl_number' if row['status'] == 'pending' else 'recently_terminated',
                          'document_id': row['reports'][0]['document_id'], 'as_of': row['temporal']['source_as_of']}})
        for jl in row['judge_links']:
            evidence = {'judge_name_as_printed': row['transferee_judge']['name_as_printed'], 'district_code': row['district_code'],
                        'fjc_nid': jl['fjc_nid'], 'fjc_court': jl['fjc_court'], 'match_kind': jl['match_kind'], 'document_id': row['reports'][0]['document_id'], 'as_of': row['temporal']['source_as_of']}
            if jl['basis'] == NATIVE_BRIDGE:
                evidence.update({k: jl.get(k) for k in ('cl_person_id', 'fjc_jid', 'entity_fjc_courts', 'court_agrees', 'surname_agrees', 'first_initial_agrees',
                                                        'cl_docket_id', 'connector_file', 'connector_file_sha256')})
            edges.append({'from': src, 'to': {'type': 'judge_entity', 'id': 'judge_entity:%s' % jl['entity_id']},
                          'relation': jl.get('relation') or 'transferee_judge', 'basis': jl['basis'], 'evidence': evidence})
        cl = row.get('cl_links') or {}
        if cl.get('docket_id') is not None:
            edges.append({'from': src, 'to': {'type': 'cl_docket', 'id': 'cl_docket:%s' % cl['docket_id']}, 'relation': 'master_docket',
                          'basis': 'cl_docket_search', 'evidence': {'connector_file': cl['connector_file'], 'sha256': cl['connector_file_sha256'], 'docket_number': cl['docket_number'], 'court_id': cl['court_id'], 'match': cl['basis']}})
            if cl.get('assigned_to_id') is not None:
                edges.append({'from': src, 'to': {'type': 'cl_person', 'id': 'cl_person:%s' % cl['assigned_to_id']}, 'relation': 'assigned_judge',
                              'basis': 'cl_assigned_to_id', 'evidence': {'connector_file': cl['connector_file'], 'sha256': cl['connector_file_sha256'], 'docket_id': cl['docket_id'], 'agreement_with_name_join': cl['agreement_with_name_join']}})
        for lc in row['local_collections']:
            edges.append({'from': src, 'to': {'type': 'local_collection', 'id': 'local_collection:%s' % lc['collection_id']}, 'relation': 'has_local_documents',
                          'basis': 'mdl_number_only', 'evidence': lc['evidence']})

    # --- write outputs
    def write_jsonl(name, rows):
        payload = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=False) + '\n' for r in rows).encode('utf-8')
        (HERE / name).write_bytes(payload)  # bytes, so the recorded hash is the on-disk hash (no CRLF translation)
        return {'path': name, 'sha256': sha256_bytes(payload), 'rows': len(rows)}

    def write_json(name, obj, rows):
        payload = json.dumps(obj, ensure_ascii=False, indent=1).encode('utf-8')
        (HERE / name).write_bytes(payload)
        return {'path': name, 'sha256': sha256_bytes(payload), 'rows': rows}

    unresolved_rows = [dict(u, mdls=['mdl:%d' % n for n in u['mdls']]) for u in unresolved]
    qa['judge_join'] = {'distinct_judge_keys': len(judge_keys), 'resolved_keys': len(links), 'unresolved_keys': len(unresolved),
                        'resolved_mdls': sum(1 for r in mdls.values() if r['judge_links']), 'unresolved_mdls': sum(1 for r in mdls.values() if not r['judge_links']),
                        'match_kinds': dict(Counter(l['match_kind'] for l in links.values())), 'unresolved_reasons': dict(Counter(u['reason'] for u in unresolved)),
                        'unresolved_keys_after_name_join_only': name_join_unresolved, 'resolved_by_native_bridge_keys': sum(1 for l in links.values() if l.get('basis') == NATIVE_BRIDGE),
                        'resolved_by_native_bridge_mdls': sum(1 for r in mdls.values() if any(l['basis'] == NATIVE_BRIDGE for l in r['judge_links'])),
                        'alias_layer_used': bool(alias_input)}
    blocking_failed = [c for c in qa['checks'] if c['status'] == 'failed' and c['blocking']]
    qa['status'] = 'passed' if not blocking_failed else 'failed'
    qa['summary'] = {'checks': len(qa['checks']), 'failed': sum(1 for c in qa['checks'] if c['status'] == 'failed'), 'blocking_failed': len(blocking_failed)}
    files = [
        write_jsonl('mdls.jsonl', [mdls[n] for n in sorted(mdls)]),
        write_jsonl('documents.jsonl', docs),
        write_json('court_map.json', court_map, len(court_map)),
        write_jsonl('edges.jsonl', edges),
        write_jsonl('unresolved.jsonl', unresolved_rows),
        write_json('qa.json', qa, len(qa['checks'])),
    ]
    for extra in (ENRICH / 'observations.jsonl', ENRICH / 'facts.jsonl', ENTITIES, PROFILES_DB, CL_DB, MDL3080):
        if extra.is_file():
            inputs.append({'path': extra.relative_to(ROOT).as_posix(), 'sha256': sha256_file(extra)})
    for d in conn_files:
        inputs.append({'path': 'sources/jpml_mdl_20260919/' + d['_file'], 'sha256': d['_sha256']})
    if alias_input:
        inputs.append(alias_input)
    pending_rows = [r for r in mdls.values() if r['status'] == 'pending']
    counts = {
        'mdls_pending': len(pending_rows), 'mdls_terminated': sum(1 for r in mdls.values() if r['status'] == 'terminated'),
        'actions_pending': sum(r['actions_pending'] or 0 for r in pending_rows), 'total_actions': sum(r['total_actions'] or 0 for r in pending_rows),
        'as_of': by_number['as_of'], 'counts_label': label_for(by_number['as_of']),
        'transferee_districts': len({r['district_code'] for r in pending_rows}), 'transferee_judges_printed': tot.get('transferee_judges'),
        'litigation_types': dict(Counter(r['litigation_type'] for r in pending_rows if r['litigation_type'])),
        'judge_join': qa['judge_join'], 'documents': len(docs), 'edges': len(edges), 'edges_by_basis': dict(Counter(e['basis'] for e in edges)),
        'district_codes_mapped': sum(1 for c in codes_seen if (court_map.get(c) or {}).get('cl_court_id')), 'district_codes_seen': len(codes_seen),
        'connector': qa['connector'], 'snapshots_quarterly': {'2026-03-31': len(circ_march['rows']), '2026-06-30': len(circ_june['rows'])},
    }
    validation = {
        'schema_version': '1', 'status': 'passed' if qa['status'] == 'passed' else 'failed', 'ready': qa['status'] == 'passed',
        'validated_at': now_utc(), 'data_files': files, 'counts': counts,
        'checks': [{'name': c['name'], 'status': c['status'], 'detail': (c['detail'] if c['status'] == 'failed' else None)} for c in qa['checks']]
                  + [{'name': 'raw_files_verified_against_receipts', 'status': 'passed', 'detail': '%d receipts re-hashed' % len(receipts)},
                     {'name': 'text_files_verified_against_manifest', 'status': 'passed', 'detail': '%d reading copies re-hashed' % len(manifest)}],
        'qualification': ('Publisher-reported JPML CM/ECF statistics as of the printed report date (%s); counts are actions pending/total as listed by the JPML, '
                          'not independently verified docket counts. Judge links are conservative name+court joins to FJC appointments (see match_kind); where the printed name '
                          'differs from the FJC name a link is made only by native ids (match_kind cl_person_native_bridge: the CourtListener person assigned to the master '
                          'docket is the person the profile is bridged to by FJC ids), never by name; '
                          'unresolved judges are listed, not guessed. CourtListener links come from saved connector responses (%d), not original HTTP bytes. '
                          'The quarterly by-circuit snapshots carry their own report dates. Nothing here is legal advice or a prediction.'
                          % (by_number['as_of'], len(conn_files))),
        'license_ref': 'U.S. Government work (JPML CM/ECF statistics reports and jpml.uscourts.gov pages); public domain in the United States.',
        'inputs': inputs,
    }
    (HERE / 'validation.json').write_bytes(json.dumps(validation, ensure_ascii=False, indent=1).encode('utf-8'))
    print('status', validation['status'], 'ready', validation['ready'])
    print('rows', {k: (len(v['rows']), v.get('printed_count') or (v.get('totals') or {}).get('mdl_dockets')) for k, v in reports.items()})
    print('sums', sum_p, sum_t, 'printed', rt)
    print('judge join', qa['judge_join'])
    print('connector', {k: v for k, v in qa['connector'].items() if k not in ('registry', 'per_mdl')})
    print('edges', counts['edges'], counts['edges_by_basis'])
    print('failed checks', [(c['name'], c['blocking']) for c in qa['checks'] if c['status'] == 'failed'])
    return 0 if validation['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
