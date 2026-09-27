"""Tests for the state_proceedings adapter (generic view contract, fail-closed hash gate)."""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_proceedings  # noqa: E402

REAL = state_proceedings.DATA
NJ_ABILIFY = 'njmcl:abilify'
CA_SAMPLE = 'jccp:4961'
CA_TITLE_NOISE_SAMPLE = 'jccp:4512'
CA_TITLE_FALLBACK_SAMPLE = 'jccp:4295'


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='statepro_gate_'))
        for name in ('validation.json', 'proceedings.jsonl', 'edges.jsonl', 'unresolved.jsonl'):
            shutil.copy2(REAL / name, self.tmp / name)
        state_proceedings.DATA = self.tmp
        state_proceedings._CACHE.clear()

    def tearDown(self):
        state_proceedings.DATA = REAL
        state_proceedings._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = state_proceedings.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertNotIn('results', result)
        self.assertIsNone(state_proceedings.detail(NJ_ABILIFY))
        self.assertIsNone(state_proceedings.for_mdl(2741))
        self.assertIsNone(state_proceedings.for_state('NJ'))

    def test_copy_without_tamper_opens(self):
        self.assertTrue(state_proceedings.listing({})['available'])

    def test_tampered_data_file_closes_gate(self):
        with open(self.tmp / 'proceedings.jsonl', 'ab') as handle:
            handle.write(b'{"id":"njmcl:injected","type":"nj_mcl","title":"x"}\n')
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.tmp / 'validation.json').unlink()
        self.assertClosed()

    def test_unregistered_data_file_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['data_files'] = [f for f in gate['data_files'] if f['path'] != 'edges.jsonl']
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()


class ContractTests(unittest.TestCase):
    def setUp(self):
        state_proceedings.DATA = REAL
        state_proceedings._CACHE.clear()

    def test_listing_shape(self):
        result = state_proceedings.listing({'limit': '2000', 'page': '1'})
        self.assertTrue(result['available'])
        self.assertEqual(result['limit'], 100)
        self.assertEqual(result['total'], 1432)  # 40 NJ MCL + 1392 CA JCCP
        self.assertEqual(len(result['results']), 100)
        self.assertIn('not for redistribution', result['qualification'].lower())
        self.assertEqual([c['label'] for c in result['columns']],
                          ['System', 'County', 'Status / category', 'Related MDL / date received'])
        names = [f['name'] for f in result['filters']]
        self.assertEqual(names, ['q', 'system', 'county', 'category', 'archived', 'has_related_mdl', 'dfrom', 'dto'])
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'title', 'subtitle', 'cells', 'badges', 'links'})

    def test_system_filter_counts(self):
        nj = state_proceedings.listing({'system': 'NJ_MCL', 'limit': '100'})
        self.assertEqual(nj['total'], 40)
        ca = state_proceedings.listing({'system': 'CA_JCCP', 'limit': '100'})
        self.assertEqual(ca['total'], 1392)

    def test_county_filter(self):
        atlantic = state_proceedings.listing({'county': 'Atlantic', 'system': 'NJ_MCL', 'limit': '100'})
        self.assertTrue(atlantic['total'] > 0)
        la = state_proceedings.listing({'county': 'Los Angeles', 'system': 'CA_JCCP', 'limit': '2000'})
        self.assertEqual(la['total'], 511)

    def test_category_filter(self):
        mass = state_proceedings.listing({'category': 'masstort_candidate', 'limit': '100'})
        self.assertEqual(mass['total'], 89)

    def test_archived_filter(self):
        archived = state_proceedings.listing({'archived': 'yes', 'limit': '100'})
        active = state_proceedings.listing({'archived': 'no', 'limit': '100'})
        self.assertEqual(archived['total'] + active['total'], 40)

    def test_has_related_mdl_filter(self):
        with_mdl = state_proceedings.listing({'has_related_mdl': 'yes', 'limit': '100'})
        # 13 njmcl_registry_suggestions.json entries parse to a distinct MDL number, but only some resolve
        # to njmcl nodes with unique matches: count is <= 13 and > 0.
        self.assertTrue(0 < with_mdl['total'] <= 13)
        for row in with_mdl['results']:
            self.assertTrue(row['id'].startswith('njmcl:'))

    def test_date_range_filter_ca_only(self):
        ranged = state_proceedings.listing({'dfrom': '2018-01-01', 'dto': '2018-12-31', 'limit': '2000'})
        self.assertTrue(ranged['total'] > 0)
        for row in ranged['results']:
            self.assertTrue(row['id'].startswith('jccp:'))

    def test_search(self):
        # Matches both the NJ MCL and a distinct CA JCCP proceeding that also names Abilify.
        result = state_proceedings.listing({'q': 'Abilify'})
        self.assertEqual(result['total'], 2)
        self.assertIn(NJ_ABILIFY, [r['id'] for r in result['results']])
        result = state_proceedings.listing({'q': 'Abilify', 'system': 'NJ_MCL'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], NJ_ABILIFY)

    def test_nj_detail_never_links_a_judge_entity(self):
        item = state_proceedings.detail(NJ_ABILIFY)
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'})
        self.assertNotIn('cl_person_id', item)
        self.assertNotIn('judge_entity_id', item)
        for label, value in item['facts']:
            self.assertNotIsInstance(value, dict)
        labels = [f[0] for f in item['facts']]
        self.assertIn('Presiding judge (as printed; text only, no entity link)', labels)

    def test_nj_detail_related_mdl_section(self):
        item = state_proceedings.detail('njmcl:roundup-products')
        section = [s for s in item['sections'] if s['heading'].startswith('Federal MDL numbers')][0]
        self.assertTrue(any(row[0] == '2741' for row in section.get('rows', [])))

    def test_ca_detail_drops_judges_field(self):
        item = state_proceedings.detail(CA_SAMPLE)
        blob = json.dumps(item)
        self.assertNotIn('Marie S. Wiener', blob)
        self.assertNotIn('Susan Irene', blob)

    def test_detail_unknown_id(self):
        self.assertIsNone(state_proceedings.detail('njmcl:does-not-exist'))
        self.assertIsNone(state_proceedings.detail('../validation.json'))

    def test_for_mdl_resolves_only_named_numbers(self):
        hit = state_proceedings.for_mdl(2741)
        self.assertIsNotNone(hit)
        self.assertTrue(any(r['id'] == 'njmcl:roundup-products' for r in hit['results']))
        self.assertIsNone(state_proceedings.for_mdl(9999999))
        self.assertIsNone(state_proceedings.for_mdl('not-a-number'))

    def test_for_state(self):
        nj = state_proceedings.for_state('NJ')
        self.assertEqual(nj['total'], 40)
        ca = state_proceedings.for_state('CA')
        self.assertEqual(ca['total'], 1392)
        self.assertIsNone(state_proceedings.for_state('TX'))
        self.assertIsNone(state_proceedings.for_state('nope'))

    def test_listing_titles_never_carry_judge_or_court_extraction_noise(self):
        noise_re = re.compile(r'\bLASC\b|\bOCSC\b|\bSF PJ\b|\bSac PJ\b|\bHon\.|\d{1,2}/\d{1,2}/\d{2,4}')
        result = state_proceedings.listing({'system': 'CA_JCCP', 'limit': '100'})
        blob = json.dumps([r['title'] for r in result['results']])
        self.assertIsNone(noise_re.search(blob))

    def test_search_haystack_uses_cleaned_title_not_raw_extraction_text(self):
        # 'LASC' is judge/court extraction noise cut out of jccp:4512's published title; it must not be a
        # searchable term via the free-text haystack.
        result = state_proceedings.listing({'q': 'LASC'})
        self.assertEqual(result['total'], 0)

    def test_ca_detail_exposes_title_raw_only_labelled_as_raw_extracted_text(self):
        item = state_proceedings.detail(CA_TITLE_NOISE_SAMPLE)
        self.assertEqual(item['title'], 'Electric Refund Cases')
        section = next(s for s in item['sections'] if s['heading'] == 'Raw extracted text, unedited')
        self.assertIn('LASC', section['text'])
        # the cleaned title never appears in a listing's search haystack together with the raw noise
        result = state_proceedings.listing({'system': 'CA_JCCP', 'limit': '2000'})
        listing_blob = json.dumps(result)
        self.assertNotIn('LASC MJ', listing_blob)

    def test_ca_detail_fallback_title_when_source_has_no_clean_case_name(self):
        item = state_proceedings.detail(CA_TITLE_FALLBACK_SAMPLE)
        self.assertEqual(item['title'], 'JCCP 4295 (no clean case name in the source)')
        section = next(s for s in item['sections'] if s['heading'] == 'Raw extracted text, unedited')
        self.assertIn('Petitioning', section['text'])

    def test_public_dicts_carry_no_filesystem_paths(self):
        blob = json.dumps([state_proceedings.listing({'limit': '100'}), state_proceedings.detail(NJ_ABILIFY),
                            state_proceedings.detail(CA_SAMPLE)])
        for needle in ('C:/', 'C:\\', 'Downloads/SW-BULK', 'Downloads/returnedfiles'):
            self.assertNotIn(needle, blob)


if __name__ == '__main__':
    unittest.main()
