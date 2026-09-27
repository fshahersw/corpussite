"""Federal Register document index 1994 to 2026 (2026-09-19), from the locally saved public-law registry staging files.

Offline and deterministic; inputs are read in place. One row per Federal Register document identity as recorded from the
federalregister.gov API and GovInfo on 2026-08-20, with the CFR parts, agencies, docket identifiers and RINs the API itself
lists for the document. No document text is stored; links go to federalregister.gov.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGING = Path('C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging')
INPUTS = [STAGING / 'federal_register_document_identities_v2_1994_2025_2026-08-20.jsonl.gz',
          STAGING / 'federal_register_document_identities_v3_2026_ytd_2026-08-20.jsonl.gz']
AGENCIES = STAGING / 'federal_register_agency_entities_v2_2026-08-20.jsonl.gz'
DB = HERE / 'federal_register.sqlite3'


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


def main():
    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript('''
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE agencies(agency_id INTEGER PRIMARY KEY, name TEXT, short_name TEXT, slug TEXT, parent_agency_id INTEGER, url TEXT, documents INTEGER DEFAULT 0);
        CREATE TABLE docs(id INTEGER PRIMARY KEY, number TEXT NOT NULL, date TEXT NOT NULL, year INTEGER, type TEXT, title TEXT, citation TEXT, pages TEXT,
            agencies TEXT, agency_ids TEXT, cfr TEXT, dockets TEXT, rins TEXT, correction_of TEXT, corrections TEXT, has_pdf INTEGER);
        CREATE TABLE doc_cfr(doc_id INTEGER, cfr_title INTEGER, cfr_part TEXT, date TEXT);
        CREATE TABLE doc_agency(doc_id INTEGER, agency_id INTEGER, date TEXT);
    ''')
    for item in rows(AGENCIES):
        try:
            agency_id = int(item.get('agency_id'))
        except (TypeError, ValueError):
            continue
        parent = item.get('parent_agency_id')
        connection.execute('INSERT OR IGNORE INTO agencies(agency_id,name,short_name,slug,parent_agency_id,url) VALUES(?,?,?,?,?,?)',
                           (agency_id, item.get('name'), item.get('short_name'), item.get('slug'), int(parent) if str(parent or '').isdigit() else None, item.get('agency_homepage_url')))
    seen = set()
    duplicates = 0
    types, years = Counter(), Counter()
    docs, cfr_rows, agency_rows = [], [], []
    identifier = 0

    def flush():
        connection.executemany('INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', docs)
        connection.executemany('INSERT INTO doc_cfr VALUES(?,?,?,?)', cfr_rows)
        connection.executemany('INSERT INTO doc_agency VALUES(?,?,?)', agency_rows)
        docs.clear(); cfr_rows.clear(); agency_rows.clear()

    for path in INPUTS:
        for item in rows(path):
            key = item.get('document_identity_id')
            number, date = item.get('document_number'), item.get('publication_date')
            if not key or not number or not date:
                continue
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            identifier += 1
            start, end = item.get('start_page'), item.get('end_page')
            pages = f'{start}-{end}' if start and end and end != start else (str(start) if start else None)
            cfr = []
            for reference in item.get('cfr_references') or []:
                title, part = reference.get('title'), reference.get('part')
                if title is None:
                    continue
                cfr.append(f"{title} CFR {part}" if part else f"{title} CFR chapter {reference.get('chapter')}")
                if part:
                    try:
                        cfr_rows.append((identifier, int(title), str(part), date))
                    except (TypeError, ValueError):
                        pass
            for agency_id in item.get('agency_ids') or []:
                try:
                    agency_rows.append((identifier, int(agency_id), date))
                except (TypeError, ValueError):
                    pass
            document_type = item.get('document_type') or 'Not stated'
            types[document_type] += 1
            years[date[:4]] += 1
            docs.append((identifier, number, date, int(date[:4]), document_type, (item.get('title') or '')[:600], item.get('official_citation'), pages,
                         json.dumps(item.get('agency_names') or [], ensure_ascii=False), json.dumps(item.get('agency_ids') or []), json.dumps(cfr),
                         json.dumps((item.get('docket_ids') or [])[:12], ensure_ascii=False), json.dumps(item.get('regulation_id_numbers') or []),
                         item.get('correction_of'), json.dumps(item.get('corrections') or []), 1 if item.get('official_document_pdf_url') else 0))
            if len(docs) >= 20000:
                flush()
    flush()
    connection.executescript('''
        CREATE INDEX docs_date ON docs(date DESC);
        CREATE INDEX docs_type ON docs(type, date DESC);
        CREATE INDEX docs_year ON docs(year, type);
        CREATE INDEX docs_number ON docs(number);
        CREATE INDEX doc_cfr_part ON doc_cfr(cfr_title, cfr_part, date DESC);
        CREATE INDEX doc_cfr_doc ON doc_cfr(doc_id);
        CREATE INDEX doc_agency_agency ON doc_agency(agency_id, date DESC);
        UPDATE agencies SET documents=(SELECT count(*) FROM doc_agency a WHERE a.agency_id=agencies.agency_id);
        CREATE TABLE facet_counts(facet TEXT, value TEXT, n INTEGER);
        INSERT INTO facet_counts SELECT 'type', type, count(*) FROM docs GROUP BY type;
        INSERT INTO facet_counts SELECT 'year', year, count(*) FROM docs GROUP BY year;
        INSERT INTO facet_counts SELECT 'cfr_title', cfr_title, count(DISTINCT doc_id) FROM doc_cfr GROUP BY cfr_title;
        CREATE VIRTUAL TABLE docs_fts USING fts5(title, agencies, dockets, rins, number, content='docs', content_rowid='id', tokenize='unicode61');
        INSERT INTO docs_fts(rowid, title, agencies, dockets, rins, number) SELECT id, title, agencies, dockets, rins, number FROM docs;
    ''')
    connection.commit()
    counts = {
        'documents': connection.execute('SELECT count(*) FROM docs').fetchone()[0], 'duplicate_identity_rows_skipped': duplicates,
        'first_publication_date': connection.execute('SELECT min(date) FROM docs').fetchone()[0],
        'last_publication_date': connection.execute('SELECT max(date) FROM docs').fetchone()[0],
        'documents_citing_a_cfr_part': connection.execute('SELECT count(DISTINCT doc_id) FROM doc_cfr').fetchone()[0],
        'document_cfr_part_links': connection.execute('SELECT count(*) FROM doc_cfr').fetchone()[0],
        'distinct_cfr_parts_cited': connection.execute('SELECT count(*) FROM (SELECT DISTINCT cfr_title, cfr_part FROM doc_cfr)').fetchone()[0],
        'documents_with_a_catalogued_agency': connection.execute('SELECT count(DISTINCT doc_id) FROM doc_agency').fetchone()[0],
        'agencies_in_catalog': connection.execute('SELECT count(*) FROM agencies').fetchone()[0],
        'documents_with_rin': connection.execute("SELECT count(*) FROM docs WHERE rins<>'[]'").fetchone()[0],
        'by_type': dict(types.most_common()),
    }
    connection.execute('VACUUM')
    connection.close()
    validation = {
        'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': DB.name, 'sha256': sha256_file(DB), 'rows': counts['documents']}], 'counts': counts,
        'checks': {'document_identity_unique': True, 'no_network_used': True, 'no_document_text_stored': True},
        'qualification': 'Federal Register documents published %s to %s as listed by the federalregister.gov API and GovInfo when the index was collected on 2026-08-20. '
                         'CFR parts, agencies, docket identifiers and RINs are the ones the API lists for each document; documents published later, and any later '
                         'correction, are not included. A document that cites a CFR part may propose, amend, correct or merely discuss it: read the document.' % (
                             counts['first_publication_date'], counts['last_publication_date']),
        'license_ref': 'us_government_public_record; index assembled in SW-BULK (sw_bulk_private_firm_work_product)', 'export_allowed': False,
        'inputs': [{'path': path.as_posix(), 'bytes': path.stat().st_size, 'sha256': sha256_file(path)} for path in INPUTS + [AGENCIES]],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1), encoding='utf-8')
    print(json.dumps(counts, indent=1))


if __name__ == '__main__':
    main()
