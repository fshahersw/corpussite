# Official county litigation resources

This is a bounded, resumable acquisition layer. It is not a completed county or
national litigation corpus. Discovery hints are separate from content
classification, and acquisition checkpoints require publication review.

The initial Orange County pilot saved 31 resources: 17 provider-rendered pages
and 14 original PDFs (255 pages processed with native text). It used 17 Firecrawl
credits, from 4,015 to 3,998, and retained one linked PDF HTTP404 as a gap. Exact
evidence and the immutable checkpoint pointer are in `pilot_report.json`.

## Storage and source fidelity

- `queue.sqlite3`: durable URL queue, county/source evidence, priority, requests,
  failures, and run history. In-flight/uncertain requests are not automatically
  resubmitted after a crash.
- `.firecrawl/`: saved provider response JSON. `html/` and `text/` contain returned
  HTML and readable Markdown. These are labeled provider captures, not original
  HTTP response bytes. Requested and provider-reported final URLs stay separate.
- `http/raw/`, `http/text/`, `http/metadata/`: original public document bytes,
  native extraction and exact access/extraction receipts. Images, OCR, current
  legal validity, and complete case access are not inferred from successful text.
- `resources.jsonl` / `summary.json`: live acquisition projections; mutable.
- `checkpoints/<UTC>/`: immutable, hash-validated resources, summary and receipt.
  `latest.json` is atomically replaced only after checkpoint validation. Consumers
  must use this checkpoint instead of racing the live projection.

Publisher link labels improve PDF display titles; original extracted metadata
titles and parent-capture hashes remain available. Capture time, source publication
time and legal effective time are independent; unknown source/effective dates
stay null. A retained citation to a rule does not establish that the resource is
itself a rule, or that it is currently effective.

## Collection bounds

The private DPAPI loader supplies authentication in memory only. No credentials
are written to artifacts or command lines. `/v2/scrape` uses basic mode, no client
retry, no paid document parser, a 60-second source timeout, and a fixed request
budget clamped to fresh live balance. There are no purchases or overages.

Five worker slots operate across hosts. The shared corpus host lock, robots
rules, crawl delays and durable access pauses apply to both provider requests
and direct document downloads. One request at a time runs on a given host.
Access challenges are retained as gaps and pause the host; no alternate mode or
proxy escalation is attempted.

Expansion admits literal captured anchors only, on the exact reviewed court
host, to depth two. Source authority must be verified; merely appearing on a
county-government page does not establish court authority. Per-county and total
frontier bounds stop uncontrolled growth. Priority and county round-robin favor
rules, orders, forms and multiple counties. News, events and community outreach
are excluded. Exact already-published source URLs and observed redirect aliases
are skipped; URL variants are not guessed.

## Running and reviewing

Run `test_collect.py` through unittest before changing collection policy. Ingest
county-agent JSONL packets with `collect.py ingest --seeds <path>`. Ingest is safe
while the worker runs; do not use offline `discover` or reclassify its records
concurrently with acquisition. One global Firecrawl worker lock prevents two
account workers from running independently.

The approved next finite run uses `run --request-cap 4000 --max-pages 6000
--max-seconds 3600 --workers 5 --auto-discover --max-frontier 6000 --per-county 250`.
Its actual process, log and fresh progress are recorded separately at launch.
Checkpoints are produced every 100 completed resources or five minutes, and on
normal exit. They are acquired data, not an assertion of application publication.

The independently owned classifier is
`sources/county_litigation_20260919/classify.py`; publication review and the local
directory remain separate from this collector.
