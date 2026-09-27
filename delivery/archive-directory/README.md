# Local legal archive directory

The Source directory now maps 9,348 imported legal source references, including
16 API listings and 62 API/bulk references. Its first reviewed parallel capture
batch adds 32 saved, readable pages with exact source links and preserved
originals. Historical map claims, capture dates and actual saved content have
separate labels. Open `http://127.0.0.1:8769/#sources` or use the sidebar.

A second availability, **Saved in archive**, links 554 references to records that
earlier collections had already saved under exactly the same URL: 370 directory
records (published captures, imported Seeger originals, federal supplement), 204
retained court-page captures that never entered the published release, and 104 judge
profiles recorded from three judge-directory pages. These are links, not new downloads;
they carry the record's own saved date, publication status and review flags. See
`reports/source_archive_links_20260919/README.md`.

September19update: Laws now has state/category hubs and saved-text/file/link
filters. County pages separate overview, rules/forms and court access; a verified
historical registry enriches374KY/TXcounties with2,358court and628clerk entries.
Judge pages include deterministic Library insights and explicit source/saved dates.
The judge presentation contains10,698profiles and43portraits; the historical
biographies remain separate. Older counts below describe earlier checkpoints.

Open **http://127.0.0.1:8769/** while the local server is running.

Double-click `START.cmd` to start it again. The server binds only to your computer.
The archive files stay in the SCRAPE workspace; this folder is a directory over them,
not a duplicate copy or a standalone export. Keep it inside this workspace.

## What you can browse

Start from **Home**, then choose **Laws & rules**, **Judges** or **Counties**.
State, category and search filters stay in the URL so views can be bookmarked.
**Collections** opens selected material found in existing local libraries.
Detailed source management is under **Library details**.

Judge pages separate Overview, Career, Analysis and Documents. The default directory
shows 5,942 detailed profiles; choose All directory entries for all 10,669 conservative
identities. All 14 portraits in the known local asset pack have source-backed matches;
education, appointment and service facts resolved the nine previously held links.
Missing portraits use initials. Sources and technical evidence
are collapsed; historical service and analysis limitations remain available.

The four curated collections have clearly distinguished catalog and preview counts:
MDL-3080 (1,612 cataloged PDFs, 24 selected document previews), settlement references
(848 catalog records, four selected official PDF previews), court registries (269
entries) and court assets (295 unique images, only reviewed previews). These are
existing local source snapshots, not newly certified current directories. County
pages use only five explicit court-branding associations, never inferred seals.

The judge directory supports exact saved court affiliation, state, court system,
profile-content filters and sorting. Active filters can be removed individually.
County availability filters distinguish local resources, saved county information
and places without saved local resources. Counts include qualified pending downloads,
with their awaiting-publication counts shown separately; published totals stay distinct.

The two September 18 backfill passes added 50 official county PDFs (33 with native
text; 17 text gaps) and six previously missing Georgia Trellis profiles. The latest
pass contributed 24 PDFs and three profiles. These profiles are recorded
DOM fields/text, not original HTTP responses or case files. The known Trellis inventory
has 1,438 saved profiles among 2,508 observed state/DC profile URLs, a separate denominator
from the Census county inventory.

Thirty-seven display titles in the recent county backfills use exact publisher link
labels or readable filenames instead of URLs and scanner paths. Original PDF title
metadata remains in source details. Draft markers, revision dates and versions are
preserved; cleaned titles do not establish legal currency.

This MVP deliberately prioritizes usability over finishing nationwide acquisition.
Source documents remain read-only and their provenance remains accessible.

- Published state laws, rules, constitutions and supporting legal resources.
- All 3,144 county-equivalent inventory rows, with counts of actual saved material.
- County-associated profiles, sites, local rules, forms and other resources.
- Judge source observations, biographies, official evaluations and limited vendor analysis.
- A separate federal court / DOJ supplement, clearly distinguishing saved pages and links.
- Original or evidence files, extracted text, source URLs, capture metadata and dataset exports.

## September 18 enrichment and readable views

The default **Grouped records** view selects one representation of confirmed judge
identities and exact duplicate source content. Switch to **Source observations** to
inspect preserved source records. A filter can match any source in a group; the
displayed representative may come from a richer matching group member.

- **Open US Law**: Vaquill AI's pinned `v2026.08` snapshot (2026-08-14), with
  229 publisher-checksummed Parquets and 2,978,617 exported records. Its separate
  SQLite full-text index is published and validated, with its explicit
  `ready.json` marker. Counts are source records, not verified unique/current law
  sections. Georgia and North Carolina statute files were withdrawn by this
  publisher and remain absent from this supplement. Original official-source URLs,
  publisher status, versions and citations remain visible. Compilation attribution:
  Vaquill AI / open-us-law, CC BY 4.0; repository scripts are Apache 2.0.
- **Seeger collection**: 11,194 preserved originals and 7,166 structured provisions
  imported from the user-selected staging directory and the court library identified
  by its delivery receipts. Identical local raw files are reused. Source geography is
  retained; county jurisdiction is not inferred. There are 495 originals with some
  native-text completeness caveat in the frozen import, including 385 initially
  without native text. The validated offline recovery pass subsequently added text
  to 384 of those same records (382 DOC/RTF and two scanned PDFs), without duplicate
  identities. One 94 MB NJ RTF compilation remains unresolved; its 5,856 separately
  imported provisions are available. Partial-text and OCR accuracy caveats remain.
- **Consolidated judges**: 11,928 observations grouped into 10,669 conservative
  display identities, retaining 60,849 fact claims and 708 analysis records. These
  are not a certified count of unique currently serving judges. Possible/conflicting
  matches stay separate; differing dates and analysis outcomes stay source-bound.
- **Cleaned reading copies**: preserve legal headings, numbering, effective dates,
  repeated table values and exact provision derivatives while removing identified
  page controls. Source links, raw extraction, original files and technical metadata
  remain available. Cleaning does not establish legal currency or completeness.

The bulk quality report records 311,321 publisher rows without an original-source
URL. Those retain verified Parquet provenance; no source URL is invented. Historical
status labels are preserved. Quality and gap reports are available in dataset exports.

The bulk snapshot and imported collection are separately validated supplements;
the earlier focused-publication totals remain a distinct release. The overview
shows both. No originals are deleted to create a grouped or cleaned view.

Search uses the published full-text index plus the supplementary record text. Multiple
source observations may refer to the same judge. Provider responses and structured
evidence are labeled as saved evidence, and are not asserted to be original HTTP bytes.
Inventory coverage does not imply full document coverage, legal currency or verified
court jurisdiction. Source and quality notes remain available in every record.

## Refresh after publication

With the same installed Python used by the collectors:

```powershell
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' 'delivery/archive-directory/server.py' --build
```

This rebuilds only the directory metadata and keeps full legal text in the shared index.
Run after a validated package publication, not during a package replacement. Refresh
the browser afterward. Live queue counts update when Overview is refreshed; a queue
count does not establish that a collector process is running.

The reader can generate source-bound views on demand for new rows. Rebuild its full
cache with `scripts/build_reading_views.py --workers 2` only when a changed reader or
source snapshot warrants it; frontend-only changes and small additions do not require
an unchanged cache sweep. The reader requires `lxml`. Do not run multiple bulk
importers, full publication builds and large extraction jobs simultaneously on a
memory-limited computer. The main directory uses compact browse/filter tables so
listing pages do not repeatedly scan document bodies. Supplementary text and the
verified bulk snapshot have their own local indexes.

The local server is read-only. Downloads are restricted to indexed artifact IDs.
Raw HTML is downloaded as inert content, and harvested text is never executed.

## Files

- `index.html`, `app.js`, `styles.css`: local interface, no CDN dependencies.
- `server.py`: read-only API, full-text search and artifact serving.
- `directory.sqlite3`: derived browse/search metadata; safe to rebuild.
- `build_receipt.json`: latest local-directory build receipt.
- `test_directory.py`: verification of search, filters, evidence files and API isolation.

## September 19 corpus-upgrade modules

- `areas.js`: research areas registered on `window.ARCHIVE_AREAS` (MDLs, Regulations, Agencies, Coverage, State legal resources); `app.js` dispatches unknown views here and shows a status card until an area's data layer passes validation.
- `supplements.py` (`/api/supplements`): readiness of every `sources/*/validation.json`, re-verifying uniform envelopes' file hashes.
- `record_facets.py` (sidecar `sources/record_facets_20260919`): derived categories, review states, file types, jurisdiction level, typed dates, display titles and capture validity for all directory records; `query_documents`, `record_detail` and `explore` read it when published. New `/api/documents` params: `file_type`, `record_type`, `review`, `jur_level`, `subtype`, `dtype`, `validity` (`any` to include parked/shell captures), `date_type` + `dfrom`/`dto` + `undated`.
- `mdl_registry.py` (`/api/mdls`, `/api/mdl?number=`, `/api/mdls/summary`, `/api/mdls/for-judge`, `/api/mdls/for-person`, `/mdl-files/<id>`): JPML pending-MDL reports; judge profiles carry `mdls`.
- `federal_regulations.py` (`/api/regulations/*`, `/api/regulation?citation=`): CFR slice with GPO and publisher texts, eCFR history, Federal Register documents.
- `agency_safety.py` (`/api/agency/*`, `/agency-files/<id>`): openFDA, FDA.gov and local CPSC datasets.
- `jurisdiction_coverage.py` (`/api/coverage/*`): coverage matrix, official-tier labels, topic candidates, venues.
- `doj_resources.py` (`/api/resources*`, `/api/resource?id=`): DOJ JMD state legal resource map from saved captures.
- Sidebar links with `data-supplement` stay hidden until that supplement is ready.
