"""Import six pinned local CourtListener CSV tables, preserving raw string fields.

No network calls, external loader execution, source writes, or entity merges.
Run with Python 3.11+. The ready marker is written last, after atomic publication.
"""
from __future__ import annotations

import argparse
import bz2
import csv
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

OUT = Path(__file__).resolve().parent
WORKSPACE = OUT.parent.parent
DEFAULT_AUDIT = WORKSPACE / 'sources/local_deep_audit_20260918/judges/returned_bulk.json'
TABLE_FILES = {
    'people': 'people-db-people-2026-06-30.csv.bz2',
    'positions': 'people-db-positions-2026-06-30.csv.bz2',
    'educations': 'people-db-educations-2026-06-30.csv.bz2',
    'schools': 'people-db-schools-2026-06-30.csv.bz2',
    'political_affiliations': 'people-db-political-affiliations-2026-06-30.csv.bz2',
    'courts': 'courts-2026-06-30.csv.bz2',
}
# Exact namespace relationships verified in schema-2026-06-30.sql. In particular,
# appointer_id is a POSITION id, unlike predecessor_id and supervisor_id.
REFERENCES = [
    ('people', 'is_alias_of_id', 'people'),
    ('schools', 'is_alias_of_id', 'schools'),
    ('courts', 'parent_court_id', 'courts'),
    ('positions', 'person_id', 'people'),
    ('positions', 'appointer_id', 'positions'),
    ('positions', 'predecessor_id', 'people'),
    ('positions', 'supervisor_id', 'people'),
    ('positions', 'court_id', 'courts'),
    ('positions', 'school_id', 'schools'),
    ('educations', 'person_id', 'people'),
    ('educations', 'school_id', 'schools'),
    ('political_affiliations', 'person_id', 'people'),
]
CAUTIONS = [
    'People include historical records, aliases and non-judge roles; rows are not a census of current judges.',
    'Raw dates and date-granularity fields are preserved. No current service determination is inferred.',
    'has_photo is a publisher availability flag, not a verified image URL or downloaded portrait.',
    'Court/service positions are historical observations. A court association does not imply present service.',
    'CourtListener person IDs, legacy fjc_id values and FJC nid identifiers are distinct namespaces.',
    'No existing judge entities were merged; no disclosure, opinion, race or extra tables were imported.',
]


class ImportErrorChecked(RuntimeError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def identifier(name):
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', name):
        raise ImportErrorChecked(f'Unsupported schema identifier: {name!r}')
    return '"' + name + '"'


def atomic_json(path, payload):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def writer_lock(path):
    """Exclusive OS lock; process termination releases it without stale PID files."""
    stream = Path(path).open('a+b')
    locked = False
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise ImportErrorChecked('Another importer holds the publication writer lock') from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def pinned_manifest(audit_path=DEFAULT_AUDIT):
    audit_path = Path(audit_path).resolve()
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    by_name = {Path(row['path']).name: row for row in audit['tables']}
    tables = {}
    for table, filename in TABLE_FILES.items():
        item = by_name[filename]
        if item['rows_counted'] is None or item['rows_with_malformed_column_count']:
            raise ImportErrorChecked(f'Unvalidated source table: {table}')
        source = Path(item['path'])
        if source.stat().st_size != item['compressed_bytes'] or digest(source) != item['sha256']:
            raise ImportErrorChecked(f'Pinned source hash/size mismatch: {table}')
        tables[table] = {
            'path': source.as_posix(), 'sha256': item['sha256'],
            'compressed_bytes': item['compressed_bytes'],
            'expected_rows': item['rows_counted'], 'columns': item['columns'],
        }
    schema = Path(audit['root']) / 'schema-2026-06-30.sql'
    return {
        'schema_version': 'courtlistener-people-source-manifest.v1',
        'source_snapshot': '2026-06-30',
        'snapshot_basis': 'Local filenames and supplied schema/loader; no fresh provider request.',
        'audit_path': audit_path.as_posix(), 'audit_sha256': digest(audit_path),
        'source_schema_path': schema.as_posix(), 'source_schema_sha256': digest(schema),
        'csv_dialect': {'encoding': 'utf-8', 'escapechar': '\\', 'doublequote': False, 'strict': True},
        'tables': tables,
        'foreign_key_contract': [{'table': a, 'column': b, 'target_table': c, 'target_column': 'id'} for a, b, c in REFERENCES],
    }


def create_raw_table(connection, table, columns):
    if not columns or columns[0] != 'id' or len(set(columns)) != len(columns):
        raise ImportErrorChecked(f'Invalid table schema: {table}')
    fields = [identifier(c) + (' TEXT PRIMARY KEY NOT NULL CHECK(id <> \'\')' if c == 'id' else " TEXT NOT NULL") for c in columns]
    # Generated nullable reference columns preserve raw empty strings while enabling
    # actual SQLite FK enforcement. They are excluded from raw profile dictionaries.
    refs = [(col, target) for owner, col, target in REFERENCES if owner == table]
    for col, target in refs:
        fields.append(identifier('_ref_' + col) + ' TEXT GENERATED ALWAYS AS (NULLIF(' + identifier(col) + ", '')) VIRTUAL")
    for col, target in refs:
        fields.append('FOREIGN KEY (' + identifier('_ref_' + col) + ') REFERENCES ' + identifier(target) + '(id) DEFERRABLE INITIALLY DEFERRED')
    connection.execute('CREATE TABLE ' + identifier(table) + ' (' + ','.join(fields) + ')')


def import_table(connection, table, spec):
    path = Path(spec['path'])
    if digest(path) != spec['sha256']:
        raise ImportErrorChecked(f'Pinned source hash mismatch before import: {table}')
    columns = spec['columns']
    sql = 'INSERT INTO ' + identifier(table) + '(' + ','.join(identifier(c) for c in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
    count = 0
    csv.field_size_limit(50_000_000)
    with bz2.open(path, 'rt', encoding='utf-8', newline='') as stream:
        reader = csv.reader(stream, escapechar='\\', doublequote=False, strict=True)
        if next(reader, None) != columns:
            raise ImportErrorChecked(f'CSV header mismatch: {table}')
        batch = []
        for row in reader:
            count += 1
            if len(row) != len(columns):
                raise ImportErrorChecked(f'CSV row width mismatch: {table}, row {count}')
            batch.append(row)
            if len(batch) == 1000:
                connection.executemany(sql, batch)
                batch.clear()
        if batch:
            connection.executemany(sql, batch)
    if count != spec['expected_rows']:
        raise ImportErrorChecked(f'Row count mismatch: {table}: {count} != {spec["expected_rows"]}')
    if digest(path) != spec['sha256']:
        raise ImportErrorChecked(f'Source changed during import: {table}')
    return count


def build_database(path, manifest):
    path = Path(path)
    if path.exists():
        raise ImportErrorChecked('Build target must be new')
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('BEGIN IMMEDIATE')
        for table, spec in manifest['tables'].items():
            create_raw_table(connection, table, spec['columns'])
        # SQLite probes child reference columns while deferred parents arrive.
        # Index the generated FK columns before inserting to avoid quadratic scans.
        for table, column, target in REFERENCES:
            connection.execute('CREATE INDEX ' + identifier('fk_' + table + '_' + column) + ' ON ' + identifier(table) + '(' + identifier('_ref_' + column) + ')')
        counts = {table: import_table(connection, table, spec) for table, spec in manifest['tables'].items()}
        connection.execute('CREATE TABLE source_files (table_name TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, compressed_bytes INTEGER NOT NULL, rows INTEGER NOT NULL, columns_json TEXT NOT NULL)')
        for table, spec in manifest['tables'].items():
            connection.execute('INSERT INTO source_files VALUES (?,?,?,?,?,?)', (table, spec['path'], spec['sha256'], spec['compressed_bytes'], counts[table], json.dumps(spec['columns'])))
        connection.execute('CREATE TABLE import_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)')
        for key, value in {'source_snapshot': manifest['source_snapshot'], 'schema_version': 'courtlistener-people-catalog.v1', 'cautions': CAUTIONS, 'manifest': manifest}.items():
            connection.execute('INSERT INTO import_metadata VALUES (?,?)', (key, json.dumps(value)))
        for table, column, target in REFERENCES:
            connection.execute('CREATE INDEX ' + identifier('idx_' + table + '_' + column) + ' ON ' + identifier(table) + '(' + identifier(column) + ')')
        connection.execute('CREATE INDEX idx_people_name ON people(name_last COLLATE NOCASE, name_first COLLATE NOCASE, id)')
        connection.execute('CREATE INDEX idx_people_fjc ON people(fjc_id)')
        connection.execute('CREATE INDEX idx_positions_person_court ON positions(person_id,court_id)')
        connection.execute('CREATE INDEX idx_positions_court_person ON positions(court_id,person_id)')
        connection.execute('CREATE TABLE people_search (person_id TEXT PRIMARY KEY REFERENCES people(id), display_name TEXT NOT NULL, search_text TEXT NOT NULL, has_photo INTEGER NOT NULL, is_alias INTEGER NOT NULL)')
        search_rows = []
        for row in connection.execute('SELECT id,name_first,name_middle,name_last,name_suffix,slug,has_photo,is_alias_of_id FROM people'):
            name = ' '.join(row[k] for k in ('name_first', 'name_middle', 'name_last', 'name_suffix') if row[k]).strip()
            search_rows.append((row['id'], name, name + ' ' + row['slug'], int(row['has_photo'] == 't'), int(bool(row['is_alias_of_id']))))
        connection.executemany('INSERT INTO people_search VALUES (?,?,?,?,?)', search_rows)
        connection.execute('CREATE INDEX idx_people_search_name ON people_search(display_name COLLATE NOCASE,person_id)')
        connection.execute("CREATE VIRTUAL TABLE people_fts USING fts5(search_text,content='people_search',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2')")
        connection.execute("INSERT INTO people_fts(people_fts) VALUES ('rebuild')")
        connection.execute("INSERT INTO people_fts(people_fts,rank) VALUES ('integrity-check',1)")
        connection.execute('CREATE VIEW position_profiles AS SELECT p.*, c.full_name AS court_full_name, c.jurisdiction AS court_jurisdiction, a.person_id AS appointer_person_id, s.display_name AS appointer_display_name FROM positions p LEFT JOIN courts c ON p.court_id=c.id LEFT JOIN positions a ON p.appointer_id=a.id LEFT JOIN people_search s ON a.person_id=s.person_id')
        foreign_key_errors = [list(row) for row in connection.execute('PRAGMA foreign_key_check')]
        if foreign_key_errors:
            raise ImportErrorChecked(f'Foreign-key errors: {foreign_key_errors[:5]}')
        connection.commit()
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise ImportErrorChecked(f'SQLite integrity check failed: {integrity}')
        refs = []
        for table, column, target in REFERENCES:
            populated = connection.execute('SELECT count(*) FROM ' + identifier(table) + ' WHERE ' + identifier(column) + " <> ''").fetchone()[0]
            refs.append({'table': table, 'column': column, 'target_table': target, 'populated_references': populated, 'unresolved_references': 0})
        summary = {
            'schema_version': 'courtlistener-people-summary.v1', 'completed_at': now(),
            'source_snapshot': manifest['source_snapshot'], 'counts': counts,
            'people_with_photo_flags': connection.execute('SELECT count(*) FROM people_search WHERE has_photo=1').fetchone()[0],
            'people_with_alias_reference': connection.execute('SELECT count(*) FROM people_search WHERE is_alias=1').fetchone()[0],
            'people_with_fjc_id': connection.execute("SELECT count(*) FROM people WHERE fjc_id<>''").fetchone()[0],
            'people_with_death_date': connection.execute("SELECT count(*) FROM people WHERE date_dod<>''").fetchone()[0],
            'people_with_court_position': connection.execute("SELECT count(DISTINCT person_id) FROM positions WHERE court_id<>''").fetchone()[0],
            'court_position_counts': [dict(row) for row in connection.execute("SELECT c.id,c.full_name,c.jurisdiction,count(*) AS position_rows,count(DISTINCT p.person_id) AS people_count FROM positions p JOIN courts c ON p.court_id=c.id GROUP BY c.id ORDER BY c.full_name")],
            'position_type_counts': dict(connection.execute('SELECT position_type,count(*) FROM positions GROUP BY position_type')),
            'new_portrait_bytes': 0, 'existing_entity_merges': 0, 'cautions': CAUTIONS,
        }
        validation = {
            'verified_at': now(), 'passed': True, 'raw_table_counts': counts,
            'input_hashes_match_before_and_after_import': True,
            'csv_headers_and_all_row_widths_match': True, 'native_primary_keys_unique': True,
            'foreign_key_errors': foreign_key_errors, 'foreign_key_checks': refs,
            'sqlite_integrity_check': integrity, 'fts_external_content_integrity_check': 'passed',
            'search_rows': connection.execute('SELECT count(*) FROM people_search').fetchone()[0],
            'network_requests': 0, 'entity_merges': 0,
        }
        return summary, validation
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def existing_publication_valid(base, manifest, ready):
    base = Path(base)
    final = base / 'catalog.sqlite3'
    if ready.get('ready') is not True or ready.get('schema_version') != 'courtlistener-people-catalog.v1':
        return False
    try:
        if final.stat().st_size != ready.get('database_bytes') or digest(final) != ready.get('database_sha256'):
            return False
        if digest(base / 'source_manifest.json') != ready.get('source_manifest_sha256'):
            return False
        if json.loads((base / 'source_manifest.json').read_text(encoding='utf-8')) != manifest:
            return False
        for filename, key in [('summary.json', 'summary'), ('validation.json', 'validation')]:
            expected = (base / filename).resolve()
            if Path(ready.get(key + '_path', '')).resolve() != expected or digest(expected) != ready.get(key + '_sha256'):
                return False
        summary = json.loads((base / 'summary.json').read_text(encoding='utf-8'))
        validation = json.loads((base / 'validation.json').read_text(encoding='utf-8'))
        counts = {name: item['expected_rows'] for name, item in manifest['tables'].items()}
        return (summary.get('counts') == counts == validation.get('raw_table_counts')
                and ready.get('summary') == {k: v for k, v in summary.items() if k != 'court_position_counts'}
                and validation.get('passed') is True
                and validation.get('foreign_key_errors') == []
                and validation.get('sqlite_integrity_check') == 'ok'
                and validation.get('fts_external_content_integrity_check') == 'passed')
    except (OSError, ValueError, TypeError, KeyError):
        return False


def run_import():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    manifest = pinned_manifest(args.audit)
    final = OUT / 'catalog.sqlite3'
    ready_path = OUT / 'ready.json'
    if final.exists():
        ready = json.loads(ready_path.read_text(encoding='utf-8')) if ready_path.exists() else {}
        if existing_publication_valid(OUT, manifest, ready):
            print(json.dumps({'status': 'already_verified', 'database': final.as_posix(), 'summary': ready['summary']}))
            return
        raise ImportErrorChecked('An existing publication differs; preserve it and use a separately named source package for a new snapshot.')
    atomic_json(OUT / 'source_manifest.json', manifest)
    atomic_json(ready_path, {'ready': False, 'status': 'building', 'started_at': now(), 'source_snapshot': manifest['source_snapshot']})
    building = OUT / ('catalog.building.' + uuid.uuid4().hex + '.sqlite3')
    try:
        summary, validation = build_database(building, manifest)
        file_hash = digest(building)
        atomic_json(OUT / 'summary.json', summary)
        atomic_json(OUT / 'validation.json', validation)
        os.replace(building, final)
        atomic_json(ready_path, {
            'ready': True, 'status': 'verified', 'completed_at': now(),
            'schema_version': 'courtlistener-people-catalog.v1',
            'database': final.as_posix(), 'database_sha256': file_hash,
            'database_bytes': final.stat().st_size,
            'source_manifest_sha256': digest(OUT / 'source_manifest.json'),
            'validation_path': (OUT / 'validation.json').as_posix(),
            'validation_sha256': digest(OUT / 'validation.json'),
            'summary_path': (OUT / 'summary.json').as_posix(),
            'summary_sha256': digest(OUT / 'summary.json'),
            'summary': {k: v for k, v in summary.items() if k != 'court_position_counts'},
        })
        print(json.dumps({'status': 'ready', 'database': final.as_posix(), 'counts': summary['counts'], 'photo_flags': summary['people_with_photo_flags'], 'validation_passed': True}))
    except Exception as exc:
        atomic_json(ready_path, {'ready': False, 'status': 'failed', 'failed_at': now(), 'error': str(exc), 'building_database': building.as_posix()})
        raise


def main():
    with writer_lock(OUT / '.import.lock'):
        run_import()


if __name__ == '__main__':
    main()
