"""Tests for the expert_rulings adapter (generic view contract, fail-closed hash gate, for_mdl hook)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expert_rulings  # noqa: E402

REAL = expert_rulings.DATA
THREE_M_ORDER = '14916674-1103-1103'
GOODSTEIN_ROW = '6224301-700-700'
MOLNAR_MDL = 2243
ZOSTAVAX_MDL_AS_SCANNED_ONLY = 2848  # scanned by the source but NOT resolvable via this crosswalk


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='expert_gate_'))
        for name in ('validation.json', expert_rulings.DB_NAME):
            shutil.copy2(REAL / name, self.tmp / name)
        expert_rulings.DATA = self.tmp
        expert_rulings._CACHE.clear()

    def tearDown(self):
        expert_rulings.DATA = REAL
        expert_rulings._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = expert_rulings.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertEqual(result['results'], [])
        self.assertIsNone(expert_rulings.detail(THREE_M_ORDER))
        self.assertIsNone(expert_rulings.for_mdl(3094))

    def test_copy_without_tamper_opens(self):
        self.assertTrue(expert_rulings.listing({})['available'])

    def test_tampered_database_closes_gate(self):
        with open(self.tmp / expert_rulings.DB_NAME, 'ab') as handle:
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

    def test_license_metadata_tamper_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['export_allowed'] = True
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()


class ContractTests(unittest.TestCase):
    def setUp(self):
        expert_rulings.DATA = REAL
        expert_rulings._CACHE.clear()

    def test_listing_shape_and_total(self):
        result = expert_rulings.listing({})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 2035)
        self.assertEqual(len(result['results']), 25)
        names = [f['name'] for f in result['filters']]
        self.assertEqual(names, ['q', 'mdl', 'court', 'year', 'kind'])
        self.assertEqual([c['key'] for c in result['columns']], ['date', 'docket', 'category', 'kind', 'mdl'])
        self.assertLess(len(result['qualification']), 400)
        self.assertIn('not a list of rulings', result['qualification'])
        self.assertIn('grant/deny outcome is not determined', result['qualification'])
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'title', 'subtitle', 'cells', 'badges', 'links'})
            self.assertLessEqual(len(row['badges']), 2)

    def test_filters_on_real_data(self):
        self.assertEqual(expert_rulings.listing({'mdl': '3094'})['total'], 213)
        self.assertEqual(expert_rulings.listing({'mdl': '2848'})['total'], 0)  # Zostavax: scanned, never verified
        self.assertEqual(expert_rulings.listing({'court': 'njd'})['total'], 389)
        self.assertEqual(expert_rulings.listing({'court': 'NJD'})['total'], 389)  # case-insensitive
        self.assertEqual(expert_rulings.listing({'kind': 'case_management_order'})['total'], 56)
        self.assertEqual(expert_rulings.listing({'year': '2020'})['total'] > 0, True)
        self.assertEqual(expert_rulings.listing({'year': '1999'})['total'], 0)
        self.assertGreater(expert_rulings.listing({'q': 'biostatistician'})['total'], 0)

    def test_stopwords_ignored_in_q(self):
        self.assertEqual(expert_rulings.listing({'q': 'the a of'})['total'], 2035)

    def test_redacted_row_never_shows_the_natural_person_name(self):
        result = expert_rulings.listing({'q': 'astrazeneca'}, )
        # The Goodstein row's own case name contains "astrazeneca"; case_name is not FTS-indexed for
        # redacted rows, so a search on the company name alone should not surface it via case-name text
        # (it may still surface via docket_number/description text, which is fine -- the name itself
        # is what must never appear in the OUTPUT).
        blob = json.dumps(result)
        self.assertNotIn('goodstein', blob.lower())
        self.assertNotIn('molnar', blob.lower())

    def test_bad_params_never_raise(self):
        result = expert_rulings.listing({'page': 'x', 'limit': '-3', 'mdl': 'not-a-number', 'year': 'abcd', 'court': ''})
        self.assertTrue(result['available'])
        self.assertEqual(result['page'], 1)
        self.assertGreater(result['limit'], 0)
        self.assertIsNone(expert_rulings.detail('does-not-exist'))
        self.assertIsNone(expert_rulings.detail(''))
        self.assertIsNone(expert_rulings.detail(None))
        self.assertIsNone(expert_rulings.for_mdl('not-a-number'))
        self.assertIsNone(expert_rulings.for_mdl(None))
        self.assertIsNone(expert_rulings.for_mdl(999999))

    def test_detail_safe_caption(self):
        item = expert_rulings.detail(THREE_M_ORDER)
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'})
        self.assertIn('3M COMBAT ARMS', item['title'].upper())
        labels = dict((f[0], f[1]) for f in item['facts'])
        self.assertEqual(labels['Case caption'], item['title'])
        self.assertEqual(labels['MDL number (verified via mdl_docket_crosswalk)'], '2885')
        self.assertIn('biostatistician', item['sections'][0]['text'].lower())
        self.assertTrue(any(link['url'].startswith('https://www.courtlistener.com/') for link in item['links']))

    def test_detail_redacted_caption(self):
        item = expert_rulings.detail(GOODSTEIN_ROW)
        self.assertNotIn('goodstein', item['title'].lower())
        self.assertNotIn('goodstein', json.dumps(item).lower())
        self.assertTrue(item['title'].startswith('Docket 2:17-md-02789'))
        labels = dict((f[0], f[1]) for f in item['facts'])
        self.assertTrue(labels['Case caption'].startswith('Redacted'))
        self.assertEqual(labels['MDL number (verified via mdl_docket_crosswalk)'], '2789')

    def test_for_mdl_verified(self):
        block = expert_rulings.for_mdl(3094)
        self.assertEqual(block['mdl_number'], 3094)
        self.assertEqual(block['total'], 213)
        self.assertLessEqual(len(block['results']), 10)
        self.assertEqual(block['link'], '#expert-rulings?mdl=3094')
        self.assertLess(len(block['qualification']), 400)

    def test_for_mdl_unresolved_scanned_only_returns_none(self):
        # Zostavax (638 rows scanned as mdl_number "02848") is never verified by this crosswalk.
        self.assertIsNone(expert_rulings.for_mdl(ZOSTAVAX_MDL_AS_SCANNED_ONLY))

    def test_for_mdl_molnar_fosamax(self):
        block = expert_rulings.for_mdl(MOLNAR_MDL)
        self.assertEqual(block['total'], 1)
        self.assertNotIn('molnar', json.dumps(block).lower())

    def test_public_dicts_carry_no_filesystem_paths(self):
        blob = json.dumps([expert_rulings.listing({'limit': '100'}), expert_rulings.detail(THREE_M_ORDER),
                            expert_rulings.detail(GOODSTEIN_ROW), expert_rulings.for_mdl(3094)])
        for needle in ('C:/', 'C:\\\\', 'Downloads', 'SW-BULK', 'expert_rulings_scan.sqlite3'):
            self.assertNotIn(needle, blob)


if __name__ == '__main__':
    unittest.main()
