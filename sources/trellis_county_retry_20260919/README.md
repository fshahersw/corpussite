# Trellis county profile retries (September 19, 2026)

One retry of each county-profile URL that `sources/trellis_county_firecrawl_20260919` left unresolved, plus a reviewed
name crosswalk for the 11 county labels `sources/trellis_coverage_20260919` could not match. Licence: Trellis publisher
page; internal research use; not for redistribution.

## Result

**No new profile was obtained, and no gap changed.** 29 requests, 29 credits (account 3,678 -> 3,649; cap 45, floor 500),
0 accepted. The retry gate rejected all 29 responses. Reconciled against what was already published:

- **27 genuine URL gaps remain**: GA Lowndes plus 26 Virginia `<name>city` URLs. They are exactly the 27 `remaining_urls`
  the coverage layer already lists (`sources/trellis_coverage_20260919/progress.json`, as of 09:36 UTC). `finalize`
  checks that the two sets are equal.
- **2 were already recovered elsewhere** and are not gaps: OK Major (population 7,527, seat Fairview) and OK Seminole
  (25,482, Wewoka). `sources/trellis_county_offline_supplement_20260919/resources.jsonl` recovered both from the prior
  run's saved responses with no request (validated 09:35 UTC, 2.5 hours before this retry at 12:11 UTC), and the
  coverage layer already publishes them with `detail_captured = true`.

**All 29 outcomes were already determinable from local files before the requests were sent, so the 29 credits bought no
new information. Do not run it again.** Per row, `gaps.json` records `outcome_known_locally_before_retry` and its basis:

| Group | URLs | What was already on disk | What the retry returned |
|---|---|---|---|
| OK Major, OK Seminole | 2 | Published by the offline supplement at 09:35 UTC. | Fresh render (`max_age_ms` 0); markdown and HTML identical to the prior response. Rejected again only because this gate keeps the prior run's 100-character floor (94 and 96 characters). |
| GA Lowndes | 1 | `reports/trellis_refocus_20260919/lowndes_browser_check.json`: a signed-in browser check found a heading and no profile fields (file dated 2026-09-19, last written 09:34 UTC by filesystem time; it records no time of day itself). | Fresh render; identical to the prior response: heading only. |
| Virginia `<name>city` slugs | 26 | Prior 404s were provider cache misses at 09:22-09:23 UTC, inside the 48-hour max-age these requests reused, so a cache replay was guaranteed. | HTTP 404 "Page Not Found", all 26 with provider `cache_state: hit` (`retry_cached_at` 09:22-09:23 UTC). These are **not** a second independent observation of trellis.law. |
| Pennsylvania website-link artifacts | 2 | The prior owner proved these are county website links misread as profile URLs. | Not retried (`excluded_from_retry.jsonl`). |

Standing guidance said not to do this: `reports/trellis_refocus_20260919/NEXT_RUN.md` (last written 09:38 UTC) states
"Do not relaunch it or retry unchanged failures" and "Do not spend on unchanged gaps", and names the same 27 gaps, the
Lowndes browser check and the two offline recoveries. The retry was queued as item 2(d) of the `gap-backfill` workflow
in `reports/corpus_upgrade_20260919/STATE.md` ("retry of 31 failed Trellis county captures"); the builder did not read
NEXT_RUN.md, the offline supplement or the coverage layer's progress file before spending. The BRIEF's network rule
"no automatic retries" was kept only in the narrow sense of one attempt per URL within this batch.

### Virginia cities: what is actually saved

Use the coverage layer's identity review, which sees every saved Trellis profile (historical catalog, browser captures,
both provider packages). `gaps.json` copies it per row (`coverage_fips`, `coverage_alternate_saved_urls`):

- **24 of the 26** cities already have another saved profile that resolves to the same Census identity, for example
  `/virginia/alexandria` for Alexandria city (51510) and `/virginia/norfolk` for Norfolk city. This includes Alexandria,
  Bristol, Buena Vista, Charlottesville, Chesapeake, Colonial Heights, Danville, Fredericksburg, Hampton and Hopewell.
  The `...city` URL itself stays unsaved; it is a failed URL variant, not a missing city.
- **2 of the 26** have no matched alternative: Richmond city (51760) and Roanoke city (51770). Their shorter slugs are
  county pages (Richmond County, Roanoke County), and NEXT_RUN.md holds `/virginia/roanoke` as an identity conflict.

The narrower field `sibling_url_saved_in_prior_run` (with `prior_batch_sibling_heading*`) only says whether the same
slug without `city` sits in the prior Firecrawl batch: 14 yes and naming a city, 2 county pages, 10 null. Null there
does **not** mean "no saved profile": the coverage layer holds an alternate for all ten from captures outside that
batch (checked for one: `/virginia/alexandria`, captured 2026-09-13; the other nine were not opened individually).
No slug was guessed or requested.

## Request settings

Same as the prior run: existing private DPAPI wrapper (`scripts/judge_firecrawl_private.py`), Firecrawl CLI `scrape`,
formats `markdown,html,links`, include-tag `.top-county-info-block__container`, proxy `basic`, no cookies, no stealth, one
attempt per URL, starts registered before the call. Concurrency 3 (prior run 5), 2 s pause between batches, credit check
after every batch, shared host hold checked before every batch. The key stays in memory and the child environment; logs are redacted.

One deviation, recorded per seed in `seeds.jsonl`: the three 200-status pages used `--max-age 0` so the provider would
render them again. The 26 404 URLs kept the prior 48-hour max-age, which is why they were answered from cache.

Rejected on sight: status other than 200, redirect or URL mismatch, challenge text, login shell, empty body, missing
"Courts Records" heading or container, fewer than two fields or under 100 characters. Access statuses 401/403/429, an
unexpected proxy or an unexpected credit cost stop the run. None of the stop conditions fired.

### Guard added in the repair (offline; nothing was re-sent)

`collect_retry.py` now refuses this kind of spend. `guard_refusal()` drops a URL when it is already in the offline
supplement (hash-gated on that folder's `validation.json`), when the browser check already settled it, or when its
prior 404 is younger than the requested max-age (unknown age counts as covered). `prepare` applies it and, because
requests were already sent, only prints the plan and exits without rewriting `seeds.jsonl`, which stays the frozen
record of what was requested. `run` applies the guard again and exits before loading the key or checking credits when
nothing is pending. With today's inputs the guard allows 0 of the 31 URLs (2 published, 1 browser-checked, 26 cached
404s, 2 artifacts).

## Files

- `seeds.jsonl` (29): frozen URL list with the prior failure reason, prior raw hash, observed parent link and max-age.
- `excluded_from_retry.jsonl` (2): the website-link artifacts.
- `started.jsonl`, `receipts.jsonl` (29): one receipt per request: times, return code, raw path, SHA-256, size, provider scrape id, proxy, cache state, credits, status, source and final URL, page title, outcome and reason. Provider JSON is a connector response, not original HTTP bytes.
- `raw/` (29 provider JSON responses, unchanged), `logs/` (redacted CLI output). Nothing was accepted, so there are no text or HTML reading copies.
- `credit_receipts.jsonl` (11), `run_state.json`: balance before the run and after each batch.
- `resources.jsonl` (0 rows): same schema as the prior run's manifest, plus additive `retry_of` and `temporal` blocks. Empty because nothing passed the gate.
- `gaps.json`: `genuine_gaps_after_retry` (27) and `already_recovered_elsewhere` (2). Each row has both reasons, status, cache state and cache time, whether the content equals the prior response, why the outcome was already known, `already_published_in`, `browser_check`, the coverage layer's FIPS and alternate saved URLs, and the narrow prior-batch sibling fields. `coverage_layer_reconciliation` and `field_scope_notes` explain the rest.
- `county_name_crosswalk.json`: 2 resolved, 9 unresolved (below).
- `validation.json`: uniform envelope. `passed` means the batch is complete, within budget, hash-bound and reconciled with the coverage layer. It does not mean gaps were closed.
- `collect_retry.py` (`prepare` | `run` | `finalize`), `build_crosswalk.py`, `inspect_prior.py`, `test_retry.py` (15 offline tests).

Re-running `finalize` and `build_crosswalk.py` is offline and safe. `finalize` reads the offline supplement and the
coverage `progress.json` only after their own validation gates and hashes match, and fails validation if the genuine
gaps stop matching the coverage layer's remaining URLs.

## Crosswalk for the 11 unmatched labels

Resolved only where a reviewed, documented normalisation gives exactly one inventory county in the same state
(`delivery/focused_legal_corpus/counties/counties.jsonl`, 3,144 rows) and no coverage row already carries that FIPS.

| State | Trellis label | Census county name | FIPS | Basis |
|---|---|---|---|---|
| NM | Dona Ana County | Doña Ana County | 35013 | Missing tilde only; one match after folding diacritics. |
| VA | Roanoke County Circuit Courts Records | Roanoke County | 51161 | Label is a page heading; literal suffix removed. Row's publisher fields (seat Salem, roanokecountyva.gov) agree. Roanoke city 51770 is separate and untouched. |

Unresolved (FIPS stays null): the eight Connecticut historical counties (Fairfield, Hartford, Litchfield, Middlesex,
New Haven, New London, Tolland, Windham), because the inventory vintage lists nine planning regions instead and the two
do not map one-to-one; and Delaware "Court of Chancery", a statewide court venue, not a county.

The review was done by the agent against the inventory, not by a person. Confirm the two rows before publishing them.
NEXT_RUN.md holds the `/virginia/roanoke` page as an identity conflict (parent link says city, heading says County);
the Roanoke County row rests on the heading and publisher fields, so treat it as the less certain of the two.

## Folding this into `trellis_coverage_20260919` (for that folder's owner)

Nothing here needs a profile merge or a gap change: `resources.jsonl` is empty, so do **not** add this folder as a
provider allowlist entry. The only usable addition is the two-row name crosswalk.

1. Check the gate: `validation.json` has `status == "passed"`, `ready == true`, and each `data_files[].sha256` matches the file.
2. Names: in `build.py`, where a county has no exact name + state match (the branch that writes
   `unresolved.append({... 'No exact county name + state match ...'})`, near line 431, and `provider_identity` for the
   Roanoke heading), look the pair `(state, county label)` up in `county_name_crosswalk.json` -> `resolved[]` by
   `state` + `trellis_label`. On a hit, set `fips` to the row's `fips` and `fips_basis` to
   `"reviewed crosswalk sources/trellis_county_retry_20260919/county_name_crosswalk.json: <normalisation>"`. Keep the
   Trellis label as the display name or switch to `census_county_name`; either way keep the original label on the row.
   Add this file and its SHA-256 to your `inputs`. Expected effect: `unmatched_county_names` 11 -> 9, two more rows with
   FIPS (35013, 51161), two new `fips:` edges. Re-check before applying that no other row carries those FIPS (none did
   at 2026-09-19 12:12 UTC).
3. Gaps: **change nothing.** Keep your 27 `remaining_urls`, your 24/2 identity review and the two Oklahoma rows exactly
   as published. Do not import rows from this folder's `gaps.json` into your gap list: two of its 29 rejected responses
   (Major, Seminole) are profiles you already publish, and its sibling field is narrower than your review. Optional
   annotation only: for the 27 URLs you may cite `gaps.json` -> `genuine_gaps_after_retry[]` (keyed by `url`) as "second
   provider response on 2026-09-19 12:11 UTC, identical content; the 26 Virginia 404s were cache replays".
4. Major and Seminole: nothing to do. They are already in your layer through the offline supplement; this folder's
   second responses for them carry the same two fields and add nothing.
5. Rebuild and re-validate your layer yourself; this folder does not touch it.

## Not verified

Whether trellis.law serves Richmond city or Roanoke city under another slug (no discovery was done); whether the 404s
would repeat on a genuinely fresh fetch (they were only ever observed once, at 09:22-09:23 UTC); the time of day of the
Lowndes browser check beyond the file's modification time; field currency or accuracy of any publisher value; the two
crosswalk rows by a human reviewer. The coverage layer's 24/2 identity review is copied here, not re-derived.
