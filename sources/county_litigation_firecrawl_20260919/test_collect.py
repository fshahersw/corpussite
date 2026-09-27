import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import sqlite3
import json
import collect as c

class CollectionGates(unittest.TestCase):
    def seed(self):
        return {'url':'https://court.example/rules','county_geoids':['06059'],'association':{'status':'observed'},'source_authority':{'class':'official_court'},'allowed_hosts':['court.example'],'depth':0}
    def good(self):
        return {'markdown':'# Local Rules\n'+('Civil filing instructions and local court rules. '*5),'rawHtml':'<main><h1>Local Rules</h1></main>','metadata':{'title':'Local Rules','statusCode':200,'sourceURL':'https://court.example/rules'}}
    def test_county_identity_required(self):
        s=self.seed();s['county_geoids']=[]
        with self.assertRaises(ValueError):c.seed_ok(s)
    def test_local_and_credentials_urls_rejected(self):
        for url in ['http://127.0.0.1/foo','https://a:b@court.example/rules','file:///x','https://court.example/?action=delete','https://court.example/login']:
            with self.assertRaises(ValueError):c.safe_url(url)
    def test_http_literal_preserved(self):
        self.assertEqual(c.safe_url('http://court.example/rules'),'http://court.example/rules')
    def test_depth_bound(self):
        s=self.seed();s['depth']=3
        with self.assertRaises(ValueError):c.seed_ok(s)
    def test_relevant_link_exact_host_only(self):
        s=self.seed()
        self.assertTrue(c.link_allowed('https://court.example/forms/claim.pdf','Civil claim',s))
        self.assertFalse(c.link_allowed('https://unreviewed.example/forms.pdf','Court forms',s))
        self.assertFalse(c.link_allowed('https://court.example/news/court-rules','Court rules',s))
        self.assertFalse(c.link_allowed('https://court.example/login','Court forms',s))
    def test_soft404_and_challenge(self):
        for title in ['Page not found','Access Denied','Just a moment...']:
            d=self.good();d['metadata']['title']=title
            with self.assertRaises(ValueError):c.quality(d,self.seed()['url'],['court.example'])
    def test_bad_status_and_cross_host_redirect(self):
        d=self.good();d['metadata']['statusCode']=403
        with self.assertRaises(ValueError):c.quality(d,self.seed()['url'],['court.example'])
        d=self.good();d['metadata']['sourceURL']='https://unreviewed.example/rules'
        with self.assertRaises(ValueError):c.quality(d,self.seed()['url'],['court.example'])
    def test_missing_html_and_empty_markdown(self):
        for key,value in [('rawHtml',''),('markdown','  ')]:
            d=self.good();d[key]=value
            with self.assertRaises(ValueError):c.quality(d,self.seed()['url'],['court.example'])
    def test_good_page_preserves_provenance(self):
        title,url,md,html,meta=c.quality(self.good(),self.seed()['url'],['court.example'])
        self.assertEqual(title,'Local Rules');self.assertEqual(meta['statusCode'],200)
    def test_provider_final_url_wins_over_requested_url(self):
        d=self.good();d['metadata']['sourceURL']='http://court.example/old';d['metadata']['url']='https://court.example/new'
        self.assertEqual(c.quality(d,'http://court.example/old',['court.example'])[1],'https://court.example/new')

class DurableDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='collector-test-',dir=c.HERE)
        self.root=Path(self.temp.name).resolve();self.assertTrue(self.root.is_relative_to(c.HERE))
        self.r=c.Runner.__new__(c.Runner);self.r.db=sqlite3.connect(':memory:');self.r.db.row_factory=sqlite3.Row
        self.r.db.execute('CREATE TABLE queue(url TEXT PRIMARY KEY,seed_json TEXT,status TEXT,resource_json TEXT,error TEXT,created_at TEXT,updated_at TEXT,priority INTEGER,county_key TEXT,discovered INTEGER)')
        self.r.max_frontier=10;self.r.per_county=10;self.r.existing_urls=set()
        class Classifier:
            def classify_link(self,label,url):return {'eligible':'form' in label.lower(),'resource_type':'court_form','priority':2}
        self.r.classifier=Classifier()
    def tearDown(self):self.r.db.close();self.temp.cleanup()
    def item(self,body,depth=1):
        path=self.root/'page.html';path.write_text(body,encoding='utf-8')
        return {'id':'parent','source_url':'https://court.example/forms','final_url':'https://court.example/forms','title':'Court forms','html_path':c.rel(path),'html_sha256':c.sha(path.read_bytes()),'source_authority':{'verified':True,'class':'official_court'},'raw_sha256':'a'*64,'county_geoids':['06059'],'association':{'status':'exact'},'classification':{'document_shape':'form_directory'},'kind':'court_form','seed_provenance':{'url':'https://court.example/forms','county_geoids':['06059'],'depth':depth,'state':'CA','county':'Orange','association':{'status':'exact'}}}
    def test_exact_links_external_rejected_and_context_pdf_admitted(self):
        item=self.item('<a href="/files/one.pdf">Blank petition</a><a href="https://other.example/forms.pdf">Form</a><a href="/news/court-form">Form</a>')
        self.assertEqual(self.r.discover(item),1)
        row=self.r.db.execute('select * from queue').fetchone();seed=json.loads(row['seed_json'])
        self.assertEqual(row['url'],'https://court.example/files/one.pdf');self.assertEqual(seed['depth'],2)
        self.assertEqual(seed['parent_raw_sha256'],item['html_sha256']);self.assertEqual(seed['county_geoids'],['06059'])
        self.assertEqual(self.r.discover(item),0)
    def test_depth_and_authority_fail_closed(self):
        item=self.item('<a href="/one.pdf">Form</a>',depth=2)
        self.assertEqual(self.r.discover(item),0)
        item['seed_provenance']['depth']=1;item['source_authority']['verified']=False
        self.assertEqual(self.r.discover(item),0)
    def test_county_cap_and_existing_capture_dedup(self):
        item=self.item(''.join(f'<a href="/{n}.pdf">Form</a>' for n in range(10)))
        self.r.per_county=2;self.r.existing_urls={'https://court.example/0.pdf'}
        self.assertEqual(self.r.discover(item),2)
        self.assertEqual(self.r.db.execute("select count(*) from queue where status='already_published'").fetchone()[0],1)
    def test_tampered_parent_fails(self):
        item=self.item('<a href="/a.pdf">Form</a>');(c.ROOT/item['html_path']).write_text('changed')
        with self.assertRaises(ValueError):self.r.discover(item)

if __name__=='__main__':unittest.main()
