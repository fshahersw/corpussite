"""Tests for the verdict_reports adapter (fail-closed, hash-gated, read-only).

Written first against the contract in the build task: listing()/detail()/for_mdl()/summary(), the exact
row/detail/for_mdl shapes, and the fail-closed gate. Uses a small hand-built SQLite fixture matching the
real schema so these tests do not depend on the real (large) build; a separate RealDataTest class checks
the real supplement when it is present and skips otherwise.
"""
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import verdict_reports as adapter


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_SCHEMA = '''
    CREATE TABLE reports(
        id TEXT NOT NULL, rank INTEGER, year INTEGER, jurisdiction TEXT, county TEXT, result_type TEXT,
        primary_type TEXT, full_type TEXT, amount_raw TEXT, amount_numeric REAL, amount_band TEXT,
        title TEXT, title_redacted INTEGER, redaction_basis TEXT, firms TEXT, attorneys TEXT,
        mass_tort_tags TEXT, is_mass_tort INTEGER, national_list INTEGER, scope_count INTEGER,
        listed_scopes TEXT, list_url TEXT, list_slug TEXT, mdl_number INTEGER, mdl_link_basis TEXT,
        mdl_registry_status TEXT, subtitle TEXT, source_row_ordinal INTEGER
    );
    CREATE UNIQUE INDEX reports_id ON reports(id);
    CREATE VIRTUAL TABLE reports_fts USING fts5(
        title, practice_area, firms, attorneys, jurisdiction, county, tags,
        content='reports', content_rowid='rowid', tokenize='unicode61'
    );
    CREATE TABLE facet_counts(facet TEXT, value TEXT, n INTEGER);
'''

_COLUMNS = ['id', 'rank', 'year', 'jurisdiction', 'county', 'result_type', 'primary_type', 'full_type',
            'amount_raw', 'amount_numeric', 'amount_band', 'title', 'title_redacted', 'redaction_basis',
            'firms', 'attorneys', 'mass_tort_tags', 'is_mass_tort', 'national_list', 'scope_count',
            'listed_scopes', 'list_url', 'list_slug', 'mdl_number', 'mdl_link_basis', 'mdl_registry_status',
            'subtitle', 'source_row_ordinal']

ROW_A = {  # redacted plaintiff, mass tort tagged, not MDL-linked
    'id': 'vsr-aaaa', 'rank': 1, 'year': 2024, 'jurisdiction': 'california', 'county': 'los-angeles',
    'result_type': 'verdict', 'primary_type': 'Product Liability', 'full_type': 'Product Liability, Talc',
    'amount_raw': '$5,000,000.00', 'amount_numeric': 5000000.0, 'amount_band': '1m_10m',
    'title': 'Individual plaintiff v. Acme Talc Corp.', 'title_redacted': 1, 'redaction_basis': 'plaintiff_redacted',
    'firms': ['Smith Law'], 'attorneys': 'Jane Smith of Smith Law', 'mass_tort_tags': ['Talc'],
    'is_mass_tort': 1, 'national_list': 1, 'scope_count': 2, 'listed_scopes': ['california'],
    'list_url': 'https://example.com/list-a', 'list_slug': 'top-10-verdicts', 'mdl_number': None,
    'mdl_link_basis': None, 'mdl_registry_status': None, 'subtitle': 'california · los-angeles · 2024',
    'source_row_ordinal': 0,
}
ROW_B = {  # kept org-vs-org caption, MDL-linked, settlement
    'id': 'vsr-bbbb', 'rank': 3, 'year': 2025, 'jurisdiction': 'texas', 'county': None,
    'result_type': 'settlement', 'primary_type': 'Breach of Contract', 'full_type': 'Breach of Contract',
    'amount_raw': '$50,000,000.00', 'amount_numeric': 50000000.0, 'amount_band': '10m_100m',
    'title': 'Acme Corp. v. Widget LLC', 'title_redacted': 0, 'redaction_basis': 'both_sides_org_kept',
    'firms': ['Big Firm LLP'], 'attorneys': 'John Doe of Big Firm LLP', 'mass_tort_tags': [],
    'is_mass_tort': 0, 'national_list': 0, 'scope_count': 1, 'listed_scopes': ['texas'],
    'list_url': 'https://example.com/list-b', 'list_slug': 'top-50-settlements', 'mdl_number': 9001,
    'mdl_link_basis': 'caption_matches_jpml_registry_title', 'mdl_registry_status': 'pending',
    'subtitle': 'texas · 2025', 'source_row_ordinal': 1,
}
ROW_C = {  # amount not stated
    'id': 'vsr-cccc', 'rank': 7, 'year': 2024, 'jurisdiction': 'florida', 'county': None,
    'result_type': 'verdict', 'primary_type': 'Medical Malpractice', 'full_type': 'Medical Malpractice',
    'amount_raw': None, 'amount_numeric': None, 'amount_band': 'not_stated',
    'title': 'Individual plaintiff v. Individual defendant', 'title_redacted': 1,
    'redaction_basis': 'plaintiff_redacted+defendant_redacted', 'firms': [], 'attorneys': '',
    'mass_tort_tags': [], 'is_mass_tort': 0, 'national_list': 0, 'scope_count': 1,
    'listed_scopes': ['florida'], 'list_url': 'https://example.com/list-c', 'list_slug': 'top-10-verdicts',
    'mdl_number': None, 'mdl_link_basis': None, 'mdl_registry_status': None,
    'subtitle': 'florida · 2024', 'source_row_ordinal': 2,
}
ALL_ROWS = [ROW_A, ROW_B, ROW_C]


def _row_tuple(row):
    return tuple(
        json.dumps(row[key]) if key in ('firms', 'mass_tort_tags', 'listed_scopes') else row[key]
        for key in _COLUMNS
    )


class _FixtureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / 'verdict_settlement_reports_20260919'
        self.folder.mkdir()
        self.db_path = self.folder / adapter.DB_NAME
        connection = sqlite3.connect(self.db_path)
        connection.executescript(_SCHEMA)
        connection.executemany(f'INSERT INTO reports({",".join(_COLUMNS)}) VALUES({",".join("?"*len(_COLUMNS))})',
                                (_row_tuple(r) for r in ALL_ROWS))
        connection.execute('''INSERT INTO reports_fts(rowid, title, practice_area, firms, attorneys, jurisdiction, county, tags)
                               SELECT rowid, title, primary_type, firms, attorneys, jurisdiction, coalesce(county,''), mass_tort_tags FROM reports''')
        connection.execute("INSERT INTO facet_counts VALUES('result_type','verdict',2)")
        connection.execute("INSERT INTO facet_counts VALUES('result_type','settlement',1)")
        connection.execute("INSERT INTO facet_counts VALUES('jurisdiction','california',1)")
        connection.execute("INSERT INTO facet_counts VALUES('jurisdiction','texas',1)")
        connection.execute("INSERT INTO facet_counts VALUES('jurisdiction','florida',1)")
        connection.execute("INSERT INTO facet_counts VALUES('year','2024',2)")
        connection.execute("INSERT INTO facet_counts VALUES('year','2025',1)")
        connection.execute("INSERT INTO facet_counts VALUES('primary_type','Product Liability',1)")
        connection.execute("INSERT INTO facet_counts VALUES('primary_type','Breach of Contract',1)")
        connection.execute("INSERT INTO facet_counts VALUES('primary_type','Medical Malpractice',1)")
        connection.execute("INSERT INTO facet_counts VALUES('amount_band','1m_10m',1)")
        connection.execute("INSERT INTO facet_counts VALUES('amount_band','10m_100m',1)")
        connection.execute("INSERT INTO facet_counts VALUES('amount_band','not_stated',1)")
        connection.execute("INSERT INTO facet_counts VALUES('mass_tort','yes',1)")
        connection.commit()
        connection.close()
        gate = {
            'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': '2026-09-19T00:00:00Z',
            'data_files': [{'path': adapter.DB_NAME, 'sha256': _digest(self.db_path.read_bytes()), 'rows': 3}],
            'counts': {'total_reports': 3, 'mass_tort_tagged': 1, 'mdl_linked': 1},
            'checks': [], 'qualification': 'test fixture qualification: reported by the publisher; not verified against a court record.',
            'license_ref': 'publisher_terms_prohibit_reuse_local_research_only', 'export_allowed': False, 'inputs': [],
        }
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        self._orig_data = adapter.DATA
        adapter.DATA = self.folder

    def tearDown(self):
        adapter.DATA = self._orig_data
        adapter._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ListingShapeTests(_FixtureBase):
    def test_available_and_total(self):
        result = adapter.listing({})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['page'], 1)

    def test_filters_present_with_expected_names(self):
        result = adapter.listing({})
        names = {f['name'] for f in result['filters']}
        self.assertEqual(names, {'q', 'type', 'state', 'year', 'area', 'mass_tort', 'amount_band', 'mdl'})

    def test_columns_are_the_five_cell_keys(self):
        result = adapter.listing({})
        keys = [c['key'] for c in result['columns']]
        self.assertEqual(keys, ['amount', 'type', 'area', 'state', 'year'])

    def test_result_row_shape(self):
        result = adapter.listing({})
        row = next(r for r in result['results'] if r['id'] == 'vsr-aaaa')
        self.assertEqual(row['title'], 'Individual plaintiff v. Acme Talc Corp.')
        self.assertEqual(row['subtitle'], 'California · Los Angeles · 2024')  # place slugs are shown as words
        self.assertEqual(row['cells'], {'amount': '$5,000,000.00', 'type': 'Verdict', 'area': 'Product Liability',
                                         'state': 'California', 'year': 2024})
        self.assertLessEqual(len(row['badges']), 2)
        self.assertTrue(any(link.get('url') == 'https://example.com/list-a' for link in row['links']))

    def test_amount_cell_is_not_stated_placeholder_when_absent(self):
        result = adapter.listing({})
        row = next(r for r in result['results'] if r['id'] == 'vsr-cccc')
        self.assertEqual(row['cells']['amount'], 'Not stated')

    def test_qualification_mentions_publisher_not_verified(self):
        result = adapter.listing({})
        self.assertIn('publisher', result['qualification'].lower())
        self.assertIn('not verified against a court record', result['qualification'].lower())


class ListingFilterTests(_FixtureBase):
    def test_filter_by_type(self):
        result = adapter.listing({'type': 'settlement'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-bbbb')

    def test_filter_by_state(self):
        result = adapter.listing({'state': 'texas'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-bbbb')

    def test_filter_by_year(self):
        result = adapter.listing({'year': '2024'})
        self.assertEqual(result['total'], 2)

    def test_filter_by_area(self):
        result = adapter.listing({'area': 'Medical Malpractice'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-cccc')

    def test_filter_by_mass_tort(self):
        result = adapter.listing({'mass_tort': 'yes'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-aaaa')

    def test_filter_by_amount_band(self):
        result = adapter.listing({'amount_band': 'not_stated'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-cccc')

    def test_filter_by_mdl(self):
        result = adapter.listing({'mdl': '9001'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-bbbb')

    def test_filter_by_mdl_no_match(self):
        result = adapter.listing({'mdl': '4242'})
        self.assertEqual(result['total'], 0)

    def test_q_search_matches_title(self):
        result = adapter.listing({'q': 'Widget'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-bbbb')

    def test_q_search_matches_firm_name(self):
        result = adapter.listing({'q': 'Smith Law'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'vsr-aaaa')

    def test_q_search_ignores_stop_words(self):
        # 'the' alone carries no signal and must not raise or silently match nothing forever.
        result = adapter.listing({'q': 'the'})
        self.assertTrue(result['available'])

    def test_bad_params_never_raise(self):
        for params in ({'page': 'nope'}, {'limit': -5}, {'year': 'abcd'}, {'mdl': 'xyz'},
                        {'amount_band': 'invalid_band'}, {'q': None}, {'unexpected_param': 'x'}, None):
            result = adapter.listing(params)
            self.assertTrue(result['available'])

    def test_limit_capped_at_100(self):
        result = adapter.listing({'limit': '99999'})
        self.assertLessEqual(result['limit'], 100)


class DetailTests(_FixtureBase):
    def test_detail_unknown_id_is_none(self):
        self.assertIsNone(adapter.detail('vsr-does-not-exist'))

    def test_detail_shape(self):
        row = adapter.detail('vsr-aaaa')
        self.assertEqual(row['title'], 'Individual plaintiff v. Acme Talc Corp.')
        fact_labels = [f[0] for f in row['facts']]
        self.assertIn('Reported by (firm)', fact_labels)
        self.assertIn('Reported by (attorney)', fact_labels)
        joined = ' '.join(f'{k}: {v}' for k, v in row['facts'])
        self.assertIn('Smith Law', joined)
        self.assertTrue(any(l.get('url') == 'https://example.com/list-a' for l in row['links']))

    def test_detail_every_row_carries_the_not_verified_label(self):
        for row_id in ('vsr-aaaa', 'vsr-bbbb', 'vsr-cccc'):
            row = adapter.detail(row_id)
            joined = ' '.join(f'{k}: {v}' for k, v in row['facts']).lower()
            self.assertIn('not verified against a court record', joined)

    def test_detail_related_mdl_link_when_linked(self):
        row = adapter.detail('vsr-bbbb')
        self.assertTrue(any(l.get('url') == '#mdl/9001' for l in row['links']))

    def test_detail_no_mdl_fact_when_not_linked(self):
        row = adapter.detail('vsr-aaaa')
        self.assertFalse(any(l.get('url', '').startswith('#mdl/') for l in row['links']))

    def test_detail_bad_id_never_raises(self):
        for bad in (None, '', 123, '; DROP TABLE reports;'):
            try:
                adapter.detail(bad)
            except Exception as exc:  # pragma: no cover
                self.fail(f'detail raised on {bad!r}: {exc!r}')


class ForMdlTests(_FixtureBase):
    def test_for_mdl_returns_block_when_linked(self):
        block = adapter.for_mdl(9001)
        self.assertIsNotNone(block)
        self.assertEqual(block['mdl_number'], 9001)
        self.assertEqual(block['total'], 1)
        self.assertEqual(len(block['results']), 1)
        self.assertEqual(block['link'], '#verdict-reports?mdl=9001')
        self.assertIn('qualification', block)

    def test_for_mdl_caps_at_10_results(self):
        block = adapter.for_mdl(9001)
        self.assertLessEqual(len(block['results']), 10)

    def test_for_mdl_none_when_no_rows(self):
        self.assertIsNone(adapter.for_mdl(424242))

    def test_for_mdl_bad_input_never_raises(self):
        for bad in (None, 'abc', -1, object()):
            try:
                result = adapter.for_mdl(bad)
            except Exception as exc:  # pragma: no cover
                self.fail(f'for_mdl raised on {bad!r}: {exc!r}')
            else:
                self.assertIsNone(result)


class SummaryTests(_FixtureBase):
    def test_summary_available_and_counts(self):
        result = adapter.summary()
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['by_type'].get('verdict'), 2)
        self.assertEqual(result['by_type'].get('settlement'), 1)
        self.assertEqual(result['by_state'].get('texas'), 1)
        self.assertEqual(result['by_year'].get('2024'), 2)

    def test_summary_fails_closed_when_gate_closed(self):
        (self.folder / 'validation.json').write_text('not json', encoding='utf-8')
        adapter._CACHE.clear()
        result = adapter.summary()
        self.assertFalse(result['available'])


class FailClosedTests(_FixtureBase):
    def test_no_original_bytes_served(self):
        self.assertFalse(hasattr(adapter, 'original'))

    def test_gate_fails_closed_on_hash_tamper(self):
        with open(self.db_path, 'ab') as handle:
            handle.write(b'tampered')
        adapter._CACHE.clear()
        result = adapter.listing({})
        self.assertFalse(result['available'])
        self.assertIsNone(adapter.detail('vsr-aaaa'))
        self.assertIsNone(adapter.for_mdl(9001))
        self.assertFalse(adapter.summary()['available'])

    def test_gate_fails_closed_when_status_not_passed(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'draft'
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        self.assertFalse(adapter.listing({})['available'])

    def test_gate_fails_closed_when_ready_false(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        self.assertFalse(adapter.listing({})['available'])

    def test_gate_fails_closed_when_validation_missing(self):
        (self.folder / 'validation.json').unlink()
        adapter._CACHE.clear()
        self.assertFalse(adapter.listing({})['available'])
        self.assertIsNone(adapter.detail('vsr-aaaa'))


class RealDataTest(unittest.TestCase):
    def test_real_supplement_if_built(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        result = adapter.listing({})
        if not result['available']:
            self.skipTest('supplement gate not open: %s' % result.get('reason'))
        self.assertEqual(result['total'], 3312)
        summary = adapter.summary()
        self.assertEqual(summary['total'], 3312)
        mass_tort = adapter.listing({'mass_tort': 'yes'})
        self.assertEqual(mass_tort['total'], 670)

    def test_real_data_never_exposes_a_known_redacted_surname(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        for surname in ('Jogani', 'Alkiviades', 'Rosenblatt', 'Latorre'):
            result = adapter.listing({'q': surname})
            if not result['available']:
                self.skipTest('supplement gate not open')
            for row in result['results']:
                self.assertNotIn(surname, row['title'])


if __name__ == '__main__':
    unittest.main()
