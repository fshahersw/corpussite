"""Verify categorized reference overlays against their actual deployment targets."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from places_judges import ROOT, OUTPUT


def load_dataset(path):
    meta = json.loads(path.with_suffix('.dataset.json').read_text(encoding='utf8'))
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == meta['export_jsonl_sha256'], path.name
    rows = [json.loads(line) for line in raw.decode('utf8').splitlines()]
    assert len(rows) == meta['expected_records'], path.name
    assert len({row['id'] for row in rows}) == len(rows), path.name
    return rows, meta


def main():
    sources, source_meta = load_dataset(OUTPUT / 'sources.jsonl')
    mdls, mdl_meta = load_dataset(OUTPUT / 'mdls.jsonl')
    documents, document_meta = load_dataset(OUTPUT / 'generic/mdl_docket_documents.jsonl')
    document_ids = {row['id'] for row in documents}
    blocks = [row['detail']['docket_documents'] for row in mdls if row['detail'].get('docket_documents')]
    for block in blocks:
        assert block['total'] + block['excluded_uncategorized'] == block['source_snapshot_total']
        assert sum(block['by_doc_type'].values()) == block['total']
        assert not set(block['by_doc_type']) & {'other', 'unknown', 'uncategorized', 'other_unknown'}
        assert all(row['id'] in document_ids for row in block['latest_25'])
    section_labels = sum(bool(item.get('section_path')) for row in sources for item in row['detail'].get('doj_listings', []))
    assert section_labels > 0
    report = {'status': 'passed', 'sources': len(sources), 'mdls': len(mdls), 'categorized_mdl_documents': len(documents),
              'mdl_related_blocks': len(blocks), 'validated_document_links': sum(len(block['latest_25']) for block in blocks),
              'categorized_documents_in_mdl_blocks': sum(block['total'] for block in blocks),
              'excluded_uncategorized_from_mdl_blocks': sum(block['excluded_uncategorized'] for block in blocks),
              'preserved_doj_section_labels': section_labels,
              'sha256': {name: meta['export_jsonl_sha256'] for name, meta in
                  [('sources', source_meta), ('mdls', mdl_meta), ('mdl_docket_documents', document_meta)]}}
    path = ROOT / 'reports/release_county_data_20260927/reference_projection_validation.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report))


if __name__ == '__main__': main()
