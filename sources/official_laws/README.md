# Official state legal sources and downloaded documents

This directory maps 50 U.S. states and the District of Columbia across statutes,
constitutions, and court rules. The District of Columbia has a Home Rule Act
entry instead of a state constitution. The source map contains 153 category
entries. It is **not a complete national legal corpus**.

The collector saves raw source HTML and original document bytes, extracted text,
SHA-256 checksums, response status, retrieval time, discovered links, and the
source that exposed each link. Public legislative, judicial, and government
pages are the source authorities. Linked public publisher portals are identified
as such. Search results and rendered page evidence are retained in `discovery/`.

## Start here

- `indexes/source_index.json` and `.csv`: one entry for each jurisdiction/category,
  including current access status and local evidence paths.
- `indexes/jurisdiction_coverage.json` and `.csv`: discovered document URLs,
  attempted URLs, downloaded files, failures, pending documents, pages, and bytes
  by jurisdiction/category. The expected size of the full corpus is **unknown**;
  `discovery_complete` remains false.
- `indexes/document_manifest.json` and `.csv`: downloaded and attempted document
  records. Updated after every 25 completed queue items and when the loop ends.
- `indexes/document_candidates.json`: the enumerated document queue.
- `indexes/discovered_links.json` and `.csv`: all links observed on captured source
  pages, including links outside the current download selection.
- `indexes/active_run.json`: process ID, command, directory, and current logs.
- `indexes/paused_hosts.json`: persisted access-barrier pauses.
- `indexes/historical_tls_audit.json` and `.csv`: prior responses needing transport
  revalidation after a historical truststore concurrency warning.
- `indexes/collection_summary.json`: snapshot updated when a stage/report ends.
  During a live run, use the document manifest and jurisdiction coverage files.

## Resume

From this directory:

```powershell
python collect_official_laws.py documents
```

Check `indexes/active_run.json` and the process list first so there is only one
collector. A completed URL is reused from `metadata/`; resume does not repeat
its request. Six workers rotate among states, with at least one second between
request starts to a host. Each state may expose many more legal pages than the
document queue currently enumerates.

`python collect_official_laws.py seeds` regenerates the state index and link
inventory from cached captures and any newly added source entries. Curated
candidate seeds are separated from links observed in source pages/search
evidence. Corrected URLs are in `source_overrides.json`, and observed bulk
download pages are in `supplemental_sources.json`. `enrich_sources.py` normalizes
existing HTML links and merges separately saved rendered page evidence; it does
not render blocked sites or fetch credentials.

```powershell
python -c "import collect_official_laws as c; c.write_coverage(); c.report()"
```

## Limits and remaining work

The document queue consists of observed linked PDF/archive/Word candidates from
the verified entry pages and selected bulk-download pages. Its exhaustion is
not proof that every statute section, rule version, judicial order, local rule,
case, county, historical record, or attachment has been enumerated. Some entry
pages are directories, JavaScript shells, stale URLs, or public publisher links.
HTTP success means the response was saved; it does not certify legal currency,
publication authenticity, or whether the response contains an entire code.

401, 403, 429, and detected browser-validation barriers pause further requests
to that host, with the pause retained across restarts. The collector uses public
GET requests and does not use authentication, paywall bypasses, CAPTCHA bypasses,
proxy rotation, or paid purchases. Existing response failures are preserved.

Historical concurrent downloads emitted unverified-TLS warnings on seven hosts.
An audit conservatively flags 63 old responses (54 PDFs) whose SHA-256 should be
compared with a new, isolated, certificate-verified GET before treating their
transport as verified. Original files are preserved; this recovery did not
blanket-download the corpus again. New downloads use separate Windows-trust SSL
contexts per request session with `verify=True`; warnings remain enabled.

## Layout

`raw/` holds HTML and error-response evidence; `documents/` holds original files;
`text/` holds HTML/PDF text and rendered markdown; `metadata/` holds per-URL JSON;
`indexes/` holds manifests and coverage summaries; `discovery/` retains search
and rendering provenance. Paths inside manifests are relative to this directory.
