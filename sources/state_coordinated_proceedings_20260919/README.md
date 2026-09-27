# state_coordinated_proceedings_20260919

New Jersey Multicounty Litigation (MCL) and California Judicial Council Coordination Proceedings (JCCP) --
the two state coordination systems most relevant to mass-tort litigation, built as a local, offline
transform of two already-local inputs. No network access, no agents.

## Inputs (read-only; main tree only, never modified)
- `C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_nodes.csv` (78 rows: 40 `njmcl`, 8 `njjudge`, 30 `drug`)
- `C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_edges.csv` (57 edges: `concerns_product` 35, `presides` 22)
- `C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_graph.json` -- read as a cross-check only (its `mcls`
  array mirrors the nodes CSV and its `federal_state_pairs` links via an internal `master_docket_id`, not
  an MDL number named by the source; per the build contract, published federal MDL links come only from
  `njmcl_registry_suggestions.json`'s own `mdl_number` field, so `federal_state_pairs` is not published).
- `C:/Users/firas/Downloads/SW-BULK/catalog/njmcl_registry_suggestions.json` (19 draft entries + a README
  key; 14 carry a structured `mdl_number` field, 13 of which parse to a number -- see below)
- `C:/Users/firas/Downloads/returnedfiles/ca_jccp_registry.jsonl` (1,392 rows; the loose file, not the
  `session_leftovers.tar.gz` copy, per the plan's decision item 3)
- `C:/Users/firas/Downloads/SCRAPE/reports/geography/input_snapshots/census.json` (3,144 Census counties;
  used only for the `usps` + county-name -> `geoid` (5-digit FIPS) crosswalk)
- `C:/Users/firas/Downloads/SCRAPE/sources/jpml_mdl_20260919/mdls.jsonl` (176 MDLs; used only to label a
  source-named MDL number `pending` / `not_in_registry` -- never to invent a link)

## What was built

### NJ Multicounty Litigation (40 rows, `type: "nj_mcl"`, id `njmcl:<slug>`)
Measured fill rates over the 40 `njmcl` nodes (confirmed against Verification 2, not the original plan's
higher estimate): `county` 30/40, `judge` 22/40, `designation_date` 21/40, `archived` 40/40,
`document_count` 40/40, `source_url` 40/40 (all `njcourts.gov`, all distinct). Only **3 distinct counties**
appear: Atlantic, Bergen, Middlesex -- so the county/FIPS filter is necessarily thin. Every missing field is
published as `null` with `*_basis: null`; nothing is inferred. `county_fips` is set only on an exact
state+county name match against the Census list (all 30 populated county values matched; NJ has 21
counties total).

`judge_name_text` and the graph's `presiding_judges_from_graph_edges` (from the 22 `presides` edges) are
published as **printed text only** -- there is no judge-entity link, no `cl_person_id`, no FJC id. NJ state
MCL judges are not FJC/CourtListener judges, and one of the 8 `njjudge` nodes
(`njjudge:john-r-tunheim`) reads as a federal judge's name inside a state judge list; publishing it as
unlinked text (not merging it into `judge_structured`) is the correct call per the plan's own warning.

`drugs` comes from the 35 `concerns_product` edges (NJ MCL is not exclusively drug litigation -- asbestos,
environmental and institutional-abuse proceedings have none).

**Federal MDL cross-links** (`related_mdls`, plus `edges.jsonl` relation `names_federal_mdl`): parsed only
from `njmcl_registry_suggestions.json`'s own `mdl_number` field (e.g. `"16-md-02741"` -> `2741`), matched to
an njmcl node id by taking the text before the first `(` in that entry's `njmcl` field (the parenthetical is
always commentary in this file, e.g. `"pinnacle-... (NOTE: ...)"`). **13 entries** parse to a distinct MDL
number; **7 of those 13** resolve to a `pending` registry MDL: 2741 (Roundup), 2738 (talc), 2740
(Taxotere), 2848 (Zostavax), 2782 (Physiomesh) -- the five the file's own README flags as "not yet
materialized" -- plus **2768** (Stryker LFIT) and **2921** (Allergan Biocell), which the README does not
call out but which do carry a parseable `mdl_number` and do exist as `pending` in the registry. The other
6 (2734, 2244, 1626, 1943, 2434, 2331) parse but are not in the local MDL registry; they are still published
on the njmcl record with `registry_status: "not_in_registry"`, never dropped. The two additional numbers
(2187, 2327) that appear only inside a free-text note on the `pelvic_mesh` entry are **not** parsed --
the contract's instruction is to parse the `mdl_number` field, not free text, and that entry's own
structured `mdl_number` is `null`.

### California JCCP (1,392 rows, `type: "ca_jccp"`, id `jccp:<jccp_no>`)
1,392 distinct `jccp_no` (0 null). 1,258 rows carry at least one county (58 distinct CA counties, the full
state, all exact-matched to a FIPS code); 1,037 carry a case number. Category split: `other` 952 /
`wage_hour` 351 / `masstort_candidate` 89. `log` field (the source's own era bucket): `2017-and-older` 870 /
`2018-present` 522 -- used as-is for era grouping, per Verification 2's finding that a derived split from
`date_received` gives a different (868/524) and less reliable answer.

**The `judges` array is dropped entirely and never published.** It is PDF-extraction noise (measured
example, JCCP 4961: `["Assigning Hon. Susan I.", "Marie", "Marie S.", "Marie S. Wiener", "Marie Weiner",
"Susan Irene Mateo Request"]` -- fragments plus a misspelling), confirmed file-wide (3,973 judge strings on
980 rows, roughly 2,512 single/two-token or trailing-initial fragments). It is not name-matched to any
judge layer.

`date_received_iso` is parsed from the printed `MM/DD/YY` with a documented century rule (00-26 -> 20xx,
27-99 -> 19xx, relative to this 2026 build) and left `null` -- logged to `unresolved.jsonl` -- on the 5
malformed rows measured in Verification 2 (jccp 4638 `"8/18/201"`, 5480 `"7/13/267"`, 5351 `"8/13/243"`,
4616 `"2/4/2010"`, 5035 `"4/25/2019"`). `date_received_basis` says this is a filing-receipt date, not an
effective date. `text` (raw concatenated PDF extraction) is kept out of the listing surface; a `detail()`
caller may show it labelled "raw extracted text, unedited" via `raw_text_available`.

Neither system records a capture date; `captured_at` is `null` on every row with an explicit basis string
rather than a guess.

**`title` is also raw PDF-extraction text, not a clean case name** (measured: 383/1,392 rows carry a
court/judge token or an embedded date, e.g. `jccp:4512` = `"Electric Refund Cases LASC MJ, Carolyn B. LASC
PJ, ... 7/07/07 ..."`). The published `title` is cut at the first noise token (`LASC`, `OCSC`, `SF PJ`,
`Sac PJ`, `Hon.`, or an embedded `M/D/YY`-style date) and a trailing connector word is stripped; the
untouched source string is kept separately as `title_raw`, and `title_is_extraction_noise` (measured true on
494/1,392 rows) flags every row where the published title differs from the source, including a fallback
`"JCCP <jccp_no> (no clean case name in the source)"` on the 6 rows where nothing clean remains. The
free-text search haystack in `delivery/archive-directory/state_proceedings.py` is built from the cleaned
`title` only; `title_raw` is surfaced solely inside `detail()`, labelled "Raw extracted text, unedited".

## Outputs
- `proceedings.jsonl` -- 1,432 rows (40 NJ + 1,392 CA), one JSON object per line, sorted keys.
- `edges.jsonl` -- 13 `names_federal_mdl` rows (`from: njmcl:<slug>`, `to: mdl:<number>`), each carrying
  `registry_status` and full `evidence`/`basis` strings.
- `unresolved.jsonl` -- 5 rows: the 5 malformed CA `date_received` values. (No county name failed to
  exact-match; both crosswalks are 100% coverage over the values actually present.)
- `validation.json` -- uniform envelope; `license_ref: "sw_bulk_private_firm_work_product"`,
  `export_allowed: false` (the NJ side is derived from SW-BULK; the CA side has no stated license and is
  treated at the same conservative level here since both ship from the same supplement).

## Known limits (state in every qualification)
- NJ: only 3 of 21 counties are ever populated; the county/FIPS filter on the planned `#state/NJ` block
  resolves to at most 3 FIPS codes, not full state coverage.
- Judge names (both states) are printed text only -- treat any apparent name collision with a federal or
  other state judge as coincidental, never merge.
- `njmcl_registry_suggestions.json` is explicitly a draft file ("REVIEW REQUIRED, nothing auto-merged");
  its `mdl_number` values are proposals from that pipeline, not a JPML or CourtListener assertion. This
  build treats them as "the source names this MDL number" for linking purposes only, and always shows the
  raw field and the registry outcome (pending / not_in_registry) rather than asserting a match.
- No SW-BULK license file exists; `SW-BULK/README.md` states "Private repository. Contents include firm
  work product; do not fork, mirror, or grant external access." -- carried into `license_ref` /
  `export_allowed` above.
