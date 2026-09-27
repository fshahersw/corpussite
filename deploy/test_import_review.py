"""Bounded regression tests for reviewed local document filter translations."""
import unittest
from import_catalog import normalize


class ImportDocumentReviewTests(unittest.TestCase):
    def test_captured_filter_uses_native_capture_date_and_link_only_is_derived(self):
        row = {'id': 'x', 'dataset': 'focused', 'category': 'rules', 'source_url': 'https://court.gov/rules',
               'item': {'has_original': False, 'has_text': False,
                        'facets': {'dates': {'captured_at': {'value': '2026-09-19T08:00:00Z'}}}},
               'filters': {'saved_at': '2026-09-19T08:00:00Z', 'availability': []}}
        result = normalize(row)
        self.assertEqual(result['filters']['captured_at'], '2026-09-19T08:00:00Z')
        self.assertEqual(result['filters']['availability'], ['link_only'])

    def test_saved_original_does_not_become_link_only(self):
        row = {'id': 'x', 'dataset': 'focused', 'category': 'rules', 'source_url': 'https://court.gov/rules',
               'item': {'has_original': True, 'has_text': False}, 'filters': {'availability': ['original']}}
        self.assertEqual(normalize(row)['filters']['availability'], ['original'])


if __name__ == '__main__': unittest.main()
