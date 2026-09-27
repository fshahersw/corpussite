# Limitation periods

The Advottic US Statute of Limitations Dataset v2026.06 (Techno Optics LLC, CC BY 4.0), reproduced unchanged, plus a mechanical check of personal-injury, wrongful-death and medical-malpractice periods against the statute text saved in this library.

- Adapter: `delivery/archive-directory/limitation_periods.py`
- In the app: States & counties > State law > Limitation periods (`#limitation-periods`) and a table on every state page
- Gate: `validation.json` (status `passed`); the adapter closes if the data file no longer matches its recorded SHA-256.

## Rules

- Attribution (required): Techno Optics LLC. (2026). Advottic Legal Data. https://github.com/TechnoOptics/legal-data. CC BY 4.0.
- The check opens one named section per state and claim and reports whether the same period wording is there. It does not analyse tolling, discovery rules, repose or later amendments, and it is not legal advice.
- Where the saved statute states a different period the table value is shown as published and the statute sentence is shown beside it.

## Counts (from validation.json, 2026-09-20)

- jurisdictions: 51
- claim types: 9
- periods: 459
- sections named: 147
- library check: period_wording_found 121; same_period_general 18; section_not_saved 9; section_saved_no_period 1; different_wording_found 4
- table version: 2026.06
- table reviewed: 2026-06-08

## Rebuild

    python build.py
