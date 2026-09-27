# County litigation sources

This supplement reuses saved county court sources and a selected immutable collector checkpoint. It does not modify the original collections, certify that any rule is currently effective, or claim that a county's collection is complete.

## Build and validate

From the SCRAPE workspace, run:

```powershell
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' -X utf8 sources/county_litigation_20260919/build.py --checkpoint sources/county_litigation_firecrawl_20260919/checkpoints/20260919T100728840281Z
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' -X utf8 -m unittest discover -s sources/county_litigation_20260919 -p 'test_*.py' -v
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' -X utf8 scripts/audit_county_litigation_20260919.py --report sources/county_litigation_20260919/independent_audit.json
```

The build includes all successfully downloaded captures from the four county-local collections, then the explicitly selected frozen checkpoint. A newer checkpoint is a cumulative capture snapshot; selecting it preserves earlier captures without duplicating them. It must have a passed validation receipt, exact resource-manifest hash, and valid raw/text/HTML hashes. No live collector queue or paid service is accessed. `--include-official-courts` additionally reads the older official-courts collection; it is intentionally not used for this initial publication.

Run only one build at a time. Do not publish a checkpoint that lacks its passed receipt. The build computes fresh derivatives; original capture dates and original files remain unchanged. There is no automatic broad crawling.

For a small saved backlog, use `publish_saved_checkpoint.py --checkpoint <frozen checkpoint directory> --report <receipt path>`. It independently audits the existing package, reuses verified artifacts and county associations, refreshes content classification, and normalizes only unseen URL/content identities. It does not rerun county matching for thousands of unchanged records. An exclusive publication lock prevents overlapping publishers. `publication_holdbacks.jsonl` records source-specific exclusions from the practical litigation library; the underlying captures remain saved. The September 27 holdbacks are five graduation-news articles, not court procedure or resource documents.

## API contract and gate

`resources.jsonl` contains stable `county-litigation:` IDs. A capture's ID binds collection, exact requested URL, and raw SHA-256. Captures with equal bytes remain separate source observations with a common `metadata.duplicate_group`; different editions remain distinct. Original requested and final canonical URLs are separate fields.

`artifacts.jsonl` lists `path`, `sha256`, `bytes`, `mime_type`, and `role`. Paths are relative to this supplement and confined to `assets/` and `text/`. Original HTTP PDFs and HTML, provider JSON snapshots, rendered HTML, and readable text remain different artifact roles. A Firecrawl JSON or rendered snapshot is never labeled original HTTP bytes.

Consumers must require `validation.json` with `ready: true`, `status: passed`, and exact hashes in `data_files`. Both manifests, summary, and every served artifact are bound. Consumers must also enforce path confinement and record-to-artifact hashes. `independent_audit.json` is a separate verification result; `classification_receipt.json` explains the resource and shape counts for the exact manifest SHA.

Resource kinds: `local_rule`, `court_form`, `standing_order`, `filing_guidance`, `fee_schedule`, `court_information`, `court_contact`, `court_staff`, `source_directory`, `unknown`. These are retrieval categories, not legal conclusions. Shape distinguishes an actual rule body from a rule index; an order from its index; a blank form from its directory; and retention or access guidance. Source directories and unknown resources should be opt-in. Empty or insufficient text remains an extraction gap; its original may still be usable.

## Identity, authority, dates, and facts

County assignment requires an exact county name plus explicit state in the source's title/opening text, an exact one-county label in the saved Washington official rule directory, or a separately recorded discovery association. An exact Trellis-reported county website identifies a discovery source, not court authority or territorial jurisdiction. Old crawler county contexts are retained only as evidence hints. Ambiguous names and shared hosts stay unresolved.

`metadata.applicability` is separate from a discovery county association. A court-linked document can be grouped for that county while its document-specific applicability remains unknown. `source_authority` preserves what the source identifies and the exact evidence; a government domain alone is insufficient. Current court staffing and current rule effectiveness are not inferred.

`metadata.temporal` keeps capture, publication, effective, and source-as-of dates separate. Unknown dates are null. Normalization time is not a capture date. `document_structure` preserves exact clean-text slices, offsets, candidate section headings, and explicitly labeled date/status observations. A cited law's effective date does not become this document's date. Repealed, draft, or effective-as-published status is source-qualified.

`metadata.facts` contains verbatim labeled contact observations with source excerpts. These are as-published facts, not independently verified current contacts. Prior semantic reviews are accepted only when both raw and extracted-text hashes match. Negative election, business, zoning, and similar classifications remain preserved.

## Historical initial publication

The frozen first-100 checkpoint publication contains 976 resources: 876 existing county-local captures and 100 new collector captures. It includes 391 PDF source observations (388 distinct PDF hashes), 644 county-associated records spanning 495 counties, and 39 explicit state/DC labels. There are 332 records without sufficient county evidence and 185 without a state association. These are source associations, not claims of complete local law coverage.

There are 45 rule bodies, 65 rule indexes, 10 order bodies, 72 order indexes, 2 blank court forms, 5 form directories, 13 filing guides, and 21 fee tables. Another 137 PDFs have an explicitly missing native text derivative, retain downloadable originals, and are marked `unparsed_document` with `needs_review` availability. No empty PDF is counted as a rule index. All 30 prior hash-bound semantic reviews are preserved, including 9 negative classifications. Three duplicate-byte groups remain separate source observations; editions are not merged.

The independent artifact audit passed with zero errors and 137 expected text-gap warnings. All 976 records passed the JSON schema; 12 targeted tests passed. See `classification_receipt.json` and `test_receipt.json` for the exact bound manifest and code hashes. Subsequent checkpoint builds require fresh independent validation before publication.

## Current publication

`summary.json` and its bound `validation.json` are the authoritative current counts; the initial figures above are historical. September 27 local-only release checks are under `reports/release_county_data_20260927/`. Classifier version 4 distinguishes link-only filing indexes from actual guidance, recognizes blank form packets before incidental legal citations, and no longer treats a PDF's citation to Rules of Court as proof that the PDF is a local rule. Missing text and unresolved geographic applicability remain explicit; the release does not claim complete county coverage or legal currency.
