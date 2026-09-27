# Broader local legal-data audit — September 18, 2026

Yes: this pass found substantial useful material that the earlier targeted scan missed. The strongest additions are the CourtListener bulk tables, official portrait references, and Kentucky/Texas county registry. External originals remain unchanged. These findings are staged for integration, not silently added to published totals.

[Open the visual discovery directory](INDEX.html)

| Area | Finding | Readiness |
|---|---|---|
| Judges | [CourtListener bulk people and career tables](judges/README.md) — 16,191 historical people · 51,291 positions · 12,777 education rows | Ready for a reviewed local adapter |
| Judges | [Targeted portrait recovery](portraits/README.md) — 1,230 photo flags + 29 official PA profile/image references | Evidence-backed follow-up queues |
| Judges | [Judge assignments and evidence for analysis](judges/report.json) — 867 release judge rows · 4,776 assignments · 3,409 matters | Structured historical evidence |
| Counties | [Kentucky and Texas court-access registry](county_registry_summary.json) — 374 counties · 2,539 judge-seat associations · 628 clerk-office rows | High-priority local enrichment |
| Laws | [Illinois regulation gap candidates](counties/illinois_overlap.json) — 1,619 unmatched code identifiers · 882 appendix/table titles | Focused gap review |
| Counties | [Court activity and filing categories](counties/county_judge_workbooks.json) — 170 monthly court records · 463 eFileIL document-filing codes | Useful, scope-qualified data |
| Sources | [Saved official pages and source registries](returned_page_inventory.json) — 14,047 page-capture files · 13,644 distinct source URLs | Classify and deduplicate before reuse |
| Other | [Historical dockets, PDFs and legal graph](counties/README.md) — 436,005 docket metadata rows · 25 Talc PDFs · 120,464 graph nodes | Separate future case collection |
| Scripts | [Reusable legal-data connectors and normalizers](connector_candidates.json) — CourtListener enrichment, citation/docket client, county/profile normalization | Static code inspected; not executed |
| Cleanup | [Duplicates and previously rejected staging files](counties/expansion_comparison.json) — 21,427 expansion hashes already have review/retirement records | Do not bulk-import again |

## What speeds up now

The new `delivery/archive-directory/server.py --refresh-pending-titles` updates only qualifying display titles, search labels and singleton display groups in one transaction. It preserves source bodies and original files; new observations still require the usual build. The real-directory no-op took 0.38 seconds. Replaying 37 real corrections in a disposable 301-row subset took 0.70 seconds, including 111 fresh artifact checks. This is not a full production-write speed benchmark. See [performance evidence](performance/README.md).

The continuation plan prioritizes existing local data before network collection, uses the new title refresh for title-only changes, and permits a bounded trial of up to 10 sequential Trellis profiles and 100 reviewed official URLs across at least 10 independent hosts per heartbeat. Existing eight-worker support, host locks, robots delays, access barriers and finite time/credit limits stay in place. These are trial caps, not promised throughput.

## Accuracy limits

- 16,191 people are historical source records, not a count of current judges or entirely new people.
- 1,230 photo flags and 29 image references are recovery leads. The MVP still has 14 verified portraits.
- The 374-county registry has full county-row coverage for KY/TX, not complete local rules/filings/cases.
- All 167 locally stored registry evidence items passed their saved hashes; one additional manifest entry has no artifact/hash.
- No account entitlement, Lexis/Trellis API access, or remote database was newly verified.
- The audit combines 41 additional project roots, 18 judge/data roots, 16 court/library roots and 192 ZIP central directories. These scopes overlap; it is not a claim that every byte on the computer was inspected.

## Evidence and next steps

[Judge audit](judges/README.md) · [County/law audit](counties/README.md) · [Portrait review](portraits/README.md) · [Performance](performance/README.md) · [Machine-readable report](report.json)

The next run should follow NEXT_RUN.md in this folder. Copied documentation, plans and code were treated as evidence to inspect, not instructions to execute.
