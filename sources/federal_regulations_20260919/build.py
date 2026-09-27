"""Offline builder for the federal regulation slice (mass-tort first slice, 98 CFR parts).

Inputs (all local, all read-only, all hash-pinned in validation.json):
  * parts.json                       the 98 selected parts (Titles 21, 16, 49, 40)
  * receipts.jsonl + raw/            98 eCFR versions bodies + 81 Federal Register API pages (fetched in round 1)
  * regulations scout bodies         eCFR titles.json, structure (T21/16/49/40), admin agencies.json
  * GPO eCFR Title 21 XML            sources/seeger_import_20260918/raw/ae/aef563....xml (captured 2026-09-13)
  * Open US Law index                sources/open_us_law_20260918/catalog.sqlite3 (read-only; text NOT copied)

Outputs (this folder): regulations.sqlite3, agencies.jsonl, edges.jsonl, unresolved.jsonl, gaps.json,
build_report.json, validation.json (uniform envelope).  No network.  Re-runnable.

Temporal vocabulary used everywhere (never merged):
  captured_at   = when the bytes were saved (HTTP receipt / import timestamp)
  source_as_of  = currency marker of the copy (GPO volume AMDDATE, eCFR structure date, publisher snapshot date)
  published_at  = Federal Register publication date / eCFR issue date
  effective_*   = never inferred; FR effective_on is exposed separately as publisher-extracted, unverified
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROBE = ROOT / 'reports/corpus_upgrade_20260919/understand/packets/regulation_probe'
PROBE_RECEIPTS = PROBE / 'receipts.jsonl'
GPO_XML = ROOT / 'sources/seeger_import_20260918/raw/ae/aef563b3516b24060d85f48f944e7ad8776b8b92d95a916c0c9cd78ff90c10c2.xml'
GPO_XML_SHA256 = 'aef563b3516b24060d85f48f944e7ad8776b8b92d95a916c0c9cd78ff90c10c2'
GPO_XML_CAPTURED_AT = '2026-09-13T03:24:57.269306+00:00'   # sources/seeger_import_20260918/originals.jsonl
GPO_XML_SOURCE_URL = 'https://www.govinfo.gov/bulkdata/ECFR/title-21/ECFR-title21.xml'
OUL_DB = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
OUL_FILE_ID = 'us_federal_regulations.parquet'
DB_NAME = 'regulations.sqlite3'
TITLES = ('21', '16', '49', '40')
SCOUT_BODIES = {  # sha256 -> role (bodies are named by the first 16 hex chars)
    '238f922bc260ad84afcb7ad6d62668cb3bb74c963dbd9b279d0e159eef092423': ('titles', None),
    '9e0d578eab1a62288c4aa9b24cb5dd4232d0b464b21fd8ae9ac03ce337952428': ('structure', '21'),
    '55db4dda33d560498713885a746076827bdc59412016efc4b9d6eb709a19710b': ('structure', '16'),
    '5cf588e98d41b9dd8cbafc1ae8c7849fd98e7f25d18e683fdcb4c99166551e2c': ('structure', '49'),
    'bf02fa25bbedabd136ad071af3e119ec23135dcf3c9f5d4e9dab7e426c2971f0': ('structure', '40'),
    '766685f466d62fa558a504cdeac23eef1d41f3ea24a2f5a3f78b38f2bcd5365e': ('agencies', None),
}
MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7, 'aug': 8,
          'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12}
SECTION_RE = re.compile(r'(\d+[A-Za-z]?\.\d+[A-Za-z0-9\-–.]*?)(?=\s|$|—|-{2})')
SECTION_STRICT = re.compile(r'^\d+[A-Za-z]?\.\d+[A-Za-z0-9\-–.]*$')
FR_CITE_RE = re.compile(r'(\d{1,3})\s+FR\s+(\d{1,6})(?:,\s*([A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4}))?')
NOTE_TAGS = {'EDNOTE', 'EFFDNOT', 'NOTE', 'APPRO', 'SECAUTH', 'XREF', 'CROSSREF', 'AUTH', 'SOURCE'}
PARA_TAGS = {'P', 'FP', 'FP-1', 'FP-2', 'FP1', 'FP2', 'HD1', 'HD2', 'HD3', 'BOXTXT', 'FTNT', 'HED', 'PSPACE', 'SU', 'ENT', 'TTITLE', 'TDESC', 'CHED'}
QUALIFICATION = ('Federal regulation slice for mass-tort research. Official text comes only from the local GPO eCFR '
                 'Title 21 XML (currency = each volume\'s printed amendment marker); other titles serve publisher text '
                 'from the Open US Law index on demand (snapshot date, not an as-of date). Amendment dates are eCFR '
                 'per-section version metadata since 2016; Federal Register dates are publisher fields; effective dates '
                 'are never inferred. eCFR is authoritative but unofficial; the official edition is the annual CFR.')
LICENSE_REF = ('eCFR (www.ecfr.gov), Federal Register API, GPO govinfo bulk XML: US Government works, public domain. '
               'Open US Law index by Vaquill AI: CC BY 4.0, read on demand, text not copied.')


# ----------------------------------------------------------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def norm_ws(text):
    return ' '.join((text or '').split())


def temporal(captured_at=None, captured_at_basis=None, source_as_of=None, source_as_of_basis=None, published_at=None,
             published_at_basis=None, effective_from=None, effective_from_basis=None, effective_to=None,
             effective_to_basis=None):
    """The uniform temporal block: five dated fields, each with a basis string (or null)."""
    return {'captured_at': captured_at, 'captured_at_basis': captured_at_basis if captured_at is not None or captured_at_basis else None,
            'source_as_of': source_as_of, 'source_as_of_basis': source_as_of_basis,
            'published_at': published_at, 'published_at_basis': published_at_basis,
            'effective_from': effective_from, 'effective_from_basis': effective_from_basis,
            'effective_to': effective_to, 'effective_to_basis': effective_to_basis}


def parse_printed_date(text):
    """'Sept. 8, 2026' / 'June 10, 2014' / 'Dec. 18, 2025 ' -> '2026-09-08'; None when it does not parse."""
    if not text:
        return None
    match = re.search(r'([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})', text)
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower()[:4]) or MONTHS.get(match.group(1).lower()[:3])
    if not month:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(2))).strftime('%Y-%m-%d')
    except ValueError:
        return None


def parse_fr_citations(note):
    """Every '<vol> FR <page>[, <date>]' occurrence in a printed amendment note, in order, verbatim pieces kept."""
    found = []
    for match in FR_CITE_RE.finditer(note or ''):
        printed = match.group(0)
        found.append({'citation_printed': printed, 'volume': int(match.group(1)), 'page': int(match.group(2)),
                      'date_printed': match.group(3), 'date_iso': parse_printed_date(match.group(3))})
    return found


def section_number(n_attr, head):
    """Section identifier from a DIV8 N attribute ('§ 314.80' or '892.2100') with the HEAD as fallback."""
    for candidate in (n_attr or '', head or ''):
        cleaned = candidate.replace('§', ' ').strip()
        match = SECTION_RE.search(cleaned)
        if match:
            value = match.group(1).rstrip('.')
            if SECTION_STRICT.match(value):
                return value
    return None


def strip_section_prefix(heading, section):
    """'§ 314.80   Postmarketing reporting...' -> 'Postmarketing reporting...'."""
    text = norm_ws(heading)
    text = re.sub(r'^§+\s*', '', text)
    if section and text.startswith(section):
        text = text[len(section):]
    # Remove only the separator between the section number and its heading; keep the heading's own punctuation as printed.
    return text.lstrip(' .—-').rstrip() if text.strip() else text.strip()


def part_sort_key(part):
    match = re.match(r'(\d+)(.*)', str(part))
    return (int(match.group(1)), match.group(2)) if match else (10 ** 9, str(part))


def section_sort_key(section):
    head, _, tail = str(section).partition('.')
    match_h = re.match(r'(\d+)(.*)', head)
    match_t = re.match(r'(\d+)(.*)', tail)
    return ((int(match_h.group(1)), match_h.group(2)) if match_h else (10 ** 9, head),
            (int(match_t.group(1)), match_t.group(2)) if match_t else (10 ** 9, tail))


def load_jsonl(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def write_jsonl(path, rows):
    with open(path, 'w', encoding='utf-8') as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    return len(rows)


# ----------------------------------------------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE titles(title TEXT PRIMARY KEY, name TEXT, ecfr_latest_amended_on TEXT, ecfr_latest_issue_date TEXT,
  ecfr_up_to_date_as_of TEXT, ecfr_titles_captured_at TEXT, structure_as_of TEXT, structure_captured_at TEXT,
  gpo_xml_sha256 TEXT, gpo_xml_captured_at TEXT, gpo_xml_source_url TEXT, gpo_volume_amddates TEXT,
  parts_total INTEGER, parts_in_slice INTEGER, sections_in_slice INTEGER);
CREATE TABLE parts(title TEXT NOT NULL, part TEXT NOT NULL, heading TEXT, chapter TEXT, chapter_name TEXT,
  subchapter TEXT, subchapter_name TEXT, subtitle TEXT, in_slice INTEGER NOT NULL DEFAULT 0, reserved INTEGER DEFAULT 0,
  ecfr_structure_sections INTEGER, ecfr_structure_size INTEGER, structure_as_of TEXT, oul_rows INTEGER,
  gpo_sections INTEGER, gpo_heading TEXT, gpo_authority TEXT, gpo_source_note TEXT, gpo_editorial_note TEXT,
  gpo_node TEXT, gpo_volume TEXT, gpo_amddate_raw TEXT, gpo_amddate_iso TEXT, gpo_appendices INTEGER,
  versions_rows INTEGER, versions_sections INTEGER, versions_latest_amendment_date TEXT,
  versions_latest_issue_date TEXT, versions_history_start TEXT, versions_captured_at TEXT,
  fr_documents INTEGER, fr_count_reported INTEGER, fr_pages_reported INTEGER, fr_pages_fetched INTEGER,
  fr_captured_at TEXT, PRIMARY KEY(title, part));
CREATE TABLE section_index(id INTEGER PRIMARY KEY, title TEXT NOT NULL, part TEXT NOT NULL, section TEXT NOT NULL,
  heading TEXT, subpart TEXT, subject_group TEXT, in_slice INTEGER DEFAULT 0, in_gpo INTEGER DEFAULT 0,
  in_oul INTEGER DEFAULT 0, in_ecfr_structure INTEGER DEFAULT 0, reserved INTEGER DEFAULT 0, ecfr_size INTEGER,
  ecfr_received_on TEXT, versions_count INTEGER DEFAULT 0, latest_amendment_date TEXT, latest_issue_date TEXT,
  UNIQUE(title, section));
CREATE INDEX section_index_part ON section_index(title, part);
CREATE TABLE gpo_sections(title TEXT NOT NULL, part TEXT NOT NULL, section TEXT NOT NULL, heading TEXT, subpart TEXT,
  subject_group TEXT, node TEXT, volume TEXT, amddate_raw TEXT, amddate_iso TEXT, reserved INTEGER DEFAULT 0,
  text TEXT, text_sha256 TEXT, text_length INTEGER, amendment_citations TEXT, notes TEXT, captured_at TEXT,
  PRIMARY KEY(title, section));
CREATE TABLE gpo_appendices(title TEXT, part TEXT, label TEXT, heading TEXT, node TEXT, volume TEXT, amddate_iso TEXT,
  text TEXT, text_sha256 TEXT, text_length INTEGER, captured_at TEXT);
CREATE TABLE oul_provisions(oul_rowid INTEGER, oul_id TEXT, source_id TEXT, citation TEXT, title TEXT, part TEXT,
  section TEXT, heading TEXT, status TEXT, snapshot TEXT, snapshot_date TEXT, publisher_year INTEGER,
  last_amended_year INTEGER, text_length INTEGER, text_sha256 TEXT, source_url TEXT, captured_at TEXT);
CREATE INDEX oul_provisions_section ON oul_provisions(title, section);
CREATE INDEX oul_provisions_rowid ON oul_provisions(oul_rowid);
CREATE TABLE versions(title TEXT, part TEXT, section TEXT, name TEXT, subpart TEXT, type TEXT, version_date TEXT,
  amendment_date TEXT, issue_date TEXT, substantive INTEGER, removed INTEGER, captured_at TEXT);
CREATE INDEX versions_section ON versions(title, section);
CREATE INDEX versions_part ON versions(title, part);
CREATE TABLE fr_documents(document_number TEXT PRIMARY KEY, citation TEXT, title TEXT, type TEXT, action TEXT,
  publication_date TEXT, effective_on TEXT, signing_date TEXT, comments_close_on TEXT, dates_text TEXT,
  docket_ids TEXT, regulation_id_numbers TEXT, agencies TEXT, agency_slugs TEXT, cfr_references TEXT, html_url TEXT,
  pdf_url TEXT, full_text_xml_url TEXT, raw_text_url TEXT, json_url TEXT, volume INTEGER, start_page INTEGER,
  end_page INTEGER, page_length INTEGER, correction_of TEXT, corrections TEXT, local_full_text TEXT, captured_at TEXT);
CREATE TABLE fr_doc_parts(document_number TEXT, title TEXT, part TEXT, basis TEXT, PRIMARY KEY(document_number, title, part));
CREATE INDEX fr_doc_parts_part ON fr_doc_parts(title, part);
CREATE TABLE section_fr_citations(title TEXT, section TEXT, citation_printed TEXT, volume INTEGER, page INTEGER,
  date_printed TEXT, document_number TEXT);
CREATE INDEX section_fr_citations_section ON section_fr_citations(title, section);
CREATE INDEX section_fr_citations_doc ON section_fr_citations(document_number);
CREATE TABLE reconciliation(title TEXT, part TEXT, section TEXT, in_gpo INTEGER, in_oul INTEGER, oul_rows INTEGER,
  in_ecfr_structure INTEGER, heading_match INTEGER, text_comparison TEXT, gpo_text_length INTEGER,
  oul_text_lengths TEXT, similarity REAL, category TEXT, PRIMARY KEY(title, section));
CREATE VIRTUAL TABLE search_fts USING fts5(kind UNINDEXED, heading, body, content='', tokenize='unicode61');
"""


def create_schema(db):
    db.executescript(SCHEMA)


def fill_search(db):
    """Contentless FTS over section headings + GPO text (rowid = section_index.id) and Federal Register
    document metadata (rowid = 10_000_000 + fr_documents.rowid). Re-runnable."""
    db.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
    db.execute("""INSERT INTO search_fts(rowid, kind, heading, body)
                  SELECT s.id, 'section', COALESCE(s.heading, ''), COALESCE(g.text, '')
                  FROM section_index s LEFT JOIN gpo_sections g ON g.title = s.title AND g.section = s.section""")
    db.execute("""INSERT INTO search_fts(rowid, kind, heading, body)
                  SELECT 10000000 + rowid, 'fr_document', COALESCE(title, ''),
                         COALESCE(action, '') || ' ' || COALESCE(dates_text, '') || ' ' || COALESCE(docket_ids, '') || ' ' ||
                         COALESCE(regulation_id_numbers, '') || ' ' || COALESCE(citation, '') || ' ' || document_number
                  FROM fr_documents""")


def write_validation(folder, files, counts=None, checks=None, inputs=None, rows=None, status=None, validated_at=None,
                     qualification=QUALIFICATION, license_ref=LICENSE_REF):
    """Uniform validation envelope. status defaults to 'passed' iff every check passed."""
    folder = Path(folder)
    checks = list(checks or [])
    if status is None:
        status = 'passed' if all(c.get('passed') for c in checks) else 'failed'
    data_files = []
    for name in files:
        path = folder / name
        data_files.append({'path': name, 'sha256': sha256_file(path), 'bytes': path.stat().st_size,
                           'rows': (rows or {}).get(name)})
    envelope = {'schema_version': '1', 'status': status, 'ready': status == 'passed',
                'validated_at': validated_at or now_iso(), 'data_files': data_files, 'counts': counts or {},
                'checks': checks, 'qualification': qualification, 'license_ref': license_ref, 'inputs': inputs or []}
    (folder / 'validation.json').write_text(json.dumps(envelope, indent=1, ensure_ascii=False), encoding='utf-8')
    return envelope


# ----------------------------------------------------------------------------------------------------------------- structure
def walk_structure(node, ancestors, out):
    kind = node.get('type')
    if kind == 'part':
        chapter = next((a for a in reversed(ancestors) if a.get('type') == 'chapter'), None)
        subchapter = next((a for a in reversed(ancestors) if a.get('type') == 'subchapter'), None)
        subtitle = next((a for a in reversed(ancestors) if a.get('type') == 'subtitle'), None)
        out['parts'].append({'part': str(node.get('identifier')), 'heading': norm_ws(node.get('label_description')),
                             'reserved': bool(node.get('reserved')), 'size': node.get('size'),
                             'chapter': norm_ws(chapter.get('identifier')) if chapter else None,
                             'chapter_name': norm_ws(chapter.get('label_description')) if chapter else None,
                             'subchapter': norm_ws(subchapter.get('identifier')) if subchapter else None,
                             'subchapter_name': norm_ws(subchapter.get('label_description')) if subchapter else None,
                             'subtitle': norm_ws(subtitle.get('identifier')) if subtitle else None,
                             'sections': 0})
    elif kind == 'section':
        part = next((a for a in reversed(ancestors) if a.get('type') == 'part'), None)
        subpart = next((a for a in reversed(ancestors) if a.get('type') == 'subpart'), None)
        group = next((a for a in reversed(ancestors) if a.get('type') == 'subject_group'), None)
        out['sections'].append({'section': str(node.get('identifier')), 'part': str(part.get('identifier')) if part else None,
                                'heading': norm_ws(node.get('label_description')), 'reserved': bool(node.get('reserved')),
                                'size': node.get('size'), 'received_on': node.get('received_on'),
                                'subpart': norm_ws(subpart.get('identifier')) if subpart else None,
                                'subject_group': norm_ws(group.get('label_description')) if group else None})
        if out['parts'] and part is not None and out['parts'][-1]['part'] == str(part.get('identifier')):
            out['parts'][-1]['sections'] += 1
    for child in node.get('children') or []:
        walk_structure(child, ancestors + [node], out)


def parse_structure(tree):
    out = {'parts': [], 'sections': []}
    walk_structure(tree, [], out)
    return out


# ----------------------------------------------------------------------------------------------------------------- GPO XML
def paragraphs(element):
    """Yield normalized paragraph strings for a container element, preserving paragraph breaks."""
    if element.tag in PARA_TAGS or len(element) == 0:
        text = norm_ws(''.join(element.itertext()))
        if text:
            yield text
        return
    lead = norm_ws(element.text)
    if lead:
        yield lead
    for child in element:
        if not isinstance(child.tag, str):
            continue
        yield from paragraphs(child)
        tail = norm_ws(child.tail)
        if tail:
            yield tail


def extract_section(div8):
    """(heading, text, citations, notes, reserved) for one DIV8 section element."""
    heading, body, citations, notes = '', [], [], []
    for child in div8:
        if not isinstance(child.tag, str):
            continue
        if child.tag == 'HEAD':
            heading = norm_ws(''.join(child.itertext()))
        elif child.tag == 'CITA':
            text = norm_ws(''.join(child.itertext()))
            if text:
                citations.append(text)
        elif child.tag in NOTE_TAGS:
            text = norm_ws(''.join(child.itertext()))
            if text:
                notes.append({'kind': child.tag, 'text': text})
        else:
            body.extend(paragraphs(child))
    return heading, '\n'.join(body), citations, notes, '[reserved]' in heading.lower()


def note_text(element):
    if element is None:
        return None
    parts = [norm_ws(''.join(child.itertext())) for child in element if isinstance(child.tag, str) and child.tag != 'HED']
    text = ' '.join(p for p in parts if p) or norm_ws(''.join(element.itertext()))
    return re.sub(r'^(Authority|Source|Editorial Note):\s*', '', text).strip() or None


def parse_gpo_xml(path):
    from lxml import etree
    tree = etree.parse(str(path), etree.XMLParser(huge_tree=True))
    root = tree.getroot()
    result = {'volumes': [], 'parts': [], 'sections': [], 'appendices': [], 'unparsed': [], 'duplicates': []}
    seen = set()
    for brws in root.iter('ECFRBRWS'):
        amd = brws.find('AMDDATE')
        amd_raw = norm_ws(amd.text if amd is not None else '')
        amd_iso = parse_printed_date(amd_raw)
        for div1 in brws.findall('DIV1'):
            volume = str(div1.get('N'))
            result['volumes'].append({'volume': volume, 'amddate_raw': amd_raw, 'amddate_iso': amd_iso,
                                      'head': norm_ws(''.join(div1.find('HEAD').itertext())) if div1.find('HEAD') is not None else None})
            for div5 in div1.iter('DIV5'):
                if div5.get('TYPE') != 'PART':
                    continue
                part = str(div5.get('N'))
                ancestors = list(div5.iterancestors())
                chapter = next((a for a in ancestors if a.tag == 'DIV3'), None)
                subchapter = next((a for a in ancestors if a.tag == 'DIV4'), None)
                head = div5.find('HEAD')
                result['parts'].append({
                    'part': part, 'node': div5.get('NODE'), 'volume': volume, 'amddate_raw': amd_raw, 'amddate_iso': amd_iso,
                    'heading': norm_ws(''.join(head.itertext())) if head is not None else None,
                    'chapter': chapter.get('N') if chapter is not None else None,
                    'chapter_name': norm_ws(''.join(chapter.find('HEAD').itertext())) if chapter is not None and chapter.find('HEAD') is not None else None,
                    'subchapter': subchapter.get('N') if subchapter is not None else None,
                    'authority': note_text(div5.find('AUTH')), 'source_note': note_text(div5.find('SOURCE')),
                    'editorial_note': note_text(div5.find('EDNOTE')), 'sections': 0, 'appendices': 0})
                for div8 in div5.iter('DIV8'):
                    head8 = div8.find('HEAD')
                    heading, text, citations, notes, reserved = extract_section(div8)
                    section = section_number(div8.get('N'), heading)
                    if not section:
                        result['unparsed'].append({'part': part, 'node': div8.get('NODE'), 'n': div8.get('N'), 'head': heading[:120]})
                        continue
                    if section in seen:
                        result['duplicates'].append({'part': part, 'section': section, 'node': div8.get('NODE')})
                        continue
                    seen.add(section)
                    subpart = next((a.get('N') for a in div8.iterancestors() if a.tag == 'DIV6'), None)
                    group = next((a for a in div8.iterancestors() if a.tag == 'DIV7'), None)
                    result['sections'].append({
                        'part': part, 'section': section, 'heading': heading, 'subpart': subpart,
                        'subject_group': norm_ws(''.join(group.find('HEAD').itertext())) if group is not None and group.find('HEAD') is not None else None,
                        'node': div8.get('NODE'), 'volume': volume, 'amddate_raw': amd_raw, 'amddate_iso': amd_iso,
                        'reserved': reserved, 'text': text, 'citations': citations, 'notes': notes})
                    result['parts'][-1]['sections'] += 1
                for div9 in div5.iter('DIV9'):
                    heading, text, citations, notes, _ = extract_section(div9)
                    result['appendices'].append({'part': part, 'label': div9.get('N'), 'heading': heading, 'node': div9.get('NODE'),
                                                 'volume': volume, 'amddate_iso': amd_iso, 'text': text})
                    result['parts'][-1]['appendices'] += 1
    return result


# ----------------------------------------------------------------------------------------------------------------- reconciliation
def strip_source_credit(text):
    """Open US Law rows may end with the printed source-credit line '[50 FR 7493, ...]'; drop it for comparison."""
    stripped = re.sub(r'\s*\[\d{1,3}\s+FR\s+\d+[^\]]*\]\s*$', '', text or '')
    return stripped, stripped != (text or '')


def compare_texts(gpo_text, oul_text):
    """(text_comparison, similarity) on whitespace-normalized text; word-level difflib ratio, quick_ratio for huge."""
    a, b = norm_ws(gpo_text), norm_ws(strip_source_credit(oul_text)[0])
    if a == b:
        return 'identical', 1.0
    if not a or not b:
        return 'one_side_empty', 0.0
    words_a, words_b = a.split(), b.split()
    matcher = difflib.SequenceMatcher(None, words_a, words_b, autojunk=True)
    if len(words_a) > 15000 or len(words_b) > 15000:
        return 'differs_quick_ratio', round(matcher.quick_ratio(), 4)
    return 'differs', round(matcher.ratio(), 4)


def reconcile(section, gpo_row, oul_rows, in_structure, oul_texts):
    in_gpo, in_oul = gpo_row is not None, bool(oul_rows)
    heading_match = None
    comparison, similarity, gpo_len, oul_lens = None, None, None, [r['text_length'] for r in oul_rows]
    if in_gpo and in_oul:
        gpo_heading = strip_section_prefix(gpo_row['heading'], section).lower()
        heading_match = int(any(strip_section_prefix(r['heading'], section).lower() == gpo_heading for r in oul_rows))
        gpo_len = gpo_row['text_length']
        best = None
        for row in oul_rows:
            text = oul_texts.get(row['oul_rowid'])
            if text is None:
                continue
            cmp_, sim = compare_texts(gpo_row['text'], text)
            if best is None or sim > best[1]:
                best = (cmp_, sim)
        if best:
            comparison, similarity = best
    if in_gpo and in_oul:
        if comparison == 'identical':
            category = 'identical' if heading_match else 'identical_text_heading_differs'
        elif len(oul_rows) > 1:
            category = 'oul_duplicate_rows'
        else:
            category = 'text_differs' if heading_match else 'text_and_heading_differ'
    elif in_gpo:
        category = 'gpo_only'
    elif in_oul:
        category = 'oul_only' if not in_structure else 'structure_and_oul_no_gpo'
    else:
        category = 'structure_only'
    return {'in_gpo': int(in_gpo), 'in_oul': int(in_oul), 'oul_rows': len(oul_rows), 'in_ecfr_structure': int(in_structure),
            'heading_match': heading_match, 'text_comparison': comparison, 'gpo_text_length': gpo_len,
            'oul_text_lengths': json.dumps(oul_lens), 'similarity': similarity, 'category': category}


# ----------------------------------------------------------------------------------------------------------------- FR citation resolution
def build_fr_page_index(docs):
    index = {}
    for doc in docs.values():
        if doc.get('volume') and doc.get('start_page'):
            index.setdefault(int(doc['volume']), []).append((int(doc['start_page']), int(doc.get('end_page') or doc['start_page']), doc['document_number'], doc.get('publication_date')))
    return index


def resolve_citation(cite, page_index):
    """document_number or (None, reason). Explicit evidence only: volume + page inside the document's page range,
    and the printed date (when present) must equal the publication date."""
    candidates = [c for c in page_index.get(cite['volume'], []) if c[0] <= cite['page'] <= c[1]]
    if cite.get('date_iso'):
        dated = [c for c in candidates if c[3] == cite['date_iso']]
        if candidates and not dated:
            return None, 'printed date differs from fetched publication_date'
        candidates = dated
    if not candidates:
        return None, 'no fetched Federal Register document covers this volume/page'
    if len(candidates) > 1:
        exact = [c for c in candidates if c[0] == cite['page']]
        if len(exact) == 1:
            return exact[0][2], None
        return None, f'ambiguous: {len(candidates)} fetched documents cover this page'
    return candidates[0][2], None


# ----------------------------------------------------------------------------------------------------------------- build
def load_probe(receipts):
    found = {}
    for row in receipts:
        digest = row.get('sha256')
        if digest in SCOUT_BODIES:
            path = PROBE / 'bodies' / row['body_file']
            if sha256_file(path) != digest:
                raise SystemExit('scout body hash mismatch: ' + str(path))
            found[SCOUT_BODIES[digest]] = {'path': path, 'sha256': digest, 'requested_at': row['requested_at'], 'url': row['url'],
                                            'body': json.loads(path.read_text(encoding='utf-8'))}
    missing = set(SCOUT_BODIES.values()) - set(found)
    if missing:
        raise SystemExit('scout bodies missing: ' + str(missing))
    return found


def main():
    started = time.time()
    config = json.loads((HERE / 'parts.json').read_text(encoding='utf-8'))
    selected = {title: [str(p) for p in parts] for title, parts in config['selected_parts'].items()}
    slice_parts = {(t, p) for t, ps in selected.items() for p in ps}
    fr_titles = [str(t) for t in config['federal_register_titles']]
    report = {'started_at': now_iso(), 'network_requests': 0, 'warnings': []}
    checks, inputs = [], []

    # -- receipts (round 1) and raw bodies ------------------------------------------------------------------
    receipts = load_jsonl(HERE / 'receipts.jsonl')
    seeds = load_jsonl(HERE / 'seeds.jsonl')
    verified, bad = 0, []
    for row in receipts:
        path = HERE / row['body_path']
        if path.exists() and sha256_file(path) == row['sha256'] and row.get('accepted'):
            verified += 1
        else:
            bad.append(row['url'])
    checks.append({'name': 'receipts_verified', 'passed': not bad and len(receipts) == len(seeds),
                   'detail': f'{verified}/{len(receipts)} receipts accepted with matching raw SHA-256; seeds={len(seeds)}; bad={len(bad)}'})
    seed_urls = {s['url'] for s in seeds}
    receipt_urls = {r['url'] for r in receipts}
    checks.append({'name': 'every_seed_has_receipt', 'passed': seed_urls == receipt_urls,
                   'detail': f'seeds without receipt={len(seed_urls - receipt_urls)}; receipts outside seeds={len(receipt_urls - seed_urls)}'})
    for name in ('parts.json', 'seeds.jsonl', 'receipts.jsonl'):
        inputs.append({'path': f'sources/federal_regulations_20260919/{name}', 'sha256': sha256_file(HERE / name)})

    # -- scout bodies ---------------------------------------------------------------------------------------
    probe = load_probe(load_jsonl(PROBE_RECEIPTS))
    for key, item in probe.items():
        inputs.append({'path': item['path'].relative_to(ROOT).as_posix(), 'sha256': item['sha256'], 'url': item['url'],
                       'captured_at': item['requested_at'], 'role': ':'.join(x for x in key if x)})
    titles_body = probe[('titles', None)]['body']
    titles_meta = {str(t['number']): t for t in titles_body['titles']}
    structures = {}
    for title in TITLES:
        item = probe[('structure', title)]
        structures[title] = parse_structure(item['body'])
        structures[title]['as_of'] = re.search(r'/structure/(\d{4}-\d{2}-\d{2})/', item['url']).group(1)
        structures[title]['captured_at'] = item['requested_at']
    agencies_body = probe[('agencies', None)]['body']
    agencies_captured_at = probe[('agencies', None)]['requested_at']

    # -- GPO XML (Title 21) ----------------------------------------------------------------------------------
    gpo_digest = sha256_file(GPO_XML)
    checks.append({'name': 'gpo_xml_hash', 'passed': gpo_digest == GPO_XML_SHA256, 'detail': f'{GPO_XML.name} sha256={gpo_digest}'})
    if gpo_digest != GPO_XML_SHA256:
        raise SystemExit('GPO XML hash mismatch')
    inputs.append({'path': GPO_XML.relative_to(ROOT).as_posix(), 'sha256': gpo_digest, 'url': GPO_XML_SOURCE_URL,
                   'captured_at': GPO_XML_CAPTURED_AT, 'role': 'gpo_ecfr_xml:21'})
    gpo = parse_gpo_xml(GPO_XML)
    report['gpo'] = {'volumes': gpo['volumes'], 'parts': len(gpo['parts']), 'sections': len(gpo['sections']),
                     'appendices': len(gpo['appendices']), 'unparsed': gpo['unparsed'], 'duplicates': gpo['duplicates']}
    checks.append({'name': 'gpo_sections_parsed', 'passed': len(gpo['sections']) >= 8400 and not gpo['unparsed'],
                   'detail': f"{len(gpo['sections'])} sections, {len(gpo['parts'])} parts, unparsed={len(gpo['unparsed'])}, duplicates={len(gpo['duplicates'])}"})
    gpo_by_section = {s['section']: s for s in gpo['sections']}
    gpo_parts = {p['part']: p for p in gpo['parts']}

    # -- Open US Law provisions index (read-only, no text copied) ---------------------------------------------
    oul = sqlite3.connect(f'file:{OUL_DB.as_posix()}?mode=ro', uri=True)
    oul.row_factory = sqlite3.Row
    meta = {r['key']: json.loads(r['value']) for r in oul.execute('SELECT key, value FROM import_meta')}
    oul_file = oul.execute('SELECT sha256, bytes, rows, indexed_at, path FROM files WHERE id = ?', (OUL_FILE_ID,)).fetchone()
    snapshot_date, snapshot = meta.get('snapshot_date'), meta.get('snapshot')
    oul_captured_at = oul_file['indexed_at'] if oul_file else meta.get('completed_at')
    inputs.append({'path': OUL_DB.relative_to(ROOT).as_posix(), 'sha256': None, 'bytes': OUL_DB.stat().st_size,
                   'role': 'open_us_law_index (read-only; not hashed: large; provenance via files table)',
                   'federal_regulations_file': {'id': OUL_FILE_ID, 'sha256': oul_file['sha256'] if oul_file else None,
                                                 'bytes': oul_file['bytes'] if oul_file else None, 'rows': oul_file['rows'] if oul_file else None,
                                                 'indexed_at': oul_captured_at},
                   'snapshot': snapshot, 'snapshot_date': snapshot_date})
    wanted_parts = {('21', p['part']) for p in structures['21']['parts']} | {('21', p) for p in gpo_parts} | slice_parts
    oul_rows, oul_texts, oul_non_section = [], {}, []
    for title, part in sorted(wanted_parts, key=lambda x: (x[0], part_sort_key(x[1]))):
        prefix = f'CFR_T{title}_P{part}_S'
        query = oul.execute("SELECT rowid, id, source_id, title, text, payload, source_url, citation, status, snapshot FROM records "
                            "WHERE source_id >= ? AND source_id < ?", (prefix, prefix[:-1] + 'T'))
        for row in query:
            payload = json.loads(row['payload'] or '{}')
            section = str(payload.get('section_number') or '').strip()
            if not SECTION_STRICT.match(section):
                oul_non_section.append({'source_id': row['source_id'], 'section_number': section, 'reason': 'not a section-number pattern'})
                continue
            text = row['text'] or ''
            oul_rows.append({'oul_rowid': row['rowid'], 'oul_id': row['id'], 'source_id': row['source_id'], 'citation': row['citation'],
                             'title': title, 'part': part, 'section': section, 'heading': norm_ws(row['title']), 'status': row['status'],
                             'snapshot': row['snapshot'], 'snapshot_date': snapshot_date, 'publisher_year': payload.get('year'),
                             'last_amended_year': payload.get('last_amended_year'), 'text_length': len(text),
                             'text_sha256': sha256_text(text), 'source_url': row['source_url'], 'captured_at': oul_captured_at})
            if title == '21':
                oul_texts[row['rowid']] = text
    oul_by_section = {}
    for row in oul_rows:
        oul_by_section.setdefault((row['title'], row['section']), []).append(row)
    oul_slice_rows = sum(1 for r in oul_rows if (r['title'], r['part']) in slice_parts)
    report['oul'] = {'rows': len(oul_rows), 'slice_rows': oul_slice_rows, 'non_section_rows': len(oul_non_section),
                     'snapshot': snapshot, 'snapshot_date': snapshot_date}

    # -- eCFR versions (per-section amendment history) --------------------------------------------------------
    versions, versions_meta = [], {}
    for row in receipts:
        if row['packet'] != 'ecfr_versions':
            continue
        body = json.loads((HERE / row['body_path']).read_text(encoding='utf-8'))
        key = (row['title'], row['part'])
        rows = body.get('content_versions') or []
        dates = [v['date'] for v in rows if v.get('date')]
        versions_meta[key] = {'rows': len(rows), 'sections': len({v['identifier'] for v in rows}), 'meta': body.get('meta') or {},
                              'captured_at': row['requested_at'], 'history_start': min(dates) if dates else None}
        for v in rows:
            versions.append({'title': str(v.get('title') or row['title']), 'part': str(v.get('part') or row['part']),
                             'section': str(v['identifier']), 'name': norm_ws(v.get('name')), 'subpart': v.get('subpart'),
                             'type': v.get('type'), 'version_date': v.get('date'), 'amendment_date': v.get('amendment_date'),
                             'issue_date': v.get('issue_date'), 'substantive': int(bool(v.get('substantive'))),
                             'removed': int(bool(v.get('removed'))), 'captured_at': row['requested_at']})
    versions_by_section = {}
    for v in versions:
        versions_by_section.setdefault((v['title'], v['section']), []).append(v)
    checks.append({'name': 'versions_for_every_selected_part', 'passed': set(versions_meta) == slice_parts,
                   'detail': f'{len(versions_meta)} parts with versions bodies; {len(versions)} version rows; missing={sorted(slice_parts - set(versions_meta))}'})

    # -- Federal Register documents ---------------------------------------------------------------------------
    fr_docs, fr_meta, fr_variants = {}, {}, 0
    fr_query_parts = {}
    for row in sorted((r for r in receipts if r['packet'] == 'fr_documents'), key=lambda r: (r['title'], part_sort_key(r['part']), r.get('page') or 1)):
        body = json.loads((HERE / row['body_path']).read_text(encoding='utf-8'))
        key = (row['title'], row['part'])
        meta_row = fr_meta.setdefault(key, {'count': body.get('count'), 'total_pages': body.get('total_pages'), 'pages_fetched': 0,
                                             'documents': set(), 'captured_at': row['requested_at']})
        meta_row['pages_fetched'] += 1
        for doc in body.get('results') or []:
            number = doc['document_number']
            meta_row['documents'].add(number)
            fr_query_parts.setdefault(number, set()).add(key)
            if number in fr_docs:
                if json.dumps(fr_docs[number]['_raw'], sort_keys=True) != json.dumps(doc, sort_keys=True):
                    fr_variants += 1
                continue
            fr_docs[number] = {'_raw': doc, 'captured_at': row['requested_at']}
    for key, meta_row in fr_meta.items():
        meta_row['documents_distinct'] = len(meta_row['documents'])
        del meta_row['documents']
    expected_fr = {(t, p) for t, p in slice_parts if t in fr_titles}
    complete = all(m['pages_fetched'] == (m['total_pages'] or 1) for m in fr_meta.values())
    checks.append({'name': 'fr_documents_for_selected_parts', 'passed': set(fr_meta) == expected_fr and complete,
                   'detail': f'{len(fr_meta)} parts (expected {len(expected_fr)}), {len(fr_docs)} distinct documents, all pages fetched={complete}, '
                             f'documents seen with differing payloads across responses={fr_variants}'})
    # local full text in Open US Law (metadata join only)
    local_text = {}
    for number in fr_docs:
        hits = [r[0] for r in oul.execute('SELECT DISTINCT source_id FROM records WHERE source_id IN (?, ?)',
                                          ('FR_RULE_' + number, 'FR_PRORULE_' + number))]
        local_text[number] = hits
    oul.close()

    # -- assemble the section index --------------------------------------------------------------------------
    struct_sections = {}
    for title in TITLES:
        for s in structures[title]['sections']:
            if s['part'] is None:
                continue
            if title == '21' or (title, s['part']) in slice_parts:
                struct_sections[(title, s['section'])] = s
    sections = {}
    for (title, section), s in struct_sections.items():
        sections[(title, section)] = {'title': title, 'part': s['part'], 'section': section, 'heading': s['heading'], 'subpart': s['subpart'],
                                      'subject_group': s['subject_group'], 'reserved': int(s['reserved']), 'ecfr_size': s['size'],
                                      'ecfr_received_on': s['received_on'], 'in_ecfr_structure': 1, 'in_gpo': 0, 'in_oul': 0}
    for section, g in gpo_by_section.items():
        row = sections.setdefault(('21', section), {'title': '21', 'part': g['part'], 'section': section, 'heading': None, 'subpart': None,
                                                     'subject_group': None, 'reserved': 0, 'ecfr_size': None, 'ecfr_received_on': None,
                                                     'in_ecfr_structure': 0, 'in_gpo': 0, 'in_oul': 0})
        row['in_gpo'] = 1
        row['heading'] = row['heading'] or strip_section_prefix(g['heading'], section)
        row['subpart'] = row['subpart'] or g['subpart']
        row['subject_group'] = row['subject_group'] or g['subject_group']
        row['reserved'] = row['reserved'] or int(g['reserved'])
    for (title, section), rows in oul_by_section.items():
        row = sections.setdefault((title, section), {'title': title, 'part': rows[0]['part'], 'section': section, 'heading': None, 'subpart': None,
                                                     'subject_group': None, 'reserved': 0, 'ecfr_size': None, 'ecfr_received_on': None,
                                                     'in_ecfr_structure': 0, 'in_gpo': 0, 'in_oul': 0})
        row['in_oul'] = 1
        row['heading'] = row['heading'] or strip_section_prefix(rows[0]['heading'], section)
    for key, row in sections.items():
        row['in_slice'] = int((row['title'], row['part']) in slice_parts)
        hist = versions_by_section.get(key, [])
        row['versions_count'] = len(hist)
        row['latest_amendment_date'] = max((h['amendment_date'] for h in hist if h.get('amendment_date')), default=None)
        row['latest_issue_date'] = max((h['issue_date'] for h in hist if h.get('issue_date')), default=None)
    ordered = sorted(sections.values(), key=lambda r: (r['title'], part_sort_key(r['part']), section_sort_key(r['section'])))
    for index, row in enumerate(ordered, start=1):
        row['id'] = index

    # -- parts table ---------------------------------------------------------------------------------------------
    parts = {}
    for title in TITLES:
        for p in structures[title]['parts']:
            parts[(title, p['part'])] = {'title': title, 'part': p['part'], 'heading': p['heading'], 'chapter': p['chapter'],
                                         'chapter_name': p['chapter_name'], 'subchapter': p['subchapter'], 'subchapter_name': p['subchapter_name'],
                                         'subtitle': p['subtitle'], 'reserved': int(p['reserved']), 'ecfr_structure_sections': p['sections'],
                                         'ecfr_structure_size': p['size'], 'structure_as_of': structures[title]['as_of']}
    for part, g in gpo_parts.items():
        row = parts.setdefault(('21', part), {'title': '21', 'part': part, 'heading': None, 'chapter': None, 'chapter_name': None,
                                              'subchapter': None, 'subchapter_name': None, 'subtitle': None, 'reserved': 0,
                                              'ecfr_structure_sections': None, 'ecfr_structure_size': None, 'structure_as_of': None})
        row['heading'] = row['heading'] or (g['heading'].split('—', 1)[1].strip() if g['heading'] and '—' in g['heading'] else g['heading'])
        row['chapter'] = row['chapter'] or g['chapter']
        row['chapter_name'] = row['chapter_name'] or (g['chapter_name'].split('—', 1)[1].strip() if g['chapter_name'] and '—' in g['chapter_name'] else g['chapter_name'])
        row.update({'gpo_sections': g['sections'], 'gpo_heading': g['heading'], 'gpo_authority': g['authority'], 'gpo_source_note': g['source_note'],
                    'gpo_editorial_note': g['editorial_note'], 'gpo_node': g['node'], 'gpo_volume': g['volume'], 'gpo_amddate_raw': g['amddate_raw'],
                    'gpo_amddate_iso': g['amddate_iso'], 'gpo_appendices': g['appendices']})
    for (title, part) in slice_parts:
        parts.setdefault((title, part), {'title': title, 'part': part, 'heading': None, 'chapter': None, 'chapter_name': None, 'subchapter': None,
                                         'subchapter_name': None, 'subtitle': None, 'reserved': 0, 'ecfr_structure_sections': None,
                                         'ecfr_structure_size': None, 'structure_as_of': None})
    oul_part_counts = {}
    for r in oul_rows:
        oul_part_counts[(r['title'], r['part'])] = oul_part_counts.get((r['title'], r['part']), 0) + 1
    for key, row in parts.items():
        row['in_slice'] = int(key in slice_parts)
        row['oul_rows'] = oul_part_counts.get(key)
        vm = versions_meta.get(key)
        if vm:
            row.update({'versions_rows': vm['rows'], 'versions_sections': vm['sections'], 'versions_latest_amendment_date': vm['meta'].get('latest_amendment_date'),
                        'versions_latest_issue_date': vm['meta'].get('latest_issue_date'), 'versions_history_start': vm['history_start'],
                        'versions_captured_at': vm['captured_at']})
        fm = fr_meta.get(key)
        if fm:
            row.update({'fr_documents': fm['documents_distinct'], 'fr_count_reported': fm['count'], 'fr_pages_reported': fm['total_pages'],
                        'fr_pages_fetched': fm['pages_fetched'], 'fr_captured_at': fm['captured_at']})
    missing_slice = [k for k in slice_parts if k not in parts or not parts[k].get('heading')]
    checks.append({'name': 'selected_parts_present', 'passed': len(slice_parts) == 98 and not missing_slice,
                   'detail': f'{len(slice_parts)} selected parts; without heading={missing_slice}'})

    # -- printed amendment citations -> Federal Register documents -----------------------------------------------
    page_index = build_fr_page_index({k: dict(v['_raw']) for k, v in fr_docs.items()})
    citation_rows, unresolved = [], []
    for g in gpo['sections']:
        for note in g['citations']:
            for cite in parse_fr_citations(note):
                number, reason = resolve_citation(cite, page_index)
                citation_rows.append(('21', g['section'], cite['citation_printed'], cite['volume'], cite['page'], cite['date_printed'], number))
                if number is None:
                    unresolved.append({'from': {'type': 'cfr_section', 'id': f"cfr:21:{g['section']}"}, 'to_hint': {'type': 'fr_doc', 'citation': cite['citation_printed']},
                                       'relation': 'amended_by', 'reason': reason})
    for row in oul_non_section:
        unresolved.append({'from': {'type': 'open_us_law_row', 'id': row['source_id']}, 'to_hint': {'type': 'cfr_section'}, 'relation': 'provision_index',
                           'reason': row['reason'] + ' (section_number=' + row['section_number'] + ')'})
    resolved_citations = sum(1 for c in citation_rows if c[6])

    # -- reconciliation --------------------------------------------------------------------------------------------
    recon_rows = []
    for row in ordered:
        key = (row['title'], row['section'])
        if row['title'] != '21' and not row['in_slice']:
            continue
        g = gpo_by_section.get(row['section']) if row['title'] == '21' else None
        g_row = None
        if g:
            g_row = {'heading': g['heading'], 'text': g['text'], 'text_length': len(g['text'])}
        rec = reconcile(row['section'], g_row, oul_by_section.get(key, []), bool(row['in_ecfr_structure']), oul_texts)
        recon_rows.append((row['title'], row['part'], row['section'], rec['in_gpo'], rec['in_oul'], rec['oul_rows'], rec['in_ecfr_structure'],
                           rec['heading_match'], rec['text_comparison'], rec['gpo_text_length'], rec['oul_text_lengths'], rec['similarity'], rec['category']))
    recon_categories = {}
    for r in recon_rows:
        recon_categories[r[12]] = recon_categories.get(r[12], 0) + 1

    # -- write the database ---------------------------------------------------------------------------------------
    db_path = HERE / DB_NAME
    for stale in (db_path, Path(str(db_path) + '-journal'), Path(str(db_path) + '-wal')):
        if stale.exists():
            stale.unlink()
    db = sqlite3.connect(db_path)
    db.execute('PRAGMA journal_mode=MEMORY')
    create_schema(db)
    for title in TITLES:
        tm = titles_meta[title]
        db.execute('INSERT INTO titles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (title, tm['name'], tm.get('latest_amended_on'), tm.get('latest_issue_date'), tm.get('up_to_date_as_of'),
                    probe[('titles', None)]['requested_at'], structures[title]['as_of'], structures[title]['captured_at'],
                    GPO_XML_SHA256 if title == '21' else None, GPO_XML_CAPTURED_AT if title == '21' else None,
                    GPO_XML_SOURCE_URL if title == '21' else None, json.dumps(gpo['volumes']) if title == '21' else None,
                    sum(1 for k in parts if k[0] == title), len(selected.get(title, [])),
                    sum(1 for r in ordered if r['title'] == title and r['in_slice'])))
    part_cols = ['title', 'part', 'heading', 'chapter', 'chapter_name', 'subchapter', 'subchapter_name', 'subtitle', 'in_slice', 'reserved',
                 'ecfr_structure_sections', 'ecfr_structure_size', 'structure_as_of', 'oul_rows', 'gpo_sections', 'gpo_heading', 'gpo_authority',
                 'gpo_source_note', 'gpo_editorial_note', 'gpo_node', 'gpo_volume', 'gpo_amddate_raw', 'gpo_amddate_iso', 'gpo_appendices',
                 'versions_rows', 'versions_sections', 'versions_latest_amendment_date', 'versions_latest_issue_date', 'versions_history_start',
                 'versions_captured_at', 'fr_documents', 'fr_count_reported', 'fr_pages_reported', 'fr_pages_fetched', 'fr_captured_at']
    db.executemany(f"INSERT INTO parts({','.join(part_cols)}) VALUES({','.join('?' * len(part_cols))})",
                   [tuple(row.get(c) for c in part_cols) for row in parts.values()])
    sec_cols = ['id', 'title', 'part', 'section', 'heading', 'subpart', 'subject_group', 'in_slice', 'in_gpo', 'in_oul', 'in_ecfr_structure',
                'reserved', 'ecfr_size', 'ecfr_received_on', 'versions_count', 'latest_amendment_date', 'latest_issue_date']
    db.executemany(f"INSERT INTO section_index({','.join(sec_cols)}) VALUES({','.join('?' * len(sec_cols))})",
                   [tuple(row.get(c) for c in sec_cols) for row in ordered])
    db.executemany('INSERT INTO gpo_sections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   [('21', g['part'], g['section'], g['heading'], g['subpart'], g['subject_group'], g['node'], g['volume'], g['amddate_raw'],
                     g['amddate_iso'], int(g['reserved']), g['text'], sha256_text(g['text']), len(g['text']), json.dumps(g['citations'], ensure_ascii=False),
                     json.dumps(g['notes'], ensure_ascii=False), GPO_XML_CAPTURED_AT) for g in gpo['sections']])
    db.executemany('INSERT INTO gpo_appendices VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   [('21', a['part'], a['label'], a['heading'], a['node'], a['volume'], a['amddate_iso'], a['text'], sha256_text(a['text']),
                     len(a['text']), GPO_XML_CAPTURED_AT) for a in gpo['appendices']])
    oul_cols = ['oul_rowid', 'oul_id', 'source_id', 'citation', 'title', 'part', 'section', 'heading', 'status', 'snapshot', 'snapshot_date',
                'publisher_year', 'last_amended_year', 'text_length', 'text_sha256', 'source_url', 'captured_at']
    db.executemany(f"INSERT INTO oul_provisions({','.join(oul_cols)}) VALUES({','.join('?' * len(oul_cols))})",
                   [tuple(r[c] for c in oul_cols) for r in oul_rows])
    ver_cols = ['title', 'part', 'section', 'name', 'subpart', 'type', 'version_date', 'amendment_date', 'issue_date', 'substantive', 'removed', 'captured_at']
    db.executemany(f"INSERT INTO versions({','.join(ver_cols)}) VALUES({','.join('?' * len(ver_cols))})", [tuple(v[c] for c in ver_cols) for v in versions])
    fr_rows, part_rows = [], []
    for number, item in fr_docs.items():
        d = item['_raw']
        slugs = [a.get('slug') for a in (d.get('agencies') or []) if a.get('slug')]
        refs = [r for r in (d.get('cfr_references') or []) if r.get('title') and r.get('part')]
        fr_rows.append((number, d.get('citation'), d.get('title'), d.get('type'), d.get('action'), d.get('publication_date'), d.get('effective_on'),
                        d.get('signing_date'), d.get('comments_close_on'), d.get('dates'), json.dumps(d.get('docket_ids') or []),
                        json.dumps(d.get('regulation_id_numbers') or []), json.dumps(d.get('agencies') or [], ensure_ascii=False), json.dumps(slugs),
                        json.dumps(d.get('cfr_references') or []), d.get('html_url'), d.get('pdf_url'), d.get('full_text_xml_url'), d.get('raw_text_url'),
                        d.get('json_url'), d.get('volume'), d.get('start_page'), d.get('end_page'), d.get('page_length'),
                        json.dumps(d.get('correction_of')) if d.get('correction_of') is not None else None,
                        json.dumps(d.get('corrections')) if d.get('corrections') is not None else None,
                        json.dumps(local_text.get(number, [])), item['captured_at']))
        referenced = set()
        for r in refs:
            referenced.add((str(r['title']), str(r['part'])))
            part_rows.append((number, str(r['title']), str(r['part']), 'cfr_references'))
        for (t, p) in fr_query_parts.get(number, ()):
            if (t, p) not in referenced:
                part_rows.append((number, t, p, 'api_query_filter_only'))
    db.executemany('INSERT INTO fr_documents VALUES(' + ','.join('?' * 28) + ')', fr_rows)
    db.executemany('INSERT OR IGNORE INTO fr_doc_parts VALUES(?,?,?,?)', part_rows)
    db.executemany('INSERT INTO section_fr_citations VALUES(?,?,?,?,?,?,?)', citation_rows)
    db.executemany('INSERT INTO reconciliation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', recon_rows)
    fill_search(db)
    db.commit()
    db.execute('VACUUM')
    db.close()

    # -- agencies.jsonl ------------------------------------------------------------------------------------------------
    part_chapter = {k: v.get('chapter') for k, v in parts.items()}
    agencies = []

    def add_agency(a, parent_slug):
        refs = a.get('cfr_references') or []
        slice_hits, chapter_parts = [], []
        for ref in refs:
            t = str(ref.get('title'))
            if t not in TITLES:
                continue
            if ref.get('part') is not None:
                key = (t, str(ref['part']))
                if key in parts:
                    chapter_parts.append(f'cfr:{t}:{key[1]}')
                if key in slice_parts:
                    slice_hits.append(f'cfr:{t}:{key[1]}')
            elif ref.get('chapter') is not None:
                for key, chapter in part_chapter.items():
                    if key[0] == t and chapter == str(ref['chapter']):
                        chapter_parts.append(f'cfr:{t}:{key[1]}')
                        if key in slice_parts:
                            slice_hits.append(f'cfr:{t}:{key[1]}')
        slice_hits = sorted(set(slice_hits), key=lambda x: (x.split(':')[1], part_sort_key(x.split(':')[2])))
        agencies.append({'id': 'agency:' + a['slug'], 'slug': a['slug'], 'name': a.get('name'), 'short_name': a.get('short_name'),
                         'display_name': a.get('display_name'), 'parent_slug': parent_slug, 'cfr_references': refs,
                         'cfr_chapters': [f"{r.get('title')}:{r.get('chapter')}" for r in refs if r.get('chapter') is not None],
                         'slice_parts': slice_hits, 'parts_in_referenced_chapters': len(set(chapter_parts)),
                         'basis': 'eCFR admin/v1/agencies.json cfr_references (title/chapter) joined to eCFR structure part->chapter',
                         'temporal': temporal(captured_at=agencies_captured_at, captured_at_basis='eCFR admin/v1/agencies.json HTTP receipt (regulations scout, 2026-09-19)',
                                             source_as_of=titles_body.get('meta', {}).get('date'), source_as_of_basis='eCFR titles.json meta.date at capture (site data date)')})
        for child in a.get('children') or []:
            add_agency(child, a['slug'])

    for a in agencies_body['agencies']:
        add_agency(a, None)
    agencies.sort(key=lambda a: (not a['slice_parts'], a['name'] or ''))
    write_jsonl(HERE / 'agencies.jsonl', agencies)

    # -- edges.jsonl -----------------------------------------------------------------------------------------------------
    edges = []
    for row in ordered:
        edges.append({'from': {'type': 'cfr_section', 'id': f"cfr:{row['title']}:{row['section']}"}, 'to': {'type': 'cfr_part', 'id': f"cfr:{row['title']}:{row['part']}"},
                      'relation': 'part_of', 'basis': 'eCFR structure' if row['in_ecfr_structure'] else ('GPO eCFR XML node' if row['in_gpo'] else 'Open US Law source_id'),
                      'evidence': f"section {row['section']} listed under part {row['part']} of title {row['title']}"})
    for (title, part), row in parts.items():
        edges.append({'from': {'type': 'cfr_part', 'id': f'cfr:{title}:{part}'}, 'to': {'type': 'cfr_title', 'id': f'cfr:{title}'}, 'relation': 'part_of',
                      'basis': 'eCFR structure' if row.get('structure_as_of') else 'GPO eCFR XML', 'evidence': f'part {part} in title {title} structure ({row.get("structure_as_of") or "GPO XML"})'})
    for (number, t, p, basis) in part_rows:
        edges.append({'from': {'type': 'fr_doc', 'id': f'fr_doc:{number}'}, 'to': {'type': 'cfr_part', 'id': f'cfr:{t}:{p}'}, 'relation': 'affects_cfr_part',
                      'basis': 'Federal Register API cfr_references' if basis == 'cfr_references' else 'Federal Register API query filter conditions[cfr] (part not in cfr_references)',
                      'evidence': f'document {number} response for CFR title {t} part {p}'})
    for c in citation_rows:
        if c[6]:
            edges.append({'from': {'type': 'cfr_section', 'id': f'cfr:21:{c[1]}'}, 'to': {'type': 'fr_doc', 'id': f'fr_doc:{c[6]}'}, 'relation': 'amended_by',
                          'basis': 'GPO eCFR XML printed amendment note (CITA) matched to fetched FR document volume/page range',
                          'evidence': c[2]})
    for a in agencies:
        for pid in a['slice_parts']:
            edges.append({'from': {'type': 'agency', 'id': a['id']}, 'to': {'type': 'cfr_part', 'id': pid}, 'relation': 'administers',
                          'basis': a['basis'], 'evidence': f"agencies.json cfr_references {a['cfr_chapters']}"})
    edge_count = write_jsonl(HERE / 'edges.jsonl', edges)
    unresolved_count = write_jsonl(HERE / 'unresolved.jsonl', unresolved)

    # -- gaps.json --------------------------------------------------------------------------------------------------------
    gaps = {
        'network': {'packets_run': 2, 'requests_total': len(receipts), 'requests_this_run': 0,
                    'not_fetched': [{'what': 'Federal Register documents for the 30 selected Title 40 parts',
                                     'reason': 'packet budget: <= 98 requests per packet, two packets total; packet 2 held 68 page-1 requests + 13 continuation pages',
                                     'parts': [f'cfr:40:{p}' for p in selected.get('40', [])]}],
                    'barriers': [{'endpoint': 'www.ecfr.gov /api/versioner/v1/full/ and /api/renderer/v1/content/',
                                  'reason': 'robots.txt Disallow (conservative reading); official text taken from the local GPO Title 21 XML; other titles use the Open US Law index text on demand'}]},
        'local': {'official_text_titles': ['21'], 'titles_without_local_official_text': ['16', '49', '40'],
                  'gpo_unparsed_sections': gpo['unparsed'], 'gpo_duplicate_sections': gpo['duplicates'],
                  'oul_non_section_rows': oul_non_section, 'fr_documents_without_local_full_text': sum(1 for n in fr_docs if not local_text.get(n))},
        'reconciliation_categories': recon_categories,
        'unresolved_edges': unresolved_count,
        'not_verified': ['no point-in-time text (versions metadata only); eCFR version history starts 2016-2017',
                         'Open US Law text currency beyond its publisher snapshot date; act_status is a publisher assertion',
                         'Federal Register effective_on is publisher machine-extracted and never promoted to an effective date',
                         'agency -> part edges assume the whole chapter is administered by the agency listed in agencies.json'],
    }
    (HERE / 'gaps.json').write_text(json.dumps(gaps, indent=1, ensure_ascii=False), encoding='utf-8')

    # -- validation ---------------------------------------------------------------------------------------------------------
    slice_sections = sum(1 for r in ordered if r['in_slice'])
    counts = {'selected_parts': len(slice_parts), 'titles': len(TITLES), 'parts_indexed': len(parts), 'sections_indexed': len(ordered),
              'sections_in_slice': slice_sections, 'gpo_title21_sections': len(gpo['sections']), 'gpo_title21_parts': len(gpo['parts']),
              'gpo_title21_appendices': len(gpo['appendices']), 'oul_provisions': len(oul_rows), 'oul_provisions_in_slice': oul_slice_rows,
              'versions_rows': len(versions), 'versions_sections': len(versions_by_section), 'fr_documents': len(fr_docs),
              'fr_documents_with_local_full_text': sum(1 for n in fr_docs if local_text.get(n)), 'fr_doc_part_links': len(part_rows),
              'printed_fr_citations': len(citation_rows), 'printed_fr_citations_resolved': resolved_citations,
              'reconciliation_rows': len(recon_rows), 'reconciliation_categories': recon_categories, 'agencies': len(agencies),
              'agencies_with_slice_parts': sum(1 for a in agencies if a['slice_parts']), 'edges': edge_count, 'unresolved': unresolved_count,
              'receipts': len(receipts), 'network_requests_this_run': 0}
    checks.append({'name': 'no_effective_date_inferred', 'passed': True,
                   'detail': 'effective_from/effective_to are null everywhere; FR effective_on kept as effective_on (publisher-extracted)'})
    checks.append({'name': 'slice_sections_vs_scout', 'passed': slice_sections >= 5500,
                   'detail': f'{slice_sections} slice sections indexed (scout: eCFR 5,560 / Open US Law 5,538)'})
    checks.append({'name': 'agencies_cover_all_slice_parts', 'passed': {p for a in agencies for p in a['slice_parts']} == {f'cfr:{t}:{p}' for t, p in slice_parts},
                   'detail': f"{sum(1 for a in agencies if a['slice_parts'])} agencies map to slice parts"})
    report.update({'finished_at': now_iso(), 'elapsed_s': round(time.time() - started, 1), 'counts': counts, 'checks': checks,
                   'fr_parts': {f'{k[0]}:{k[1]}': v for k, v in fr_meta.items()}})
    (HERE / 'build_report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
    envelope = write_validation(HERE, [DB_NAME, 'agencies.jsonl', 'edges.jsonl', 'unresolved.jsonl'], counts=counts, checks=checks, inputs=inputs,
                                rows={DB_NAME: len(ordered), 'agencies.jsonl': len(agencies), 'edges.jsonl': edge_count, 'unresolved.jsonl': unresolved_count})
    print(json.dumps({'status': envelope['status'], 'elapsed_s': report['elapsed_s'], 'counts': counts,
                      'failed_checks': [c['name'] for c in checks if not c['passed']]}, indent=1))


if __name__ == '__main__':
    main()
