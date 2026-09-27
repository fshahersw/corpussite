import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('browser_archive', ROOT / 'scripts/trellis_browser_archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class BrowserArchiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.base = (ROOT / 'test_artifacts/browser_archive').resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='fixture-', dir=self.base)).resolve()
        self.payload = {
            'url': 'https://trellis.law/state-rules/az/constitution/example/section-1',
            'title': 'Constitution example | Trellis Law', 'heading': 'Section 1',
            'legal_text': 'Section 1\nPreserved legal body text with a section symbol: § 1.',
            'legal_html': '<div class="rule-header"><h1>Section 1</h1><pre>Preserved text</pre></div>',
            'captured_at': '2026-09-13T12:00:00Z', 'content_kind': 'law_text',
            'dom_selector': 'div.rule-header', 'signed_in_observed': True,
            'observed_law_links': [],
        }

    def tearDown(self):
        resolved = self.folder.resolve()
        if resolved.parent != self.base or not resolved.is_relative_to(ROOT.resolve()):
            raise RuntimeError('Refusing cleanup outside the generated fixture directory')
        shutil.rmtree(resolved)

    def test_capture_preserves_utf8_and_is_idempotent(self):
        result = archive.archive_capture(self.payload, self.folder)
        self.assertEqual(result['status'], 'saved')
        second = copy.deepcopy(self.payload)
        second['captured_at'] = '2026-09-13T12:01:00Z'
        self.assertEqual(archive.archive_capture(second, self.folder)['status'], 'already_saved')
        rows = (self.folder / 'manifest.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows), 1)
        record = json.loads(rows[0])
        self.assertEqual((self.folder / record['text_path']).read_text(encoding='utf-8'), self.payload['legal_text'])
        self.assertIsNone(record['source_http_status'])

    def test_case_and_external_urls_are_rejected(self):
        for url in ('https://trellis.law/case/123/example', 'https://example.com/state-rules/az/example'):
            payload = {**self.payload, 'url': url}
            with self.assertRaises(ValueError):
                archive.archive_capture(payload, self.folder)
        self.assertFalse((self.folder / 'manifest.jsonl').exists())

    def test_unrelated_observed_link_is_rejected(self):
        payload = {**self.payload, 'observed_law_links': [{'url': 'https://trellis.law/doc/1/example', 'text': 'Document'}]}
        with self.assertRaises(ValueError):
            archive.archive_capture(payload, self.folder)

    def test_credential_field_is_rejected(self):
        with self.assertRaises(ValueError):
            archive.archive_capture({**self.payload, 'cookies': 'fixture-only'}, self.folder)

    def test_directory_requires_observed_directory_selector(self):
        payload = {**self.payload, 'content_kind': 'law_directory'}
        with self.assertRaises(ValueError):
            archive.archive_capture(payload, self.folder)
        payload['dom_selector'] = 'div.profileBillingContainer'
        self.assertEqual(archive.archive_capture(payload, self.folder)['status'], 'saved')

    def test_existing_text_tampering_is_not_reported_as_saved(self):
        archive.archive_capture(self.payload, self.folder)
        record = json.loads((self.folder / 'manifest.jsonl').read_text(encoding='utf-8'))
        (self.folder / record['text_path']).write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            archive.archive_capture(self.payload, self.folder)


if __name__ == '__main__':
    unittest.main()
