"""Validate the fixed ten-document WA OCR derivative pass without source mutations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = ROOT / 'corpus/county_local_rules_washington_20260914'
OCR = COLLECTION / 'ocr'
REPORT = ROOT / 'reports/counties/local_rules_washington_20260914/ocr'


def rows(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(path_value):
    path = Path(path_value).resolve()
    assert path.is_relative_to(ROOT), path_value
    return path


def rel(path):
    return local(path).relative_to(ROOT).as_posix()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def main():
    plan = json.loads((REPORT / 'run_plan.json').read_text(encoding='utf-8'))
    diagnosis = json.loads((REPORT / 'diagnosis_summary.json').read_text(encoding='utf-8'))
    assert digest(OCR / 'pages.jsonl') == plan['pages_manifest_sha256']
    assert digest(OCR / 'documents.jsonl') == plan['documents_manifest_sha256']
    assert digest(REPORT / 'selected_resource_snapshot.jsonl') == diagnosis['selected_snapshot_sha256']
    assert digest(OCR / 'models/eng.traineddata') == plan['model_sha256']
    selected = rows(REPORT / 'selected_resource_snapshot.jsonl')
    docs, pages, results, manifests = rows(OCR / 'documents.jsonl'), rows(OCR / 'pages.jsonl'), rows(OCR / 'page_results.jsonl'), rows(OCR / 'pdf_ocr_manifest.jsonl')
    assert len(docs) == len(selected) == len(manifests) == 10
    assert len(pages) == len(results) == 63
    expected_source_hashes = {r['sha256'] for r in selected}
    assert {d['source_pdf_sha256'] for d in docs} == expected_source_hashes
    key = lambda r: (r['source_id'], r['source_pdf_sha256'], r['page_number'], r['image_sha256'])
    assert len({key(p) for p in pages}) == 63
    result_map = {key(p): p for p in results}
    assert len(result_map) == 63 and set(result_map) == {key(p) for p in pages}
    checks = []
    for page in pages:
        result = result_map[key(page)]
        assert result['status'] == 'succeeded' and result['language_model_sha256'] == plan['model_sha256']
        assert digest(local(page['image_path'])) == page['image_sha256']
        textpath = local(result['text_path']); assert digest(textpath) == result['text_sha256']
        text = textpath.read_text(encoding='utf-8'); assert text.strip()
        tsv = local(result['tsv_path']); tsv.read_text(encoding='utf-8')
        checks.append({'source_id': page['source_id'], 'source_pdf_sha256': page['source_pdf_sha256'], 'page_number': page['page_number'], 'image_path': rel(page['image_path']), 'image_sha256': page['image_sha256'], 'text_path': rel(textpath), 'text_sha256': result['text_sha256'], 'tsv_path': rel(tsv), 'tsv_sha256': digest(tsv), 'confidence': result['confidence'], 'text_characters': len(text), 'status': 'hash_verified_ocr_derivative'})
    manifest_map = {d['source_id']: d for d in manifests}
    validated = []
    for doc in docs:
        assert digest(local(doc['source_pdf_path'])) == doc['source_pdf_sha256']
        assert digest(local(doc['embedded_text_path'])) == doc['embedded_text_sha256']
        coverage = rows(local(doc['page_coverage_path']))
        assert len(coverage) == doc['pdf_pages'] and {r['page_number'] for r in coverage} == set(range(1, doc['pdf_pages'] + 1))
        manifest = manifest_map[doc['source_id']]
        assert manifest['ocr_status'] == 'complete' and manifest['ocr_pages_completed'] == manifest['ocr_pages_required'] == doc['pdf_pages']
        combined_path = local(manifest['ocr_text_path'])
        assert digest(combined_path) == manifest['ocr_text_sha256']
        expected_parts = []
        doc_pages = sorted((r for r in checks if r['source_id'] == doc['source_id']), key=lambda r: r['page_number'])
        for page in doc_pages:
            text = (ROOT / page['text_path']).read_text(encoding='utf-8')
            expected_parts.append(f"===== OCR PAGE {page['page_number']} =====\n{text}")
        assert combined_path.read_text(encoding='utf-8') == '\n\n'.join(expected_parts)
        aliases = []
        for resource in doc['source_resources']:
            metapath = COLLECTION / resource['metadata_path']; meta = json.loads(metapath.read_text(encoding='utf-8'))
            assert meta['sha256'] == resource['sha256'] == doc['source_pdf_sha256'] and meta['fetch_id'] == resource['last_fetch_id'] and meta['requested_url'] == resource['url']
            if resource.get('text_path'):
                assert digest(COLLECTION / resource['text_path']) == meta['text_sha256']
            aliases.append({'collection': rel(COLLECTION), 'resource_id': resource['id'], 'url': resource['url'], 'source_metadata_path': rel(metapath), 'source_metadata_sha256': digest(metapath)})
        validated.append({'source_id': doc['source_id'], 'source_pdf_sha256': doc['source_pdf_sha256'], 'source_pdf_path': rel(doc['source_pdf_path']), 'source_resources': aliases, 'pdf_pages': doc['pdf_pages'], 'ocr_pages_completed': len(doc_pages), 'ocr_complete_for_this_pdf': True, 'ocr_text_path': rel(combined_path), 'ocr_text_sha256': manifest['ocr_text_sha256'], 'embedded_text_path': rel(doc['embedded_text_path']), 'embedded_text_sha256': doc['embedded_text_sha256'], 'page_coverage_path': rel(doc['page_coverage_path']), 'page_coverage_sha256': digest(local(doc['page_coverage_path'])), 'ocr_engine': manifest['ocr_engine'], 'language_model_sha256': plan['model_sha256'], 'min_page_confidence': min(r['confidence'] for r in doc_pages), 'text_correctness_certified': False, 'current_law_status_verified': False})
    (REPORT / 'verified_pages.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in checks), encoding='utf-8')
    (REPORT / 'verified_documents.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in validated), encoding='utf-8')
    visual_review = {'reviewed_at_utc': datetime.now(timezone.utc).isoformat(), 'sampled_pages': [
        {'source_pdf_sha256': '06650d76daa03e7a8d695edafe49943143d91c6c2591dc9d3802520714cf16f0', 'page_number': 1, 'observation': 'The image is a scanned Blaine Municipal Court rule index and preface. OCR captures its court title and explicit Whatcom County / Washington text. Some numbering punctuation is noisy; no county seed mapping was changed.'},
        {'source_pdf_sha256': 'bcaf628b46eff69bffb6446708849fa355e01e778d4203a43481032e77228183', 'page_number': 1, 'observation': 'The Garfield rule scan is legible, but OCR reading order places body material before the rule heading and introduces enumeration artifacts. Preserve image/TSV and require review before exact rule reconstruction.'}
    ], 'sample_size': 2, 'total_ocr_pages': 63, 'all_pages_visually_verified': False, 'interpretation': 'OCR produces searchable derivatives. Confidence and successful extraction do not certify legal wording, logical order, or current force.'}
    write(REPORT / 'visual_sample_review.json', visual_review)
    status = json.loads((OCR / 'status.json').read_text(encoding='utf-8'))
    receipt = {'validated': True, 'validated_at_utc': datetime.now(timezone.utc).isoformat(), 'fixed_input_documents': 10, 'fixed_input_pages': 63, 'verified_complete_documents': len(validated), 'verified_page_derivatives': len(checks), 'failures': 0, 'minimum_page_confidence': min(r['confidence'] for r in checks), 'ocr_elapsed_run_seconds': status['elapsed_run_seconds'], 'source_originals_and_collector_embedded_text_hashes_unchanged': True, 'collector_database_modified': False, 'collector_config_modified': False, 'network_requests': 0, 'new_arrivals_included': False, 'unprocessed_native_text_gap_documents_in_original_100_snapshot': 16, 'visual_sample_pages': 2, 'legal_wording_or_currency_certified': False, 'files': {rel(p): digest(p) for p in [OCR / 'documents.jsonl', OCR / 'pages.jsonl', OCR / 'page_results.jsonl', OCR / 'pdf_ocr_manifest.jsonl', REPORT / 'verified_documents.jsonl', REPORT / 'verified_pages.jsonl', REPORT / 'visual_sample_review.json']}}
    write(REPORT / 'validation.json', receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
