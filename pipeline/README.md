# Resumable legal corpus crawler

This tool archives explicitly scoped **public HTTP** resources. It saves exact response bytes, extracts HTML/text/PDF content, records links and redirects, and maintains an auditable SQLite queue. It does not import browser cookies, use private account credentials, execute page JavaScript, or acquire subscription entitlements. Authenticated browser downloads can be stored alongside its output by the operator's separate workflow.

Python 3.10+ is required. Networking, SQLite, HTML extraction, and CSV/JSON export use the standard library. PDF text extraction uses `pypdf` when installed; without it, original PDF bytes are retained and `parser_unavailable` is reported. Scanned PDFs report `no_text_ocr_needed` when no text can be extracted; this engine does not run OCR. TLS verification uses Python's default SSL context, including Windows system certificate roots, with no `certifi` dependency or verification bypass.

## Run from the SCRAPE workspace

Use a separate corpus output directory. The examples below are PowerShell commands, executed individually:

```powershell
python pipeline/corpus_crawler.py --root corpus/public_http ingest --seeds pipeline/seeds.example.jsonl --config pipeline/config.example.json
python pipeline/corpus_crawler.py --root corpus/public_http run --max-pages 100 --max-seconds 600
python pipeline/corpus_crawler.py --root corpus/public_http status
python pipeline/corpus_crawler.py --root corpus/public_http export
```

The example seeds repeat the user-provided starting points; they are not claims that the pages are currently accessible. Replace or extend the config with the exact official hosts and path prefixes found during source mapping. The public HTTP engine will checkpoint a Trellis access block instead of repeatedly hammering the site.

Resume with the same `run` command. Successfully downloaded URLs are not downloaded again. Additional JSONL seed files can be ingested between runs. Identical URLs are merged, and new source/jurisdiction contexts remain separately traceable. If a new context attaches to a page already downloaded, its stored links are expanded without another HTTP request. Changing the run config, including increasing `max_depth` after a pilot, re-evaluates stored links and scope-excluded seeds before continuing; it does not refetch downloaded parents.

For a long foreground run, `--max-pages 0 --max-seconds 0 --wait-for-retries` processes the available frontier until it is exhausted or every remaining host is paused. Zero disables that specific budget. Default runs stop after 100 resource attempts or 600 seconds; budgets include resource visits, while separate robots requests are recorded under `controls`. Only already-running requests drain after a time limit or Ctrl+C. Network reads have timeouts, and individual transfers have a time limit.

```powershell
python pipeline/corpus_crawler.py --root corpus/public_http run --max-pages 0 --max-seconds 0 --wait-for-retries
```

There is no hidden scheduler or detached process. Another operator can read `status` or `export` during a run. Concurrent mutation commands for the same corpus are prevented by an OS file lock, which is automatically released after a crash; interrupted `fetching` rows are recovered to `pending` on the next run.

## Seeds and scope

Each nonblank line in the seed file is a JSON object:

```json
{"url":"https://example.gov/courts/rules","source_family":"official_court","jurisdiction":{"country":"US","state":"Example"},"category":"court_rules","discovered_from":"https://example.gov/","scope":{"host":"example.gov","path_prefixes":["/courts"]}}
```

Only `url` is required. The other first-class fields preserve source provenance; any additional fields are retained in the original seed metadata. `jurisdiction` can be a string or object. `scope` is optional and can narrow the global host/path allowlist; it cannot expand it. Each seed URL starts at depth 0. Ordinary links add one level; redirects do not add depth. Different contexts on the same URL have separate depth and provenance records.

Config `allow` rules are exact hosts, optionally with a port, plus boundary-aware path prefixes. `example.gov` does not include `www.example.gov`; `/rules` includes `/rules/1` but does not include `/rules-old`. Decoded dot segments are checked against the scope. Cross-host hyperlinks are recorded only by default; explicitly ingest external official URLs as seeds. The optional `follow_external_allowed_links` switch permits cross-host traversal only when the destination is also explicitly allowlisted and within the seed scope. Redirect targets face the same allowlist, except the cross-host hyperlink switch does not apply to redirects. All redirects are recorded; out-of-scope destinations are never fetched.

URLs preserve query parameter order and escaping to avoid breaking legal search and signed document URLs. Fragments are removed. Only HTTP(S) GET requests are made. Authentication, logout, signup, upgrade, checkout, payment, edit/delete/upload and similar action paths are excluded. Common image/script/font/media assets are recorded as links but not fetched. This excludes obvious action URLs, not a substitute for choosing appropriate read-only site sections in the allowlist.

## Storage and evidence

| Location | Contents |
| --- | --- |
| `corpus.sqlite3` | Queue, all source contexts, fetch history, link graph, host pauses, run history and operator events |
| `config.json` | Latest supplied config; every run also preserves its own config snapshot in SQLite |
| `raw/aa/SHA256.ext` | Complete response bodies, including evidence of access failures; content-addressed and deduplicated |
| `raw_partial/aa/SHA256.ext` | Truncated/oversized responses, explicitly excluded from complete payload counts |
| `text/aa/SHA256.v1.0.0.txt` | UTF-8 extracted text; parser limits and errors are reported explicitly |
| `metadata/aa/FETCH_ID.json` | Per-attempt response metadata, checksums, extraction outcome, source contexts and discovered links |
| `controls/robots/` | Robots decisions and original response references |
| `checkpoint.json` | Latest finished run summary and stop reason |
| `reports/` | Snapshot JSONL for every table, `resources.csv`, and `summary.json` |

CSV values are protected from spreadsheet formula evaluation. JSONL preserves the original strings. Sensitive response headers such as `Set-Cookie` are omitted from archived metadata. Requests carry neither cookies nor authorization headers. Raw response bodies are retained as received, including a compressed body if a server applies content encoding; gzip can be decoded for extraction while preserving the original bytes.

`resources` holds the latest outcome for each canonical URL. `fetches` preserves every attempt and its metadata even after an explicit retry. The `contexts` and `resource_contexts` tables link a downloaded resource to every observed seed/jurisdiction/category. `links` retains destinations even when excluded from traversal, with a per-context decision explaining why they were or were not queued. `duplicate_of` identifies another resource with the same complete response bytes. A completed payload can still be a forbidden/login/challenge page; count successful corpus resources using `status = 'downloaded'`, not merely `raw_complete = 1`.

## Access blocks, retries, and limits

Robots directives are honored by default, including crawl delay. A disallowed resource is marked `robots_disallowed`. Unavailable/invalid robots responses are recorded and retried within the configured limits. Normal requests use a per-host delay (default 2 seconds), at most 8 workers (default 3), a 64 MiB response cap, and a 2 GiB free-space floor. These are operator-configurable; the crawler stops with `disk_guard` before crossing the free-space floor and leaves work resumable. HTML, PDF or text extraction is capped at `max_extract_chars` (default 20 Mi characters) and reports `text_truncated` instead of silently claiming a complete extraction.

Version 1.0.1 dispatches at most one resource per exact host at a time, rotates among eligible hosts, and keeps pacing waits in the scheduler. A host with a long crawl delay or server cooldown does not hold a worker while unrelated hosts have eligible work. Robots redirects and the transition from robots to the page also yield during pacing. These scheduling deferrals do not create fetch-history rows, increment attempts, or consume page/retry budgets. Normal pacing waits remain runnable without `--wait-for-retries`; the flag still controls future error retries and server cooldowns.

`controls/host_pacing.json` atomically records request-start deadlines before HTTP begins and extends them when robots introduces a larger delay. Restarts restore those deadlines alongside existing SQLite host pauses and cooldowns. For an archive created by version 1.0.0, which did not save pacing, the crawler reads its archived robots responses and waits one full known interval from startup for each legacy host. For example, saved `www.azleg.gov` robots directives require 120 seconds; the upgrade does not reduce that interval. Robots policy is then checked afresh through the normal paced request flow. The pacing control file contains no credentials.

To deploy onto an existing run, let it checkpoint or have its operator stop only the verified worker, wait until its process and corpus lock are released, and restart the same corpus root/config with version 1.0.1. Interrupted `fetching` rows recover to pending automatically; no manual SQLite edit or frontier rebuild is required. For a forced stop of the old version, retain at least the known host delay from termination (120 seconds for `www.azleg.gov`); the legacy startup guard is additionally conservative. Do not run old and new workers concurrently against one root. Workers already running keep their loaded version until their next process start.

## Shared hosts across collections (version 1.0.2)

Generic crawler processes from this workspace now share `corpus/_shared_hosts/hosts.sqlite3` and exact-host OS locks. The default applies when `shared_host_dir` is missing or null. Set an absolute path, or a path relative to this workspace, to select another shared directory; every cooperating collection must use the same directory. An empty string disables coordination for an intentionally isolated fixture. Production collections with overlapping hosts must keep coordination enabled. Firecrawl, authenticated browser operations, and old generic workers do not participate in this coordinator.

Shared policy includes the maximum observed robots/local delay, request-start deadlines, server cooldowns and configured access blocks. A request keeps its OS lock through body handling and response classification, so a second process cannot enter before a 403, challenge, or Retry-After is recorded. Host-only waits yield to the scheduler and leave unrelated hosts runnable, including when a process has only one worker. Scheduling collisions do not create fetch rows or consume attempt/page/retry budgets. Each collection still stores its own responses, robots observations, provenance, SQLite frontier, and checkpoint. Its `controls/shared_host_coordinator.json` records the coordinator location, and `status` includes relevant shared host controls.

There is no expiring lease that can steal a live request. OS locks release when their owning process exits. If durable ownership metadata remains after a crash, the replacement must first acquire the OS lock, preserve all saved pacing/cooldowns/blocks, and add a full known host interval before requesting. This bounds recovery by the known interval and any longer existing restriction, rather than waiting forever for a stale PID. A still-running or hung owner remains exclusive until its operator stops it; PID reuse cannot grant ownership.

Before migrating overlapping collections, checkpoint or stop every pre-1.0.2 generic worker that may contact those hosts. Confirm each old process and collection lock has released; for forced stops preserve at least the full known host delay from termination. Then merge saved controls from **all** existing generic collection roots, including completed collections with access restrictions, before starting new workers. The command reads source collection SQLite in read-only mode and writes only the shared coordinator:

```powershell
python pipeline/corpus_crawler.py --root corpus/official_law_pages shared-host-import --collection-root corpus/official_law_pages --collection-root corpus/official_courts --collection-root corpus/county_sites
```

Repeat `--collection-root` for any additional existing collections. Imported legacy robots controls conservatively apply a full startup interval if no durable pacing checkpoint exists. Every new run also merges its own saved controls before HTTP begins. After import, restart the same collection roots with their existing `run` arguments; the CLI and per-collection SQLite schema are unchanged. A newly imported recovery queue can then run against the same coordinator. Starting a new version while an overlapping old process still runs does not provide cross-process exclusion.

An explicitly resolved access restriction can be cleared with `shared-host-unblock --host example.gov --reason "..."` using a root configured for that coordinator. Local collection restrictions remain separate: clear the applicable local `unblock-host` controls first, then the shared control, so a restart does not re-import an old local block. These actions retain pacing deadlines and record the reason; they do not automatically retry failed resources. Do not clear a restriction merely to retry an unchanged barrier.

HTTP 401, 403, recognizable login pages, access-upgrade redirects and anti-bot challenge pages are terminal outcomes for that request and pause further work on that host by default. No CAPTCHA, paywall, entitlement restriction or authentication barrier is bypassed. HTTP 429 and transient transport/server failures use bounded backoff. The server's `Retry-After` is never shortened to the local maximum-backoff setting. Unless `--wait-for-retries` is set, runs checkpoint and exit when only future retries/paused hosts remain.

An operator can explicitly retry a known failure after resolving its cause. Host pauses and URL retry state are separate controls. These commands record the change and reason in the event log:

```powershell
python pipeline/corpus_crawler.py --root corpus/public_http retry --status network_error --status http_error
python pipeline/corpus_crawler.py --root corpus/public_http unblock-host --host example.gov --reason "Source administrator restored public access"
python pipeline/corpus_crawler.py --root corpus/public_http retry --status forbidden --host example.gov
python pipeline/corpus_crawler.py --root corpus/public_http run --max-pages 100 --max-seconds 600
```

Do not use these controls to cycle around an unchanged access restriction. A separate authenticated browser/export/API workflow must supply records that this public engine cannot access. Purchased content and new subscriptions are not performed by this tool.

## Reading completion accurately

`frontier_exhausted` means no pending/deferred/in-progress URL remains. It can be true when failures or exclusions remain. `all_enqueued_resources_downloaded` also requires no error/exclusion outcomes and every redirect chain to resolve to a downloaded/no-content destination. `full_source_corpus_complete` is always `false`: discovery alone cannot establish completeness against all case IDs, filings, court jurisdictions, paginated searches, dynamically loaded results, or records behind authentication. National-corpus completion requires external inventories and reconciliation against them.

The link graph keeps excluded URLs, but they are generally not placed in the download frontier. Depth caps and disabled traversal can therefore exhaust a bounded frontier while undiscovered documents remain. Parser failures preserve the raw original and appear in `extraction_statuses`; a downloaded PDF is not necessarily fully text-extracted. Redirect cycles, out-of-scope redirects, HTTP errors, login/entitlement blocks, and source gaps must be reviewed explicitly.

## Verification

The tests start an isolated local HTTP server and create temporary artifacts beneath `pipeline/tests/_tmp`. They do not call live public websites:

```powershell
python -m unittest discover -s pipeline/tests -v
```

Fixtures verify queue resume, interrupted claim recovery, idempotent seed ingestion, source contexts, graph expansion, redirects, binary PDF preservation and extraction, content deduplication, credential-header omission, scoped traversal, robots exclusion, forbidden/login/challenge outcomes, rate-limit backoff, per-host delay under parallel workers, partial response accounting, disk guard and snapshot exports. Scheduling fixtures verify slow robots hosts yield with one or three workers, one active request per host, fair dispatch when responses outlast the local delay, unrelated-host progress during Retry-After, robots redirect continuation without duplicate requests, and durable/legacy pacing restoration.

`test_shared_hosts.py` starts independent Python processes against isolated loopback servers and a temporary shared coordinator. It verifies exclusivity beyond a pacing interval, unrelated-host progress while another process owns the host, shared robots delays, 403/challenge/Retry-After propagation without false attempts, process-death recovery preserving restrictions, and offline policy import without changing source collection rows. It never initializes or touches the production shared coordinator.
