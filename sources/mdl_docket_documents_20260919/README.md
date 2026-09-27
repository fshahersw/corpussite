# mdl_docket_documents_20260919

Master-docket paper trail for MDL detail pages and the Settlements tab, built from
`SW-BULK/catalog/documents.json` (35,862 rows, file captured 2026-08-07).

## What this is
- Every row of `documents.json`, unmodified in substance, with: a stable composite `id`, a
  deterministic `doc_type` classified from the raw docket text, and a join to the MDL registry.
- **Licence.** `SW-BULK/README.md` states the repository is private firm work product built on public
  CourtListener/RECAP data, with an explicit no-fork/no-mirror/no-external-access restriction.
  `license_ref: sw_bulk_private_firm_work_product`, `export_allowed: false`. Local personal-testing
  view only. Never re-host RECAP bytes; `download_url`/`courtlistener_url` link OUT to
  CourtListener/RECAP.

## Join to the MDL registry
Joined **only** through `sources/mdl_docket_crosswalk_20260919/crosswalk.jsonl`'s
`catalog_master_docket_id` field, keyed by the row's native `master_docket_id` (a CourtListener
docket id) — never by the file's own `mdl_number` string and never by name. This recovers MDL 3060
(Hair Relaxer, master `66801859`, 2,242 rows) in addition to the 12 MDLs whose native `mdl_number`
already matched the registry.

- **Measured: 27,840 of 35,862 rows (77.6%) resolve to 13 registry MDLs** (12 pending + MDL 2592,
  terminated) — not the 25,598/12 in the original plan; that count undercounted by omitting the
  crosswalk-only join for master 66801859.
- **8,022 unresolved rows**, in `unresolved.jsonl` with a reason, never dropped from `documents.jsonl`:
  2,263 rows for master `4261857` (Testosterone Replacement Therapy, not in the pending registry) and
  5,759 rows for MDL numbers 2100/2606/2641 (native `mdl_number` present but those MDLs are not in
  `sources/jpml_mdl_20260919/mdls.jsonl`).

## Record id
`doc_uid` is native but **not unique** (31,858 distinct values over 35,862 rows; 4,004 duplicate rows,
mostly the `"<master>-None-"` shape for minute entries with no `entry_number`/`document_number`). The
record id is the composite `doc:<master_docket_id>:<entry_number>:<document_number>:<row ordinal>` —
the ordinal guarantees uniqueness even when `doc_uid` collides. Separately, **23 rows are exact
full-content duplicates** of another row (`is_exact_duplicate`/`exact_duplicate_of`); these are also
kept, not collapsed.

## Document-type classifier
Deterministic keyword/regex classifier (`classify_document_type` in `build.py`) over the raw
`entry_description` + `document_description` text (kept verbatim as `raw_entry_description` /
`raw_document_description`). Categories, in precedence order: `settlement`, `bellwether`,
`leadership_appointment`, `daubert_expert`, `dispositive`, `pretrial_order`,
`case_management_order`, `other`. This is **independent** of the source pipeline's own `doc_category`
(kept verbatim as `source_doc_category`, labelled "categorised by the source pipeline, not by the
court" — it is a different, coarser taxonomy from the same vendor's `assemble_catalog.py`).

**Measured Settlements-tab volume for the 12 pending MDLs** (using this classifier, not
`source_doc_category`): 134 settlement / 1,155 case_management_order / 331 bellwether = **1,620
rows**, not the ~2,227 the plan estimated from `source_doc_category`'s own labels (125/1,976/126 =
2,227 restricted to 11 pending MDLs, `source_doc_category` values). The two taxonomies disagree
because `source_doc_category`'s `case_management_order` bucket also carries generic scheduling and
status-conference orders that this classifier's text rules route to `other` (no literal
"case management order"/"CMO" phrase) or to `pretrial_order` when the docket instead numbers its
orders "Pretrial Order No. N". Both counts are reported in `validation.json` (`doc_type_counts_*`,
this classifier; `source_doc_category` is preserved verbatim per row for anyone who wants the
vendor's own labelling instead).

## Dates and labels
- `entry_date_filed` is the docket **entry** filing date, not a document effective date — maps to
  `published_at` in a consuming view; there is no `effective_from`.
- `high_value`, `source_doc_category` are the vendor pipeline's own classification, "categorised by
  the source pipeline, not by the court".
- `is_available` (16,021 rows with both `download_url` and `sha1`) means a RECAP copy was recorded at
  capture time, not that the link resolves today; not re-checked over the network.
- `captured_at` for the whole file is the file mtime, 2026-08-07 (no internal capture timestamp
  exists); `source_as_of` is left null.
- MDL 2592 is `terminated` (Xarelto) — shown, never mixed silently into pending-MDL counts.
- **Party names (party-name rule).** `case_name` is published **only** when it matches an `In re ...`
  master-docket caption (`suppress_natural_person_caption` in `build.py`); every other caption is an
  individual member-case caption (e.g. MDL 2789's registered docket caption is `STEVEN GOODSTEIN v.
  ASTRAZENECA PHARMACEUTICALS LP` — 1,361 rows) and is suppressed to `null` with
  `caption_suppressed_natural_person: true`. `validation.json.checks[]` carries
  `no_natural_person_plaintiff_caption_published`, a real measurement over every distinct published
  `case_name` (person-v-party shape: `re.split` on `v\.`/`vs\.`, left side 1-4 tokens, no
  corporate/entity/government token), not a hardcoded pass. The adapter (`mdl_docket_documents.py`)
  never uses `case_name` as a title: `listing()`/`detail()` titles and `for_mdl()`'s `latest_25`
  entries are derived only from `docket_number`/`court` (optionally suffixed with the resolved
  `mdl_title`). The `q` search index (`_matches()`) is likewise restricted to `docket_number`,
  `court`, `mdl_number`/`mdl_title` and the `doc_type` label — it never indexes `case_name` or the
  verbatim `raw_entry_description`/`raw_document_description` text, so a person's name typed into the
  filter (e.g. an individual plaintiff named in "Pro hac vice motion(s) granted for ... on behalf of
  Plaintiffs ...") returns no hits. The verbatim docket text itself is still shown, unindexed, in
  `detail()`'s "Docket text" section and in the listing's 200-char `description` cell, where it reads
  as the court's record rather than as a name lookup.

## QA cross-check
`catalog_report.csv` (17 masters) states an independent `document_count` per `master_docket_id`.
Measured: **0 mismatches across all 17 masters** — no stale/page-capped master detected in this
build. `validation.json.checks[].name == "catalog_report_counts_match_per_master"`.

## Files
- `build.py` — deterministic, re-runnable, offline.
- `documents.jsonl` — all 35,862 rows (resolved + unresolved), one JSON object per line.
- `unresolved.jsonl` — the 8,022 unresolved rows with a reason (subset of `documents.jsonl`, for QA).
- `validation.json` — uniform envelope; `counts` carries every number in this README.
- `test_build.py` — classifier unit tests, composite-id/join tests on a synthetic fixture reproducing
  the doc_uid-collision shape, the catalog-report cross-check, and two real-data smoke tests.

## Adapter
`delivery/archive-directory/mdl_docket_documents.py`: `listing()` (filters `mdl`, `doc_type`, `year`,
`has_free_document`), `detail(id)`, `for_mdl(mdl_number)` (counts by `doc_type` + latest 25, only for
resolved MDLs). No `original()` — no document bytes are stored; every link goes out to
CourtListener/RECAP.
