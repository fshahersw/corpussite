# Open US Law bulk supplement

Structured US primary-law data from **Open US Law by Vaquill AI** ([repository](https://github.com/Vaquill-AI/open-us-law)), used under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The repository's scripts are Apache-2.0. The pinned license files are in `evidence/`. Local changes are checksum verification, a separate searchable index, and source/quality metadata. Legal text is preserved as exported; no source scraper or upstream proxy/reconciliation code was executed.

## Exact source

- Snapshot: **v2026.08**, publisher snapshot date **2026-08-14**.
- Repository commit: `2f7aeb85a434a54a351ac44e3c188fec318f78ba`.
- Canonical Hugging Face revision observed: `16bc9a159faabea4af9db08f1b33832e80e85b2d`.
- Downloaded artifact: the exact [publisher-linked bulk tar](https://oss-data-us.vaquill.ai/v2026.08/open-us-law-v2026.08-parquet.tar).
- Tar SHA-256: `02af3e8cd850edcd79f553b56c5d0cc7a0b487d57ae70f910019713fa5ccedca`.
- **229 Parquet files, 4,087,188,678 bytes**, all verified against the publisher's individual SHA-256 values. The retained tar is 4,087,603,200 bytes.
- Actual Parquet metadata: **2,978,617 exported rows**. These are source records, not an independently verified count of unique laws or complete sections.
- The 53 jurisdiction labels represent the 50 states, DC, Puerto Rico and federal material. A jurisdiction appearing in the data does not mean every law category is available for it.

The two public manifests agree. They contain 233 object entries: 229 Parquets plus a README, checksums, totals, and the duplicate combined tar. Their `total_bytes` field counts Parquets only.

Individual Parquet URLs returned HTTP403; canonical Hugging Face file access was gated. Those barriers were not retried. The separately published bulk tar returned200 and downloaded successfully, removing the need for a sign-in. See `access_barriers.json`, `evidence/tar_access_probe.json`, and `download_tar_receipt.json` for that history.

## Search and provenance contract

`catalog.sqlite3` is a separate database for this bulk dataset; it is published only after indexing and integrity checks. Treat it as ready **only when `ready.json` says `ready: true`**. An in-progress index is `catalog.building.sqlite3`. `import_summary.json` and `ready.json` are authoritative for index readiness, actual indexed rows and query/filter counts.

The catalog was published on 2026-09-18 with all 2,978,617 rows and 229 files reconciled. Its FTS external-content integrity check and SQLite `quick_check` passed. `catalog_validation.json` records five exact original-Parquet row bindings and three successful real text searches. These are technical integrity checks, not a semantic audit of every law. All source IDs are present; 135 publisher citations are blank.

The initial process encountered a Windows default-encoding error while assembling UTF-8 quality metadata after its index checks. Finalize-only recovery preserved the completed index, repeated `quick_check` once, and published successfully. The traceback, control-flow evidence and resulting proof are retained in `index_failure_20260918.txt`, `finalization_recovery.json` and `verification_checkpoint.json`.

`records` columns:

| Column | Meaning |
|---|---|
| `id` | `oul:` plus SHA-256 of snapshot/source ID/file/row; source-ID collisions are retained |
| `source_id` | Original exported `act_id` |
| `title`, `citation` | Exported section title and citation |
| `state`, `kind` | Uppercase file jurisdiction code and file corpus name |
| `source_url` | Exporter's original legal-source link; this import did not retrieve that government URL |
| `text` | Exact exported legal text string |
| `payload` | Remaining exported fields, including hierarchy, section number, source year, amendment year and cross-references |
| `file_id`, `row_index` | Exact Parquet filename and zero-based row locator |
| `content_hash` | SHA-256 of the retained text string |
| `snapshot`, `status` | Publisher snapshot and exported `act_status`; status is not independently verified |

`files(id,path,sha256,bytes,rows,state,kind,indexed_at,schema_json)` links each record's `file_id` to its verified Parquet. `records_fts(title,citation,text)` is an external-content FTS5 index whose rowids join `records.rowid`. The `import_meta` table stores JSON-encoded summary values. Summary `state_counts` and `kind_counts` support UI filtering without repeated whole-dataset scans.

Parameterized search example:

```sql
SELECT r.id, r.title, r.citation, r.state, r.kind, r.source_url
FROM records_fts
JOIN records AS r ON r.rowid = records_fts.rowid
WHERE records_fts MATCH ? AND r.state = ?
LIMIT ? OFFSET ?;
```

`resources.jsonl` registers **file-level artifacts only**. Do not insert millions of duplicate row JSON objects into the central archive database. Read individual rows from this separate catalog and serve readable text alongside its Parquet provenance.

## Quality and remaining gaps

- Georgia and North Carolina **statutes were withdrawn by the publisher** because their section bodies included website boilerplate. They are absent from this snapshot. Other available categories for those states do not close that statutory gap.
- The export has a uniform 24-column schema but no `level_classifier`. Counts of nonempty text or nonempty section numbers are useful measurements; neither proves unique, substantive, complete statutory coverage.
- All 2,978,617 rows have nonempty text, but 57,097 bodies are under 80 characters. **311,321 rows lack an original source URL**, including every exported Mississippi, Arkansas, New Mexico and Tennessee statute row and Texas regulation row. Retained Parquet/file/row provenance still identifies their publisher source; missing government links must not be invented. See `quality_summary.json` and `source_url_gap_validation.json`.
- New York contributes 40,140 statute rows, 204 constitution rows, 1,088 court-rule rows, and 1,199 guidance rows. These are downloaded third-party structured reproductions with original-source links, not fresh official-government HTTP captures.
- New York's court-rule export includes 51 rows labeled repealed and 50 reserved; its guidance includes 502 rescinded rows. Two constitution records lack a source URL, and the publisher's preamble pinpoint (`N.Y. Const. art. 0, § 0`) is synthetic. Preserve those source assertions rather than treating every record as current law or a verified citation.
- A snapshot date is not an effective date. Exported `in_force` labels and amendment dates are retained as publisher assertions. Historical, repealed and reserved material must keep its status.
- Federal regulations include whole Federal Register notices, including two exported rows over one million words. Row counts are therefore not counts of individual CFR sections; readers should truncate only the inline preview and keep the full retained text available separately.
- 40,185 exported text rows contain literal backslash-n sequences, including every New York statute row. They are retained and counted, rather than silently rewritten. This can affect token boundaries in searches of the raw text. Short/empty text and missing source URLs are recorded per file.
- No county-specific content is inferred from statewide material. This supplement contains no judge identity/analytics integration.
- See `snapshot_metadata_validation.json`, `quality_by_file/`, `file_quality.json` and `validation_samples.json`. Automated screening is not comprehensive legal-text validation.

## Reproduce locally

Run the workspace's reviewed importer, not downloaded repository code:

```powershell
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' scripts/import_open_us_law.py --index
```

The importer verifies every Parquet against the frozen manifest before indexing. Completed file transactions can be resumed. A completed rerun verifies the originals and published database checksum, then returns without rebuilding the index. It does not purchase services, call paid APIs, collect credentials, rescrape government sources, or remove the existing corpus.

## Upstream adapter review

Pinned NY, NJ and LA bulk-ingester source files are preserved under `evidence/scripts/statutes/` as data. Their comments include optional proxy use and destructive database reconciliation examples, which were **not executed or adopted**. A useful future official-source candidate is New Jersey's explicitly documented daily `STATUTES-TEXT.zip` export, but fresh access, licensing/scope and duplicate review would be required before introducing another collector. The NY upstream adapter requires its own OpenLegislation API credential; no such API call was made.
