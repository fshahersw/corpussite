# Downloaded additions awaiting publication

`resources.jsonl` is a separate directory supplement, not part of the validated published corpus. Every entry has quality **Awaiting publication validation**. It selects downloaded captures from exactly four named collections whose `(collection, source_url, raw_sha256)` triple is absent from the frozen published `delivery/focused_legal_corpus/focused.sqlite3` snapshot. Published counts remain unchanged.

All paths are relative to the SCRAPE workspace. Original bytes stay in their collection. Raw, text, and metadata hashes and their source database resource/fetch rows are retained under `metadata`. Each capture-time jurisdiction and original seed context is retained with its provenance and association verification flags. County GEOIDs are source associations, not independently verified court territorial assignments. Ambiguous or missing state/county values stay null; nothing is inferred from URLs.

`resource_kind` is the discovery category, not a new semantic review. A downloaded PDF or HTML page is not a guarantee of current law, authentic case filings, exhaustive content, or usable extracted text. Empty or unavailable text is preserved honestly in `metadata.text_evidence`; originals remain available. The title uses saved capture title, then an unambiguous observed seed label, then the source URL.

Import by stable `id`; keep this supplement separately filterable by `quality`, `group`, `state`, `county_geoids`, and `resource_kind`. On the next full publication refresh, drop supplementary entries whose exact collection/URL/raw-hash triple has become published. Never add these counts to published-validation totals.

`build_pending.py` reads only the four source databases and published selection, verifies capture artifacts, and writes only this directory. `validate_pending.py` independently rechecks saved file hashes, capture bindings, literal context geography, exact published absence and current downloaded source rows. Neither script makes network requests or edits queues, collector configurations, or delivery artifacts.
