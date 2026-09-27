# Settlements supplement (built 2026-09-19)

Discovery layer for settlements. **It is not a mass-tort settlement corpus.** Rebuild offline with `build.py`
(no network). Adapter: `delivery/archive-directory/settlements.py` (`listing`, `detail`, `original`).

## Files

| File | Rows | What it is |
|---|---:|---|
| `settlements.jsonl` | 869 | 848 publisher references + 13 saved-official-page references + 8 `Settlement-phrase docket search for MDL <n>` rows (2 of them end in `: no settlement-specific entry found`) |
| `court_documents.jsonl` | 92 | docket-entry references (not documents) from CourtListener searches of 8 verified MDL master dockets; 55 match a settlement-specific phrase, 37 only a non-specific one; see "Court docket evidence" |
| `edges.jsonl` | 6 | `settlement:mdl-docket-<n>` -> `mdl:<n>` (`docket_phrase_search_for`, basis: verified master docket id, evidence carries `settlement_specific_entries` / `entries_total`). **No edge for MDL 2738 and 3060** (0 settlement-specific entries; listed in `validation.json` `counts.edges_withheld_no_settlement_specific_entry`) |
| `receipts_court/` | 8 | one JSON per CourtListener MCP `search` call: tool, arguments, time window, response. Connector responses **transcribed by the agent**, not original HTTP bytes; SHA-256 of each file is in `validation.json` `inputs` and on every row |
| `court_docs.py` / `test_court_docs.py` | - | builds the court layer from the receipts (offline) and its tests |
| `documents.jsonl` | 35 | packet1 captures that passed the quality gate (34 saved HTML pages, 1 downloaded PDF) |
| `rejected_captures.jsonl` | 57 | every other packet1 resource with the reason (46 never fetched, 5 HTTP 403 challenge/denied shells, 3 redirects, 2 empty script shells, 1 redirect shell) |
| `validation.json` | - | uniform envelope; the adapter gates on status, ready and every data-file hash |
| `classifier.py` / `test_classifier.py` | - | document typing rules (link text, file name/URL, first-page text) and mass-tort keyword list |
| `packet1/` | - | collector run of 2026-09-19 (92 resources, 53 fetch receipts, raw bytes, SHA-256); `packet2/` is prepared only and was **not** run |

## Court docket evidence (added 2026-09-19, later run)

Administrator sites answer 403, so court-side evidence was taken from CourtListener instead, without touching those sites.

- **Scope**: 8 of the 18 MDLs that have a verified master docket id in `sources/jpml_mdl_20260919` (court id + docket number
  match): 2666, 2738, 2789, 2846, 2873, 2875, 3004, 3060. MDL 2885 (3M earplugs) has **no** linked master docket there, so it was
  not searched. The other 10 linked MDLs (2570, 2741, 2974, 3029, 3044, 3047, 3081, 3092, 3094, 3140) were **not searched**.
- **Method**: one CourtListener MCP `search` call per MDL (`type=rd`, `docket_id:<id>`, description contains any of: settlement
  agreement, master settlement, preliminary approval, final approval, order approving, claims administrator, qualified settlement
  fund, notice plan, plan of allocation, common benefit; newest first). `get_api_usage` was checked first (413 of 600 daily requests
  left); 8 search requests were used in total, under 12 per minute. No PACER fetch, no RECAP download, no paid call.
- **Completeness**: complete result sets for 2666 (5), 2789 (4), 2846 (7), 3004 (5), 3060 (17). **First result page only** for
  2738 (20 of 47 RECAP documents, attachments included in that one query, = 14 docket entries), 2873 (20 of 153) and 2875 (20 of 33).
- **Rows**: 92 docket entries: docket id, entry number (null for unnumbered clerk entries), `date_filed` (CourtListener
  `entry_date_filed`, labelled as the court filing date), description as recorded, `is_available` (RECAP flag at search time: 37 of
  92 main documents were in the free archive; `validation.json` count `court_docket_entries_main_document_in_recap`), CourtListener URL.
- **Typing**: existing `classifier.py` label rules on the first sentence of the description only, accepted only when the type agrees
  with the filing kind the description leads with (order / motion). Responses, notices, transcripts, minute entries, clerk deadline
  entries, text orders and procedural filings stay `unclassified`: 32 typed, 60 unclassified.
- **Settlement-specific vs non-specific phrases** (repair after independent review, 2026-09-19): the eight phrases settlement
  agreement, master settlement, preliminary approval, final approval, qualified settlement fund, claims administrator, notice plan,
  plan of allocation are `SETTLEMENT_SPECIFIC_TERMS`; "common benefit" and "order approving" are `NON_SPECIFIC_TERMS` because they
  also match entries unrelated to any settlement (common-benefit fee protocols, MDL 2738 "ORDER Approving proposed schedule").
  Every entry carries `settlement_specific`, `matched_settlement_specific_terms`, `matched_non_specific_terms`; every row carries
  `settlement_specific_label` = "entries matching a settlement-specific phrase: N of M": 2666 4 of 5, 2738 **0 of 14**, 2789 3 of 4,
  2846 4 of 7, 2873 20 of 20, 2875 20 of 20, 3004 4 of 5, 3060 **0 of 17**.
- **MDL 2738 and 3060 are not evidence of settlement activity**: every captured hit is "common benefit" (2738: 13, 3060: 17) or
  "order approving" (2738: 1, a schedule). Their rows are titled `... : no settlement-specific entry found`, carry
  `settlement_specific_finding`, show the badge "No settlement-specific entry found (matched only: ...)" and get **no edge**. For
  2738 the statement covers the first result page only (20 of 47 RECAP documents); older matches were not captured.
- A case-management order whose description has no settlement-specific phrase is typed `case_management_order` ("Case-management
  order (no settlement-specific phrase in the description)", 5 entries) instead of the classifier's combined "Case-management or
  settlement-administration order" (3 entries keep it, each with a qualified-settlement-fund phrase).
- **What it is not**: not a settlement record, not an amount, not proof that a settlement exists, even where N > 0: a phrase in a
  docket description is not a reading of the document.
- **Government pages**: no additional unblocked government page was added; `packet2/` holds seeds only (nothing captured locally).
- Descriptions are public court text and can name parties; the adapter strips phone numbers and e-mail addresses from public output.

## What the counts mean

- **848 publisher references**: one third-party consumer aggregator feed (SettleSignal, feed `generated`
  2026-09-13T02:48Z), copied from the normalized catalog
  `Court-Document-Library/07-Settlement-References/catalog/catalog.json`. The `publisher` object is verbatim.
  `publisher_status`, `claim_deadline`, `proof_required`, `verification_status` are the publisher's own assertions
  (`verification.label` = "Publisher self-assertion; not independently verified"). 9 rows carry a bounded manual review.
- **13 saved-official-page references** (`record_layer = captured_official_page`): passed captures whose URL is not the
  official URL of any catalog row (PFAS water, national and tribal opioid sites, Fire Victim Trust, BCBS, real-estate
  commissions, Equifax, VCF, corn seed, FTC refunds index, 3 JPML pages and the JPML 2026-08-03 district report PDF). The
  title is the page's own `<title>`. Site ownership and court-approved status were **not** verified; several of these
  URLs came from model general knowledge in the audit, only the saved bytes are evidence.
- **family**: `ag_government` (publisher type is a government/AG refund, or the official host is `.gov`), `data_breach`
  (publisher type/category, or "data breach" in the title), `mdl_mass_tort` (explicit keyword only, see below),
  `class_consumer` (remaining publisher consumer/class types), `other`. `family_basis` states the rule that fired.
- **mass_tort**: keyword mention in the publisher title or in the saved official page title (MDL / multidistrict,
  products liability, personal injury terms, drug / device / chemical names from `classifier.MASS_TORT_TERMS`).
  15 rows. It is a keyword flag, never a legal characterisation: it includes false positives such as a securities case
  against a pharmaceutical company. "Personal Injury Protection" and "non-toxic" are suppressed; data-breach rows are not flagged.
- **caption / amount**: copied only when printed in the title (`reported_by: "title text"`). 122 rows have a caption,
  194 a dollar figure. Title figures are not classified as fund, per-person amount or fee and must never be summed.
  For unparenthesised captions the defendant side can include descriptive words (flagged per row).
- **states**: the publisher's `applicable_states` (76 rows). An empty list is "none listed", not a class definition.
- **deadline_state** vs 2026-09-19: `passed` 478, `within_30_days` 76 (deadline 2026-09-19..2026-10-19), `future` 60,
  `unknown` 247 (234 publisher rows without a date + 13 page rows). Pure date arithmetic on the publisher date; it says
  nothing about eligibility, time zones or cut-off method. The publisher status is kept unchanged beside it, so a row can
  read "Open for claims" with a passed deadline.
- **documents**: 35 files, 8,069,891 bytes. Types: settlement website home page 22, press release 1, unclassified 12
  (government refund/index pages and the JPML report: no rule matched, nothing guessed). A saved HTML page is never typed
  as an agreement, order or judgment. 22 of the 848 publisher rows have a saved official page, linked by exact equality of
  the captured URL and `publisher.official_settlement_url`.

## Dates kept separate

`temporal.source_as_of` = feed generation time (publisher rows); `temporal.captured_at` = collector receipt `fetched_at`
(documents and page rows); `publisher_last_verified` = the publisher's workflow check date; `claim_deadline` = publisher-reported.
Publication and effective dates are unknown and stay null.

## Limits and what was not done

- No settlement agreement, notice, order or claim form was captured: packet1 fetched exact home/refund pages only, no link
  following. 32 NAAG settlement PDFs and the NAAG/CFPB/SEC/HRSA pages were not obtained (naag.org, consumerfinance.gov,
  sec.gov and hrsa.gov answered 403 or rate limits; the rest stayed pending when the run stopped). Not rerouted.
- packet2 was not run; the optional Firecrawl capture was skipped (the only private wrapper found,
  `scripts/judge_firecrawl_private.py`, is scoped to the judge project and its authorised key).
- The 4 PDFs saved earlier under `Court-Document-Library/07-Settlement-References/documents/` are not re-published here
  (they are already served by the local-library collection).
- No court, case number, MDL number, judge or defendant field exists on publisher/page rows except in the 9 reviewed rows; no
  edges are emitted for them (the only edges are the 6 court-docket rows with at least one settlement-specific entry).
- **Licence conflict (unresolved)**: the feed metadata says free use with attribution; the publisher's general terms
  (June 3, 2026) restrict commercial use and bulk republication. Treat as internal research until a person decides.
- Saved HTML is third-party markup: serve it with `Content-Security-Policy: sandbox` or as a download.

## Tests

`python -m unittest discover -s sources/settlements_20260919 -p "test_*.py"` (33: classifier 16, build 7, court docs 10) and
`python -m unittest discover -s delivery/archive-directory -p "test_settlements.py"` (13). Regression tests for the repair:
`test_mdls_without_settlement_specific_entry_are_not_published_as_settlement_activity` (data) and
`test_mdls_without_settlement_specific_entry_are_not_shown_as_settlement_activity` (adapter). The running server keeps the old
adapter module and old row titles until the integrator restarts it (not done here).
