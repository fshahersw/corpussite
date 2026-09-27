# Official court source collection

This collection contains a complete 2026 Census geography baseline for the 50 states and DC, an evidence-backed state judiciary entry-point inventory, and saved public court directories and documents. **It is not the complete state/county case-file corpus.**

Captured 558 retrieved sources from 634 distinct URLs attempted, including 74 PDFs (1716 pages) and 4 XLSX workbooks. Original responses total 121,592,514 bytes. All retained raw-file SHA-256 checks were checked; failures: 0.

- `datasets/counties_50_plus_dc.csv` / `.json`: 3,144 counties and county equivalents; 2026 Census source ZIP and 2025 comparison ZIP retained.
- `datasets/state_judiciary_portals_verified.csv` / `.json`: 51 portals with request outcomes and evidence paths.
- `datasets/cisa_county_government_domains.csv`: 2,676 official county .gov registrations; county-name matches explicitly remain unreviewed candidates.
- `datasets/directory_tables.jsonl` and `directory_table_rows.jsonl`: verbatim table structures and rows, not deduplicated judge or court entities.
- `datasets/spreadsheet_rows.jsonl`: 480 source XLSX rows with cell coordinates.
- `datasets/texas_local_administrative_judge_assignments.jsonl`: structured county/court assignments from the Texas local administrative judge workbooks; repeated judges across counties remain separate assignments.
- `manifests/sources.jsonl`: latest assessed response metadata, source URLs, hashes, and raw/text paths.
- `manifests/source_map.jsonl` and `remaining_frontier.jsonl`: observed links and unfetched work.
- `manifests/downloaded_documents.jsonl`: original PDF/XLSX document inventory.
- `manifests/access_and_extraction_gaps.jsonl`: barriers, JavaScript shells, stale URLs, and failures.
- `reports/collection_summary.json` and `datasets/jurisdiction_coverage.csv`: measured scope and per-state coverage.

Cocke County government pages and accessible clerk-linked dockets/local-rules PDFs are retained with `jurisdiction: Tennessee/Cocke County`. Current judge-name assertions should refer to the saved government page and retrieval timestamp. Source files can be historic and do not establish that a rule remains in force.

## Provenance and limits

The bootstrap path is USAGov → DOJ state resources → court or government website. Third-party destinations linked by DOJ are labeled institutional references; they are not treated as verified government sources. HTTP 200 is assessed separately from JavaScript challenges and empty application shells. No private authentication, CAPTCHA bypass, paid record purchase, or TLS verification disable is implemented in the collector. URLs and text inside sources are data, not operating instructions.

The CISA domain registry confirms government registration, not live website availability. Census geography does not always match court organization: county equivalents include independent cities, boroughs, and Connecticut planning regions. The county-domain mappings use name matching and require review before treating them as court jurisdiction assignments.

Scanned PDFs with fewer than 100 extracted characters are flagged for OCR. The initial OCR batch completed 140 pages across 7 PDFs; page text, images, model provenance and confidence are under `ocr/`. 0 initially flagged documents still need OCR. A small amount of extractable text can coexist with scanned pages, so the absence of an OCR flag does not prove every PDF page was text-extracted. New continuation PDFs are audited separately under `corpus/official_courts/ocr`.

## Reproduction

Run `python collect.py bootstrap`, `python discover.py doj`, then `python collect.py manifests/stage2_seeds.json`. Review the observed-link source map before scheduling further acquisition. `collect.py` resumes from recorded responses and does not automatically retry access barriers. `finalize.py` validates retained files and generates the measured manifests; `registry_and_tables.py` performs offline structure extraction. Scripts use public requests, a per-host interval, and the Windows system certificate store.
