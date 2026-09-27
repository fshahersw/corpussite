import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import judge_report_links as links


class ReportLinksTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.row={'entity_id':'judge-entity-test','title':'Motions','url':'https://trellis.law/judge-dashboard/test/motions/preview',
                  'source_url':'https://trellis.law/judge/test','kind':'dashboard_preview','publisher':'Trellis',
                  'availability':'publisher_link','numeric_analytics_saved':False}
        self.save()

    def tearDown(self):self.temp.cleanup()

    def save(self, status='passed'):
        data=(json.dumps(self.row)+'\n').encode();(self.folder/'links.jsonl').write_bytes(data)
        (self.folder/'validation.json').write_text(json.dumps({'status':status,'ready':True,'data_files':[{'path':'links.jsonl','sha256':hashlib.sha256(data).hexdigest()}]}))

    def test_exact_identity_only(self):
        self.assertEqual(len(links.references('judge-entity-test',self.folder)),1)
        self.assertEqual(links.references('Judge Test',self.folder),[])
        self.assertNotIn('entity_id',links.references('judge-entity-test',self.folder)[0])

    def test_changed_manifest_fails_closed_after_cached_read(self):
        self.assertEqual(len(links.references('judge-entity-test',self.folder)),1)
        with (self.folder/'links.jsonl').open('a') as f:f.write('{}\n')
        self.assertEqual(links.references('judge-entity-test',self.folder),[])

    def test_not_ready_returns_no_links(self):
        self.save('failed');self.assertEqual(links.references('judge-entity-test',self.folder),[])

    def test_analytic_flag_cannot_be_promoted(self):
        self.row['numeric_analytics_saved']=True;self.save()
        self.assertEqual(links.references('judge-entity-test',self.folder),[])


if __name__=='__main__':unittest.main()
