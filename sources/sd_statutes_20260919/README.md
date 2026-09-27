# sd_statutes_20260919

South Dakota Codified Laws (SDCL) title-level cache, parsed into a chapter/section index.

## Input

`C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging/sd_statutes_cache_2026-08-20/` -- 71
`title_*.json.gz` files. Each is one response from the South Dakota Legislature's public statutes API
(`https://sdlegislature.gov/api/Statutes/Statute/<title>`), captured 2026-08-20, with a `retrieval` receipt
(request URL, retrieval timestamp, HTTP status, content type/length) and a `payload` describing one
`StatuteId` node of `Type == "Title"`. This directory sits inside the private firm dataset `SW-BULK` (see
licence note below) but its content is public South Dakota Legislature text, not RECAP/CourtListener data.

**Never modified, moved or copied**: `build.py` reads the 71 `.json.gz` files read-only and writes only into
this folder.

## What is actually in the cache (measured)

All 71 files decompress to exactly one Title node each, all with `Type == "Title"` and a non-empty `Html`
body -- 71 titles, 71 with HTML, matching the plan's "71 with an Html body." The `Html` is a Word-generated
document (hashed inline CSS class names) whose text content is the **title's own chapter/section table of
contents**: chapter numbers with their catchline, and under each chapter, section numbers with their
catchline. `StatuteText` is empty on every file and there is no per-section API response in this cache --
**full statutory text is not present**, only the structural index (numbers + captions). Scope was kept to
this title-level structure per the integration plan; section-body text was explicitly parked as a later,
separate decision.

## Build

`build.py` streams each `.json.gz` (one at a time), parses `Html` with `lxml.html`, and extracts:
- `CHAPTER <n>` headers followed by their catchline paragraph.
- Section entries of the shape `<id>  <catchline>` (also `<id> to <id>. ...`, `<id>, <id>. ...`, and rules
  subsection ids like `15-6-4(a)`) using a deterministic regex over whitespace-normalised text. A matched id
  is only kept as a `section_id` when it is chapter-prefixed (`^\d+[A-Za-z]{0,3}-\d+[A-Za-z]?-`); a bare
  ordinal such as `"1"`, `"2"`, `"3"` (produced by numbered paragraphs of pleading/pretrial form text embedded
  in a few chapters) is never a real SDCL citation and is routed to `unparsed` instead.
- A run of two or three entries chained together without a blank-line gap (e.g. `"...disability . 15-3-19
  Time allowed..."` or `"...continue. 31-18-5 Liability..."`) is split at the embedded section-id boundary so
  no section's catchline is swallowed into the one before it.
- Anything that still does not match a recognised id shape is kept **verbatim** as an `unparsed` string on the
  current chapter (never split, never assigned a guessed number) -- nothing is silently dropped.
- Run again to regenerate `titles.jsonl` / `unresolved.jsonl` / `validation.json` from the same input;
  output is byte-deterministic (sorted keys, sorted title order).

Run: `& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' build.py`

## Output files

- `titles.jsonl` -- 71 rows, one per title: `id` (`sdcl:<StatuteId>`), `statute_id`, `title_number` (native
  `Statute` field, e.g. `"1"`, `"23A"`), `catch_line`, `repealed`, `request_url`, `public_url` (constructed
  1:1 as `https://sdlegislature.gov/Statutes/<title_number>` from the native `Statute` field -- **not** the
  page's own `og:url` meta tag, which is wrong or null on about half the titles because the Word export
  carries whatever chapter page it was generated from), `og_url` (the raw, unlabelled `og:url` value, kept
  for reference only, never shown as a link), `temporal` (`{captured_at, source_as_of, published_at,
  effective_from, effective_to}` each with a `*_basis`; only `captured_at` is non-null -- the source API
  publishes no effective/published date), `retrieved_at`, `http_status`, `content_type`,
  `content_length`, `source_file`, `source_file_sha256`, `chapter_count`, `section_count`,
  `unparsed_line_count`, and `chapters` (list of `{chapter_number, chapter_title, sections:[{section_id,
  text}], unparsed:[...]}`).
- `unresolved.jsonl` -- rows that could not be attributed to any chapter (none in this build; kept for
  re-runs against different input).
- `validation.json` -- uniform envelope (see below).

## Counts (this build)

71 titles, 71 with an HTML body, 831 chapters, 44,660 sections, 3,184 unparsed table-of-contents lines kept
verbatim (see `validation.json` -> `counts` for the authoritative figures). All 71 retrieval receipts report
`http_status == 200`. Counts changed from an earlier build after fixing the bare-ordinal and embedded-entry
defects described above (fewer false sections, more correctly-unparsed lines, and a small number of
previously-swallowed sections recovered).

## Limits / what this is not

- Title-level structure only: chapter and section **numbers and catchlines**, not statutory text. Do not
  present a `section_id` + `text` pair as the law itself.
- `retrieved_at` (2026-08-20) is a capture date, not an effective date; the API returns no effective date so
  `temporal.effective_from`/`effective_to`/`published_at`/`source_as_of` are always null (each with its own
  `*_basis` explaining why). `last_modified`/`etag` are empty strings in every receipt. This build ran
  2026-09-19; see `validation.json` -> `qualification` for both dates.
- `Repealed` is a boolean field on the title record; individual chapters/sections marked `[Repealed]`,
  `[Transferred]`, etc. are visible only in their catchline text (as printed by the source), never
  reclassified.
- Table-of-contents lines that do not match a chapter-prefixed section-id shape are not split into
  `section_id`/`text`; they are shown verbatim (see `validation.json` -> `counts.unparsed_toc_lines`) so
  nothing is silently dropped or guessed.

## Licence

This directory sits inside the private firm dataset `SW-BULK`. Per the build contract's blanket rule for
anything derived from SW-BULK: `license_ref = "sw_bulk_private_firm_work_product"`, `export_allowed = false`,
qualification states "not for redistribution." The underlying content is South Dakota's own public statutes
API, not CourtListener/RECAP data, and is not linked to any RECAP document.
