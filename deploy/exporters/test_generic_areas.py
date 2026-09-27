import importlib.util
import pathlib
import tempfile
import types
import unittest

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('generic_areas',HERE/'generic_areas.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)


class GenericExportTests(unittest.TestCase):
    def test_native_values_are_not_rendered_labels(self):
        f=g.native_filters('court_documents',{'doc_type':'local_rule','extension':'pdf','state':'CA','court_id':'cacd'}, {'cells':{'doc_type':'Local rule'}})
        self.assertEqual(f['doc_type'],['local_rule']);self.assertEqual(f['court'],['cacd'])

    def test_cpsc_product_membership_includes_all_three_codes(self):
        f=g.native_filters('cpsc_injury_data',{'product_1_code':12,'product_2_code':34,'product_3_code':None,'sex_code':0},{},'neiss')
        self.assertEqual(f['product'],['12','34']);self.assertEqual(f['sex'],['0']);self.assertEqual(f['dataset'],['neiss'])

    def test_categories_use_semantics_not_file_extension(self):
        self.assertFalse(g.is_categorized('court_documents',{'doc_type':'other_unknown','extension':'pdf'}))
        self.assertFalse(g.is_categorized('url_directory',{'doc_kind':'pdf','content_type':None,'is_noise':0}))
        self.assertFalse(g.is_categorized('mdl_docket_documents',{'doc_type':'other'}))
        self.assertTrue(g.is_categorized('mdl_docket_documents',{'doc_type':'pretrial_order'}))
        self.assertTrue(g.is_categorized('url_directory',{'doc_kind':'page','content_type':'local_rules','is_noise':0}))

    def test_missing_optional_filter_values_do_not_become_unknown_categories(self):
        self.assertTrue(g.is_categorized('court_documents',{'doc_type':'local_rule','state':None}))
        self.assertEqual(g.native_filters('court_documents',{'doc_type':'local_rule','state':None},{})['state'],[])

    def test_source_text_and_native_date_are_preserved(self):
        f=g.native_filters('settlements',{'claim_deadline':'2026-09-30'},{})
        self.assertEqual(f['date'],'2026-09-30')
        r=g.record('saved_pages',{'id':'1','title':'Title'}, {'sections':[]},{'_full_text':'full source body'},f,2,[])
        self.assertEqual(r['text'],'full source body');self.assertEqual(r['filters']['_listing'],['yes'])

    def test_native_integer_expansion_restores_function(self):
        mod=types.SimpleNamespace(_integer=lambda p,k,default,hi:min(hi,int(p.get(k,default))))
        original=mod._integer
        with g.expanded_listing(mod): self.assertEqual(mod._integer({'limit':900000000},'limit',25,100),900000000)
        self.assertIs(mod._integer,original)

    def test_privacy_restricted_docket_search_does_not_index_caption(self):
        r=g.record('mdl_docket_documents',{'id':'1','title':'Docket'}, {'sections':[{'text':'Personal plaintiff name'}]},
                   {'docket_number':'123','court':'cacd','mdl_number':123,'mdl_title':'Products','doc_type':'motion'}, {},1,[])
        self.assertNotIn('Personal plaintiff',r['text']);self.assertIn('cacd',r['text'])


if __name__=='__main__': unittest.main()
