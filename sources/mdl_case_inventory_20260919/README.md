# mdl_case_inventory_20260919 — member-case inventory and relationship graph (plan slice 8)

Built 2026-09-19, offline, from local read-only inputs. `build.py` is deterministic and re-runnable.

## What this is

One row per matter in the SW-BULK AWS release `b2b-cdbb8d040b95c7b65cfc` (4,159), enriched where any
courtlistener_docket_id/courtlistener_master_docket_id alias of that matter also appears in
`SW-BULK/catalog/matters.json` (2,122 of 2,122 catalog dockets joined to a matter), plus a typed
relationship graph (docket→court, docket→judge, docket→MDL).

**This is a firm-focused docket sample, not an MDL member-case list.** A per-MDL count here counts
what this corpus holds; it is never the size of the MDL. `mdl_coverage.json` puts every per-MDL count
next to the JPML report's own `actions_pending` / `total_actions` (report dated 2026-09-01) as two
plain counts — never a ratio or a completeness claim of any kind. The largest blocks are small
slivers, e.g. MDL 2789 — 219 cases here against 11,404 actions pending; MDL 2873 — 153 against
15,264; MDL 3060 — 153 against 12,129.

## Files

| File | Rows | What it is |
|---|---|---|
| `cases.jsonl` | 4,159 | one row per matter: native ids, court, dates, status, judges, MDL membership + evidence |
| `graph_edges.jsonl` | 12,872 | typed edges `{from,to,relation,basis,evidence}` |
| `unresolved.jsonl` | 5,072 | every join that did not resolve, each with a reason; nothing is guessed |
| `mdl_coverage.json` | 59 | per-MDL rollups recomputed from `cases.jsonl` + the JPML denominators |
| `validation.json` | — | uniform envelope: counts, checks, sha256 of each data file, qualification |

## Measured counts (from validation.json, all recomputed from the emitted rows)

- 4,159 matters: 4,086 `public_docket`, 57 `target_matter`, 16 `support_master` (exactly three roles).
- 4,159 of 4,159 carry a CourtListener docket id; record id is `cl_docket:<int>`.
- 2,122 catalog dockets, 977 with an `mdl_master_docket_id`; all 2,122 joined to a matter (the join
  is against every courtlistener_docket_id/courtlistener_master_docket_id alias of a matter, not
  only the single id chosen for that matter's record identity — 18 matters carry more than one such
  alias).
- 901 matters carry an MDL link across 59 MDLs: 59 master dockets, 40 `member_of_mdl` edges,
  802 via a catalog `mdl_master_docket_id` that resolves to a crosswalk master. 3,258 have none:
  3,245 with no member_of_mdl relationship and no resolvable catalog master, plus 13 whose
  member_of_mdl relationship in the AWS release points at a parent matter the crosswalk cannot
  resolve to a JPML registry MDL (`member_of_mdl_parent_not_resolvable`, 4 distinct parents).
- 116 distinct courts linked; 0 matters with an unlinked court.
- 4,776 judicial-assignment rows (3,422 assigned, 1,354 referred) over 3,409 matters; 848 distinct
  CourtListener person ids; 3,096 rows bridge to 556 MVP judge entities, 1,680 do not.
- 12,872 edges: 4,159 `filed_in_court`, 6,443 `assigned_judge`, 1,369 `referred_judge`,
  59 `master_docket_of_mdl`, 40 `member_of_mdl`, 802 `catalog_mdl_master_docket_id`.
  (Judge relations produce up to two edges per assignment: one to `cl_person`, one to `judge_entity`
  when the person bridges.)

## Join rules

- **Courts**: exact CourtListener court id match against `sources/court_spine_20260919/courts.jsonl`.
  Never by name. A court id not in the spine leaves the row unlinked with the reason recorded.
- **Judges**: the release's `judges.native_judge_id` **is** a CourtListener person id — the SW-BULK
  loader selects `p.id AS judge_id` from the CourtListener people table
  (`AWS-BATCH1-DOCKETS/src/batch1_registry/bulk_enrichment.py`) and writes
  `native_judge_id=str(raw["judge_id"])` sourced as `courtlistener_bulk_dockets`
  (`batch2a.py`). The MVP judge entity is reached **only** through `ids.cl_person_id` in
  `sources/judge_structured_20260919/overlay.jsonl`. As a QA cross-check (never a join), 558 of the
  867 release judges appear as a bridged `cl_person_id` and 555 of those 558 agree on name;
  the 3 that differ are diacritic/nickname variants of the same person.
  `catalog/matters.json.judge` is free text and is published as text only — it creates no link.
- **MDL membership** precedence: the matter is the MDL master docket in slice 1's crosswalk >
  a `member_of_mdl` edge in slice 1's `edges.jsonl` > a catalog `mdl_master_docket_id` that slice 1
  resolves to a registry MDL. Nothing else. Every linked row carries `mdl.evidence`. A matter with a
  real `member_of_mdl` relationship in the AWS release whose parent matter the crosswalk cannot
  resolve gets the distinct reason `member_of_mdl_parent_not_resolvable`, never the generic
  "no relationship" reason (13 matters, 4 distinct unresolvable parents).
- **Catalog join**: a catalog docket joins a matter if it matches ANY active
  `courtlistener_docket_id`/`courtlistener_master_docket_id` alias of that matter in
  `matter_aliases.jsonl.gz`, not only the single alias chosen as that matter's primary
  `cl_docket_id`. Extra aliases are recorded on the row as `cl_docket_id_aliases`.
- **Firms**: `catalog/matters.json.firms` is free text with no ids; it is never reconciled against
  slice 5's canonical firm ids.

## Labelling and safety

- **Captions.** Only a collective "In re …"/"In the Matter of …" caption that carries no
  " v."/" vs." party separator is displayed. A caption that merely begins with "In re" but names a
  party (e.g. an individual member-case caption such as "In re: Samuel Keller v. Electronic Arts
  Inc.") is still suppressed, because it names a natural person. Every other caption may also name a
  natural-person party and is suppressed to `null` with `case_name_suppressed_reason`; those rows
  are titled by docket number and court. `defendant` from the catalog (an organisation field) is shown.
- **CourtListener links** are built from the docket id alone. The slug the catalog records spells the
  caption, so it is never emitted.
- **Dates** are kept separate and labelled: `aws_date_filed` / `aws_date_terminated` (docket dates as
  recorded by the release), `catalog_date_filed` / `catalog_date_terminated`, and
  `temporal.source_as_of` 2026-08-24 (release manifest build date). No effective dates exist here.
- **Nature of suit is absent** from every file of the AWS release and from `catalog/matters.json`.
  It is emitted as `null` with a basis string and is never inferred.
- Counts are counts as of the dataset build date, never rates or completeness claims. No rollup was
  copied from `matter_graph/` or `firms.csv`.

## Limits / known gaps

- Coverage is the firm's collection, not the MDLs. 3,258 of 4,159 matters have no MDL link at all.
- 59 of 176 registry MDLs are represented; 166 are pending.
- 1,680 of 4,776 assignment rows do not reach an MVP judge entity (the person is not bridged in
  `judge_structured`), and 60 assignments have no native judge id at all. They stay visible as
  unlinked with the reason.
- All 2,122 catalog dockets now join a matter (via any alias); none are left unmatched.
- 134 catalog dockets carry an `mdl_master_docket_id` that the crosswalk cannot resolve to a JPML MDL
  (`catalog_mdl_master_not_resolvable`); the remaining difference between 977 links and 802 rows
  credited to that basis is rows where a native master/member relationship already won on precedence.
- `unresolved.jsonl` by type: 3,245 `no_mdl_membership`, 1,680 `judge_entity_unbridged`,
  134 `catalog_mdl_master_not_resolvable`, 13 `member_of_mdl_parent_not_resolvable`.
- `parties_by_docket/` (59 of 2,122 dockets) was not used: natural-person plaintiffs may not be
  listed and its `attorney.firm` field is sometimes a street address.
- The `.worktrees/batch2-corpus-execution` copy of SW-BULK differs on 39 `mdl_master_docket_id`
  values and was never read. Main tree only.

## Licence / status

`license_ref: sw_bulk_private_firm_work_product`, `export_allowed: false`. Local personal-testing
view derived from a private firm dataset built from CourtListener/RECAP API data; not for
redistribution. Documents are linked out to CourtListener; no RECAP bytes are re-hosted.

## Run / test

```
C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe sources/mdl_case_inventory_20260919/build.py
python -m unittest discover -s <abs>/sources/mdl_case_inventory_20260919 -p test_build.py
python -m unittest discover -s <abs>/delivery/archive-directory -p test_mdl_case_inventory.py
```

No third-party packages. No network.
