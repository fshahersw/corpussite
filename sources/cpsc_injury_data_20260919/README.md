# CPSC injury data supplement (NEISS 2025 sample + SaferProducts.gov incident reports)

Built 2026-09-19 from two local files under `C:/Users/firas/Downloads/returnedfiles/` (read-only in
place; never modified, never copied byte-for-byte into this folder). Neither file was previously used by
the MVP: `sources/agency_safety_20260919` already indexes a *different* local CPSC file, `Recalls.csv`
(sha256 `fc134252...9409c9`, the `cpsc_recalls_local` dataset, 9,970 recall records). The two files built
here are separate CPSC datasets with different sha256 hashes and different content (injury-surveillance
sample rows and consumer-submitted incident reports, not recall notices).

## What is in the folder

| File | Meaning |
|---|---|
| `build.py` | Offline, re-runnable build: reads both inputs read-only, writes `cpsc_injury_data.sqlite3` and `validation.json`. Streams the XLSX with `openpyxl(read_only=True)`; never loads the whole file into memory. |
| `cpsc_injury_data.sqlite3` | The supplement database (schema below). |
| `validation.json` | Uniform envelope; the adapter opens only when `status == "passed"`, `ready == true` and the database hash matches. |
| `test_build.py` | TDD unit tests for the label/age/PII-column helper functions (no filesystem dependency) plus assertions against the built database (skipped if it does not exist yet). |
| `build.log` | Output of the last `build.py` run. |

## Datasets (2)

### `neiss` -- CPSC NEISS calendar-year-2025 file (`neiss2025.xlsx`, 53,950,392 bytes)

The National Electronic Injury Surveillance System file distributed by CPSC: one row per **sampled**
emergency-department visit associated with a consumer product, drawn from a probability sample of about
100 hospitals nationwide. **410,201 rows**, all with `Treatment_Date` in calendar year 2025.

The file itself carries a second sheet, `NEISS_FMT` (**1,250 rows, 11 format names**: `AGELTTWO`,
`ALC_DRUG`, `BDYPT`, `DIAG`, `DISP`, `FIRE`, `HISP`, `LOC`, `PROD`, `RACE`, `SEX`) -- a SAS-style code
table (`Format name`, `Starting value`, `Ending value`, `Format value label`). Every coded column in
`neiss_cases` (`body_part_code`, `diagnosis_code`, `disposition_code`, `location_code`,
`fire_involvement_code`, `product_1/2/3_code`, `alcohol_code`, `drug_code`, `sex_code`, `race_code`,
`hispanic_code`) is labelled **only** from an exact-value row of this table (`neiss_fmt`, stored verbatim
alongside the data); a code with no such row is left bare. `PROD` alone has **1,124** distinct product
codes, so product names come from the file itself, never a hand-maintained list.

**Hard rule enforced by `build.py` and the adapter:** every row is a sample record, never a count/rate/
trend. The publisher's own statistical `Weight` column is stored and always printed exactly as found; no
code anywhere in this supplement multiplies, sums or otherwise aggregates it into a national estimate.

Age is NEISS's own two-part coding: `0` = unknown, `1-120` = whole years, `201-223` = 1-23 months (a child
under 2). `age_display` (e.g. `"6 months"`, `"47 years"`) and `age_band` (e.g. `"Under 1 year"`,
`"1-4 years"`, ... `"75+ years"`, `"Unknown"`) are both a **deterministic, per-row mechanical translation**
of that published code (see `build.age_display` / `build.age_band` and their tests) -- a navigational label
and filter bucket, never an estimate computed across rows.

### `saferproducts` -- CPSC SaferProducts.gov incident report export (`IncidentReports.csv`, 99,792,102 bytes)

The "Publicly Available Consumer Product Safety Information Database" export: **69,333 rows** (2011-03-11
to 2026-08-03), each a consumer (or occasionally professional/agency) **submission**, not a verified
finding. The file's own first line is CPSC's disclaimer, reproduced in the adapter's qualification and
below. `Category of Submitter` for this file: 67,164 Consumer, 581 Public Safety Entity, 446 Local
Government Agency, 402 Health Care Professional, 363 State Government Agency, 133 Medical Examiner and
Coroner, 129 Child Service Provider, 114 Federal Government Agency, 1 Other.

**No submitter name, e-mail or phone column exists in this export** (verified against the file's own
40-column header). `build.classify_saferproducts_columns` still runs on every build to flag any column
whose header looks like submitter identity/contact data, so a *future* export gaining one cannot slip
through unnoticed; `validation.json -> counts.saferproducts_columns_dropped_as_pii_count` records the
result (0 for this file). Manufacturer/importer/private-labeler name, brand and the free-text product
description are shown, as instructed -- they describe the product, not the person who submitted the
report.

44,631 characters in the CSV decoded as invalid UTF-8 (stray Windows-1252 bytes, e.g. curly quotes, mixed
into otherwise-UTF-8 free text); those bytes were replaced with U+FFFD (`errors='replace'`) and the count
is recorded in `validation.json`, never silently dropped or guessed at.

## Schema

`neiss_cases` (410,201 rows) -- one row per sampled ED visit; every `*_code` column is the publisher's
value as printed, every `*_label` column is looked up from `neiss_fmt` (`NULL` when uncoded), `narrative`
is `Narrative_1` as printed, `weight`/`stratum`/`psu` are the publisher's sample-design columns as printed.
`neiss_fts` (FTS5, external content) indexes `narrative` + the three product labels.

`saferproducts_incidents` (69,333 rows) -- one row per consumer report; each publisher date column keeps
its own `_iso` companion (parsed `M/D/YYYY -> YYYY-MM-DD`, `NULL` when unparseable, never guessed).
`saferproducts_fts` (FTS5, external content) indexes `incident_description`, `product_description`,
`manufacturer_name`, `brand`.

`facet_counts` (`dataset`, `facet`, `value`, `label`, `n`) -- filter-option counts computed once at build
time (`build.build_facet_counts`), for the 7 NEISS + 4 SaferProducts select-type filters below.

## Adapter (`delivery/archive-directory/cpsc_injury_data.py`)

Generic view contract (`listing(params)` / `detail(id)`), fail-closed hash gate, read-only SQLite
(`file:...?mode=ro`), stop words ignored in `q`, badges capped at 2, qualification strings under 400
characters. A `dataset` param (`neiss` default, `saferproducts`) selects which table `listing()` reads;
`detail(id)` is routed by the id's own prefix (`neiss:<case_number>` / `sp:<report_no>`).

- **neiss filters**: `q` (narrative + product text), `product` (code or name, matches any of the three
  product slots), `year`, `month`, `body_part`, `diagnosis`, `disposition`, `age_band` (as coded), `sex`.
- **saferproducts filters**: `q` (incident + product text), `product_category`, `state`, `year`,
  `category_of_submitter`.

**Performance note (why `facet_counts` exists):** the first working version computed every select
filter's options live, grouping on `neiss_cases`' own denormalised `*_label` column. Grouping on two
columns of a 410,201-row table forced SQLite off its single-column covering indexes into a full table
scan every time -- measured **2-9 seconds per filter**, and the default (unfiltered) listing's
`ORDER BY treatment_date DESC` cost a further **~2.5s** with no index covering that sort. Total: 8-12s for
one page load. Two fixes, both verified by rerunning the same profile: (1) `facet_counts` is built once,
offline, so every filter's options come from a handful of rows keyed by `(dataset, facet)` regardless of
the source table's size; (2) `ix_neiss_treatment_date`/`ix_sp_report_date_iso` are `(date DESC, rid)`
indexes so the default listing's sort is answered by a covering index scan. Measured after both fixes:
default listing ~0.1-0.3s (see `test_default_listing_is_fast`). `total` for an unfiltered listing is read
from `validation.json`'s own row count instead of running `count(*)` again.

## What the counts mean

`validation.json -> counts`: `neiss_cases` / `saferproducts_incidents` (row counts), `neiss_fmt_rows` /
`neiss_fmt_format_names` (the embedded code table), `saferproducts_columns_dropped_as_pii` (list, expected
empty) / `_count`, `saferproducts_mojibake_bytes_replaced`.

## Limits

- NEISS 2025 is one calendar year only; no other year is in this file. A national estimate, rate or trend
  requires applying the sample design (`Stratum`/`PSU`/`Weight`) correctly across the full multi-year
  design, which this supplement does not attempt -- CPSC's own NEISS query tool is linked from every row
  for that purpose.
- SaferProducts reports are unverified consumer submissions per the publisher's own disclaimer ("CPSC does
  not guarantee the accuracy, completeness, or adequacy of the contents ... particularly with respect to
  information submitted by people outside of CPSC"); a report is not a confirmed defect, a recall or a
  legal finding.
- Body-part/diagnosis filters apply to the *primary* injury only (`Body_Part`/`Diagnosis`); the secondary
  fields (`Body_Part_2`/`Diagnosis_2`) are shown in the detail view but are not separate filters.
- No cross-reference between the two datasets or to `sources/agency_safety_20260919`'s CPSC recalls
  dataset was attempted; a shared product code does not imply the same product record.

## Rebuild and test

```bash
"/c/Users/firas/AppData/Local/Programs/Python/Python311/python.exe" sources/cpsc_injury_data_20260919/build.py
"/c/Users/firas/AppData/Local/Programs/Python/Python311/python.exe" -m unittest discover -s sources/cpsc_injury_data_20260919 -p "test_build.py"
"/c/Users/firas/AppData/Local/Programs/Python/Python311/python.exe" -m unittest discover -s delivery/archive-directory -p "test_cpsc_injury_data.py"
```
