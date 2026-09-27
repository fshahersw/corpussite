"""Adapter tests for the record-facets sidecar (fail-closed gate, pure filter functions, path-free dicts)."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'sources/record_facets_20260919'))
import schema  # noqa: E402  (sidecar schema shared with build.py)
import record_facets as facets  # noqa: E402


def row(**values):
    base = {name: None for name in schema.COLUMN_NAMES}
    base.update(multi_state=0, has_original=1, has_text=1, validity='ok', capture_flags='[]', kind_flags='[]')
    base.update(values)
    return [base[name] for name in schema.COLUMN_NAMES]


ROWS = [
    row(record_id='r1', dataset='focused', group_name='laws', original_kind='needs_content_review', base_kind='needs_content_review',
        derived_category='statutes', categories='["statutes","constitutions"]', category_basis='retained source category field',
        review_state='needs_content_review', review_basis='content_kind', record_type='law_document', representation='original_document',
        file_type='pdf', file_type_basis='suffix+magic', bytes=36250, jurisdiction_level='state', state='New Jersey', states='["New Jersey"]',
        saved_at='2026-09-13T08:08:50+00:00', saved_at_field='retrieved_at', saved_lo='2026-09-13', saved_hi='2026-09-13',
        captured_at='2026-09-13T08:08:50+00:00', title='https://x.gov/t15c29.pdf', display_title='Title 15 Chapter 29',
        title_basis='document heading', title_issue='url', text_chars=1200),
    row(record_id='r2', dataset='seeger', group_name='federal', original_kind='court_rule_or_order', base_kind='court_rule_or_order',
        derived_category='rules', categories='["rules"]', category_basis='resource kind', review_state='reviewed', record_type='court_rule',
        doc_subtype='rule', representation='original_document', file_type='docx', bytes=9000, jurisdiction_level='federal', state='Federal',
        states='["Federal"]', court_label_as_published='Tax Court', saved_at='2026-09-12', saved_lo='2026-09-12', saved_hi='2026-09-12',
        captured_at='2026-09-12', source_as_of='2025-12', source_lo='2025-12-01', source_hi='2025-12-31', source_as_of_basis='metadata.source_date',
        title='Rule 7069-1', display_title='Rule 7069-1', title_basis='retained record title', title_issue='none', text_chars=500),
    row(record_id='r3', dataset='focused', group_name='counties', original_kind='county_government_entry_unreviewed',
        base_kind='county_government_entry_unreviewed', derived_category='directories', categories='["directories"]',
        review_state='needs_content_review', record_type='county_page', representation='saved_page', file_type='html', bytes=114,
        jurisdiction_level='multi', state=None, states='["Alabama","Texas"]', multi_state=1, has_text=0, text_chars=0,
        title='https://swishercounty.gov/', display_title='swishercounty.gov', title_basis='source URL host', title_issue='url',
        validity='parked_redirect', validity_reason='JavaScript redirect to /lander; 114 bytes', capture_flags='["under_1kb","shared_hash_multi_host"]'),
    row(record_id='r4', dataset='judge_entities', group_name='judges', original_kind='Judge profile', base_kind='Judge profile',
        derived_category='judges', categories='["judges"]', review_state='not_applicable', record_type='judge_profile',
        representation='profile', file_type='jsonl', bytes=5000000, jurisdiction_level='federal', state='Texas', states='["Texas"]',
        title='Dorwin Wallace Suttle', display_title='Dorwin Wallace Suttle', title_basis='retained record title', title_issue='none'),
    row(record_id='r5', dataset='federal', group_name='federal', original_kind='federal_legal_reference_resource',
        base_kind='federal_legal_reference_resource', derived_category='guidance', categories='["guidance"]', review_state='not_applicable',
        record_type='source_reference', representation='observed_link', file_type='none', has_original=0, has_text=0, bytes=None,
        jurisdiction_level='federal', state='Federal', states='["Federal"]', saved_at='2026-09-18T17:37:00+00:00', saved_lo='2026-09-18',
        saved_hi='2026-09-18', captured_at='2026-09-18T17:37:00+00:00', title='DOJ Manual', display_title='DOJ Manual',
        title_basis='retained record title', title_issue='none', validity='no_capture', validity_reason='observed link only'),
]


class FacetFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.db = self.folder / 'facets.sqlite3'
        con = sqlite3.connect(self.db)
        con.executescript(schema.ddl())
        con.executemany(schema.insert_sql(), ROWS)
        con.executemany('INSERT INTO facet_categories VALUES(?,?)', [('r1', 'statutes'), ('r1', 'constitutions'), ('r2', 'rules'),
                                                                     ('r3', 'directories'), ('r4', 'judges'), ('r5', 'guidance')])
        con.executemany('INSERT INTO wrong_county_joins VALUES(?,?,?,?)', [('r3', '01021', 'parked-domain redirect shared by 90 hosts', 'a' * 64),
                                                                          ('r3', '48437', 'parked-domain redirect shared by 90 hosts', 'a' * 64)])
        con.executemany('INSERT INTO meta VALUES(?,?)', [('schema_version', schema.SCHEMA_VERSION), ('records', '5'),
                                                        ('built_at', '2026-09-19T12:00:00+00:00'), ('directory_records', '5')])
        con.commit(); con.close()
        self.publish()
        self.patcher = patch.object(facets, 'DATA', self.folder)
        self.patcher.start()
        facets.reset_cache()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def publish(self, ready=True, status='passed', sha=None):
        digest = sha or hashlib.sha256(self.db.read_bytes()).hexdigest()
        gate = {'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T12:00:00+00:00',
                'data_files': [{'path': 'facets.sqlite3', 'sha256': digest, 'rows': 5}], 'counts': {'records': 5}, 'checks': [],
                'qualification': 'test', 'license_ref': '', 'inputs': []}
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        facets.reset_cache()


class GateTests(FacetFixture):
    def test_ready_when_gate_and_hash_agree(self):
        status = facets.status()
        self.assertTrue(status['ready'])
        self.assertEqual(status['records'], 5)
        self.assertIsNotNone(facets.db_path())
        self.assertNotIn('reason', {k: v for k, v in status.items() if v})

    def test_tampered_database_fails_closed(self):
        with self.db.open('ab') as out: out.write(b' ')
        facets.reset_cache()
        self.assertFalse(facets.status()['ready'])
        self.assertIsNone(facets.db_path())
        self.assertEqual(facets.ids_for({}), [])
        self.assertIsNone(facets.facets_for('r1'))
        self.assertEqual(facets.facet_counts({})['total'], 0)
        self.assertFalse(facets.facet_counts({})['ready'])
        self.assertEqual(facets.excluded_records(), [])

    def test_not_ready_or_wrong_status_fails_closed(self):
        self.publish(ready=False)
        self.assertFalse(facets.status()['ready'])
        self.publish(status='building')
        self.assertFalse(facets.status()['ready'])
        self.publish(sha='0' * 64)
        self.assertFalse(facets.status()['ready'])

    def test_missing_folder_fails_closed(self):
        with patch.object(facets, 'DATA', self.folder / 'missing'):
            facets.reset_cache()
            self.assertFalse(facets.status()['ready'])
            self.assertEqual(facets.ids_for({'ftype': 'pdf'}), [])


class FilterTests(FacetFixture):
    def test_single_value_filters(self):
        self.assertEqual(facets.ids_for({'ftype': 'pdf'}), ['r1'])
        self.assertEqual(facets.ids_for({'rtype': 'court_rule'}), ['r2'])
        self.assertEqual(facets.ids_for({'review': 'not_applicable'}), ['r4', 'r5'])
        self.assertEqual(facets.ids_for({'jur_level': 'federal'}), ['r2', 'r4', 'r5'])
        self.assertEqual(facets.ids_for({'state': 'texas'}), ['r4'])
        self.assertEqual(facets.ids_for({'state': 'Federal'}), ['r2', 'r5'])
        self.assertEqual(facets.ids_for({'validity': 'ok'}), ['r1', 'r2', 'r4'])
        self.assertEqual(facets.ids_for({'dtype': 'observed_link'}), ['r5'])
        self.assertEqual(facets.ids_for({'subtype': 'rule'}), ['r2'])
        self.assertEqual(facets.ids_for({'dataset': 'focused'}), ['r1', 'r3'])

    def test_category_is_multi_membership(self):
        self.assertEqual(facets.ids_for({'category': 'constitutions'}), ['r1'])
        self.assertEqual(facets.ids_for({'category': 'statutes'}), ['r1'])
        self.assertEqual(facets.ids_for({'category': 'judges'}), ['r4'])

    def test_invalid_values_return_nothing(self):
        self.assertEqual(facets.ids_for({'ftype': 'exe'}), [])
        self.assertEqual(facets.ids_for({'review': "x' OR 1=1 --"}), [])
        self.assertEqual(facets.ids_for({'jur_level': 'galaxy'}), [])
        self.assertEqual(facets.ids_for({'date_type': 'saved', 'dfrom': 'yesterday'}), [])
        self.assertEqual(facets.ids_for({'state': 'x' * 200}), [])

    def test_date_filters_with_undated_toggle(self):
        self.assertEqual(facets.ids_for({'date_type': 'saved', 'dfrom': '2026-09-13'}), ['r1', 'r3', 'r4', 'r5'])
        self.assertEqual(facets.ids_for({'date_type': 'saved', 'dfrom': '2026-09-13', 'undated': '0'}), ['r1', 'r5'])
        self.assertEqual(facets.ids_for({'date_type': 'saved', 'dto': '2026-09-12', 'undated': '0'}), ['r2'])
        self.assertEqual(facets.ids_for({'date_type': 'saved', 'undated': '0'}), ['r1', 'r2', 'r5'])
        # partial-precision source date (2025-12) overlaps a December range but not January.
        self.assertEqual(facets.ids_for({'date_type': 'source_as_of', 'dfrom': '2025-12-15', 'dto': '2025-12-20', 'undated': '0'}), ['r2'])
        self.assertEqual(facets.ids_for({'date_type': 'source_as_of', 'dfrom': '2026-01-01', 'undated': '0'}), [])
        self.assertEqual(facets.ids_for({'dfrom': '2026-09-13'}), ['r1', 'r2', 'r3', 'r4', 'r5'])  # no date_type: bounds ignored

    def test_pagination_and_ordering(self):
        self.assertEqual(facets.ids_for({}, limit=2), ['r1', 'r2'])
        self.assertEqual(facets.ids_for({}, limit=2, offset=3), ['r4', 'r5'])
        self.assertEqual(facets.ids_for({}, limit='bad', offset=-1)[:1], ['r1'])

    def test_where_clause_is_embeddable(self):
        sql, args = facets.where_clause({'ftype': 'pdf', 'state': 'new jersey'}, alias='f')
        self.assertIn('f.file_type', sql)
        self.assertEqual(args, ['pdf', 'new jersey'])
        con = sqlite3.connect(self.db)
        try:
            found = con.execute('SELECT record_id FROM facets f WHERE ' + sql, args).fetchall()
        finally:
            con.close()
        self.assertEqual(found, [('r1',)])
        self.assertEqual(facets.where_clause({}, alias='f'), ('1', []))
        self.assertEqual(facets.where_clause({'ftype': 'nope'})[0], '0')
        with self.assertRaises(ValueError): facets.where_clause({}, alias='f; DROP')


class CountTests(FacetFixture):
    def test_counts_are_filtered_except_self(self):
        result = facets.facet_counts({'category': 'rules'})
        self.assertTrue(result['ready'])
        self.assertEqual(result['total'], 1)
        categories = {x['value']: x['count'] for x in result['facets']['category']}
        self.assertEqual(categories['statutes'], 1)
        self.assertEqual(categories['constitutions'], 1)
        self.assertEqual(categories['rules'], 1)
        self.assertEqual({x['value']: x['count'] for x in result['facets']['ftype']}, {'docx': 1})
        self.assertTrue(all(x['label'] for x in result['facets']['review']))

    def test_dates_coverage_and_totals(self):
        result = facets.facet_counts({})
        self.assertEqual(result['total'], 5)
        self.assertEqual(result['dates_coverage']['saved'], {'known': 3, 'unknown': 2})
        self.assertEqual(result['dates_coverage']['source_as_of'], {'known': 1, 'unknown': 4})
        self.assertEqual({x['value']: x['count'] for x in result['facets']['validity']}, {'ok': 3, 'parked_redirect': 1, 'no_capture': 1})
        self.assertEqual({x['value']: x['count'] for x in result['facets']['state']}, {'New Jersey': 1, 'Federal': 2, 'Texas': 1})
        self.assertIn('qualification', result)


class RecordTests(FacetFixture):
    def test_facets_for_is_path_free_with_bases(self):
        item = facets.facets_for('r3')
        self.assertEqual(item['validity'], 'parked_redirect')
        self.assertEqual(item['states'], ['Alabama', 'Texas'])
        self.assertTrue(item['multi_state'])
        self.assertEqual(item['capture_flags'], ['under_1kb', 'shared_hash_multi_host'])
        self.assertEqual(item['display_title'], 'swishercounty.gov')
        self.assertIsNone(facets.facets_for('missing'))
        self.assertIsNone(facets.facets_for(None))
        dumped = json.dumps([item, facets.facets_for('r1'), facets.facet_counts({}), facets.excluded_records(), facets.status()])
        self.assertNotIn(self.folder.name, dumped)
        self.assertNotIn('_path', dumped)
        self.assertNotIn('sqlite3', dumped)
        self.assertEqual(facets.facets_for('r2')['dates']['source_as_of'], {'value': '2025-12', 'basis': 'metadata.source_date'})
        self.assertEqual(facets.facets_for('r2')['category_label'], 'Rules & orders')

    def test_excluded_records_and_wrong_joins(self):
        excluded = facets.excluded_records()
        self.assertEqual([(x['record_id'], x['validity']) for x in excluded], [('r3', 'parked_redirect'), ('r5', 'no_capture')])
        self.assertEqual([x['record_id'] for x in facets.excluded_records(validity='parked_redirect')], ['r3'])
        joins = facets.wrong_county_joins()
        self.assertEqual([(x['record_id'], x['geoid']) for x in joins], [('r3', '01021'), ('r3', '48437')])
        self.assertEqual(facets.wrong_county_joins(geoid='48437')[0]['reason'], 'parked-domain redirect shared by 90 hosts')
        self.assertEqual(facets.excluded_ids(), ('r3', 'r5'))


if __name__ == '__main__':
    unittest.main()
