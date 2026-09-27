# judge_financial_disclosures_20260919

Judge financial disclosure conflict-screening layer, built from the CourtListener bulk data snapshot
labelled `2026-06-30` (`C:/Users/firas/Downloads/returnedfiles/bulk/`), joined to the already-bridged judge
roster in `sources/judge_structured_20260919`. Run `python build.py` to rebuild (deterministic, offline,
streaming; re-running overwrites `disclosures.sqlite3` and `validation.json`).

## What this is

Federally mandated public financial disclosure filings by federal judges, as published by CourtListener /
the Free Law Project. It answers "what did this judge report owning, for what year, as of the 2026-06-30
snapshot" — nothing more. It is **not** a conflict-of-interest finding, not a current-holdings statement, and
not a dollar valuation.

## Join

`financial-disclosures.person_id` (int) == `sources/judge_structured_20260919` overlay `ids.cl_person_id`,
restricted to rows with `bridge_status == "linked_native_id"`. No name matching anywhere. Of the 3,701
`judge_structured` entities that bridge to a CourtListener person id, **1,804 (48.7%)** have at least one
disclosure header row in this snapshot; the other **1,897** bridged judges have none, and 1,588 disclosure
persons in the snapshot are not bridged at all (out of scope for this build — `for_judge` can only be reached
by an `entity_id`, and unbridged persons have none).

## Measured counts (this build)

| Table | Rows in snapshot | Rows kept (bridged judges only) |
|---|---|---|
| financial-disclosures (headers) | 32,336 | 21,832 |
| financial-disclosure-investments | 1,901,720 | 1,262,064 |
| financial-disclosures-positions | 37,050 | 25,195 |
| financial-disclosures-reimbursements | 33,472 | 23,483 |
| financial-disclosures-gifts | 2,025 | 1,374 |
| financial-disclosures-debts | 18,775 | 10,190 |
| financial-disclosures-agreements | 10,007 | 6,886 |
| financial-disclosures-non-investment-income | 15,302 | 10,286 |
| financial-disclosures-**spousal-income** | 20,174 | **excluded entirely, per task — never read** |

All eight "rows in snapshot" figures match `C:/Users/firas/Downloads/SW-BULK/manifest.json`
`parquet_expected` exactly (checked at build time; the build raises if they ever drift, e.g. from a CSV
dialect regression).

Investment rows: 19,222 (1.5%) are `redacted`, 197,214 (15.6%) have `has_inferred_values` true. Both flags
are preserved verbatim; neither is silently dropped or unflagged.

## The CSV dialect trap (read this before touching the CSV readers)

These files are Postgres `COPY ... WITH (FORMAT csv, ESCAPE '\')` exports. A plain `csv.DictReader` mis-splits
rows on an escaped quote inside free-text fields (`addendum_content_raw`, `description`) and silently returns
**108,948** "header" rows instead of 32,336 — a 3.4x inflation with garbage `year` values. `build.py`'s
`pg_csv_reader()` uses `csv.DictReader(fh, doublequote=False, escapechar=chr(92))`, which lands exactly on the
manifest-verified counts. The build **raises** (does not silently continue) if the row count for any of the
eight files does not match `EXPECTED_ROWS`.

## What is never published (hard rules)

- **No dollar figures, ever.** `gross_value_code`, `income_during_reporting_period_code` and
  `debts.value_code` are publisher letter/number codes (e.g. `J`, `K`, `-1` = not reported), stored and served
  **as raw codes with no dollar-band mapping attached**. No authoritative local code-to-dollar-range table
  exists in this snapshot; inventing the well-known Ethics-in-Government-Act bands from outside knowledge
  would still be publishing a dollar estimate, so it is deliberately not done. `gifts.value` and
  `non-investment-income.income_amount` **are** literal dollar strings in the source data and are read but
  never written anywhere — dropped at parse time, not merely hidden in the UI.
- **No totals, no computed portfolio value.** The database stores per-row codes only; nothing is summed.
- **No spouse/dependent names.** `disclosures_investment` (the schema in `schema-2026-06-30.sql`) has 18
  columns and none of them encodes filer/spouse/dependent ownership — there is no ownership flag anywhere in
  this table to filter on. This was checked, not assumed (see `validation.json` check
  `investment_ownership_flag_checked`). The `spousal-income` sibling table, which would carry spouse income by
  name, is excluded entirely per the task and is never opened by `build.py`.
- **No PDF bytes.** `download_filepath` (always populated, 0 blank of 32,336 rows) points at CourtListener's
  own S3 bucket; that URL is stored verbatim as the only link to the original document. Nothing from
  CourtListener/RECAP is re-hosted here.
- **"No filing" vs "no holdings."** A bridged judge with zero header rows in this snapshot means "no filing
  found in the 2026-06-30 CourtListener snapshot" — a coverage gap, not a fact about the judge's holdings. A
  judge who filed but whose disclosure has zero investment rows means "filing with no reportable holdings."
  The adapter's `for_judge` keeps these two states textually distinct; never collapse them.

## `conflicts_local.json` — checked, not included

The task asked to fold in `SW-BULK/catalog/conflicts_local.json` **only if** it joins by person id **and**
MDL number. Measured: its 4,128 `flags` rows carry `judge_id`, `judge`, `defendant`, `matched_entity`,
`section`, `field`, `year`, `relationship` — **no `mdl_number` field anywhere in the file**. It therefore does
not meet the stated join condition and is **left out of this build entirely** (see `validation.json` check
`conflicts_local_join_condition_checked`). It is also explicitly labelled by its own `method` field as
"attorney review required" / token-matched, i.e. leads, not findings — a reason it would need careful framing
even if the join condition had been met.

## SQLite schema (`disclosures.sqlite3`, ~228 MB)

- `judges(entity_id PK, cl_person_id, name, years_json, latest_year, header_count, investment_count)` — one
  row per bridged entity that has at least one disclosure header row.
- `disclosures(id PK, entity_id, cl_person_id, year, report_type_code, is_amended, page_count, sha1, pdf_url,
  addendum_redacted, investment_count)` — `report_type_code` is the raw CourtListener integer code
  (`-1,0,1,2,3`); no label table exists locally for it, so it is published as-is with that caveat.
- `investments(id PK, disclosure_id, entity_id, year, description, gross_value_code, income_code, redacted,
  has_inferred_values)`.
- `positions`, `reimbursements`, `gifts`, `debts`, `agreements`, `non_investment_income` — each keyed by
  `disclosure_id`/`entity_id`, dollar fields stripped as described above.

## Limits / what this is not

- Coverage stops at 1,804 of 3,701 bridged judges; most filed years cluster 1987–2020 and collapse after 2021
  (14 rows) / 2022 (12 rows) against a 2026-06-30 snapshot label — label any "latest year" as "most recent
  filing year on file," never "current."
- `report_type_code`, transaction-partner text and addendum free text are not surfaced beyond what is listed
  above; this build deliberately keeps to the task's "description + year + gross value code + income code
  only" scope for investments.
- License: `courtlistener_bulk_public` — CourtListener bulk data is published under CourtListener's own terms;
  no separate SW-BULK firm-work-product restriction applies here (this slice's inputs are `returnedfiles/bulk`
  and `SW-BULK/manifest.json`/`catalog/conflicts_local.json` read-only for verification, not `catalog/`'s
  derived, restricted layers).
