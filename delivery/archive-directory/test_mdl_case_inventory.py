"""Adapter tests for mdl_case_inventory (slice 8). Fail-closed gate, view contract, real data."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdl_case_inventory as adapter  # noqa: E402


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.folder = os.path.join(self.tmp, 'slice')
        shutil.copytree(str(adapter.DATA), self.folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.py'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        adapter._CACHE.clear()

    def test_clean_copy_loads(self):
        self.assertTrue(adapter._load(self.folder)['cases'])

    def test_tampered_data_file_fails_the_gate(self):
        p = os.path.join(self.folder, 'cases.jsonl')
        with open(p, 'a', encoding='utf-8') as f:
            f.write('{"id": "cl_docket:1"}\n')
        with self.assertRaises(ValueError):
            adapter._load(self.folder)

    def test_status_not_passed_fails_the_gate(self):
        p = os.path.join(self.folder, 'validation.json')
        v = json.load(open(p, encoding='utf-8'))
        v['status'] = 'failed'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(v, f)
        with self.assertRaises(ValueError):
            adapter._load(self.folder)

    def test_missing_folder_never_raises_from_public_functions(self):
        missing = os.path.join(self.tmp, 'gone')
        orig = adapter.DATA
        try:
            adapter.DATA = missing
            adapter._CACHE.clear()
            out = adapter.listing({})
            self.assertFalse(out['available'])
            self.assertIn('reason', out)
            self.assertEqual(out['results'], [])
            self.assertIsNone(adapter.detail('cl_docket:1'))
            self.assertIsNone(adapter.for_mdl(2804))
            self.assertIsNone(adapter.for_judge('judge-entity-x'))
            self.assertIsNone(adapter.for_court('nysd'))
        finally:
            adapter.DATA = orig
            adapter._CACHE.clear()


class ListingTests(unittest.TestCase):
    def test_shape(self):
        out = adapter.listing({})
        self.assertTrue(out['available'])
        self.assertEqual(out['page'], 1)
        self.assertTrue(out['qualification'])
        self.assertLessEqual(len(out['filters']), 7)
        self.assertLessEqual(len(out['columns']), 5)
        for r in out['results']:
            self.assertTrue(r['id'])
            self.assertIn('cells', r)
            self.assertIn('badges', r)
            self.assertIn('links', r)

    def test_limit_is_capped_and_bad_values_never_raise(self):
        self.assertLessEqual(adapter.listing({'limit': '5000'})['limit'], 100)
        self.assertTrue(adapter.listing({'limit': 'abc', 'page': '-3', 'year': 'zzz'})['available'])
        self.assertTrue(adapter.listing({'mdl': 'not-a-number'})['available'])

    def test_unknown_params_are_ignored(self):
        a = adapter.listing({'limit': '10'})
        b = adapter.listing({'limit': '10', 'nonsense': 'x'})
        self.assertEqual(a['total'], b['total'])

    def test_mdl_filter_narrows(self):
        out = adapter.listing({'mdl': '2804', 'limit': '100'})
        self.assertGreater(out['total'], 0)
        self.assertLess(out['total'], adapter.listing({})['total'])

    def test_court_and_status_filters(self):
        court = adapter.listing({'court': 'nysd', 'limit': '5'})
        self.assertGreater(court['total'], 0)
        self.assertTrue(adapter.listing({'status': 'terminated'})['total'] > 0)

    def test_public_dicts_carry_no_filesystem_paths(self):
        blob = json.dumps(adapter.listing({'limit': '50'}))
        self.assertNotIn('C:\\\\', blob)
        self.assertNotIn('.jsonl', blob)
        self.assertNotIn('AWS-BATCH1-DOCKETS', blob)

    def test_no_party_style_caption_is_published_as_a_title_or_inside_a_link(self):
        for r in adapter.listing({'limit': '200'})['results']:
            self.assertNotIn(' v. ', r['title'])
            for link in r['links']:
                # the CourtListener slug spells the caption; only the bare docket url may be shown
                self.assertNotRegex(link['url'], r'/docket/\d+/.+')


class DetailTests(unittest.TestCase):
    def _first_id(self):
        return adapter.listing({'mdl': '2804', 'limit': '1'})['results'][0]['id']

    def test_detail_shape(self):
        d = adapter.detail(self._first_id())
        self.assertIsNotNone(d)
        for key in ('id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'):
            self.assertIn(key, d)
        self.assertTrue(all(len(f) == 2 for f in d['facts']))

    def test_unknown_id_returns_none(self):
        self.assertIsNone(adapter.detail('cl_docket:999999999'))
        self.assertIsNone(adapter.detail(None))
        self.assertIsNone(adapter.detail(''))


class HookTests(unittest.TestCase):
    def test_for_mdl_is_compact_and_states_the_sample_fraction(self):
        out = adapter.for_mdl(2804)
        self.assertIsNotNone(out)
        self.assertLessEqual(len(out['sample']), 25)
        self.assertIn('by_court', out)
        self.assertIn('by_filed_year', out)
        self.assertIn('by_status', out)
        self.assertIn('jpml_actions_pending', out)
        self.assertTrue(out['link'].startswith('#'))
        self.assertIn('sample', out['qualification'].lower())

    def test_for_mdl_qualification_is_short_enough_for_an_embedded_panel(self):
        out = adapter.for_mdl(2804)
        self.assertLessEqual(len(out['qualification']), 500)
        self.assertNotIn('fraction_of_jpml_actions_pending', out)
        self.assertNotIn('fraction_of_jpml_total_actions', out)

    def test_for_mdl_unknown_returns_none(self):
        self.assertIsNone(adapter.for_mdl(999999))
        self.assertIsNone(adapter.for_mdl('abc'))

    def test_for_judge_groups_by_mdl(self):
        entity = None
        for r in adapter.listing({'mdl': '2804', 'limit': '25'})['results']:
            d = adapter.detail(r['id'])
            for f in d['facts']:
                if f[0].startswith('Judge entity'):
                    entity = f[1]
        if entity is None:
            self.skipTest('no bridged judge entity in the sampled rows')
        out = adapter.for_judge(entity)
        self.assertIsNotNone(out)
        self.assertLessEqual(len(out['sample']), 25)
        self.assertIn('by_mdl', out)

    def test_for_judge_unknown_returns_none(self):
        self.assertIsNone(adapter.for_judge('judge-entity-does-not-exist'))
        self.assertIsNone(adapter.for_judge(None))

    def test_for_court_compact(self):
        out = adapter.for_court('nysd')
        self.assertIsNotNone(out)
        self.assertLessEqual(len(out['sample']), 25)
        self.assertGreaterEqual(out['total'], len(out['sample']))
        self.assertTrue(out['link'].startswith('#'))

    def test_for_court_unknown_returns_none(self):
        self.assertIsNone(adapter.for_court('not-a-court'))


class RealDataTests(unittest.TestCase):
    def test_row_count_matches_the_validated_count(self):
        v = json.load(open(os.path.join(str(adapter.DATA), 'validation.json'), encoding='utf-8'))
        self.assertEqual(adapter.listing({})['total'], v['counts']['cases_rows'])
        self.assertEqual(v['counts']['cases_rows'], 4159)

    def test_mdl_2804_case_count_matches_the_recomputed_coverage(self):
        v = json.load(open(os.path.join(str(adapter.DATA), 'mdl_coverage.json'), encoding='utf-8'))
        self.assertEqual(adapter.listing({'mdl': '2804'})['total'],
                         v['mdl:2804']['cases_in_this_corpus'])


if __name__ == '__main__':
    unittest.main()
