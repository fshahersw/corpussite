# Evidence-supported portrait backfill

Nine held local portraits now have unique, source-supported links to existing
judge entities. Together with the five previously accepted links, this resolves
**all 14 known portraits in the local asset pack**. All are from the Northern
District of California; this is not national portrait coverage.

Identity was established through the named official portrait profile, explicit
court affiliation, and two to four corroborating education, appointment or
service facts in the saved FJC-derived entity record. No facial identification or
automatic expansion of initials was used. The original entity names and source
manifests are unchanged.

`accepted_links.jsonl` provides exact entity IDs, original image paths, SHA-256,
verified local copies, and per-link evidence-file paths and hashes.
`evidence/asset_*.json` retains literal corroborating passages, source URLs,
provider-capture hashes, original entity rows and rejected namesakes. A copied
local official court-history page supplies additional service-year evidence.

William H. Orrick is linked to **William Horsley Orrick III**, supported by Yale
1976, Boston College JD 1979, commission May 16, 2013, and senior status May 20,
2023. William Horsley Orrick Jr. fails these facts. Differences in the two
sources' nomination and career dates are recorded and not overwritten or used
as identity evidence.

## Integration

1. Read `accepted_links.jsonl` and verify `verified_copy_path` against `sha256`.
2. Copy each image to its `proposed_presentation_path` within the presentation
   folder. These are nine small existing WEBP images, not new downloads.
3. Append the nine records in `portraits.append.jsonl` to the existing
   `sources/judge_presentation_20260918/portraits.jsonl`, preserving its five
   previous records and rejecting duplicate entity or asset IDs.
4. Rebuild the normal judge presentation database and verify portrait routes.

`portraits.append.jsonl` uses the current presentation schema:
`entity_id`, `id`, `path`, `sha256`, `mime`, `provenance`. The proposed destination
paths are not created by this backfill. Root owns integration; this package
does not modify the presentation database, current portrait manifest, entities,
or upstream files. `scripts/backfill_judge_portraits_20260918.py` rebuilds only
this package offline from the pinned local inputs and saved profile responses.

## Collection limits and source boundaries

Exactly nine official profile requests were made through the configured
Firecrawl account. Each returned the requested URL with status 200 and reported
one credit: the observed balance changed from 5,087 to 5,078. Provider responses
are extracted representations, **not original government HTTP bytes**.
No image bytes were downloaded from the web in this pass.

The targeted scan compared both staging and curated asset manifests, including
rejected candidates. It found no additional eligible portrait beyond the known
14. The only extra named candidate was the previously rejected decorative
gavel associated with Margo K. Brodie; that exclusion remains intact. The prior
eleven-folder discovery inventory is linked as context, not claimed as a new
exhaustive disk scan.

Permission status remains `not_established` as in the source manifests.
Current service, matter assignment, and court endorsement are not asserted.
`remaining_gaps.jsonl` is empty only for the nine held identity matches; other
judges in the wider corpus still lack portraits.

Validation: five focused regressions check Orrick disambiguation, rejection of
name/court-only matches, biography scope, exact evidence/image hashes, and the
decorative-gavel exclusion.
