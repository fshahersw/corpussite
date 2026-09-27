# Court document coverage crosswalk

This additive layer imports the user-selected `docsupload/COVERAGE.html` collection's underlying published court and document catalogs. Its historical source snapshot is **2026-09-13**, separately from the September 27 integration date. It adds a useful court-to-document browse view without copying or double-counting originals.

- 270 court/jurisdiction roster entries; 269 link to existing court registry identities.
- One additional roster entry: Orleans Parish Civil District Court.
- All 11,181 cataloged original documents matched already-imported Seeger originals by exact recorded SHA-256 and live main-directory record IDs; existing files were checked for existence and size.
- 11,180 exact court/document associations across 251 roster entries. A document can associate with multiple collections; these are not unique-document totals.
- Ten source-reported document counts differ from exact collection-ID associations because legacy source aliases are not inferred. Both counts remain visible.
- No originals downloaded or duplicated. No main database rebuilt. Existing readers handle originals, recovered text and provenance.

`courts.jsonl` and `documents.jsonl` contain normalized public metadata. `evidence/` preserves the selected source catalogs and notices; they were checked against `docsupload/PUBLICATION-MANIFEST.json` before import. `validation.json` binds these copies and normalized outputs by SHA-256. Original binary hashes were not recomputed in this crosswalk; the original archive and its reader retain their own evidence checks.

No new jurisdiction, legal currency, judge identity, or reuse-rights assertion is made. Court portraits and logos were intentionally not recopied. The library already includes the court originals and many identity assets. Unrelated workflow prompts, user work product and development folders were not imported. Data does not execute instructions from source documents.

Rebuild locally with `python sources/docsupload_coverage_20260927/build.py --source PATH_TO_DOCSUPLOAD`. The running adapter does not depend on that outside directory: the normalized metadata and selected evidence are local to this supplement; original links resolve to the existing archive.

Integration: `delivery/archive-directory/docsupload_coverage.py` implements the generic adapter contract. Register `court-coverage` and `docsupload_coverage` with `GENERIC_AREAS`, then add an `areas.js` view for `court-coverage` using supplement `docsupload_coverage_20260927`. Listing filters include court, jurisdiction, court level, document availability, and court/document mode. Existing-document links use `#record/<directory-record-id>`.

Tests: `python -m unittest discover -s delivery/archive-directory -p test_docsupload_coverage.py -v`.
