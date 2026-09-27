# Cited authorities

Every full citation eyecite finds in the text of four saved-document layers (agency and science documents, downloaded source files, U.S. Courts publications, saved web pages), grouped into one row per authority with the documents that cite it and a passage from each.

- Adapter: `delivery/archive-directory/citation_index.py`
- In the app: Federal law & agencies > Law & regulations > Cited authorities (`#citation-index`), "Cited in N saved documents" under a law provision, "Authorities cited" inside saved documents
- Gate: `validation.json` (status `passed`); the adapter closes if the data file no longer matches its recorded SHA-256.

## Rules

- Counts are saved documents citing the authority, never importance or validity. Short forms (Id., supra) are not counted.
- U.S.C. and C.F.R. citations open the saved law text only on an exact citation match (a cited range opens its first section and says so); Federal Register citations open a saved entry only when the cited page is its first page; public laws by number or Statutes at Large page. Case citations carry a CourtListener look-up address; no case text is saved.
- A reporter-shaped string outside the unambiguous reporters is counted as a case only when a case name stands beside it ("on Day 23" in a lab report is not a Connecticut case).
- `python build.py scan` is resumable; `python build.py` rebuilds the index from the scan without rescanning.

## Counts (from validation.json, 2026-09-20)

- authorities: 60,591
- case: 50,724
- law: 9,354
- journal: 513
- mentions rows: 106,519
- law citations opening saved text: 3,064
- citations opening a federal register document: 555
- citations opening a saved public law: 49
- case shaped strings not counted: 9,074
- documents scanned: 24,289
- documents with citations: 4,860
- documents failed: 0
- layers: agency-documents {"documents": 3101, "scanned": 3101, "with_citations": 463}; source-documents {"documents": 740, "scanned": 740, "with_citations": 291}; uscourts {"documents": 1401, "scanned": 1401, "with_citations": 217}; saved-pages {"documents": 19047, "scanned": 19047, "with_citations": 3889}

## Rebuild

    python build.py
