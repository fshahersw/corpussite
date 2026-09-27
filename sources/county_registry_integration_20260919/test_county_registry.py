import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
b = load('registry_build', HERE / 'build.py')
a = load('registry_adapter', HERE.parents[1] / 'delivery/archive-directory/county_registry.py')


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.row = {'fips': {'value': '21123', 'status': 'derived'}, 'county': {'value': 'LaRue', 'status': 'observed'},
                    'state': {'value': 'Kentucky', 'status': 'derived'}, 'state_code': {'value': 'KY', 'status': 'derived'}}
        self.inv = {'21123': {'geoid': '21123', 'name': 'Larue County', 'state': 'Kentucky', 'usps': 'KY'}}
    def test_case_only_spelling(self):
        self.assertEqual(b.check_join(self.row, self.inv)['geoid'], '21123')
    def test_wrong_county(self):
        self.row['county']['value'] = 'Adair'
        with self.assertRaises(ValueError): b.check_join(self.row, self.inv)
    def test_wrong_state(self):
        self.row['state']['value'] = 'Texas'
        with self.assertRaises(ValueError): b.check_join(self.row, self.inv)
    def test_unknown_evidence(self):
        with self.assertRaises(ValueError): b.check_tags({'status': 'observed', 'value': 'x', 'evidence_id': 'wrong'}, {'known'})
    def test_null_reason(self):
        with self.assertRaises(ValueError): b.check_tags({'status': 'null', 'value': None}, set())
    def test_url_constraints(self):
        self.assertIsNone(b.safe_url('javascript:alert(1)'))
        self.assertIsNone(b.safe_url('https://user:secret@example.com'))
    def test_live_projection(self):
        rows = a._load()
        self.assertEqual(len(rows), 374)
        self.assertEqual(sum(r['counts']['judge_seats'] for r in rows.values()), 2539)
        for r in rows.values():
            self.assertEqual(r['source_as_of'], '2026-08-22')
            self.assertIsNone(r['saved_at'])
            self.assertIs(r['current_service_verified'], False)
            self.assertEqual(r['source']['verified_artifacts'], 167)
            self.assertEqual(r['source']['missing_artifacts'], 1)
        card = next(l for l in rows['48001']['links'] if 'card.txcourts.gov' in l['url'])
        self.assertIn('not case search', card['label'])
        self.assertFalse(card['downloaded_document'])
        self.assertEqual(rows['48001']['courts'][0]['url_role'], 'source reference')
    def test_hash_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'validation.json').write_text(json.dumps({'status': 'passed', 'ready': True, 'counties_sha256': '0'*64}))
            (p / 'counties.json').write_text('{}')
            with self.assertRaises(ValueError): a._load(p)
    def test_uncovered_and_path_ids(self):
        self.assertFalse(a.county('01001')['available'])
        self.assertFalse(a.county('../21123')['available'])

if __name__ == '__main__': unittest.main()
