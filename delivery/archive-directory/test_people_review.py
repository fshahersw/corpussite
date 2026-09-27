"""Independent semantic and fail-closed gate tests; all writes are disposable fixtures."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('people_review_subject', Path(__file__).with_name('people.py'))
people = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(people)


class DateAndRoleSemantics(unittest.TestCase):
    def test_year_padding_is_not_displayed_as_january_first(self):
        self.assertEqual(people.format_date('1986-01-01', '%Y'),
                         {'text': '1986', 'precision': 'year', 'recorded_value': '1986-01-01'})

    def test_month_padding_is_not_displayed_as_day_one(self):
        result = people.format_date('1957-12-01', '%Y-%m')
        self.assertEqual(result['text'], 'December 1957')
        self.assertEqual(result['precision'], 'month')
        self.assertEqual(result['recorded_value'], '1957-12-01')

    def test_known_day_is_preserved(self):
        self.assertEqual(people.format_date('1916-07-26', '%Y-%m-%d')['text'], 'July 26, 1916')

    def test_missing_value_stays_missing_despite_precision_marker(self):
        for value in ('', None, ' '):
            for precision in ('%Y', '%Y-%m-%d'):
                self.assertIsNone(people.format_date(value, precision))

    def test_unknown_precision_is_qualified_and_raw_is_preserved(self):
        for precision in ('', None, 'unexpected'):
            result = people.format_date('1986-03-19', precision)
            self.assertEqual(result['precision'], 'unspecified')
            self.assertEqual(result['text'], '1986 (precision not specified)')
            self.assertEqual(result['recorded_value'], '1986-03-19')

    def test_invalid_date_is_not_repaired_or_invented(self):
        result = people.format_date('1986-02-31', '%Y-%m-%d')
        self.assertEqual(result['precision'], 'unrecognized')
        self.assertEqual(result['recorded_value'], '1986-02-31')

    def test_recorded_title_wins_and_unknown_type_is_neutral(self):
        self.assertEqual(people.role_label({'job_title': 'Law Clerk', 'position_type': 'jud'}), 'Law Clerk')
        self.assertEqual(people.role_label({'job_title': '', 'position_type': 'unknown'}), 'Recorded position')

    def test_court_link_does_not_promote_clerk_to_judge_or_make_service_current(self):
        row = {'id': '10', 'position_type': 'clerk', 'court_id': 'ct', 'court_full_name': 'Example Court',
               'date_start': '1986-01-01', 'date_granularity_start': '%Y',
               'date_termination': '', 'date_granularity_termination': '%Y', 'has_inferred_values': 't'}
        result = people.position_view(row)
        self.assertEqual(result['role'], 'Clerk')
        self.assertEqual(result['start'], '1986')
        self.assertEqual(result['end'], '')
        self.assertIsNone(result['end_detail'])
        self.assertTrue(result['inferred'])
        self.assertIn('inferred', result['inference_note'])
        self.assertNotIn('present', json.dumps(result).lower())

    def test_unknown_role_code_and_event_date_evidence_are_retained(self):
        result = people.position_view({'id': '11', 'position_type': 'new-code', 'date_nominated': '2000-02-03'})
        self.assertEqual(result['role'], 'Recorded position')
        self.assertEqual(result['source_role_code'], 'new-code')
        self.assertEqual(result['dates'][0]['precision'], 'unspecified')
        self.assertEqual(result['dates'][0]['recorded_value'], '2000-02-03')

    def test_education_specific_award_is_not_replaced_by_broad_category(self):
        for level, detail in [('ba', 'B.S.'), ('ma', 'A.M.'), ('llb', 'B.L.')]:
            with self.subTest(level=level):
                result = people.education_view({'id': '7', 'degree_level': level, 'degree_detail': detail})
                self.assertEqual(result['degree'], detail)
                self.assertEqual(result['degree_detail'], '')
                self.assertEqual(result['source_degree_level'], level)
                self.assertEqual(result['source_degree_detail'], detail)

    def test_unspecified_bachelor_award_is_not_invented_as_ba(self):
        result = people.education_view({'id': '7', 'degree_level': 'ba', 'degree_detail': '', 'degree_year': '1947'})
        self.assertEqual(result['degree'], "Bachelor's degree")
        self.assertEqual(result['year'], '1947')
        self.assertEqual(result['source_degree_detail'], '')

    def test_unknown_degree_code_does_not_invent_an_award(self):
        result = people.education_view({'id': '8', 'degree_level': 'new-degree', 'degree_detail': ''})
        self.assertEqual(result['degree'], 'Degree recorded')
        self.assertEqual(result['source_degree_level'], 'new-degree')


class PresentationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='people-review-')
        self.folder = Path(self.temp.name)
        self.db = self.folder / 'catalog.sqlite3'
        c = sqlite3.connect(self.db)
        c.executescript('''
          CREATE TABLE people(id TEXT PRIMARY KEY,is_alias_of_id TEXT,has_photo TEXT,date_dob TEXT,
            date_granularity_dob TEXT,date_dod TEXT,date_granularity_dod TEXT,fjc_id TEXT);
          INSERT INTO people VALUES ('1','','t','1900-01-01','%Y','','','9001');
          INSERT INTO people VALUES ('2','','f','','','','','9002');
          INSERT INTO people VALUES ('3','1','f','','','','','');
          CREATE TABLE people_search(person_id TEXT PRIMARY KEY,display_name TEXT,search_text TEXT,has_photo INTEGER,is_alias INTEGER);
          INSERT INTO people_search VALUES ('1','Alex Example','Alex Example',1,0),
            ('2','Alex Example','Alex Example',0,0),('3','A. Example','A. Example',0,1);
          CREATE VIRTUAL TABLE people_fts USING fts5(search_text,content='people_search',content_rowid='rowid');
          INSERT INTO people_fts(people_fts) VALUES ('rebuild');
          CREATE TABLE positions(id TEXT PRIMARY KEY,person_id TEXT,court_id TEXT,date_start TEXT,date_granularity_start TEXT,
            date_termination TEXT,date_granularity_termination TEXT,position_type TEXT,job_title TEXT,has_inferred_values TEXT);
          INSERT INTO positions VALUES ('10','1','ct','1950-01-01','%Y','','','clerk','','t');
          CREATE TABLE courts(id TEXT PRIMARY KEY,full_name TEXT);
          INSERT INTO courts VALUES ('ct','Example Court');
          CREATE TABLE educations(id TEXT PRIMARY KEY,person_id TEXT,school_id TEXT,degree_year TEXT);
          CREATE TABLE schools(id TEXT PRIMARY KEY,name TEXT);
          CREATE TABLE source_files(table_name TEXT,sha256 TEXT);
          INSERT INTO source_files VALUES ('people','fixture-people-sha'),('positions','fixture-position-sha');
        ''')
        c.commit()
        c.close()
        self.meta = {'ready': True, 'summary': {'snapshot_label': 'Fixture snapshot'}}

    def tearDown(self):
        people._verified.cache_clear()
        self.temp.cleanup()

    @contextmanager
    def readonly(self):
        c = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    def test_alias_keeps_native_identity_and_does_not_inherit_target_positions(self):
        with patch.object(people, 'info', return_value=self.meta), patch.object(people, 'connect', self.readonly):
            result = people.profile('3')
            target = people.profile('1')
        self.assertEqual((result['id'], result['name']), ('3', 'A. Example'))
        self.assertEqual(result['alias_of'], {'id': '1', 'name': 'Alex Example'})
        self.assertTrue(result['is_alias'])
        self.assertEqual(result['positions'], [])
        self.assertEqual(result['source']['person_id'], '3')
        self.assertEqual(target['source']['legacy_fjc_id'], '9001')
        self.assertTrue(target['source']['has_inferred_values'])
        self.assertTrue(target['photo_reference'])
        self.assertNotIn('photo_url', target)

    def test_same_names_stay_separate_and_alias_inclusion_is_explicit(self):
        with patch.object(people, 'info', return_value=self.meta), patch.object(people, 'connect', self.readonly):
            default = people.listing({'q': 'Alex Example'})
            all_rows = people.listing({'include_aliases': '1'})
            court = people.listing({'court': 'ct'})
        self.assertEqual(default['total'], 2)
        self.assertEqual({r['id'] for r in default['items']}, {'1', '2'})
        self.assertEqual(all_rows['total'], 3)
        self.assertEqual(court['total'], 1)
        self.assertEqual(court['items'][0]['career_summary'], 'Clerk · Example Court')

    def test_unready_layer_never_opens_database(self):
        with patch.object(people, 'info', return_value={'ready': False, 'summary': {}, 'message': 'Unavailable'}), \
             patch.object(people, 'connect') as connect:
            self.assertFalse(people.listing({})['ready'])
            self.assertFalse(people.profile('1')['ready'])
            connect.assert_not_called()

    def test_non_native_ids_and_sql_fragments_are_not_resolved(self):
        with patch.object(people, 'connect') as connect:
            for key in ('entity:1', '1 OR 1=1', '../1', '0', 1):
                self.assertIsNone(people.profile(key))
            connect.assert_not_called()

    def test_connection_is_read_only_on_disposable_fixture(self):
        with patch.object(people, 'DB', self.db):
            with people.connect() as connection:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("DELETE FROM people WHERE id='1'")

    def test_exact_court_filter_and_search_are_parameterized(self):
        with patch.object(people, 'info', return_value=self.meta), patch.object(people, 'connect', self.readonly):
            self.assertEqual(people.listing({'court': "ct' OR 1=1 --"})['total'], 0)
            self.assertEqual(people.listing({'q': '" OR * --'})['total'], 0)

    def gate_bundle(self, replacements=None):
        counts = {'people': 3, 'positions': 1, 'educations': 0, 'schools': 0, 'courts': 1,
                  'political_affiliations': 0}
        payloads = {
            'source_manifest.json': {'schema_version': 'courtlistener-people-source-manifest.v1',
                'source_snapshot': '2026-06-30', 'tables': {
                    name: {'sha256': '0' * 64, 'expected_rows': count} for name, count in counts.items()}},
            'summary.json': {'schema_version': 'courtlistener-people-summary.v1',
                'source_snapshot': '2026-06-30', 'counts': counts,
                'people_with_alias_reference': 1, 'people_with_photo_flags': 1},
            'validation.json': {'passed': True, 'foreign_key_errors': [], 'sqlite_integrity_check': 'ok',
                'input_hashes_match_before_and_after_import': True, 'csv_headers_and_all_row_widths_match': True,
                'native_primary_keys_unique': True, 'raw_table_counts': counts, 'search_rows': 3,
                'fts_external_content_integrity_check': 'passed'},
        }
        for filename, replacement in (replacements or {}).items():
            if callable(replacement):
                replacement(payloads[filename])
            elif filename != 'ready.json':
                payloads[filename] = replacement
        for filename, payload in payloads.items():
            (self.folder / filename).write_text(json.dumps(payload), encoding='utf8')
        ready = {'ready': True, 'status': 'verified', 'schema_version': 'courtlistener-people-catalog.v1',
            'database': str(self.db), 'database_bytes': self.db.stat().st_size,
            'database_sha256': people.digest(self.db),
            'source_manifest_sha256': people.digest(self.folder / 'source_manifest.json'),
            'summary_sha256': people.digest(self.folder / 'summary.json'),
            'validation_sha256': people.digest(self.folder / 'validation.json')}
        (self.folder / 'ready.json').write_text(json.dumps((replacements or {}).get('ready.json', ready)), encoding='utf8')
        people._verified.cache_clear()

    def gate_info(self):
        with patch.object(people, 'FOLDER', self.folder), patch.object(people, 'DB', self.db):
            return people.info()

    def test_ready_gate_accepts_complete_receipt_and_rejects_changed_artifact(self):
        self.gate_bundle()
        self.assertTrue(self.gate_info()['ready'])
        with (self.folder / 'summary.json').open('a', encoding='utf8') as output:
            output.write(' ')
        self.assertFalse(self.gate_info()['ready'])

    def test_malformed_receipt_containers_fail_closed_without_exception(self):
        for filename in ('ready.json', 'validation.json', 'summary.json', 'source_manifest.json'):
            with self.subTest(filename=filename):
                self.gate_bundle({filename: []})
                self.assertFalse(self.gate_info()['ready'])

    def test_missing_or_failed_validation_proofs_fail_closed(self):
        cases = [lambda v: v.pop('foreign_key_errors'),
                 lambda v: v.pop('fts_external_content_integrity_check'),
                 lambda v: v.update(fts_external_content_integrity_check='failed')]
        for change in cases:
            with self.subTest(change=change):
                self.gate_bundle({'validation.json': change})
                self.assertFalse(self.gate_info()['ready'])

    def test_pinned_table_count_disagreement_fails_closed(self):
        self.gate_bundle({'source_manifest.json': lambda m: m['tables']['people'].update(expected_rows=4)})
        self.assertFalse(self.gate_info()['ready'])


class PublishedSnapshotReadOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = people.info()
        if not cls.meta['ready']:
            raise unittest.SkipTest('Verified local biography layer is not published')

    def test_source_counts_and_alias_listing_scope(self):
        self.assertEqual(self.meta['summary']['people'], 16191)
        self.assertEqual(self.meta['summary']['positions'], 51291)
        self.assertEqual(self.meta['summary']['aliases'], 394)
        self.assertEqual(people.listing({'limit': '1'})['total'], 15797)
        self.assertEqual(people.listing({'limit': '1', 'include_aliases': '1'})['total'], 16191)

    def test_real_aliases_keep_names_ids_and_distinct_targets(self):
        for alias_id, target_id in [('7607', '4803'), ('10291', '10290'), ('6781', '1810')]:
            with self.subTest(alias_id=alias_id):
                item = people.profile(alias_id)
                self.assertEqual(item['id'], alias_id)
                self.assertEqual(item['alias_of']['id'], target_id)
                self.assertEqual(item['source']['person_id'], alias_id)
                self.assertTrue(item['is_alias'])
                self.assertEqual(item['positions'], [])

    def test_real_padded_and_missing_dates_display_at_recorded_precision(self):
        year = people.profile('1429')['birth']
        month = people.profile('12732')['birth']
        missing = people.profile('10298')['death']
        self.assertEqual((year['text'], year['precision']), ('1933', 'year'))
        self.assertEqual((month['text'], month['precision']), ('December 1957', 'month'))
        self.assertIsNone(missing)


if __name__ == '__main__':
    unittest.main(verbosity=2)
