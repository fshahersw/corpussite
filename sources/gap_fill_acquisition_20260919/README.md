# State law / court gap-fill capture packet (September 19, 2026)

One bounded packet over the 99 reviewed exact URLs in
`reports/corpus_upgrade_20260919/understand/packets/gap_fill_candidates.jsonl` (30 states: regulation hubs,
statewide court rules, civil forms, jury instructions, and mass-tort coordination programme pages such as the
Philadelphia Complex Litigation Center, Texas MDL panel, West Virginia Mass Litigation Panel).
It is a set of saved representations of single URLs. It is not a rules, forms or regulations corpus, and it does
not establish that any captured rule, form, order or programme page is current, complete or in effect.

## Result of the one packet run (2026-09-19, about 10:57-10:59 UTC, 76 s)

| Outcome | URLs | Meaning |
| --- | --- | --- |
| Saved resources | 40 | 32 provider page captures + 8 original PDFs (incl. the CACI 2026 PDF, 11.3 MB), 18 states |
| Rejected after capture | 5 | 2 South Dakota title pages returned an "unsupported browser" script shell; WV MLP "Supreme Court Orders" rendered without its order list (325 characters); `nysenate.gov/legislation/laws/all` is a redirect alias of the saved `/laws` page; `in.gov/legislative/iac/` redirected to an unreviewed host |
| Blocked by robots policy (no provider request, no credit) | 28 | robots.txt disallow, 401/403 on robots.txt, HTML robots.txt or off-host robots redirect (TN, DC, LA, MI, NC, SC, IN, GA, FL, NY health, PA Code, OK, AZ, Cook County, legacy IL host) |
| Skipped barriers (no request) | 25 | historical 403/503 URLs and audit-listed barrier hosts (NJ, NY courts, MO courts, MA, CT, NH courts, AL, govt.westlaw.com) |
| Skipped for terms review (no request) | 1 | LexisNexis-hosted N.J.A.C. |

Credits: balance 3,715 before, 3,678 after = 37 credits for 37 admitted provider requests (32 kept, 5 rejected); cap was 100,
floor 500. PDFs cost no credits. Deduplication found 0 of the 99 URLs already saved (exact or loose) in any local store.
States with at least one saved resource (18): AR, CA, DE, FL, GA, ID, IL, KY, MI, MO, NH, NM, NV, NY, PA, SD, TX, WV.
States in the packet with nothing saved (12): AL, AZ, CT, DC, IN, LA, MA, NC, NJ, OK, SC, TN.
By family: mass-tort coordination 12 (Philadelphia CLC page + mass tort docket/liaison counsel PDF, Texas MDL panel 2 pages,
WV MLP page + 3 annual reports + trial court rules, FL 11th Circuit complex business 3), statutes 6, court rules 5,
jury instructions 5, regulations 5, venue/local 4, court forms 3. The reviewed packet contained no California JCCP URL,
and both New Jersey MCL URLs sit on a listed barrier host, so neither programme was captured.
Rejected captures keep their provider receipt and files on disk as evidence but are not resources. Exact counts are
in `validation.json`; tests: 11 here + the adapter's 22 pass, and `source_captures.batches()` reports this batch `loaded`.
Robots 401/403 observations pause the host in the shared corpus host gate (`corpus/_shared_hosts`) by design.

## What each file is

- `seeds.jsonl` - the frozen 99-row list (hash pinned in `prepare_receipt.json`): `state`, `gap_type`, `family`,
  `kind`, `doc_kind`, `source_directory_id`, reviewed `allowed_hosts`, and a `decision` with its reason
  (`fetch_firecrawl`, `fetch_http_document`, `skip_barrier`, `skip_terms_review`, `skip_already_saved`).
- `resources.jsonl` - same schema as `sources/public_law_acquisition_20260919/resources.jsonl`
  (`id` = `gapfill:<sha256(url)[:24]>`, `source_url`, `final_url`, `title`, `kind`, `captured_at`, `raw_path`/`raw_sha256`,
  `text_path`/`text_sha256`, `mime_type`, `capture_kind`, `source_as_of` = null) plus `state`, `gap_type`, `family`
  (`law_family` is the same value under the adapter's filter name), `doc_kind`, `packet`, `source_directory_id`,
  a `temporal` block (only `captured_at` is known; published/effective/as-of stay null) and receipt pointers.
- `gaps.json` - every candidate that is not a resource, with a category and reason.
- `validation.json` - carries BOTH the pilot gate (`status: passed`, `resources_sha256`) and the uniform envelope
  (`ready`, `data_files` hashes for `resources.jsonl`, `seeds.jsonl`, `gaps.json`, counts, checks, qualification).
- `capture_registry.json` + `capture_registry.validation.json` - the hash-gated batch registry that
  `delivery/archive-directory/source_captures.py` already reads from this folder (`REGISTRY`). The adapter was NOT edited.
  With the registry in place the batch loads next to the built-in pilot batch and captures attach to source-directory
  references by exact `source_url` (`attachments(url)`), with `state` / `gap_type` / `law_family` / `doc_kind` / `batch` filters.
- `.firecrawl/<request id>.json` - the complete provider response for every admitted request (receipt; key redacted
  defensively). `html/` = provider `rawHtml` bytes (the `raw_path` of a page record), `text/` = provider main-content Markdown.
- `http/` - direct official-host downloads for the PDF candidates (original bytes, native text extraction, per-request
  receipts under `http/metadata/`, robots observations under `http/controls/robots/`).
- `preflight.json`, `run_log.json`, `run_state.json`, `run-20260919.log` - credit preflight, measured credit delta, per-URL outcomes.
- `gapfill.py` (`prepare` -> `run` -> `build`), `test_gapfill.py`, `inspect_inputs.py`, `show_plan.py` (offline helpers).

## Two kinds of capture - do not conflate them

- Pages: `capture_kind = provider_capture_not_original_http_bytes`. The HTML and Markdown are what the Firecrawl
  `/v2/scrape` provider rendered and returned (basic proxy, no paid parsers, `maxAge: 0`), NOT the publisher's HTTP
  response bytes. `http_status` on these rows is the provider-reported source status (`http_status_basis`).
- PDFs: `capture_kind = official_http_original`. Downloaded directly from the official host with the shared corpus
  crawler (User-Agent `LegalCorpusResearch/1.0`); zero Firecrawl credits. HTTP `Last-Modified` is kept only as a header
  value and is never a publication or effective date.

## Rules that were applied

- Reuse by import only: `sources/county_litigation_firecrawl_20260919/collect.py` supplies the private DPAPI credential
  loader, credit client, the single global Firecrawl worker lock, the quality gate, and the shared host gate/robots
  policy. That folder was not edited. The key is held in memory only and is never printed or written.
- A process check found no active Firecrawl worker before the run; the global worker lock was held during the run.
- Credits: live balance checked first; request cap = min(100, balance - 500, page URLs); no retries; no purchases.
- Deduplication before spending: exact and loose (scheme/`www`/trailing-slash-insensitive) comparison against
  `directory.sqlite3` `records.source_url` and `browse.source_url`, `catalog/documents.sqlite3` `versions.source_url`
  and `final_url`, and every `sources/*/resources.jsonl` (`source_url`, `final_url`). Scan sizes are in `prepare_receipt.json`.
- Barriers were skipped WITHOUT any request and listed in `gaps.json`: every URL whose source-directory record has a
  historical HTTP 403/503, every host family the gap audit lists as a barrier (`state_county_gaps.md` section 7:
  courts.mo.gov, mass.gov, jud.ct.gov, njcourts.gov, nycourts.gov incl. ww2, courts.nh.gov, judicial.alabama.gov,
  alabamaadministrativecode.state.al.us, govt.westlaw.com), and the vendor-hosted N.J.A.C. page (terms review required).
  This is deliberately conservative: e.g. the NJ Multicounty Litigation page had a historical 200 but sits on a listed host.
- robots.txt is checked with our own User-Agent before any provider request, fail-closed: a disallow, a 401/403 on
  robots.txt, an HTML robots.txt, or an off-host robots redirect all mean no request and no credit. Firecrawl was not
  used to get around any of these.
- Quality gate on HTTP 200: provider-reported source status must be 2xx; final host must be the reviewed host
  (`www` variants; NC legacy host may land on ncleg.gov); soft 404 / error titles, challenge shells, login shells,
  unrendered script shells and bodies under 400 characters are rejected (again at build time, plus redirect aliases
  of an already saved URL). An observed access barrier pauses the host for the rest of the run.
- One packet, 5 workers, one request at a time per host with the shared >= 2 s host delay, <= 600 s admission window.

## Limits / not verified

- No captured page was read by a person; titles are the publisher's page titles as returned by the provider.
- A saved hub page does not include the rules, forms, PDFs or case lists it links to.
- Script-rendered sites (e.g. sdlegislature.gov) are saved as the provider rendered them; completeness of the rendered
  listing was not checked.
- The two West Virginia Mass Litigation Panel pages and the WV trial court rules page are not source-directory
  references yet (`source_directory_id` null), so they have nothing to attach to until the directory adds them.
- The Florida 11th Circuit items are complex business litigation, and the FL jury-instruction page is Florida Bar hosted;
  neither is a mass-tort docket.
- No third-party packages were installed.

## Re-running

`gapfill.py prepare` refuses to overwrite the frozen seeds. `gapfill.py run` never re-submits a URL that already has an
outcome in `run_state.json` (including robots blocks and rejections - no automatic retries) and counts earlier admitted
requests against the 100-credit task cap. `gapfill.py build` is offline and re-hashes every file.
Tests: `python -m unittest discover -s sources/gap_fill_acquisition_20260919 -p "test_*.py"`.
