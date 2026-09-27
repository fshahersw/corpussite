"""Fail-closed adapter for the federal regulation slice (``sources/federal_regulations_20260919``).

Gate: ``validation.json`` must say ``status == "passed"`` and ``ready == true`` and every listed data
file (``regulations.sqlite3``, ``agencies.jsonl`` and any other listed file such as ``edges.jsonl`` and
``unresolved.jsonl``) must hash to the recorded SHA-256; hashes are re-verified whenever a file's size or
mtime changes. The database is opened read-only for every call.

Text: official regulation text is served only from the supplement's own database (GPO govinfo eCFR bulk
XML, Title 21; hash re-checked at serve time). Publisher text for the other titles is read on demand from
the Open US Law index (read-only) and served only when its SHA-256 matches the hash recorded at build
time; a missing or changed publisher index degrades to ``text: None`` without failing the section.

Temporal labels keep the eCFR amendment date, the Federal Register publication date, the publisher-
extracted ``effective_on``, the GPO volume amendment marker and the publisher snapshot date apart and
never infer one from another; ``effective_from``/``effective_to`` are always null with a basis string.
Public dicts carry no file-system paths.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/federal_regulations_20260919'
OUL_DB = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
DB_NAME = 'regulations.sqlite3'
REQUIRED_DATA_FILES = (DB_NAME, 'agencies.jsonl')
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
LIST_MAX_LIMIT = 1000
LIST_DEFAULT_LIMIT = 500
RELATED_DOCS_LIMIT = 20
FR_ROWID_OFFSET = 10_000_000
TEMPORAL_KEYS = ('captured_at', 'captured_at_basis', 'source_as_of', 'source_as_of_basis', 'published_at', 'published_at_basis',
                 'effective_from', 'effective_from_basis', 'effective_to', 'effective_to_basis')
NO_EFFECTIVE = 'not stated by the source; never inferred from an amendment, publication, issue, marker or snapshot date'
DATE_TYPES = {
    'amendment_date': 'eCFR amendment date (per-section version metadata; eCFR history begins in 2016/2017)',
    'ecfr_issue_date': 'eCFR issue date of a section version (Federal Register issue date as reported by eCFR version metadata)',
    'gpo_amendment_marker': 'GPO eCFR XML volume amendment marker (AMDDATE: currency of the printed Title 21 volume, not a per-section date)',
    'oul_snapshot_date': 'Open US Law index snapshot date (publisher snapshot; not an as-of or effective date)',
    'fr_publication_date': 'Federal Register publication date (publisher field publication_date)',
    'fr_effective_on': 'Federal Register effective_on (publisher-extracted from the DATES section; unverified; never promoted to an effective date)',
    'fr_signing_date': 'Federal Register signing_date (publisher field)',
    'fr_comments_close_on': 'Federal Register comments_close_on (publisher field)',
}
SECTION_DATE_TYPES = ('amendment_date', 'ecfr_issue_date', 'gpo_amendment_marker', 'oul_snapshot_date')
FR_DATE_TYPES = ('fr_publication_date', 'fr_effective_on', 'fr_signing_date', 'fr_comments_close_on')
TEXT_SOURCES = {
    'gpo_ecfr_xml': 'GPO govinfo eCFR bulk XML, local copy (eCFR is authoritative but unofficial; the official edition is the annual CFR)',
    'open_us_law': 'Open US Law index (CC BY 4.0): publisher text read on demand, served only when its SHA-256 matches the build-time hash',
}
RECORD_TYPES = ('section', 'fr_document')
_SHA = re.compile(r'[a-f0-9]{64}')
_ISO = re.compile(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?')
_REF = re.compile(r'^\s*(?:cfr:(?P<t1>\d{1,2}):|(?P<t2>\d{1,2})\s*:\s*|(?P<t3>\d{1,2})\s*C\.?\s*F\.?\s*R\.?\s*(?:part\s+|pt\.?\s*|§+\s*|section\s+|sec\.?\s*)?)?'
                  r'(?P<part>\d{1,4}[a-z]?)(?:\.(?P<sec>\d{1,5}[a-z0-9\-–]*))?\s*$', re.I)
_DOCNUM = re.compile(r'^(?:fr_doc:)?([A-Za-z0-9][A-Za-z0-9\-]{2,30})$')
_SECTION_DATE_SQL = {
    'amendment_date': "SELECT DISTINCT s.id FROM section_index s JOIN versions v ON v.title = s.title AND v.section = s.section "
                      "WHERE v.type = 'section' AND v.amendment_date >= ? AND v.amendment_date <= ?",
    'ecfr_issue_date': "SELECT DISTINCT s.id FROM section_index s JOIN versions v ON v.title = s.title AND v.section = s.section "
                       "WHERE v.type = 'section' AND v.issue_date >= ? AND v.issue_date <= ?",
    'gpo_amendment_marker': "SELECT s.id FROM section_index s JOIN gpo_sections g ON g.title = s.title AND g.section = s.section "
                            "WHERE g.amddate_iso >= ? AND g.amddate_iso <= ?",
    'oul_snapshot_date': "SELECT DISTINCT s.id FROM section_index s JOIN oul_provisions o ON o.title = s.title AND o.section = s.section "
                         "WHERE o.snapshot_date >= ? AND o.snapshot_date <= ?",
}
_FR_DATE_COLUMN = {'fr_publication_date': 'publication_date', 'fr_effective_on': 'effective_on', 'fr_signing_date': 'signing_date',
                   'fr_comments_close_on': 'comments_close_on'}
_LOCK = threading.Lock()
_CACHE = {}


# ----------------------------------------------------------------------------- helpers
def reset_cache():
    with _LOCK:
        _CACHE.clear()


def _digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _stat(path):
    try:
        info = Path(path).stat()
        return info.st_size, info.st_mtime_ns
    except OSError:
        return None


def _number(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _iso_bound(value, end=False):
    """YYYY, YYYY-MM or YYYY-MM-DD -> inclusive ISO bound; None when absent; False when malformed."""
    if value is None or value == '':
        return None
    match = _ISO.fullmatch(str(value).strip())
    if not match:
        return False
    year, month, day = match.groups()
    if day:
        return '%s-%s-%s' % (year, month, day)
    if month:
        return '%s-%s-%s' % (year, month, '31' if end else '01')
    return year + ('-12-31' if end else '-01-01')


def _full_date(value):
    """A real calendar date written YYYY-MM-DD, else None."""
    try:
        return _dt.date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def _json(value, default):
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return default
    try:
        loaded = json.loads(value)
    except ValueError:
        return default
    return loaded if isinstance(loaded, type(default)) else default


def _tokens(q):
    return [t for t in re.findall(r'[^\W_]+', q or '') if t][:16]


def _fts_expr(column, tokens):
    return '%s : (%s)' % (column, ' '.join('"%s"*' % token for token in tokens))


def _num_key(text):
    key = []
    for piece in re.split(r'[.\-–]', str(text or '')):
        match = re.match(r'(\d+)(.*)', piece)
        key.append((int(match.group(1)), match.group(2)) if match else (10 ** 9, piece))
    return tuple(key)


def _parse_ref(text, title=None):
    """'cfr:21:314.80', '21 C.F.R. § 314.80', '21 CFR 314.80', '21:314', '314' (+title) -> dict(title, part, section|None)."""
    if not isinstance(text, str):
        return None
    match = _REF.match(text)
    if not match:
        return None
    found = match.group('t1') or match.group('t2') or match.group('t3')
    if not found and title not in (None, ''):
        found = str(title).strip()
        if not found.isdigit():
            return None
    part = match.group('part').lower()
    section = (part + '.' + match.group('sec').lower()) if match.group('sec') else None
    return {'title': found, 'part': part, 'section': section}


def _temporal(**fields):
    return {key: fields.get(key) for key in TEMPORAL_KEYS}


def _norm_temporal(block):
    return {key: (block.get(key) if isinstance(block, dict) else None) for key in TEMPORAL_KEYS}


# ----------------------------------------------------------------------------- gate and cache
def _gate(base):
    """Return (state, None, listed files) when every hash matches, else (None, reason, listed files). Never raises."""
    try:
        gate = json.loads((base / 'validation.json').read_bytes().decode('utf-8-sig'))
    except (OSError, ValueError):
        return None, 'validation_unreadable', []
    if not isinstance(gate, dict) or gate.get('status') != 'passed' or gate.get('ready') is not True:
        return None, 'validation_not_passed', []
    files = gate.get('data_files')
    if not isinstance(files, list):
        return None, 'validation_malformed', []
    listed, watched, seen = [], [], set()
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('sha256'), str):
            return None, 'validation_malformed', listed
        if not _SHA.fullmatch(entry['sha256']):
            return None, 'validation_malformed', listed
        label = Path(entry['path']).stem
        file = (base / entry['path']).resolve()
        listed.append(file)
        if not file.is_relative_to(base) or not file.is_file():
            return None, 'data_file_missing:' + label, listed
        try:
            if _digest_file(file) != entry['sha256']:
                return None, 'data_file_hash_mismatch:' + label, listed
        except OSError:
            return None, 'data_file_unreadable:' + label, listed
        seen.add(file.name)
        watched.append((file, _stat(file)))
    missing = [Path(name).stem for name in REQUIRED_DATA_FILES if name not in seen]
    if missing:
        return None, 'data_file_not_gated:' + ','.join(missing), listed
    agencies = []
    try:
        with (base / 'agencies.jsonl').open('r', encoding='utf-8-sig') as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get('slug'), str):
                    return None, 'agencies_malformed', listed
                agencies.append(item)
    except (OSError, ValueError):
        return None, 'agencies_unreadable', listed
    return {'base': base, 'gate': gate, 'watched': watched, 'agencies': agencies}, None, listed


def _state():
    base = Path(DATA).resolve()
    control = _stat(base / 'validation.json')
    with _LOCK:
        cached = _CACHE.get(base)
        if cached and cached['control'] == control and all(_stat(f) == s for f, s in cached['watched']):
            return cached['state'], cached['reason']
        state, reason, listed = _gate(base)
        watched = state['watched'] if state else [(f, _stat(f)) for f in listed]
        _CACHE[base] = {'control': control, 'state': state, 'reason': reason, 'watched': watched}
        return state, reason


def _connect(state):
    """Read-only connection that is closed (not merely committed) on exit."""
    return closing(sqlite3.connect((state['base'] / DB_NAME).as_uri() + '?mode=ro', uri=True))


def _oul_open():
    """Read-only connection to the Open US Law index, or None when it is absent or unopenable."""
    try:
        path = Path(OUL_DB)
        if not path.is_file():
            return None
        return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    except (OSError, ValueError, sqlite3.Error):
        return None


def _rows(db, sql, params=()):
    cursor = db.execute(sql, params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(db, sql, params=()):
    rows = _rows(db, sql, params)
    return rows[0] if rows else None


def _titles(db):
    return {row['title']: row for row in _rows(db, 'SELECT * FROM titles')}


# ----------------------------------------------------------------------------- shaping
def _title_public(row):
    markers = [{'volume': m.get('volume'), 'amddate_raw': m.get('amddate_raw'), 'amddate_iso': m.get('amddate_iso'), 'head': m.get('head')}
               for m in _json(row.get('gpo_volume_amddates'), []) if isinstance(m, dict)]
    official = bool(row.get('gpo_xml_sha256'))
    return {'id': 'cfr:' + row['title'], 'title': row['title'], 'name': row.get('name'),
            'parts_total': row.get('parts_total'), 'parts_in_slice': row.get('parts_in_slice'), 'sections_in_slice': row.get('sections_in_slice'),
            'official_text_local': official, 'official_text_source': TEXT_SOURCES['gpo_ecfr_xml'] if official else None,
            'gpo_xml_sha256': row.get('gpo_xml_sha256'), 'gpo_xml_source_url': row.get('gpo_xml_source_url'),
            'gpo_volume_amendment_markers': markers, 'gpo_volume_amendment_markers_basis': DATE_TYPES['gpo_amendment_marker'] if markers else None,
            'publisher_text_source': TEXT_SOURCES['open_us_law'],
            'ecfr': {'latest_amended_on': row.get('ecfr_latest_amended_on'), 'latest_issue_date': row.get('ecfr_latest_issue_date'),
                     'up_to_date_as_of': row.get('ecfr_up_to_date_as_of'), 'structure_as_of': row.get('structure_as_of'),
                     'basis': 'eCFR versioner titles.json and structure endpoints (metadata only)'},
            'temporal': _temporal(captured_at=row.get('ecfr_titles_captured_at'), captured_at_basis='eCFR versioner titles.json HTTP receipt',
                                  source_as_of=row.get('ecfr_up_to_date_as_of'),
                                  source_as_of_basis='eCFR titles.json up_to_date_as_of (eCFR currency statement for the title)',
                                  effective_from_basis=NO_EFFECTIVE, effective_to_basis=NO_EFFECTIVE)}


def _part_public(row, title_row):
    title_row = title_row or {}
    if row.get('gpo_amddate_iso'):
        captured, captured_basis = title_row.get('gpo_xml_captured_at'), 'GPO govinfo eCFR bulk XML download receipt'
        as_of = row['gpo_amddate_iso']
        as_of_basis = ('GPO eCFR XML volume %s amendment marker (AMDDATE "%s"): currency of the printed volume carrying this part; '
                       'not a per-section or effective date' % (row.get('gpo_volume'), row.get('gpo_amddate_raw')))
    else:
        captured, captured_basis = title_row.get('structure_captured_at'), 'eCFR structure endpoint HTTP receipt'
        as_of, as_of_basis = row.get('structure_as_of'), 'eCFR structure snapshot date for the title (no local official text for this part)'
    return {'id': 'cfr:%s:%s' % (row['title'], row['part']), 'title': row['title'], 'part': row['part'], 'heading': row.get('heading'),
            'chapter': row.get('chapter'), 'chapter_name': row.get('chapter_name'), 'subchapter': row.get('subchapter'),
            'subchapter_name': row.get('subchapter_name'), 'subtitle': row.get('subtitle'), 'in_slice': bool(row.get('in_slice')),
            'reserved': bool(row.get('reserved')), 'sections_in_ecfr_structure': row.get('ecfr_structure_sections'),
            'sections_with_local_official_text': row.get('gpo_sections') or 0, 'appendices_with_local_official_text': row.get('gpo_appendices') or 0,
            'publisher_index_rows': row.get('oul_rows'), 'official_text_local': bool(row.get('gpo_sections')),
            'heading_as_printed': row.get('gpo_heading'), 'authority_note_as_printed': row.get('gpo_authority'),
            'source_note_as_printed': row.get('gpo_source_note'), 'editorial_note_as_printed': row.get('gpo_editorial_note'),
            'gpo_volume': row.get('gpo_volume'),
            'gpo_amendment_marker': {'raw': row.get('gpo_amddate_raw'), 'iso': row.get('gpo_amddate_iso'), 'basis': DATE_TYPES['gpo_amendment_marker']}
            if row.get('gpo_amddate_iso') else None,
            'versions': {'rows': row.get('versions_rows'), 'sections': row.get('versions_sections'),
                         'latest_amendment_date': row.get('versions_latest_amendment_date'), 'latest_issue_date': row.get('versions_latest_issue_date'),
                         'history_start': row.get('versions_history_start'), 'captured_at': row.get('versions_captured_at'),
                         'basis': 'eCFR versioner API per-section version metadata (dates only; no point-in-time text)'},
            'federal_register': {'documents_linked': row.get('fr_documents'), 'count_reported': row.get('fr_count_reported'),
                                 'pages_reported': row.get('fr_pages_reported'), 'pages_fetched': row.get('fr_pages_fetched'),
                                 'captured_at': row.get('fr_captured_at'), 'basis': 'Federal Register API documents whose cfr_references list this part'},
            'ecfr_url': 'https://www.ecfr.gov/current/title-%s/part-%s' % (row['title'], row['part']),
            'temporal': _temporal(captured_at=captured, captured_at_basis=captured_basis, source_as_of=as_of, source_as_of_basis=as_of_basis,
                                  effective_from_basis=NO_EFFECTIVE, effective_to_basis=NO_EFFECTIVE)}


def _section_summary(row, title_row):
    title_row = title_row or {}
    sources = [name for name, flag in (('gpo_ecfr_xml', row.get('in_gpo')), ('open_us_law', row.get('in_oul'))) if flag]
    return {'id': 'cfr:%s:%s' % (row['title'], row['section']), 'record_type': 'section',
            'citation': '%s C.F.R. § %s' % (row['title'], row['section']), 'title': row['title'], 'part': row['part'],
            'part_id': 'cfr:%s:%s' % (row['title'], row['part']), 'section': row['section'], 'heading': row.get('heading'),
            'subpart': row.get('subpart'), 'subject_group': row.get('subject_group'), 'reserved': bool(row.get('reserved')),
            'in_slice': bool(row.get('in_slice')), 'in_ecfr_structure': bool(row.get('in_ecfr_structure')), 'in_current_index': True,
            'text_sources_available': sources, 'ecfr_size': row.get('ecfr_size'), 'ecfr_received_on': row.get('ecfr_received_on'),
            'ecfr_received_on_basis': 'eCFR structure received_on (eCFR metadata; not an amendment or effective date)' if row.get('ecfr_received_on') else None,
            'versions_count': row.get('versions_count') or 0,
            'latest_amendment_date': row.get('latest_amendment_date'),
            'latest_amendment_date_basis': DATE_TYPES['amendment_date'] if row.get('latest_amendment_date') else None,
            'latest_issue_date': row.get('latest_issue_date'),
            'latest_issue_date_basis': DATE_TYPES['ecfr_issue_date'] if row.get('latest_issue_date') else None,
            'ecfr_url': 'https://www.ecfr.gov/current/title-%s/section-%s' % (row['title'], row['section']),
            'temporal': _temporal(captured_at=title_row.get('structure_captured_at'), captured_at_basis='eCFR structure endpoint HTTP receipt',
                                  source_as_of=title_row.get('structure_as_of'),
                                  source_as_of_basis='eCFR structure snapshot date for the title (section identity and heading; text currency is stated per text source)',
                                  effective_from_basis=NO_EFFECTIVE, effective_to_basis=NO_EFFECTIVE)}


def _gpo_text_public(g):
    text = g.get('text') if isinstance(g.get('text'), str) else ''
    verified = hashlib.sha256(text.encode('utf-8')).hexdigest() == g.get('text_sha256')
    marker_basis = ('GPO eCFR XML volume %s amendment marker (AMDDATE "%s"): currency of the printed volume, not a per-section date'
                    % (g.get('volume'), g.get('amddate_raw')))
    return {'source': 'gpo_ecfr_xml', 'label': TEXT_SOURCES['gpo_ecfr_xml'], 'role': 'official_text_local_copy',
            'text': text if verified else None, 'text_hash_verified': verified, 'text_sha256': g.get('text_sha256'), 'text_length': g.get('text_length'),
            'unavailable_reason': None if verified else 'local_text_hash_mismatch',
            'heading_as_printed': g.get('heading'), 'subpart': g.get('subpart'), 'subject_group': g.get('subject_group'), 'node': g.get('node'),
            'volume': g.get('volume'), 'reserved': bool(g.get('reserved')),
            'amendment_marker': {'raw': g.get('amddate_raw'), 'iso': g.get('amddate_iso'), 'basis': DATE_TYPES['gpo_amendment_marker']},
            'amendment_citations_as_printed': _json(g.get('amendment_citations'), []), 'notes_as_printed': _json(g.get('notes'), []),
            'temporal': _temporal(captured_at=g.get('captured_at'), captured_at_basis='GPO govinfo eCFR bulk XML download receipt',
                                  source_as_of=g.get('amddate_iso'), source_as_of_basis=marker_basis,
                                  effective_from_basis=NO_EFFECTIVE, effective_to_basis=NO_EFFECTIVE)}


def _oul_text_public(prov, oul):
    entry = {'source': 'open_us_law', 'label': TEXT_SOURCES['open_us_law'], 'role': 'publisher_text_on_demand', 'text': None,
             'text_hash_verified': False, 'text_sha256': prov.get('text_sha256'), 'text_length': prov.get('text_length'),
             'publisher_index_available': oul is not None, 'unavailable_reason': None,
             'citation_as_indexed': prov.get('citation'), 'heading_as_indexed': prov.get('heading'), 'status_as_indexed': prov.get('status'),
             'snapshot': prov.get('snapshot'), 'snapshot_date': prov.get('snapshot_date'), 'snapshot_date_basis': DATE_TYPES['oul_snapshot_date'],
             'publisher_year': prov.get('publisher_year'), 'publisher_last_amended_year': prov.get('last_amended_year'),
             'publisher_year_basis': 'Open US Law payload fields year / last_amended_year (publisher-reported; not verified)',
             'source_url_as_indexed': prov.get('source_url'), 'publisher_record_id': prov.get('oul_id'), 'publisher_source_id': prov.get('source_id'),
             'temporal': _temporal(captured_at=prov.get('captured_at'), captured_at_basis='Open US Law index row indexed_at (local index build)',
                                   source_as_of=prov.get('snapshot_date'),
                                   source_as_of_basis='Open US Law snapshot %s date: publisher index snapshot, not an as-of or effective date' % prov.get('snapshot'),
                                   effective_from_basis=NO_EFFECTIVE, effective_to_basis=NO_EFFECTIVE)}
    if oul is None:
        entry['unavailable_reason'] = 'publisher_index_unavailable'
        return entry
    try:
        row = oul.execute('SELECT id, text FROM records WHERE rowid = ?', (prov.get('oul_rowid'),)).fetchone()
    except sqlite3.Error:
        entry['unavailable_reason'] = 'publisher_index_error'
        return entry
    if row is None:
        entry['unavailable_reason'] = 'publisher_row_missing'
    elif row[0] != prov.get('oul_id'):
        entry['unavailable_reason'] = 'publisher_row_identity_mismatch'
    else:
        text = row[1] if isinstance(row[1], str) else ''
        if hashlib.sha256(text.encode('utf-8')).hexdigest() == prov.get('text_sha256'):
            entry['text'], entry['text_hash_verified'] = text, True
        else:
            entry['unavailable_reason'] = 'publisher_text_hash_mismatch'
    return entry


def _history_public(v):
    return {'version_date': v.get('version_date'), 'ecfr_amendment_date': v.get('amendment_date'), 'ecfr_issue_date': v.get('issue_date'),
            'substantive': bool(v.get('substantive')), 'removed': bool(v.get('removed')), 'type': v.get('type'),
            'name_as_reported': v.get('name'), 'subpart': v.get('subpart'),
            'basis': 'eCFR versioner API version metadata (dates only; no point-in-time text held locally)',
            'temporal': _temporal(captured_at=v.get('captured_at'), captured_at_basis='eCFR versioner API HTTP receipt',
                                  source_as_of=v.get('version_date'), source_as_of_basis='eCFR version date (metadata only)',
                                  published_at=v.get('issue_date'),
                                  published_at_basis='eCFR issue_date: Federal Register issue date as reported by eCFR version metadata',
                                  effective_from_basis=NO_EFFECTIVE, effective_to_basis=NO_EFFECTIVE)}


def _doc_temporal(row):
    return _temporal(captured_at=row.get('captured_at'), captured_at_basis='Federal Register API HTTP receipt',
                     published_at=row.get('publication_date'), published_at_basis=DATE_TYPES['fr_publication_date'],
                     effective_from_basis='not promoted: effective_on is a publisher-extracted field (unverified against the document text); '
                                          'see effective_on_publisher_extracted and dates_text_raw',
                     effective_to_basis=NO_EFFECTIVE)


def _doc_summary(row):
    refs = [r for r in _json(row.get('cfr_references'), []) if isinstance(r, dict)]
    part_ids = sorted({'cfr:%s:%s' % (r.get('title'), r.get('part')) for r in refs if r.get('title') is not None and r.get('part')})
    return {'id': 'fr_doc:' + row['document_number'], 'record_type': 'fr_document', 'document_number': row['document_number'],
            'citation': row.get('citation'), 'title': row.get('title'), 'type': row.get('type'), 'action': row.get('action'),
            'publication_date': row.get('publication_date'), 'publication_date_basis': DATE_TYPES['fr_publication_date'],
            'effective_on_publisher_extracted': row.get('effective_on'), 'effective_on_basis': DATE_TYPES['fr_effective_on'],
            'signing_date': row.get('signing_date'), 'comments_close_on': row.get('comments_close_on'),
            'agency_slugs': [s for s in _json(row.get('agency_slugs'), []) if isinstance(s, str)], 'cfr_part_ids': part_ids,
            'html_url': row.get('html_url'), 'temporal': _doc_temporal(row)}


def _doc_public(db, row):
    out = _doc_summary(row)
    agencies = [{'name': a.get('name'), 'raw_name': a.get('raw_name'), 'slug': a.get('slug'), 'id': a.get('id'), 'parent_id': a.get('parent_id')}
                for a in _json(row.get('agencies'), []) if isinstance(a, dict)]
    linked = _rows(db, 'SELECT title, part, basis FROM fr_doc_parts WHERE document_number = ? ORDER BY title, part', (row['document_number'],))
    cited_by = _rows(db, 'SELECT DISTINCT title, section FROM section_fr_citations WHERE document_number = ? ORDER BY title, section',
                     (row['document_number'],))
    out.update({
        'dates_text_raw': row.get('dates_text'), 'docket_ids': [d for d in _json(row.get('docket_ids'), []) if isinstance(d, str)],
        'regulation_id_numbers': [r for r in _json(row.get('regulation_id_numbers'), []) if isinstance(r, str)], 'agencies': agencies,
        'cfr_references': [r for r in _json(row.get('cfr_references'), []) if isinstance(r, dict)],
        'cfr_parts_linked': [{'id': 'cfr:%s:%s' % (l['title'], l['part']), 'basis': 'Federal Register API ' + str(l.get('basis'))} for l in linked],
        'cited_by_sections': sorted(('cfr:%s:%s' % (c['title'], c['section']) for c in cited_by), key=lambda s: _num_key(s.split(':', 1)[1].replace(':', '.'))),
        'cited_by_sections_basis': 'printed "[NN FR PPPP, date]" amendment notes in the GPO eCFR XML resolved by volume and start page',
        'urls': {'html': row.get('html_url'), 'pdf': row.get('pdf_url'), 'full_text_xml': row.get('full_text_xml_url'),
                 'raw_text': row.get('raw_text_url'), 'json': row.get('json_url')},
        'volume': row.get('volume'), 'start_page': row.get('start_page'), 'end_page': row.get('end_page'), 'page_length': row.get('page_length'),
        'correction_of': row.get('correction_of'), 'corrections': _json(row.get('corrections'), []),
        'local_full_text_record_ids': [i for i in _json(row.get('local_full_text'), []) if isinstance(i, str)],
        'local_full_text_basis': 'ids of locally held Federal Register full-text records in the seeger import; not served by this adapter',
        'qualification': 'Federal Register API metadata only; publication_date is the publisher field; effective_on is publisher-extracted '
                         'and unverified; the document text itself is not held here.'})
    return out


def _agency_public(item):
    return {'id': item.get('id') or ('agency:' + item['slug']), 'slug': item['slug'], 'name': item.get('name'), 'display_name': item.get('display_name'),
            'short_name': item.get('short_name'), 'parent_slug': item.get('parent_slug'),
            'cfr_references': [r for r in (item.get('cfr_references') or []) if isinstance(r, dict)],
            'cfr_chapters': [c for c in (item.get('cfr_chapters') or []) if isinstance(c, str)],
            'parts_in_referenced_chapters': item.get('parts_in_referenced_chapters'),
            'slice_parts': [p for p in (item.get('slice_parts') or []) if isinstance(p, str)], 'basis': item.get('basis'),
            'temporal': _norm_temporal(item.get('temporal'))}


def _agency_parts(state, slug):
    """Set of (title, part) an agency's slice_parts cover; None when the slug is unknown."""
    needle = str(slug or '').strip().lower()
    if needle.startswith('agency:'):
        needle = needle[7:]
    for item in state['agencies']:
        if item['slug'].lower() == needle:
            found = set()
            for ref in item.get('slice_parts') or []:
                parsed = _parse_ref(ref) if isinstance(ref, str) else None
                if parsed and parsed['title']:
                    found.add((parsed['title'], parsed['part']))
            return found
    return None


def _closed_list(key, reason=None, **extra):
    return dict({'available': False, 'reason': reason, 'total': 0, key: []}, **extra)


def _closed_search(reason=None):
    return {'available': False, 'reason': reason, 'total': 0, 'page': 1, 'limit': DEFAULT_LIMIT, 'pages': 0, 'results': [], 'filters': {},
            'date_filter': None, 'publisher_index': {'available': False, 'searched': False}}


# ----------------------------------------------------------------------------- public API
def info():
    """Path-free load report: gate state, validation counts and checks, date-type and text-source labels."""
    state, reason = _state()
    closed = {'ready': False, 'available': False, 'reason': reason, 'counts': {}, 'date_types': DATE_TYPES, 'text_sources': TEXT_SOURCES}
    if not state:
        return closed
    gate = state['gate']
    try:
        with _connect(state) as db:
            live = {'titles': db.execute('SELECT count(*) FROM titles').fetchone()[0],
                    'parts_indexed': db.execute('SELECT count(*) FROM parts').fetchone()[0],
                    'parts_in_slice': db.execute('SELECT count(*) FROM parts WHERE in_slice = 1').fetchone()[0],
                    'sections_indexed': db.execute('SELECT count(*) FROM section_index').fetchone()[0],
                    'sections_in_slice': db.execute('SELECT count(*) FROM section_index WHERE in_slice = 1').fetchone()[0],
                    'sections_with_local_official_text': db.execute('SELECT count(*) FROM gpo_sections').fetchone()[0],
                    'publisher_provisions': db.execute('SELECT count(*) FROM oul_provisions').fetchone()[0],
                    'version_rows': db.execute('SELECT count(*) FROM versions').fetchone()[0],
                    'fr_documents': db.execute('SELECT count(*) FROM fr_documents').fetchone()[0],
                    'agencies': len(state['agencies'])}
            snapshot = _one(db, 'SELECT snapshot, snapshot_date FROM oul_provisions LIMIT 1') or {}
    except sqlite3.Error as error:
        return dict(closed, reason='database_error:' + type(error).__name__)
    oul = _oul_open()
    if oul is not None:
        oul.close()
    inputs = [{k: v for k, v in item.items() if k != 'path'} for item in (gate.get('inputs') or []) if isinstance(item, dict)]
    checks = [{'name': c.get('name'), 'passed': c.get('passed'), 'detail': c.get('detail')} for c in (gate.get('checks') or []) if isinstance(c, dict)]
    return {'ready': True, 'available': True, 'reason': None, 'validated_at': gate.get('validated_at'),
            'counts': dict(gate.get('counts') or {}, live=live), 'checks': checks, 'qualification': gate.get('qualification'),
            'license_ref': gate.get('license_ref'), 'inputs': inputs,
            'publisher_index': {'available': oul is not None, 'snapshot': snapshot.get('snapshot'), 'snapshot_date': snapshot.get('snapshot_date'),
                                'basis': 'Open US Law index read on demand, read-only; every served text is re-hashed against the build-time SHA-256'},
            'date_types': DATE_TYPES, 'text_sources': TEXT_SOURCES, 'record_types': list(RECORD_TYPES),
            'limits': {'search_default_limit': DEFAULT_LIMIT, 'search_max_limit': MAX_LIMIT, 'list_default_limit': LIST_DEFAULT_LIMIT,
                       'list_max_limit': LIST_MAX_LIMIT, 'related_documents_limit': RELATED_DOCS_LIMIT}}


def titles():
    """The CFR titles in the slice with eCFR currency metadata and the local official-text flag. [] when closed."""
    state, reason = _state()
    if not state:
        return _closed_list('titles', reason)
    try:
        with _connect(state) as db:
            rows = sorted(_rows(db, 'SELECT * FROM titles'), key=lambda r: _num_key(r['title']))
    except sqlite3.Error as error:
        return _closed_list('titles', 'database_error:' + type(error).__name__)
    return {'available': True, 'reason': None, 'total': len(rows), 'titles': [_title_public(r) for r in rows]}


def parts(title, include_all=False, page=1, limit=LIST_DEFAULT_LIMIT):
    """Parts of one title: the selected slice by default, every eCFR-structure part with include_all. [] when closed."""
    page, limit = _number(page, 1, 1, 10 ** 6), _number(limit, LIST_DEFAULT_LIMIT, 1, LIST_MAX_LIMIT)
    key = str(title).strip().lower().replace('cfr:', '') if title is not None else ''
    empty = {'available': True, 'reason': None, 'title': None, 'include_all': bool(include_all), 'total': 0, 'page': page, 'limit': limit,
             'pages': 0, 'parts': []}
    state, reason = _state()
    if not state:
        return dict(empty, available=False, reason=reason)
    if not key.isdigit():
        return dict(empty, error='title must be a CFR title number')
    try:
        with _connect(state) as db:
            title_row = _one(db, 'SELECT * FROM titles WHERE title = ?', (key,))
            if not title_row:
                return empty
            clause = '' if include_all else ' AND in_slice = 1'
            rows = sorted(_rows(db, 'SELECT * FROM parts WHERE title = ?' + clause, (key,)), key=lambda r: _num_key(r['part']))
    except sqlite3.Error as error:
        return dict(empty, available=False, reason='database_error:' + type(error).__name__)
    total = len(rows)
    window = rows[(page - 1) * limit:page * limit]
    return dict(empty, title=_title_public(title_row), total=total, pages=math.ceil(total / limit) if total else 0,
                parts=[_part_public(r, title_row) for r in window])


def sections(part, as_of=None, title=None, page=1, limit=LIST_DEFAULT_LIMIT):
    """Sections of one part ('21:314', 'cfr:21:314', '21 CFR 314', or '314' with title=).

    as_of (YYYY-MM-DD): lists the section identifiers that eCFR per-section version metadata shows as present on that
    date. Metadata only: the texts served remain the current local copy / publisher snapshot, and sections without any
    version rows are omitted from the as_of listing (their count is reported). Dates before the part's version history
    start are unsupported.
    """
    page, limit = _number(page, 1, 1, 10 ** 6), _number(limit, LIST_DEFAULT_LIMIT, 1, LIST_MAX_LIMIT)
    ref = _parse_ref(part, title)
    empty = {'available': True, 'reason': None, 'found': False, 'part': None, 'as_of': None, 'total': 0, 'page': page, 'limit': limit,
             'pages': 0, 'sections': []}
    state, reason = _state()
    if not state:
        return dict(empty, available=False, reason=reason)
    if not ref or not ref['title']:
        return dict(empty, error='part must be given as cfr:<title>:<part>, <title>:<part>, "<title> CFR <part>" or <part> with title=')
    try:
        with _connect(state) as db:
            title_row = _one(db, 'SELECT * FROM titles WHERE title = ?', (ref['title'],)) or {}
            part_row = _one(db, 'SELECT * FROM parts WHERE title = ? AND part = ?', (ref['title'], ref['part']))
            if not part_row:
                return empty
            current = sorted(_rows(db, 'SELECT * FROM section_index WHERE title = ? AND part = ?', (ref['title'], ref['part'])),
                             key=lambda r: _num_key(r['section']))
            as_of_block = None
            rows = [_section_summary(r, title_row) for r in current]
            if as_of is not None:
                versions = _rows(db, "SELECT * FROM versions WHERE title = ? AND part = ? AND type = 'section' ORDER BY version_date, rowid",
                                 (ref['title'], ref['part']))
                rows, as_of_block = _as_of_listing(as_of, part_row, current, versions, title_row)
    except sqlite3.Error as error:
        return dict(empty, available=False, reason='database_error:' + type(error).__name__)
    total = len(rows)
    return dict(empty, found=True, part=_part_public(part_row, title_row), as_of=as_of_block, total=total,
                pages=math.ceil(total / limit) if total else 0, sections=rows[(page - 1) * limit:page * limit])


def _as_of_listing(as_of, part_row, current, versions, title_row):
    date = _full_date(as_of)
    dated = [v for v in versions if v.get('version_date')]
    history_start = part_row.get('versions_history_start') or (min(v['version_date'] for v in dated) if dated else None)
    block = {'requested': as_of, 'date': date, 'supported': False, 'reason': None, 'history_start': history_start,
             'metadata_captured_at': part_row.get('versions_captured_at') or (dated[0].get('captured_at') if dated else None),
             'basis': 'eCFR versioner per-section version metadata (version_date and removed flags) for this part',
             'caveat': 'Metadata only, not point-in-time text: the listing shows which section identifiers eCFR version metadata records as '
                       'present on that date; any text served is the current local GPO copy or the publisher snapshot.',
             'sections_without_version_metadata_omitted': 0, 'listed': 0}
    if not date:
        block['reason'] = 'as_of must be a calendar date written YYYY-MM-DD'
        return [], block
    if not dated or not history_start:
        block['reason'] = 'no eCFR version metadata for this part'
        return [], block
    if date < history_start:
        block['reason'] = 'as_of precedes the eCFR version history for this part (starts %s); earlier states are unknown' % history_start
        return [], block
    latest = {}
    for v in dated:
        if v['version_date'] <= date:
            latest[v['section']] = v
    versioned = {v['section'] for v in dated}
    by_section = {r['section']: r for r in current}
    listing = []
    for ident, v in latest.items():
        if v.get('removed'):
            continue
        row = by_section.get(ident)
        if row:
            item = _section_summary(row, title_row)
        else:
            heading = re.sub(r'^§+\s*' + re.escape(ident) + r'\s*', '', v.get('name') or '').strip() or None
            item = dict(_section_summary({'title': part_row['title'], 'part': part_row['part'], 'section': ident, 'heading': heading,
                                          'subpart': v.get('subpart')}, title_row), in_current_index=False, text_sources_available=[])
        item['as_of_state'] = {'version_date': v['version_date'], 'ecfr_amendment_date': v.get('amendment_date'),
                               'ecfr_issue_date': v.get('issue_date'), 'basis': block['basis']}
        listing.append(item)
    listing.sort(key=lambda r: _num_key(r['section']))
    omitted = sum(1 for r in current if r['section'] not in versioned)
    block.update(supported=True, sections_without_version_metadata_omitted=omitted, listed=len(listing))
    return listing, block


def section(citation):
    """One section by citation ('21 C.F.R. § 314.80', '21 CFR 314.80', 'cfr:21:314.80'): texts per source, history,
    printed amendment citations, related Federal Register documents and the build-time reconciliation. None when absent."""
    ref = _parse_ref(citation)
    if not ref or not ref['section']:
        return None
    state, _ = _state()
    if not state:
        return None
    try:
        with _connect(state) as db:
            if ref['title']:
                row = _one(db, 'SELECT * FROM section_index WHERE title = ? AND section = ?', (ref['title'], ref['section']))
            else:
                rows = _rows(db, 'SELECT * FROM section_index WHERE section = ?', (ref['section'],))
                row = rows[0] if len(rows) == 1 else None
            if not row:
                return None
            key = (row['title'], row['section'])
            title_row = _one(db, 'SELECT * FROM titles WHERE title = ?', (row['title'],)) or {}
            part_row = _one(db, 'SELECT * FROM parts WHERE title = ? AND part = ?', (row['title'], row['part'])) or {}
            out = _section_summary(row, title_row)
            out.update(part_heading=part_row.get('heading'), authority_note_as_printed=part_row.get('gpo_authority'),
                       source_note_as_printed=part_row.get('gpo_source_note'), title_name=title_row.get('name'))
            g = _one(db, 'SELECT * FROM gpo_sections WHERE title = ? AND section = ?', key)
            texts = [_gpo_text_public(g)] if g else []
            provisions = _rows(db, 'SELECT * FROM oul_provisions WHERE title = ? AND section = ? ORDER BY oul_rowid', key)
            if provisions:
                oul = _oul_open()
                try:
                    texts.extend(_oul_text_public(p, oul) for p in provisions)
                finally:
                    if oul is not None:
                        oul.close()
            out['texts'] = texts
            out['text_sources_available'] = [t['source'] for t in texts]
            out['amendment_citations_as_printed'] = _json(g.get('amendment_citations'), []) if g else []
            out['notes_as_printed'] = _json(g.get('notes'), []) if g else []
            out['history'] = [_history_public(v) for v in _rows(
                db, 'SELECT * FROM versions WHERE title = ? AND section = ? ORDER BY version_date, amendment_date, issue_date, rowid', key)]
            cites = _rows(db, 'SELECT * FROM section_fr_citations WHERE title = ? AND section = ? ORDER BY rowid', key)
            out['printed_fr_citations'] = [{'citation_printed': c.get('citation_printed'), 'volume': c.get('volume'), 'page': c.get('page'),
                                            'date_printed': c.get('date_printed'),
                                            'document_id': ('fr_doc:' + c['document_number']) if c.get('document_number') else None,
                                            'resolved': bool(c.get('document_number'))} for c in cites]
            out['related_fr_documents'] = _related_documents(db, row, cites)
            rec = _one(db, 'SELECT * FROM reconciliation WHERE title = ? AND section = ?', key)
            out['reconciliation'] = None if not rec else {
                'category': rec.get('category'), 'in_gpo': bool(rec.get('in_gpo')), 'in_oul': bool(rec.get('in_oul')), 'oul_rows': rec.get('oul_rows'),
                'in_ecfr_structure': bool(rec.get('in_ecfr_structure')),
                'heading_match': None if rec.get('heading_match') is None else bool(rec.get('heading_match')),
                'text_comparison': rec.get('text_comparison'), 'gpo_text_length': rec.get('gpo_text_length'),
                'oul_text_lengths': _json(rec.get('oul_text_lengths'), []), 'similarity': rec.get('similarity'),
                'basis': 'build-time comparison of the GPO eCFR XML text with the Open US Law text (whitespace-normalised, source credit stripped)'}
            return out
    except sqlite3.Error:
        return None


def _related_documents(db, row, cites):
    docs = {}
    cited = [c['document_number'] for c in cites if c.get('document_number')]
    if cited:
        for d in _rows(db, 'SELECT rowid, * FROM fr_documents WHERE document_number IN (%s)' % ','.join('?' * len(cited)), cited):
            docs[d['document_number']] = {'row': d, 'relations': {'cited_in_printed_amendment_note'}}
    part_rows = _rows(db, 'SELECT d.rowid, d.* FROM fr_documents d JOIN fr_doc_parts p ON p.document_number = d.document_number '
                          'WHERE p.title = ? AND p.part = ? ORDER BY (d.publication_date IS NULL), d.publication_date DESC, d.document_number',
                      (row['title'], row['part']))
    added = 0
    for d in part_rows:
        if d['document_number'] in docs:
            docs[d['document_number']]['relations'].add('fr_cfr_reference_to_part')
        elif added < RELATED_DOCS_LIMIT:
            docs[d['document_number']] = {'row': d, 'relations': {'fr_cfr_reference_to_part'}}
            added += 1
    ordered = sorted(docs.values(), key=lambda x: (x['row'].get('publication_date') or '', x['row']['document_number']), reverse=True)
    ordered.sort(key=lambda x: 0 if 'cited_in_printed_amendment_note' in x['relations'] else 1)
    part_listed = sum(1 for d in part_rows if d['document_number'] in docs)
    return {'documents': [dict(_doc_summary(x['row']), relations=sorted(x['relations'])) for x in ordered],
            'printed_citations_total': len(cites), 'printed_citations_resolved': len(cited),
            'part_documents_total': len(part_rows), 'part_documents_listed': part_listed, 'truncated': part_listed < len(part_rows),
            'more': "search(part='cfr:%s:%s', record_type='fr_document')" % (row['title'], row['part']),
            'relation_bases': {'cited_in_printed_amendment_note': 'printed "[NN FR PPPP, date]" note in the GPO eCFR XML resolved by volume and '
                                                                  'start page to a Federal Register document in the local slice',
                               'fr_cfr_reference_to_part': 'Federal Register API cfr_references lists this part (document-to-part, not to this section)'}}


def fr_document(document_number):
    """One Federal Register document ('2014-13414' or 'fr_doc:2014-13414'): publisher metadata with separate date labels. None when absent."""
    match = _DOCNUM.match(str(document_number).strip()) if isinstance(document_number, str) else None
    if not match:
        return None
    state, _ = _state()
    if not state:
        return None
    try:
        with _connect(state) as db:
            row = _one(db, 'SELECT rowid, * FROM fr_documents WHERE document_number = ?', (match.group(1),))
            return _doc_public(db, row) if row else None
    except sqlite3.Error:
        return None


document = fr_document


def agencies(slice_only=False, q=None):
    """Agencies from the eCFR admin agencies endpoint joined to the slice parts; those covering slice parts come first. [] when closed."""
    state, reason = _state()
    if not state:
        return _closed_list('agencies', reason)
    needle = str(q or '').strip().lower()
    rows = []
    for item in state['agencies']:
        if slice_only and not item.get('slice_parts'):
            continue
        if needle and needle not in ' '.join(str(item.get(k) or '') for k in ('name', 'display_name', 'short_name', 'slug')).lower():
            continue
        rows.append(item)
    rows.sort(key=lambda a: (0 if a.get('slice_parts') else 1, str(a.get('name') or a['slug']).lower()))
    return {'available': True, 'reason': None, 'total': len(rows), 'slice_only': bool(slice_only), 'q': needle or None,
            'basis': 'eCFR admin/v1/agencies.json cfr_references (title/chapter) joined to the eCFR structure part->chapter map',
            'agencies': [_agency_public(a) for a in rows]}


def search(q=None, title=None, part=None, agency=None, record_type=None, date_type=None, dfrom=None, dto=None, page=1, limit=DEFAULT_LIMIT):
    """Filtered, paginated search over sections and Federal Register documents.

    q: words (each a prefix, all required) matched against section headings, local GPO text, the Open US Law publisher text
    (read on demand; skipped when that index is unavailable) and Federal Register titles/metadata; malformed input never raises.
    title/part: restrict to a title or a part reference. agency: eCFR agency slug (sections via its slice parts; documents via the
    publisher's agency_slugs). record_type: 'section' or 'fr_document'. date_type + dfrom/dto: inclusive range on ONE explicitly
    named date (see DATE_TYPES); section date types exclude documents and vice versa; dfrom/dto without date_type is an error.
    """
    state, reason = _state()
    if not state:
        return _closed_search(reason)
    page, limit = _number(page, 1, 1, 10 ** 6), _number(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    filters = {'q': q or None, 'title': title or None, 'part': part or None, 'agency': agency or None, 'record_type': record_type or None,
               'date_type': date_type or None, 'dfrom': dfrom or None, 'dto': dto or None}
    base = {'available': True, 'reason': None, 'total': 0, 'page': page, 'limit': limit, 'pages': 0, 'results': [], 'filters': filters,
            'date_filter': None, 'publisher_index': {'available': None, 'searched': False, 'basis': 'publisher text index is consulted only when q is given'},
            'record_types': list(RECORD_TYPES),
            'order': 'sections in citation order, then Federal Register documents by publication date descending'}
    if record_type and record_type not in RECORD_TYPES:
        return dict(base, error='record_type must be one of ' + ', '.join(RECORD_TYPES))
    if date_type and date_type not in DATE_TYPES:
        return dict(base, error='unknown date_type; choose one of ' + ', '.join(DATE_TYPES))
    low, high = _iso_bound(dfrom), _iso_bound(dto, end=True)
    if low is False or high is False:
        return dict(base, error='dfrom/dto must be YYYY, YYYY-MM or YYYY-MM-DD')
    if (low or high) and not date_type:
        return dict(base, error='date_type is required with dfrom/dto; choose one of ' + ', '.join(DATE_TYPES))
    if date_type:
        base['date_filter'] = {'type': date_type, 'label': DATE_TYPES[date_type], 'from': low, 'to': high,
                               'applies_to': 'section' if date_type in SECTION_DATE_TYPES else 'fr_document'}
    title_f, part_f = None, None
    if part not in (None, ''):
        ref = _parse_ref(str(part), title)
        if not ref or not ref['title']:
            return dict(base, error='part must be cfr:<title>:<part>, <title>:<part>, "<title> CFR <part>" or <part> with title=')
        title_f, part_f = ref['title'], ref['part']
    elif title not in (None, ''):
        title_f = str(title).strip().lower().replace('cfr:', '')
        if not title_f.isdigit():
            return dict(base, error='title must be a CFR title number')
    agency_parts, agency_slug = None, None
    if agency not in (None, ''):
        agency_parts = _agency_parts(state, agency)
        if agency_parts is None:
            return base
        agency_slug = str(agency).strip().lower().replace('agency:', '')
    tokens = _tokens(q) if q not in (None, '') else None
    if q not in (None, '') and not tokens:
        return base
    want_sections = record_type in (None, '', 'section') and (not date_type or date_type in SECTION_DATE_TYPES)
    want_docs = record_type in (None, '', 'fr_document') and (not date_type or date_type in FR_DATE_TYPES)
    try:
        with _connect(state) as db:
            titles_by_id = _titles(db)
            results = []
            if want_sections:
                results += _search_sections(db, state, titles_by_id, tokens, title_f, part_f, agency_parts, date_type, low, high, base)
            if want_docs:
                results += _search_documents(db, tokens, title_f, part_f, agency_slug, date_type, low, high)
    except sqlite3.Error as error:
        return _closed_search('database_error:' + type(error).__name__)
    total = len(results)
    return dict(base, total=total, pages=math.ceil(total / limit) if total else 0, results=results[(page - 1) * limit:page * limit])


def _fts_ids(db, column, tokens, docs):
    try:
        bound = 'rowid >= %d' % FR_ROWID_OFFSET if docs else 'rowid < %d' % FR_ROWID_OFFSET
        return {r[0] for r in db.execute('SELECT rowid FROM search_fts WHERE search_fts MATCH ? AND ' + bound, (_fts_expr(column, tokens),))}
    except sqlite3.OperationalError:
        return set()


def _search_sections(db, state, titles_by_id, tokens, title_f, part_f, agency_parts, date_type, low, high, base):
    where, params = [], []
    if title_f:
        where.append('title = ?'); params.append(title_f)
    if part_f:
        where.append('part = ?'); params.append(part_f)
    rows = _rows(db, 'SELECT * FROM section_index' + ((' WHERE ' + ' AND '.join(where)) if where else ''), params)
    if agency_parts is not None:
        rows = [r for r in rows if (r['title'], r['part']) in agency_parts]
    matched = {}
    if tokens:
        heading_ids = _fts_ids(db, 'heading', tokens, docs=False)
        body_ids = _fts_ids(db, 'body', tokens, docs=False)
        oul_ids = _search_publisher(db, state, tokens, base)
        for r in rows:
            labels = [label for label, hit in (('heading', r['id'] in heading_ids), ('gpo_text', r['id'] in body_ids),
                                               ('open_us_law_text', r['id'] in oul_ids)) if hit]
            if labels:
                matched[r['id']] = labels
        rows = [r for r in rows if r['id'] in matched]
    if date_type in _SECTION_DATE_SQL and rows:
        dated = {r[0] for r in db.execute(_SECTION_DATE_SQL[date_type], (low or '0000-00-00', high or '9999-99-99'))}
        rows = [r for r in rows if r['id'] in dated]
    rows.sort(key=lambda r: (_num_key(r['title']), _num_key(r['part']), _num_key(r['section'])))
    return [dict(_section_summary(r, titles_by_id.get(r['title'], {})), matched_in=matched.get(r['id'], [])) for r in rows]


def _search_publisher(db, state, tokens, base):
    """Section ids whose Open US Law publisher text matches; empty when the index is unavailable (base['publisher_index'] says so)."""
    oul = _oul_open()
    base['publisher_index'] = {'available': oul is not None, 'searched': False,
                               'basis': 'Open US Law full-text index read on demand; hits point at publisher text whose hash is re-verified only when the section is opened'}
    if oul is None:
        return set()
    try:
        bounds = db.execute('SELECT min(oul_rowid), max(oul_rowid) FROM oul_provisions').fetchone()
        if not bounds or bounds[0] is None:
            return set()
        hits = {r[0] for r in oul.execute('SELECT rowid FROM records_fts WHERE records_fts MATCH ? AND rowid >= ? AND rowid <= ?',
                                          (_fts_expr('text', tokens), bounds[0], bounds[1]))}
        base['publisher_index']['searched'] = True
        if not hits:
            return set()
        by_rowid = {}
        for oul_rowid, sid in db.execute('SELECT o.oul_rowid, s.id FROM oul_provisions o JOIN section_index s ON s.title = o.title AND s.section = o.section'):
            by_rowid.setdefault(oul_rowid, set()).add(sid)
        return {sid for rowid in hits for sid in by_rowid.get(rowid, ())}
    except sqlite3.Error:
        return set()
    finally:
        oul.close()


def _search_documents(db, tokens, title_f, part_f, agency_slug, date_type, low, high):
    where, params = [], []
    if part_f:
        where.append('d.document_number IN (SELECT document_number FROM fr_doc_parts WHERE title = ? AND part = ?)'); params += [title_f, part_f]
    elif title_f:
        where.append('d.document_number IN (SELECT document_number FROM fr_doc_parts WHERE title = ?)'); params.append(title_f)
    if date_type in _FR_DATE_COLUMN:
        column = _FR_DATE_COLUMN[date_type]
        where.append('d.%s >= ? AND d.%s <= ?' % (column, column)); params += [low or '0000-00-00', high or '9999-99-99']
    rows = _rows(db, 'SELECT d.rowid, d.* FROM fr_documents d' + ((' WHERE ' + ' AND '.join(where)) if where else ''), params)
    if agency_slug is not None:
        rows = [r for r in rows if agency_slug in [s.lower() for s in _json(r.get('agency_slugs'), []) if isinstance(s, str)]]
    matched = {}
    if tokens:
        heading_ids = _fts_ids(db, 'heading', tokens, docs=True)
        body_ids = _fts_ids(db, 'body', tokens, docs=True)
        for r in rows:
            fts_id = FR_ROWID_OFFSET + r['rowid']
            labels = [label for label, hit in (('fr_title', fts_id in heading_ids), ('fr_metadata', fts_id in body_ids)) if hit]
            if labels:
                matched[r['rowid']] = labels
        rows = [r for r in rows if r['rowid'] in matched]
    rows.sort(key=lambda r: r['document_number'])
    rows.sort(key=lambda r: (r.get('publication_date') or ''), reverse=True)
    return [dict(_doc_summary(r), matched_in=matched.get(r['rowid'], [])) for r in rows]
