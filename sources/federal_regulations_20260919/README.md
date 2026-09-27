# Federal regulation slice for mass-tort research (build 2026-09-19)

Status: `validation.json` says `status == "passed"`, `ready == true` (round 2, resumed after the
round-1 cutoff; see it for counts, checks and the qualification). The adapter refuses to serve anything
unless `status == "passed"` and `ready == true` and every data-file hash matches.

## What is here

- `parts.json` — the 98 selected CFR parts (Titles 21, 16, 49, 40) from the regulations scout.
- `seeds.jsonl` — frozen URL list (179 URLs: 98 eCFR versions endpoints, 81 Federal Register API pages).
- `receipts.jsonl` + `raw/` — one receipt per request with status, headers, final URL, timestamp, SHA-256;
  raw bodies stored under their SHA-256. Round 1 fetched all 179; round 2 verified every hash and
  fetched nothing (0 network requests).
- `fetch.py` — the bounded packet collector (round 1). Not re-run in round 2.
- `build.py` — offline builder (in progress).

## Network budget

Two packets total: packet 1 `ecfr_versions` (97 live requests + 1 body reused from the scout),
packet 2 `fr_documents` (68 page-1 requests + 13 continuation pages). Title 40 parts were not
included in the Federal Register packet (budget); see `gaps.json` once written.

## Adapter

`delivery/archive-directory/federal_regulations.py` (tests: `delivery/archive-directory/test_federal_regulations.py`)
loads this folder fail-closed: `validation.json` must say `status == "passed"` and `ready == true`, and every
listed data file (`regulations.sqlite3`, `agencies.jsonl`, `edges.jsonl`, `unresolved.jsonl`) must hash to the
recorded SHA-256 (re-verified whenever a file's size or mtime changes). SQLite is opened read-only for every
call. With the gate closed every function returns a `ready: false` / `available: false` shape, an empty list or
`None`. Public dicts carry no file-system paths.

Functions: `info()`, `titles()`, `parts(title, include_all=False, page, limit)`, `sections(part, as_of=None,
title=None, page, limit)`, `section(citation)` (accepts `21 C.F.R. § 314.80`, `21 CFR 314.80`, `cfr:21:314.80`),
`fr_document(document_number)` (alias `document`), `agencies(slice_only=False, q=None)`, `search(q, title, part,
agency, record_type, date_type, dfrom, dto, page, limit)`; `DATE_TYPES` labels the eight selectable dates.
Proposed routes, real example responses and UI placement: `integration_spec.json`.

Text rules: `section()['texts']` holds one entry per source. `gpo_ecfr_xml` is the local GPO eCFR Title 21 text
(hash re-checked at serve time). `open_us_law` is read on demand from the Open US Law index (read-only) and served
only when its SHA-256 equals the hash recorded here at build time; if the index is absent or the text changed, the
entry keeps `text: null` with an `unavailable_reason` and the rest of the section still works. Text search over the
publisher copy uses that index's full-text table restricted to this slice's rows and is skipped (reported under
`publisher_index`) when the index is unavailable.

Temporal rules: every record carries the uniform temporal block. `source_as_of` is the GPO volume amendment marker
for Title 21 parts and texts, the eCFR structure snapshot date for other parts and sections, and the Open US Law
snapshot date for publisher text. eCFR amendment and issue dates appear only in `history`, `latest_amendment_date`
and the `amendment_date` / `ecfr_issue_date` search filters. Federal Register `publication_date` is `published_at`;
`effective_on` stays `effective_on_publisher_extracted`; `effective_from` / `effective_to` are always null with a
basis string. `sections(part, as_of=YYYY-MM-DD)` lists the section identifiers that eCFR version metadata shows as
present on that date: unsupported before the part's history start, sections without version rows are omitted and
counted, and it is never point-in-time text.
