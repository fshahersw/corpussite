# CorpusSite

A public legal research library with state laws and rules, county resources, court information, judge profiles, federal regulations, agency records and linked originals.

**Cloud migration is in progress. This repository is not yet a completed hosted release.** The local archive remains usable. Supabase imports are stopped until the approved 64 GB database disk is available; its disk-change cooldown was still active on September 27, 2026. No compute upgrade is authorized. Scheduled scraping remains paused.

## Run and deploy

- Existing archive on this computer: `delivery/archive-directory/START.cmd`, then http://127.0.0.1:8769/. Python can be selected with `CORPUS_PYTHON`.
- Hosted application: Vercel static UI and Node API, Supabase Postgres, private Supabase Storage. Read [the deployment and migration guide](deploy/README.md) before deployment.
- Frontend build: `npm ci`, `npm test`, `npm run build` (Node 22 or newer).
- A new checkout contains code, not the large corpus. The legacy local restore process is documented in [TRANSFER.md](TRANSFER.md); the hosted runtime reads categorized Supabase data after validation and publication.

Set `CORPUS_SUPABASE_URL` and `CORPUS_SUPABASE_SECRET_KEY` only in server-side configuration. See [.env.example](.env.example). Browsing, read-only API requests and available downloads require no login. `CORPUS_SITE_PASSWORD` is no longer used. Never put the Supabase server secret in browser code.

## Data and release boundaries

The migration retains classified records, original source relationships, exact court identities, capture/publication/effective dates, review qualifications and explicit missing content. Uncategorized records are excluded. Overlapping source observations remain distinct where merging would lose evidence; record counts are not counts of unique current laws or judges.

Local exports include 2,968,623 Open US Law provisions, 58,289 primary records, 1,910,592 records across 32 additional collections, and 259,865 federal/agency records, plus county/judge/reference/navigation datasets. These are export counts, **not completed cloud import counts**. Court-library integration maps 270 directory entries and 11,180 document associations. Category-filtered resources and source dates remain inspectable in the reader.

Originals and full text stay outside Git. Source files are retained locally, and hosted downloads use a private bucket with expiring links. Large cleaned-text downloads use Storage rather than truncating content to fit an API response.

Release publication requires reconciled hashes/counts, original download checks, working filters/readers/portraits and access-control checks. Until then the cloud health endpoint reports `ready: false` and unvalidated datasets remain unavailable. This archive does not claim complete nationwide coverage or uniform legal currency.

The dated source-additions layer provides separate readers and evidence connections at `#additions` and `#connections`, with contextual links from state, county and MDL pages. It preserves original documents, extracted sections, exact identifiers and date distinctions. Reader totals include extracted sections and are reported separately from distinct source-file totals. Rescission notices and unresolved legal currency remain explicit.

`scripts/build_gap_enrichment_20260927.py` prepares the local layer with its publication gate closed. Independent source/hash validation is required before local publication. `deploy/exporters/enrichment.py` produces a separate, pinned Supabase delta without changing the base migration plan. Its small listing index and bounded entity graph contexts must be imported and accepted along with originals before cloud publication; deploying code alone does not publish the data.

## Code layout

| Directory | Purpose |
|---|---|
| `delivery/archive-directory/` | UI and existing local Python API |
| `api/`, `middleware.js`, `deploy/*-api.mjs` | Hosted routing and native Supabase-backed endpoints |
| `supabase/migrations/` | Schema, search, filter and navigation functions |
| `deploy/exporters/`, `deploy/import_*.py` | Categorized export and resumable migration tools |
| `sources/`, `pipeline/`, `scripts/` | Collection, normalization and validation code |

Imports use a shared writer lock and keep publication gates closed. The local migration plan and transfer receipts live in `_transfer_scratch/supabase_export/` and `reports/supabase_migration_20260927/`; they are not part of the public repository. Do not start duplicate importers or resume collection to deploy existing data.
