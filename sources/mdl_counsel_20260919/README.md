# MDL counsel layer (firms / attorneys / parties) - 2026-09-19 (round 3 update)

Firm / attorney / party layer for the six largest products-liability MDLs that already carry a verified CourtListener
master-docket id in `sources/jpml_mdl_20260919` (no docket searches were made to find dockets):
MDL 2738 (J&J Talc), 2846 (Davol/Bard hernia mesh), 2873 (AFFF), 3060 (Hair Relaxer), 2789 (PPI II), 2666 (Bair Hugger).

**Status: passed, PARTIAL COVERAGE.** Everything here is a *connector response* from the user's CourtListener MCP
connector, captured 2026-09-19 UTC. It is not original court bytes and not original HTTP bytes.

## Round 3 (counsel paging) - what was asked, what was possible
- Asked: page `call_endpoint attorneys` filtered by master docket (100 per page, up to 10 pages) for MDLs 2738, 2846,
  2873, 3060, 2789 with correctly typed JSON arguments.
- **Still impossible from this harness.** The connector's tools are again exposed to the agent with an empty parameter
  schema (`{"type": "object"}`), so the harness serialises every argument as a string. `call_endpoint` validates
  `num_results` as integer and `query` as object and rejected the call again (receipt 058, same error as 006/007; no quota
  cost). Probes with deliberately invalid arguments (receipts 059-063, no quota cost) showed the only accepted properties:
  `call_endpoint` = `endpoint_id`, `num_results`, `query`; `get_more_results` = `query_id`; `get_endpoint_item` =
  `endpoint_id`, `item_id`, `fields`. The connector exposes no MCP resources. There is no string-only way to filter
  attorneys by docket. Fix outside this folder: the connector must publish typed tool schemas to the agent harness (or the
  paging must be run from a client that sends typed JSON).
- Done instead, inside the same budget rules: a **by-id packet of 30 requests** (`seeds.jsonl`, frozen by `select_ids.py`
  before any request): the 6 lowest CourtListener attorney ids of each of the five master dockets' search-index
  `attorney_id` sets (lowest id = attorney records CourtListener created earliest for that docket). Measured first: all
  4,406 candidate ids are listed for exactly one master docket, so no id could serve two MDLs.
  `get_endpoint_item attorneys <id>` with `fields=id,name,contact_raw,date_modified`. `parties_represented` was NOT
  requested: a by-id request cannot restrict it to one docket and for mass-tort counsel it can be very large.
  **So these 30 records have source-native id, name, firm line and city/state, but NO roles and NO parties.**
  This is not a leadership roster and not a random sample.
- Quota: 443 of 600 remaining before (receipt 056), 413 after (receipt 094): exactly 30 counted requests, sent in groups of
  at most 5 with offline work between groups (the closing usage reading showed 5 of 20 used in the last minute); no 429,
  no retries. Receipt 057 is the `attorneys` endpoint schema (typed filter definitions, for the next attempt).

## Files
| File | Rows | Meaning |
|---|---|---|
| `attorneys.jsonl` | 4,432 | 57 `attorney_record` rows = 27 `record_scope: full_record_with_roles` (MDL 2666) + 30 `id_name_firm_line_only` (six per other MDL; `roles: []`, `roles_note`, `mdl_link_basis`) ; 4,375 `search_index_name_only` rows (name + MDL only; `cl_attorney_id` null) |
| `firms.jsonl` | 2,213 (2,441 before the repair below) | one row per **normalised firm text after the note rule**; per-MDL `attorney_count` = attorney records whose parsed firm line has exactly that key; `in_search_index` = name listed in the search-index firm set of that master docket; 54 firm rows have linked attorney records; `source_texts` = the text(s) as recorded, `admission_note_source_texts` = how many of them carried a note (609 rows have at least one) |
| `parties.jsonl` | 295 | 13 `party_record` rows (MDL 2666) + 282 name-only rows (MDL 2873); unchanged in round 3 |
| `coverage.json` | 1 | per-MDL "N retrieved of M reported" labels, rules, role-label map |
| `edges.jsonl` | 114 | `attorney:cl_attorney:<id>` -> `mdl:<n>` (57; basis says whether the link is the attorney record or the search-index id set) and -> `firm:<slug>` (57) |
| `unresolved.jsonl` | 53 | search-index firm texts that were not published: 12 that were only a bar-admission note, 41 address lines / address fragments. **Reason, MDL and receipt file only - never the text** |
| `seeds.jsonl` | 30 | frozen by-id packet (endpoint, id, fields, MDL, basis) |
| `receipts/` | 94 + manifest | verbatim connector responses with tool, arguments, UTC call/receive time, SHA-256. 001-055 = round 1 (untouched); 056-094 = round 3, appended by `harvest_receipts_r3.py` (never overwrites; idempotent by harness tool-use id) |

Per-MDL coverage (M = attorney ids reported by the CourtListener search index for the master docket on 2026-09-19):

| MDL | attorney records retrieved of M | of which with roles | name-only rows | party rows published |
|---|---|---|---|---|
| 2666 | 27 of 27 | 27 | 0 | 13 records |
| 2738 | 6 of 2,150 | 0 | 2,143 | withheld (49,273 party ids) |
| 2846 | 6 of 717 | 0 | 711 | withheld (21,647) |
| 2873 | 6 of 364 | 0 | 358 | 282 name-only |
| 3060 | 6 of 551 | 0 | 545 | withheld (14,528) |
| 2789 | 6 of 624 | 0 | 618 | withheld (9,664) |

Firm rows per MDL after the repair (a firm text listed for two MDLs counts in both): 2666: 58 · 2738: 849 (951 before) ·
2789: 249 (285 before) · 2846: 655 · 2873: 319 · 3060: 426. Of 2,719 firm texts in the six search-index responses, 1,965 are
published as recorded, 701 after removing a note, 12 not published (only a note), 41 not published (address line or
fragment). The same numbers are in `validation.json` (`counts.search_index_firm_text_filter`, `counts.firm_rows_per_mdl`)
and per MDL in `coverage.json` (`search_index_firm_text_filter`, `firm_coverage_label`).

## Repair 2026-09-19 (independent review, two major defects - both confirmed and fixed in `build.py`)
1. **Litigant address lines were published as "firms".** CourtListener records self-represented litigants as their own
   counsel, so the search-index firm field also holds their address line (an inmate number and prison, "c/o" a private
   house, a correctional institution) and office-address fragments. The old filter only caught "number + street word".
   New rule `FIRM_TEXT_FILTER_RULE` (function `firm_text_problem`): a firm text is not published when it contains `#`, a
   custody word, a care-of marker, a suite/floor/room token, a later comma segment that starts with a street number or a
   spelled-out number or names an office building, or an e-mail / phone / street-address pattern. 41 texts are left out.
2. **A bar-admission note about an individual attorney was glued to 600+ firm texts** ("COUNSEL NOT ADMITTED TO USDC-NJ
   BAR, <firm>", 20+ spellings, also "Not a member of NJ bar", trailing position, and an "MDL <number>," prefix). New
   rule `ADMISSION_NOTE_RULE` (function `strip_admission_note`): split on commas, drop every note segment, re-join the rest
   unchanged. The firm key is computed after that, so "COUNSEL NOT ADMITTED ..., Motley Rice LLC" and "MOTLEY RICE LLC" are
   one row. Texts with nothing left (only the note, or note and firm in one comma segment) are not published (12).
   The same two rules are applied to the firm line parsed from attorney contact blocks (no row changed there).
Both rules are pattern rules on text, not a judgement about who is a law firm: what remains can still include a solo
practitioner's name, a government office, a text such as "NJ New Jersey", or a firm text that starts with a lower-case
"the" (28 rows) because the source title-cased the words after the removed note. Regression tests: `test_build.py`
(`test_admission_note_segments_are_stripped`, `test_firm_text_problem_rejects_litigant_address_lines`,
`test_no_firm_name_is_an_admission_note_or_address_fragment`, `test_note_prefixed_spellings_fold_into_the_plain_firm_row`,
`test_unresolved_rows_carry_reasons_not_text`) and two sweeps over every served firm row in the adapter test.
No network or connector request was made for the repair; `peek_firms.py` is the read-only diagnostic used to size the rules.

## Rules
- **Firm line:** first non-empty line of the attorney contact block after skipping admission-note lines such as
  "COUNSEL NOT ADMITTED TO ... BAR"; null if that line looks like an address, PO box, city/state/ZIP, phone, fax or
  e-mail line. City/state = first line shaped `City, ST 12345`. Nothing else from the contact block is kept.
- **Search-index firm text:** note rule, then firm-text filter (see Repair above); the text as recorded stays in
  `source_texts`, rejected texts stay in `receipts/` only.
- **Firm key:** NFKC, whitespace collapsed, trimmed, case-folded text after the note rule. No punctuation stripping, **no fuzzy merging**:
  "BEASLEY ALLEN LAW FIRM", "BEASLEY ALLEN CROW METHVIN PORTIS & MILES PC" and "Beasley, Allen, Crow, Methvin, Portis &
  Miles, P.C." are three rows; "Johnson Becker" and "Johnson Becker PLLP" are two.
- **Roles:** the integer code is what the source recorded (MDL 2666 only). Labels come from CourtListener's open-source
  Role choices because the connector returned no labels. By-id records carry `roles: []` plus `roles_note`.
- **MDL link of a by-id record:** its id is listed in the search-index `attorney_id` set of that master docket
  (`mdl_link_basis` names the receipt). No other inference.
- **People are never merged by name.** One row per CourtListener attorney id per MDL. Name-only rows have no id.
  Same-docket de-duplication only: a name-only row is not emitted when an attorney record retrieved for the *same* master
  docket has exactly the same name text (case-folded); 31 name strings were suppressed this way (one name had two casings).
- **Privacy:** e-mail addresses, phone/fax numbers and street addresses exist only in `receipts/` (never served; the
  adapter does not read that folder). The build fails if an e-mail or phone pattern appears in any data file, if any
  firm name / key / variant / attorney firm line matches the firm-text filter or carries a note, or if an unresolved row
  has a key other than reason, mdl_number, receipt_file, cl_attorney_id, docket_id. Names of attorneys as listed on the
  master docket (which include self-represented litigants, as the source records them) are published as names only.
- **Party names withheld:** unchanged (`PARTY_NAME_PUBLISH_LIMIT = 1000` in `build.py`); four master dockets list
  9,664-49,273 parties, overwhelmingly individual plaintiffs; only counts are published.

## Limits / not verified
- Five MDLs remain ~99% name-only. 30 of 4,406 attorney ids were retrieved; roles, sides (plaintiff/defence) and
  leadership designations of those 30 are NOT recorded.
- Master (lead) dockets only, as held by CourtListener/RECAP; counsel appearing only in member cases are absent.
- Whether the search-index sets are complete for very large dockets was not verified. `get_counts` was not called.
- `date_modified` is CourtListener's record timestamp, not a court date.
- The live server was not restarted; it serves this layer only after the integrator's next restart/reload.
- Next step when typed connector arguments exist: `call_endpoint attorneys` with
  `{"docket": <id>, "filter_nested_results": true, "order_by": "id", "fields": ["id","name","contact_raw","parties_represented","date_modified"]}`,
  `num_results` 100, then `get_more_results` by `query_id` (about 35 requests for 10/8/4/6/7 pages); `build.py` already
  accepts attorney records with `parties_represented` from any receipt, so the rebuild needs only a small receipt-reader
  for list responses.

## Re-run
`python select_ids.py` (offline, rewrites `seeds.jsonl`), `python harvest_receipts_r3.py <agent-transcript.jsonl>`
(one-time, additive), then `python build.py` (offline) and
`python -m unittest discover -s <this folder> -p test_build.py` (9 tests).
Adapter: `delivery/archive-directory/mdl_counsel.py` (`listing(params)`, `detail(id)`; 12 tests in `test_mdl_counsel.py`).
`python write_spec.py` refreshes `integration_spec.json` from the adapter. No third-party packages.
