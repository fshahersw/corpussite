# Statute snapshot audit

The publisher's own completeness statement for each jurisdiction's statutes (complete / partial / withdrawn, container counts, official source, notes), copied from coverage.yml in the Open US Law repository and joined to the saved row counts.

- Adapter: `delivery/archive-directory/law_outline.py (field `statute_audit`)`
- In the app: the header of every state's law browser
- Gate: `validation.json` (status `passed`); the adapter closes if the data file no longer matches its recorded SHA-256.

## Rules

- Not re-audited here. Georgia and North Carolina statutes were withdrawn from the snapshot by the publisher; Pennsylvania's unconsolidated statutes are missing.

## Counts (from validation.json, 2026-09-20)

- jurisdictions: 53
- complete: 50
- partial: 1
- withdrawn: 2

## Rebuild

    python build.py
