"""Tests for the public_laws adapter (generic view contract, fail-closed hash gate)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import public_laws  # noqa: E402

REAL = public_laws.DATA


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='plaw_gate_'))
        for name in ('validation.json', public_laws.DB_NAME):
            shutil.copy2(REAL / name, self.tmp / name)
        public_laws.DATA = self.tmp
        public_laws._CACHE.clear()

    def tearDown(self):
        public_laws.DATA = REAL
        public_laws._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = public_laws.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertEqual(result.get('results'), [])
        self.assertIsNone(public_laws.detail('PLAW-113publ1'))
        self.assertIsNone(public_laws.for_usc('42', '4016'))

    def test_copy_without_tamper_opens(self):
        self.assertTrue(public_laws.listing({})['available'])

    def test_tampered_data_file_closes_gate(self):
        with open(self.tmp / public_laws.DB_NAME, 'ab') as handle:
            handle.write(b'garbage-bytes-appended')
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_not_passed_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'failed'
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.tmp / 'validation.json').unlink()
        self.assertClosed()

    def test_unregistered_data_file_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['data_files'] = []
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()


class ContractTests(unittest.TestCase):
    def setUp(self):
        public_laws.DATA = REAL
        public_laws._CACHE.clear()

    def test_listing_shape(self):
        result = public_laws.listing({'limit': '500', 'page': '1'})
        self.assertTrue(result['available'])
        self.assertEqual(result['limit'], 100)  # clamped to MAX_LIMIT
        self.assertEqual(result['page'], 1)
        self.assertGreater(result['total'], 0)
        self.assertTrue(result['filters'])
        self.assertTrue(result['columns'])
        self.assertLessEqual(len(result['results']), 100)
        self.assertLess(len(result['qualification']), 400)

    def test_listing_never_raises_on_bad_params(self):
        for bad in ({'page': 'not-a-number'}, {'limit': -5}, {'congress': 'abc'}, {'usc_title': 'xyz'},
                    {'action': '<script>'}, {'q': None}, {'year': '99999'}, 'not-a-dict', None):
            result = public_laws.listing(bad if isinstance(bad, dict) else {}) if not isinstance(bad, dict) else public_laws.listing(bad)
            self.assertIn('available', result)

    def test_congress_filter(self):
        result = public_laws.listing({'congress': '113', 'limit': '5'})
        self.assertTrue(result['available'])
        for row in result['results']:
            self.assertEqual(row['cells']['congress'], 113)

    def test_usc_title_filter_returns_only_matching_laws(self):
        result = public_laws.listing({'usc_title': '42', 'limit': '5'})
        self.assertTrue(result['available'])
        self.assertGreater(result['total'], 0)

    def test_action_filter_amends(self):
        result = public_laws.listing({'action': 'amends', 'limit': '5'})
        self.assertTrue(result['available'])
        self.assertGreater(result['total'], 0)

    def test_search_by_citation(self):
        result = public_laws.listing({'q': 'flood insurance', 'limit': '5'})
        self.assertTrue(result['available'])

    def test_detail_unknown_id_returns_none(self):
        self.assertIsNone(public_laws.detail('PLAW-does-not-exist'))
        self.assertIsNone(public_laws.detail(None))
        self.assertIsNone(public_laws.detail('x' * 500))

    def test_detail_known_law_has_effects_table(self):
        listing = public_laws.listing({'usc_title': '42', 'limit': '1'})
        self.assertTrue(listing['results'])
        law_id = listing['results'][0]['id']
        detail = public_laws.detail(law_id)
        self.assertIsNotNone(detail)
        self.assertTrue(detail['title'])
        self.assertTrue(detail['sections'])
        self.assertEqual(detail['sections'][0]['type'], 'table')
        self.assertTrue(any(row for row in detail['sections'][0]['rows']))

    def test_for_usc_returns_compact_block(self):
        block = public_laws.for_usc('42', '4016')
        self.assertIsNotNone(block)
        self.assertLessEqual(len(block['results']), 25)
        self.assertGreater(block['total'], 0)

    def test_for_usc_bad_params_returns_none(self):
        self.assertIsNone(public_laws.for_usc('not-a-title', '4016'))
        self.assertIsNone(public_laws.for_usc('42', None))
        self.assertIsNone(public_laws.for_usc(None, None))

    def test_for_usc_unknown_section_returns_none(self):
        self.assertIsNone(public_laws.for_usc('999', '999999'))


if __name__ == '__main__':
    unittest.main()
