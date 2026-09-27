# Native Supabase / Vercel deployment

**Migration is incomplete. Production release is held.** The hosted application uses Vercel server routes, Supabase Postgres/RPCs and private Supabase Storage. It no longer requires a hosted Python/SQLite server. The existing localhost application remains the comparison baseline.

## Checkpoint: 2026-09-27 21:06 UTC

- All import writers are intentionally stopped. Keep scheduled collection paused.
- The user authorized **64 GB of database disk only**, estimated at **approximately $7/month additional**. No compute upgrade or larger disk change is authorized.
- The dashboard still showed **8 GB allocated, 2.09 GB used**, with **t3.micro / 1 GB compute**. The 64 GB change was **not performed**.
- Disk editing was disabled by the rolling 24-hour, four-change limit. The displayed cooldown was **3 hours 51 minutes** at 21:06 UTC. This is a historical observation, not a guarantee of when the control will become available.
- Logs confirmed disk-full/read-only failures during the earlier parallel import, plus eight-second PostgREST timeouts. Logical database size or a healthy status alone does not establish physical disk headroom.

When the dashboard permits the already-authorized change, apply **64 GB only** and verify that capacity is effective before resuming. Do not upgrade compute, increase the authorized ceiling, disable read-only protection, or restart writers just because time has elapsed. Recheck disk, processes and receipts. Do not create an automatic resume task.

Local evidence and resumable files are under reports/supabase_migration_20260927/ and _transfer_scratch/supabase_export/. These private data/checkpoint directories are not part of the Git/Vercel payload.

## Runtime architecture

| Component | Responsibility |
|---|---|
| delivery/archive-directory/ → dist/ | Browser UI and static visual assets |
| middleware.js, api/archive.js | Private-site authentication and routing of existing archive URLs |
| deploy/cloud-api.mjs and domain adapters | Pages, filters, readers, navigation and download contracts |
| deploy/cloud-context.mjs | Server-only Supabase access, readiness gates, context assembly and signed assets |
| supabase/migrations/ | Postgres schema, search/filter RPCs, outline/group tables and access controls |
| deploy/exporters/ | Category-selected, source-bound exports from the local archive |
| deploy/import_*.py | Explicit operator-run staging imports and local progress receipts |

The target is project **xosqzzsnhxcyehcnirpa**, at https://xosqzzsnhxcyehcnirpa.supabase.co. The client is deliberately pinned to this project; changing the URL is not a supported project switch.

The core export contains **3,026,912 categorized records**: 58,289 main records and 2,968,623 Open US Law records. County, judge, court and reference layers have additional manifests. These are **local export counts, not completed cloud counts**. Unmapped/unknown records are excluded; source distinctions, dates, review flags and original references are retained. Do not rebuild the large local database or restart collection to deploy these checkpoints.

### Private originals and complete text

Originals, portraits and derivatives use the private **corpus-originals** bucket. The corpus_artifacts table maps existing same-origin routes to content-addressed objects. The server checks readiness and issues a short-lived signed URL. Neither the Supabase secret nor a permanent public object URL goes to the browser.

The ordinary reader shows a bounded preview. Exact full cleaned text above **3,145,728 UTF-8 bytes** is exported separately so large /api/text?id=... downloads can redirect to Storage instead of crossing a Vercel response-size limit. The current text-assets manifest contains **155 files / 233 record-and-group route aliases**, totaling 814,322,556 bytes. Its largest file is 82,070,039 bytes; verify that the bucket file-size setting permits it before uploading this phase. These are cleaned-reader derivatives; original publisher files remain separate.

### Readiness and large contexts

Imports stage datasets and context rows with **ready=false**. Large metadata/context values go through deploy/context_transfer.py: values over approximately 400 KB are split into 120,000-character pieces, with a parent manifest and SHA-256. The runtime reads only ready context rows and verifies the assembled hash. Every required piece and its parent must pass validation before publication. A dataset whose required context cannot be read remains unavailable.

Do not post a large context JSON object directly or mark all context rows ready in bulk. Keep the law outline gated together with its matching Open US Law rows and source row IDs. A successful build or /api/health response alone does not prove complete context, artifact or feature migration.

## Environment and credentials

Use the repository-root .env.example as a variable reference. Set these as **server-side Vercel environment variables** for each intended environment:

| Variable | Purpose |
|---|---|
| CORPUS_SUPABASE_URL | Pinned project origin above |
| CORPUS_SUPABASE_SECRET_KEY | Server secret with migration/runtime access; never a browser variable |
| CORPUS_SITE_PASSWORD | Separate private-library password of at least 16 characters |

Hosted login uses username **reader** and the site password. Authentication is checked by middleware and by the archive API. Anonymous database/RPC and Storage access is not the runtime access model.

Never commit credentials, private .env files, .auth, transfer data or private receipts. Never prefix secrets with NEXT_PUBLIC_ or VITE_. Python import tools read CORPUS_SUPABASE_SECRET_KEY from their process environment or use the existing Windows user-bound DPAPI credential. They do not automatically load .env.example or .env. Configure Vercel through its environment settings; the Windows credential cannot be copied there.

Legacy CORPUS_BACKEND_ORIGIN / CORPUS_BACKEND_TOKEN proxy variables are not used by the native hosted path.

## Safe operator resume

These are instructions for a deliberate future resume. **Do not run write commands while the migration remains on hold.**

1. Inspect active import processes, locks and the latest local/remote receipts. Confirm there is no other writer. Preserve JSONL exports, descriptors, hashes and SQLite progress journals.
2. Recheck actual disk allocation, free space and read-only/error state. Apply and verify only the authorized 64 GB change when available. Confirm corpus/index/WAL headroom within that allocation.
3. Review schema migrations and export validation. Run `python deploy/migration_plan.py --verify` to validate the exact export hashes/counts and write the local migration plan; this does not start imports or activate data. Run local checks below. Do not reset the database or recreate tables destructively. For very large transfers, a reviewed client-side COPY FROM STDIN path through a direct/session PostgreSQL connection is preferable; the current PostgREST scripts do not implement that transport.
4. Resume **one catalog import process, one worker** initially. Select one reviewed JSONL and its matching descriptor; omit --activate while staging. Example:

   ~~~powershell
   python deploy/import_catalog.py _transfer_scratch/supabase_export/core/open_us_law.jsonl --workers 1 --batch-rows 25 --batch-bytes 256000
   ~~~

   The importer verifies source count/hash and exact remote count, and splits an atomic batch after a statement timeout. One oversized row cannot be fixed by splitting a batch: inspect and handle that record explicitly. Different batch dimensions use a different checkpoint key and replay idempotent upserts instead of reusing incompatible offsets; this can cause substantial extra writes. Preserve recorded settings when they were already reliable.

5. After the core records are imported, run `python deploy/import_groups.py` to import the 45,638 reviewed display groups. It uses migration_plan.json's group hash/count, batches of at most 200 rows / 256 KB, and a shared single-writer lock. Preserve groups_progress.sqlite3. It verifies the exact remote count and local source hash, and does not change readiness. If no migration plan is available, an explicitly reviewed --receipt JSON must pin path, table, rows and sha256; no unpinned import is allowed. Then import context files with deploy/import_context.py so the chunking helper is used. Use deploy/import_law_outline.py --workers 1 for the dedicated outline, without --activate. Follow manifest dependencies. Keep Storage uploads, broad updates, index builds and schema reloads out of the catalog-loading phase.
6. Run Storage separately at low concurrency with a reviewed manifest. Example:

   ~~~powershell
   python deploy/import_artifacts.py _transfer_scratch/supabase_export/text_assets/large_text_assets.jsonl --workers 1
   ~~~

   Preserve artifact_progress.sqlite3. Hashes deduplicate objects; route aliases are registered separately. The script's byte-transfer ceiling is a local bound, not spending authorization. Keep the bucket private. An upload receipt can still require independent remote readback checks.

7. On disk-full/read-only errors, stop and reassess capacity. The client raises CapacityError; do not turn this into endless retries. On repeated timeouts or schema-cache errors, investigate before raising concurrency. Do not force readiness to make pages look complete.

Journals do not replace remote count/hash checks. Do not delete remote rows or checkpoints to conceal a mismatch. No scraping, scheduled collection, compute upgrade or further purchase is part of these commands.

## Acceptance before publication

- Required dataset counts match reviewed export descriptors and hashes; every excluded/held layer is identified.
- Context pieces and parent manifests are complete, hash-verified and deliberately published in dependency order. Required dataset and outline readiness gates agree.
- Original, portrait and full-text routes resolve to verified private objects. Check signed downloads, a large text file and its preferred-group alias.
- Compare hosted lists and filters to localhost: states/counties, county resource types, laws/rules categories, judge portraits/details, source grouping, date/review filters, related records, and outline next/previous links.
- Confirm readable text, source/as-of dates, explicit unavailable states, authentication, direct API protection and no secret/private-path leakage.
- Only then may the reviewed publication step activate datasets and their contexts. --activate does not replace artifact/context checks.

## Vercel release setup — held until acceptance

Import **fshahersw/corpussite** with repository root, framework **Other**, install **npm ci**, build **npm run build**, output **dist**, and Node 22 or newer as required by package.json. Configure the three server variables above. The build copies UI assets; SQLite and corpus data are not bundled into Functions.

Run from repository root:

~~~powershell
npm ci
npm test
npm run build
python -m unittest discover -s deploy -p test_import_review.py
python -m unittest discover -s deploy -p test_import_groups.py
python -m unittest discover -s deploy/exporters -p test_large_text_assets.py
node delivery/archive-directory/test_ui_release.cjs
~~~

After migration gates pass, test the actual Vercel deployment: login, Montana, a county rules/forms reader, a judge portrait, law navigation, a private original and a large full-text download. Local tests cannot establish live Vercel/Supabase acceptance. Until then, report migration and release as incomplete.

Platform guidance: [bulk imports](https://supabase.com/docs/guides/database/import-data), [timeouts](https://supabase.com/docs/guides/database/postgres/timeouts), and [database versus disk size](https://supabase.com/docs/guides/platform/database-size).
