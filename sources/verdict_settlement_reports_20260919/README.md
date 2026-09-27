# Verdict & settlement reports (verdict_settlement_reports_20260919)

Built 2026-09-19 &middot; 3,312 rows from one local input file &middot; SQLite + FTS5, no network.
Adapter: `delivery/archive-directory/verdict_reports.py` (`listing`, `detail`, `for_mdl`, `summary`; no
`original` -- no document bytes are stored here).

## What this is, and what it is not

Every row is a **verdict or settlement lead self-reported by a law firm** to a commercial
verdict-ranking site (topverdict.com) in order to be listed on one of its published "top N" pages, taken
from `C:/Users/firas/Downloads/returnedfiles/settlementsverdicts/verdict_lead_index.json` (3,312
deduplicated results from 289 published lists, snapshot built by the publisher 2026-08-22; the
publisher's own provenance warning, carried into `validation.json.qualification`, states amounts are
self-reported, list composition is shaped by "the interest we receive from you in the form of orders",
and accuracy/completeness are explicitly disclaimed).

**This is not a court-records source.** It is not verified against any docket. It is not a representative
sample -- each list is a top-N by amount, so the distribution is truncated from below (see the input
report's own median-of-the-top-tail warning). Amounts are shown exactly as printed; they are never
summed, ranked or averaged, here or in any consumer of this adapter.

## License

The publisher's terms prohibit reuse beyond personal research (list composition/ranking asserted as the
publisher's IP; no republication or redistribution). `license_ref:
"publisher_terms_prohibit_reuse_local_research_only"`, `export_allowed: false`. This layer is local
personal research only. Every `detail()` and `listing()` response links out to the published list URL;
no page content is re-hosted, only the structured fields already present in the local snapshot.

## Files

| File | Rows | What it is |
|---|---:|---|
| `build.py` | - | Deterministic, offline, re-runnable. Single input: `verdict_lead_index.json`; also reads (read-only) `sources/jpml_mdl_20260919/mdls.jsonl` for MDL caption matching. |
| `verdict_settlement_reports.sqlite3` | 3,312 | One `reports` row per input record, an FTS5 `reports_fts` index (title, practice area, firms, attorneys, jurisdiction, county, mass-tort tags), and a `facet_counts` table used to build filter option counts. |
| `validation.json` | - | Uniform envelope; the adapter gates on `status`, `ready` and the database file's SHA-256. |
| `test_build.py` | 44 tests | Written first (TDD): amount parsing/banding, caption redaction (the bulk of the suite -- see below), MDL linking, id generation, and a real-data smoke test. |

## Measured counts (this build)

- **3,312 total** &middot; 1,755 verdict / 1,557 settlement.
- **Amount**: every row's `amount_raw` string was an unambiguous `$#,###.##` figure, so `amount_numeric`
  is populated on all 3,312 (this is a property of this particular input, not assumed -- a row whose
  printed amount is a range, "Confidential" or otherwise ambiguous keeps `amount_numeric: null` and is
  banded `not_stated`; see `test_build.ParseAmountTests`). Bands: under $1M 1,151 &middot; $1M-$10M 1,504
  &middot; $10M-$100M 539 &middot; over $100M 118 &middot; not stated 0.
- **Mass tort tagged: 670** (keyword tag already assigned by the source pipeline: Medical malpractice 398,
  Childhood sexual abuse 124, Toxic exposure 80, Rideshare assault 75, Nursing home 66, Pharmaceutical 45,
  Data breach/privacy 21, Asbestos/mesothelioma 18, Talc 9, Wildfire/utility 4, Roundup/glyphosate 2,
  Medical device 1, Social media/platform 1). This is the publisher's own tag, carried through verbatim;
  it is a keyword flag, not a legal characterisation.
- **Captions redacted: 3,162 of 3,312 (95.5%)**; **150 kept verbatim** (organisation-vs-organisation
  suits, and 6 institutional "In re ..." captions -- see "Caption redaction" below for the full
  breakdown and why the redaction rate is this high).
- **MDL-linked: 0.** Both link paths were checked against all 3,312 rows (see "MDL linking").
- 10 distinct jurisdictions (california 1,310, texas 602, united-states 520, new-york 421, florida 124,
  new-jersey 106, illinois 74, georgia 62, washington 49, colorado 44), 73 distinct practice areas
  (`primary_type`), years 2024 (2,137) and 2025 (1,175 -- the publisher's 2025 lists were partial as of
  the snapshot date), 953 rows flagged by the publisher as appearing on a national ("top N" across the
  whole US) list.

## Caption redaction (privacy) -- read this before trusting any `title`

The build contract's rule: *"Natural-person names are never published: rewrite a caption to 'Individual
plaintiff v. &lt;organisation defendant&gt;' when the plaintiff side is a person; keep organisations, 'In
re ...' captions, government entities. Write the rule conservatively... when unsure, redact."*

`redact_caption()` in `build.py` implements this as one conservative predicate: a caption side is treated
as an **organisation/government** only when it carries an explicit signal (a corporate suffix -- Inc,
LLC, Corp, Co, Ltd, LP, ...; a government phrase -- "County of", "State of", "United States", ...; an
institutional word -- University, Hospital, Diocese, Church, Bank, ...). **Every other side is treated as
a person and redacted.** This means "unsure" always resolves to "redact", exactly as instructed. The
redacted title never contains the raw case text anywhere it could be recovered -- not in the SQLite file,
not in the FTS index (`reports_fts.title` is built from the *post-redaction* title only), not in
`unresolved`/QA output. No raw caption is written to any file this build produces.

Three real, measured findings from the actual input data drove extensions beyond the rule's literal
wording (all covered by regression tests in `test_build.py::CaptionRedactionTests`, using the real
caption strings):

1. **The rule is symmetric, not plaintiff-only.** The literal rule only describes rewriting the
   plaintiff side (its example output keeps the defendant, "&lt;organisation defendant&gt;"). But this
   dataset contains suits between two named individuals with no organisation on either side --
   `Jogani v. Jogani, et al.` (a family dispute) and, more importantly, `Jane Doe vs. Alkiviades David,
   et al.`, where the defendant is a real, named private individual, not a company. Publishing
   "Alkiviades David" would violate the contract's own overarching statement ("natural-person names are
   never published"), so both sides are checked with the same predicate, and either side is redacted to
   "Individual plaintiff"/"Individual defendant" (pluralised to "plaintiffs"/"defendants" when the
   caption says "et al." or uses a plural noun like "Survivors").
2. **'trust' and a bare '&' are deliberately not organisation signals.** `Jean Michele Cross Revocable
   Trust v. Four P's Grp. LLC, et al.` names a living person in a personal estate-planning trust; treating
   "Trust" as an organisation marker would have published her name. A bare `X & Y` is equally consistent
   with a company (`Johnson & Johnson`) or two individual co-plaintiffs (`Lincome & Bishop`) -- there is
   no reliable syntactic way to tell them apart without a name database (out of scope, no network), so
   neither is treated as a safe signal. **Accepted cost:** a legitimate bare-brand defendant with no other
   corporate marker (the one observed case is `Moore v. Johnson & Johnson`) is over-redacted to
   "Individual defendant" rather than risk under-redacting a real co-plaintiff pair. This is the
   conservative trade-off the contract asks for.
3. **Not every "In re ..." caption is a collective/mass-tort caption.** Probate captions --
   `In re Durable Power of Attorney of Dock Dean`, `In re Saunders`, `In Re: Amendment & Complete
   Restatement of the Arnold Rosenblatt Revocable [Trust]` -- name one specific private person. An "In
   re" caption is now kept verbatim only when its remainder contains a collective-litigation signal
   ("Litigation", "Products Liability", "Class Action", "MDL") or an organisation/government token, and
   is redacted (to `"In re: matter involving a named individual (name withheld)"`) whenever it instead
   carries a probate signal ("Estate of", "Guardianship", "Power of Attorney", "Trust", "Will", ...) or
   neither signal at all. **Accepted cost:** a legitimate collective caption with neither signal (e.g.
   `In Re: East Palestine Train Derailment`) is also over-redacted under this rule.
4. **A measured publisher data-quality defect: 669 of 3,312 rows (20.2%) concatenate several distinct
   case captions into one `case` string with no reliable delimiter** (e.g. `"Hetsler, et al. v. Ford
   Motor Co., et al. Lubben v. Lopez Latorre, et al. v. Mendez, et al."` -- three unrelated captions run
   together; mostly on "top-50"/"top-100" personal-injury and motor-vehicle settlement/verdict lists,
   519 settlement rows and 150 verdict rows). Rather than guess where one caption ends and the next
   begins and risk publishing a name hidden mid-blob, **any caption containing two or more "v./vs."
   occurrences is redacted wholesale** to `"Multiple parties (bundled entry; captions withheld)"`. This
   single rule accounts for the majority of the 95.5% redaction rate.

150 captions are kept verbatim: 6 institutional "In re ..." captions (NFL Sunday Ticket litigation, two
Catholic diocese abuse-litigation captions, a train-derailment litigation, a corporate securities
litigation, a surgery-center petition) and 144 organisation-vs-organisation captions (patent, trademark,
commercial and government-vs-company suits -- e.g. `Mayor & City Council of Baltimore v. Purdue Pharma
L.P., et al.`). All 150 were reviewed by hand while building the classifier; an end-to-end check was also
run directly against the built SQLite file (searching the FTS index and the `title` column for every
surname found problematic during development) and confirmed zero matches in `title` for any of them --
the only FTS hits on those surnames are unrelated attorneys who happen to share a common surname (e.g.
several different real attorneys named "Saunders", "Mendez" or "Jean" appear correctly in the `attorneys`
field of unrelated rows -- firm/attorney names are explicitly permitted to be shown per the contract).

Firm and attorney names are shown as printed (permitted -- "professional advertising"); `firms` and
`attorneys` text is passed through a defensive email/phone-number filter (none were observed in this
dataset, but the same filter used in `sources/settlements_20260919` is applied here too).

## MDL linking (measured: zero links, by design)

Rule: *"MDL links ONLY when the report itself names an MDL number or its caption equals a JPML registry
caption after whitespace/case normalisation; otherwise no link."* Both paths are implemented
(`extract_explicit_mdl_number` / `link_mdl` in `build.py`, loading `sources/jpml_mdl_20260919/mdls.jsonl`
read-only, never modified) and unit-tested against a synthetic registry. Run over the real 176-MDL
registry and all 3,312 real rows: **zero rows name an explicit MDL number, and zero captions are an exact
whitespace/case-normalized match to a JPML registry title.** This is expected, not a defect: the
publisher's captions are short-form case names ("In Re NFL Sunday Ticket Antitrust Litig.") while the
JPML's own captions are longer formal titles ("IN RE: National Football League Sunday Ticket Antitrust
Litigation" -- illustrative; the point is the two vocabularies do not agree even for the same litigation),
and the rule is intentionally strict rather than approximate, so it never guesses a link. If the report
data or the registry changes in a future rebuild, `mdl_number`, `mdl_link_basis`
(`report_named_mdl_number` or `caption_matches_jpml_registry_title`) and `mdl_registry_status` (`pending`
/ `terminated` / `not_in_registry`) are ready to populate; `for_mdl(n)` and the `mdl` listing filter
already work against them (see `test_verdict_reports.py::ForMdlTests`, which exercises the linked path
with a synthetic fixture since the real data has none).

## Record id

No native unique id exists on the source rows. `id` is `"vsr-" + sha256(list_url|rank|case)[:16]` --
deterministic (a re-run over an unchanged input file reproduces the same ids) and collision-free in
practice (verified unique across all 3,312 rows at build time; the build aborts if it ever is not).

## Dates

`year` is the publisher's own printed year (2024 or 2025 in this snapshot); there is no other date field
on a source row (no filing, entry or judgment date -- only the list's year). `subtitle` therefore reads
"`<jurisdiction> &middot; <county> &middot; <year>`" (county omitted when absent) rather than a court/date
combination -- **there is no distinct court field in this dataset**, only the publisher's own
jurisdiction ("california", "united-states", ...) and county ("los-angeles", ...) scope labels, kept
exactly as printed (no title-casing or renaming).

## Limits and what was not done

- No amount is ever summed, ranked or averaged by this layer or its adapter.
- `full_type` (the publisher's comma-separated full category list) is kept for `detail()` but not
  indexed for search beyond `primary_type`.
- The three other CSV files that ship beside `verdict_lead_index.json` --
  `vli_mass_tort_leads.csv` (670 rows), `vli_jurisdiction_rollup.csv` (10 rows),
  `vli_firm_leaderboard.csv` (400 rows) -- were inspected and are pure derivatives/subsets of the same
  3,312 records already in the JSON (a mass-tort filter, a per-jurisdiction rollup, and a firm
  leaderboard respectively); the JSON was used as the sole input so nothing is parsed twice or can drift
  out of sync with it. `vli_coverage_gap_states.csv` (1,408 rows) is likewise the subset of records
  outside California/Illinois, already present in the JSON.

## Local settlement/verdict-related data found but NOT used here (with paths)

- `C:/Users/firas/Downloads/SW-BULK/settlement-44708c4b6f7b873dc264-research-brief.md` -- a single
  research-note file about one SettleSignal settlement record (a pay-transparency class action), not a
  bulk dataset. That underlying SettleSignal feed is already the primary input of
  `sources/settlements_20260919` (see its README, "848 publisher references"); this brief does not point
  to any additional bulk data.
- `C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc/outcomes.jsonl.gz`
  -- 25 rows, `outcome_type: "fjc_case_disposition"`, every `amount`/`amount_disclosed` field `null`
  (only `raw_codes.amount_received` present, and empty or `"0"` on every row observed). Not usable as a
  settlement/verdict amount source; not incorporated. (Other files in that same release --
  `docket_entries.jsonl.gz`, `documents.jsonl.gz`, `matters.jsonl.gz` -- are already the inputs of
  `sources/mdl_docket_activity_20260919` and `sources/mdl_docket_documents_20260919`; not re-read here.)
- The other `AWS-BATCH1-DOCKETS` release directories (`b1-*`, `b2a-0ef93bbef92cff5b3532`) and everything
  under `staging/unpublished-drafts` and `.worktrees/` were not opened, per the environment rules (only
  `b2b-cdbb8d040b95c7b65cfc`, main tree, is in scope).
- `daubert_scan` under SW-BULK was not opened (explicitly out of scope for this task).

## Tests

```
C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe -m unittest discover -s sources/verdict_settlement_reports_20260919 -p test_build.py
C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe -m unittest discover -s delivery/archive-directory -p test_verdict_reports.py
```

44 build tests (amount parsing 8, amount banding 6, caption redaction 17, MDL linking 7, id generation 3,
real-data smoke test 1 -- includes `test_real_registry_has_zero_exact_caption_matches`, which documents
the zero-MDL-link finding as an assertion, not just prose) + 38 adapter tests (listing shape 6, listing
filters 12, detail 6, for_mdl 4, summary 2, fail-closed gate 5, real-data 2 -- skip automatically if the
supplement has not been built in a given environment).

## Re-run

```
C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe sources/verdict_settlement_reports_20260919/build.py
```

Deterministic and offline: re-running over the same `verdict_lead_index.json` and
`sources/jpml_mdl_20260919/mdls.jsonl` reproduces the same row count, ids, redaction decisions and
(barring a JPML registry change) MDL-link count.
