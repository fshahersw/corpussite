# Court reference and seals

courts-db facts (citation abbreviation, level, type, place, parent court, the dates a court existed with the publisher's notes, name forms seen in captions) and seal-rookery seals, attached to the court registry by identical CourtListener court id only.

- Adapter: `delivery/archive-directory/court_reference.py`
- In the app: inside each court in Courts & litigation > Courts > Court registry; `/api/court-resolve?q=`; seal files at `/supplement-files/court_reference/<court id>`
- Gate: `validation.json` (status `passed`); the adapter closes if the data file no longer matches its recorded SHA-256.

## Rules

- No names are compared; a courts-db court that is not in the registry is kept and marked, not attached.
- A PNG original is shown only if it matches the SHA-256 printed in the project's own index (one does not and is held); 22 SVG-only seals are the project's PNG renditions and are marked as not hash-verified.
- A seal identifies a court. It is not an endorsement and must not be reused to suggest one.

## Counts (from validation.json, 2026-09-20)

- courts db courts: 2,809
- courts in registry: 2,504
- courts not in registry: 305
- registry courts: 5,413
- with citation abbreviation: 1,959
- with existence dates: 1,020
- with name forms: 2,150
- seals: 253
- seals hash verified: 231
- seals in registry: 250
- seals skipped: original_missing 0; hash_mismatch 1; rendition_unavailable 0; unreadable_image 0
- built on: 2026-09-20

## Rebuild

    python build.py
