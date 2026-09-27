# County seed packets — Firecrawl frontier preparation (no network, no ingest)

Reviewed candidate seeds for the county litigation Firecrawl collector
(`sources/county_litigation_firecrawl_20260919/collect.py`), prepared from the
Firecrawl state-court URL frontier in `SW-BULK/source_gap_reconciliation_2026-08-22/`
(`state_map_group1/2/3.jsonl`, 19,245 rows, plus `sources_state_courts_zero_coverage.jsonl`,
22 rows). This folder does **not** call the network and does **not** write to the
collector's queue. `collect.py ingest --seeds <path>` is a separate, later step
an operator runs by hand.

Run: `python build.py` (re-runnable; deterministic given the same snapshot of the
collector's `queue.sqlite3` and the published county registry supplement, both
opened read-only).

## What the filter keeps

A frontier row becomes a seed only if all of the following hold:

1. `authority_tier == "official_primary"` and `retention_class != "quarantine"`.
2. The URL host matches `(court|clerk|judici|county)` case-insensitively — an
   official court/clerk/county-government host, not a generic state agency host.
3. The URL path does not match the collector's own `EXCLUDE` regex (news,
   events, careers, procurement, elections, parks, tourism, social media, ...).
4. The title or URL path matches the collector's own `TOPIC` vocabulary (rules,
   forms, clerk, filing, docket, probate, family, self-help, ...) — the same
   test `collect.py` uses to decide whether a *discovered* link is worth
   following, applied here to the *frontier* rows.
5. The exact URL is not already a row in
   `sources/county_litigation_firecrawl_20260919/queue.sqlite3` (any status)
   and not already present anywhere in the published county supplement
   `sources/county_registry_integration_20260919/counties.json`.

## County resolution (the part most likely to need a human second look)

**64% of accepted frontier rows carry an empty `title`** (measured:
`rows_with_empty_title` in `validation.json`). A title match alone would
therefore leave the large majority of otherwise-good county rows
misclassified as statewide, so resolution runs in two steps against each
surviving row, scoped to its own `jurisdiction` (state):

1. **Title match.** Check the row's own `title` field for an exact Census
   county name (whole-word, case-insensitive).
2. **URL-path fallback.** If the title is empty or matches nothing, unquote
   the row's own `canonical_url` path, turn `-`/`_`/`/` into spaces, and run
   the same match against that text (e.g.
   `/locations/alamance-county/alamance-county-local-rules-and-forms` ->
   `Alamance County`). This is still "the row's own fields", per the brief —
   no external lookup.

The reference list is
`sources/official_courts/datasets/2026_Gaz_counties_national.json` (3,222 US
counties/county-equivalents, `NAME`+`GEOID`+`USPS`), read-only, not copied.
Matching is: for the row's state, try each county's full Census name
(`"Autauga County"`, `"Orleans Parish"`, `"Denver city"`, ...) as a whole-word,
case-insensitive substring of the text, **longest name first** (so `"New York
County"` is matched and accepted before the shorter `"York County"`, which is
textually contained inside it, is even tried).

- A title match -> `seeds_county.jsonl`, `association.status:
  "title_county_name_match"`.
- No title match but a URL-path match -> `seeds_county.jsonl`,
  `association.status: "url_path_county_name_match"`. Both carry
  `county_fips`/`county_geoids` set to that county's 5-digit GEOID.
- No match in either -> `seeds_statewide.jsonl`, `association.status:
  "no_title_and_no_url_match"`, `county_fips: null`, `county_geoids: []`.
- This is a **name match on the row's own text**, not a content review. It
  does not attempt to resolve county names embedded without spaces in a
  hostname (e.g. `adaircircuitcourt.ky.gov`). A wrong or missing match only
  ever produces a false statewide classification (safe: no FIPS asserted),
  never a wrong FIPS from a different match, because match names are the
  full, disambiguated Census strings.

## Prioritisation

County seeds get `priority: 1` if their county currently has **zero saved
local resources**, else `priority: 2`. Statewide seeds are `priority: 3`.
"Zero saved local resources" = the county's 5-digit FIPS has no row with
`status='downloaded'` in the collector's own `queue.sqlite3`, and no
court/clerk entry in `sources/county_registry_integration_20260919/counties.json`
(`counts.courts + counts.clerks == 0`). This is necessarily a lower bound: the
registry supplement only covers KY and TX (374 of 3,222 counties), so a county
outside those two states is judged solely on the Firecrawl collector's own
download history.

## Outputs

- `seeds_county.jsonl` — seeds with a resolved county FIPS, schema-compatible
  with `collect.py`'s `seed_ok()` gate (verified by `test_build.py`).
- `seeds_statewide.jsonl` — seeds with no county match; `applicability.level`
  is `"state"`, not `"county"`.
- `rejected.jsonl` — every dropped candidate with its exact reason
  (`quarantined`, `not_official_primary`, `host_not_official_court_clerk_county`,
  `excluded_topic_path`, `no_collector_topic_match`, `already_in_queue`,
  `already_in_published_supplement`, `duplicate_within_batch`).
- `coverage_gain.json` — per-state counties newly reachable by this batch, and
  the subset that were at zero saved local resources before this batch.
- `validation.json` — uniform envelope; `checks` lists what was enforced.

## Licence

The input frontier (`SW-BULK/source_gap_reconciliation_2026-08-22/`) is derived
from a private firm dataset (`SW-BULK`'s own README: "Private repository.
Contents include firm work product; do not fork, mirror, or grant external
access."), itself built from CourtListener/RECAP API data. `validation.json`
therefore carries `license_ref: "sw_bulk_private_firm_work_product"` and
`export_allowed: false`. This is a local personal-testing view only; not for
redistribution.

## Limits / what this is not

- Not fetched, not scraped, not ingested. No Firecrawl credits spent.
- Not a completeness claim for any state or county: this is one frontier
  (19,267 input rows) filtered and classified, not a full crawl of all US
  county court websites.
- `source_authority.verified` is always `false` and
  `applicability.status` is always `"destination_review_required"` — an
  operator (or the collector's own scrape-time `quality()` check) still has to
  confirm the destination is what the title says before it is trusted.
- The Census gazetteer file is read from its existing location
  (`sources/official_courts/datasets/2026_Gaz_counties_national.json`) and is
  not copied into this folder; its sha256 at build time is recorded in
  `validation.json` `inputs`.
- To actually queue these, an operator runs
  `python collect.py ingest --seeds sources/county_seed_packets_20260919/seeds_county.jsonl`
  (and separately for the statewide file, once statewide handling is decided —
  this folder does not do that itself).
