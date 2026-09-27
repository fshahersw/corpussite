# counsel_directory_20260919

A unified counsel directory — firms, attorneys, and their appearances per docket and per MDL — built from
every local counsel/firm source found under `SW-BULK`, plus the Philadelphia Complex Litigation Center
mass-tort liaison-counsel list. Deterministic, offline, re-runnable (`build.py` produces byte-identical
output on every run against the same inputs).

Run: `C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe build.py`
Tests: `python -m unittest discover -s <this dir> -p test_build.py`

## What this is

Five local sources, each kept labelled by its own identity space and never merged by name:

1. **`SW-BULK/catalog/parties_by_docket/*.json`** (59 dockets, 854 parties, 4,768 attorney entries, every
   one carrying a native CourtListener `attorney_id`) — the primary, richest source. Cross-checked against
   `catalog/attorneys.json` (129 rows), `catalog/firms.json` (52 rows) and `catalog/parties_report.csv`
   (a build-time QA check, see "Open defects" below), and joined to an MDL number through
   `catalog/masters.json` (17 catalog masters) → `sources/mdl_docket_crosswalk_20260919`.
2. **The AWS release `b2b-cdbb8d040b95c7b65cfc`** — consumed via the already-built
   `sources/mdl_counsel_appearances_20260919` layer (`attorneys.jsonl`/`firms.jsonl`/`appearances.jsonl`/
   `parties.jsonl`), which was built directly from that release's `*.jsonl.gz` files with the crosswalk
   linkage and role normalisation already done. This build independently re-measures the raw `*.jsonl.gz`
   row counts by streaming them (`verify_aws_release_counts()`) and cross-checks them against that layer's
   own `validation.json` counts (181 attorneys / 12 firms / 878 appearances / 503 parties — confirmed) rather
   than re-parsing the release a second time, to avoid re-implementing its crosswalk/role-normalisation
   logic. Its own UUID attorney/firm ids are kept in their own `aws:`-prefixed id space, never merged with
   CourtListener ids. `raw_firm_name` from its appearances feeds this build's firm-key grouping.
3. **`sources/mdl_counsel_20260919`** (a CourtListener search-index scrape of 6 MDL master dockets:
   2666, 2738, 2789, 2846, 2873, 3060) — its 57 native-id `attorney_record` rows (`attorneys.jsonl`) become
   ordinary appearance rows (one row per role; 27 of the 57 carry rich per-role dicts with `role_code` and
   `party_types`, unpacked here), and its 2,213 `firms.jsonl` rows contribute firm-name variants at the MDL
   level (no specific docket or attorney for most of them — this source itself does not link them). Its
   4,375 `search_index_name_only` rows (bare name, no id, no firm, no role) are **excluded** — see "Open
   defects".
4. **Leadership evidence, link only** — `sources/mdl_docket_documents_20260919` rows with
   `doc_type == 'leadership_appointment'` (451) and `sources/mdl_docket_activity_20260919` rows with
   `entry_type in {'leadership','steering_committee'}` (568) become `leadership_links` rows: MDL number,
   date, docket number/court, a CourtListener link, and a short verbatim excerpt of the court's own docket
   text. No attorney or firm name is ever parsed out of that free text.
5. **The Philadelphia Mass Tort liaison-counsel PDF**
   (`returnedfiles/www.courts.phila.gov_pdf_cpcivil_Mass-Tort-Docket-and-Liaison-Counsel-List.pdf.json`) — a
   Pennsylvania **state**-court list, not a federal MDL. Parsed by a deterministic line-scanner
   (`parse_phila_liaison`) over the extracted PDF markdown across all 9 programs (Asbestos, Elmiron, Glen
   Mills Schools, Paraquat, Roundup, Vena Cava Filter, Zantac, Hair Relaxer, Johnson & Johnson Talc): only a
   program number/name/code, a role label as printed, a liaison's name and a firm name are ever captured —
   never an address, phone number or e-mail (excluded by construction: the scanner's "looks like a firm
   line" test explicitly rejects address-start, PO-box, city/state/ZIP, phone-only and e-mail-bearing
   lines). 37 liaison mentions across all 9 programs; every one has a firm attached.

MDL numbers are attached **only** through `sources/mdl_docket_crosswalk_20260919` (`crosswalk.jsonl` for the
59 resolved MDL↔master pairs, falling back to `sources/jpml_mdl_20260919/mdls.jsonl` for a title/status when
an MDL number is not one of those 59 — MDL 2666 is the one case that needs this fallback here). Never joined
by name. A docket that does not resolve keeps its raw `mdl_master_docket_id` and a `mdl_resolution_basis`
string explaining why (unresolved-with-reason, or "no master docket linked in source data"); nothing is
dropped or guessed.

## Firm identity (never fuzzy-matched)

`firm_key(raw)`: strip one trailing parenthetical (a city/office/jurisdiction label, e.g. `(Newark)`,
`(US)`, `(Indianapolis)`); casefold; `"&"` → `"and"`; delete periods/apostrophes outright (so `"L.L.P."`
collapses to `llp` instead of fragmenting into single letters); replace any other punctuation with a space;
collapse whitespace; drop a trailing `"et al"` phrase, else one trailing entity suffix
(`llp/llc/pllc/pc/pa/ltd/lpa/apc/plc/chartered`). Two strings that differ by anything else — a misspelling
("COVINGINTON" vs "COVINGTON", "Fulbright & Jaorski" vs "...Jaworski", "BOIS" vs "BOIES"), an added partner
name ("Levin Papantonio" vs "Levin Papantonio Rafferty"), or a truncation ("BARTLIT, BECK ET AL" vs
"Bartlit Beck Herman Palenchar & Scott") — are **never** grouped together and stay as distinct firms; this
is intentional and is unit-tested (`FirmKeyNeverFuzzyMatchTests` in `test_build.py`).

**Measured: 2,170 firms grouped from 2,868 printed variants** across every source (variants and their
source are kept verbatim in `firm_variants`; `firms.display_name` picks the most-common printed variant,
ties broken by shortest-then-alphabetical, purely for a stable display label).

### Not a firm — rejected, with a reason (37 strings)

| Reason | Count | Example (real, from the inputs) |
|---|---|---|
| `email_address` | 18 | `Email: adam.perlman@lw.com` |
| `address` | 11 | `333 Main Street`, `PO Box 3268` |
| `person_name_no_firm_words` | 3 | `PRENTISS W. HALLENBECK, JR` |
| `data_artifact` | 2 | `UNDELIVERABLE EMAIL 4/10/2019`, `ADDRESS EXPIRED / UNKNOWN` |
| `bar_admission_note` | 1 | `COUNSEL NOT ADMITTED TO USDC-NJ BAR` |
| `pro_se` | 1 | `PRO SE` |
| `registered_agent_notation` | 1 | `c/o Lawyers Incorporating` |

The `person_name_no_firm_words` rule is deliberately narrow (a generational suffix — Jr/Sr/II/III/IV — **and**
a middle-initial token **and** no firm-indicating word), because a real 2-3 word firm name with no suffix
(`DLA Piper`, `Jones Day`, `Ice Miller`, `Bartlit Beck`) is otherwise indistinguishable in shape from a
person's name; those are proven to stay as firms in `test_build.py`. `mdl_counsel_20260919`'s `firms.jsonl`
prints its already-cleaned `name_variants` (bar-admission notes already split off by that source's own
`admission_note_rule`), not its raw `source_texts` — using the raw field first over-rejected 630 legitimate
firm variants as bar-admission notes during development; fixed before this was measured.

## Attorneys (never merged by name)

**2,378 attorneys total: 2,197 with a native CourtListener `attorney_id` (`cl:<id>`, from sources 1 and 3 —
the same id space, so the *same* CourtListener id appearing in both sources correctly becomes one attorney
record), 181 with an AWS-release UUID (`aws:<uuid>`, source 2, kept in its own space per BUILD_CONTRACT).**
No attorney in this build lacks a native id while still being published (the one no-native-id source,
`mdl_counsel_20260919`'s 4,375 name-only rows, is excluded — see "Open defects"); the Philadelphia liaison
rows are kept in their own `phila_liaison` table rather than folded into `attorneys`, since a liaison
counsel's name there carries no native id at all.

## Roles and sides

`normalize_role` maps every raw role value through one documented table: the string spellings measured in
sources 1 and 2 (`attorney_to_be_noticed`, `lead_attorney`, `pro_hac_vice`, `terminated` / any
`TERMINATED: ...` text, `inactive`, `unknown`), **and** the full CourtListener Role-choices integer code
table (1–10) measured directly in `mdl_counsel_20260919`'s per-role dicts and cross-checked against that
source's own `coverage.json`: 1 Attorney to be noticed, 2 Lead attorney, 3 Attorney in sealed group, 4 Pro
hac vice, 5 Self-terminated, 6 Terminated, 7 Suspended, 8 Inactive, 9 Disbarred, **10 Unknown**. The raw
value is always kept alongside the normalised label. `side` (`plaintiff`/`defendant`/`mixed`/`other`/
`unspecified`) comes from the specific party's `party_types` in the same source row (source 2 already
supplies `party_side` directly); it is never inferred from the firm or attorney.

## Parties: natural-person litigant names are never published

`is_organization(name)` is a conservative name-only heuristic: an explicit business/entity/government-body
token or phrase (`inc`, `llc`, `corp`, `company`, `county`, `city of`, ...) makes it an organisation, unless
a *strong* wrapper phrase (`estate of`, `personal representative`, `guardian of`, ...) shows a person's name
is what is actually being named — that always wins. A *weak* capacity phrase such as `"individually"` does
**not** override a real organisation token: `"The Chemours Company FC, LLC, individually and as successor in
interest to DuPont Chemical Solutions Enterprise"` and `"Clariant Corporation, individually and as successor
..."` are correctly kept as organisations (both real strings from `parties_by_docket`), while
`"Amber Herrera, individually and as parent and next friend to minor Plaintiff B.H.G."` (no organisation
token at all) is correctly withheld. Critically, **the test runs regardless of side**: `"Richard S. Sackler"`
is a real *named Defendant* in the sample and is still withheld, because he is a natural person, not because
of which side he is on. When unsure: not an organisation — counted, never listed. Docket captions
(`case_name`) get the same treatment (`public_case_name`): an `"In re ..."` caption is always safe; an
`"X v. Y"` caption is published only when the plaintiff side clears `is_organization`
(`"Cannon County, Tennessee v. Purdue Pharma L.P."` is published; `"Allen v. Pfizer Inc."` is suppressed to
protect the plaintiff's surname); anything else is conservatively suppressed.

Measured over the 854 parties in source 1: **4,248 of 8,423 appearances carry an organisation/named-defendant
party name** (`party_name_public` set); the rest carry no name at all, only the `is_organization=false`
flag, i.e. a count.

## Schema (`counsel_directory.sqlite3`)

`firms`, `firm_variants`, `attorneys`, `appearances` (nullable `attorney_id` for the firm-text-only evidence
from source 3; nullable `firm_id` for a rejected/blank firm string), `dockets`, `mdl_links` (one row per MDL
number reached by any appearance/docket/leadership link, with its title/status resolved as above and
denormalised roll-up counts), `leadership_links`, `phila_liaison`, `rejected`, plus a standalone FTS5 table
`search_fts(entity_id, kind, text)` over firm and attorney display names (an external-content FTS5 table was
not usable here since firm/attorney ids are text, not integer rowids).

## Measured counts (this build)

| Metric | Count |
|---|---|
| Firms grouped (from 2,868 printed variants) | **2,170** |
| Firm strings rejected (not a firm) | **37** |
| Attorneys, total | **2,378** |
| — with a native CourtListener id | 2,197 |
| — AWS-release UUID (own id space) | 181 |
| Appearances, total | **8,423** |
| — `sw_bulk_parties_by_docket` | 4,768 |
| — `aws_release_b2b` | 878 |
| — `mdl_counsel_courtlistener_scrape` (native-id, role-linked) | 109 |
| — `mdl_counsel_courtlistener_scrape_firm_text` (firm text only, no attorney link) | 2,668 |
| Dockets, total | **127** (59 parties-by-docket + 62 AWS matters + 6 MDL master dockets) |
| MDLs covered | **21** (20 with a resolved CourtListener master-docket id; MDL 2666 title/status only, via the JPML registry fallback) |
| Leadership-order links | **1,019** (451 `mdl_docket_documents` + 568 `mdl_docket_activity`) |
| Philadelphia programs parsed / liaison rows | **9 / 37** |

Full counts, per-source breakdowns and build-time QA checks are in `validation.json`'s `counts`/`checks`.

## Open defects / things this build found but could not fix

- **`catalog/firms.json` (SW-BULK's own rollup) does not exactly match a direct recount of
  `catalog/parties_by_docket/*.json` for 7 of 20 spot-checked exact firm strings** (e.g. `SEEGER WEISS LLP`:
  13 dockets by direct recount of the 59 files vs. 14 in `catalog/firms.json`; `KIRKLAND & ELLIS LLP`: 3 vs.
  2). An independent line-by-line recount from the raw docket files (outside this build's own code) matches
  this build's numbers exactly in every case checked, so this is a pre-existing inconsistency between two
  SW-BULK catalog artifacts, not a bug here; this build trusts the more granular per-docket files directly.
  Recorded, not silently reconciled, in `validation.json`'s `checks[0]`.
- **`catalog/matters.json` disagrees with `catalog/parties_by_docket` for 1 of the 59 shared dockets**
  (docket `8496426`: `parties_by_docket` says `mdl_master_docket_id: 18753355`, `matters.json` says
  `null`). This build uses `parties_by_docket`'s own field (the task's primary source). Recorded in
  `validation.json`'s `checks[1]`.
- **`mdl_counsel_20260919`'s 4,375 `search_index_name_only` attorney rows are excluded entirely.** They
  carry no native id, no firm and no role — just a bare name scoped to one MDL's CourtListener search
  index — so including them would add volume with no firm-linkage value to a *counsel* directory. Measured
  and reported (`mdl_counsel_old_layer.name_only_excluded` = 4375); not published.
- **The 2,668 "firm text only" appearances from `mdl_counsel_20260919`'s `firms.jsonl`** (mostly for MDL
  2738/2846/2873/3060/2789, which the CourtListener search index reports hundreds of firm names for) have no
  attorney link and `role_normalized: "Not stated"` — this is a genuine, source-documented limitation of
  that scrape ("not a leadership roster, not a complete appearance list"), not a defect introduced here; the
  adapter's qualification text says so, and a consumer can filter on `attorney_id IS NOT NULL` for the
  higher-confidence subset.
- **Philadelphia PDF parsing is best-effort** over a 10-page, multi-format (headings/bold/markdown tables)
  scrape; it is scoped deliberately to rows explicitly labelled "Liaison" or "(Co-)Lead Counsel" (the
  document's own title), so the several named individual defense attorneys in the Zantac section who are
  not labelled as liaison counsel (e.g. "Attorneys for Defendant Pfizer Inc.") are correctly not captured.

## What we looked for under SW-BULK but did not use

- **Other AWS-BATCH1-DOCKETS releases** (`b1-2e03d6c8b8189fab6f09`, `b1-78fe28d84a27a8764b57`,
  `b1-d44f3eb792b161b74dfd`, `b2a-0ef93bbef92cff5b3532`) and **`staging/unpublished-drafts/*`** — excluded
  per the environment rule (only `b2b-cdbb8d040b95c7b65cfc`, never staging/unpublished-drafts or `state/`).
- **`AWS-BATCH1-DOCKETS/config/firms.json`** — a hand-curated seed list of 20 `firm_id`/`canonical_name`/
  `aliases` entries used to build the AWS pipeline itself (e.g. `"Seeger Weiss LLP (Newark)"` as a known
  alias of `"Seeger Weiss"`). Pipeline configuration, not court-sourced data; this build's own `firm_key`
  independently collapses the same aliases anyway.
- **`AWS-BATCH1-DOCKETS/src/batch1_registry/counsel.py` + `tests/test_counsel.py`** — the pipeline source
  code that produced the AWS release's counsel data. Application code, not data.
- **`matter_graph/firms.csv`** and **`catalog/matters.json`'s own `firms[]`/`roles[]` fields** (values like
  `"competitor"`/`"anchor"`, `total_matters` in the thousands) — a broader, search-derived business-tracking
  signal across ~2,100+ dockets (only 59 of which have real per-docket attorney data), explicitly the kind of
  market-share/ranking artifact this build's counting rule is written to avoid ("every count is in the
  saved dockets, never a market-share or ranking claim"). Not ingested.
- Everything else under `SW-BULK` matching `counsel`/`attorney`/`firm`/`liaison`/`lawyer` in its filename
  (outside `.git`, `.worktrees`, `publiclaw_registry_v2`) was one of the files already listed above as an
  input.

## License / status

`license_ref: "sw_bulk_private_firm_work_product"`, `export_allowed: false`. Local personal-testing view
only; derived from a private firm dataset built on CourtListener/RECAP API data; not for redistribution.
Every link out goes to CourtListener; no RECAP bytes are re-hosted. No contact details (no e-mails, phone
numbers or street addresses) are published anywhere in this dataset — enforced both by the firm-rejection
rule above and by a build-time check (`no_contact_details_in_phila_liaison_output`).

## Adapter

`delivery/archive-directory/counsel_directory.py` (fail-closed hash gate on this folder's
`validation.json` + `counsel_directory.sqlite3`): `listing(params)` — `kind` (`firm` default | `attorney` |
`philadelphia_liaison`), `q` (FTS over firm/attorney display names via a standalone FTS5 table, common stop
words ignored), `mdl`, `court`, `role`, `side`, `page`/`limit`. `detail(id)` dispatches on the id's prefix
(`firm:` / `phila:` / anything else → attorney). `for_mdl(mdl_number)` — compact embedding block (≤15 firms,
≤8 leadership orders, qualification ≤400 chars), or `None` when the MDL is not in this directory.
`test_counsel_directory.py` covers the hash gate (missing/failed/tampered), listing/detail/for_mdl shape
against a synthetic fixture, and real-data smoke tests including a check that no natural-person litigant
name (`"Roy Allen"`) is ever findable through the adapter's own search.
