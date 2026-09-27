"""Builder rules: publication classes, review flags and capture-date evidence are explicit."""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


b = module('archive_links_build', HERE / 'build.py')


class BuilderRuleTests(unittest.TestCase):
    def test_publication_class_follows_dataset(self):
        self.assertEqual(b.publication_for('focused'), 'published_focused_release')
        self.assertEqual(b.publication_for('seeger'), 'imported_collection')
        self.assertEqual(b.publication_for('federal'), 'federal_supplement')
        self.assertEqual(b.publication_for('pending_publication'), 'awaiting_publication')
        self.assertIsNone(b.publication_for('judge_vendor'))
        self.assertIsNone(b.publication_for('judge_enrichment'))

    def test_review_flags_reject_soft_404_and_empty_text(self):
        self.assertIn('suspect_title', b.review_flags('Page not found | Court', 1000))
        self.assertIn('suspect_title', b.review_flags('Access Denied', 1000))
        self.assertIn('suspect_title', b.review_flags('Just a moment...', 1000))
        self.assertIn('no_usable_text', b.review_flags('Court Rules', 0))
        self.assertEqual(b.review_flags('Court Rules', 500), [])
        self.assertEqual(b.review_flags('Error Correction Procedures for Judgments', 500), [])

    def test_captured_at_prefers_explicit_retrieval_evidence(self):
        self.assertEqual(b.captured_at({'retrieved_at': '2026-09-13T07:45:17+00:00'}), ('2026-09-13T07:45:17+00:00', 'retrieved_at'))
        self.assertEqual(b.captured_at({'captured_at': ['2026-09-18T17:38:22.248Z', '2026-09-13T07:37:36+00:00']})[1], 'captured_at')
        self.assertEqual(b.captured_at({'captured_at': '2026'}), (None, None))
        self.assertEqual(b.captured_at({}), (None, None))
        self.assertEqual(b.captured_at({'metadata': {'captured_at': '2026-09-01T00:00:00+00:00'}}), ('2026-09-01T00:00:00+00:00', 'metadata.captured_at'))


if __name__ == '__main__': unittest.main()
