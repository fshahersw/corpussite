# Public Laws and their U.S. Code effects (2026-09-19)

One row per Public Law from GovInfo PLAW bulk XML (`plaw_bulk_metadata`), joined to its classified U.S. Code effects
from the promoted aggregated authority-edge graph (`plaw_uscode_semantics_v4/plaw_uscode_authority_edges_v4`) and
candidate temporal/effective notes from the promoted temporal graph (`plaw_temporal_graph_v5`).

## Inputs (read-only, staging, never modified)
- `plaw_bulk_metadata_2026-08-20.jsonl.gz` — law identity, citation, Statutes at Large cite, approved date, title, GovInfo URL.
- `plaw_uscode_semantics_v4_2026-08-20/plaw_uscode_authority_edges_v4_2026-08-20.jsonl.gz` — per (law, U.S. Code
  title+section) aggregated edge with `semantic_classification` (amends/adds/repeals/redesignates/transfers/
  appropriates/mixed_direct_actions/mixed_contextual_actions/reference_only). Every row in this input carries
  `semantic_evidence_status == occurrence_level_official_uslm_evidence` (official GovInfo bulk-XML evidence); the
  build still checks this per row and drops anything that is not, so a future non-official/candidate row can never
  become a fact here.
- `plaw_temporal_graph_v5_2026-08-20/plaw_uscode_temporal_target_edges_v5_2026-08-20.jsonl.gz` +
  `plaw_temporal_provision_entities_v5_2026-08-20.jsonl.gz` — candidate temporal/effective-date rules attached by
  `(plaw_package_id, reference_entity_key)`.

## What counts as a fact vs. a candidate
- `usc_effects.is_action=1` rows (amends/adds/repeals/redesignates/transfers/appropriates/mixed_direct_actions) are
  U.S. Code changes as classified by the official GovInfo bulk-XML semantic classifier.
- `usc_effects.is_action=0` rows (`reference_only`, `mixed_contextual_actions`) are citations to a U.S. Code section
  that are not themselves a classified code change — shown, but never presented as an amendment.
- `temporal_notes` rows are candidate deadlines/effective rules with a source confidence label
  (`high`/`medium`/`low`); they are never authoritative effective-date determinations.
- No Tavily/Firecrawl candidate/discovery rows are inputs to this build; the promoted graphs used here are already
  restricted to official-evidence rows.

## Build
`python build.py` — streams the four jsonl.gz inputs, writes `public_law_uscode.sqlite3` (tables `laws`,
`usc_effects`, `temporal_notes`, FTS5 `laws_fts`), and regenerates `validation.json` with measured counts, input
hashes, and the output DB hash. Deterministic and re-runnable; no network.

## Tests
`python -m unittest discover -s . -p "test_build.py"` — unit tests for the PLAW-id parser and the action-type
classification rules against real sample rows.

## Adapter
`delivery/archive-directory/public_laws.py` (+ `test_public_laws.py`), modeled on `federal_register_history.py`:
`listing(params)` with filters `q` (FTS over titles/citations, common stop words ignored), `congress`, `year`,
`usc_title`, `action`; `detail(id)`; `for_usc(title, section)` returning a compact (<=25 row) block of Public Laws
touching that section. Fail-closed: any missing/failed validation or hash mismatch returns the "not available" shape
and never raises.
