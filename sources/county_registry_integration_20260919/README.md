# Kentucky and Texas county supplement

`county_registry.county(geoid)` reads this separate, hash-gated cache. It does not modify the main county inventory, merge judges, or make network requests. Uncovered counties and failed validation return `available:false`.

The historical registry has 374 county rows: 120 Kentucky and 254 Texas. FIPS, state and county names join the current county inventory; only capitalization is normalized (LaRue/Larue). Original tagged fields preserve observed, derived and null values in collapsed provenance.

`source_as_of` is the registry generation date, August 22, 2026. This is not an appointment, legal effective date or current-service check. Source observations and timestamps remain in provenance. `saved_at` is null because the later local-copy date is not established.

Court/clerk URLs are source references, sometimes statewide PDFs rather than court websites. Access links are observed links, not claims of downloaded documents or present availability. CARD is a court administration directory, not case search. Judge-seat counts are county associations and can repeat a person.

`build.py` binds the independently audited registry and evidence-manifest hashes, verifies 167 stored evidence files afresh, and records the one known unsaved Texas clerk-directory PDF. It retains no invented route for missing county case access. No external file-serving endpoint is provided.

Run `build.py` to prepare the cache, then `test_county_registry.py` for identity, date, evidence, URL and failure-gate checks. `validation.json` binds the normalized cache SHA-256; `evidence_verification.json` lists actual artifact verification.
