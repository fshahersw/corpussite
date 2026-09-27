"""Tests for the judge_structured adapter.

Synthetic fixtures prove the fail-closed gate and the pure functions (overlay_for, facets, ids_for); the last class checks
the real supplement in sources/judge_structured_20260919 and independently re-verifies every bridge against the inputs.
"""
import csv
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import judge_structured as js  # noqa: E402

A = 'judge-entity-aaaaaaaaaaaaaaaaaaaaaaaa'
B = 'judge-entity-bbbbbbbbbbbbbbbbbbbbbbbb'
C = 'judge-entity-cccccccccccccccccccccccc'


def _rows():
    status = {'value': 'senior', 'as_of': '2026-09-14', 'label': 'FJC-reported status as of 2026-09-14',
              'detail': 'senior status since 2021-01-31'}
    return [
        {'entity_id': A, 'name': 'Alpha Q. Judge',
         'fjc': {'name_as_published': 'Alpha Q. Judge', 'appointments': [
             {'court': 'U.S. District Court for the District of New Jersey', 'appointing_president': 'Barack Obama'},
             {'court': 'U.S. Court of Appeals for the Third Circuit', 'appointing_president': 'Joseph R. Biden'}]},
         'fjc_status': status,
         'ids': {'fjc_nid': '1', 'fjc_jid': '2', 'cl_person_id': '111', 'bridge_status': 'linked_native_id', 'bridge_basis': 'native'},
         'courtlistener': {'political_affiliations': [], 'educations': [], 'positions_count': 3, 'how_selected': []},
         'role_normalized': {'value': 'circuit', 'basis': 'fjc_court_type_of_open_appointment', 'evidence': 'x'},
         'completeness': {'score': 90, 'present': ['fjc_status'], 'missing': ['biography']},
         'facets': {'status': 'senior', 'role': 'circuit', 'appointing_president': ['Barack Obama', 'Joseph R. Biden']}},
        {'entity_id': B, 'name': 'Beta Judge', 'fjc': None, 'fjc_status': None, 'ids': None, 'courtlistener': None,
         'role_normalized': {'value': 'state_trial', 'basis': 'court_name_pattern', 'evidence': 'X County Superior Court'},
         'completeness': {'score': 15, 'present': ['role_established'], 'missing': ['fjc_status']},
         'facets': {'status': None, 'role': 'state_trial', 'appointing_president': []}},
        {'entity_id': C, 'name': 'Gamma Judge',
         'fjc': {'name_as_published': 'Gamma Judge', 'appointments': [{'court': 'U.S. District Court for the District of Utah',
                                                                       'appointing_president': 'Barack Obama'}]},
         'fjc_status': dict(status, value='active', detail='open appointment'),
         'ids': {'fjc_nid': '3', 'fjc_jid': '4', 'cl_person_id': None, 'bridge_status': 'no_courtlistener_person_with_this_fjc_jid',
                 'bridge_basis': 'native'},
         'courtlistener': None, 'role_normalized': {'value': 'district', 'basis': 'fjc_court_type_of_open_appointment', 'evidence': 'x'},
         'completeness': {'score': 60, 'present': [], 'missing': []},
         'facets': {'status': 'active', 'role': 'district', 'appointing_president': ['Barack Obama']}},
    ]


def _write(folder, status='passed', ready=True, tamper=None, drop=None):
    folder = Path(folder)
    files = {'overlay.jsonl': ''.join(json.dumps(r, sort_keys=True) + '\n' for r in _rows()), 'unresolved.jsonl': ''}
    data_files = []
    for name, text in files.items():
        raw = text.encode('utf-8')
        (folder / name).write_bytes(raw)
        data_files.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'rows': text.count('\n')})
    gate = {'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T00:00:00+00:00',
            'data_files': [d for d in data_files if d['path'] != drop], 'counts': {'overlay_rows': 3}, 'checks': [],
            'qualification': 'q', 'license_ref': '', 'inputs': []}
    (folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
    if tamper:
        with (folder / tamper).open('ab') as handle:
            handle.write(b'{"entity_id":"judge-entity-dddddddddddddddddddddddd"}\n')
    return folder


class GateTests(unittest.TestCase):
    def test_valid_supplement_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write(tmp)
            self.assertTrue(js.summary(folder=folder)['available'])
            self.assertEqual(js.overlay_for(A, folder=folder)['fjc_status']['value'], 'senior')

    def test_fails_closed(self):
        cases = [dict(status='failed'), dict(ready=False), dict(ready='true'), dict(tamper='overlay.jsonl'),
                 dict(tamper='unresolved.jsonl'), dict(drop='overlay.jsonl')]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                folder = _write(tmp, **case)
                info = js.summary(folder=folder)
                self.assertFalse(info['available'])
                self.assertTrue(info['reason'])
                self.assertIsNone(js.overlay_for(A, folder=folder))
                self.assertEqual(js.facets(folder=folder), {'status': [], 'role': [], 'appointing_president': []})
                self.assertEqual(js.ids_for({'status': 'senior'}, folder=folder), set())

    def test_missing_folder_or_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(js.summary(folder=Path(tmp) / 'absent')['available'])
            folder = _write(tmp)
            (folder / 'validation.json').unlink()
            self.assertIsNone(js.overlay_for(A, folder=folder))

    def test_alteration_after_first_load_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write(tmp)
            self.assertIsNotNone(js.overlay_for(A, folder=folder))
            with (folder / 'overlay.jsonl').open('ab') as handle:
                handle.write(b'{"entity_id":"judge-entity-eeeeeeeeeeeeeeeeeeeeeeee"}\n')
            self.assertIsNone(js.overlay_for(A, folder=folder))
            self.assertEqual(js.ids_for({}, folder=folder), set())


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = _write(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_overlay_for(self):
        row = js.overlay_for(A, folder=self.folder)
        self.assertEqual(row['role_normalized']['value'], 'circuit')
        self.assertEqual(row['role_label'], js.ROLE_LABELS['circuit'])
        self.assertEqual(row['fjc_status']['as_of'], '2026-09-14')
        self.assertNotIn('current', json.dumps(row).lower())
        self.assertIsNone(js.overlay_for('judge-entity-ffffffffffffffffffffffff', folder=self.folder))

    def test_bad_identifiers(self):
        for bad in (None, '', '../x', 'judge-entity-ZZ', 'a' * 500, 5, ['x'], A + '\n'):
            self.assertIsNone(js.overlay_for(bad, folder=self.folder))

    def test_facets(self):
        facets = js.facets(folder=self.folder)
        self.assertEqual(set(facets), {'status', 'role', 'appointing_president'})
        self.assertEqual(dict(map(tuple, facets['status'])), {'senior': 1, 'active': 1})
        self.assertEqual(dict(map(tuple, facets['role'])), {'circuit': 1, 'state_trial': 1, 'district': 1})
        self.assertEqual(facets['appointing_president'][0], ['Barack Obama', 2])
        self.assertTrue(all(isinstance(pair, list) and len(pair) == 2 for group in facets.values() for pair in group))

    def test_ids_for(self):
        ids = lambda **f: js.ids_for(f, folder=self.folder)  # noqa: E731
        self.assertEqual(ids(), {A, B, C})
        self.assertEqual(ids(status='senior'), {A})
        self.assertEqual(ids(role='state_trial'), {B})
        self.assertEqual(ids(president='Barack Obama'), {A, C})
        self.assertEqual(ids(president='Joseph R. Biden'), {A})
        self.assertEqual(ids(president='Barack Obama', status='active', role='district'), {C})
        self.assertEqual(ids(status=''), {A, B, C})
        self.assertEqual(ids(status='nonsense'), set())
        self.assertEqual(ids(role=['district']), set())
        self.assertEqual(js.ids_for(None, folder=self.folder), {A, B, C})

    def test_results_are_copies_and_path_free(self):
        first = js.overlay_for(A, folder=self.folder)
        first['role_normalized']['value'] = 'mutated'
        self.assertEqual(js.overlay_for(A, folder=self.folder)['role_normalized']['value'], 'circuit')
        js.ids_for({}, folder=self.folder).clear()
        self.assertEqual(len(js.ids_for({}, folder=self.folder)), 3)
        text = json.dumps([js.overlay_for(A, folder=self.folder), js.summary(folder=self.folder), js.facets(folder=self.folder)])
        self.assertNotIn(Path(self.folder).name, text)
        self.assertIsNone(re.search(r'[A-Za-z]:[\\/]{1,2}Users', text))


class RealSupplementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (js.DATA / 'validation.json').is_file():
            raise unittest.SkipTest('supplement not built yet')
        cls.all_ids = js.ids_for({})

    def test_ready_and_counts(self):
        info = js.summary()
        self.assertTrue(info['available'])
        self.assertEqual(info['counts']['fjc_entities'], 4074)
        self.assertEqual(info['counts']['fjc_serving_district'], 1134)
        self.assertEqual(len(self.all_ids), info['counts']['overlay_rows'])

    def test_martinotti_has_appointments_and_dated_status(self):
        pool = js.ids_for({'president': 'Barack Obama', 'role': 'district'})
        hits = [row for row in map(js.overlay_for, sorted(pool)) if row['fjc']['name_as_published'] == 'Brian R. Martinotti']
        self.assertEqual(len(hits), 1)
        row = hits[0]
        first = row['fjc']['appointments'][0]
        self.assertEqual(first['court'], 'U.S. District Court for the District of New Jersey')
        self.assertEqual((first['commission_date'], first['aba_rating'], first['senate_vote_ayes_nays']),
                         ('2016-07-11', 'Well Qualified', '92/5'))
        self.assertEqual(row['fjc_status']['as_of'], '2026-09-14')
        self.assertEqual(row['fjc_status']['label'], 'FJC-reported status as of 2026-09-14')
        self.assertIn(row['fjc_status']['value'], ('active', 'senior', 'terminated', 'deceased'))
        self.assertEqual((row['ids']['fjc_nid'], row['ids']['fjc_jid']), ('1394871', '3608'))
        self.assertNotIn('current', json.dumps(row).lower())

    def test_no_name_only_bridges(self):
        """Every cl_person_id must be reproducible from native ids alone: nid -> FJC row -> jid == people.fjc_id."""
        root = js.ROOT
        with open(root / 'sources/judges/enrichment_20260914/federal_biographies/judges.csv', encoding='utf-8-sig', newline='') as handle:
            jid_by_nid = {r['nid'].strip(): r['jid'].strip() for r in csv.DictReader(handle)}
        db = (root / 'sources/courtlistener_people_20260918/catalog.sqlite3').as_posix()
        con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        fjc_id_by_person = dict(con.execute("select id, fjc_id from people where fjc_id <> ''"))
        con.close()
        bridged = 0
        for entity_id in self.all_ids:
            row = js.overlay_for(entity_id)
            ids = row['ids']
            if not ids or not ids['cl_person_id']:
                self.assertIsNone(row['courtlistener'])
                continue
            bridged += 1
            self.assertEqual(ids['bridge_status'], 'linked_native_id')
            self.assertEqual(jid_by_nid[ids['fjc_nid']], ids['fjc_jid'])
            self.assertEqual(fjc_id_by_person[ids['cl_person_id']], ids['fjc_jid'])
        self.assertEqual(bridged, js.summary()['counts']['bridged'])
        self.assertGreater(bridged, 3000)

    def test_facets_match_ids_for(self):
        facets = js.facets()
        self.assertLessEqual({v for v, _ in facets['status']}, {'active', 'senior', 'terminated', 'deceased'})
        self.assertLessEqual({v for v, _ in facets['role']}, set(js.ROLE_LABELS))
        self.assertEqual(sum(n for _, n in facets['role']), len(self.all_ids))
        for value, count in facets['status']:
            self.assertEqual(len(js.ids_for({'status': value})), count)
        value, count = facets['appointing_president'][0]
        self.assertEqual(len(js.ids_for({'president': value})), count)


if __name__ == '__main__':
    unittest.main()
