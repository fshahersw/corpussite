import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result
b = module('source_build', HERE/'build.py')
a = module('source_adapter', HERE.parents[1]/'delivery/archive-directory/source_directory.py')


class DirectoryTests(unittest.TestCase):
    def test_parenthesized_url(self):
        s = '## Rules\n### Court\n- [Order](https://example.gov/files/(order).pdf) [court_rules] - Verified through 2026-08-19'
        row = b.parse_markdown(s)[0]
        self.assertEqual(row['url'], 'https://example.gov/files/(order).pdf')
        self.assertEqual(row['section'], 'Rules')
        self.assertEqual(row['subsection'], 'Court')
        self.assertEqual(row['tags'], ['court_rules'])
        self.assertEqual(row['verbatim'], s.splitlines()[-1])
    def test_source_bad_parenthesis_preserved(self):
        row = b.parse_markdown('- [X](https://example.gov/?context=(open) [recheck]')[0]
        self.assertEqual(row['url'], 'https://example.gov/?context=(open')
    def test_full_reconciliation(self):
        d = a.listing()
        self.assertTrue(d['ready'])
        self.assertEqual(d['total'], 9348)
        self.assertEqual(d['summary']['historical_verified_claims'], 8349)
        self.assertEqual(d['summary']['freshly_verified'], 0)
        self.assertEqual(sum(x['count'] for x in d['facets']['jurisdictions']), 9348)
    def test_paging_list_params(self):
        first = a.listing({'limit':['2'],'page':['1']})
        second = a.listing({'limit':'2','page':'2'})
        self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
    def test_explicit_jurisdiction(self):
        d = a.listing({'jurisdiction':'ky', 'limit':'100'})
        self.assertTrue(d['items'])
        self.assertTrue(all(r['jurisdiction']=='ky' for r in d['items']))
        self.assertEqual(a.listing({'jurisdiction':'invented'})['total'],0)
        self.assertEqual(a.listing({'jurisdiction':'New York'})['total'], a.listing({'jurisdiction':'ny'})['total'])
        self.assertEqual(a.listing({'jurisdiction':'TX'})['total'], a.listing({'jurisdiction':'Texas'})['total'])
    def test_api_layers_distinct(self):
        self.assertEqual(a.listing({'access_method':'api'})['total'],16)
        self.assertEqual(a.listing({'api_bulk':'1'})['total'],62)
    def test_details_preserve_original_claim(self):
        entry = a.detail(a.listing({'q':'Current Federal Rules of Practice'})['items'][0]['id'])
        self.assertEqual(entry['original_category'], 'court_forms')
        self.assertIn('http_status',entry['source_record'])
        self.assertIn('source_as_of',entry['observations'][0])
        self.assertFalse(entry['has_saved_content'])
        self.assertEqual(entry['captures'],[])
        self.assertIsNone(entry['api_hint'])
    def test_query_is_literal(self):
        self.assertEqual(a.listing({'q':"' OR 1=1; --"})['total'],0)
        self.assertIsNone(a.detail('../catalog.json'))
    def test_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'catalog.json').write_text('{}')
            (p/'validation.json').write_text(json.dumps({'status':'passed','ready':True,'catalog_sha256':'0'*64}))
            with self.assertRaises(ValueError): a._load(p)
    def test_public_address_checks(self):
        for url in ('file:///etc/passwd','https://user:pass@example.org','http://localhost/a','http://127.0.0.1','http://10.0.0.1','http://server.local/a'):
            self.assertFalse(b.public_url(url),url)
        self.assertTrue(b.public_url('https://www.uscourts.gov/rules'))
    def test_saved_filter_before_paging(self):
        rows = a.listing({'limit':'6'})['items']
        saved = {r['url'] for r in rows[2:5]}
        saved.add('https://unlisted.example.gov/source')
        first = a.listing({'has':'saved','limit':'2','page':'1'}, saved_urls=saved)
        second = a.listing({'has':'saved','limit':'2','page':'2'}, saved_urls=saved)
        self.assertEqual(first['total'], 3)
        self.assertEqual([r['id'] for r in first['items'] + second['items']], [r['id'] for r in rows[2:5]])
        self.assertTrue(all(r['has_saved_content'] for r in first['items'] + second['items']))
        self.assertEqual(first['summary']['saved_content_records'], 3)
        self.assertEqual(first['facets']['availability'][0]['count'], 3)
    def test_links_only_and_no_cached_mutation(self):
        rows = a.listing({'limit':'2'})['items']
        saved = {rows[0]['url']}
        result = a.listing({'has':'links_only','limit':'2'}, saved_urls=saved)
        self.assertEqual(result['total'], 9347)
        self.assertNotIn(rows[0]['id'], [r['id'] for r in result['items']])
        self.assertFalse(any(r['has_saved_content'] for r in result['items']))
        self.assertEqual(a.listing()['summary']['saved_content_records'], 0)
        self.assertFalse(a.detail(rows[0]['id'])['has_saved_content'])
    def test_reference_type_filters_and_facets(self):
        facets = a.listing({'limit':'1'})['facets']
        for key in ('layers','task_families','content_kinds','source_types','access_requirements'):
            self.assertEqual(sum(x['count'] for x in facets[key]), 9348, key)
        self.assertEqual({x['value']:x['count'] for x in facets['content_kinds']}['pdf'], 1676)
        self.assertEqual(next(x for x in facets['task_families'] if x['value']=='complex-litigation')['label'], 'Complex litigation / MDL')
        self.assertEqual(next(x for x in facets['layers'] if x['value']=='mdl_practice')['label'], 'MDL practice')
        self.assertEqual(a.listing({'layer':'mdl_practice'})['total'], 15)
        self.assertEqual(a.listing({'task_family':'settlement-recovery'})['total'], 374)
        self.assertEqual(a.listing({'task_family':'unspecified'})['total'], 838)
        self.assertEqual(a.listing({'access_requirements':'fee'})['total'], 19)
        both = a.listing({'content_kind':'pdf','source_type':'official','limit':'100'})
        self.assertTrue(0 < both['total'] < 1676)
        self.assertTrue(all(r['content_kind']=='pdf' and r['source_type']=='official' for r in both['items']))
        self.assertEqual(a.listing({'layer':'invented'})['total'], 0)
        self.assertEqual(a.listing({'content_kind':"pdf' OR 1=1"})['total'], 0)
    def test_archive_links_are_a_distinct_availability(self):
        rows = a.listing({'limit':'4'})['items']
        archived = {rows[0]['url']: 3, rows[1]['url']: 1, 'https://unlisted.example.gov/x': 2}
        saved = {rows[1]['url']}
        result = a.listing({'has':'archived','limit':'10'}, saved_urls=saved, archive_counts=archived)
        self.assertEqual(result['total'], 2)
        self.assertEqual({r['id'] for r in result['items']}, {rows[0]['id'], rows[1]['id']})
        self.assertEqual(next(r for r in result['items'] if r['id']==rows[0]['id'])['archive_record_count'], 3)
        self.assertTrue(all(r['has_archive_records'] for r in result['items']))
        self.assertEqual(a.listing({'has':'saved'}, saved_urls=saved, archive_counts=archived)['total'], 1)
        self.assertEqual(a.listing({'has':'links_only'}, saved_urls=saved, archive_counts=archived)['total'], 9346)
        facets = {f['value']: f['count'] for f in result['facets']['availability']}
        self.assertEqual((facets['saved'], facets['archived'], facets['links_only']), (1, 2, 9346))
        self.assertEqual(result['summary']['archive_linked_records'], 2)
        plain = a.listing({'limit':'1'})['items'][0]
        self.assertFalse(plain['has_archive_records'])
        self.assertEqual(plain['archive_record_count'], 0)
        self.assertEqual(a.listing({'has':'archived'})['total'], 0)
    def test_exact_saved_url_and_qualified_absence(self):
        row = a.listing({'limit':'1'})['items'][0]
        result = a.listing({'has':'saved'}, saved_urls={row['url']+'#unverified-fragment'})
        self.assertEqual(result['total'], 0)
        self.assertIn('may have content elsewhere', result['summary']['qualification'])
        self.assertEqual(a.listing({'has':'saved'})['total'], 0)

if __name__=='__main__': unittest.main()
