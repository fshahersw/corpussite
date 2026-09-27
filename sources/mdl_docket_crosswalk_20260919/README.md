# mdl_docket_crosswalk_20260919 — the id spine

No UI of its own. This is a build-time input for other slices (`mdl_docket_documents`,
`mdl_counsel_appearances`, `mdl_docket_activity`, `mdl_case_inventory`, and any future slice that needs
to join a CourtListener docket or an AWS-release matter to a JPML MDL number).

## What this is

A join of three private/local corpora, purely by native id or by `(court, docket-number)` equality —
**never by name**:

- `sources/jpml_mdl_20260919/mdls.jsonl` — the JPML MDL registry (176 MDLs, 166 pending, 10 terminated,
  as printed in the JPML report dated 2026-09-01).
- `SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc/` (`matters.jsonl.gz`,
  `matter_aliases.jsonl.gz`, `matter_relationships.jsonl.gz`, `manifest.json`) — a private firm AWS
  release, `successor` status, validation `pass`, built 2026-08-24.
- `SW-BULK/catalog/{masters.json,matters.json}` — the SW catalog's own docket projection.

Release b2b's own `descriptor.run_scope` (quoted verbatim in every `validation.json.qualification`):
`requested_docket_aliases 4186`, `returned_docket_aliases 4160`, `selected_canonical_matters 4129`.

## Join method

1. **AWS matter <-> registry MDL**: `(matter.court_id, normalize(matter.docket_number))` equals
   `(mdl.cl_court_id, normalize(mdl.master_docket))`. `normalize()` lowercases and reduces the segment
   after the last `-` to `str(int(x))`, so `1:17-md-02804` and `1:17-md-2804` are the same key. This is
   load-bearing: the exact-string join yields 0 pairs; **all 59 resolved pairs require it** (verified by
   a unit test). 57 pending, 2 terminated (2591, 2592).
2. **CourtListener docket id**: pulled from `matter_aliases.jsonl.gz` (`courtlistener_docket_id`,
   preferred, else `courtlistener_master_docket_id`) for the matched matter; falls back to
   `catalog/masters.json.master_docket_id` when no alias exists.
3. **Catalog masters** (17 rows, `SW-BULK/catalog/masters.json`): 15 carry a native, zero-padded-string
   `mdl_number`. 12 of those resolve to a registry MDL number; the other 3 (Bard IVC Filters/2641,
   Benicar/2606, Yasmin-Yaz/2100) name an MDL number that **does not exist** in the 176-row registry
   snapshot and are written to `unresolved.jsonl`.
4. **The two "parked" masters** the verifier identified (`catalog/masters.json` rows with `mdl_number:
   null`) are resolved **only** through the registry's own links, per the build contract:
   - Master `66801859` / `1:23-cv-00818` (Hair Relaxer) matches registry `mdl:3060` two ways at once:
     `cl_links.docket_id == 66801859` on the registry row, **and** the same
     `(court, normalized docket number)` equality as step 1 (its AWS matter `2a592a74-...` is already one
     of the 59). Resolved.
   - Master `4261857` / `1:14-cv-01748` (Testosterone Replacement Therapy) matches **no** registry row by
     either `cl_links.docket_id` or `(court, normalized docket number)`; the JPML MDL this case belonged
     to (MDL 2545) is not present in the 176-row registry snapshot at all. Left in `unresolved.jsonl` with
     that reason. Its AWS matter (`828788b8-...`, `case_status: supporting_master`) is also the parent of
     13 of the 13 unresolved `member_of_mdl` edges below (the other unresolved parents are the
     not-in-registry Bard/Benicar/Yasmin-Yaz masters).
5. **`member_of_mdl` edges**: `matter_relationships.jsonl.gz` has 53 rows, all `member_of_mdl`, pointing
   at 16 distinct parent matters. 12 of those parents are inside the 59-matter crosswalk (40 edges
   resolved to a `mdl:<number>`); 4 parents are not (13 edges unresolved, reason
   `"member_of_mdl parent not resolvable to an MDL"`, never dropped silently).

## Outputs

- **`crosswalk.jsonl`** (59 rows, one per resolved MDL<->master pair): `id` (`mdl:<number>`),
  `mdl_number`, `mdl_status` (`pending`/`terminated`), `mdl_title`, `cl_court_id`,
  `master_docket_number_registry`, `master_docket_number_aws`, `aws_matter_id`, `aws_case_name`,
  `aws_case_status`, `aws_date_filed`, `aws_date_terminated`, `cl_docket_id`, `cl_docket_id_basis`,
  `catalog_master_docket_id`, `catalog_master_docket_number`, `catalog_master_case_name`,
  `catalog_master_document_count`, `catalog_master_high_value_docs`, `basis` (which key(s) matched).
- **`edges.jsonl`** (40 rows): `{"from":{"type":"aws_matter","id":<uuid>},
  "to":{"type":"mdl","id":<int>},"relation":"member_of_mdl","basis":"...","evidence":{...}}`.
- **`unresolved.jsonl`** (17 rows: 4 catalog masters + 13 member-edges), each with a `reason` string.
  Never guessed, never dropped.
- **`validation.json`** — uniform envelope; `license_ref: sw_bulk_private_firm_work_product`,
  `export_allowed: false`.

## Limits / what this is not

- Not a statement that a matter is currently active — `aws_case_status`/`mdl_status` are as recorded in
  their own source, as of the release/report dates named above.
- `cl_docket_id` is the CourtListener docket id of the **master** docket only; it is not a list of every
  docket in the MDL (that is `edges.jsonl` `member_of_mdl` plus whatever future slice enumerates all
  member matters).
- No document content, no party names, no dollar amounts. This slice is ids and relationships only.
- Read-only against `SW-BULK` (main tree only, never `.worktrees/`) and `sources/jpml_mdl_20260919`;
  nothing there was modified.

## Adapter

`delivery/archive-directory/mdl_crosswalk.py` (fail-closed hash gate on this folder's `validation.json`):
`listing(params)` / `detail(mdl_id)` (one row per MDL, 59 total, coverage-depth columns), plus lookup
helpers other adapters may import: `for_mdl(mdl_number)`, `mdl_for_docket(cl_docket_id)`,
`mdl_for_matter(matter_id)`, `members(mdl_number)`.
