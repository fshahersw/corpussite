"""Write lightweight source-discovery pointers; never import external collections."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
D = Path('C:/Users/firas/Downloads')


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def pointer(relative, kind, usefulness, limitations, counts=None):
    path = D / relative
    result = {'path': path.as_posix(), 'bytes': path.stat().st_size, 'kind': kind,
              'integration_suggestion': usefulness, 'limitations': limitations,
              'counts_from_manifest_not_new_acquisition': counts or {}, 'external_original_modified': False}
    if path.stat().st_size <= 4_000_000:
        result['metadata_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


resources = [
    pointer('Court-Document-Library/05-Court-and-Judge-Assets/judge-assets.json', 'curated_judge_portrait_manifest',
        'Use portrait_links.jsonl for the five evidence-supported entity matches; keep nine unmatched images separate.',
        ['All 14 portraits are one federal district, not national coverage.', 'Source rights/current-service caveats retained.'], {'portraits': 14}),
    pointer('Court-Document-Library/05-Court-and-Judge-Assets/court-assets.json', 'curated_court_identity_registry',
        'Use court_links.jsonl to attach an official mark only to an explicitly matched court or judiciary.',
        ['Statewide branding does not identify every local county court.', 'Three prior source exclusions remain excluded.'], {'court_entries': 269, 'entries_with_primary_image': 238}),
    pointer('Court-Library-Expansion-2026-09-12/states/registry.json', 'observed_state_court_source_registry',
        'Offer a compact official forms/rules source directory, preserving per-link page observations and boundaries.',
        ['56 statewide/DC/territory entries, not 56 US states.', 'Observed directories do not prove all county forms captured.'], {'entries': 56}),
    pointer('Court-Library-Expansion-2026-09-12/local/registry.json', 'observed_local_court_source_registry',
        'Add court source links for the six explicit local court identities; no inferred county identity.',
        ['Selected local courts only; not a national county inventory.'], {'entries': 6}),
    pointer('Court-Library-Expansion-2026-09-12/federal/registry.json', 'observed_federal_court_source_registry',
        'Add explicitly labeled official court homepage/form links, with federal jurisdiction kept distinct.',
        ['207 jurisdiction/special entries; shared websites do not mean distinct physical courts.'], {'entries': 207}),
    pointer('Court-Document-Library/07-Settlement-References/catalog/catalog.json', 'curated_settlement_reference_catalog',
        'Consider a separate settlement/reference collection after its row-level scope and flags are reviewed.',
        ['Catalog has 848 records; this scan did not verify every source body or outcome.', 'Not a judge win-rate or prediction dataset.'], {'catalog_records': 848}),
    pointer('LAWONTOLOGY/deliverables/corpus-inventory/inventory-summary.json', 'mdl_3080_inventory_summary',
        'Expose a local MDL-3080 matter collection with transparent docket coverage.',
        ['Complete for supplied local directory, not all PACER docket items.', '922 observed docket entries plus 127 unknown entry-presence assessments.'], {'documents': 1612, 'pages': 21357, 'observed_entries': 922}),
    pointer('LAWONTOLOGY/deliverables/corpus-native-text/documents.jsonl', 'mdl_3080_native_text_document_manifest',
        'Index by source SHA/document/docket entry; join exact page record IDs to the existing native text.',
        ['Manifest inspected; original PDFs and every page were not rehashed in this bounded discovery.'], {'documents': 1612}),
    pointer('LAWONTOLOGY/deliverables/corpus-native-text/pages.jsonl', 'mdl_3080_native_page_text',
        'Potential source-grounded document reading and scoped matter search without re-extracting PDFs.',
        ['Source-manifest provenance retained; no import performed here.', 'Native text can omit scanned content.'], {'pages': 21357, 'characters_from_manifest': 40705908}),
    pointer('LAWONTOLOGY/deliverables/corpus-legal-graph/manifest.json', 'mdl_3080_evidence_graph_manifest',
        'Useful later for evidence-linked citations, filing types, explicitly stated outcomes and unresolved references.',
        ['Rule-based grounded candidates, not adjudicated legal truth.', 'Filename-derived and native-text-derived assertions must remain distinct.', 'No identity adjudication or current-law certification.'], {'semantic_nodes': 93894, 'semantic_edges': 104354}),
    pointer('LAWONTOLOGY/deliverables/corpus-legal-graph/graph.sqlite', 'mdl_3080_local_evidence_graph_database',
        'A later read-only adapter can expose evidence-linked queries rather than copying the graph wholesale.',
        ['Database not opened or rehashed during discovery.', 'Not a general judicial analytics product.']),
    pointer('seeger-insight-hub-main/seeger-insight-hub-main/src/routes/roster.tsx', 'mvp_ui_reference',
        'Reusable case/counsel roster layout ideas; data comes from named Supabase views.',
        ['This file is application code, not an exported judge/person corpus.', 'No database connection, credentials, or remote reads were used.']),
]
with (OUT / 'collection_candidates.jsonl').open('w', encoding='utf-8') as f:
    for resource in resources: f.write(json.dumps(resource, ensure_ascii=False) + '\n')

scans = [
    ('Court-Document-Library', 273, 'Curated assets identified; law/document originals already represented in Seeger import.'),
    ('Court-Library-Cleanup-2026-09-12', 15, 'Catalog tooling and preview screenshots; no additional identity-linked portrait manifest found.'),
    ('Court-Library-Deep-Enrichment-2026-09-12', 48, 'Prior legal-reference work and application/toolkit assets; no additional portrait corpus found.'),
    ('Court-Library-Enrichment-2026-09-12', 12, 'Prior Vaquill/settlement work and previews; keep duplicate references distinct from new acquisitions.'),
    ('Court-Library-Expansion-2026-09-12', 315, 'Original court/judge asset collection and explicit court registries; accepted curated pack chosen over rejected candidates.'),
    ('Seeger-Corpus-Enrichment-2026-09-13', 66, 'Previously reviewed staging/platform/agent materials; screenshots are not new judge photographs.'),
    ('Document-Library-Publish-2026-09-13', 0, 'Publication receipts and transfer tooling; no image corpus. Authentication/configuration files not read.'),
    ('legal-intelligence-retrieval', 0, 'Retrieval project/config scaffold; no local portrait/data export found in scoped patterns. Configuration not read.'),
    ('LAWONTOLOGY', 0, 'Useful MDL-3080 PDFs, native text, inventory and evidence graph; no image asset corpus.'),
    ('SEG-DATA', 0, 'Directory exists but has no nonhidden entries in the top-level inventory.'),
    ('seeger-insight-hub-main', 1, 'Application source and favicon; roster queries reference case/counsel views rather than local judge records.'),
]
write('folder_inventory.json', {'created_at': datetime.now(timezone.utc).isoformat(),
    'method': 'Scoped rg filename patterns, then bounded parsing of useful manifests; default ignore rules retained.',
    'excluded': ['node_modules', '.git', '.next', 'cache', 'caches', '.cache', '.venv', 'venv', '.worktrees', 'secrets/credentials/environment configuration'],
    'folders': [{'path': (D / name).as_posix(), 'raster_filename_matches': images, 'finding': finding} for name, images, finding in scans],
    'additional_roots': [{'path': str(Path('C:/Users/firas') / name), 'exists': (Path('C:/Users/firas') / name).exists(),
        'promising_top_level_name_matches': []} for name in ['Documents', 'Desktop']],
    'boundaries': ['Not an unrestricted drive scan.', 'Raster filename counts exclude SVG and are not portrait counts.',
        'No credentials, browser history, network, original modifications, or whole-library copies.',
        '14 official portraits decoded as WEBP; identity determined from source metadata, never image appearance.']})

print(json.dumps({'collection_pointers': len(resources), 'folder_roots': len(scans)}))
