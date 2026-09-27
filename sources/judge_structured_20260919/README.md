# judge_structured_20260919

Evidence-based overlay for the Judges UI, keyed by consolidated judge `entity_id`. Built offline by `build.py`
(no network, every input opened read-only, re-runnable; output is byte-stable except `validated_at`).

## Inputs (hashes in `validation.json` -> `inputs`)
- `sources/judge_entities_20260918/entities.jsonl` (10,669 entities; member key `fjc:nid:<nid>` is the only FJC join)
- `sources/judges/enrichment_20260914/federal_biographies/judges.csv` (FJC Biographical Directory export, 4,074 rows,
  captured 2026-09-14; carries both `nid` and `jid`)
- `sources/courtlistener_people_20260918/catalog.sqlite3` (CourtListener people bulk, snapshot label 2026-06-30)

## Files
- `overlay.jsonl` - 10,669 rows, one per entity: `fjc`, `fjc_status`, `ids`, `courtlistener`, `role_normalized`,
  `completeness`, `facets`, `temporal`. Blocks that have no evidence are `null`.
- `unresolved.jsonl` - 9 native-id bridges withheld for review (surname or birth year disagrees between FJC and CourtListener).
- `validation.json` - uniform envelope; the adapter gates on it. `test_build.py` - rule tests + hash check.

## What the counts mean
- `fjc_entities` 4,074: entities with exactly one FJC nid that resolves to exactly one export row.
- `fjc_status_*`: active 846, senior 618, terminated 224, deceased 2,386. Rule: death year -> deceased; else an appointment with
  no termination -> active (or senior when every open appointment has a senior status date); else terminated (reason, date).
  The label is always `FJC-reported status as of 2026-09-14`; the date is the capture date of the export (the file has no
  internal as-of date). It is never a statement of present service and `verified_independently` is false.
- `fjc_serving` 1,464 and `fjc_serving_district` 1,134 (648 active + 486 senior) reproduce the judge audit exactly.
- `bridged` 3,701 + `bridge_withheld` 9 = 3,710 FJC jids that equal a CourtListener `people.fjc_id`; 364 FJC entities have no
  CourtListener person in this snapshot (mostly 2025-2026 appointees). `fjc_serving_district_bridged` 829.
- `role_*`: district 2,990, circuit 832, state_trial 4,615, state_appellate 93, bankruptcy 73, magistrate 1, other 2,065.
  FJC entities: court type of the open (else latest) appointment. Everyone else: a pattern reading of the saved court name
  (`basis: court_name_pattern`, the matched court string is the evidence). `other` includes 1,490 entities with no court
  string (plus 18 with an unrecognised court name, e.g. tribal or tax courts), 262 FJC entities on the Supreme Court / Court of
  International Trade / historical circuit courts, and 295 entities on a U.S. district court
  with no FJC record, where district vs magistrate title is NOT established (only an explicit profile sentence yields `magistrate`).
- `completeness.score`: fixed weights (sum 100) over eleven evidence flags; `present`/`missing` list them. It measures evidence
  coverage in this corpus, not the judge.

## Limits / not verified
- No name joins anywhere. Non-FJC entities (state judges, magistrate/bankruptcy stubs) therefore have no CourtListener block.
- "Party of appointing president" is the FJC column as published; CourtListener `political_affiliations` are shown as recorded,
  with source code (`a` = appointer, `b` = ballot, `o` = other, empty = not recorded) and dates. Neither is the judge's registration.
- CourtListener code labels (party, source, how_selected) follow the provider's published code lists; unknown codes keep a null label.
- State role patterns handle New York (county Supreme Court = trial) and Pennsylvania (Superior/Commonwealth = appellate); other
  state-specific naming quirks were not reviewed court by court.
- SW-BULK matter assignments, duplicate-stub review and relationship edges are intentionally out of scope for this round.
- FJC education/career text is not duplicated here (already in the entity profile).

## Rebuild / test
```
python sources/judge_structured_20260919/build.py
python -m unittest discover -s sources/judge_structured_20260919 -p "test_build.py"
python -m unittest discover -s delivery/archive-directory -p "test_judge_structured.py"
```
No third-party packages.
