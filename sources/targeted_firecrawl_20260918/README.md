# Targeted Firecrawl pilot — September 18, 2026

This closed pilot used **13 of its maximum 50 credits**. The account balance changed from 5,100 to 5,087. Two discovery searches consumed four credits; nine provider captures consumed nine credits. No further scrape or search job is active.

## Eligible directory supplement

`resources.jsonl` contains **one eligible record**: the New York State Division of Human Rights directory, with 4,875 characters of provider-extracted text and observed links. It is a directory, **not saved statutory body text**. Provider JSON, extracted Markdown, hashes, retrieval metadata and source evidence are retained. A provider response is **not original HTTP bytes**.

## Redirect finding and exclusion

The agency's apparent section URLs redirected to NYSenate. This was detected while reviewing the first collected section bodies; the pilot was stopped immediately. Eight responses had already been returned. They are labeled `quarantined_redirect_to_blocked_publisher` in `attempts/`, remain as incident evidence, and are excluded from `resources.jsonl`. They must not enter the central directory or legal corpus as eligible captures. See `redirect_incident.json`.

The source-control evidence already records barriers for the NYSenate laws directory, DOS constitution entry, and NYCourts rules entry. No request to those original URLs was made by this pilot. The unexpected provider redirects demonstrate why future provider requests require an original-source redirect preflight as well as robots and shared-host checks. The pilot runner refuses to restart while `PILOT_CLOSED.json` is present.

The alternative CJC constitution excerpt was robots-disallowed and received no provider request. Two additional section URLs were deferred by shared host coordination and never submitted to the provider. Their discovery links remain inventory only.

## Scope and evidence

- `selected_inputs.json`: 12 candidate URLs and their discovery evidence.
- `search-*.json`: provider search results, not saved source pages.
- `credits-before.json`, `credits-after.json`, `receipt.json`: account/provider accounting.
- `access_evidence/`: original robots responses and shared-host coordination evidence.
- `provider/`, `text/`, `links/`: retained provider captures and derived artifacts; eligibility is determined only by `resources.jsonl`.
- `attempts/`, `scheduling_deferrals/`: completed and deferred outcomes.
- `manifest.json`: local artifact hashes.

New York's nationwide official-law coverage gap remains unresolved. No completeness or current-law claim is made.
