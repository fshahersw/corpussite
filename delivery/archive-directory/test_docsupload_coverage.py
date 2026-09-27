"""Crosswalk integrity, identity reuse, filtering and fail-closed regression checks."""
import hashlib, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import docsupload_coverage as m

class CoverageTests(unittest.TestCase):
    def test_real_counts_and_filters(self):
        response = m.listing({})
        self.assertTrue(response['available']); self.assertEqual(response['total'], 270)
        self.assertEqual(m.listing({'mode': 'documents'})['total'], 11181)
        self.assertEqual(m.listing({'collection': 'LC:la_orleans'})['total'], 5)
        self.assertEqual(m.listing({'jurisdiction': 'Montana'})['total'], 1)
        self.assertEqual(m.listing({'collection': 'missing'})['total'], 0)
        self.assertEqual(m.listing({'availability': 'saved'})['total'], 251)

    def test_existing_documents_have_real_ids_and_dates(self):
        row = m.listing({'collection': 'LC:la_orleans'})['results'][0]
        detail = m.detail(row['id'])
        self.assertRegex(detail['links'][0]['url'], r'^#record/[a-f0-9]{32}$')
        self.assertTrue(any(x[0] == 'Source date' for x in detail['facts']))
        self.assertTrue(any(x[0] == 'Library snapshot' for x in detail['facts']))
        self.assertIsNone(m.original(row['id']))

    def test_no_private_paths_and_invalid_ids(self):
        data = m.listing({'q': 'Orleans'})
        text = json.dumps(m.detail(data['results'][0]['id']))
        self.assertNotIn('C:/', text); self.assertNotIn('C:\\', text)
        self.assertNotIn('Downloads', text); self.assertNotIn('raw_path', text)
        self.assertIsNone(m.detail('../../.auth'))

    def test_count_discrepancy_is_explicit(self):
        result = m.listing({'q': 'Eighth Circuit'})['results'][0]
        detail = m.detail(result['id'])
        self.assertTrue(any(s['heading'] == 'Count distinction' for s in detail['sections']))

    def test_tamper_and_traversal_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            court = {'id': 'coverage_' + 'a'*20, 'title': 'Test'}
            a = (json.dumps(court)+'\n').encode(); b = b''
            (folder/'courts.jsonl').write_bytes(a); (folder/'documents.jsonl').write_bytes(b)
            gate = {'status':'passed','ready':True,'source_as_of':'2026-09-13','qualification':'test','counts':{},'data_files':[
                {'path':'courts.jsonl','sha256':hashlib.sha256(a).hexdigest(),'rows':1},
                {'path':'documents.jsonl','sha256':hashlib.sha256(b).hexdigest(),'rows':0}]}
            (folder/'validation.json').write_text(json.dumps(gate))
            with patch.object(m,'DATA',folder):
                self.assertEqual(len(m._load()['courts']),1)
                (folder/'courts.jsonl').write_bytes(a+b' ')
                self.assertFalse(m.listing({})['available'])
                gate['data_files'][0]['path']='../courts.jsonl'
                (folder/'validation.json').write_text(json.dumps(gate))
                self.assertFalse(m.listing({})['available'])

if __name__ == '__main__': unittest.main()
