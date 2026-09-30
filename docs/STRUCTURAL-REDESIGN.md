# Corpus structural redesign — 2026-09-30

This revision replaces the previous additive dashboard, not the underlying archive. It is built around three task-oriented workspaces.

## Acceptance contract
- Corpus: exact collection tree, source rows in the middle, persistent reader on the right; global indexed search across only published research collections; selected dataset and native ID remain attached to every record.
- Atlas: geographic map, explicit state-court hierarchy, source-reported multi-county practice units, historical status separation, and native court-to-MDL joins. No county-to-federal-district or appellate hierarchy is inferred.
- Matters: registry → native docket directory → selected docket entries/documents → source reader → exact linked counterpart records. Captured dockets and source dates are not represented as complete or current.
- Mobile: reader opens on deliberate record selection and offers Back to results. No page-wide horizontal scroll.

## Database work
Three additive migrations in this branch match the applied Supabase migration versions. They create 5,413 court identity projections and 35,724 docket-source projections, plus bounded search and native-ID join functions. Original corpus rows, publication gates, private bucket settings, and existing permissions remain unchanged. New projection tables have RLS enabled, no anon/authenticated grants, and service-role-only RPC execution.

Initial reconciliation identified 96 document references without a directly recorded CourtListener URL. Ninety-five were uniquely resolved by exact court ID + exact source docket number already corroborated by native IDs in other saved records. One remained unresolved. Non-numeric entry labels become NULL only in the projection, preventing invented entry joins.

Projection refresh: rerun the INSERT ... ON CONFLICT portions of the first migration and the reconciliation updates after an ingestion changes the source catalogs. The projections are a dated derived index, not a separate source of truth. Search itself reads the source tables and rechecks publication gates.

## Search boundary
Indexed keyword search uses existing source search vectors. Broad searches examine at most 2,001 matches and explicitly label truncated totals, sample facets, and ranking; no semantic-search or complete-universe claim is made. Unsupported filters are rejected instead of discarded. Unknown IDs never broaden to another collection. Ended court dates take precedence over contradictory in-use flags in the display projection.

## Scope not included
No paid docket acquisition, mass PDF downloading, or publication of held collections. No parent-site authentication changes. Existing specialist readers remain reachable for their advanced contracts.
