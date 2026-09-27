# Federal court statistics (Administrative Office of the U.S. Courts) - supplement 2026-09-19

Official statistical tables saved from `www.uscourts.gov` on 2026-09-19 (two bounded packets, receipts in `receipts/`),
published as registered originals with a bounded preview. Build is offline and re-runnable: `python build.py`
(first run ~3.5 min because pypdf reads 12,031 CJRA pages; later runs ~15 s from `_work/cjra_cache/`).

## What is here
| File | Meaning |
|---|---|
| `seeds.jsonl`, `receipts/`, `raw/` | Frozen URL list (93 seeds), per-request receipts, original bytes. Never modified by the build. |
| `tables.jsonl` | 92 rows, one per saved original: `table_id`, title as listed by the publisher (+ `title_in_file`), `series`, `topic`, `period_label` / `period_end` / `period_start` with `period_basis`, `format`, `bytes`, `sha256`, `file_id`, `source_page`, `source_url`, `sheet_names`, `page_count`, bounded `preview`, temporal block. |
| `cjra_rows.jsonl` | 6,329 per-judge rows parsed from CJRA Tables 7, 8 and 9 (as of March 31, 2026): court, circuit, judge type and judge name **as printed**, the printed total, PDF page range. |
| `gaps.json` | 1 seed not saved (A072, FCMS "Explanation of Selected Terms": off-host http redirect was not followed). |
| `validation.json` | Uniform envelope; hashes of the two data files; counts; checks. |
| `integration_spec.json` | Adapter signatures, routes, UI notes, real example responses. |
| `build.py`, `test_build.py` | Build and its offline tests. `fetch.py` / `prepare.py` are the earlier network packet scripts (not run by the build). |

Counts: 71 XLSX, 16 PDF, 5 HTML. Series: Judicial Business 29, Facts & Figures 19, Judgeships 13 (8 authorized-judgeship
PDFs + 5 vacancy/judgeship pages), Statistical Tables 12, CJRA 8, Caseload Statistics 8, FCMS 3.

## What the numbers mean
- Every figure is a **publisher-reported count for the stated period or as-of date**. The build computes no rates.
  Percent-change and median columns that appear in a preview are the publisher's own cells, stored as found in the workbook
  (floats rounded to 6 decimals; the adapter shows at most 2 decimals; the original file is authoritative).
- `period_label` and `period_end` come only from (a) the title line inside the workbook, (b) the "As of" line on page 1 of a
  CJRA PDF, (c) the "as of MM/DD/YYYY" statement on a saved vacancy page, or (d) the period printed on the publisher's
  data-table listing (seed) - `period_basis` says which. Where several periods are named ("June 30, 2025 and 2026",
  "September 30, 1995 Through 2025") `period_end` is the latest named date and the label is kept whole.
  80 of 92 have a period (79 from the file, 1 from the listing - the FCMS district-profile workbook); **12 are null** (8 authorized-judgeship PDFs, 3 live pages
  without an as-of statement, 1 FCMS explanation workbook). `period_start` is set only for a single explicit
  "12-Month Period Ending <date>" (35 tables), and is labelled as computed.
- HTTP Last-Modified and URL folder dates are never used as publication or effective dates; `published_at` is null everywhere.
- Preview = title lines, up to 8 header rows and the first 40 data rows per sheet, at most 3 sheets and 40 columns
  (openpyxl, read-only, cached cell values). It is a reading aid, not a full extraction; header/data split is heuristic.
- Civil counts include MDL member cases; a single MDL can dominate a district's totals.

## CJRA Tables 7 / 8 / 9 (per judge)
pypdf text extraction was clean, so rows were parsed from the printed line `Total <All Cases|Motions|Bench Trials> for <judge type> [:] <name> <count>`
(2,136 + 2,116 + 2,077 rows). Checks, all passing: exactly one court line and one circuit line on every page; the judge header
printed on the page (or the latest earlier header on a totals-only continuation page) names the same judge as the total line;
footer "Page x of N" equals the page count; one as-of label per file; and **reconciliation**: the per-circuit sums of the printed
per-judge totals equal CJRA Table 2 of the same report for all 12 circuits and the national total
(civil cases pending over three years 85,476; motions pending over six months 9,593; bench trials 56).
Not parsed: the case-level lines (docket number, case title, status codes) - they wrap unpredictably in extracted text.
Rows include "Unassigned", "(MDL)", "(CIT)" and visiting-judge entries exactly as printed. No row is linked to a judge entity
(`judge_match.status = "not_attempted"`): matching needs court + name candidates and a review queue, never name alone.

## Adapter
`delivery/archive-directory/court_statistics.py` (generic contract): `listing(params)` with filters q / series / topic / format /
period and columns Table / Series / Period / Format; `detail(id)` (id = `table_id`, or `<table_id>@<court-slug>` for all CJRA rows
of one court); `original(file_id)` re-hashes the file at serve time and only reads inside `raw/`. Gate: `validation.json`
status == "passed", ready == true, and both data-file hashes. Tests: `delivery/archive-directory/test_court_statistics.py`.

## Limits / not verified
- Latest periods only (STFJ June 30, 2026; FJCS March 31, 2026; JB/JFF September 30, 2025; FCMS June 30, 2026; CJRA March 31, 2026); no history.
- No per-district or nature-of-suit extraction, no court-code mapping, no edges (`edges.jsonl` not emitted).
- JB/JFF PDF twins, appellate FCMS, CJRA 10/11, JPML and FJC hosts were outside this packet.
- Licence: works of the federal judiciary published on uscourts.gov; not independently reviewed.
- Packages: `openpyxl` 3.1.5 and `pypdf` 6.0.0 were already installed; nothing was installed for this build.
