# JPML MDL registry (jpml_mdl_20260919)

Pending multidistrict litigations as listed by the United States Judicial Panel on Multidistrict Litigation (JPML) in its
CM/ECF statistics reports dated **September 1, 2026**, with the January-September 2026 terminations, two quarterly
by-circuit snapshots (March 31 and June 30, 2026), a conservative transferee-judge join to the Judges UI, a curated
district-code crosswalk to CourtListener court ids, and a small CourtListener docket cross-check. Everything is offline and
re-runnable: `build.py` reads only the saved originals in `raw/`, the pypdf reading copies in `text/`, read-only local
databases and the saved connector responses in `connector/`.

Gate: `validation.json` (uniform envelope). Adapter: `delivery/archive-directory/mdl_registry.py` (+ `test_mdl_registry.py`).
Integration proposal: `integration_spec.json`.

## Files

| File | What it is |
|---|---|
| `seeds.jsonl`, `fetch.py`, `receipts.jsonl`, `gaps.json`, `raw/` | Frozen 16-URL packet (jpml.uscourts.gov only, Crawl-delay 10 s honored, no retries), one receipt per URL (all HTTP 200, 0 gaps), original bytes + SHA-256. Never modified by the build. |
| `extract_text.py`, `text/*.txt`, `text/manifest.json` | pypdf page text of the 11 PDFs (reading copies, form feed between pages). `manifest.json` hashes are of the LF form; the on-disk files are CRLF (Windows `write_text`); the build accepts either and records the on-disk hash. |
| `build.py` | Parses the reports, reconciles them, writes every data file below and `validation.json`. Exit code 1 if a blocking check fails. |
| `mdls.jsonl` (176 rows) | One row per MDL: 166 pending (as of 2026-09-01) + 10 terminated between 2026-01-01 and 2026-09-01. |
| `documents.jsonl` (16 rows) | Registry of the saved originals (11 PDFs, 4 landing pages, robots.txt): document_id, sha256, bytes, title, report date + basis, seed URL, receipt capture time. The adapter serves them at `/mdl-files/<document_id>` after re-hashing. |
| `court_map.json` (94 codes) | JPML CM/ECF district code -> CourtListener court id + FJC court string + circuit. Curated crosswalk, verified against read-only tables (see below). |
| `edges.jsonl` | Relationships with basis + evidence (grammar below). |
| `unresolved.jsonl` (4 rows; 9 before the native-id alias pass) | Every printed transferee-judge name that did not join, with the reason and candidate evidence. |
| `qa.json` | Every reconciliation check with its detail; `mismatches` lists what did not reconcile (nothing is hidden). |
| `connector/*.json` (20 files) | CourtListener MCP responses saved verbatim with tool name, arguments, time window; labelled "connector response, not original HTTP bytes". Two are exploratory (malformed `fields`) and are skipped by the build. |
| `test_build.py` | Parser tests on the tricky strings + assertions on the published outputs. |
| `write_spec.py`, `integration_spec.json` | Writes the integration proposal with real adapter responses. |
| `verify_receipts.py`, `peek_*.py` | Scratch helpers from the fetch/scoping phase (read-only). |

## What the counts mean

* **actions_pending / total_actions** are the JPML's own CM/ECF figures ("Actions Now Pending" and "Total Actions
  (Historical)") for each docket, exactly as printed in the *Distribution of Pending MDL Dockets by District* report dated
  9/1/2026. Every count carries `counts_label` = "as listed in the JPML report dated 2026-09-01". They are publisher-reported,
  change daily, and are not independently verified docket counts.
* **166 pending MDLs, 206,182 actions pending, 716,121 total actions, 50 transferee districts, 142 transferee judges** -
  all reconcile exactly with the printed report totals (see `qa.json`).
* **litigation_type** is the JPML docket-type category from the *Docket Type Summary* (Products Liability 65, Antitrust 38,
  Data Breach and Consumer Privacy 22, Miscellaneous 15, Intellectual Property 8, Sales Practices 8, Securities 4, Common
  Disaster 3, Air Disaster 2, Employment Practices 1). Not a legal classification.
* **snapshots** hold the same two figures from the quarterly by-circuit reports dated 2026-03-31 (158 MDLs) and 2026-06-30
  (162 MDLs), each labelled with its own report date.
* **status = terminated** rows come from the *Recently Terminated MDLs* listing (closed between 1/1/2026 and 9/1/2026);
  they have no pending counts, only master docket, dates and the judge as printed there.
* Temporal block per row: `captured_at` (fetch receipt), `source_as_of` (printed "Report Date"), `published_at` null (the
  JPML posts these files without a publication date; HTTP Last-Modified is kept on the document, never used as a legal
  date), `effective_from` = DateTransferred (Panel transfer order date), `effective_to` = DATE CLOSED for terminated MDLs.

## How the reports were parsed and reconciled

pypdf plain text of the two *Docket Summary Listing* reports (by MDL number, by type) and the terminated listing is
line-based; PDF line wraps glue words together ("LitigationCote, Denise L.", "2:23-md-308005/09/2023"). The *Distribution*
reports (by district, by actions pending, by circuit) come out as one stream; wide numbers are glued ("69,25071,935").
The build never guesses a glued number: a glued token is split only when the thousands-separator grammar allows exactly one
split (a token without separators is left null and flagged). Captions are anchored on the by-MDL-number caption so that a
caption ending in digits ("September 11, 2001 371 381") cannot lose its tail. Judge names are anchored on the
"First M. Last" form printed in the by-district report to split "Last, First" from the caption.

Checks in `qa.json` (all passed unless noted): parsed rows vs printed row count for all 7 reports; identical MDL sets across
the four September reports; sum of pending/total vs "Report Totals"; the printed docket-count-range tables (4 pending and 4
total buckets, counts and sums); districts (50) and judges (142, by distinct printed name); per-MDL agreement of district,
counts, caption (whitespace-insensitive), judge name and master docket/dates across reports; docket-type section counts;
per-circuit summaries in both quarterly reports (their totals print glued without separators and are accepted only when the
string equals the concatenation of the parsed sums); June-vs-September inventory.

Listed, not hidden: (1) MDL 3074 is absent from the June 30 by-circuit report (filter "Limited to Active Litigations") but
present in March and September - probably inactive in June; left as a warning. (2) Joshua D. Wolson is printed under DE
(sitting by designation, MDL 2358) and PAE; the JPML counts him once.

## Court crosswalk (`court_map.json`)

The JPML prints CM/ECF district codes (ALN, NYS, ...). `CURATED_COURTS` in `build.py` maps every code to a CourtListener
court id and the FJC court string; the build keeps an entry only when the CourtListener row exists in
`sources/courtlistener_people_20260918/catalog.sqlite3` and the FJC string exists among the Judges-UI court strings
(`profiles.sqlite3`, read-only). Unverified entries stay null. All 51 codes seen in the reports are mapped. Circuits come
from the district grouping under circuit headings in the by-circuit reports (basis recorded per entry).

## Judge join (conservative)

Key = (transferee judge name as printed, district code). Target court = the FJC court string of that district. Candidates =
FJC judges (`delivery/judge_enrichment_20260914/federal_biographies/facts.jsonl`, main federal judicial service
appointments) at that exact court whose name matches after normalization (case, punctuation and accents folded, generational
suffix must agree when both sides print one). `match_kind` records the normalization that was needed:

| match_kind | rule | keys |
|---|---|---:|
| exact | identical tokens | 62 |
| middle_initial | first + last identical; middle tokens identical or one side is the other's initial | 68 |
| middle_omitted | JPML prints no middle name, FJC does | 5 |
| first_initial | JPML prints a first initial ("K. Michael Moore" / "Kevin Michael Moore"); middle + last identical | 5 |

A key resolves only when exactly one FJC judge at that court matches and that judge's FJC nid maps (via
`sources/judge_entities_20260918/entities.jsonl` members) to exactly one Judges-UI entity in `profiles.sqlite3`.
Result: 149 keys, 140 resolved, 9 unresolved; 164 of 176 MDL rows carry a `judge_links` entry (12 do not). Unresolved
reasons: 6 `no_fjc_name_match_at_court` (nicknames or FJC-side middle names: M. Casey Rodgers, Raag Singhal,
Jane Triche Milazzo, Beth Phillips, Nicholas G. Garaufis, Denise L. Cote), 3 `name_matches_fjc_judge_at_other_court_only`
(judges sitting by designation: Wolson/DE, Bailey/MD, Shelby/NM). The FJC same-last-name candidates are listed in
`unresolved.jsonl` for manual review; the build never links them by name.

### Native-id alias pass (added 2026-09-19, `sources/judge_aliases_20260919`)

After the name+court join, still-unresolved printed names are looked up in the hash-gated alias layer
`sources/judge_aliases_20260919/aliases.jsonl`. A link is made only when the CourtListener person assigned to that MDL's
master docket (saved connector response) is the `cl_person_id` the judge entity is bridged to by FJC ids (FJC jid ==
CourtListener `people.fjc_id`), the surname agrees, and every MDL of that printed name has its own docket evidence. Such
links carry `basis` and `match_kind` `cl_person_native_bridge`, the CourtListener person id, FJC jid/nid, the entity's FJC
courts, `court_agrees`, `surname_agrees`, `first_initial_agrees`, the docket id and the receipt file + SHA-256, and a
`relation` (`transferee_judge`, or `sitting_by_designation_or_intercircuit_assignment` when the entity's FJC court differs
from the transferee court; the profile's court is never changed). If the alias layer is missing or fails its gate the build
simply keeps those names unresolved.

Result now: 149 keys, 145 resolved (140 by name+court, 5 by native-id bridge: M. Casey Rodgers, Jane Triche Milazzo,
Beth Phillips, Nicholas G. Garaufis, Denise L. Cote), 4 unresolved; 172 of 176 MDL rows carry a `judge_links` entry (8 of
them by native bridge: 1358, 2740, 2885, 2984, 3023, 3043, 3044, 3140). Still unresolved: Joshua D. Wolson (2358) and
John P. Bailey (2879) - CourtListener records the assigned judge only as text, no person id; Robert J. Shelby (2695) - the
CourtListener master docket is assigned to a different person (James O. Browning); Raag Singhal (3015) - CourtListener
person 15357 has no FJC id in the saved people snapshot, so no bridge. For rows linked by the native bridge the connector
agreement value is `judge_link_is_the_native_bridge_itself` (no independent comparison is claimed).

## CourtListener cross-check (connector)

`get_api_usage` showed 521/600 daily requests remaining, so 20 docket searches were made (type=d, q="MDL <n>", court filter,
docket_number filter, fields docket_id/court_id/assigned_to_id/referred_to_id/dateFiled/dateTerminated/docketNumber/
caseName/assignedTo). Two exploratory calls for MDL 2738 passed `fields` as a JSON list and came back without field values;
they are saved and flagged `exploratory`. The remaining 18 cover the 18 largest Products Liability MDLs by actions pending
(2738 ... 2875); 2921 and 2924 (positions 19-20) were not queried. Each response is saved verbatim under `connector/` with
tool name, arguments, request window and file/response SHA-256 (registry in `qa.json` -> `connector.registry`).

All 18 returned exactly one docket whose court id and normalized docket number equal the JPML master docket. Agreement of
the CL `assigned_to_id` with the name join (CL person -> `people.fjc_id` -> FJC jid -> nid): 13 agree, 0 disagree,
2 not comparable because the name join is unresolved (3044 Garaufis, 3140 Rodgers - CL names the same judges), 2 CL persons
have no FJC id in the local CourtListener people table (3060, 3094), 1 docket has no `assigned_to_id` (3092).

## Edges (`edges.jsonl`)

`{"from":{"type","id"},"to":{"type","id"},"relation","basis","evidence"}` with ids `mdl:<number>`, `cl_court:<id>`,
`judge_entity:<entity_id>`, `cl_person:<int>`, `cl_docket:<int>`, `local_collection:<slug>`.

* mdl -> cl_court, relation `transferee_district`, basis `jpml_district_code_crosswalk` (176)
* mdl -> judge_entity, relation `transferee_judge`, basis `jpml_name_court` (164)
* mdl -> judge_entity, relation `transferee_judge` (or `sitting_by_designation_or_intercircuit_assignment`), basis `cl_person_native_bridge` (8)
* mdl -> cl_docket, relation `master_docket`, basis `cl_docket_search` (18)
* mdl -> cl_person, relation `assigned_judge`, basis `cl_assigned_to_id` (17)
* mdl -> local_collection, relation `has_local_documents`, basis `mdl_number_only` (1: MDL 3080 ->
  `sources/local_library_presentation_20260918/mdl-3080.jsonl`, 24 records, linked by MDL number only)

## Limits

* Publisher-reported statistics only; no docket was counted independently. Nothing here is a prediction or legal advice.
* The JPML reports carry no publication date; only the printed report date is used as `source_as_of`.
* Judge links describe the transferee judge as printed on the report date; they say nothing about current assignment.
* `cl_links` exist only for the 18 checked MDLs; absence means "not checked".
* Panel roster, CY-2025 statistics, FY-2025 terminated-litigations and FY-2025 statistical-analysis PDFs are registered as
  documents (servable originals) but not parsed into rows.
* No packages were installed; pypdf (already present) was used only by `extract_text.py` in the fetch phase.

## Re-run

```
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' sources/jpml_mdl_20260919/build.py
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' -m unittest discover -s sources/jpml_mdl_20260919 -p test_build.py
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' -m unittest discover -s delivery/archive-directory -p test_mdl_registry.py
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' sources/jpml_mdl_20260919/write_spec.py
```

License: U.S. Government work (JPML CM/ECF statistics reports and jpml.uscourts.gov pages); public domain in the United States.
