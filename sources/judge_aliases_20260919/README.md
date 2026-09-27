# Judge aliases resolved by native ids (2026-09-19)

Problem: the JPML pending-MDL report (report date 2026-09-01) prints some transferee judges under a name that differs from the
FJC name of the saved profile ("M. Casey Rodgers" vs "Margaret Catharine Rodgers"). The MDL registry's name+court join left
9 printed names unresolved, and searching the Judges page for "Casey Rodgers" found nothing.

Rule: **people are never joined by name.** An alias is written only when

1. a saved CourtListener MCP docket search (pinned by court id + master docket number) returns exactly one docket whose court id and
   normalized docket number equal the JPML master docket, and that docket has a CourtListener `assigned_to_id` (a person id, not the
   free-text `assignedTo` string);
2. that person id equals the `cl_person_id` of exactly one judge entity in `sources/judge_structured_20260919/overlay.jsonl`
   (`bridge_status == linked_native_id`: entity -> FJC nid -> FJC jid == CourtListener `people.fjc_id` -> person id);
3. the surname of the printed name equals the surname of the FJC-published name (corroboration; it can only withhold a link);
4. if the printed name covers several MDLs, all their dockets point to the same person.

If the entity's FJC court differs from the JPML transferee court the alias is kept only with relation
`sitting_by_designation_or_intercircuit_assignment` and a note; the profile's court is never changed. (No such case resolved today.)

## Files

| File | What it is |
| --- | --- |
| `aliases.jsonl` (5 rows) | `{alias, entity_id, display_name, district_code, source, basis, relation, mdls, evidence{cl_person_id, fjc_jid, fjc_nid, entity_fjc_courts, jpml_printed_court, court_agrees, surname_agrees, first_initial_agrees, dockets[...receipt file + SHA-256]}, temporal}` |
| `unresolved.jsonl` (4 rows) | printed names that could not be resolved by native ids, with reason and the docket evidence that was seen |
| `validation.json` | uniform envelope; adapters gate on status/ready and the SHA-256 of both data files |
| `receipts/` | 11 CourtListener MCP connector responses of 2026-09-19 (1 `get_api_usage` pre-check + 10 docket searches), each with tool, arguments, UTC time, SHA-256. **Connector responses, not original HTTP bytes.** |
| `build.py` | offline, re-runnable from the saved receipts (also reads the 18 responses already saved in `sources/jpml_mdl_20260919/connector/`) |
| `save_receipts.py` | one-off that persisted the responses received in the session; never overwrites an existing receipt |
| `patch_mdl_build.py` | record of the one-off change applied to `sources/jpml_mdl_20260919/build.py` (refuses to run twice) |
| `test_build.py` | unit tests of the resolution rules + real-data assertions |

## Result (as of the JPML report dated 2026-09-01; CourtListener responses saved 2026-09-19)

Resolved (5 printed names, 8 MDLs, all at the judge's own FJC court, relation `transferee_judge`):

| Printed name (JPML) | Entity (FJC name) | CL person | FJC jid / nid | MDLs | first initial agrees |
| --- | --- | --- | --- | --- | --- |
| M. Casey Rodgers (N.D. Fla.) | judge-entity-24222174cb13b7162bfcfcbc Margaret Catharine Rodgers | 2755 | 3043 / 1392051 | 2885, 3140 | yes |
| Jane Triche Milazzo (E.D. La.) | judge-entity-1d68b9b45d93fdae1000058b Jane Margaret Triche Milazzo | 2243 | 3392 / 1393796 | 2740, 3023 | yes |
| Beth Phillips (W.D. Mo.) | judge-entity-a4e368d2f942427d5ad6997c Mary Elizabeth Phillips | 2558 | 3414 / 1393906 | 2984 | no ("Beth" is contained in the middle name "Elizabeth"; recorded, surname + court + native id agree) |
| Nicholas G. Garaufis (E.D.N.Y.) | judge-entity-18dbc0ae50756da0da26c216 Nicholas Garaufis | 1145 | 2862 / 1391146 | 3044 | yes |
| Denise L. Cote (S.D.N.Y.) | judge-entity-c8e4cc3c74c0b1a6487279b7 Denise Cote | 733 | 514 / 1379526 | 1358, 3043 | yes |

Unresolved (4), listed not guessed:

* Joshua D. Wolson (D. Del., MDL 2358) and John P. Bailey (D. Md., MDL 2879): CourtListener gives the assigned judge only as a text
  string on the master docket (`assigned_to_id` is null). A string is a name, not a native id -> `cl_docket_has_no_assigned_to_id`.
* Robert J. Shelby (D.N.M., MDL 2695): the CourtListener master docket is assigned to person 430 (James O. Browning), a different
  person than the JPML prints -> `cl_assigned_person_surname_differs_from_printed_name`.
* Raag Singhal (S.D. Fla., MDL 3015): CourtListener person 15357 has no FJC id in the saved people snapshot, so it bridges to no
  entity -> `cl_person_has_no_native_fjc_bridge`. (The Judges search already finds "Anuraag Hari Singhal" for "Raag Singhal" by substring.)

## Consumers

* `delivery/archive-directory/judge_aliases.py`: fail-closed gate; `aliases_for(entity_id)`, `entity_ids_for_query(q)`, `status()`.
  Every row must carry native-id evidence or the whole layer closes.
* `delivery/archive-directory/judges.py`: `listing()` matches a record when its search text matches OR its entity id is in
  `entity_ids_for_query(q)` (other filters stay ANDed; the court facet uses the same clause); `profile()` adds `aliases`. Both are
  wrapped in try/except, so a missing layer changes nothing.
* `sources/jpml_mdl_20260919/build.py`: folds the aliases in as `judge_links` with basis/match_kind `cl_person_native_bridge`.

Rebuild order (fixed point, offline): `judge_aliases build.py` -> `jpml_mdl build.py` -> `judge_aliases build.py`. The alias build
reads only stable MDL fields, so `aliases.jsonl` is byte-identical before and after the registry folds it in.

## Limits / not verified

* Connector quota before the run: 455 of 600 daily requests remaining; 11 requests used (budget 12). Request times are recorded as
  "requested after 2026-09-19T11:41:11Z" (derived from the usage response window), not per-request clock times.
* The `get_api_usage` receipt keeps the quota-relevant part of the response (summary, user limits, membership), not the full text.
* CourtListener assignment reflects CourtListener's docket metadata on the capture date; it was not checked against PACER.
* Aliases exist only for JPML-printed transferee judges that the name+court join could not resolve; this is not a general nickname list.
* No network is used by `build.py`. Nothing here is a prediction or legal advice.
