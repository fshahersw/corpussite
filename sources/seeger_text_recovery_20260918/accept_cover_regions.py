"""Bind the reviewed regional OCR derivative to its existing Seeger original ID."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('recover_seeger_text', ROOT / 'scripts/recover_seeger_text.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
OUT = recovery.OUT
original_id = 'seeger_original_aa5f536dfdbc24ac97cf497d1d53b1992b73951fe4ac7609a5a081aaa14f68a0'
original = next(r for r in recovery.lines(OUT / 'input_originals.jsonl') if r['id'] == original_id)
prior = next(r for r in recovery.lines(OUT / 'resources.jsonl') if r['id'] == original_id)
manifest_path = OUT / 'ocr_cover_regions/manifest.json'
regional = json.loads(manifest_path.read_text(encoding='utf-8'))
assert recovery.sha(ROOT / original['raw_path']) == original['sha256'] == regional['source_pdf_sha256']
assert recovery.sha(Path(regional['source_image_path'])) == regional['source_image_sha256']
for region in regional['regions']:
    assert recovery.sha(Path(region['text_path'])) == region['text_sha256']
text_path = Path(regional['text_path'])
assert recovery.sha(text_path) == regional['text_sha256']
text = text_path.read_text(encoding='utf-8', errors='strict')
assert len(regional['regions']) == 9 and text.strip()
notes = [
    'Region-based OCR follows observed table columns; labels are supplied by the extraction procedure.',
    'Visual review confirmed column/category order and restored readable case-type labels compared with whole-page OCR.',
    'Residual OCR character errors remain, including typography and the revision marker; no manual textual corrections applied.',
    'OCR confidence is an engine estimate, not a correctness guarantee; verify against the original image.',
    'Legal currency and applicability are not verified.'
]
metadata_path = OUT / 'metadata' / (original_id + '.regions.json')
metadata = {'id': original_id, 'raw_path': original['raw_path'], 'source_url': original['source_url'],
            'raw_sha256_before': original['sha256'], 'raw_sha256_after': recovery.sha(ROOT / original['raw_path']),
            'method': 'tesseract_js_region_ocr', 'captured_at': recovery.now(),
            'quality_notes': notes, 'regional_derivative': regional,
            'regional_manifest_path': recovery.relative(manifest_path),
            'regional_manifest_sha256': recovery.sha(manifest_path),
            'superseded_whole_page_derivative': prior,
            'visual_review': {'reviewed': True, 'source_page': 1, 'all_nine_regions_compared_to_image': True,
                             'manual_text_corrections_applied': False, 'character_accuracy_certified': False}}
recovery.atomic(metadata_path, metadata)
record = {'id': original_id, 'raw_sha256': original['sha256'], 'text_path': recovery.relative(text_path),
          'text_sha256': recovery.sha(text_path), 'characters': len(text), 'method': 'tesseract_js_region_ocr',
          'metadata_path': recovery.relative(metadata_path), 'metadata_sha256': recovery.sha(metadata_path),
          'status': 'recovered', 'quality_notes': notes}
recovery.event({'event': 'recovered', 'reason': 'Reviewed replacement of interleaved whole-page OCR with observed-region OCR', 'record': record})
recovery.validate(recovery.load_input(), final=True)
