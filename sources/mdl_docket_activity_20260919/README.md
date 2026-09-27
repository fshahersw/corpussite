# mdl_docket_activity_20260919

Master/member-docket entries from the SW-BULK AWS release `b2b-cdbb8d040b95c7b65cfc`
(`docket_entries.jsonl.gz`, 41,946 rows), joined to the JPML MDL registry via
`sources/mdl_docket_crosswalk_20260919` (matter_id -> mdl_number, covering both the 59 master matters
and the 40 `member_of_mdl` member matters). Plan slice 7 of `reports/local_corpus_20260919/INTEGRATION_PLAN.md`.

License / status: derived from SW-BULK, a private firm dataset built from CourtListener/RECAP API data.
`license_ref: sw_bulk_private_firm_work_product`, `export_allowed: false`. Local personal-testing view
only; not for redistribution. Link out to CourtListener/RECAP for documents; RECAP bytes are never
re-hosted here (and none are read — this slice never touches document bytes, only metadata rows).

## What is unique here

`sources/mdl_docket_documents_20260919` (plan slice 2, built from the SW-BULK catalog's
`documents.json`) already covers 12 MDLs with richer per-document metadata (PDF URLs, page counts,
`doc_category`). This slice's sole reason to exist is the **six MDLs slice 2 does not reach at all**:

| MDL | Title (from the crosswalk) | Entries | Capped? |
|---|---|---|---|
| 2323 | National Football League Players' Concussion Injury Litigation | 2,000 | **yes** |
| 3060 | Hair Relaxer Marketing, Sales Practices, and Products Liability Litigation | 1,980 | no |
| 2243 | Fosamax (Alendronate Sodium) Products Liability Litigation (No. II) | 1,920 | no |
| 1570 | Terrorist Attacks on September 11, 2001 | 1,835 | **yes** |
| 2904 | American Medical Collection Agency, Inc., Customer Data Security Breach Litigation | 936 | no |
| 2921 | Allergan Biocell Textured Breast Implant Products Liability Litigation | 764 | no |

For the 12 MDLs both slices cover, the two sources are kept separate and labelled here; nothing is
merged between them.

## Files

- `entries.jsonl` (26,549 rows) — one row per docket entry whose matter resolves to a registry MDL.
  Fields: `id` (native `docket_entry_id`, a UUID, already unique — used as-is, no composite needed),
  `mdl_number`, `matter_id`, `entry_number` (string, always), `entry_number_int` (int or null, only when
  purely numeric), `description` (court docket text, **with an individual member-case plaintiff caption
  redacted** — see below), `description_redacted` (bool), `redaction_basis` (string or null),
  `member_docket_number` / `court_id` (this entry's own AWS matter's native docket number and court —
  from `matters.jsonl.gz`, never the MDL master docket), `cl_docket_id` / `cl_docket_id_basis` (this
  matter's CourtListener docket id, from `matter_aliases.jsonl.gz`, or null when the matter carries no
  alias — never guessed), `entry_type` (see classifier below), `published_at` / `published_at_basis`
  (see dates below), `has_verified_document`, `verified_document_count`, `record_status`,
  `schema_version_source`.

## Party-name redaction (build contract: "natural-person plaintiffs are never listed; show counts")

`description` is the court's own docket text; a deterministic redactor (`redact_description` in
`build.py`) withholds the caption of an individual member-case plaintiff before the row is written —
the pre-redaction text is never written to any output file. Three shapes are redacted, replacing the
captured plaintiff caption with `[plaintiff name withheld]`:
1. `Short Form Complaint[s] [-] <caption> by PLAINTIFF(S)` (the MDL 2323 NFL-concussion short-form
   complaints and similar member-case filings across other MDLs).
2. `Plaintiff/Decedent <First Last>` narrative references.
3. The plaintiff side of an `<X> v. <Y>` party-style caption (deliberately conservative: it also
   redacts a non-person plaintiff caption such as a state or company name, erring toward withholding
   rather than risking an unredacted natural-person name).

Attorney/filer parentheticals (e.g. `(SEEGER, CHRISTOPHER)`), organisation names and named defendants
are left intact — those are permitted under the build contract. `description_redacted` and
`redaction_basis` flag the rows this applied to; `counts.entries_with_redacted_caption` in
`validation.json` reports how many. A build check (`no_published_description_matches_plaintiff_caption_regexes`)
and an adapter test (`listing({'q': 'Brett Basanez'})['total'] == 0`) assert the rule holds. Because a
redacted row's caption carries no other identifying case reference, the row also carries
`member_docket_number` + `court_id` (native to its own AWS matter) so the adapter can render `"<docket
number> (<court>)"` in place of the (now withheld) caption, and `cl_docket_id` so it can still link out
to the case on CourtListener.
- `unresolved.jsonl` (15,397 rows) — entries whose `matter_id` has no MDL link in the crosswalk. Parked
  with a reason, never dropped, never guessed.
- `mdl_summary.jsonl` (36 rows) — one row per matched MDL: `mdl_title`, `mdl_status`,
  `is_unique_to_this_slice`, `total_entries`, `counts_by_type`, `published_at_min`/`max`,
  `entries_with_no_parsed_date`, one `matters[]` entry per contributing AWS matter (a master matter can
  have separate member matters, each with its own entry-number sequence), `entries_capped` /
  `entries_capped_basis` (mirrors the largest/primary matter), `latest_25_ids` (entry ids, most recent
  `published_at` first, ties by `entry_number_int` descending — resolve against `entries.jsonl`).

## Entry-type classifier

Deterministic, priority-ordered, case-insensitive keyword match against `description`; **first match
wins**, so every entry gets exactly one label. Priority order: `settlement`, `bellwether`, `daubert`,
`common_benefit`, `steering_committee`, `leadership`, `pretrial_order`, `case_management_order`, else
`other`. This means the per-type counts here are **not** the same as the plan's file-wide keyword-hit
counts (an entry can contain more than one keyword; the plan's counts allow overlap, this classifier does
not). Implemented independently in this file (`classify_entry_type` in `build.py`); the
`mdl_docket_documents_20260919` slice's `doc_category` classifier, if and when it exists, is a different
implementation over different source text and is never imported here.

## Dates

There is no structured date field on a docket entry. `published_at` is parsed **only** from the trailing
`(Entered: MM/DD/YYYY)` text of the description, converted to ISO `YYYY-MM-DD`, with
`published_at_basis` recording exactly that rule. Left `null` when the pattern is absent or the calendar
date is invalid — never inferred any other way. 24,107 of the 26,549 MDL-matched entries carry that text.

## The "~2,000 per matter" cap — measured, not assumed

Several MDLs cluster at or just under 2,000 entries (2323: 2,000; 2873: 1,997; 2570: 1,988; 3060: 1,980;
2592: 1,967; 2243: 1,920 — the last of these is coincidental, see below), which looks like a uniform
release-side row cap. It is not uniform. `entries_capped` is measured **per contributing matter** by
comparing the highest observed `entry_number_int` to the row count: a gap of 50 or more between them means
rows beyond that number were not captured in this release (a real cap); a smaller gap is ordinary docket
noise (stricken/renumbered entries), not truncation.

- **Real cap (large gap):** MDL 2323's matter has entries 1 through 2,000 present but the highest
  `entry_number` on record is 3,138 — 1,138 entries missing. MDL 1570 similarly: entries 1–1,835 present,
  highest entry_number 3,826 — 1,991 missing. (Also true of two MDLs slice 2 already covers: 2873 —
  325 missing — and 2804 — 280 missing.)
- **No cap, just a small docket (of the six unique MDLs):** 2243, 2904 and 2921 have entry numbers running
  contiguously from 1 to the row count with **zero** gap — the release holds every entry these dockets
  have. 3060 runs 0–1,980 with a gap of 1 (a single stricken/renumbered entry) — effectively complete.

Treat any MDL flagged `entries_capped: true` as a **lower bound**, never as the full docket.

## Verified-document flag

`documents.jsonl.gz` (13,971 rows, all `verification_status: "verified"`) joins to `docket_entries.jsonl.gz`
on `docket_entry_id` (10,676 distinct entries have at least one verified document; one entry can have up to
66). Only a boolean flag and a count are published; `s3_bucket`, `s3_key` and `sha256` from that file are
read to build the count and are **never** written to any output here (s3 pointers are never exposed).

## Build / tests

`python -m unittest discover -s <this dir> -p test_build.py` — unit tests for the classifier, date
parser, entry-number normalizer and cap-detection rule, plus one smoke test that runs the real build and
checks the plan's key measured numbers. `python build.py` re-runs the build and rewrites `validation.json`
(deterministic; same inputs always produce the same output bytes and hashes).
