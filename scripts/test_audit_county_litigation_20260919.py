"""Adversarial fixtures for the independent publication audit."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from audit_county_litigation_20260919 import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        (self.folder/'body.txt').write_text('Local civil filing instructions from the county court.\n',encoding='utf-8')
        self.bodyhash=hashlib.sha256((self.folder/'body.txt').read_bytes()).hexdigest()
        self.row={'id':'test-court-resource','title':'Civil filing guide','source_url':'https://www.court.example.gov/filing',
            'state':'AL','county_fips':'01001','county_geoids':['01001'],'resource_kind':'filing_guidance','text_path':'body.txt',
            'metadata':{'resource_type':'filing_guidance','classification':{'status':'structural','evidence':['Civil filing guide']},
                'applicability':{'level':'county','status':'candidate','evidence':[]},'source_authority':{'verified':False},
                'temporal':{'captured_at':'2026-09-19','effective_at':None},
                'artifacts':[{'path':'body.txt','sha256':self.bodyhash,'mime':'text/plain','role':'text'}]}}
        self.publish()

    def tearDown(self):self.tmp.cleanup()

    def publish(self):
        (self.folder/'resources.jsonl').write_text(json.dumps(self.row)+'\n',encoding='utf-8')
        files=[{'path':name,'sha256':hashlib.sha256((self.folder/name).read_bytes()).hexdigest()} for name in ['resources.jsonl','body.txt']]
        (self.folder/'validation.json').write_text(json.dumps({'status':'passed','ready':True,'data_files':files}),encoding='utf-8')

    def codes(self):return {r['code'] for r in audit(self.folder)['errors']}

    def test_candidate_scope_is_allowed_without_claiming_verification(self):
        self.assertEqual(audit(self.folder)['status'],'passed')

    def test_changed_saved_bytes_fail(self):
        (self.folder/'body.txt').write_text('unexpected changed evidence',encoding='utf-8')
        self.assertIn('gate_hash_mismatch',self.codes())

    def test_wrong_state_for_county_fails(self):
        self.row['state']='CA';self.publish()
        self.assertIn('county_state_mismatch',self.codes())

    def test_verified_authority_requires_evidence(self):
        self.row['metadata']['source_authority']={'verified':True};self.publish()
        self.assertIn('unsupported_verified_authority',self.codes())

    def test_artifact_cannot_escape_gate(self):
        self.row['metadata']['artifacts'][0]['path']='../secret.txt';self.publish()
        self.assertIn('artifact_not_bound_to_gate',self.codes())

    def test_retrieval_date_does_not_become_effective_date(self):
        self.row['metadata']['temporal']['effective_at']='2026-09-19';self.publish()
        self.assertIn('date_without_source_basis',self.codes())

    def test_transitively_bound_artifact_is_verified_and_tampering_fails(self):
        self.row['metadata']['artifacts']=[];self.row['text_sha256']=self.bodyhash
        (self.folder/'resources.jsonl').write_text(json.dumps(self.row)+'\n',encoding='utf-8')
        artifact={'path':'body.txt','sha256':self.bodyhash,'bytes':(self.folder/'body.txt').stat().st_size,'mime_type':'text/plain'}
        (self.folder/'artifacts.jsonl').write_text(json.dumps(artifact)+'\n',encoding='utf-8')
        files=[{'path':name,'sha256':hashlib.sha256((self.folder/name).read_bytes()).hexdigest()} for name in ['resources.jsonl','artifacts.jsonl']]
        (self.folder/'validation.json').write_text(json.dumps({'status':'passed','ready':True,'data_files':files}),encoding='utf-8')
        self.assertEqual(audit(self.folder)['status'],'passed')
        (self.folder/'body.txt').write_text('changed saved bytes',encoding='utf-8')
        self.assertIn('indexed_artifact_hash_mismatch',self.codes())

    def test_extracted_fact_must_quote_saved_text(self):
        self.row['metadata']['facts']=[{'field':'filing','source_excerpt':'Invented requirement'}];self.publish()
        self.assertIn('fact_excerpt_not_in_reading_copy',self.codes())

    def test_structure_evidence_is_bound_to_exact_slice(self):
        self.row['metadata']['document_structure']={'text_sha256':self.bodyhash,'outline':[{'evidence':{'start':0,'end':5,'excerpt':'Wrong'}}]};self.publish()
        self.assertIn('structure_evidence_slice_mismatch',self.codes())


if __name__=='__main__':unittest.main()
