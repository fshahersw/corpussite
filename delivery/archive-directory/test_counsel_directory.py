"""Adapter tests for counsel_directory.py (sources/counsel_directory_20260919).

Fixture builds a tiny SQLite database with the same table shape as the real one in a temp folder, plus a
uniform validation envelope with a matching SHA-256, so the hash gate and the listing/detail/for_mdl shape
are exercised without touching the private live data. Real-data assertions at the end run against the
actual built directory and are skipped if it is not present.
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import counsel_directory as c

SCHEMA = '''
CREATE TABLE firms(id TEXT PRIMARY KEY, firm_key TEXT UNIQUE, display_name TEXT, variant_count INTEGER,
    appearance_count INTEGER, docket_count INTEGER, attorney_count INTEGER, mdl_count INTEGER, roles_seen TEXT);
CREATE TABLE firm_variants(firm_id TEXT, variant_text TEXT, source TEXT, count INTEGER);
CREATE TABLE attorneys(id TEXT PRIMARY KEY, display_name TEXT, id_kind TEXT, source TEXT,
    appearance_count INTEGER, docket_count INTEGER, mdl_count INTEGER, firm_ids TEXT);
CREATE TABLE appearances(id TEXT PRIMARY KEY, docket_id TEXT, attorney_id TEXT, firm_id TEXT, firm_raw TEXT,
    role_raw TEXT, role_normalized TEXT, side TEXT, party_name_public TEXT, party_is_organization INTEGER,
    mdl_number INTEGER, source TEXT);
CREATE TABLE dockets(id TEXT PRIMARY KEY, native_docket_id INTEGER, aws_matter_id TEXT, docket_number TEXT,
    court TEXT, case_name_public TEXT, case_name_suppressed INTEGER, mdl_master_docket_id INTEGER,
    mdl_number INTEGER, mdl_title TEXT, mdl_resolution_basis TEXT, source TEXT, courtlistener_url TEXT);
CREATE TABLE mdl_links(mdl_number INTEGER PRIMARY KEY, mdl_title TEXT, mdl_status TEXT, cl_court_id TEXT,
    master_docket_number TEXT, resolution_basis TEXT, firms_count INTEGER, attorneys_count INTEGER,
    dockets_count INTEGER, appearances_count INTEGER);
CREATE TABLE leadership_links(id TEXT PRIMARY KEY, mdl_number INTEGER, source_layer TEXT, record_id TEXT,
    label TEXT, date TEXT, docket_number TEXT, court TEXT, courtlistener_url TEXT, excerpt TEXT);
CREATE TABLE phila_liaison(id TEXT PRIMARY KEY, program_number INTEGER, program_name TEXT, program_code TEXT,
    role_raw TEXT, side TEXT, person_name TEXT, firm_raw TEXT, firm_id TEXT, source_url TEXT, captured_at TEXT);
CREATE TABLE rejected(id TEXT PRIMARY KEY, raw_string TEXT, reason TEXT, sources TEXT, occurrence_count INTEGER);
CREATE VIRTUAL TABLE search_fts USING fts5(entity_id UNINDEXED, kind UNINDEXED, text);
'''


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.db_path = self.folder / c.DB_NAME
        self._build_db()

    def _build_db(self):
        if self.db_path.exists():
            self.db_path.unlink()
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO firms VALUES('firm:seeger','seeger weiss','Seeger Weiss LLP',3,5,2,2,1,'[\"Lead attorney\",\"Attorney to be noticed\"]')")
        conn.execute("INSERT INTO firms VALUES('firm:other','other firm','Other Firm LLC',1,1,1,1,0,'[]')")
        conn.execute("INSERT INTO firm_variants VALUES('firm:seeger','SEEGER WEISS LLP','sw_bulk_parties_by_docket',3)")
        conn.execute("INSERT INTO firm_variants VALUES('firm:seeger','Seeger Weiss, LLP','phila_mass_tort_liaison_list',1)")
        conn.execute("INSERT INTO attorneys VALUES('cl:1001','Christopher A Seeger','courtlistener','sw_bulk_parties_by_docket',5,2,1,'[\"firm:seeger\"]')")
        conn.execute("INSERT INTO attorneys VALUES('aws:uuid-1','Devin Bolton','aws_release_uuid','aws_release_b2b',1,1,0,'[\"firm:other\"]')")
        conn.execute("INSERT INTO dockets VALUES('cl:111',111,NULL,'1:20-cv-001','njd',NULL,1,NULL,2789,'IN RE: Test MDL','in the crosswalk','sw_bulk_parties_by_docket','https://www.courtlistener.com/docket/111/')")
        conn.execute("INSERT INTO dockets VALUES('cl:222',222,NULL,'1:20-cv-002','cand','IN RE: Org Co',0,NULL,NULL,NULL,'no link','sw_bulk_parties_by_docket','https://www.courtlistener.com/docket/222/')")
        conn.execute("INSERT INTO appearances VALUES('app:1','cl:111','cl:1001','firm:seeger','Seeger Weiss LLP','lead_attorney','Lead attorney','plaintiff',NULL,NULL,2789,'sw_bulk_parties_by_docket')")
        conn.execute("INSERT INTO appearances VALUES('app:2','cl:222','aws:uuid-1','firm:other','Other Firm LLC','terminated','Terminated','defendant','Acme Pharma Inc.',1,NULL,'aws_release_b2b')")
        conn.execute("INSERT INTO mdl_links VALUES(2789,'IN RE: Test MDL','pending','njd','1:20-cv-001','in the 59-row crosswalk',1,1,1,1)")
        conn.execute("INSERT INTO leadership_links VALUES('lead:1',2789,'mdl_docket_documents_20260919','doc:1','Leadership appointment','2022-01-01','1:20-cv-001','njd','https://www.courtlistener.com/docket/111/1/','Order appointing leadership.')")
        conn.execute("INSERT INTO phila_liaison VALUES('phila:1',1,'ASBESTOS','T1','Plaintiff Liaison','plaintiff','Larry Brown','Brookman, Rosenberg, Brown & Sandler','firm:other','https://www.courts.phila.gov/pdf/x.pdf','2026-08-22')")
        conn.execute("INSERT INTO rejected VALUES('rej:1','PRO SE','pro_se','[\"sw_bulk_parties_by_docket\"]',3)")
        conn.execute("INSERT INTO search_fts(entity_id,kind,text) VALUES('firm:seeger','firm','Seeger Weiss LLP')")
        conn.execute("INSERT INTO search_fts(entity_id,kind,text) VALUES('firm:other','firm','Other Firm LLC')")
        conn.execute("INSERT INTO search_fts(entity_id,kind,text) VALUES('cl:1001','attorney','Christopher A Seeger')")
        conn.execute("INSERT INTO search_fts(entity_id,kind,text) VALUES('aws:uuid-1','attorney','Devin Bolton')")
        conn.commit()
        conn.close()

    def sha(self):
        digest = hashlib.sha256()
        with open(self.db_path, 'rb') as handle:
            digest.update(handle.read())
        return digest.hexdigest()

    def write_validation(self, status='passed', ready=True, sha_override=None, rows=2):
        gate = {
            'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T00:00:00+00:00',
            'data_files': [{'path': c.DB_NAME, 'sha256': sha_override or self.sha(), 'rows': rows}],
            'counts': {}, 'checks': [], 'qualification': 'Test qualification string for the fixture.',
            'license_ref': 'sw_bulk_private_firm_work_product', 'export_allowed': False, 'inputs': [],
        }
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')

    def cleanup(self):
        self.tmp.cleanup()


class GateTests(unittest.TestCase):
    def test_missing_validation_fails_closed(self):
        fx = Fixture()
        try:
            result = c.listing({}, folder=fx.folder)
            self.assertFalse(result['available'])
            self.assertIsNone(c.detail('firm:seeger', folder=fx.folder))
            self.assertIsNone(c.for_mdl(2789, folder=fx.folder))
        finally:
            fx.cleanup()

    def test_status_not_passed_fails_closed(self):
        fx = Fixture()
        try:
            fx.write_validation(status='draft')
            self.assertFalse(c.listing({}, folder=fx.folder)['available'])
        finally:
            fx.cleanup()

    def test_ready_false_fails_closed(self):
        fx = Fixture()
        try:
            fx.write_validation(ready=False)
            self.assertFalse(c.listing({}, folder=fx.folder)['available'])
        finally:
            fx.cleanup()

    def test_hash_mismatch_fails_closed(self):
        fx = Fixture()
        try:
            fx.write_validation(sha_override='0' * 64)
            self.assertFalse(c.listing({}, folder=fx.folder)['available'])
            self.assertIsNone(c.detail('firm:seeger', folder=fx.folder))
        finally:
            fx.cleanup()

    def test_tamper_after_publish_fails_closed(self):
        fx = Fixture()
        try:
            fx.write_validation()
            self.assertTrue(c.listing({}, folder=fx.folder)['available'])
            with open(fx.db_path, 'ab') as handle:
                handle.write(b'tampered bytes')
            result = c.listing({}, folder=fx.folder)
            self.assertFalse(result['available'])
        finally:
            fx.cleanup()

    def test_never_raises_on_garbage_params(self):
        fx = Fixture()
        try:
            fx.write_validation()
            for bad in ({'page': 'abc', 'limit': object(), 'mdl': 'not-a-number', 'kind': 12345, 'q': None},
                        {'q': '"' * 500}, {'kind': ['a', 'b']}):
                result = c.listing(bad, folder=fx.folder)
                self.assertIn('available', result)
            self.assertIsNone(c.detail(None, folder=fx.folder))
            self.assertIsNone(c.detail(12345, folder=fx.folder))
            self.assertIsNone(c.detail('; DROP TABLE firms; --', folder=fx.folder))
            self.assertIsNone(c.for_mdl('not-a-number', folder=fx.folder))
            self.assertIsNone(c.for_mdl(None, folder=fx.folder))
        finally:
            fx.cleanup()


class ListingShapeTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.fx.write_validation()

    def tearDown(self):
        self.fx.cleanup()

    def test_default_listing_is_firms_ordered_by_docket_count(self):
        result = c.listing({}, folder=self.fx.folder)
        self.assertTrue(result['available'])
        self.assertEqual([col['key'] for col in result['columns']], ['firm', 'mdls', 'dockets', 'attorneys', 'roles'])
        self.assertEqual(result['results'][0]['id'], 'firm:seeger')  # higher docket_count first
        self.assertLessEqual(len(result['results'][0]['badges']), 2)

    def test_kind_attorney(self):
        result = c.listing({'kind': 'attorney'}, folder=self.fx.folder)
        self.assertTrue(result['available'])
        ids = {r['id'] for r in result['results']}
        self.assertEqual(ids, {'cl:1001', 'aws:uuid-1'})

    def test_kind_philadelphia_liaison(self):
        result = c.listing({'kind': 'philadelphia_liaison'}, folder=self.fx.folder)
        self.assertTrue(result['available'])
        self.assertEqual(result['results'][0]['title'], 'Larry Brown')

    def test_unknown_kind_falls_back_to_firm(self):
        result = c.listing({'kind': 'nonsense'}, folder=self.fx.folder)
        self.assertEqual([col['key'] for col in result['columns']], ['firm', 'mdls', 'dockets', 'attorneys', 'roles'])

    def test_q_filters_by_name(self):
        result = c.listing({'q': 'seeger'}, folder=self.fx.folder)
        self.assertEqual([r['id'] for r in result['results']], ['firm:seeger'])

    def test_mdl_filter(self):
        result = c.listing({'mdl': '2789'}, folder=self.fx.folder)
        self.assertEqual([r['id'] for r in result['results']], ['firm:seeger'])
        result2 = c.listing({'mdl': '9999'}, folder=self.fx.folder)
        self.assertEqual(result2['results'], [])

    def test_side_filter(self):
        result = c.listing({'side': 'defendant'}, folder=self.fx.folder)
        self.assertEqual([r['id'] for r in result['results']], ['firm:other'])

    def test_pagination(self):
        result = c.listing({'limit': '1', 'page': '2'}, folder=self.fx.folder)
        self.assertEqual(result['limit'], 1)
        self.assertEqual(result['page'], 2)
        self.assertEqual(len(result['results']), 1)


class DetailTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.fx.write_validation()

    def tearDown(self):
        self.fx.cleanup()

    def test_firm_detail_shape(self):
        d = c.detail('firm:seeger', folder=self.fx.folder)
        self.assertEqual(d['title'], 'Seeger Weiss LLP')
        headings = [s['heading'] for s in d['sections']]
        self.assertTrue(any('Printed variants' in h for h in headings))
        self.assertTrue(any('Attorneys' in h for h in headings))
        self.assertTrue(any('MDLs' in h for h in headings))
        self.assertTrue(any('Dockets' in h for h in headings))
        self.assertTrue(any('Leadership' in h for h in headings))
        mdl_section = next(s for s in d['sections'] if 'MDLs' in s['heading'])
        self.assertEqual(mdl_section['items'][0]['links'][0]['url'], '#mdl/2789')

    def test_attorney_detail_shape(self):
        d = c.detail('cl:1001', folder=self.fx.folder)
        self.assertEqual(d['title'], 'Christopher A Seeger')
        headings = [s['heading'] for s in d['sections']]
        self.assertTrue(any('Firm' in h for h in headings))
        self.assertTrue(any('Roles' in h for h in headings))

    def test_phila_detail_shape(self):
        d = c.detail('phila:1', folder=self.fx.folder)
        self.assertEqual(d['title'], 'Larry Brown')
        self.assertIn('Pennsylvania state-court', d['qualification'])
        self.assertEqual(d['links'][0]['url'], 'https://www.courts.phila.gov/pdf/x.pdf')

    def test_unknown_id_returns_none(self):
        self.assertIsNone(c.detail('firm:does-not-exist', folder=self.fx.folder))
        self.assertIsNone(c.detail('cl:99999999', folder=self.fx.folder))


class ForMdlTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.fx.write_validation()

    def tearDown(self):
        self.fx.cleanup()

    def test_known_mdl(self):
        block = c.for_mdl(2789, folder=self.fx.folder)
        self.assertEqual(block['mdl_number'], 2789)
        self.assertEqual(block['total_firms'], 1)
        self.assertEqual(block['link'], '#counsel?mdl=2789')
        self.assertLessEqual(len(block['firms']), 25)
        self.assertLessEqual(len(block['leadership_orders']), 8)
        self.assertLessEqual(len(block['qualification']), 400)
        self.assertIn('plaintiff', block['by_side'])

    def test_unknown_mdl_returns_none(self):
        self.assertIsNone(c.for_mdl(999999, folder=self.fx.folder))

    def test_string_mdl_number_accepted(self):
        block = c.for_mdl('2789', folder=self.fx.folder)
        self.assertEqual(block['mdl_number'], 2789)


@unittest.skipUnless(c.DATA.exists() and (c.DATA / c.DB_NAME).exists(), 'real counsel directory not built')
class RealDataTests(unittest.TestCase):
    def test_real_gate_open(self):
        result = c.listing({})
        self.assertTrue(result['available'])
        self.assertGreater(result['total'], 100)

    def test_real_seeger_weiss_search(self):
        result = c.listing({'q': 'seeger weiss'})
        self.assertGreater(result['total'], 0)
        self.assertIn('Seeger Weiss', result['results'][0]['title'])
        d = c.detail(result['results'][0]['id'])
        self.assertIsNotNone(d)
        self.assertTrue(any('Printed variants' in s['heading'] for s in d['sections']))

    def test_real_philadelphia_liaison_listing(self):
        result = c.listing({'kind': 'philadelphia_liaison', 'q': 'Brown'})
        self.assertGreater(result['total'], 0)

    def test_real_for_mdl_2789(self):
        block = c.for_mdl(2789)
        self.assertIsNotNone(block)
        self.assertEqual(block['mdl_number'], 2789)
        self.assertLessEqual(len(block['qualification']), 400)

    def test_real_for_mdl_unknown_number(self):
        self.assertIsNone(c.for_mdl(1))

    def test_no_natural_person_litigant_name_ever_surfaces(self):
        # 'Roy Allen' is a real natural-person plaintiff name from the parties_by_docket sample that must
        # never be published anywhere in this adapter's output (firms/attorneys/phila never carry party
        # names; only an organisation/named-defendant name is ever attached to an appearance, internally).
        result = c.listing({'kind': 'firm', 'q': 'Roy Allen'})
        for row in result['results']:
            self.assertNotIn('Roy Allen', json.dumps(row))
        result2 = c.listing({'kind': 'attorney', 'q': 'Roy Allen'})
        for row in result2['results']:
            self.assertNotIn('Roy Allen', json.dumps(row))


if __name__ == '__main__':
    unittest.main()
