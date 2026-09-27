# Judge evidence layer (2026-09-19)

Offline build from local data only (no network). Adds citable evidence blocks to saved judge entities
(`judge_entities`, 10,669 rows in the `judge_structured_20260919` overlay). Adapter:
`delivery/archive-directory/judge_evidence.py` (fail-closed hash gate). Not yet wired into `server.py` / `judges.py`.

## Files
- `build.py` re-runnable, deterministic apart from `validated_at`. `test_build.py` 7 tests.
- `evidence.jsonl` one row per entity that gained at least one block (3,983 rows).
- `unresolved.jsonl` every CJRA printed judge and every SW-BULK judge row that was NOT attached, with `reason_code` + `reason`.
- `validation.json` uniform envelope (hashes of both data files, counts, checks, input hashes).
- `integration_spec.json` adapter signatures, proposed routes, UI placement and caveat sentences.
- `_work/` throw-away probe scripts.

## Blocks and how each one is joined
| block | source | join (nothing is joined by name alone) |
|---|---|---|
| `cjra` | `federal_court_statistics_20260919/cjra_rows.jsonl`, CJRA Tables 7/8/9 as of 2026-03-31 | printed `SURNAME, GIVEN` -> exact normalized surname + given-name initial(s) agree with the FJC export name of an entity (looked up by the entity's native FJC `nid`) AND that entity holds an FJC appointment to the SAME district court AND exactly one entity qualifies AND an FJC appointment of that entity is open on 2026-03-31 |
| `assignments` | SW-BULK release `b2b-cdbb8d040b95c7b65cfc` (built 2026-08-24, 4,159 matters) | release `judges.native_judge_id` == overlay `ids.cl_person_id`, exactly one entity |
| `education`, `positions` | `courtlistener_people_20260918/catalog.sqlite3` (bulk snapshot 2026-06-30) | CourtListener `person_id` == overlay `ids.cl_person_id`, exactly one entity |

## Counts (of 10,669 entities)
- any block 3,983 · `cjra` 1,010 · `assignments` 556 · `education` 2,362 · `positions` 3,701 · all four 319.
- 282 entities gain a CJRA block although they have no CourtListener bridge; 3 judges are printed in two districts
  (joint appointments, e.g. E.D. + W.D. Kentucky): the second court is under `cjra.other_courts`, counts are never summed.
  The adapter's `listing()` cell prints one court-labelled segment per printed court for these three judges, e.g.
  `Missouri Eastern - CJRA 7: 0; CJRA 8: 0; CJRA 9: 0 | Missouri Western - CJRA 7: 2; CJRA 8: 0; CJRA 9: 0` (court label = the
  publisher's printed court name without the "U.S. District Court for" prefix); a judge printed in one court keeps the plain text.
- CJRA rows: 6,329 total = 3,028 attached + 3,301 unresolved (1,241 printed judges). Unresolved printed judges by reason:
  magistrate 780 · no entity with same surname and court 307 (mostly visiting judges, judges newer than the FJC export, CIT
  judges) · not a personal name ("Unassigned", "Bankruptcy Appeal") 94 · given name does not agree 27 · no FJC appointment open on
  the as-of date 15 · bankruptcy 14 · ambiguous 4 (father/son judges of one district, e.g. "MOODY, JAMES M., JR.").
- Given-name agreement of the 1,013 attached court blocks: identical first name 982, identical initial 15, initial only 13,
  one first name a prefix of the other 3 (`match_basis.given_name_agreement`).
- SW-BULK: 867 judge rows; 558 rows carry a CourtListener person id that is bridged to exactly one entity (556 entities with at
  least one matter, 3,090 judge-matter links); 292 rows have a person id with no bridged entity (mostly magistrate judges); 17 rows
  carry no person id (name-only rows, e.g. a second "Brian R. Martinotti" row with 1 matter) and are never attached.

## What the numbers mean
- CJRA counts are **publisher-reported** totals printed per judge for the period ending 2026-03-31 (Table 7 civil cases pending more
  than three years, Table 8 motions pending more than six months, Table 9 bench trials submitted more than six months). MDL member
  cases inflate counts (Judge Martinotti: 1,891 three-year cases). They are never turned into a rate; no denominator exists here.
- `assignments` describes a saved, firm-focused docket dataset, not a judge's docket history. `assigned_count` / `referred_count`
  are distinct matters in that dataset; `by_nature_of_suit` uses the nature-of-suit string exactly as recorded in the release's
  CourtListener bulk-docket source records (coded and uncoded variants of one category stay separate).
- `education` / `positions` are as recorded by CourtListener; `date_*_display` applies CourtListener's own granularity
  (`%Y` -> year only). `position_type` / `how_selected` are CourtListener codes, not expanded here.
- No rates, predictions, rankings or scores are computed (checked: no such key exists in any record).

## Limits / not verified
- CJRA attachments are rule-based and carry `verified_independently: false`. The rule is stricter than required (full first names
  that both exist must be equal or one a prefix of the other); suffixes (Jr./III) are NOT used to break a father/son tie.
- The "open on the as-of date" guard withholds 15 printed judges whose FJC service ended earlier (deceased or resigned judges that
  the publisher still prints); they are listed with their single candidate entity.
- The FJC export was captured 2026-09-14; judges commissioned after the export are unresolved.
- Territorial courts (Guam, Virgin Islands, Northern Marianas) have no Article III appointment in the FJC export: unresolved.
- Not checked against the CJRA PDFs again; row parsing is owned by `federal_court_statistics_20260919`.
- The record-level `temporal` block is null by design: each block carries its own as-of / snapshot / release date.
- No third-party packages. No network. SQLite opened read-only.

## Repair log
- 2026-09-19 (review repair, adapter only): `listing()` built the CJRA cell from the first court block only, so a count printed
  for the second district was hidden (Wimes: W.D. Mo. CJRA 7 = 2 read as 0; Heil: N.D. Okla. 3 / 16 omitted; Boom: W.D. Ky.
  5 / 1 omitted). Fixed in `judge_evidence._cjra_cell`; regression test
  `test_listing_shows_every_printed_court_for_two_district_judges`. `evidence.jsonl` / `unresolved.jsonl` already held both
  blocks and were NOT rebuilt (hashes in `validation.json` unchanged); `detail()` and `evidence_for()` were already correct.
  Tests after the repair: adapter 19, build 7, all passing.

## Rebuild
`python sources/judge_evidence_20260919/build.py` then
`python -m unittest discover -s sources/judge_evidence_20260919 -p test_build.py` and
`python -m unittest discover -s delivery/archive-directory -p test_judge_evidence.py`.
