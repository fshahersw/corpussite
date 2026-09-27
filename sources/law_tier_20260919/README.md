# law_tier_20260919 - official law tier classification and jurisdiction coverage layer

**Status: BUILD IN PROGRESS (resumed 2026-09-19).** `validation.json` says `status: building, ready: false`
until `build.py finalize` has run and every check passed. The adapter `delivery/archive-directory/jurisdiction_coverage.py`
fails closed until then. This README is rewritten at the end of the build.

Offline, read-only build (every SQLite database opened with `mode=ro`, no network, no server restart).

## Planned outputs (this folder)

| File | Content |
|---|---|
| `record_labels.jsonl` | one structural label per unreviewed focused official law record (`needs_content_review`, `law_document_title_evidence_needs_review`) |
| `oul_rule_sets.jsonl` | compact rule-set mapping keyed by the Open US Law stable row id (no text copied) |
| `topics.json` / `topic_index.jsonl` | mass-tort topic catalogue and citation-anchored candidate provisions per state |
| `coverage.json` | per-jurisdiction counts by family and source tier, explicit gaps, venues, Trellis unsaved profiles |
| `edges.jsonl` / `unresolved.jsonl` | provision -> state and provision -> topic relationships; unresolved joins |
| `integration_spec.json` | adapter signatures, proposed routes, UI placement |

Nothing here asserts legal currency or completeness.
