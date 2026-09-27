import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('doj_resources_adapter', HERE / 'doj_resources.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

TEMPORAL = ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to')


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


class DojResourcesTests(unittest.TestCase):
    def test_states_cover_56_published_jurisdictions(self):
        d = a.states()
        self.assertTrue(d['ready'])
        self.assertEqual(d['total'], 56)
        by = {s['usps']: s for s in d['items']}
        self.assertEqual(len(by), 56)
        self.assertEqual((by['NJ']['label'], by['NJ']['state_fips'], by['NJ']['jurisdiction_class']), ('New Jersey', '34', 'state'))
        self.assertEqual(by['DC']['jurisdiction_class'], 'federal_district')
        self.assertEqual({u for u, s in by.items() if s['jurisdiction_class'] == 'territory'}, {'AS', 'GU', 'MP', 'PR', 'VI'})
        self.assertEqual(by['NJ']['resource_count'], 60)
        self.assertEqual(by['NJ']['page_updated_label'], 'January 6, 2026')
        self.assertEqual(sum(s['resource_count'] for s in d['items']), 3055)
        self.assertEqual(d['summary']['external_unique_urls_not_in_source_directory'], 200)
        self.assertEqual(d['summary']['source_directory_references'], 9348)
        self.assertTrue(all(s['capture_version_id'] and s['captured_at'] and s['capture_raw_sha256'] for s in d['items']))
        self.assertIn('not_checked', d['qualification'])

    def test_public_dicts_are_path_free(self):
        for payload in (a.states(), a.state_resources('TX', params={'limit': '100'}), a.circuits(), a.sections()):
            for key, value in walk(payload):
                self.assertNotIn('raw_path', key)
                if isinstance(value, str):
                    self.assertNotIn('sources/official_courts', value)
                    self.assertNotIn(':\\', value)
                    self.assertNotIn('Users/', value)

    def test_state_lookup_by_code_or_published_label(self):
        code = a.state_resources('NJ', params={'limit': '100'})
        self.assertTrue(code['found'])
        self.assertEqual(code['total'], 60)
        self.assertEqual(a.state_resources('nj')['total'], 60)
        self.assertEqual(a.state_resources('New Jersey')['total'], 60)
        self.assertEqual(a.state_resources(['new jersey'])['total'], 60)
        self.assertEqual([r['position'] for r in code['items']], sorted(r['position'] for r in code['items']))
        self.assertTrue(all(r['usps'] == 'NJ' and r['jurisdiction_label'] == 'New Jersey' for r in code['items']))
        self.assertEqual(code['state']['usps'], 'NJ')

    def test_section_filter_uses_published_labels(self):
        laws = a.state_resources('NJ', 'Legislature and Laws', {'limit': '100'})
        self.assertTrue(0 < laws['total'] < 60)
        self.assertTrue(all(r['section_h2'] == 'Legislature and Laws' for r in laws['items']))
        codes = a.state_resources('NJ', 'legislature and laws > laws, codes and statutes', {'limit': '100'})
        self.assertTrue(0 < codes['total'] < laws['total'])
        self.assertTrue(all(r['section_h3'] == 'Laws, Codes and Statutes' for r in codes['items']))
        self.assertTrue(any('lis.njleg.state.nj.us' in r['url'] for r in codes['items']))
        self.assertEqual(a.state_resources('NJ', params={'section': 'Legislature and Laws'})['total'], laws['total'])
        self.assertEqual(a.state_resources('NJ', 'Invented section')['total'], 0)
        self.assertEqual(sum(f['count'] for f in a.state_resources('NJ')['facets']['sections']), 60)

    def test_pagination_and_bounds(self):
        first = a.state_resources('TX', params={'limit': ['2'], 'page': ['1']})
        second = a.state_resources('TX', params={'limit': '2', 'page': '2'})
        self.assertEqual(first['total'], 82)
        self.assertEqual(len(first['items']), 2)
        self.assertFalse({r['record_id'] for r in first['items']} & {r['record_id'] for r in second['items']})
        self.assertEqual(a.state_resources('TX', params={'limit': '5000'})['limit'], 100)
        self.assertEqual(a.state_resources('TX', params={'limit': 'x', 'page': '-4'})['page'], 1)

    def test_unknown_or_hostile_state(self):
        for value in ('ZZ', '', None, "NJ' OR 1=1", '../resources.jsonl', 17):
            d = a.state_resources(value)
            self.assertTrue(d['ready'])
            self.assertFalse(d['found'])
            self.assertEqual((d['total'], d['items']), (0, []))

    def test_text_search_is_literal(self):
        d = a.state_resources('NJ', params={'q': 'attorney general', 'limit': '100'})
        self.assertTrue(0 < d['total'] < 60)
        self.assertEqual(a.state_resources('NJ', params={'q': "' OR 1=1; --"})['total'], 0)

    def test_links_missing_from_source_directory_are_flagged_only_here(self):
        missing = a.state_resources('MD', params={'in_directory': 'no', 'limit': '100'})
        self.assertTrue(missing['total'] >= 15)
        self.assertTrue(all(r['not_in_source_directory'] is True and r['directory_ref_ids'] == [] for r in missing['items']))
        self.assertEqual(len({r['url'] for r in missing['items']}), 15)
        present = a.state_resources('MD', params={'in_directory': 'yes', 'limit': '100'})
        self.assertTrue(all(r['directory_ref_ids'] and r['directory_match_basis'] != 'none' for r in present['items']))
        self.assertTrue(all(i.startswith('pld-') for r in present['items'] for i in r['directory_ref_ids']))

    def test_sections_facet(self):
        d = a.sections()
        self.assertTrue(d['ready'])
        self.assertEqual(sum(s['count'] for s in d['items']), 3055)
        agencies = next(s for s in d['items'] if s['value'] == 'State Agencies & Offices')
        self.assertEqual((agencies['count'], agencies['states']), (588, 53))
        courts = next(s for s in d['items'] if s['value'] == 'State and Local Courts')
        self.assertEqual(sum(x['count'] for x in courts['subsections']), courts['count'])
        self.assertIn('Supreme Court', {x['value'] for x in courts['subsections']})
        nj = a.sections('NJ')
        self.assertEqual(sum(s['count'] for s in nj['items']), 60)
        self.assertEqual(a.sections('ZZ')['items'], [])

    def test_circuits_as_published(self):
        d = a.circuits()
        self.assertTrue(d['ready'] and d['available'])
        self.assertEqual(len(d['items']), 13)
        third = next(c for c in d['items'] if c['circuit_label'] == 'Third Circuit')
        self.assertEqual(third['cl_court_id'], 'ca3')
        self.assertEqual([s['usps'] for s in third['states']], ['DE', 'NJ', 'PA', 'VI'])
        self.assertEqual(sum(len(c['states']) for c in d['items']), 55)
        self.assertEqual(d['capture_kind'], 'scout_capture_decoded_bytes')
        states = {s['usps']: s for s in a.states()['items']}
        self.assertEqual(states['NJ']['circuit']['circuit_label'], 'Third Circuit')
        self.assertIsNone(states['AS']['circuit'])
        # Publisher inconsistency is shown, never silently resolved.
        self.assertEqual(states['MI']['circuit']['circuit_label'], 'Sixth Circuit')
        self.assertIs(states['MI']['circuit']['state_page_agrees'], False)
        self.assertEqual(states['MI']['circuit']['state_page_circuit_labels'], ['8th Circuit'])
        self.assertTrue(any(u['usps'] == 'MI' for u in d['unresolved']))

    def test_federal_page_is_separate_from_states(self):
        d = a.state_resources('federal', params={'limit': '100'})
        self.assertTrue(d['found'])
        self.assertEqual(d['total'], 146)
        self.assertTrue(all(r['jurisdiction_class'] == 'federal' and r['usps'] is None for r in d['items']))
        self.assertTrue(all(r['capture_kind'] == 'scout_capture_decoded_bytes' and r['capture_version_id'] is None for r in d['items']))
        self.assertEqual(a.states()['federal_page']['resource_count'], 146)

    def test_temporal_block_and_provenance_on_every_item(self):
        for r in a.state_resources('GU', params={'limit': '100'})['items']:
            for key in TEMPORAL:
                self.assertIn(key, r)
                self.assertIn(key + '_basis', r)
            self.assertIsNone(r['published_at'])
            self.assertIsNone(r['effective_from'])
            self.assertTrue(r['captured_at'].startswith('2026-09-13'))
            self.assertEqual(r['link_status'], 'not_checked')
            self.assertEqual(len(r['capture_raw_sha256']), 64)

    def test_resource_detail_and_source_listings(self):
        row = a.state_resources('NJ', params={'q': 'judiciary.state.nj.us', 'limit': '1'})['items'][0]
        self.assertEqual(a.resource(row['record_id'])['url'], row['url'])
        for bad in ('../validation.json', 'slrm-', None, 5, 'slrm-zzzzzzzzzzzz'):
            self.assertIsNone(a.resource(bad))
        listings = a.source_listings(row['directory_ref_ids'][0])
        self.assertTrue(any(x['usps'] == 'NJ' and x['section_path'].startswith('State and Local Courts') for x in listings))
        self.assertEqual(a.source_listings('pld-000000000000'), [])
        self.assertEqual(a.source_listings("x' OR 1=1"), [])

    def test_edges_are_evidence_backed(self):
        d = a.edges({'relation': 'served_by_federal_circuit', 'limit': '100'})
        self.assertEqual(d['total'], 54)
        self.assertTrue(all(e['from']['id'].startswith('state:') and e['to']['id'].startswith('cl_court:ca') and e['evidence'] for e in d['items']))
        self.assertNotIn('state:MI', {e['from']['id'] for e in d['items']})
        listed = a.edges({'relation': 'listed_on_doj_state_resource_page', 'limit': '3'})
        self.assertTrue(listed['total'] > 2600)
        self.assertTrue(all(e['from']['id'].startswith('source:pld-') and e['evidence']['capture_raw_sha256'] for e in listed['items']))

    def test_hash_gate_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'copy'
            shutil.copytree(a.DATA, folder, ignore=shutil.ignore_patterns('__pycache__'))
            self.assertTrue(a._load(folder)['resources'])
            with open(folder / 'resources.jsonl', 'ab') as handle:
                handle.write(b'{"record_id": "slrm-injected"}\n')
            with self.assertRaises(ValueError):
                a._load(folder)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'copy'
            shutil.copytree(a.DATA, folder, ignore=shutil.ignore_patterns('__pycache__'))
            gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
            gate['ready'] = False
            (folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
            with self.assertRaises(ValueError):
                a._load(folder)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((OSError, ValueError)):
                a._load(Path(tmp))

    def test_unavailable_supplement_returns_empty_payloads(self):
        original = a.DATA
        try:
            a.DATA = Path(tempfile.gettempdir()) / 'doj-resource-map-does-not-exist'
            self.assertEqual(a.states()['ready'], False)
            self.assertEqual(a.states()['items'], [])
            self.assertEqual(a.state_resources('NJ')['items'], [])
            self.assertEqual(a.sections()['items'], [])
            self.assertEqual(a.circuits()['items'], [])
            self.assertIsNone(a.resource('slrm-2208d48519a7'))
            self.assertEqual(a.source_listings('pld-f8ed1066906e'), [])
            self.assertEqual(a.edges()['items'], [])
        finally:
            a.DATA = original


if __name__ == '__main__':
    unittest.main()
