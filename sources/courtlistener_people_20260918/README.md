# CourtListener people source layer

This package imports six existing local files from `C:/Users/firas/Downloads/returnedfiles/bulk/`. It makes no network requests and does not merge people into the existing judge entity corpus. The `2026-06-30` snapshot label comes from the local filenames and supplied schema/loader, rather than a fresh provider request.

Run the reproducible importer with:

```powershell
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' -B 'C:/Users/firas/Downloads/SCRAPE/sources/courtlistener_people_20260918/import_people.py'
```

The importer validates source hashes against the earlier audit before and after reading each file. It holds an exclusive OS writer lock, builds a separate transactional database, checks every row width and count, enforces deferred foreign keys, checks SQLite and external-content FTS integrity, then atomically publishes `catalog.sqlite3`. `ready.json` is written last and binds the database, source manifest, summary and validation hashes. A matching completed publication is verified and reused; a differing existing database is never overwritten automatically.

## Reader contract

- Require `ready.json` to contain `ready: true` and `schema_version: courtlistener-people-catalog.v1`. Verify its artifact hashes before serving.
- `people`, `positions`, `educations`, `schools`, `political_affiliations`, and `courts` retain every original CSV column as raw `TEXT`. Each has native `id TEXT PRIMARY KEY`.
- Raw empty strings remain empty strings. Additional generated `_ref_*` columns turn empty references into SQL NULL solely for foreign-key enforcement. Select original columns listed in `source_files.columns_json` when presenting raw records.
- `people_search(person_id, display_name, search_text, has_photo, is_alias)` is a derived search table. `people_fts(search_text)` is FTS5 external content keyed by `people_search.rowid`.
- `source_files(table_name, path, sha256, compressed_bytes, rows, columns_json)` binds each table to the source artifact. `import_metadata` stores snapshot, schema, manifest and interpretation notes.
- `position_profiles` adds `court_full_name`, `court_jurisdiction`, `appointer_person_id` and `appointer_display_name` to raw positions. **`positions.appointer_id` references `positions.id`**, while predecessor and supervisor IDs reference people.
- Name lookups, FJC IDs, aliases, person associations, and court/person position joins are indexed. Query people by `people.id`; this is the CourtListener native person ID.

Example full-text lookup:

```sql
SELECT s.person_id, s.display_name, s.has_photo, s.is_alias
FROM people_fts f
JOIN people_search s ON s.rowid = f.rowid
WHERE people_fts MATCH ?
LIMIT 50;
```

Bind and safely construct the FTS query in the serving layer. This source package does not expose a web server.

## Interpretation

People records include historical figures, aliases and non-judge roles. A court-linked position does not establish that a person is a judge or currently serving. Keep the original dates together with `date_granularity_dob`, `date_granularity_dod`, `date_granularity_start` and `date_granularity_termination`; January 1 can represent a year-only value. Other date precision and inferred-value fields also remain unchanged.

`has_photo` is a publisher availability flag. This package contains **zero newly downloaded portraits** and supplies no guessed image URLs. Alias references are preserved and displayed as relationships; they do not cause records to be collapsed. CourtListener person IDs, legacy `fjc_id`, and FJC `nid` values must not be treated as interchangeable namespaces.

No financial disclosures, opinions, races or other bulk tables are imported. The original SQL loader is read as a schema reference and is never executed. Source originals remain unchanged.

`summary.json` contains actual table counts, photo flags, aliases, historical court-position counts and role codes. `validation.json` contains row/hash/FK/SQLite/FTS checks. `test_import_people.py` exercises escaping, raw date precision, alias and appointer relationships, search, corrupted inputs, and duplicate-writer rejection.
