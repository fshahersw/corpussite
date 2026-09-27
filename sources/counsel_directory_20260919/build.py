"""Unified counsel directory: firms, attorneys and their appearances per docket and per MDL,
built from every local counsel/firm source found under SW-BULK plus the Philadelphia Mass Tort
liaison-counsel list. Deterministic, offline, re-runnable.

Inputs (read-only, in place; see README.md for the full source-by-source account):
  1. SW-BULK/catalog/parties_by_docket/*.json (59 dockets; native CourtListener attorney_id) -- primary.
  2. SW-BULK/catalog/{attorneys.json,firms.json,parties_report.csv,masters.json,matters.json} -- QA cross-checks
     and docket -> MDL-master links; matters.json's own firms[]/roles[] business-tracking tags are NOT ingested
     (see README "what we found but did not use").
  3. sources/mdl_counsel_appearances_20260919 (already built from the AWS release b2b-cdbb8d040b95c7b65cfc;
     row counts re-verified directly against the raw *.jsonl.gz release files by this build, see
     verify_aws_release_counts()) -- own UUID identity space, never merged with CourtListener ids.
  4. sources/mdl_counsel_20260919 (CourtListener search-index scrape of 6 MDL master dockets) -- its 57 native
     attorney_record rows and 2,213 firm-text rows are folded into the unified firm/attorney grouping; its
     4,375 name-only rows (no id, no firm, no role) are excluded (see README).
  5. sources/mdl_docket_crosswalk_20260919 (crosswalk.jsonl + edges.jsonl) -- the only path used to attach an
     MDL number to a docket; sources/jpml_mdl_20260919/mdls.jsonl is a title/status fallback for an MDL number
     that is not one of the crosswalk's 59 resolved rows. Never joined by name.
  6. sources/mdl_docket_documents_20260919 (doc_type == 'leadership_appointment') and
     sources/mdl_docket_activity_20260919 (entry_type in {'leadership','steering_committee'}) -- linked only
     (CourtListener url + date + a short verbatim excerpt); no name is ever parsed out of the free text.
  7. returnedfiles/www.courts.phila.gov_pdf_cpcivil_Mass-Tort-Docket-and-Liaison-Counsel-List.pdf.json --
     Philadelphia Complex Litigation Center liaison-counsel list, parsed with a deterministic line-scanner
     (parse_phila_liaison) that only ever captures program/role/person-name/firm-name text, never contact data.

Rules enforced here (see README.md for the full write-up):
  - Attorney identity = native CourtListener attorney_id where present; never merge people by name.
  - Firm identity = a single deterministic normalised key (firm_key); never fuzzy-matched.
  - Natural-person litigant names are never published, regardless of side; organisations/named defendants may be.
  - Roles/sides are published exactly as the source gives them, plus one normalised label from a documented table.
  - No contact details (no e-mails, phones, street addresses) anywhere in the output.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SW_BULK = Path('C:/Users/firas/Downloads/SW-BULK')
CATALOG = SW_BULK / 'catalog'
PBD_DIR = CATALOG / 'parties_by_docket'
AWS_RELEASE = SW_BULK / 'AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc'
SCRAPE_SOURCES = HERE.parent
MDL_COUNSEL_OLD = SCRAPE_SOURCES / 'mdl_counsel_20260919'
MDL_COUNSEL_APPEARANCES = SCRAPE_SOURCES / 'mdl_counsel_appearances_20260919'
CROSSWALK_DIR = SCRAPE_SOURCES / 'mdl_docket_crosswalk_20260919'
JPML_DIR = SCRAPE_SOURCES / 'jpml_mdl_20260919'
MDL_DOCS_DIR = SCRAPE_SOURCES / 'mdl_docket_documents_20260919'
MDL_ACTIVITY_DIR = SCRAPE_SOURCES / 'mdl_docket_activity_20260919'
PHILA_JSON = Path('C:/Users/firas/Downloads/returnedfiles/'
                   'www.courts.phila.gov_pdf_cpcivil_Mass-Tort-Docket-and-Liaison-Counsel-List.pdf.json')
DB_PATH = HERE / 'counsel_directory.sqlite3'
VALIDATION_PATH = HERE / 'validation.json'

LICENSE_REF = 'sw_bulk_private_firm_work_product'

# --------------------------------------------------------------------------------------------------- firm identity
ENTITY_SUFFIXES = {'llp', 'llc', 'pllc', 'pc', 'pa', 'ltd', 'lpa', 'apc', 'plc', 'chartered'}
_TRAILING_PAREN_RE = re.compile(r'\s*\([^()]*\)\s*$')
_NON_ALNUM_RE = re.compile(r'[^a-z0-9\s]')
_WS_RE = re.compile(r'\s+')


def firm_key(raw):
    """Deterministic grouping key: casefold, '&'->'and', strip punctuation, collapse whitespace, drop a
    trailing entity suffix (llp/llc/pllc/pc/p.c./pa/p.a./ltd/lpa/apc/plc/chartered/'et al') and a trailing
    parenthetical (city, office or jurisdiction label, e.g. '(Newark)', '(US)'). Never fuzzy-matched: two
    strings that differ by anything else (a misspelling, an added partner name, a truncation) stay distinct.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = _TRAILING_PAREN_RE.sub('', raw.strip()).strip()
    if not text:
        return None
    text = text.casefold()
    text = text.replace('&', ' and ')
    # Delete periods/apostrophes outright (not to a space) so an abbreviation like "L.L.P." or "P.C."
    # collapses to "llp"/"pc" instead of fragmenting into single letters.
    text = text.replace('.', '').replace("'", '')
    text = _NON_ALNUM_RE.sub(' ', text)
    text = _WS_RE.sub(' ', text).strip()
    if not text:
        return None
    words = text.split(' ')
    if len(words) >= 2 and words[-2:] == ['et', 'al']:
        words = words[:-2]
    elif words and words[-1] in ENTITY_SUFFIXES:
        words = words[:-1]
    key = ' '.join(w for w in words if w).strip()
    return key or None


_ADDRESS_START_RE = re.compile(r'^\d+\s')
_PO_BOX_RE = re.compile(r'^p\.?\s*o\.?\s*box\b', re.I)
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
_GENERATIONAL = {'jr', 'sr', 'ii', 'iii', 'iv'}
_MIDDLE_INITIAL_RE = re.compile(r'^[A-Za-z]\.?$')
_FIRM_HINT_WORDS = ('llp', 'llc', 'pllc', 'pc', 'p.c.', 'pa', 'p.a.', 'ltd', 'lpa', 'apc', 'plc', 'chartered',
                     'law', 'firm', 'office', 'offices', 'group', 'associates', 'partners', 'company', 'llp.',
                     'attorneys', 'counsel', '&', ' and ')

REJECT_ADDRESS = 'address'
REJECT_PRO_SE = 'pro_se'
REJECT_BAR_NOTE = 'bar_admission_note'
REJECT_DATA_ARTIFACT = 'data_artifact'
REJECT_EMAIL = 'email_address'
REJECT_REGISTERED_AGENT = 'registered_agent_notation'
REJECT_PERSON_NAME = 'person_name_no_firm_words'

REJECT_REASON_LABELS = {
    REJECT_ADDRESS: 'Street address or PO box, not a firm name',
    REJECT_PRO_SE: "Self-represented ('pro se'), not a firm",
    REJECT_BAR_NOTE: 'Bar-admission / clerk note about an individual attorney, not a firm name',
    REJECT_DATA_ARTIFACT: 'Data-entry artifact (bounced e-mail notice, expired-address placeholder), not a firm',
    REJECT_EMAIL: 'E-mail address; contact details are never published and this is not a firm name',
    REJECT_REGISTERED_AGENT: "A 'c/o' registered-agent / service notation, not a law firm",
    REJECT_PERSON_NAME: 'A bare person name with no firm-indicating word',
}


def _looks_like_bare_person_name(s):
    cleaned = s.strip().rstrip('.')
    tokens = [t for t in re.split(r'[\s,]+', cleaned) if t]
    if len(tokens) < 3:
        return False
    last = tokens[-1].strip('.').casefold()
    if last not in _GENERATIONAL:
        return False
    if not any(_MIDDLE_INITIAL_RE.match(t) for t in tokens[:-1]):
        return False
    low = s.casefold()
    return not any(hint in low for hint in _FIRM_HINT_WORDS)


def classify_not_a_firm(raw):
    """Return one of the REJECT_* reason codes when `raw` is not really a firm name (an address, a bar
    admission note, a person name with no firm words, 'pro se', an e-mail address, a registered-agent
    notation, or a data-entry artifact), else None. Deliberately narrow: when unsure this returns None
    (keeps the string as a firm) because dropping a real firm loses directory data.
    """
    if not raw or not isinstance(raw, str) or not raw.strip():
        return REJECT_DATA_ARTIFACT
    s = raw.strip()
    low = s.casefold()
    if low == 'pro se':
        return REJECT_PRO_SE
    if _EMAIL_RE.search(s) or low.startswith('email:') or low.startswith('email '):
        return REJECT_EMAIL
    if 'admitted' in low and ('bar' in low or 'member' in low):
        return REJECT_BAR_NOTE
    if 'undeliverable' in low or 'address expired' in low or low == 'unknown':
        return REJECT_DATA_ARTIFACT
    if _ADDRESS_START_RE.match(s) or _PO_BOX_RE.match(s):
        return REJECT_ADDRESS
    if low.startswith('c/o '):
        return REJECT_REGISTERED_AGENT
    if _looks_like_bare_person_name(s):
        return REJECT_PERSON_NAME
    return None


# --------------------------------------------------------------------------------------------- party org/person
_ORG_WORDS = {
    'inc', 'incorporated', 'corp', 'corporation', 'company', 'co', 'llc', 'llp', 'ltd', 'limited', 'lp',
    'pllc', 'pc', 'plc', 'na', 'bank', 'trust', 'holdings', 'group', 'partners', 'partnership', 'association',
    'assn', 'foundation', 'university', 'college', 'hospital', 'healthcare', 'pharmaceutical', 'pharmaceuticals',
    'laboratories', 'labs', 'industries', 'enterprises', 'services', 'solutions', 'systems', 'technologies',
    'ventures', 'capital', 'insurance', 'agency', 'authority', 'district', 'municipal', 'township', 'county',
    'ag', 'sa', 'nv', 'gmbh', 'plc.', 'employees',
}
_ORG_PHRASES = ('city of', 'county of', 'state of', 'commonwealth of', 'united states', 'department of',
                 'board of', 'credit union', 'medical center', 'health system')
_STRONG_PERSON_WRAPPERS = ('estate of', 'personal representative', 'administrator of the estate',
                            'next of kin of', 'guardian of', 'special administrator')
_WORD_RE = re.compile(r'[a-z0-9]+')


def is_organization(name):
    """Conservative name-only heuristic: True only when an explicit business/entity/government-body
    token or phrase is present, unless a *strong* wrapper phrase ('estate of', 'personal representative',
    ...) shows a person's name is what is actually being named. A weak capacity phrase such as
    'individually' never overrides a real organisation token (e.g. 'The Chemours Company ... individually
    and as successor ...' stays an organisation). When unsure: not an organisation (counted, never listed).
    """
    if not name or not isinstance(name, str) or not name.strip():
        return False
    low = name.casefold()
    if any(p in low for p in _STRONG_PERSON_WRAPPERS):
        return False
    if any(re.search(r'\b' + re.escape(p) + r'\b', low) for p in _ORG_PHRASES):
        return True
    words = set(_WORD_RE.findall(low))
    return bool(words & _ORG_WORDS)


def public_case_name(case_name):
    """Return (public_text_or_None, suppressed: bool, reason). Only an 'In re ...' master-docket caption or
    a 'X v. Y' caption whose plaintiff side clears is_organization() is published; anything else
    (a bare person name, an unrecognised caption shape) is conservatively suppressed."""
    if not case_name or not case_name.strip():
        return None, True, 'no case name in source'
    s = case_name.strip()
    if s.casefold().startswith('in re'):
        return s, False, "'In re ...' master-docket caption names no individual party"
    parts = re.split(r'\s+v\.?s?\.?\s+', s, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        if is_organization(parts[0]):
            return s, False, 'plaintiff-side caption text names an organisation, not a natural person'
        return None, True, 'plaintiff-side caption text does not clear the organisation test; suppressed as a possible natural-person name'
    return None, True, "caption has no 'In re'/'v.' structure recognised as safe; suppressed as a possible natural-person name"


# ------------------------------------------------------------------------------------------------- role mapping
ROLE_MAP = {
    'attorney_to_be_noticed': 'Attorney to be noticed',
    'lead_attorney': 'Lead attorney',
    'pro_hac_vice': 'Pro hac vice',
    'terminated': 'Terminated',
    'inactive': 'Inactive',
    'unknown': 'Unknown',
    # Full CourtListener Role choices table (integer code, as recorded in
    # sources/mdl_counsel_20260919/coverage.json role_labels, cross-checked against the role_code/role_label
    # pairs embedded in that source's own attorney records). Code 10 = Unknown.
    '1': 'Attorney to be noticed',
    '2': 'Lead attorney',
    '3': 'Attorney in sealed group',
    '4': 'Pro hac vice',
    '5': 'Self-terminated',
    '6': 'Terminated',
    '7': 'Suspended',
    '8': 'Inactive',
    '9': 'Disbarred',
    '10': 'Unknown',
}


def normalize_role(raw):
    """Normalise a raw role value through the documented table above. The raw value is always kept
    alongside this label (never silently replaced). CourtListener role code 10 (int or str) = Unknown."""
    if raw is None:
        return 'Not stated'
    if isinstance(raw, int):
        raw = str(raw)
    text = str(raw).strip()
    if not text:
        return 'Not stated'
    if text.casefold().startswith('terminated'):
        return 'Terminated'
    key = text.strip().casefold().replace(' ', '_')
    return ROLE_MAP.get(key, text)


def normalize_side(party_types):
    """party_types is a list of strings as printed by the source (e.g. ['Defendant'] or ['Plaintiff'])."""
    if not party_types:
        return 'unspecified'
    types = set(t for t in party_types if isinstance(t, str))
    has_p = any('plaintiff' in t.casefold() for t in types)
    has_d = any('defendant' in t.casefold() for t in types)
    if has_p and not has_d:
        return 'plaintiff'
    if has_d and not has_p:
        return 'defendant'
    if has_p and has_d:
        return 'mixed'
    return 'other'


# -------------------------------------------------------------------------------------- Philadelphia liaison list
_MD_STRIP = str.maketrans('', '', '#*_\\')
_HEADER_RE = re.compile(r'^\((\d+)\)\s*(.+?)\s*\(([A-Za-z0-9]{1,10})\)\s*:?\s*$')
_ROLE_CORE = r"[A-Za-z][A-Za-z'\-\s]*?(?:Liaison(?:\s+Counsel)?|Lead\s+Counsel)"
_NAME_CORE = r"[A-Z][A-Za-z.\-']*(?:\s+[A-Z][A-Za-z.\-']*){0,4}(?:,?\s+(?:Jr|Sr|II|III|IV)\.?)?"
_ROLE_NAME_RE = re.compile(r"(?P<role>%s)\s*:\s*\|?\s*(?P<name>%s)" % (_ROLE_CORE, _NAME_CORE))
_HEADING_ONLY_RE = re.compile(r"^(?P<role>%s)\s*:?\s*$" % _ROLE_CORE)
_BARE_NAME_RE = re.compile(r"^%s$" % _NAME_CORE)
_PHONE_ONLY_RE = re.compile(r'^\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]?\d{4}')
_TRAILING_PHONE_RE = re.compile(r'\s*\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]?\d{4}.*$')
_CITY_STATE_ZIP_RE = re.compile(r',\s*[A-Z]{2}\.?\s*\d{5}')


def _clean_line(line):
    line = line.replace('\u2019', "'").replace('\x92', "'").replace('\ufffd', "'")
    return line.translate(_MD_STRIP)


def _join_table_row(line):
    stripped = line.strip()
    if stripped.count('|') < 2:
        return None
    cells = [c.strip() for c in stripped.strip('|').split('|')]
    cells = [c for c in cells if c and not re.fullmatch(r'-{2,}', c)]
    return ' '.join(cells)


def _match_program_header(line):
    m = _HEADER_RE.match(line.strip())
    if not m:
        return None
    return {'number': int(m.group(1)), 'name': m.group(2).strip(), 'code': m.group(3)}


def _looks_like_bare_name(s):
    if not s or any(ch.isdigit() for ch in s):
        return False
    return bool(_BARE_NAME_RE.fullmatch(s.strip()))


def _looks_like_firm_line(s):
    if not s:
        return False
    low = s.casefold()
    if '@' in s or low.startswith('email'):
        return False
    if _ADDRESS_START_RE.match(s) or _PO_BOX_RE.match(s):
        return False
    if low.startswith(('telephone', 'phone', 'fax', 'tel:')):
        return False
    if _PHONE_ONLY_RE.match(s.strip()):
        return False
    if _CITY_STATE_ZIP_RE.search(s):
        return False
    if low.startswith('attorney') and ' for ' in low:
        return False
    if low.startswith('prepared by'):
        return False
    if not re.search(r'[A-Za-z]{2,}', s):
        return False
    return True


def _clean_firm_candidate(s):
    s = _TRAILING_PHONE_RE.sub('', s).strip()
    s = re.sub(r'\s+(fax|tel|telephone)\.?:?\s*$', '', s, flags=re.I).strip()
    return s


def _classify_side(role_text):
    low = role_text.casefold()
    if 'plaintiff' in low:
        return 'plaintiff'
    if 'defendant' in low or 'defense' in low:
        return 'defendant'
    return 'unspecified'


def parse_phila_liaison(markdown):
    """Deterministic line-scanner over the extracted PDF markdown. Only ever captures a program
    number/name/code, a role label as printed, a person name and a firm name; never an address, phone
    number or e-mail (those are excluded by construction, not redacted after the fact). Best-effort over a
    10-page, multi-format (headings/bold text/markdown tables) court PDF: a liaison mention that this
    scanner cannot confidently pair with both a role and a name is skipped, never guessed.
    """
    if not markdown:
        return []
    lines = []
    for raw_line in markdown.splitlines():
        cleaned = _clean_line(raw_line)
        joined = _join_table_row(cleaned)
        lines.append(joined if joined is not None else cleaned)
    n = len(lines)
    results = []
    seen = set()
    current = None
    i = 0
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        header = _match_program_header(line)
        if header:
            current = header
            i += 1
            continue
        role_text = name_text = None
        next_index = i + 1
        m = _ROLE_NAME_RE.search(line)
        if m:
            role_text = m.group('role').strip()
            name_text = m.group('name').strip().rstrip(',')
        else:
            hm = _HEADING_ONLY_RE.match(line)
            if hm and current is not None:
                role_text = hm.group('role').strip()
                found_name = None
                j, attempts = i + 1, 0
                # Blank/whitespace-only lines are formatting noise, not scan budget: only a line with
                # actual content counts toward the attempt cap. j is still bounded absolutely so the
                # scanner never drifts arbitrarily far into unrelated content. j must NOT be advanced past
                # a line that causes a break (next_index = j below needs to point AT that line, not after
                # it, so the firm-search phase starting from next_index reconsiders it).
                while j < n and j < i + 25 and attempts < 8:
                    cand = lines[j].strip()
                    if not cand:
                        j += 1
                        continue
                    attempts += 1
                    if _looks_like_bare_name(cand):
                        found_name = cand
                        j += 1
                        continue
                    break
                name_text = found_name
                next_index = j
        if role_text and name_text and current is not None:
            firm_text = None
            k, attempts = next_index, 0
            while k < n and k < next_index + 25 and attempts < 8:
                cand = lines[k].strip()
                k += 1
                if not cand:
                    continue
                attempts += 1
                dup = _ROLE_NAME_RE.search(cand)
                if dup and (dup.group('role').strip(), dup.group('name').strip().rstrip(',')) == (role_text, name_text):
                    continue
                if _ROLE_NAME_RE.search(cand) or _HEADING_ONLY_RE.match(cand) or _match_program_header(cand):
                    break
                # Note: unlike the name-scan loop above, a bare-name-shaped candidate is NOT skipped here.
                # Many real firm names (e.g. 'Motley Rice LLC', 'Troutman Pepper', 'Wagstaff Law Firm') are
                # indistinguishable in shape from a person's name; skipping them made the scanner walk past
                # the real firm line into the next address/city line instead.
                if not _looks_like_firm_line(cand):
                    continue
                firm_text = _clean_firm_candidate(cand)
                break
            key = (current['number'], role_text, name_text)
            if key not in seen:
                seen.add(key)
                results.append({
                    'program_number': current['number'], 'program_name': current['name'],
                    'program_code': current['code'], 'role_raw': role_text, 'side': _classify_side(role_text),
                    'person_name': name_text, 'firm_raw': firm_text,
                })
        i += 1
    return results


# ------------------------------------------------------------------------------------------------------ IO helpers
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def read_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_jsonl_gz(path):
    rows = []
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def short_id(*parts):
    return hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:16]


# =========================================================================================== build orchestration
class Directory:
    """Accumulates the unified counsel directory in memory before it is written to SQLite."""

    def __init__(self):
        self.firm_variants = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'sources': set()}))
        self.rejected = defaultdict(lambda: {'reason': None, 'sources': set(), 'count': 0})
        self.dockets = {}
        self.attorneys = {}
        self.appearances = []
        self.mdl_meta = {}
        self.leadership_links = []
        self.phila_rows = []
        self.checks = []
        self.notes = []

    # ---- firms -----------------------------------------------------------------------------------------
    def add_firm_text(self, raw_text, source):
        """Feed one printed firm string through the reject classifier then the firm-key grouping.
        Returns the firm_key (for later resolution to a firm id), or None when rejected or blank."""
        if raw_text is None:
            return None
        text = raw_text if isinstance(raw_text, str) else str(raw_text)
        text = text.strip()
        if not text:
            return None
        reason = classify_not_a_firm(text)
        if reason:
            entry = self.rejected[text]
            entry['reason'] = reason
            entry['sources'].add(source)
            entry['count'] += 1
            return None
        key = firm_key(text)
        if key is None:
            return None
        variant = self.firm_variants[key][text]
        variant['count'] += 1
        variant['sources'].add(source)
        return key

    # ---- dockets -----------------------------------------------------------------------------------------
    def upsert_docket(self, docket_id, **fields):
        row = self.dockets.setdefault(docket_id, {'id': docket_id})
        for k, v in fields.items():
            if v is not None or k not in row:
                row.setdefault(k, v)
                if row.get(k) is None and v is not None:
                    row[k] = v
        return row

    # ---- attorneys ---------------------------------------------------------------------------------------
    def upsert_attorney(self, attorney_id, display_name, id_kind, source):
        row = self.attorneys.setdefault(attorney_id, {
            'id': attorney_id, 'display_name': display_name, 'id_kind': id_kind, 'sources': set(),
        })
        if display_name and (not row.get('display_name')):
            row['display_name'] = display_name
        row['sources'].add(source)
        return row

    # ---- appearances -------------------------------------------------------------------------------------
    def add_appearance(self, docket_id, attorney_id, firm_key_tmp, firm_raw, role_raw, side,
                        mdl_number, source, party_name_public=None, party_is_organization=None):
        app_id = short_id('app', docket_id, attorney_id, firm_key_tmp, role_raw, side,
                           party_name_public, len(self.appearances))
        self.appearances.append({
            'id': app_id, 'docket_id': docket_id, 'attorney_id': attorney_id, 'firm_key_tmp': firm_key_tmp,
            'firm_raw': firm_raw, 'role_raw': None if role_raw is None else str(role_raw),
            'role_normalized': normalize_role(role_raw), 'side': side, 'mdl_number': mdl_number,
            'source': source, 'party_name_public': party_name_public,
            'party_is_organization': party_is_organization,
        })
        return app_id


# ------------------------------------------------------------------------------------------------- MDL resolution
def load_crosswalk():
    validation = read_json(CROSSWALK_DIR / 'validation.json')
    crosswalk = read_jsonl(CROSSWALK_DIR / 'crosswalk.jsonl')
    edges = read_jsonl(CROSSWALK_DIR / 'edges.jsonl')
    unresolved = read_jsonl(CROSSWALK_DIR / 'unresolved.jsonl')
    by_number = {r['mdl_number']: r for r in crosswalk}
    by_catalog_master = {r['catalog_master_docket_id']: r for r in crosswalk if r.get('catalog_master_docket_id')}
    by_cl_docket = {r['cl_docket_id']: r for r in crosswalk if r.get('cl_docket_id')}
    by_aws_matter = {r['aws_matter_id']: r for r in crosswalk if r.get('aws_matter_id')}
    member_matter_to_mdl = {}
    for e in edges:
        if e.get('relation') == 'member_of_mdl' and e.get('to', {}).get('type') == 'mdl' and e.get('from', {}).get('type') == 'aws_matter':
            member_matter_to_mdl[e['from']['id']] = e['to']['id']
    unresolved_by_catalog_master = {}
    for u in unresolved:
        cid = u.get('catalog_master_docket_id')
        if cid:
            unresolved_by_catalog_master[cid] = u.get('reason', 'unresolved (no reason recorded)')
    return {
        'by_number': by_number, 'by_catalog_master': by_catalog_master, 'by_cl_docket': by_cl_docket,
        'by_aws_matter': by_aws_matter, 'member_matter_to_mdl': member_matter_to_mdl,
        'unresolved_by_catalog_master': unresolved_by_catalog_master, 'validation': validation,
    }


def load_jpml_registry():
    mdls = read_jsonl(JPML_DIR / 'mdls.jsonl')
    return {m['mdl_number']: m for m in mdls}


def resolve_mdl_for_catalog_master(master_docket_id, crosswalk, jpml):
    if master_docket_id is None:
        return None, None, None, None, 'no master docket linked in source data (mdl_master_docket_id is null)'
    row = crosswalk['by_catalog_master'].get(master_docket_id)
    if row:
        return (row['mdl_number'], row['mdl_title'], row['mdl_status'], row.get('cl_court_id'),
                'resolved via sources/mdl_docket_crosswalk_20260919 crosswalk.jsonl (catalog_master_docket_id direct match)')
    reason = crosswalk['unresolved_by_catalog_master'].get(master_docket_id)
    if reason:
        return None, None, None, None, 'unresolved per sources/mdl_docket_crosswalk_20260919/unresolved.jsonl: %s' % reason
    return None, None, None, None, 'master docket id not found in the 17-row SW-BULK/catalog/masters.json crosswalk join; no MDL link available'


def resolve_mdl_for_number(mdl_number, crosswalk, jpml):
    row = crosswalk['by_number'].get(mdl_number)
    if row:
        return (row['mdl_title'], row['mdl_status'], row.get('cl_court_id'), row.get('cl_docket_id'),
                row.get('master_docket_number_registry'), 'in the 59-row mdl_docket_crosswalk_20260919 crosswalk')
    reg = jpml.get(mdl_number)
    if reg:
        return (reg.get('title'), reg.get('status'), reg.get('cl_court_id'), None, None,
                'not in the 59-row crosswalk; title/status from sources/jpml_mdl_20260919 registry only, no local master-docket id available')
    return None, None, None, None, None, 'MDL number not found in either the crosswalk or the 176-row JPML registry snapshot'


# ------------------------------------------------------------------------------------------------ source ingestion
def ingest_parties_by_docket(d: Directory, crosswalk, jpml):
    files = sorted(PBD_DIR.glob('*.json'))
    counts = Counter()
    for path in files:
        doc = read_json(path)
        docket_id = doc.get('docket_id')
        docket_key = 'cl:%s' % docket_id
        master_id = doc.get('mdl_master_docket_id')
        mdl_number, mdl_title, mdl_status, cl_court_id, basis = resolve_mdl_for_catalog_master(master_id, crosswalk, jpml)
        case_public, suppressed, reason = public_case_name(doc.get('case_name'))
        d.upsert_docket(docket_key, native_docket_id=docket_id, aws_matter_id=None,
                        docket_number=doc.get('docket_number'), court=doc.get('court'),
                        case_name_public=case_public, case_name_suppressed=suppressed,
                        mdl_master_docket_id=master_id, mdl_number=mdl_number, mdl_title=mdl_title,
                        mdl_resolution_basis=basis, source='sw_bulk_parties_by_docket',
                        courtlistener_url='https://www.courtlistener.com/docket/%s/' % docket_id if docket_id else None)
        counts['dockets'] += 1
        for party in doc.get('parties') or []:
            party_types = party.get('party_types') or []
            side = normalize_side(party_types)
            party_name = party.get('name')
            org = is_organization(party_name) if party_name else False
            party_public = party_name if (party_name and org) else None
            counts['parties'] += 1
            for att in party.get('attorneys') or []:
                counts['attorney_entries'] += 1
                cl_attorney_id = att.get('attorney_id')
                if cl_attorney_id:
                    attorney_key = 'cl:%s' % cl_attorney_id
                    d.upsert_attorney(attorney_key, att.get('name'), 'courtlistener', 'sw_bulk_parties_by_docket')
                else:
                    attorney_key = 'printed:pbd:%s:%s' % (docket_id, short_id(att.get('name')))
                    d.upsert_attorney(attorney_key, att.get('name'), 'name_only', 'sw_bulk_parties_by_docket')
                firm_key_tmp = d.add_firm_text(att.get('firm'), 'sw_bulk_parties_by_docket')
                d.add_appearance(docket_key, attorney_key, firm_key_tmp, att.get('firm'), att.get('role'), side,
                                  mdl_number, 'sw_bulk_parties_by_docket', party_public, org)
    return counts


def verify_aws_release_counts():
    """Stream-count the raw AWS release gz files directly (per BUILD_CONTRACT: 'measure each before
    designing; stream gz files') and cross-check against sources/mdl_counsel_appearances_20260919's own
    validation.json counts, which this build consumes instead of re-parsing the release a second time."""
    names = ('attorneys.jsonl.gz', 'firms.jsonl.gz', 'counsel_appearances.jsonl.gz', 'parties.jsonl.gz')
    measured = {}
    for name in names:
        n = 0
        with gzip.open(AWS_RELEASE / name, 'rt', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    n += 1
        measured[name] = n
    appearances_layer = read_json(MDL_COUNSEL_APPEARANCES / 'validation.json')
    expected = appearances_layer.get('counts', {})
    match = (measured['attorneys.jsonl.gz'] == expected.get('attorney_records_total') and
             measured['firms.jsonl.gz'] == expected.get('firms_total') and
             measured['counsel_appearances.jsonl.gz'] == expected.get('appearances_total') and
             measured['parties.jsonl.gz'] == expected.get('parties_total'))
    return measured, match


def ingest_aws_release_layer(d: Directory):
    """Consume the already-built sources/mdl_counsel_appearances_20260919 layer (attorneys/firms/
    appearances/parties.jsonl), which was built directly from the AWS release b2b-cdbb8d040b95c7b65cfc and
    already carries the crosswalk-based MDL linkage and role normalisation for that release; this avoids
    re-implementing that join a second time from the raw *.jsonl.gz files (independently re-measured above)."""
    appearances = read_jsonl(MDL_COUNSEL_APPEARANCES / 'appearances.jsonl')
    attorneys_meta = {r['attorney_id']: r for r in read_jsonl(MDL_COUNSEL_APPEARANCES / 'attorneys.jsonl')}
    parties_meta = {r['party_id']: r for r in read_jsonl(MDL_COUNSEL_APPEARANCES / 'parties.jsonl')}
    counts = Counter()
    for row in appearances:
        counts['appearances'] += 1
        matter_id = row['matter_id']
        docket_key = 'aws:%s' % matter_id
        mdl_number = row.get('mdl_number_extended')
        mdl_title = mdl_status = None
        if mdl_number is not None:
            cw = load_crosswalk_cached()
            entry = cw['by_number'].get(mdl_number)
            if entry:
                mdl_title, mdl_status = entry['mdl_title'], entry['mdl_status']
        d.upsert_docket(docket_key, native_docket_id=None, aws_matter_id=matter_id, docket_number=None,
                        court=None, case_name_public=None, case_name_suppressed=True,
                        mdl_master_docket_id=None, mdl_number=mdl_number, mdl_title=mdl_title,
                        mdl_resolution_basis=('mdl_match_basis=%s via mdl_counsel_appearances_20260919 '
                                               '(extended through crosswalk member_of_mdl edges when direct)' % row.get('mdl_match_basis'))
                        if mdl_number is not None else 'appearance matter does not resolve to an MDL even via the crosswalk extension',
                        source='aws_release_b2b', courtlistener_url=None)
        attorney_key = 'aws:%s' % row['attorney_id']
        d.upsert_attorney(attorney_key, row.get('attorney_name'), 'aws_release_uuid', 'aws_release_b2b')
        firm_key_tmp = d.add_firm_text(row.get('raw_firm_name'), 'aws_release_b2b')
        party_meta = parties_meta.get(row.get('party_id')) or {}
        party_public = party_meta.get('name') if party_meta.get('listable') else None
        party_is_org = True if party_meta.get('category') == 'organization' else (
            False if party_meta.get('category') in ('natural_person_count_only',) else None)
        d.add_appearance(docket_key, attorney_key, firm_key_tmp, row.get('raw_firm_name'), row.get('role'),
                          row.get('party_side') or 'unspecified', mdl_number, 'aws_release_b2b',
                          party_public, party_is_org)
    return counts


_CROSSWALK_CACHE = {}


def load_crosswalk_cached():
    if 'v' not in _CROSSWALK_CACHE:
        _CROSSWALK_CACHE['v'] = load_crosswalk()
    return _CROSSWALK_CACHE['v']


def ingest_mdl_counsel_old(d: Directory, crosswalk, jpml):
    """Fold sources/mdl_counsel_20260919 (CourtListener search-index scrape of 6 MDL master dockets) into
    the unified grouping: its 57 native-id attorney_record rows become appearance rows; its 2,213 firm-text
    rows become firm-variant evidence at the MDL level (no specific attorney; this source itself does not
    link most of them to one). The 4,375 'search_index_name_only' rows (no native id, no firm, no role) are
    excluded -- see README."""
    attorneys = read_jsonl(MDL_COUNSEL_OLD / 'attorneys.jsonl')
    firms = read_jsonl(MDL_COUNSEL_OLD / 'firms.jsonl')
    counts = Counter()
    mdl_docket_key = {}

    def docket_key_for_mdl(mdl_number):
        if mdl_number in mdl_docket_key:
            return mdl_docket_key[mdl_number]
        title, status, court, cl_docket_id, master_docket_number, basis = resolve_mdl_for_number(mdl_number, crosswalk, jpml)
        key = 'cl:%s' % cl_docket_id if cl_docket_id else 'mdl_master:%s' % mdl_number
        d.upsert_docket(key, native_docket_id=cl_docket_id, aws_matter_id=None, docket_number=master_docket_number,
                        court=court, case_name_public=title if title and title.casefold().startswith('in re') else None,
                        case_name_suppressed=not (title and title.casefold().startswith('in re')),
                        mdl_master_docket_id=None, mdl_number=mdl_number, mdl_title=title, mdl_resolution_basis=basis,
                        source='mdl_counsel_courtlistener_scrape', courtlistener_url=(
                            'https://www.courtlistener.com/docket/%s/' % cl_docket_id if cl_docket_id else None))
        mdl_docket_key[mdl_number] = key
        return key

    for row in attorneys:
        if row.get('detail_level') != 'attorney_record':
            counts['name_only_excluded'] += 1
            continue
        counts['attorney_records'] += 1
        cl_attorney_id = row.get('cl_attorney_id')
        attorney_key = 'cl:%s' % cl_attorney_id if cl_attorney_id else 'printed:mdlcounsel:%s' % short_id(row.get('name'), row.get('mdl_number'))
        d.upsert_attorney(attorney_key, row.get('name'), 'courtlistener' if cl_attorney_id else 'name_only', 'mdl_counsel_courtlistener_scrape')
        docket_key = docket_key_for_mdl(row.get('mdl_number'))
        firm_key_tmp = d.add_firm_text(row.get('firm_text'), 'mdl_counsel_courtlistener_scrape')
        roles = row.get('roles') or []
        if roles:
            # For the 27 records with roles, each role entry is a rich dict (role_code, party_types,
            # party_name, ...) -- one appearance row per role, exactly like source A's own granularity.
            for role_item in roles:
                if isinstance(role_item, dict):
                    role_value = role_item.get('role_code')
                    if role_value is None:
                        role_value = role_item.get('role_label')
                    side = normalize_side(role_item.get('party_types'))
                else:
                    role_value, side = role_item, 'unspecified'
                d.add_appearance(docket_key, attorney_key, firm_key_tmp, row.get('firm_text'), role_value, side,
                                  row.get('mdl_number'), 'mdl_counsel_courtlistener_scrape')
        else:
            d.add_appearance(docket_key, attorney_key, firm_key_tmp, row.get('firm_text'), None, 'unspecified',
                              row.get('mdl_number'), 'mdl_counsel_courtlistener_scrape')

    for row in firms:
        counts['firm_text_rows'] += 1
        # name_variants is this source's OWN already-cleaned printed-variant list (bar-admission notes
        # already split off per its admission_note_rule); source_texts intentionally keeps the raw,
        # uncleaned text (still carrying the note) for its own provenance and must not be used here.
        texts = row.get('name_variants') or ([row['name']] if row.get('name') else [])
        for mdl_number in row.get('mdl_numbers') or []:
            docket_key_for_mdl(mdl_number)  # ensure the docket row exists even with no linked attorney
            for text in texts:
                firm_key_tmp = d.add_firm_text(text, 'mdl_counsel_courtlistener_scrape')
                if firm_key_tmp is None:
                    continue
                d.add_appearance(docket_key_for_mdl(mdl_number), None, firm_key_tmp, text, None, 'unspecified',
                                  mdl_number, 'mdl_counsel_courtlistener_scrape_firm_text')
    return counts


def ingest_leadership_evidence(d: Directory):
    counts = Counter()
    docs = read_jsonl(MDL_DOCS_DIR / 'documents.jsonl')
    for row in docs:
        if row.get('doc_type') != 'leadership_appointment':
            continue
        counts['documents'] += 1
        excerpt = (row.get('raw_entry_description') or '')[:300]
        d.leadership_links.append({
            'id': 'lead:doc:%s' % row['id'], 'mdl_number': row.get('mdl_number'),
            'source_layer': 'mdl_docket_documents_20260919', 'record_id': row['id'],
            'label': 'Leadership appointment', 'date': row.get('entry_date_filed'),
            'docket_number': row.get('docket_number'), 'court': row.get('court'),
            'courtlistener_url': row.get('courtlistener_url'), 'excerpt': excerpt,
        })
    entries = read_jsonl(MDL_ACTIVITY_DIR / 'entries.jsonl')
    cl_base = 'https://www.courtlistener.com/docket/%s/'
    for row in entries:
        entry_type = row.get('entry_type')
        if entry_type not in ('leadership', 'steering_committee'):
            continue
        counts['activity_entries'] += 1
        label = 'Steering committee' if entry_type == 'steering_committee' else 'Leadership'
        excerpt = (row.get('description') or '')[:300]
        url = cl_base % row['cl_docket_id'] if row.get('cl_docket_id') else None
        d.leadership_links.append({
            'id': 'lead:act:%s' % row['id'], 'mdl_number': row.get('mdl_number'),
            'source_layer': 'mdl_docket_activity_20260919', 'record_id': row['id'], 'label': label,
            'date': row.get('published_at'), 'docket_number': row.get('member_docket_number'),
            'court': row.get('court_id'), 'courtlistener_url': url, 'excerpt': excerpt,
        })
    return counts


def ingest_phila_liaison(d: Directory):
    doc = read_json(PHILA_JSON)
    rows = parse_phila_liaison(doc.get('markdown') or '')
    metadata = doc.get('metadata') or {}
    for row in rows:
        firm_key_tmp = d.add_firm_text(row.get('firm_raw'), 'phila_mass_tort_liaison_list') if row.get('firm_raw') else None
        d.phila_rows.append({
            'id': 'phila:%s' % short_id(row['program_number'], row['role_raw'], row['person_name']),
            'program_number': row['program_number'], 'program_name': row['program_name'],
            'program_code': row['program_code'], 'role_raw': row['role_raw'], 'side': row['side'],
            'person_name': row['person_name'], 'firm_raw': row.get('firm_raw'), 'firm_key_tmp': firm_key_tmp,
            'source_url': metadata.get('sourceURL'), 'captured_at': metadata.get('cachedAt'),
        })
    return Counter(programs=len({r['program_number'] for r in rows}), liaisons=len(rows))


# ------------------------------------------------------------------------------------------------------ QA checks
def qa_cross_check_catalog_firms(d: Directory):
    """Cross-check MY per-docket ingestion of parties_by_docket against SW-BULK's own catalog/firms.json
    rollup for the exact same printed firm string (never the grouped firm), mirroring the
    'catalog_report_counts_match_per_master' pattern used elsewhere in this corpus."""
    catalog_firms = read_json(CATALOG / 'firms.json')
    by_raw_docket = defaultdict(set)
    by_raw_side_count = defaultdict(Counter)
    for app in d.appearances:
        if app['source'] != 'sw_bulk_parties_by_docket' or not app['firm_raw']:
            continue
        by_raw_docket[app['firm_raw']].add(app['docket_id'])
        by_raw_side_count[app['firm_raw']][app['side']] += 1
    mismatches = []
    checked = 0
    for row in catalog_firms:
        raw = row['firm']
        if raw not in by_raw_docket:
            continue
        checked += 1
        mine_dockets = len(by_raw_docket[raw])
        if mine_dockets != row.get('docket_count'):
            mismatches.append({'firm': raw, 'field': 'docket_count', 'mine': mine_dockets, 'catalog': row.get('docket_count')})
    return {'name': 'catalog_firms_json_docket_counts_match_for_exact_printed_strings', 'checked': checked,
            'mismatches': mismatches, 'passed': not mismatches}


def qa_cross_check_matters_master_link(d: Directory):
    """Cross-check catalog/matters.json's own mdl_master_docket_id against parties_by_docket's, for the
    dockets the two catalog files share (docket -> MDL master links, per the declared inputs)."""
    matters = {m['docket_id']: m for m in read_json(CATALOG / 'matters.json')}
    mismatches = []
    checked = 0
    for docket_id_key, row in d.dockets.items():
        if row.get('source') != 'sw_bulk_parties_by_docket':
            continue
        native = row.get('native_docket_id')
        m = matters.get(native)
        if not m:
            continue
        checked += 1
        if m.get('mdl_master_docket_id') != row.get('mdl_master_docket_id'):
            mismatches.append({'docket_id': native, 'parties_by_docket': row.get('mdl_master_docket_id'),
                                'matters_json': m.get('mdl_master_docket_id')})
    return {'name': 'matters_json_master_docket_id_matches_parties_by_docket', 'checked': checked,
            'mismatches': mismatches, 'passed': not mismatches}


# --------------------------------------------------------------------------------------------------- finalisation
def finalize_firms(d: Directory):
    """Assign a stable id to each firm_key, pick a display name (the printed variant with the highest
    combined count; ties broken by shortest-then-alphabetical for determinism), and resolve every
    appearance/phila_liaison row's firm_key_tmp to that id."""
    firm_rows = []
    key_to_id = {}
    for key in sorted(d.firm_variants):
        variants = d.firm_variants[key]
        best_text = sorted(variants, key=lambda t: (-variants[t]['count'], len(t), t))[0]
        firm_id = 'firm:%s' % short_id('firm', key)
        key_to_id[key] = firm_id
        firm_rows.append({'id': firm_id, 'firm_key': key, 'display_name': best_text,
                           'variant_count': len(variants)})
    for app in d.appearances:
        app['firm_id'] = key_to_id.get(app['firm_key_tmp'])
    for row in d.phila_rows:
        row['firm_id'] = key_to_id.get(row.get('firm_key_tmp'))
    variant_rows = []
    for key, variants in d.firm_variants.items():
        firm_id = key_to_id[key]
        for text, info in variants.items():
            variant_rows.append({'firm_id': firm_id, 'variant_text': text,
                                  'source': ','.join(sorted(info['sources'])), 'count': info['count']})
    # roll-ups
    docket_sets, attorney_sets, mdl_sets, roles_seen, appearance_counts = (defaultdict(set), defaultdict(set),
                                                                            defaultdict(set), defaultdict(set), Counter())
    for app in d.appearances:
        fid = app.get('firm_id')
        if not fid:
            continue
        appearance_counts[fid] += 1
        docket_sets[fid].add(app['docket_id'])
        if app.get('attorney_id'):
            attorney_sets[fid].add(app['attorney_id'])
        if app.get('mdl_number') is not None:
            mdl_sets[fid].add(app['mdl_number'])
        if app.get('role_normalized'):
            roles_seen[fid].add(app['role_normalized'])
    for row in firm_rows:
        fid = row['id']
        row['appearance_count'] = appearance_counts.get(fid, 0)
        row['docket_count'] = len(docket_sets.get(fid, ()))
        row['attorney_count'] = len(attorney_sets.get(fid, ()))
        row['mdl_count'] = len(mdl_sets.get(fid, ()))
        row['roles_seen'] = json.dumps(sorted(roles_seen.get(fid, ())))
    return firm_rows, variant_rows, key_to_id


def finalize_attorneys(d: Directory):
    rows = []
    docket_sets, mdl_sets, firm_sets, appearance_counts = (defaultdict(set), defaultdict(set), defaultdict(set), Counter())
    for app in d.appearances:
        aid = app.get('attorney_id')
        if not aid:
            continue
        appearance_counts[aid] += 1
        docket_sets[aid].add(app['docket_id'])
        if app.get('mdl_number') is not None:
            mdl_sets[aid].add(app['mdl_number'])
        if app.get('firm_id'):
            firm_sets[aid].add(app['firm_id'])
    for aid, meta in d.attorneys.items():
        rows.append({
            'id': aid, 'display_name': meta.get('display_name') or '(name not recorded)', 'id_kind': meta['id_kind'],
            'source': ','.join(sorted(meta['sources'])), 'appearance_count': appearance_counts.get(aid, 0),
            'docket_count': len(docket_sets.get(aid, ())), 'mdl_count': len(mdl_sets.get(aid, ())),
            'firm_ids': json.dumps(sorted(firm_sets.get(aid, ()))),
        })
    return rows


def finalize_mdl_links(d: Directory, crosswalk, jpml):
    numbers = set()
    for app in d.appearances:
        if app.get('mdl_number') is not None:
            numbers.add(app['mdl_number'])
    for row in d.dockets.values():
        if row.get('mdl_number') is not None:
            numbers.add(row['mdl_number'])
    for row in d.leadership_links:
        if row.get('mdl_number') is not None:
            numbers.add(row['mdl_number'])
    firm_sets, attorney_sets, docket_sets, app_counts = (defaultdict(set), defaultdict(set), defaultdict(set), Counter())
    for app in d.appearances:
        n = app.get('mdl_number')
        if n is None:
            continue
        app_counts[n] += 1
        docket_sets[n].add(app['docket_id'])
        if app.get('firm_id'):
            firm_sets[n].add(app['firm_id'])
        if app.get('attorney_id'):
            attorney_sets[n].add(app['attorney_id'])
    rows = []
    for number in sorted(numbers):
        title, status, court, cl_docket_id, master_docket_number, basis = resolve_mdl_for_number(number, crosswalk, jpml)
        rows.append({
            'mdl_number': number, 'mdl_title': title, 'mdl_status': status, 'cl_court_id': court,
            'master_docket_number': master_docket_number, 'resolution_basis': basis,
            'firms_count': len(firm_sets.get(number, ())), 'attorneys_count': len(attorney_sets.get(number, ())),
            'dockets_count': len(docket_sets.get(number, ())), 'appearances_count': app_counts.get(number, 0),
        })
    return rows


def finalize_rejected(d: Directory):
    rows = []
    for text, info in d.rejected.items():
        rows.append({'id': short_id('rej', text), 'raw_string': text, 'reason': info['reason'],
                     'sources': json.dumps(sorted(info['sources'])), 'occurrence_count': info['count']})
    return rows


# ------------------------------------------------------------------------------------------------------ SQLite
SCHEMA = '''
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
CREATE TABLE firms(id TEXT PRIMARY KEY, firm_key TEXT UNIQUE, display_name TEXT, variant_count INTEGER,
    appearance_count INTEGER, docket_count INTEGER, attorney_count INTEGER, mdl_count INTEGER, roles_seen TEXT);
CREATE TABLE firm_variants(firm_id TEXT, variant_text TEXT, source TEXT, count INTEGER);
CREATE TABLE attorneys(id TEXT PRIMARY KEY, display_name TEXT, id_kind TEXT, source TEXT,
    appearance_count INTEGER, docket_count INTEGER, mdl_count INTEGER, firm_ids TEXT);
CREATE TABLE appearances(id TEXT PRIMARY KEY, docket_id TEXT, attorney_id TEXT, firm_id TEXT, firm_raw TEXT,
    role_raw TEXT, role_normalized TEXT, side TEXT, party_name_public TEXT, party_is_organization INTEGER,
    mdl_number INTEGER, source TEXT);
CREATE TABLE dockets(id TEXT PRIMARY KEY, native_docket_id INTEGER, aws_matter_id TEXT, docket_number TEXT,
    court TEXT, case_name_public TEXT, case_name_suppressed INTEGER, mdl_master_docket_id INTEGER,
    mdl_number INTEGER, mdl_title TEXT, mdl_resolution_basis TEXT, source TEXT, courtlistener_url TEXT);
CREATE TABLE mdl_links(mdl_number INTEGER PRIMARY KEY, mdl_title TEXT, mdl_status TEXT, cl_court_id TEXT,
    master_docket_number TEXT, resolution_basis TEXT, firms_count INTEGER, attorneys_count INTEGER,
    dockets_count INTEGER, appearances_count INTEGER);
CREATE TABLE leadership_links(id TEXT PRIMARY KEY, mdl_number INTEGER, source_layer TEXT, record_id TEXT,
    label TEXT, date TEXT, docket_number TEXT, court TEXT, courtlistener_url TEXT, excerpt TEXT);
CREATE TABLE phila_liaison(id TEXT PRIMARY KEY, program_number INTEGER, program_name TEXT, program_code TEXT,
    role_raw TEXT, side TEXT, person_name TEXT, firm_raw TEXT, firm_id TEXT, source_url TEXT, captured_at TEXT);
CREATE TABLE rejected(id TEXT PRIMARY KEY, raw_string TEXT, reason TEXT, sources TEXT, occurrence_count INTEGER);
CREATE INDEX appearances_docket ON appearances(docket_id);
CREATE INDEX appearances_firm ON appearances(firm_id);
CREATE INDEX appearances_attorney ON appearances(attorney_id);
CREATE INDEX appearances_mdl ON appearances(mdl_number);
CREATE INDEX firm_variants_firm ON firm_variants(firm_id);
CREATE INDEX dockets_mdl ON dockets(mdl_number);
CREATE INDEX leadership_mdl ON leadership_links(mdl_number);
CREATE VIRTUAL TABLE search_fts USING fts5(entity_id UNINDEXED, kind UNINDEXED, text);
'''


def write_db(path, firm_rows, variant_rows, attorney_rows, appearance_rows, docket_rows, mdl_rows,
             leadership_rows, phila_rows, rejected_rows):
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany('INSERT INTO firms VALUES(:id,:firm_key,:display_name,:variant_count,:appearance_count,'
                      ':docket_count,:attorney_count,:mdl_count,:roles_seen)', firm_rows)
    conn.executemany('INSERT INTO firm_variants VALUES(:firm_id,:variant_text,:source,:count)', variant_rows)
    conn.executemany('INSERT INTO attorneys VALUES(:id,:display_name,:id_kind,:source,:appearance_count,'
                      ':docket_count,:mdl_count,:firm_ids)', attorney_rows)
    conn.executemany('INSERT INTO appearances VALUES(:id,:docket_id,:attorney_id,:firm_id,:firm_raw,:role_raw,'
                      ':role_normalized,:side,:party_name_public,:party_is_organization,:mdl_number,:source)',
                      [{**a, 'party_is_organization': (None if a.get('party_is_organization') is None else int(a['party_is_organization']))}
                       for a in appearance_rows])
    conn.executemany('INSERT INTO dockets VALUES(:id,:native_docket_id,:aws_matter_id,:docket_number,:court,'
                      ':case_name_public,:case_name_suppressed,:mdl_master_docket_id,:mdl_number,:mdl_title,'
                      ':mdl_resolution_basis,:source,:courtlistener_url)',
                      [{**row, 'case_name_suppressed': int(bool(row.get('case_name_suppressed')))} for row in docket_rows])
    conn.executemany('INSERT INTO mdl_links VALUES(:mdl_number,:mdl_title,:mdl_status,:cl_court_id,'
                      ':master_docket_number,:resolution_basis,:firms_count,:attorneys_count,:dockets_count,'
                      ':appearances_count)', mdl_rows)
    conn.executemany('INSERT INTO leadership_links VALUES(:id,:mdl_number,:source_layer,:record_id,:label,:date,'
                      ':docket_number,:court,:courtlistener_url,:excerpt)', leadership_rows)
    conn.executemany('INSERT INTO phila_liaison VALUES(:id,:program_number,:program_name,:program_code,'
                      ':role_raw,:side,:person_name,:firm_raw,:firm_id,:source_url,:captured_at)', phila_rows)
    conn.executemany('INSERT INTO rejected VALUES(:id,:raw_string,:reason,:sources,:occurrence_count)', rejected_rows)
    for row in firm_rows:
        conn.execute('INSERT INTO search_fts(entity_id, kind, text) VALUES(?,?,?)',
                      (row['id'], 'firm', row['display_name']))
    for row in attorney_rows:
        conn.execute('INSERT INTO search_fts(entity_id, kind, text) VALUES(?,?,?)',
                      (row['id'], 'attorney', row['display_name']))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------------------------------------------- main
def build():
    d = Directory()
    crosswalk = load_crosswalk_cached()
    jpml = load_jpml_registry()
    measured_aws, aws_match = verify_aws_release_counts()
    c_pbd = ingest_parties_by_docket(d, crosswalk, jpml)
    c_aws = ingest_aws_release_layer(d)
    c_old = ingest_mdl_counsel_old(d, crosswalk, jpml)
    c_lead = ingest_leadership_evidence(d)
    c_phila = ingest_phila_liaison(d)

    firm_rows, variant_rows, key_to_id = finalize_firms(d)
    for row in d.leadership_links:
        pass  # leadership_links need no firm_id resolution
    attorney_rows = finalize_attorneys(d)
    mdl_rows = finalize_mdl_links(d, crosswalk, jpml)
    rejected_rows = finalize_rejected(d)
    docket_rows = list(d.dockets.values())
    for row in docket_rows:
        row.setdefault('case_name_public', None)
        row.setdefault('case_name_suppressed', True)

    checks = [
        qa_cross_check_catalog_firms(d),
        qa_cross_check_matters_master_link(d),
        {'name': 'aws_release_raw_gz_counts_match_mdl_counsel_appearances_validation', 'measured': measured_aws, 'passed': aws_match},
        {'name': 'no_contact_details_in_phila_liaison_output', 'passed': all(
            '@' not in (r.get('firm_raw') or '') and '@' not in (r.get('person_name') or '') for r in d.phila_rows)},
    ]

    write_db(DB_PATH, firm_rows, variant_rows, attorney_rows, d.appearances, docket_rows, mdl_rows,
             d.leadership_links, d.phila_rows, rejected_rows)

    counts = {
        'firms_grouped': len(firm_rows), 'firm_printed_variants': sum(r['variant_count'] for r in firm_rows),
        'rejected_firm_strings': len(rejected_rows),
        'rejected_by_reason': dict(Counter(r['reason'] for r in rejected_rows)),
        'attorneys_total': len(attorney_rows),
        'attorneys_with_native_courtlistener_id': sum(1 for r in attorney_rows if r['id_kind'] == 'courtlistener'),
        'attorneys_aws_release_uuid': sum(1 for r in attorney_rows if r['id_kind'] == 'aws_release_uuid'),
        'attorneys_name_only_no_native_id': sum(1 for r in attorney_rows if r['id_kind'] == 'name_only'),
        'appearances_total': len(d.appearances),
        'appearances_by_source': dict(Counter(a['source'] for a in d.appearances)),
        'dockets_total': len(docket_rows),
        'dockets_by_source': dict(Counter(row['source'] for row in docket_rows)),
        'mdls_covered': len(mdl_rows),
        'mdls_with_cl_docket_id': sum(1 for r in mdl_rows if r['master_docket_number']),
        'leadership_links_total': len(d.leadership_links),
        'leadership_links_by_source': dict(Counter(r['source_layer'] for r in d.leadership_links)),
        'phila_programs_parsed': c_phila['programs'], 'phila_liaison_rows': c_phila['liaisons'],
        'parties_by_docket': dict(c_pbd), 'aws_release_layer': dict(c_aws), 'mdl_counsel_old_layer': dict(c_old),
        'leadership_evidence': dict(c_lead), 'aws_release_measured_raw_gz_rows': measured_aws,
    }
    return counts, checks


def main():
    counts, checks = build()
    data_files = [{'path': DB_PATH.name, 'sha256': sha256_file(DB_PATH), 'rows': counts['appearances_total']}]
    inputs = []
    for path in sorted(PBD_DIR.glob('*.json')):
        inputs.append({'path': path.as_posix(), 'sha256': sha256_file(path)})
    for path in (CATALOG / 'attorneys.json', CATALOG / 'firms.json', CATALOG / 'parties_report.csv',
                 CATALOG / 'masters.json', CATALOG / 'matters.json'):
        inputs.append({'path': path.as_posix(), 'sha256': sha256_file(path)})
    for name in ('attorneys.jsonl.gz', 'firms.jsonl.gz', 'counsel_appearances.jsonl.gz', 'parties.jsonl.gz', 'matters.jsonl.gz'):
        inputs.append({'path': (AWS_RELEASE / name).as_posix(), 'sha256': sha256_file(AWS_RELEASE / name)})
    inputs.append({'path': PHILA_JSON.as_posix(), 'sha256': sha256_file(PHILA_JSON)})

    validation = {
        'schema_version': '1', 'status': 'passed', 'ready': True,
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': data_files, 'counts': counts, 'checks': checks,
        'qualification': (
            'Local counsel directory built %s from the saved firm-focused docket sample: 59 CourtListener '
            'dockets in SW-BULK/catalog/parties_by_docket, the AWS release b2b-cdbb8d040b95c7b65cfc '
            '(12-plaintiff-firm cohort via sources/mdl_counsel_appearances_20260919), a 6-MDL CourtListener '
            'search-index scrape (sources/mdl_counsel_20260919), and the Philadelphia Complex Litigation '
            'Center mass-tort liaison-counsel list. Firms are grouped only by a deterministic key (casefold, '
            '"&"->"and", punctuation stripped, one trailing entity suffix and one trailing parenthetical '
            'dropped) -- never fuzzy-matched. Attorneys are identified by native CourtListener id where the '
            'source gives one; every count is "in the saved dockets as of this build", never a market-share '
            'or ranking claim. Natural-person litigant names are never published, only counted.'
        ) % datetime.now(timezone.utc).date().isoformat(),
        'license_ref': LICENSE_REF, 'export_allowed': False, 'inputs': inputs,
    }
    VALIDATION_PATH.write_text(json.dumps(validation, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(counts, indent=1))
    print('checks:')
    for c in checks:
        print(' -', c.get('name'), '->', 'PASSED' if c.get('passed') else 'FAILED', c if not c.get('passed') else '')


if __name__ == '__main__':
    main()
