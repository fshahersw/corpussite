"""Diagnose saved WA PDFs and prepare a finite offline OCR derivative queue."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import prepare_court_ocr as ocr

COLLECTION = ROOT / 'corpus/county_local_rules_washington_20260914'
OUTPUT = COLLECTION / 'ocr'
REPORT = ROOT / 'reports/counties/local_rules_washington_20260914/ocr'
MAX_DOCUMENTS, MAX_PDF_PAGES = 10, 80


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def main():
    if (OUTPUT / 'documents.jsonl').exists():
        raise RuntimeError('OCR queue already prepared; preserve it and use its recorded resume command.')
    REPORT.mkdir(parents=True, exist_ok=True)
    dbpath = COLLECTION / 'corpus.sqlite3'
    with closing(sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        resources = [dict(r) for r in db.execute("SELECT id,url,status,raw_path,text_path,metadata_path,sha256,byte_count,last_fetch_id,extraction_status,last_http_status,raw_complete FROM resources WHERE status='downloaded' AND raw_complete=1 ORDER BY id")]
    diagnoses, errors, eligible = [], [], []
    for resource in resources:
        rawpath = (COLLECTION / resource['raw_path']).resolve()
        metapath = (COLLECTION / resource['metadata_path']).resolve()
        try:
            assert rawpath.is_relative_to(COLLECTION) and metapath.is_relative_to(COLLECTION)
            assert sha(rawpath) == resource['sha256']
            metadata = json.loads(metapath.read_text(encoding='utf-8'))
            assert metadata['sha256'] == resource['sha256'] and metadata['fetch_id'] == resource['last_fetch_id'] and metadata['requested_url'] == resource['url']
            assert metadata['http_status'] == resource['last_http_status'] == 200 and metadata['raw_complete'] is True
            with ocr.fitz.open(rawpath) as pdf:
                assert not pdf.needs_pass
                count = len(pdf)
                sample_numbers = sorted({1, (count + 1) // 2, count}) if count else []
                sampled = [{'page_number': n, **{k: v for k, v in ocr.screen_page(pdf[n - 1]).items() if k != 'embedded_text'}} for n in sample_numbers]
            is_gap = resource['extraction_status'] != 'extracted'
            scan_sample = any(p['needs_ocr'] for p in sampled)
            native_text = any(p['embedded_alphanumeric_characters'] >= 120 for p in sampled)
            diagnosis = 'scan_image_with_sparse_native_text_on_sampled_pages' if is_gap and scan_sample else 'alternative_native_extractor_found_text_review_needed' if is_gap and native_text else 'nontext_gap_requires_review' if is_gap else 'native_text_capture_present_sampled_for_comparison'
            record = {'collection': relative(COLLECTION), 'resource_id': resource['id'], 'source_url': resource['url'], 'source_pdf_path': relative(rawpath), 'source_pdf_sha256': resource['sha256'], 'metadata_path': relative(metapath), 'metadata_sha256': sha(metapath), 'collector_extraction_status': resource['extraction_status'], 'collector_extraction_error': metadata.get('extraction_error'), 'collector_pages_extracted': metadata.get('pages_extracted'), 'collector_page_count': metadata.get('page_count'), 'collector_text_characters': metadata.get('text_char_count'), 'pymupdf_page_count': count, 'native_pages_extracted_matches_actual_count': metadata.get('pages_extracted') == count, 'diagnosis': diagnosis, 'screened_sample_pages': sampled, 'all_pdf_pages_screened_in_this_diagnosis': len(sample_numbers) == count, 'selection_status': 'not_selected_native_text_present' if not is_gap else 'pending_ocr_selection'}
            diagnoses.append(record)
            if is_gap and scan_sample and not metadata.get('extraction_error'):
                eligible.append((count, resource['url'], resource, record))
            elif is_gap:
                record['selection_status'] = 'deferred_manual_gap_review'
        except Exception as exc:
            errors.append({'resource_id': resource['id'], 'url': resource['url'], 'error': type(exc).__name__ + ': ' + str(exc)})
    selected, page_count = [], 0
    for count, _, resource, record in sorted(eligible):
        if len(selected) < MAX_DOCUMENTS and page_count + count <= MAX_PDF_PAGES:
            selected.append(resource); page_count += count; record['selection_status'] = 'selected_offline_ocr'
        else:
            record['selection_status'] = 'deferred_bounded_pass_document_or_page_cap'
    ocr.atomic_lines(REPORT / 'pdf_diagnosis.jsonl', diagnoses)
    ocr.atomic_lines(REPORT / 'diagnosis_errors.jsonl', errors)
    ocr.atomic_lines(REPORT / 'selected_resource_snapshot.jsonl', selected)
    summary = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'downloaded_resources_in_read_only_snapshot': len(resources), 'verified_diagnosed_pdfs': len(diagnoses), 'diagnosis_counts': dict(Counter(r['diagnosis'] for r in diagnoses)), 'collector_extraction_status_counts': dict(Counter(r['collector_extraction_status'] for r in diagnoses)), 'diagnosis_errors': len(errors), 'selected_documents': len(selected), 'selected_total_pdf_pages': page_count, 'max_documents': MAX_DOCUMENTS, 'max_pdf_pages': MAX_PDF_PAGES, 'selection_rule': 'Shortest fully saved affected PDFs first; exactly observed URLs, no source download or crawler queue changes.', 'network_requests': 0, 'collector_database_opened_read_only': True, 'source_originals_modified': False, 'legal_currency_verified': False, 'runtime': 'PyMuPDF ' + ocr.fitz.VersionBind, 'selected_snapshot_sha256': sha(REPORT / 'selected_resource_snapshot.jsonl'), 'screening_script_sha256': sha(ROOT / 'pipeline/prepare_court_ocr.py'), 'ocr_worker_sha256': sha(ROOT / 'sources/official_courts/ocr_worker.cjs'), 'diagnosis_sha256': sha(REPORT / 'pdf_diagnosis.jsonl')}
    ocr.atomic_json(REPORT / 'diagnosis_summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    if errors:
        raise RuntimeError('Source evidence diagnosis errors require review before OCR preparation.')
    if not selected:
        return
    prepared = ocr.prepare(COLLECTION, OUTPUT, ROOT / 'sources/official_courts/ocr/models', resource_snapshot=selected, snapshot_kind='bounded_hash_verified_WA_resource_snapshot')
    plan = {'prepared_at_utc': datetime.now(timezone.utc).isoformat(), 'root': relative(OUTPUT), 'documents': prepared['documents'], 'pages_queued': prepared['pages'], 'worker': 'sources/official_courts/ocr_worker.cjs', 'node_executable': 'C:/Users/firas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe', 'environment': {'OFFICIAL_OCR_ROOT': str(OUTPUT), 'OFFICIAL_OCR_RUN_SECONDS': '300', 'OFFICIAL_OCR_PAGE_TIMEOUT_MS': '45000', 'OFFICIAL_OCR_WORKERS': '2'}, 'network_requests': 0, 'models_already_local': True, 'model_sha256': prepared['model_sha256'], 'pages_manifest_sha256': sha(OUTPUT / 'pages.jsonl'), 'documents_manifest_sha256': sha(OUTPUT / 'documents.jsonl'), 'original_and_embedded_text_overwrite': False, 'worker_run_required': True, 'scope_note': 'OCR output is a derivative with source/page/image/model hashes; completion is not a text-correctness or current-law guarantee.'}
    ocr.atomic_json(REPORT / 'run_plan.json', plan)
    print(json.dumps(plan, indent=2), flush=True)


if __name__ == '__main__':
    main()
