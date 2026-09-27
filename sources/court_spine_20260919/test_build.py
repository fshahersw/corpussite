"""Checks on the built court spine data (run after build.py; offline)."""
import hashlib
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RASTER = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}


def rows(name):
    return [json.loads(l) for l in (HERE / name).read_text(encoding='utf-8').splitlines() if l.strip()]


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        cls.courts = rows('courts.jsonl')
        cls.logos = rows('logos.jsonl')
        cls.by = {c['id']: c for c in cls.courts}

    def test_envelope_and_hashes(self):
        g = self.gate
        self.assertEqual((g['schema_version'], g['status'], g['ready']), ('1', 'passed', True))
        self.assertTrue(all(c['ok'] for c in g['checks']))
        for d in g['data_files']:
            self.assertEqual(hashlib.sha256((HERE / d['path']).read_bytes()).hexdigest(), d['sha256'])
        self.assertTrue(all(len(i['sha256']) == 64 and not re.match(r'[A-Za-z]:', i['path']) for i in g['inputs']))

    def test_courtlistener_rows_and_crosswalk(self):
        cl = [c for c in self.courts if c['id_kind'] == 'courtlistener']
        self.assertEqual(len(cl), 3361)
        self.assertEqual(len(self.by), len(self.courts))
        self.assertEqual(self.gate['counts']['crosswalk'], {'exact_id': 206, 'reviewed_institution_match': 7, 'unresolved': 56})
        self.assertEqual(self.by['njd']['registry'][0]['crosswalk_kind'], 'exact_id')
        self.assertEqual(self.by['arb']['registry'][0]['key'], 'FB:azb')
        self.assertEqual(self.by['pactcomplphilad']['county_fips'], '42101')
        self.assertIsNone(self.by['reg-LC-mo_st_louis']['cl_court_id'])
        self.assertEqual(self.by['reg-LC-mo_st_louis']['county_fips'], '29510')
        self.assertIsNone(self.by['ca3']['state'])
        self.assertIsNone(self.by['nyjustctportwa']['state'])
        self.assertEqual((self.by['njd']['state'], self.by['njd']['start_date'], self.by['njd']['system']), ('NJ', '1789-09-24', 'federal'))
        self.assertTrue(all(bool(c['state']) == bool(c['state_basis']) for c in self.courts))

    def test_temporal_block(self):
        for c in self.courts[:50] + self.courts[-50:]:
            for key in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to'):
                self.assertIn(key, c['temporal'])
                self.assertIn(key + '_basis', c['temporal'])

    def test_mdl_join(self):
        self.assertEqual(sum(c['pending_mdl_count'] for c in self.courts), self.gate['counts']['pending_mdls_joined'])
        self.assertTrue(all(c['id_kind'] == 'courtlistener' for c in self.courts if c['pending_mdl_count']))
        self.assertTrue(self.by['njd']['pending_mdl_count'] >= 1 and self.by['njd']['mdl_as_of'])

    def test_locations(self):
        listed = sum(l['times_listed'] for c in self.courts for l in c['locations'])
        self.assertEqual(listed, 2358)
        self.assertTrue(all(re.fullmatch(r'\d{5}', l['county_fips']) and l['county_fips'][:2] in ('21', '48') for c in self.courts for l in c['locations']))
        self.assertTrue(all(c['id_kind'] == 'county_registry' for c in self.courts if c['locations']))
        generic = [c for c in self.courts if c['short_name'] == 'County Court' and c['id_kind'] == 'county_registry']
        self.assertTrue(len(generic) > 200 and all(len(c['locations']) == 1 for c in generic))

    def test_logos(self):
        self.assertTrue(self.logos)
        for l in self.logos:
            self.assertIn(l['mime'], RASTER)
            self.assertEqual(l['permission_status'], 'not_established')
            self.assertIsInstance(l['shared_mark'], bool)
            blob = (HERE / 'assets' / ('%s.%s' % (l['sha256'], RASTER[l['mime']]))).read_bytes()
            self.assertEqual((hashlib.sha256(blob).hexdigest(), len(blob)), (l['sha256'], l['bytes']))
            self.assertTrue(all(i in self.by and l['file_id'] in self.by[i]['logo_file_ids'] for i in l['court_ids']))
        self.assertEqual(sum(1 for c in self.courts if c['has_logo']), self.gate['counts']['courts_with_logo'])

    def test_no_private_strings(self):
        text = (HERE / 'courts.jsonl').read_text(encoding='utf-8') + (HERE / 'logos.jsonl').read_text(encoding='utf-8')
        for needle in ('C:/', 'C:\\\\', 'Users/', 'Court-Document-Library', 'absolute_path', 'mailto:', '"phone"', '"email"'):
            self.assertNotIn(needle, text)


if __name__ == '__main__':
    unittest.main()
