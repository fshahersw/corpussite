"""Tests for the sd_statutes adapter (generic view contract, fail-closed hash gate)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sd_statutes  # noqa: E402

REAL = sd_statutes.DATA


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sdstat_gate_'))
        for name in ('validation.json', 'titles.jsonl', 'unresolved.jsonl'):
            shutil.copy2(REAL / name, self.tmp / name)
        sd_statutes.DATA = self.tmp
        sd_statutes._CACHE.clear()

    def tearDown(self):
        sd_statutes.DATA = REAL
        sd_statutes._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = sd_statutes.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertNotIn('results', result)
        self.assertIsNone(sd_statutes.detail('sdcl:2030342'))
        self.assertIsNone(sd_statutes.for_state('SD'))

    def test_copy_without_tamper_opens(self):
        self.assertTrue(sd_statutes.listing({})['available'])

    def test_tampered_data_file_closes_gate(self):
        with open(self.tmp / 'titles.jsonl', 'ab') as handle:
            handle.write(b'{"id":"sdcl:999999","title_number":"99"}\n')
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_status_not_passed_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'draft'
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.tmp / 'validation.json').unlink()
        self.assertClosed()

    def test_missing_data_file_closes_gate(self):
        (self.tmp / 'titles.jsonl').unlink()
        self.assertClosed()


class ShapeTests(unittest.TestCase):
    def test_listing_shape(self):
        result = sd_statutes.listing({})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 71)
        self.assertIn('qualification', result)
        self.assertIn('filters', result)
        self.assertIn('columns', result)
        self.assertLessEqual(len(result['columns']), 5)
        self.assertLessEqual(len(result['filters']), 7)
        self.assertLessEqual(len(result['results']), result['limit'])
        for row in result['results']:
            self.assertIn('id', row)
            self.assertIn('title', row)
            self.assertIn('cells', row)

    def test_listing_search_filters(self):
        result = sd_statutes.listing({'q': 'sovereignty'})
        self.assertTrue(result['available'])
        self.assertGreaterEqual(result['total'], 1)
        self.assertIn('sdcl:2030342', [r['id'] for r in result['results']])
        empty = sd_statutes.listing({'q': 'zzz_no_such_term_zzz'})
        self.assertEqual(empty['total'], 0)

    def test_listing_bad_params_never_raise(self):
        result = sd_statutes.listing({'page': 'x', 'limit': '-5', 'q': 12345})
        self.assertTrue(result['available'])

    def test_unknown_params_ignored(self):
        result = sd_statutes.listing({'not_a_real_param': 'xyz'})
        self.assertTrue(result['available'])

    def test_pagination_capped_at_100(self):
        result = sd_statutes.listing({'limit': '99999'})
        self.assertEqual(result['limit'], 100)

    def test_detail_title_1_has_real_chapters_and_sections(self):
        detail = sd_statutes.detail('sdcl:2030342')
        self.assertIsNotNone(detail)
        self.assertEqual(detail['id'], 'sdcl:2030342')
        self.assertIn('facts', detail)
        self.assertIn('sections', detail)
        self.assertGreater(len(detail['sections']), 0)
        chapter_1_1 = next((s for s in detail['sections'] if s['heading'].startswith('Chapter 1-1 ')), None)
        self.assertIsNotNone(chapter_1_1)
        self.assertGreater(len(chapter_1_1['rows']), 5)

    def test_detail_native_id_fact_is_the_records_own_id(self):
        # The "Native id" fact must publish an id that detail() itself resolves -- not a fabricated
        # "sdcl:<title_number>" that returns None.
        detail = sd_statutes.detail('sdcl:2030342')
        facts = dict(detail['facts'])
        self.assertIn(detail['id'], facts['Native id'])
        self.assertIsNotNone(sd_statutes.detail(detail['id']))

    def test_detail_unknown_id_returns_none(self):
        self.assertIsNone(sd_statutes.detail('sdcl:99999999'))
        self.assertIsNone(sd_statutes.detail('not-even-the-right-shape'))
        self.assertIsNone(sd_statutes.detail(None))

    def test_for_state_sd(self):
        block = sd_statutes.for_state('SD')
        self.assertIsNotNone(block)
        self.assertEqual(block['total'], 71)
        self.assertLessEqual(len(block['results']), 25)
        self.assertIn('qualification', block)
        self.assertIn('link', block)
        # Must point at this slice's actual registered route (areas.js: 'sd-statutes'), not a dead '#laws'.
        self.assertEqual(block['link'], '#sd-statutes')

    def test_for_state_other_state_returns_none(self):
        self.assertIsNone(sd_statutes.for_state('NJ'))
        self.assertIsNone(sd_statutes.for_state(''))
        self.assertIsNone(sd_statutes.for_state(None))

    def test_no_filesystem_paths_leak_into_public_dicts(self):
        result = sd_statutes.listing({})
        blob = json.dumps(result)
        self.assertNotIn('C:/Users', blob)
        self.assertNotIn('C:\\\\Users', blob)
        self.assertNotIn('.json.gz', blob)


if __name__ == '__main__':
    unittest.main()
