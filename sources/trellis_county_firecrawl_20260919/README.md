# Trellis county profiles — bounded public-section batch

**Finished September 19:** all 1,054 selected requests attempted once; 1,028 substantive profiles accepted, including five reused pilots and 15 offline parser recoveries. The worker spent 1,054 credits, leaving 4,015; the five earlier pilot credits are reported separately (1,059 total for the phase). Final verification checked 3,084 artifact hashes. `final_receipt.json` and `summary.json` contain the frozen results.

There are 29 genuine profile-URL gaps: three heading-only/thin sections and 26 HTTP404 responses. Two additional failed Pennsylvania URLs were website links misclassified as county URLs; their requests and costs remain in history, and `invalid_candidate_exclusions.jsonl` removes them from the legitimate county denominator. Complete queue processing does not establish complete county websites or case-file coverage.

This batch uses the latest user authorization for 5,000 Firecrawl credits and the root agent's five successful basic-provider pilot responses. It targets only the exact remaining county-profile URLs already observed in the saved catalog. It does not discover more URLs or request cases, dockets, judge reports, or paid document endpoints.

`collect.py` is a finite, resumable worker with at most five concurrent basic-provider requests, a 1,500-credit ceiling for this worker and a 3,000-credit account reserve. Each URL receives one attempt. Access/auth/rate challenges, unexpected credit use, unknown credit state and timeouts stop the next batch. A failed or interrupted attempt is never silently retried. The existing private wrapper's DPAPI loader/runtime is reused; no credential is written to these files or command arguments.

The starting selection has 1,054 URLs after excluding the ten browser-selected captures, five pilot captures, the false website-as-county URL and prior access-blocked URLs. `selection.jsonl` records the exact finite selection; `started.jsonl` and `attempts.jsonl` retain attempt history. All five pilots are reused without requests.

## Artifacts

- `raw/`: unchanged Firecrawl JSON responses, including held/failed-content responses.
- `text/` and `html/`: extracted selected county information sections.
- `resources.jsonl`: only clean HTTP200, exact-URL, basic-provider responses with a county heading and substantive fields. Each row binds its raw response, extracted text and HTML by SHA256.
- `validation.json`: ready gate binding the current manifest hash and record count. Readers must use one bound manifest snapshot and recheck the validation file has not changed; a transient hash mismatch during an atomic checkpoint is unavailable, not a valid mixed snapshot.
- `progress.json`: current PID, attempted/successful/failed counts, queue state and account-credit difference.
- `credit_receipts.jsonl`: fresh provider account-credit checks before collection and after each batch.
- `failures.jsonl`: retained content/access/transport gaps. A thin heading-only page is not counted as a substantive profile.

Each resource retains its original observed-parent URL/label, queue SHA, source URL, capture time basis, provider scrape ID/cache state/cost, heading, field text and exact website href. County GEOIDs remain null until the adapter independently matches the heading/state and source-backed Census crosswalk. URL slugs alone do not establish a county identity.

The captures are provider-rendered representations of the selected information section, **not original HTTP response bytes**. Capture time is local acquisition completion; a cached provider response may be older. Trellis population, addresses and other fields are publisher claims, not independently verified current government data. No absence or current-service inference is made.

The historical direct robots probe of September13 returned403 and is preserved at `sources/trellis/resume_20260913/direct_robots_probe.json`. This batch uses the newly authorized and pilot-verified public basic-provider route; it sends no authentication cookies, uses no stealth escalation and does not retry specifically blocked profile URLs. Shared host pause/cooldown state is checked before each batch; no shared controls are changed.

Run `python sources/trellis_county_firecrawl_20260919/collect.py` to resume the unattempted finite remainder. An exclusive OS lock prevents duplicate writers. Do not start another paid collector against the same account while this worker measures account-credit deltas. Offline checks: `python sources/trellis_county_firecrawl_20260919/test_collect.py`.
