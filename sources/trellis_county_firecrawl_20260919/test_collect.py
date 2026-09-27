"""Offline parser/barrier checks; no provider requests or credential reads."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('county_collect',HERE/'collect.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
URL='https://trellis.law/coverage/georgia/johnson'

class CollectorChecks(unittest.TestCase):
    def test_saved_pilot_structure(self):
        data=json.loads((HERE/'raw/georgia_johnson.json').read_text(encoding='utf-8'))
        p=worker.ProfileParser();p.feed(data['html'])
        self.assertEqual(p.heading,'Johnson County Superior Courts Records')
        self.assertEqual(p.fields['population'],'9,189')
        self.assertEqual(p.fields['county_seat'],'Wrightsville')
        self.assertEqual(p.website,'https://www.johnsonco.org/')

    def test_exact_website_anchor_only(self):
        p=worker.ProfileParser();p.feed('<h2>Phone Number</h2><h3><a href="https://example.org/">wrong field</a></h3><h2>Website</h2><h3><a href="javascript:alert(1)">bad URL</a></h3>')
        self.assertIsNone(p.website)

    def test_duplicate_field_not_overwritten(self):
        p=worker.ProfileParser();p.feed('<h2>Administration Address</h2><h3>First<br>Street</h3><h2>Administration Address</h2><h3>Second</h3>')
        self.assertEqual(p.fields['administration_address'],'First Street')

    def reject(self,metadata,markdown='',expected=RuntimeError):
        with tempfile.TemporaryDirectory(prefix='offline-test-',dir=HERE) as name:
            p=Path(name)/'response.json';p.write_text(json.dumps({'metadata':metadata,'markdown':markdown}),encoding='utf-8')
            with self.assertRaises(expected):worker.normalize(p,URL,'2026-09-19T00:00:00Z','a'*64)

    def test_target_access_barrier(self):self.reject({'statusCode':403})
    def test_proxy_escalation_rejected(self):self.reject({'statusCode':200,'proxyUsed':'stealth'})
    def test_unexpected_credits_rejected(self):self.reject({'statusCode':200,'proxyUsed':'basic','creditsUsed':5})
    def test_redirect_rejected(self):self.reject({'statusCode':200,'proxyUsed':'basic','creditsUsed':1,'sourceURL':URL,'url':'https://trellis.law/login'},expected=ValueError)
    def test_challenge_rejected(self):self.reject({'statusCode':200,'proxyUsed':'basic','creditsUsed':1,'sourceURL':URL,'url':URL},'Verify you are human')
    def test_empty_success_rejected(self):self.reject({'statusCode':200,'proxyUsed':'basic','creditsUsed':1,'sourceURL':URL,'url':URL},expected=ValueError)

if __name__=='__main__':unittest.main()
