# mdl_counsel_appearances_20260919

Counsel appearances, attorneys, firms and parties from a **second, independent pipeline's** output in the
SW-BULK AWS release `b2b-cdbb8d040b95c7b65cfc` (`attorneys.jsonl.gz`, `firms.jsonl.gz`,
`counsel_appearances.jsonl.gz`, `parties.jsonl.gz`, and `matter_relationships.jsonl.gz` for the
member-of-MDL-parent-unresolvable check below). This is a different source from
`sources/mdl_counsel_20260919` (a CourtListener search-index scrape); the two layers are published side by
side, each labelled with its own source, and are **never merged**.

Run: `C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe build.py`
(offline, streaming, deterministic, re-runnable). Tests: `python -m unittest discover -s <this dir> -p test_build.py`.

## Files

- `attorneys.jsonl` — 181 attorney appearance records (native `attorney_id` UUID from this pipeline; **not**
  a CourtListener attorney id), name, appearance count, firm ids reached, MDL numbers reached (extended
  linkage, see below). These 181 records cover only **74 distinct name spellings** (lowercase, letters-only
  normalisation, used solely to report this count — never to merge records): 39 names carry more than one
  record (e.g. "Christopher A Seeger" holds 31 separate UUIDs across capitalisation/punctuation variants).
  Per BUILD_CONTRACT, records are **never merged by name**, so an attorney detail page for one UUID shows
  only that UUID's appearances — the same person may hold other UUIDs elsewhere in this dataset.
  `validation.json` reports both `attorney_records_total` (181) and
  `attorney_records_distinct_normalized_names` (74).
- `firms.jsonl` (12) — native `firm_id` (this pipeline's own canonical slug, e.g. `seeger_weiss`), canonical
  name, appearance count, MDL numbers reached. Firms are never merged by name; there is no other firm
  identity space here to merge against.
- `appearances.jsonl` (878) — one row per counsel appearance: attorney id/name, firm id/name, raw firm name
  (kept even when it differs from the firm's canonical name), matter id, party id, party side (plaintiff /
  defendant, from the linked party's `party_types`, when a party is linked), raw role, normalised role label,
  parsed termination date (only for the one `TERMINATED: MM/DD/YYYY` row), and both the direct and
  crosswalk-extended MDL linkage (see below).
- `parties.jsonl` (503) — one row per party. `listable` is true only for organisations and named defendants;
  a natural-person plaintiff-side party has `name: null` and a `name_withheld_reason`, and is represented
  only as a count (see `party_counts_by_matter.jsonl` and the `party_categories` counts in `validation.json`).
- `party_counts_by_matter.jsonl` — per-matter totals: total parties, listed parties, organisation count,
  named-defendant count, and the natural-person-plaintiff count that is never listed by name.
- `edges.jsonl` (607) — `attorney:<uuid>` -> `mdl:<number>`, relation `appeared_in`, emitted only when the
  appearance's matter resolves to an MDL (direct or via the crosswalk's `member_of_mdl` edges); each edge
  carries the appearance id, firm id, raw role and which linkage path resolved it.
- `unresolved.jsonl` (405 = 271 appearances + 134 parties) — appearances/parties whose matter does not
  resolve to any MDL even after the crosswalk extension. Never dropped silently, never guessed. Most rows
  carry the generic reason `matter_id not present in crosswalk.jsonl (direct) nor reachable via edges.jsonl
  member_of_mdl (extended)`. A distinct subset — **61 appearances / 19 parties**, flagged with `evidence.
  parent_matter_id` and the reason `member_of_mdl parent matter <id> is not resolvable to an MDL in
  crosswalk.jsonl` — are matters the AWS release's own `matter_relationships.jsonl.gz` DOES state are
  member cases of an MDL parent, but that parent itself is one of the 13 (of 53) member relationships whose
  parent matter falls outside the 59-matter crosswalk (a fixable crosswalk gap, not "this matter is unknown"
  per INTEGRATION_PLAN Verification 1). `validation.json`'s
  `unresolved_member_of_mdl_parent_unresolvable` carries these two counts.
- `validation.json` — uniform envelope; `counts` carries every headline number below.

## MDL linkage — two numbers, always reported together

A matter resolves to an MDL two ways, per the BUILD_CONTRACT:

- **Direct**: `sources/mdl_docket_crosswalk_20260919/crosswalk.jsonl`'s `aws_matter_id -> mdl_number`
  (the 59-matter crosswalk). Reaches **3 MDLs / 192 appearances / 67 parties**.
- **Extended**: direct, plus `crosswalk.jsonl`'s sibling `edges.jsonl` `member_of_mdl` edges (a member
  matter's parent resolves to an MDL in the crosswalk). Reaches **14 MDLs / 607 appearances / 369 parties**
  (40 of the crosswalk's 40 resolved member edges are used here; matters not found in the crosswalk at all
  stay unresolved).

Both figures are always published together; the UI/adapter must never show only the extended number. The
`delivery/archive-directory/mdl_appearances.py` adapter's `for_mdl()` embedding hook and `listing()`'s
per-row `linkage` cell/badge (from each appearance's `mdl_match_basis`: `direct` / `member_of_mdl_edge`)
enforce this at the UI layer, so no MDL detail page or listing row shows an unqualified extended-only count.
`sources/mdl_docket_crosswalk_20260919` is read-only input here and was not modified.

## Compact qualification for embedding blocks

`validation.json` carries two qualification strings: `qualification` (the full ~1000-character version above,
for `listing()`/`detail()`) and `qualification_short` (under 500 characters, for `for_mdl()`'s compact
embedding block on the MDL detail page, per BUILD_CONTRACT's embedding size ceiling).

## Role normalisation

9 raw spellings measured in `counsel_appearances.jsonl.gz`: `attorney_to_be_noticed` (397),
`ATTORNEY TO BE NOTICED` (205), `LEAD ATTORNEY` (140), `lead_attorney` (64), `PRO HAC VICE` (43),
`terminated` (19), `'10'` (8), `'TERMINATED: 06/29/2010'` (1), `'unknown'` (1). Normalised to 4 labels:
Attorney to be noticed, Lead attorney, Pro hac vice, Terminated, Unknown. `'10'` is CourtListener's Role
choice 10 ("Unknown"; see `sources/mdl_counsel_20260919/coverage.json` `role_labels`) and is merged with the
literal string `'unknown'` — both raw values map to the same normalised label, but the raw value is always
kept alongside it. The one `'TERMINATED: 06/29/2010'` row normalises to "Terminated" with the date parsed
out separately as `terminated_date_raw`.

## Party person/organisation heuristic (conservative)

`is_organization(name)` matches an explicit business/entity/government-body token (Inc, LLC, Corp, Company,
Ltd, Trust, Partners, Association, Bank, University, Hospital, Pharmaceutical(s), Healthcare, Holdings,
Foundation, "City of"/"County of"/"State of", Department of, Agency, Authority, ...), **unless** the name
also contains a wrapper phrase that indicates a natural person is inside ("estate of", "personal
representative", "administrator of", "individually", "next of kin", ...), which always wins. Anything that
matches neither rule is a natural person. **When unsure, the party is counted, never listed by name** — this
is deliberately conservative and will undercount organisations before it ever discloses a plaintiff's name.
Measured: 5 organisation parties, 0 rows where a natural person also carries a `Defendant` role (the 2
measured `Defendant`-tagged rows, both Bayer Healthcare entities, are organisations by the token rule first),
498 natural-person plaintiff-side rows published as counts only.

## What this is not

- Not a bar-admission record; not every firm appearing in these MDLs — only the 12 firms this AWS pipeline
  targeted (a Seeger Weiss-adjacent plaintiff-firm cohort).
- Attorney ids here are this pipeline's own UUIDs. They are **never** merged with the CourtListener attorney
  ids in `sources/mdl_counsel_20260919` — publish the two layers side by side, each labelled with its source.
- No emails, phone numbers or street addresses are published anywhere in this dataset (none exist in the
  source rows).
- License: `sw_bulk_private_firm_work_product`, `export_allowed: false` — local personal-testing view only.
