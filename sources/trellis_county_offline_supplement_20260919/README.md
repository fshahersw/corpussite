# Offline county profile supplement

Two previously saved HTTP 200 responses contain useful fields despite having fewer than 100 markdown characters. They are recovered here without another request:

| Source | Saved population claim | Saved county seat |
| --- | --- | --- |
| Major County, Oklahoma | 7,527 | Fairview |
| Seminole County, Oklahoma | 25,482 | Wewoka |

Lowndes County, Georgia remains excluded: its saved selected HTML contains the heading and empty information containers, with no county fields or links. There is no full-page HTML beyond the selected container in that response to recover.

`resources.jsonl` uses the original collector schema. Raw paths reference immutable JSON files in `sources/trellis_county_firecrawl_20260919/raw/`; extracted text and HTML live in this supplemental folder. `validation.json` binds the two-row manifest and the supporting summary/review. The original 1,028-row manifest, collector code, failure history and credit receipts are unchanged. No network requests, new credits or identity merges were made. Capture times come from the original completed-at receipts; normalization time is separate.

The saved fields are publisher claims without a known effective date. Neither response supplies a website, address, filings or docket content. GEOIDs are deliberately unresolved here; the coverage adapter independently checks exact names, observed parent labels and explicit state evidence before assigning a Census identity.

The county integration agent's source-bound review of the 26 Virginia city-suffixed 404 URLs found 24 with another saved profile for the exact Census identity. Richmond city and Roanoke city do not have another explicitly matched saved detail. The unsuffixed Roanoke response has a city-versus-county heading conflict and remains unassigned. These are alternate saved profiles, not evidence that the failed URLs work or that URL variants are interchangeable. The precise review and its source hash are retained in `offline_review.json`.

Reproduce with Python 3.11: `python sources/trellis_county_offline_supplement_20260919/build.py`. The script validates pinned raw response hashes and the frozen collector/manifest before extracting. It performs no network operations and writes only in this supplement.
