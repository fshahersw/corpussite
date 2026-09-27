# Federal directory supplement

This isolated supplement captures 15 pages selected from the U.S. Courts court-website directory and the Department of Justice resources page. It does not change the state or county corpus.

- `resources.jsonl`: importable union of saved page captures and useful observed directory links. Import by stable `id`; `record_type` and `capture_status` distinguish captured text from links whose targets were not fetched.
- `captures.jsonl`: 15 saved page records with text and hash-bound source artifacts. Filter these for a searchable content index.
- `observed_links.jsonl`: federal court destinations and legal references with exact source-link evidence. A target is downloaded only when `target_capture_id` identifies a saved capture.
- `resource.schema.json`: JSON Schema for the union records.
- `summary.json` and `validation.json`: scope, counts, source hashes, and verification results.

Paths are relative to the SCRAPE workspace. `sha256` is the hash of `raw_path`, while `text_sha256` is the hash of `text_path`. For 14 primary captures, `raw_path` is an archived provider-result JSON and the text is Tavily-extracted markdown; these are not original HTML or PDF bytes. All 15 provider responses are retained. DOJ resources additionally has original HTTP HTML and headers, saved after an explicit robots check; that original capture is the primary DOJ resource record because its links were partly omitted by provider extraction.

Observed-link records have null `raw_path`, `text_path`, and `sha256`. Their proof lives in `parent_text_path` with exact markdown character offsets, or in `parent_raw_path` with an original HTML anchor and metadata digest. `target_capture_id` is a convenience join, not a claim that all directory targets have been downloaded. Do not count observed links as saved documents.

`state_if_explicit` comes only from the source title or exact observed anchor. It does not assign federal judicial territory, counties, or state-court jurisdiction. Unknown values stay null. Federal defender links include destinations whose ownership was not independently reverified; federal authority identifies the official federal source and labeled role. The same destination can have different source labels and roles, which remain separate records.

Suggested API filters: `scope=federal`, `record_type`, `capture_status`, `resource_kind`, `state_if_explicit`, `parent_url`, and `capture_kind`. Preserve the original evidence fields when importing. Current legal effect and the provider's source fetch/cache time are not certified. No PACER or court account was accessed and no forms were submitted.
