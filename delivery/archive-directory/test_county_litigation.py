import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import county_litigation as mod

class CountyLitigationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'assets').mkdir();(self.root/'text').mkdir()
        self.raw=self.root/'assets/rule.pdf';self.raw.write_bytes(b'%PDF-1.4\nRule source')
        self.text=self.root/'text/rule.txt';self.text.write_text('Rule 1. A source-specific requirement.',encoding='utf-8')
        self.artifacts=[{'path':p.relative_to(self.root).as_posix(),'sha256':mod.digest(p.read_bytes()),'bytes':p.stat().st_size,'mime_type':mime,'role':role} for p,mime,role in [(self.raw,'application/pdf','original'),(self.text,'text/plain','text')]]
        self.rows=[{'id':'county-litigation:fixture','title':'Local rule (draft)','source_url':'https://court.example.gov/rule.pdf','state':'California','county':'Orange County','county_fips':'06059','county_geoids':['06059'],'resource_kind':'local_rule','raw_path':'assets/rule.pdf','text_path':'text/rule.txt','sha256':self.artifacts[0]['sha256'],'text_sha256':self.artifacts[1]['sha256'],'captured_at':'2026-09-19T10:00:00Z','metadata':{'availability':'saved','legal_status':'draft','document_shape':'rule_body','applicability':{'status':'unknown','note':'County association does not establish applicability'},'temporal':{'effective_at':None}}}]
        self.publish()
    def publish(self,ready=True):
        pins=[]
        for name,rows in [('resources.jsonl',self.rows),('artifacts.jsonl',self.artifacts)]:
            data=''.join(json.dumps(r)+'\n' for r in rows).encode();(self.root/name).write_bytes(data);pins.append({'path':name,'sha256':mod.digest(data),'rows':len(rows)})
        (self.root/'validation.json').write_text(json.dumps({'ready':ready,'status':'passed','data_files':pins}),encoding='utf-8')
    def test_real_detail_filters_and_exact_bytes(self):
        self.assertEqual(mod.query({'geoid':'06059'},self.root)['total'],1)
        self.assertEqual(mod.query({'geoid':'06060'},self.root)['total'],0)
        self.assertEqual(mod.query({'resource_type':'court_form'},self.root)['total'],0)
        detail=mod.detail(self.rows[0]['id'],self.root)
        self.assertEqual(detail['legal_status'],'draft');self.assertIsNone(detail['effective_date'])
        self.assertEqual(detail['applicability']['status'],'unknown')
        self.assertEqual(mod.asset(self.rows[0]['id'],'original',self.root)[0],self.raw.read_bytes())
    def test_unready_and_modified_receipt_close_gate(self):
        self.publish(False);self.assertFalse(mod.query({},self.root)['available'])
        self.publish();(self.root/'resources.jsonl').write_text('{}\n');self.assertIsNone(mod.detail(self.rows[0]['id'],self.root))
    def test_tampered_artifact_cannot_be_served_after_cache(self):
        self.assertIsNotNone(mod.asset(self.rows[0]['id'],'original',self.root));self.raw.write_bytes(b'altered')
        self.assertIsNone(mod.asset(self.rows[0]['id'],'original',self.root))
    def test_artifact_paths_confined(self):
        self.artifacts[0]['path']='../secret.txt';self.rows[0]['raw_path']='../secret.txt';self.publish()
        self.assertFalse(mod.query({},self.root)['available'])
    def test_directory_optin_link_only_and_needs_review(self):
        self.rows.append({'id':'county-litigation:directory','title':'Directory','source_url':'https://court.example.gov','county_geoids':['06059'],'resource_kind':'source_directory','metadata':{'availability':'linked'}})
        self.rows.append({'id':'county-litigation:review','title':'Uncertain contact','source_url':'https://court.example.gov/contact','county_geoids':['06059'],'resource_kind':'court_contact','metadata':{'availability':'needs_review'}});self.publish()
        self.assertEqual(mod.query({},self.root)['total'],2)
        self.assertEqual(mod.query({'include_directories':'1'},self.root)['total'],3)
        self.assertEqual(mod.query({'resource_type':'source_directory','availability':'linked'},self.root)['total'],1)
        self.assertEqual(mod.query({'availability':'needs_review'},self.root)['total'],1)
        self.assertFalse(mod.detail('county-litigation:directory',self.root)['has_original'])
    def test_canonical_authority_and_private_paths(self):
        self.rows[0]['metadata']['source_authority']={'class':'official_court','evidence':{'local_path':'C:/private/raw.pdf','note':'Source: C:/private/raw.pdf'}}
        self.rows[0]['metadata']['classification']={'evidence_path':'sources/private.json','note':'Source heading'}
        self.publish();detail=mod.detail(self.rows[0]['id'],self.root)
        self.assertEqual(detail['authority']['class'],'official_court')
        self.assertNotIn('C:/private',json.dumps(detail));self.assertNotIn('evidence_path',json.dumps(detail))
        self.assertEqual(mod._public_evidence('https://court.example.gov/rules'),'https://court.example.gov/rules')
    def test_blank_text_keeps_original_without_readable_claim(self):
        self.text.write_text('  \n',encoding='utf-8');self.artifacts[1]['sha256']=mod.digest(self.text.read_bytes());self.artifacts[1]['bytes']=self.text.stat().st_size
        self.rows[0]['text_sha256']=self.artifacts[1]['sha256'];self.publish()
        detail=mod.detail(self.rows[0]['id'],self.root)
        self.assertTrue(detail['has_original']);self.assertFalse(detail['has_text']);self.assertIsNone(detail['text_url'])

if __name__=='__main__':unittest.main()
