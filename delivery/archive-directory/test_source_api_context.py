import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import source_api_context as api


class APIContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.row = {'registry_id': 'native1', 'url': 'https://example.gov/api',
                    'reference_class': 'api_documentation_or_service_landing',
                    'source_record': {'id':'native1','url':'https://example.gov/api'},
                    'source_citation': {'registry_id':'native1'},
                    'access_requirements': {'status':'unverified','source':'https://example.gov/api','note':'Unknown requirements'},
                    'requirements_reviewed_at': None, 'live_api_query_performed': False}
        self.write()
    def tearDown(self): self.temp.cleanup()
    def write(self, rows=None):
        rows = rows if rows is not None else [self.row]
        payload = '\n'.join(json.dumps(row) for row in rows).encode('utf-8')
        (self.folder/'api_sources.jsonl').write_bytes(payload)
        gate = {'mapping_sha256':hashlib.sha256(payload).hexdigest(),'mapped_references':len(rows),
                'classification_counts':dict(api.Counter(row['reference_class'] for row in rows)),
                'live_documentation_reviews':sum(bool(row['requirements_reviewed_at']) for row in rows),
                'api_calls_performed':0,'typed_api_rows':1,'api_bulk_layer_rows':0}
        (self.folder/'summary.json').write_text(json.dumps(gate))
    def test_exact_two_key_join(self):
        checked = api._load(self.folder)
        with patch.object(api,'_load',return_value=checked):
            self.assertIsNotNone(api.detail('native1','https://example.gov/api'))
            self.assertIsNone(api.detail('native1','https://example.gov/api/'))
            self.assertIsNone(api.detail('wrong','https://example.gov/api'))
            self.assertIsNone(api.detail('pld-native1','https://example.gov/api'))
    def test_unknown_fields_remain_unknown(self):
        with patch.object(api,'_load',return_value=api._load(self.folder)):
            d = api.detail('native1','https://example.gov/api')
            self.assertIsNone(d['access_requirements']['method'])
            self.assertIsNone(d['requirements_reviewed_at'])
            self.assertFalse(d['live_api_query_performed'])
    def test_changed_mapping_rejected(self):
        with (self.folder/'api_sources.jsonl').open('a') as stream: stream.write(' ')
        with self.assertRaises(ValueError): api._load(self.folder)
    def test_duplicate_identity_rejected(self):
        self.write([self.row,self.row])
        with self.assertRaises(ValueError): api._load(self.folder)
    def test_duplicate_url_rejected(self):
        other = copy.deepcopy(self.row)
        other['registry_id'] = other['source_record']['id'] = other['source_citation']['registry_id'] = 'native2'
        self.write([self.row,other])
        with self.assertRaises(ValueError): api._load(self.folder)
    def test_inner_source_mismatch_rejected(self):
        self.row['source_record']['url']='https://different.gov/api'
        self.write()
        with self.assertRaises(ValueError): api._load(self.folder)
    def test_count_mismatch_rejected(self):
        p=self.folder/'summary.json';gate=json.loads(p.read_text());gate['mapped_references']=2;p.write_text(json.dumps(gate))
        with self.assertRaises(ValueError): api._load(self.folder)
    def test_query_success_is_not_invented(self):
        self.row['live_api_query_performed']=True;self.write()
        with self.assertRaises(ValueError): api._load(self.folder)
    def test_production_counts(self):
        data=api.summary()
        self.assertTrue(data['ready'])
        self.assertEqual((data['mapped_references'],data['live_documentation_reviews'],data['typed_api_rows'],data['api_bulk_layer_rows']), (109,9,16,62))

if __name__=='__main__': unittest.main()
