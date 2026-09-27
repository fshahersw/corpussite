# court_document_library_20260919

A SQLite index (`index.sqlite3`) over the two hash-manifested local court-document stores under
`SW-BULK/publiclaw_registry_v2/staging` (main tree only). Original bytes stay in place; nothing was
copied into SCRAPE. `build.py` is deterministic, streaming, and safe to re-run.

## Inputs (never modified)

- `court_expansion_file_download_v2_4_2026-08-20/wave_*/court_file_download_results_v2.jsonl` — 11
  wave folders that actually carry this manifest file (8 more `*_followup`/`*_cumulative_followup`
  folders hold only retry/review queues and are not indexed). **47,230** `download_status=="downloaded"`
  rows (of 47,445 total; 194 `download_error`, 21 `signature_rejected_body_removed` excluded).
- `state_trial_court_acquisition_v1_2026-08-20/artifact_download_results_v1.jsonl` — **3,710**
  downloaded rows (of 3,718).
- `court_expansion_corpus_v2_4_2026-08-20/court_expansion_file_url_corpus_v2.jsonl.gz` (52,798 rows)
  and `court_expansion_file_preflight_v2_4_2026-08-20/court_file_preflight_results_v2.jsonl` — joined
  by the artifact hash shared with `artifact_id`/`url_node_id`, for `source_families` context only
  (never fetched here; this is the source pipeline's own prior classification).
- `SCRAPE/sources/court_spine_20260919/courts.jsonl` (5,413 rows) — court join.
- `titles.jsonl` (optional, written by `extract_titles.py`) — first-page PDF titles.

**Total indexed rows: 50,940** (47,230 + 3,710). Every count below states which manifest(s) it covers.

## doc_type (deterministic, tested in `test_build.py`)

Priority 1: the source pipeline's own `source_families` tag (from the preflight/url-corpus join),
mapped to one of eight buckets with a fixed tie-break order when a row carries more than one family.
Priority 2 (only when no family is present or none of it maps): a regex over the URL + filename.
Never opens a PDF and never reads PDF metadata (Author, `/Title`, etc.) for this decision.

| doc_type | rows |
|---|---|
| other_unknown | 30,079 |
| administrative_financial | 7,262 |
| court_form | 4,832 |
| standing_or_general_order | 2,272 |
| calendar_notice | 2,221 |
| opinion | 2,160 |
| local_rule | 1,877 |
| fee_schedule | 237 |

`other_unknown` is large because most rows carry no `source_families` tag at all (only ~19,866 of
47,230 court-expansion rows do) and no filename/URL pattern matched; `extract_titles.py`'s first-page
text is the intended way to shrink this bucket further, one file at a time.

## Court join (re-measured this build, host-exact only, never guessed)

`court_spine.website` hostname (lower-cased, `www.` stripped) vs. the manifest's own final/source
URL host. A host that resolves to exactly one `court_spine` row is `matched`; a host used by more than
one row (the shared `*.uscourts.gov` domains — 16 measured this build — plus every multi-court state
judiciary domain, 540 hosts total map to some court, 355 of them to exactly one) is `ambiguous`, with
every candidate id listed and no court chosen. The bare national placeholder host `uscourts.gov`
(9 defunct `court_spine` rows: `californiad`, `illinoisd`, `ohiod`, `okwd`, `pennsylvaniad`,
`tennessed`, `bapma`, `usjc`, `reglrailreorgct`) is excluded from the join entirely, not attached to
any of them.

| link_status | rows (both manifests) |
|---|---|
| none (no court_spine host match) | 28,330 |
| matched (single court, unambiguous) | 13,184 |
| ambiguous (host shared by >1 court) | 9,426 |

Matched + ambiguous = **22,610** files that reach some court_spine host at all, across both manifests
combined; **13,184** of those resolve to one specific court. No county field exists anywhere in either
manifest; none is inferred from a hostname.

## State (two independent bases, never merged — repair 2026-09-19)

`state` is set only when the manifest's own `jurisdiction_codes` collapses to exactly one USPS code
(**17,603** rows). A separate `court_state` column is populated only for `link_status='matched'` rows,
from the matched `court_spine` row's own native `state` field — a native-id join, never a name match
(**7,911** rows, most of which have no manifest jurisdiction code at all). The adapter's `for_state()`
matches `state OR court_state` and reports each basis's count separately in `counts_by_basis`; the row
badge says which basis (or both) applied. This closes a gap where 8,099-ish federal-court documents
that resolve to a named court were invisible on that court's state page.

## Ambiguous-court coverage (`for_court`, repair 2026-09-19)

`for_court(court_id)` previously returned only `link_status='matched'` rows, so it returned `None` for
every court_spine court that appears only as an ambiguous candidate on a shared host — 198 courts,
40,158 candidate row-slots (e.g. `for_court('txsd')`, sharing `www.txs.uscourts.gov` with `txsb`, now
returns **825** rows in a `shared_host` bucket instead of `None`). The bucket is always separate from
`matched` and is labelled "served from a website host this court shares with other courts; not
attributed to any one court".

## Other measured counts

- Unique SHA-256 across both manifests combined: **49,108** of 50,940 rows.
- Distinct hosts across both manifests combined: **310** (222 from the court-expansion manifest alone,
  wider once the state-trial manifest's hosts are folded in).
- Extensions (top 10, both manifests): pdf 45,257 · xlsx 3,257 · docx 925 · doc 856 · xls 534 · zip 15 ·
  xml 76 · txt 12 · rss 3 · json 2.
- Titles loaded from `titles.jsonl` as of the last build: see `validation.json` `counts.titles_loaded`
  (a resumable, partial layer — see below). As of this build: **5,730** (of 41,874 `.pdf` rows).
- `host` is a first-class listing filter and result/column field (Sources-tab provenance roll-up).

## Dates

`started_at` / `completed_at` are download timestamps from the acquisition pipeline, never a legal
effective date. The manifest snapshot date is 2026-08-20. `title` is `null` until `extract_titles.py`
has processed that file's SHA-256; the index is fully usable either way.

## `extract_titles.py`

Resumable, checkpointed (`checkpoint.json` + append-only `titles.jsonl`) first-page-text extraction
over the `.pdf` rows in `index.sqlite3`, using `pypdf` (present under both
`C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe` and the codex runtime's
interpreter — nothing was installed). It reads only rendered page text, never PDF metadata /Author
fields, and never fabricates a title (`text_extracted: false`, `title: null` when no usable line is
found). Run with `--seconds N` (default 600); it always exits cleanly within budget and is safe to
resume. Re-run `build.py` afterwards to merge any new titles into `index.sqlite3` (this changes the
data-file hash, so `validation.json` is rewritten each time).

**Line-acceptance rule (repair 2026-09-19).** A candidate first-page line is accepted as a title only
if it is 15-200 chars, has >=3 words, is not junk-only punctuation/digits, is not more than 50%
digits/commas, and does not match a reject list: `Page N` pagination, a `Total` line followed by >=2
numeric tokens (table row), a street-address/suite/phone/ZIP pattern, "Please wait" (Acrobat
placeholder), or bare court-name letterhead (federal or state, e.g. "UNITED STATES DISTRICT COURT",
"COMMONWEALTH OF VIRGINIA CIRCUIT COURT"). The scan keeps trying later lines and leaves `title: null`,
`text_extracted: true` if nothing on the page survives. An independent review measured 1,603 of the
prior 4,233 extracted titles as letterhead/pagination/address/placeholder garbage; all 1,603 were
evicted from `titles.jsonl` and their sha256 dropped from `checkpoint.json` for re-extraction under the
new rule (0 of the 5,730 titles now on file fail the new rule).

## Licence / qualification

`license_ref: "sw_bulk_private_firm_work_product"`, `export_allowed: false`. Local personal-testing
view; derived from a private firm dataset built from CourtListener/RECAP API data; not for
redistribution. Link out to the source URL for each document; the saved bytes are re-hashed against
the manifest SHA-256 every time `original()` serves them.

## Limits / not done here

- robots/ToS was not checked for the 310 hosts (out of scope for this slice).
- `local_path` values in the manifests are Windows filesystem paths (absolute for the state-trial
  manifest, `staging\`-relative for the court-expansion manifest); they are converted to a
  staging-root-relative, forward-slash path and never exposed by the adapter.
- No OCR; scanned/image-only PDFs yield `text_extracted: false`.
