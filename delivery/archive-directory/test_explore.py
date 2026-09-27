"""Navigation counts and availability must agree with the real browse predicates."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import explore
import bulk_laws
import categories


class LocalFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='explore-test-');self.root=Path(self.tmp.name)
        self.db=self.root/'directory.sqlite3';self.catalog=self.root/'catalog.sqlite3'
        c=sqlite3.connect(self.catalog);c.executescript("CREATE TABLE versions(content_id INTEGER,index_text_status TEXT);INSERT INTO versions VALUES(1,'searchable'),(2,'empty_text');CREATE INDEX versions_content ON versions(content_id);");c.close()
        c=sqlite3.connect(self.db)
        c.executescript('''CREATE TABLE browse(id TEXT,dataset TEXT,state TEXT,kind TEXT,eligible INT,content_id INT,inline_text INT,original_id TEXT,source_url TEXT);
          INSERT INTO browse VALUES('rich','focused','Pennsylvania','court_rule_or_order',1,1,0,'file','https://official/rule');
          INSERT INTO browse VALUES('link','federal','Pennsylvania','federal_order_document_link',1,NULL,0,NULL,'https://official/rule');
          INSERT INTO browse VALUES('only-link','focused','Kentucky','court_rule_or_order',1,NULL,0,NULL,'https://official/link');
          INSERT INTO browse VALUES('empty','focused','Pennsylvania','court_rule_or_order',1,2,0,'file2','https://official/empty');
          INSERT INTO browse VALUES('ineligible','focused','Pennsylvania','administrative_document',0,NULL,1,NULL,'https://official/admin');
          CREATE TABLE display_members(record_id TEXT,display_id TEXT);
          INSERT INTO display_members VALUES('rich','g1'),('link','g1'),('only-link','g2'),('empty','g3'),('ineligible','g4');
          CREATE TABLE display_groups(id TEXT,preferred_id TEXT);
          INSERT INTO display_groups VALUES('g1','rich'),('g2','only-link'),('g3','empty'),('g4','ineligible');
          CREATE TABLE record_groups(record_id TEXT,group_name TEXT);
          INSERT INTO record_groups VALUES('rich','laws'),('link','laws'),('only-link','laws'),('empty','laws'),('ineligible','laws');
          CREATE TABLE settings(key TEXT,payload TEXT);
          INSERT INTO settings VALUES('summary','{"datasets":[{"id":"focused","title":"Saved rules"}],"directory_built_at":"2026-09-19T00:00:00Z","published":{"completed_at":"2026-09-18T00:00:00Z"}}');''')
        c.close();self.patches=patch.multiple(explore,DB=self.db,CATALOG=self.catalog);self.patches.start()
        self.bulk_patch=patch.object(bulk_laws,'info',return_value={'ready':False});self.bulk_patch.start()
        self.states_patch=patch.object(bulk_laws,'state_names',return_value={'PA':'Pennsylvania','KY':'Kentucky'});self.states_patch.start()
        explore._local_rows.cache_clear();explore._bulk_rows.cache_clear()

    def tearDown(self):
        self.patches.stop();self.bulk_patch.stop();self.states_patch.stop();explore._local_rows.cache_clear();explore._bulk_rows.cache_clear();self.tmp.cleanup()

    def test_group_counts_deduplicate_retained_members(self):
        data=explore.summary({'group':'laws'})
        self.assertEqual(data['totals']['local_documents'],3)
        self.assertEqual(data['totals']['local_source_observations'],4)
        self.assertEqual(data['totals']['bulk_records'],0)

    def test_state_category_and_unknown_group_are_scoped(self):
        data=explore.summary({'group':'laws','state':'Pennsylvania','category':'rules'})
        self.assertEqual(data['totals']['local_documents'],2)
        self.assertEqual(explore.summary({'group':'not-a-group'})['totals']['total'],0)
        self.assertEqual(explore.summary({'group':'laws','state':'Unknown'})['totals']['total'],0)

    def test_group_availability_uses_preferred_display_not_link_member(self):
        # Matching dataset=federal retains g1, but it displays the preserved rich rule.
        self.assertEqual(explore.summary({'group':'laws','dataset':'federal','availability':'text'})['totals']['total'],1)
        self.assertEqual(explore.summary({'group':'laws','dataset':'federal','availability':'link_only'})['totals']['total'],0)

    def test_empty_content_reference_never_claims_saved_text(self):
        data=explore.summary({'group':'laws','state':'Pennsylvania','availability':'text'})
        self.assertEqual(data['totals']['total'],1)
        self.assertEqual(explore.summary({'group':'laws','availability':'original'})['totals']['total'],2)
        self.assertEqual(explore.summary({'group':'laws','availability':'link_only'})['totals']['total'],1)
        self.assertEqual(explore.summary({'group':'laws','availability':'unknown'})['totals']['total'],0)

    def test_shared_sql_predicates_match_hub_counts(self):
        with explore.connect() as c:
            for choice in explore.AVAILABILITY:
                actual=c.execute('SELECT count(*) FROM display_groups g JOIN browse p ON p.id=g.preferred_id WHERE p.eligible!=0 AND '+explore.availability_condition(choice,'p')).fetchone()[0]
                self.assertEqual(actual,explore.summary({'group':'laws','availability':choice})['totals']['total'])
        with self.assertRaises(ValueError):explore.local_text_sql('r); DROP TABLE browse;--')

    def test_unknown_legal_asof_does_not_become_publication_date(self):
        data=explore.summary({'group':'laws','state':'Pennsylvania'})
        for dataset in data['datasets']:
            self.assertIsNone(dataset['source_as_of']);self.assertIn('no uniform',dataset['source_as_of_label'])
            self.assertEqual(dataset['indexed_at'],'2026-09-19T00:00:00Z')
            if dataset['id']=='focused':self.assertEqual(dataset['published_at'],'2026-09-18T00:00:00Z')
            else:self.assertIsNone(dataset['published_at'])


class Taxonomy(unittest.TestCase):
    def test_known_guidance_and_orders_remain_distinct_from_unknown_legal_fragments(self):
        self.assertEqual(categories.classify('administrative_guidance'),'guidance')
        self.assertEqual(categories.classify('executive_order'),'rules')
        self.assertEqual(categories.classify('legal_inventory_navigation'),'directories')
        self.assertEqual(categories.classify('law_document_title_evidence_needs_review'),'other')
        self.assertEqual(categories.classify('historical_repeal_notice'),'other')


class RealCatalogReadOnly(unittest.TestCase):
    def test_bulk_availability_count_and_returned_item_flags(self):
        base={'group':'laws','dataset':'open_us_law','state':'New York','category':'statutes'}
        original=bulk_laws.count(base)
        self.assertEqual(original,40140)
        for choice in ('text','original'):
            params=base|{'availability':choice}
            self.assertEqual(bulk_laws.count(params),original)
            rows=bulk_laws.query(params,0,2)
            self.assertTrue(rows)
            self.assertTrue(all(r['has_text' if choice=='text' else 'has_original'] for r in rows))
        self.assertEqual(bulk_laws.count(base|{'availability':'link_only'}),0)
        self.assertEqual(bulk_laws.query(base|{'availability':'link_only'},0,2),[])

    def test_hub_state_category_counts_match_integrated_query(self):
        import server
        for state in ('Pennsylvania','Kentucky'):
            hub=explore.summary({'group':'laws','state':state})
            for category in ('rules','statutes'):
                facet=next(r for r in hub['categories'] if r['id']==category)
                actual=server.query_documents({'group':'laws','state':state,'category':category,'limit':'1'})
                self.assertEqual(facet['total'],actual['total'])

    def test_availability_counts_match_query_and_cards(self):
        import server
        for choice in explore.AVAILABILITY:
            params={'group':'laws','state':'Pennsylvania','category':'rules','availability':choice}
            hub=explore.summary(params);actual=server.query_documents(params|{'limit':'10'})
            self.assertEqual(hub['totals']['total'],actual['total'])
            for row in actual['items']:
                if choice=='text':self.assertTrue(row['has_text'])
                elif choice=='original':self.assertTrue(row['has_original'])
                else:self.assertFalse(row['has_text']);self.assertFalse(row['has_original']);self.assertTrue(row['source_url'])


if __name__=='__main__':unittest.main(verbosity=2)
