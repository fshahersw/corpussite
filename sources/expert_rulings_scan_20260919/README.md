# Expert-admissibility (Daubert / FRE 702) docket scan supplement

Built 2026-09-19 from a private firm's local keyword/category scan of MDL docket entries:
`C:/Users/firas/Downloads/SW-BULK/daubert_scan/daubert_documents.jsonl` (2,035 rows, read-only in place,
never modified). `daubert_stats.json` (an earlier, narrower pass by `daubert_scan.py` over the same
underlying docket corpus: 90,879 entries scanned, 61 regex hits, 36 unique documents) is carried in
`validation.json -> counts.earlier_narrower_scan_pass_daubert_stats_json` for transparency only; it is a
different run of a predecessor script and its "90,879 scanned" figure is **not** the universe size for the
1,976 rows this build actually uses that were already tagged `daubert_expert` by category matching (that
broader tagging's own scanned-universe size is not recorded anywhere available to this build).

**This is SW-BULK-derived data**: `license_ref: sw_bulk_private_firm_work_product`, `export_allowed:
false`. Local personal-testing view only; not for redistribution. Every link goes OUT to CourtListener; no
RECAP document bytes are stored or re-hosted here, only the docket entry's own short description text.

## What is in the folder

| File | Meaning |
|---|---|
| `build.py` | Offline, re-runnable build: reads the scan's jsonl and the MDL crosswalk supplement (both read-only), writes `expert_rulings_scan.sqlite3` and `validation.json`. |
| `expert_rulings_scan.sqlite3` | The supplement database (schema below). |
| `validation.json` | Uniform envelope; `license_ref`/`export_allowed` are checked by the adapter's gate in addition to `status`/`ready`/hash. |
| `test_build.py` | TDD tests for the caption classifier, the MDL-crosswalk join and the CourtListener-slug scrubber (no filesystem dependency), plus assertions against the built database. |
| `build.log` | Output of the last `build.py` run. |

## What this is, precisely

A regex/category keyword scan of docket entries (orders, motions, oppositions, replies, exhibits) for
relevance to expert admissibility under Daubert / Federal Rule of Evidence 702. **It is a scan of docket
text, not a list of rulings.** `doc_category` is the scan's own classification
(`daubert_expert` 1,976, `case_management_order` 56, `master_complaint` 1, `complaint` 1, `other` 1);
`matched_by` records whether a row was a fresh regex keyword hit (59) or was already tagged by the source
corpus's own category matching (1,976). **A grant or deny outcome is never stated or inferred** -- neither
by the source scan nor by this build.

## Caption privacy (natural-person party names)

A docket's `case_name` is published as printed only when it is a safe, consolidated MDL-style caption
("In re ...", or a title ending "... Litigation") that does **not** also contain a "party v. party"
pattern (`build.is_safe_consolidated_caption`, tested against all 23 distinct captions actually present in
the source plus synthetic edge cases). Two real rows fail that test and are redacted:

- **`MOLNAR v. MERCK & CO., INC.`** (docket `1:08-cv-00008`, D.N.J., 1 row). This docket is not an
  "individual member case" in the ordinary sense -- `sources/mdl_docket_crosswalk_20260919` independently
  identifies its native CourtListener docket id (66810477) as the **master docket of MDL 2243** ("IN RE:
  Fosamax (Alendronate Sodium) Products Liability Litigation (No. II)"): the master docket itself was
  simply never renamed off its first-filed individual caption. The crosswalk's own build already suppressed
  this exact case name for the same reason (`crosswalk.jsonl` `mdl:2243`
  `aws_case_name_suppressed_reason: "party-style caption containing a natural-person plaintiff; suppressed
  per build contract"`) -- this build applies the identical rule independently and the two agree.
- **`STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP`** (docket `2:17-md-02789`, D.N.J., 74 rows) -- the
  master docket of MDL 2789, same situation.

Redacted rows: `case_name_public` is `NULL` (never the original caption, not even alongside a flag);
`case_name_redacted_reason` explains why; the adapter's display title falls back to `"Docket <number>
(<COURT>)"`. `courtlistener_url`'s human-readable slug (e.g. `.../steven-goodstein-v-astrazeneca-.../`) is
rewritten to a generic placeholder (`build.scrub_courtlistener_slug`) so the name is not published via the
link either; the numeric docket id and entry id that CourtListener's routing actually uses are untouched.
Redacted rows' case names are excluded from `expert_scan_fts` (verified by
`test_redacted_case_names_are_not_fts_searchable` / `test_no_natural_person_names_leak_into_courtlistener_urls`),
so a name search cannot surface them either.

`"In re ..."` and other consolidated captions (e.g. `IN RE: ZOSTAVAX ... LITIGATION`, 638 rows) are
published in full -- they name products, companies and litigation titles, never a natural person.

## MDL identity: verified only through the crosswalk

Per the build contract, a row is said to belong to MDL *n* **only** when
`sources/mdl_docket_crosswalk_20260919`'s `crosswalk.jsonl` independently resolves that docket's native id
(`master_docket_id`, a CourtListener docket id) to MDL *n* (`mdl_number_crosswalk`). The scan's own
`mdl_number` field is kept too, as `mdl_number_scanned`, but purely as an unverified, as-printed value: it
is never used by the `mdl` filter or by `for_mdl()`. `build.py` verifies the crosswalk's own hash against
its `validation.json` before joining to it, and aborts rather than joining against unverified data.

Of the 23 distinct master dockets in the scan, **13 resolve** via the crosswalk (1570, 2243, 2789, 2804,
2846, 2873, 2885, 2904, 2921, 2973, 3047, 3060, 3094) and **11 do not** -- including the two largest groups
in the whole scan, Zostavax (`mdl_number_scanned="02848"`, 638 rows) and Benicar (`"02606"`, 194 rows):
real MDL numbers, printed by the source scan, simply outside this particular crosswalk's 59-MDL coverage
(it is built from one firm's own docket portfolio, not all 176 JPML MDLs). Those rows are never dropped --
`mdl_number_crosswalk` is `NULL`, they stay in the listing and in `q` search, and `for_mdl(2848)` correctly
returns `None` rather than guessing from the scanned number.

## Schema

`expert_scan_documents` (2,035 rows, one per docket entry) -- see column comments in `build.SCHEMA`.
`expert_scan_fts` (FTS5, external content) indexes `description`, `case_name_searchable` (NULL for
redacted rows), `docket_number`, `mdl_number_scanned`.

## Adapter (`delivery/archive-directory/expert_rulings.py`)

Generic view contract: `listing(params)` / `detail(id)` / `for_mdl(mdl_number)`. Fail-closed hash gate that
also checks `license_ref`/`export_allowed` were not tampered with. Filters: `q`, `mdl` (verified only),
`court`, `year`, `kind` (the scan's own `doc_category` classification). `for_mdl(n)` returns
`{mdl_number, total, results<=10, link:'#expert-rulings?mdl=<n>', qualification}` or `None` when the MDL
has no crosswalk-verified rows. `detail()` best-effort enriches a verified MDL number with its status from
the sibling `mdl_crosswalk` adapter (never raises if that adapter or its supplement is unavailable).

## What the counts mean

`validation.json -> counts`: `documents` (2,035), `case_name_redacted` (75), `doc_category_counts`,
`mdl_numbers_verified_via_crosswalk` (the 13 numbers above) / `_count`,
`mdl_number_scanned_unresolved_via_crosswalk` (the 11 scanned-but-unverified numbers, with their row
counts), `distinct_courts`, `year_range` (2005-2026).

## Limits

- Not a list of Daubert rulings and no grant/deny outcome is determined, by the source scan or by this
  build.
- `is_available`/`is_free_on_pacer`/`has_url` are the scan's own recorded flags, not re-verified against
  CourtListener at build time (no network access).
- `mdl_number_scanned` for the 11 unresolved numbers is real (these are genuine, well-known MDLs) but
  unverified by this build; do not treat it as equivalent to `mdl_number_crosswalk`.
- One of the 75 redacted rows (`case_name_public` NULL) has no `courtlistener_url` at all in the source
  scan (blank in the source data); that is a source-data characteristic, not a redaction side-effect.

## Rebuild and test

```bash
"/c/Users/firas/AppData/Local/Programs/Python/Python311/python.exe" sources/expert_rulings_scan_20260919/build.py
"/c/Users/firas/AppData/Local/Programs/Python/Python311/python.exe" -m unittest discover -s sources/expert_rulings_scan_20260919 -p "test_build.py"
"/c/Users/firas/AppData/Local/Programs/Python/Python311/python.exe" -m unittest discover -s delivery/archive-directory -p "test_expert_rulings.py"
```
