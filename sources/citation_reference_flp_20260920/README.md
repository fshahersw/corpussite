# Citation guide

Case reporters with every series and its years, statute / regulation / session-law citation forms by jurisdiction, law journals, and the standard case-name and state abbreviations, from Free Law Project reporters-db (BSD-2-Clause). One row per published entry; an abbreviation listed twice upstream stays two rows.

- Adapter: `delivery/archive-directory/citation_reference.py`
- In the app: Courts & litigation > Courts > Reporters & citation forms (`#citation-guide`)
- Gate: `validation.json` (status `passed`); the adapter closes if the data file no longer matches its recorded SHA-256.

## Rules

- A finding aid for reading citations. It does not say any cited authority is saved here.
- Edition years are the publisher's; an open end year means "recorded as still published".

## Counts (from validation.json, 2026-09-20)

- entries: 2,433
- reporters: 1,262
- laws: 373
- journals: 798
- editions and variants: 4,931
- abbreviations: 289
- abbreviations listed more than once: 27

## Rebuild

    python build.py
