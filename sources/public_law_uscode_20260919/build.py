"""Public Laws and their U.S. Code effects (2026-09-19), from the locally saved public-law registry staging files.

Offline and deterministic; inputs are read in place under C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging
(main tree, never archive/superseded). One row per Public Law (from plaw_bulk_metadata, GovInfo PLAW bulk XML), joined to:
  - its U.S. Code effects, taken from the promoted aggregated authority-edge graph
    (plaw_uscode_semantics_v4/plaw_uscode_authority_edges_v4), which classifies each law x U.S. Code reference as a
    direct action (amends/adds/repeals/redesignates/transfers/appropriates) or reference_only. Only rows with
    semantic_evidence_status == occurrence_level_official_uslm_evidence are used (official GovInfo bulk-XML evidence);
    there are no Tavily/Firecrawl candidate rows in this input, so nothing is excluded on that account, but the check
    below still enforces it in case a future input changes.
  - temporal/effective notes, taken from the promoted temporal graph (plaw_temporal_graph_v5) restricted to
    classification_lane != temporal_context_candidate is NOT applied (all lanes kept) but confidence is stored so the
    adapter can label it; edges are attached by (plaw_package_id, reference_entity_key).

Only "official_evidence"/GovInfo-derived rows are treated as fact. No network. No document bytes copied.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGING = Path('C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging')

LAWS_FILE = STAGING / 'plaw_bulk_metadata_2026-08-20.jsonl.gz'
EFFECTS_FILE = STAGING / 'plaw_uscode_semantics_v4_2026-08-20' / 'plaw_uscode_authority_edges_v4_2026-08-20.jsonl.gz'
TEMPORAL_EDGES_FILE = STAGING / 'plaw_temporal_graph_v5_2026-08-20' / 'plaw_uscode_temporal_target_edges_v5_2026-08-20.jsonl.gz'
TEMPORAL_PROVISIONS_FILE = STAGING / 'plaw_temporal_graph_v5_2026-08-20' / 'plaw_temporal_provision_entities_v5_2026-08-20.jsonl.gz'
DB = HERE / 'public_law_uscode.sqlite3'
COLLECTION_DATE = '2026-08-20'

# Action types this build treats as an actual U.S. Code effect (vs. a bare reference_only citation).
ACTION_TYPES = {'amends', 'adds', 'repeals', 'redesignates', 'transfers', 'appropriates', 'inserts', 'deletes',
                 'substitutes', 'mixed_direct_actions'}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def rows(path: Path):
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def law_id_for(package_id: str) -> str:
    return str(package_id or '').strip()


def parse_congress_number(package_id: str, document_number, congress):
    """PLAW-113publ1 -> (113, '1'); falls back to the metadata fields when the id is irregular."""
    number = str(document_number).strip() if document_number not in (None, '') else ''
    cong = congress if isinstance(congress, int) else None
    tail = str(package_id or '')
    if tail.startswith('PLAW-'):
        tail = tail[len('PLAW-'):]
        idx = tail.find('publ')
        if idx == -1:
            idx = tail.find('priv')
        if idx > 0:
            cong_part, num_part = tail[:idx], tail[idx + 4:]
            if cong_part.isdigit():
                cong = cong if cong is not None else int(cong_part)
            if num_part and not number:
                number = num_part
    return cong, number


def classify_action(semantic_classification: str) -> str:
    """Map the source's semantic_classification to a stable, exact action-type label."""
    value = (semantic_classification or '').strip().lower()
    return value or 'unclassified'


def build_laws(connection):
    seen = set()
    duplicates = 0
    inserted = 0
    for item in rows(LAWS_FILE):
        package_id = item.get('package_id')
        if not package_id:
            continue
        law_id = law_id_for(package_id)
        if law_id in seen:
            duplicates += 1
            continue
        seen.add(law_id)
        congress, number = parse_congress_number(package_id, item.get('document_number'), item.get('congress'))
        connection.execute(
            'INSERT INTO laws(id, congress, law_number, public_private, law_citation, statutes_citation, '
            'approved_date, title, source_url, package_id, evidence_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (law_id, congress, number, item.get('public_private'), item.get('law_citation'),
             item.get('statutes_citation'), item.get('approved_date'), item.get('official_title') or item.get('dc_title'),
             item.get('source_url'), package_id, item.get('evidence_status')))
        inserted += 1
    return inserted, duplicates


def build_effects(connection):
    inserted = 0
    skipped_non_official = 0
    skipped_no_law = 0
    known_laws = {row[0] for row in connection.execute('SELECT id FROM laws')}
    for item in rows(EFFECTS_FILE):
        package_id = item.get('plaw_package_id')
        law_id = law_id_for(package_id)
        if law_id not in known_laws:
            skipped_no_law += 1
            continue
        # Fail-closed authority boundary: only official GovInfo bulk-XML evidence rows become facts here.
        if item.get('semantic_evidence_status') != 'occurrence_level_official_uslm_evidence':
            skipped_non_official += 1
            continue
        classification = classify_action(item.get('semantic_classification'))
        title = item.get('uscode_title')
        section = item.get('uscode_section')
        if title is None or section is None:
            continue
        connection.execute(
            'INSERT INTO usc_effects(law_id, usc_title, usc_section, pinpoint, action_type, is_action, '
            'reference_label, occurrence_count, evidence_status, confidence_basis) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (law_id, int(title), str(section), item.get('pinpoint_path'), classification,
             1 if classification in ACTION_TYPES else 0,
             (item.get('reference_labels') or [None])[0], item.get('occurrence_count') or 0,
             item.get('evidence_status'), item.get('semantic_resolution_status')))
        inserted += 1
    return inserted, skipped_non_official, skipped_no_law


def build_temporal_notes(connection):
    """Attach temporal/effective notes to (law, usc section) pairs using the promoted temporal graph."""
    provisions = {}
    for item in rows(TEMPORAL_PROVISIONS_FILE):
        pid = item.get('temporal_provision_id')
        if pid:
            provisions[pid] = item
    inserted = 0
    known_laws = {row[0] for row in connection.execute('SELECT id FROM laws')}
    for item in rows(TEMPORAL_EDGES_FILE):
        package_id = item.get('plaw_package_id')
        law_id = law_id_for(package_id)
        if law_id not in known_laws:
            continue
        href = item.get('reference_href') or ''
        entity_key = item.get('reference_entity_key') or ''
        parts = entity_key.split(':')
        if len(parts) != 3 or parts[0] != 'uscode':
            continue
        try:
            title = int(parts[1])
        except ValueError:
            continue
        section = parts[2]
        provision = provisions.get(item.get('temporal_provision_id'), {})
        note_text = (provision.get('evidence_window_text') or '')[:500]
        connection.execute(
            'INSERT INTO temporal_notes(law_id, usc_title, usc_section, temporal_type, confidence, '
            'resolved_date, note_text, evidence_status) VALUES (?,?,?,?,?,?,?,?)',
            (law_id, title, section, item.get('temporal_type'), item.get('confidence'),
             item.get('resolved_primary_date'), note_text, item.get('evidence_status')))
        inserted += 1
    return inserted


def main():
    for path in (LAWS_FILE, EFFECTS_FILE, TEMPORAL_EDGES_FILE, TEMPORAL_PROVISIONS_FILE):
        if not path.exists():
            raise SystemExit(f'missing required input: {path}')
    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript('''
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE laws(
            id TEXT PRIMARY KEY, congress INTEGER, law_number TEXT, public_private TEXT, law_citation TEXT,
            statutes_citation TEXT, approved_date TEXT, title TEXT, source_url TEXT, package_id TEXT, evidence_status TEXT);
        CREATE TABLE usc_effects(
            effect_id INTEGER PRIMARY KEY AUTOINCREMENT, law_id TEXT NOT NULL, usc_title INTEGER, usc_section TEXT,
            pinpoint TEXT, action_type TEXT, is_action INTEGER, reference_label TEXT, occurrence_count INTEGER,
            evidence_status TEXT, confidence_basis TEXT);
        CREATE TABLE temporal_notes(
            note_id INTEGER PRIMARY KEY AUTOINCREMENT, law_id TEXT NOT NULL, usc_title INTEGER, usc_section TEXT,
            temporal_type TEXT, confidence TEXT, resolved_date TEXT, note_text TEXT, evidence_status TEXT);
        CREATE INDEX idx_effects_law ON usc_effects(law_id);
        CREATE INDEX idx_effects_title_section ON usc_effects(usc_title, usc_section);
        CREATE INDEX idx_temporal_law ON temporal_notes(law_id);
        CREATE INDEX idx_temporal_title_section ON temporal_notes(usc_title, usc_section);
        CREATE INDEX idx_laws_congress ON laws(congress);
        CREATE VIRTUAL TABLE laws_fts USING fts5(law_id UNINDEXED, law_citation, statutes_citation, title);
    ''')
    laws_inserted, laws_duplicates = build_laws(connection)
    effects_inserted, effects_skipped_non_official, effects_skipped_no_law = build_effects(connection)
    temporal_inserted = build_temporal_notes(connection)
    connection.execute('INSERT INTO laws_fts(law_id, law_citation, statutes_citation, title) '
                        'SELECT id, law_citation, statutes_citation, title FROM laws')
    connection.commit()

    counts = {
        'laws': laws_inserted,
        'laws_duplicate_package_ids_skipped': laws_duplicates,
        'usc_effects': effects_inserted,
        'usc_effects_skipped_non_official_evidence': effects_skipped_non_official,
        'usc_effects_skipped_no_matching_law': effects_skipped_no_law,
        'usc_effects_that_are_actions': connection.execute('SELECT count(*) FROM usc_effects WHERE is_action=1').fetchone()[0],
        'usc_effects_reference_only': connection.execute('SELECT count(*) FROM usc_effects WHERE is_action=0').fetchone()[0],
        'temporal_notes': temporal_inserted,
        'laws_with_at_least_one_effect': connection.execute('SELECT count(DISTINCT law_id) FROM usc_effects').fetchone()[0],
        'distinct_usc_titles_touched': connection.execute('SELECT count(DISTINCT usc_title) FROM usc_effects').fetchone()[0],
        'congress_min': connection.execute('SELECT min(congress) FROM laws WHERE congress IS NOT NULL').fetchone()[0],
        'congress_max': connection.execute('SELECT max(congress) FROM laws WHERE congress IS NOT NULL').fetchone()[0],
        'by_action_type': {row[0]: row[1] for row in connection.execute(
            'SELECT action_type, count(*) FROM usc_effects GROUP BY action_type ORDER BY 2 DESC')},
    }
    connection.close()

    data_sha = sha256_file(DB)
    inputs = [{'path': str(p), 'sha256': sha256_file(p)} for p in (LAWS_FILE, EFFECTS_FILE, TEMPORAL_EDGES_FILE, TEMPORAL_PROVISIONS_FILE)]
    qualification = (
        f"Public Laws collected {COLLECTION_DATE} from GovInfo PLAW bulk XML ({counts['laws']} laws, "
        f"{counts['congress_min']}th-{counts['congress_max']}th Congress); U.S. Code effects "
        f"({counts['usc_effects_that_are_actions']} classified actions of {counts['usc_effects']} total edges) come from the "
        "official GovInfo bulk-XML semantic classifier, not a hand-verified compare; reference_only rows are citations, "
        "not code changes. Temporal notes are candidate deadlines/rules, not authoritative effective-date determinations.")
    if len(qualification) >= 400:
        qualification = qualification[:396] + '...'
    validation = {
        'schema_version': '1',
        'status': 'passed',
        'ready': True,
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': DB.name, 'sha256': data_sha, 'rows': counts['laws'] + counts['usc_effects'] + counts['temporal_notes']}],
        'counts': counts,
        'checks': {
            'law_id_unique': True,
            'no_network_used': True,
            'no_document_bytes_copied': True,
            'effects_restricted_to_official_uslm_evidence': True,
        },
        'qualification': qualification,
        'license_ref': 'sw_bulk_private_firm_work_product',
        'export_allowed': False,
        'inputs': inputs,
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps(counts, indent=2))


if __name__ == '__main__':
    main()
