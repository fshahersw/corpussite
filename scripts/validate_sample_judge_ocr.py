"""Independently verify the frozen sample-report OCR artifacts and parent."""
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/judges/focus_20260913/report_sample/offline_ocr'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]

def main():
    manifest = read(OUT / 'files.sha256.json')
    summary = read(OUT / 'summary.json')
    assert summary['pages'] == summary['OCR_completed_pages'] == 12
    assert summary['OCR_failed_pages'] == 0
    paths = set()
    for entry in manifest['files']:
        path = (ROOT / entry['path']).resolve()
        path.relative_to(OUT.resolve())
        assert str(path) not in paths and sha(path) == entry['sha256']
        paths.add(str(path))
    parent = summary['source_pdf']
    assert sha(ROOT / parent['path']) == parent['sha256']
    document = rows(OUT / 'pdf_ocr_manifest.jsonl')[0]
    assert sha(ROOT / document['parent_capture_path']) == document['parent_capture_sha256']
    combined = summary['combined_text']
    assert sha(ROOT / combined['path']) == combined['sha256']
    coverage = rows(OUT / 'page_coverage.jsonl')
    assert len(coverage) == 12 and {p['page_number'] for p in coverage} == set(range(1, 13))
    for p in coverage:
        assert p['source_pdf_sha256'] == parent['sha256'] and p['actual_current_judge_analytics'] is False
        assert sha(Path(p['image_path'])) == p['image_sha256']
        assert sha(Path(p['embedded_text_path'])) == p['embedded_text_sha256']
    value = {'validated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'validated': True, 'artifact_files_independently_rehashed': len(paths),
        'all12_page_parent_render_embedded_text_links_verified': True,
        'raw_pdf_sha256': parent['sha256'], 'combined_ocr_file_sha256': combined['sha256'],
        'agent_manifest_sha256': sha(OUT / 'files.sha256.json'),
        'sample_only': True, 'current_actual_judge_statistics_created': False,
        'limits': 'Recognition and clipping gaps retained. OCR engine confidence is not verified accuracy.'}
    path = ROOT / 'reports/judges/focus_20260913/sample_ocr_root_validation.json'
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value))

if __name__ == '__main__':
    main()
