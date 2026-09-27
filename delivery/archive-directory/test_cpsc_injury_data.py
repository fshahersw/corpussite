"""Tests for the cpsc_injury_data adapter (generic view contract, fail-closed hash gate)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cpsc_injury_data  # noqa: E402

REAL = cpsc_injury_data.DATA
NEISS_BEDFRAME_CASE = 'neiss:250102066'
SP_SCOOTER_REPORT = 'sp:20260731-B67E0-2147313090'


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='cpsc_gate_'))
        for name in ('validation.json', cpsc_injury_data.DB_NAME):
            shutil.copy2(REAL / name, self.tmp / name)
        cpsc_injury_data.DATA = self.tmp
        cpsc_injury_data._CACHE.clear()

    def tearDown(self):
        cpsc_injury_data.DATA = REAL
        cpsc_injury_data._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = cpsc_injury_data.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertEqual(result['results'], [])
        self.assertIsNone(cpsc_injury_data.detail(NEISS_BEDFRAME_CASE))

    def test_copy_without_tamper_opens(self):
        self.assertTrue(cpsc_injury_data.listing({})['available'])

    def test_tampered_database_closes_gate(self):
        with open(self.tmp / cpsc_injury_data.DB_NAME, 'ab') as handle:
            handle.write(b'\x00\x00\x00\x00')
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.tmp / 'validation.json').unlink()
        self.assertClosed()

    def test_status_not_passed_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'failed'
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()


class ContractTests(unittest.TestCase):
    def setUp(self):
        cpsc_injury_data.DATA = REAL
        cpsc_injury_data._CACHE.clear()

    def test_neiss_listing_shape_and_total(self):
        result = cpsc_injury_data.listing({})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 410201)
        self.assertEqual(result['limit'], 25)
        self.assertEqual(len(result['results']), 25)
        names = [f['name'] for f in result['filters']]
        self.assertEqual(names, ['q', 'product', 'year', 'month', 'body_part', 'diagnosis', 'disposition', 'age_band', 'sex'])
        # the product is the row title, so its column leads and the table does not print it twice
        self.assertEqual([c['key'] for c in result['columns']], ['product', 'date', 'diagnosis', 'body_part', 'disposition'])
        self.assertLess(len(result['qualification']), 400)
        self.assertIn('SAMPLE', result['qualification'])
        for flt in result['filters']:
            if flt['type'] == 'select':
                for option in flt['options']:
                    self.assertEqual(set(option), {'value', 'label', 'count'})
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'title', 'subtitle', 'cells', 'badges', 'links'})
            self.assertLessEqual(len(row['badges']), 2)
            self.assertTrue(row['id'].startswith('neiss:'))

    def test_age_band_options_are_in_age_order_not_alphabetical(self):
        age_band = [f for f in cpsc_injury_data.listing({})['filters'] if f['name'] == 'age_band'][0]
        values = [o['value'] for o in age_band['options']]
        self.assertEqual(values, ['Under 1 year', '1-4 years', '5-9 years', '10-14 years', '15-19 years',
                                   '20-24 years', '25-34 years', '35-44 years', '45-54 years', '55-64 years',
                                   '65-74 years', '75+ years', 'Unknown'])

    def test_default_listing_is_fast(self):
        # Regression: a naive live GROUP BY on neiss_cases' own *_label columns for facet options, plus an
        # unindexed date sort, measured 8-12s per call; precomputed facet_counts plus a covering date index
        # brought the default (unfiltered) listing down to well under a second.
        import time
        cpsc_injury_data._CACHE.clear()
        start = time.time()
        cpsc_injury_data.listing({})
        self.assertLess(time.time() - start, 2.0)

    def test_saferproducts_listing_shape_and_total(self):
        result = cpsc_injury_data.listing({'dataset': 'saferproducts'})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 69333)
        names = [f['name'] for f in result['filters']]
        self.assertEqual(names, ['q', 'product_category', 'state', 'year', 'category_of_submitter'])
        self.assertEqual([c['key'] for c in result['columns']], ['date', 'category', 'manufacturer', 'state', 'submitter'])
        self.assertLess(len(result['qualification']), 400)
        self.assertIn('unverified', result['qualification'].lower())
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'title', 'subtitle', 'cells', 'badges', 'links'})
            self.assertLessEqual(len(row['badges']), 2)
            self.assertIn('Unverified consumer-submitted report', row['badges'])
            self.assertTrue(row['id'].startswith('sp:'))

    def test_neiss_filters_on_real_data(self):
        self.assertEqual(cpsc_injury_data.listing({'disposition': '8'})['total'], 376)
        self.assertEqual(cpsc_injury_data.listing({'sex': '1'})['total'], 221547)
        self.assertEqual(cpsc_injury_data.listing({'age_band': 'Under 1 year'})['total'], 10259)
        self.assertEqual(cpsc_injury_data.listing({'year': '2025'})['total'], 410201)
        self.assertEqual(cpsc_injury_data.listing({'product': 'scooter'})['total'], 7609)  # matches product_1/2/3_label
        self.assertEqual(cpsc_injury_data.listing({'body_part': '94'})['total'], 5815)
        self.assertGreater(cpsc_injury_data.listing({'q': 'laceration'})['total'], 0)
        self.assertEqual(cpsc_injury_data.listing({'year': '1999'})['total'], 0)

    def test_saferproducts_filters_on_real_data(self):
        self.assertEqual(cpsc_injury_data.listing({'dataset': 'saferproducts', 'category_of_submitter': 'Consumer'})['total'], 67164)
        self.assertEqual(cpsc_injury_data.listing({'dataset': 'saferproducts', 'state': 'Illinois'})['total'], 2261)
        top = cpsc_injury_data.listing({'dataset': 'saferproducts', 'product_category': 'Kitchen'})
        self.assertEqual(top['total'], 25078)

    def test_stopwords_ignored_in_q(self):
        self.assertEqual(cpsc_injury_data.listing({'q': 'the a of'})['total'], 410201)
        self.assertEqual(cpsc_injury_data.listing({'dataset': 'saferproducts', 'q': 'the a of'})['total'], 69333)

    def test_bad_params_never_raise(self):
        result = cpsc_injury_data.listing({'page': 'x', 'limit': '-3', 'year': 'abcd', 'body_part': 'not-a-number', 'sex': 'zz'})
        self.assertTrue(result['available'])
        self.assertEqual(result['page'], 1)
        self.assertGreater(result['limit'], 0)
        self.assertIsNone(cpsc_injury_data.detail('neiss:does-not-exist'))
        self.assertIsNone(cpsc_injury_data.detail('sp:does-not-exist'))
        self.assertIsNone(cpsc_injury_data.detail('bogus:1'))
        self.assertIsNone(cpsc_injury_data.detail(''))
        self.assertIsNone(cpsc_injury_data.detail(None))
        self.assertIsNone(cpsc_injury_data.detail('../../validation.json'))

    def test_neiss_detail(self):
        item = cpsc_injury_data.detail(NEISS_BEDFRAME_CASE)
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'})
        self.assertIn('4076', item['title'])
        labels = dict((f[0], f[1]) for f in item['facts'])
        self.assertIn('46.4246', labels['Statistical weight (publisher-reported; do not compute a rate or estimate from a single row)'])
        self.assertIn('code 47', labels['Age (as coded)'])
        self.assertEqual(item['sections'][0]['heading'], 'Narrative (as printed)')
        self.assertIn('LACERATION', item['sections'][0]['text'].upper())

    def test_saferproducts_detail(self):
        item = cpsc_injury_data.detail(SP_SCOOTER_REPORT)
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'})
        self.assertIn('Volpram', item['title'])
        labels = dict((f[0], f[1]) for f in item['facts'])
        self.assertEqual(labels['Brand'], 'Volpram')
        self.assertIn('Illinois', labels['City / state / ZIP (as submitted)'])
        self.assertIn('scooter', item['sections'][0]['text'].lower())

    def test_public_dicts_carry_no_filesystem_paths(self):
        blob = json.dumps([cpsc_injury_data.listing({'limit': '100'}), cpsc_injury_data.listing({'dataset': 'saferproducts', 'limit': '100'}),
                            cpsc_injury_data.detail(NEISS_BEDFRAME_CASE), cpsc_injury_data.detail(SP_SCOOTER_REPORT)])
        for needle in ('C:/', 'C:\\\\', 'Downloads', 'cpsc_injury_data.sqlite3', 'sources/cpsc_injury_data'):
            self.assertNotIn(needle, blob)


if __name__ == '__main__':
    unittest.main()
