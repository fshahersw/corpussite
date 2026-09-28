# Native Supabase / Vercel deployment

**The authorized one-time migration is in progress. Publication gates remain held; this is not a completed-release claim.** Scheduled collection stays paused. The hosted application uses Vercel server routes, Supabase Postgres/RPCs and private Storage; it does not require a hosted Python/SQLite server. Localhost remains the feature-comparison baseline.

## Capacity and frozen release

The database disk has been increased to **64 GB**, with its maximum capped at **64 GB**. Micro compute is unchanged. Additional Storage spending is authorized up to **$1/month**, with a **34 GiB cumulative transfer ceiling** for this run. These limits do not authorize a compute upgrade, another disk increase, an overage or a new purchase. Inspect actual usage and the active writer before any resume; a byte ceiling is not a billing guarantee.

The frozen release inventory is:

| Required component | Expected count |
|---|---:|
| Categorized datasets | 69 |
| Catalog records | 5,268,216 |
| Final context rows, including required chunks | 44,611 |
| Display groups | 45,638 |
| Law collections / outline nodes / source segments | 224 / 196,458 / 818,617 |
| Artifact routes | 103,805 |
| Distinct private objects | 99,622 |

These are expected release counts, not a statement that every item is uploaded or verified. The base plan and approved enrichment delta, descriptors, JSONL files, acceptance manifests and progress journals are immutable inputs. Unmapped records are excluded; source distinctions, dates, review flags and original references remain intact. Do not rebuild the main local database or restart scraping for this deployment.

## Runtime architecture

| Component | Responsibility |
|---|---|
| `delivery/archive-directory/` → `dist/` | Browser UI and static visual assets |
| `middleware.js`, `api/archive.js` | Public read-only access and existing URL routing |
| `deploy/cloud-api.mjs` and domain adapters | Lists, filters, readers, navigation and downloads |
| `deploy/cloud-context.mjs` | Server-only Supabase access, readiness, context assembly and signed assets |
| `supabase/migrations/` | Schema, search/filter RPCs, groups, outline and access controls |
| `deploy/exporters/`, `deploy/import_*.py` | Operator-run exports, staging imports and resumable receipts |

Runtime and importer clients are pinned to project **xosqzzsnhxcyehcnirpa**, at `https://xosqzzsnhxcyehcnirpa.supabase.co`. Replacing an environment URL is not a supported project migration. Data exports, credentials and operator receipts are excluded from Git and Vercel builds.

### Readers, search and private assets

Originals, portraits and cleaned-text derivatives use the private `corpus-originals` bucket. `corpus_artifacts` maps same-origin routes to content-addressed objects. The server verifies readiness and returns short-lived signed URLs. The browser never receives the server secret or permanent public object access.

Readers use bounded previews. The reviewed large-text export contains 155 exact cleaned-text files with 233 record/group aliases; complete downloads redirect to Storage. The importer verifies the full file and source hash before storing a preview. Missing unpublished full-text assets return an explicit unavailable response, never a preview presented as a complete download. Originals remain separate from cleaned derivatives.

Search normally indexes the title plus the first one million text characters. The bounded-search migration preserves all existing vectors/index entries and all source text. New writes try the prior expression first; only the exact PostgreSQL tsvector-size error shortens the searchable prefix until it fits. Exceptionally large records can therefore have less searchable text. Other errors propagate. Changing the helper later does not automatically reindex previously stored rows.

Large context values are chunked with a parent manifest and SHA-256. The runtime assembles only published pieces and checks their hash. Outline publication also requires matching publisher records, scope, hierarchy and source row IDs. A successful build or health response alone does not establish these dependencies.

## Environment and credentials

Set these **server-side Vercel environment variables**, using `.env.example` as a reference:

| Variable | Purpose |
|---|---|
| `CORPUS_SUPABASE_URL` | Pinned project origin |
| `CORPUS_SUPABASE_SECRET_KEY` | Server-only Supabase secret |

The site and its read-only API are public and require no login. `CORPUS_SITE_PASSWORD` is unused. Table/RPC permissions remain server-only, and Storage remains private; the application exposes only published records and signed downloads. Legacy `CORPUS_BACKEND_ORIGIN` / `CORPUS_BACKEND_TOKEN` are unused.

Never commit secrets, private environment files, credential stores, corpus exports or transfer receipts. Never expose secrets through `NEXT_PUBLIC_*`, `VITE_*` or frontend bundles. Local Python tools use a private process environment or the existing Windows user-bound DPAPI wrapper; they do not load `.env` automatically. Configure Vercel secrets separately.

## Safe operator resume

1. Inspect current processes, driver/shared-writer locks, receipts and remote health. **Do not launch a second writer or another continuation while the one-time pipeline is active.** Preserve every export and checkpoint. Do not delete locks to force a resume.
2. Verify existing plan, descriptor and data hashes/counts without rewriting them. **Do not run `migration_plan.py --verify` during resume:** that command generates/writes a plan and can invalidate the frozen pins. Use the reviewed read-only verifier/driver against the existing plans instead.
3. Use one importer process at a time and one catalog/outline worker initially. Preserve the existing batch dimensions: ordinary catalogs use **200 rows / 1,500,000 raw characters**; `open_us_law` uses **1,000 rows / 4,000,000 raw characters**. The CLI option is named `--batch-bytes`, but its checkpoint boundary counts raw JSONL characters. Do not substitute smaller values: that creates different checkpoint keys and unnecessary replay. Resume every unacknowledged batch, not just those after the largest offset.
4. Preserve the verified large-text transformation and its hash-bound per-batch journal. Successful old receipts cannot skip required transformed batches. The bounded-search migration addresses the confirmed tsvector limit without changing source/export text, batch dimensions or role timeouts. Stop and diagnose other errors; do not truncate sources or hide failed rows.
5. After catalog records, import reviewed display groups, then law outline and ordered context manifests with their existing importers. Context order preserves deliberate overrides. Use `--workers 1` for the outline; its standalone default is higher. Keep all dataset/context/outline gates false while staging. Do not pass `--activate` to individual importers.
6. Run the artifact phase separately in the exact reviewed manifest order. Use the shared object journal and **`--max-total-gib 34`** on every artifact invocation. The reviewed driver permits at most four artifact workers; begin with one unless measured concurrency has been approved. The ceiling is cumulative across manifests, not a fresh allowance per file. Preserve resumable upload journals; large objects use TUS, while hashes deduplicate shared bytes. Artifact routes remain `ready=false` after upload.
7. On capacity/read-only errors, stop and check actual disk usage. Do not change billing limits or disable protection. On repeated errors, use sanitized exception type/HTTP/SQLSTATE evidence rather than looping. Retain failed checkpoints for an explicitly reviewed restart; there is no automatic retry schedule.

Individual catalog commands, with placeholders replaced by the already-reviewed local inputs, retain these settings:

```text
python deploy/import_catalog.py <ordinary-jsonl> --metadata <descriptor> --workers 1 --batch-rows 200 --batch-bytes 1500000
python deploy/import_catalog.py <open-us-law-jsonl> --metadata <descriptor> --workers 1 --batch-rows 1000 --batch-bytes 4000000
python deploy/import_groups.py --batch-rows 200
python deploy/import_law_outline.py --workers 1
python deploy/import_context.py <next-ordered-context-jsonl>
python deploy/import_artifacts.py <next-reviewed-artifact-manifest> --workers 1 --max-total-gib 34
```

Do not start these individually alongside the existing driver. Journals make interrupted transfers resumable; they do not replace fresh remote verification.

Applied migration versions can differ from local CLI filenames because MCP assigns remote versions. For example, the bounded-search migration is local `20260928021538` / remote `20260928021933`, and the large-text preview migration is local `20260928010951` / remote `20260928011434`; older migrations also differ. **Do not blindly run `supabase db push` or reapply by filename to this existing project.** Reconcile applied history by migration name and reviewed SQL content first. Do not rename files or rewrite history as part of an ordinary import resume. Vercel builds/deployments do not apply database DDL.

## Acceptance and two-step publication

First verify the exact 69-dataset catalog counts and manifest pins; all final context payloads, hashes and chunk ordering; full group membership/source-count semantics; and outline hierarchy, scope and publisher-row references. Artifact verification checks every required route and exact object key/size against a fresh private Storage inventory, plus complete-byte SHA-256 for the reviewed stratified samples. Metadata registration is not a claim that every object body was downloaded and hashed.

Only after those proofs pass may the scoped staging step activate the exact validated artifact routes, chunks before parents, outline gates and datasets. No blanket table-wide readiness updates are allowed. The final `publication:release` gate remains closed during this step.

Next run fresh staged checks and real hosted API/browser acceptance: state/county navigation, rules/forms readers, judge portraits/details, laws/outline next/previous links, grouped/source records, dates, unavailable states and signed original/large-text downloads. Confirm public browsing, read-only methods, and no credentials/private paths in responses. **Write the exact validated `publication:release` inventory last**, after this acceptance. Import completion or `--activate` alone never constitutes release acceptance.

## Vercel and local checks

For `fshahersw/corpussite`, use repository root, framework **Other**, install **`npm ci`**, build **`npm run build`**, output **`dist`**, and Node22 or newer. `vercel.json` pins `npm ci`; `.vercelignore` keeps Python/local corpus inputs out of Functions. Do not install Python build tools to work around a deployment using an obsolete install configuration. Configure the two server environment variables above.

```text
npm ci
npm test
npm run build
python -m unittest discover -s deploy -p test_import_review.py
python -m unittest discover -s deploy -p test_import_groups.py
python -m unittest discover -s deploy -p test_large_text_import.py
python -m unittest discover -s deploy -p test_artifact_import.py
node delivery/archive-directory/test_ui_release.cjs
```

The portable PostgreSQL regression fixture is `supabase/tests/corpus_search_vector.sql`. Run it with `psql --set=ON_ERROR_STOP=1 --file=supabase/tests/corpus_search_vector.sql` against an isolated migrated test database; it needs no pgTAP or application dependency and rolls back its temporary synthetic rows. It exercises real tsvector overflow, ordinary/multilingual parity, source/heap/index preservation, trigger updates and server-only grants.

Local checks do not establish live Vercel/Supabase acceptance. Until the separate publication proofs and final gate pass, report the migration and release as incomplete.

## Resuming large catalog imports

Keep a single import driver and the shared writer lock. Preserve the frozen source hash and original batch boundaries when resuming. Successful subdivisions of a timed-out batch are now committed to a separate local journal with project, source and payload hashes; a later failure does not replay those acknowledged subdivisions. Sanitized events distinguish upsert, checkpoint and resume stages without including source text or credentials. Exact remote counts and final source hashes remain required before a completion receipt is written.

The September 28 database repair lowers only the search GIN index's pending-list limit to 512 KiB. Its accumulated cleanup had taken 10.036 seconds, exceeding the existing eight-second API timeout. The migration preserves search semantics and the existing timeout; it does not rebuild or remove the index. Bounded Federal Register probes each verified 10,000 new rows: one worker averaged 0.70 seconds per batch, and two workers measured approximately 42,700 rows/minute during writes with no timeouts. These samples do not establish the throughput of larger law records or an end-to-end completion estimate. Use two workers for this measured collection and benchmark other collections separately before increasing concurrency.
