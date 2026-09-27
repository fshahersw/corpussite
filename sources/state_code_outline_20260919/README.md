# Law outline (table of contents for the saved law snapshot)

Built 2026-09-19 from `sources/open_us_law_20260918/catalog.sqlite3` (2,978,617 provisions: statutes, constitutions, court
rules, regulations and agency guidance for 53 jurisdictions, each with its full text).

The catalog stores one row per provision and the publisher's own hierarchy in each row. `build.py` reads that hierarchy once and
writes `outline.sqlite3`: 196,529 headings (code or title, then chapter, article, part ...) and, for every heading, the ranges of
catalog row numbers filed directly under it. No text is copied. The adapter (`delivery/archive-directory/law_outline.py`) reads
headings from the outline and provision titles and text from the catalog.

What a reader gets: State law page, choose a state, open a code or title, then a chapter, then a section; in the reader, the
position of the section in its code and previous / next section.

## Rules

- Headings are the publisher's. Where a code is published only as an abbreviation, the heading uses the name in the
  publisher's own citation. California's 29 code abbreviations and 87 New York law identifiers are expanded to their official
  names; every other abbreviation is shown as published. `label_basis` records which rule produced each heading.
- Order inside a level follows the heading numbers (roman numerals read as numbers); where a level has no numbers, the
  publisher's stored order. Provisions under a heading are listed in citation order (publisher's order above 4,000 provisions).
- 26 provisions have no published hierarchy; they sit under one plain heading, "Other provisions".
- The outline is a finding aid, not an official table of contents, and says nothing about currency: check the edition and
  status of a provision at the official source.

## Gate

`validation.json` records the outline's SHA-256 and the size and modification time of the catalog that was scanned. The adapter
closes (answers `available: false`) if the outline hash differs, the gate is not `passed`, or the catalog is not that same file,
because the outline addresses provisions by catalog row number.

## Rebuild

    python build.py            # full scan, about five minutes
    python build.py --relabel  # recompute headings and order only

Licence: the snapshot is the Open US Law compilation (CC BY 4.0); original official-source addresses are kept on every provision.
