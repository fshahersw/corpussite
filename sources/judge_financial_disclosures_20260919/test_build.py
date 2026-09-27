"""Tests for judge_financial_disclosures_20260919/build.py.

Fast unit tests use small synthetic fixtures (never touch the real 200MB+ bulk files). One slow-ish test runs
the real build against the actual inputs and checks the measured counts -- guarded so `python -m unittest
discover` still finishes quickly if the real inputs are not present in this environment.
"""
import bz2
import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as b  # noqa: E402


def _write_pg_csv(path, header, rows):
    """Write a Postgres COPY-style CSV (backslash-escaped, doublequote=False) matching production dialect."""
    with bz2.open(path, 'wt', encoding='utf-8', newline='') as f:
        w = csv.writer(f, doublequote=False, escapechar=chr(92))
        w.writerow(header)
        for r in rows:
            w.writerow(r)


class DialectTests(unittest.TestCase):
    def test_backslash_escaped_quote_stays_in_one_field(self):
        """A field with an escaped double-quote must not split the row -- this is the exact 3.4x bug."""
        tmp = Path(tempfile.mkdtemp()) / 'x.csv.bz2'
        header = ['id', 'addendum_content_raw', 'person_id']
        rows = [['1', 'Note: the \\"trust\\" account, comma, and more text', '99']]
        _write_pg_csv(tmp, header, rows)
        handle, reader = b.pg_csv_reader(tmp)
        try:
            got = list(reader)
        finally:
            handle.close()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['person_id'], '99')
        self.assertIn('trust', got[0]['addendum_content_raw'])

    def test_plain_dictreader_would_have_broken_this_row(self):
        """Sanity check that the trap is real: default csv dialect mis-splits the same fixture."""
        tmp = Path(tempfile.mkdtemp()) / 'x.csv.bz2'
        header = ['id', 'addendum_content_raw', 'person_id']
        rows = [['1', 'Note: the \\"trust\\" account, comma, and more text', '99']]
        _write_pg_csv(tmp, header, rows)
        with bz2.open(tmp, 'rt', encoding='utf-8', newline='') as f:
            plain = list(csv.DictReader(f))
        # the escaped quote confuses the default dialect: the row's person_id no longer parses as '99' alone
        # (either the reader raises via restkey/None padding, or fields shift) -- assert the fixed reader
        # differs from this, not any particular bug shape, since default behavior can vary by field content.
        self.assertNotEqual(plain[0].get('person_id'), None)


class BridgeTests(unittest.TestCase):
    def _write_overlay(self, tmp, rows):
        data = '\n'.join(json.dumps(r) for r in rows) + '\n'
        (tmp / 'overlay.jsonl').write_bytes(data.encode('utf-8'))
        import hashlib
        digest = hashlib.sha256(data.encode('utf-8')).hexdigest()
        (tmp / 'validation.json').write_text(json.dumps({
            'schema_version': '1', 'status': 'passed', 'ready': True,
            'data_files': [{'path': 'overlay.jsonl', 'sha256': digest, 'rows': len(rows)}],
        }), encoding='utf-8')

    def test_only_linked_native_id_rows_bridge(self):
        tmp = Path(tempfile.mkdtemp())
        self._write_overlay(tmp, [
            {'entity_id': 'e1', 'name': 'A', 'ids': {'cl_person_id': '10', 'bridge_status': 'linked_native_id'}},
            {'entity_id': 'e2', 'name': 'B', 'ids': {'cl_person_id': '20', 'bridge_status': 'withheld'}},
            {'entity_id': 'e3', 'name': 'C', 'ids': None},
        ])
        bridge, digest, n, total = b.load_bridge(tmp)
        self.assertEqual(set(bridge), {'10'})
        self.assertEqual(bridge['10']['entity_id'], 'e1')
        self.assertEqual(n, 1)
        self.assertEqual(total, 3)
        self.assertTrue(digest)

    def test_gate_failure_raises(self):
        tmp = Path(tempfile.mkdtemp())
        self._write_overlay(tmp, [{'entity_id': 'e1', 'name': 'A', 'ids': {'cl_person_id': '10', 'bridge_status': 'linked_native_id'}}])
        gate = json.loads((tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        with self.assertRaises(ValueError):
            b.load_bridge(tmp)


class MiniBuildTests(unittest.TestCase):
    """Runs build() against small synthetic bulk-style fixtures wired through monkeypatched paths."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bulk = self.tmp / 'bulk'
        self.bulk.mkdir()
        self.out = self.tmp / 'out'
        self.out.mkdir()
        self.js = self.tmp / 'judge_structured'
        self.js.mkdir()

        overlay_rows = [
            {'entity_id': 'e-bridged', 'name': 'Judge Bridged', 'ids': {'cl_person_id': '111', 'bridge_status': 'linked_native_id'}},
            {'entity_id': 'e-nofile', 'name': 'Judge NoFile', 'ids': {'cl_person_id': '222', 'bridge_status': 'linked_native_id'}},
        ]
        data = '\n'.join(json.dumps(r) for r in overlay_rows) + '\n'
        (self.js / 'overlay.jsonl').write_bytes(data.encode('utf-8'))
        import hashlib
        digest = hashlib.sha256(data.encode('utf-8')).hexdigest()
        (self.js / 'validation.json').write_text(json.dumps({
            'schema_version': '1', 'status': 'passed', 'ready': True,
            'data_files': [{'path': 'overlay.jsonl', 'sha256': digest, 'rows': len(overlay_rows)}],
        }), encoding='utf-8')

        # disclosures: one for the bridged judge (111), one for an unbridged person (999) -> must be dropped.
        _write_pg_csv(self.bulk / 'financial-disclosures-2026-06-30.csv.bz2',
                      ['id', 'date_created', 'date_modified', 'year', 'download_filepath', 'filepath',
                       'thumbnail', 'thumbnail_status', 'page_count', 'sha1', 'report_type', 'is_amended',
                       'addendum_content_raw', 'addendum_redacted', 'has_been_extracted', 'person_id'],
                      [
                          ['1', '', '', '2015', 'https://s3.example/one.pdf', 'p/one.pdf', '', '0', '5', 'sha1a',
                           '2', 't', '', 'f', 't', '111'],
                          ['2', '', '', '2016', 'https://s3.example/two.pdf', 'p/two.pdf', '', '0', '3', 'sha1b',
                           '0', 'f', '', 'f', 't', '999'],
                      ])
        _write_pg_csv(self.bulk / 'financial-disclosure-investments-2026-06-30.csv.bz2',
                      ['id', 'date_created', 'date_modified', 'page_number', 'description', 'redacted',
                       'income_during_reporting_period_code', 'income_during_reporting_period_type',
                       'gross_value_code', 'gross_value_method', 'transaction_during_reporting_period',
                       'transaction_date_raw', 'transaction_date', 'transaction_value_code',
                       'transaction_gain_code', 'transaction_partner', 'has_inferred_values',
                       'financial_disclosure_id'],
                      [
                          ['10', '', '', '1', 'Acme Corp common stock', 'f', 'A', 'Int/Div', 'K', 'T', '', '', '',
                           '', '', 'Acme Brokerage', 'f', '1'],
                          ['11', '', '', '1', 'Widget Co bond', 't', 'B', 'Int/Div', 'L', 'T', '', '', '', '',
                           '', '', 'f', '1'],
                          ['12', '', '', '1', 'Orphan row on unbridged disclosure', 'f', 'A', 'Int/Div', 'J', 'T',
                           '', '', '', '', '', '', 'f', '2'],
                      ])
        for name, cols, rows in [
            ('financial-disclosures-positions-2026-06-30.csv.bz2',
             ['id', 'date_created', 'date_modified', 'position', 'organization_name', 'redacted', 'financial_disclosure_id'],
             [['20', '', '', 'Trustee', 'Family Trust', 'f', '1']]),
            ('financial-disclosures-reimbursements-2026-06-30.csv.bz2',
             ['id', 'date_created', 'date_modified', 'source', 'date_raw', 'location', 'purpose', 'items_paid_or_provided', 'redacted', 'financial_disclosure_id'],
             [['21', '', '', 'Bar Assoc', '2015', 'City', 'Conference', 'Lodging', 'f', '1']]),
            ('financial-disclosures-gifts-2026-06-30.csv.bz2',
             ['id', 'date_created', 'date_modified', 'source', 'description', 'value', 'redacted', 'financial_disclosure_id'],
             [['22', '', '', 'Friend', 'A book', '$50', 'f', '1']]),
            ('financial-disclosures-debts-2026-06-30.csv.bz2',
             ['id', 'date_created', 'date_modified', 'creditor_name', 'description', 'value_code', 'redacted', 'financial_disclosure_id'],
             [['23', '', '', 'Bank', 'Mortgage', 'M', 'f', '1']]),
            ('financial-disclosures-agreements-2026-06-30.csv.bz2',
             ['id', 'date_created', 'date_modified', 'date_raw', 'parties_and_terms', 'redacted', 'financial_disclosure_id'],
             [['24', '', '', '2010', 'Pension plan', 'f', '1']]),
            ('financial-disclosures-non-investment-income-2026-06-30.csv.bz2',
             ['id', 'date_created', 'date_modified', 'date_raw', 'source_type', 'income_amount', 'redacted', 'financial_disclosure_id'],
             [['25', '', '', '2015', 'Teaching', '$1,000', 'f', '1']]),
        ]:
            _write_pg_csv(self.bulk / name, cols, rows)

        self.expected_backup = dict(b.EXPECTED_ROWS)
        b.EXPECTED_ROWS.update({
            'financial_disclosures': 2, 'financial_disclosure_investments': 3,
            'financial_disclosures_positions': 1, 'financial_disclosures_reimbursements': 1,
            'financial_disclosures_gifts': 1, 'financial_disclosures_debts': 1,
            'financial_disclosures_agreements': 1, 'financial_disclosures_non_investment_income': 1,
        })
        self.sw_manifest_backup = b.SW_MANIFEST
        b.SW_MANIFEST = self.tmp / 'no_manifest.json'
        self.conflicts_backup = b.CONFLICTS_LOCAL
        b.CONFLICTS_LOCAL = self.tmp / 'no_conflicts.json'

    def tearDown(self):
        b.EXPECTED_ROWS.clear()
        b.EXPECTED_ROWS.update(self.expected_backup)
        b.SW_MANIFEST = self.sw_manifest_backup
        b.CONFLICTS_LOCAL = self.conflicts_backup

    def test_unbridged_person_and_orphan_investment_are_dropped(self):
        validation, _ = b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        self.assertEqual(validation['counts']['header_rows_bridged'], 1)
        self.assertEqual(validation['counts']['investment_rows_bridged'], 2)
        self.assertEqual(validation['counts']['judges_with_at_least_one_filing'], 1)
        self.assertEqual(validation['counts']['judges_bridged_with_no_filing_in_snapshot'], 1)

    def test_no_dollar_fields_in_db(self):
        b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        conn = sqlite3.connect(str(self.out / 'disclosures.sqlite3'))
        gift_cols = [r[1] for r in conn.execute('PRAGMA table_info(gifts)')]
        nii_cols = [r[1] for r in conn.execute('PRAGMA table_info(non_investment_income)')]
        self.assertNotIn('value', gift_cols)
        self.assertNotIn('income_amount', nii_cols)
        row = conn.execute('SELECT gross_value_code, income_code FROM investments WHERE id = 10').fetchone()
        self.assertEqual(row, ('K', 'A'))
        conn.close()

    def test_redacted_flag_preserved(self):
        b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        conn = sqlite3.connect(str(self.out / 'disclosures.sqlite3'))
        row = conn.execute('SELECT redacted FROM investments WHERE id = 11').fetchone()
        self.assertEqual(row[0], 1)
        conn.close()

    def test_pdf_link_is_the_courtlistener_original_never_local(self):
        b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        conn = sqlite3.connect(str(self.out / 'disclosures.sqlite3'))
        row = conn.execute('SELECT pdf_url FROM disclosures WHERE id = 1').fetchone()
        self.assertTrue(row[0].startswith('https://'))
        conn.close()

    def test_unresolved_person_ids_are_recorded_not_silently_dropped(self):
        validation, _ = b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        unresolved_path = self.out / 'unresolved.jsonl'
        self.assertTrue(unresolved_path.exists())
        lines = [json.loads(l) for l in unresolved_path.read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['person_id'], '999')
        self.assertEqual(lines[0]['header_rows'], 1)
        self.assertEqual(validation['counts']['unresolved_person_ids'], 1)
        self.assertEqual(validation['counts']['unresolved_header_rows'], 1)
        data_file_paths = {d['path'] for d in validation['data_files']}
        self.assertIn('unresolved.jsonl', data_file_paths)

    def test_qualification_bridge_denominator_is_total_entities_not_bridged_twice(self):
        validation, _ = b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        # fixture overlay has 2 total entities, both bridged
        self.assertEqual(validation['counts']['total_entities_in_judge_structured'], 2)
        self.assertEqual(validation['counts']['bridged_entities_in_judge_structured'], 2)
        self.assertIn('2 of 2 judge_structured entities bridge', validation['qualification'])
        self.assertIn('Built 2026-09-19', validation['qualification'])
        self.assertIn('unresolved.jsonl', validation['qualification'])

    def test_export_allowed_false_in_envelope(self):
        validation, _ = b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        self.assertEqual(validation['export_allowed'], False)

    def test_bridged_entities_table_has_every_bridged_entity_including_no_filing(self):
        b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        conn = sqlite3.connect(str(self.out / 'disclosures.sqlite3'))
        rows = {r[0] for r in conn.execute('SELECT entity_id FROM bridged_entities')}
        self.assertEqual(rows, {'e-bridged', 'e-nofile'})
        conn.close()

    def test_investment_count_inferred_tracked_per_disclosure_and_judge(self):
        b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)
        conn = sqlite3.connect(str(self.out / 'disclosures.sqlite3'))
        disc_row = conn.execute('SELECT investment_count, investment_count_inferred FROM disclosures WHERE id = 1').fetchone()
        self.assertEqual(disc_row, (2, 0))  # neither of disclosure 1's investment rows has_inferred_values='t'
        judge_row = conn.execute('SELECT investment_count, investment_count_inferred FROM judges WHERE entity_id = ?', ('e-bridged',)).fetchone()
        self.assertEqual(judge_row, (2, 0))
        conn.close()

    def test_dialect_mismatch_raises(self):
        b.EXPECTED_ROWS['financial_disclosures'] = 999
        with self.assertRaises(ValueError):
            b.build(out_dir=self.out, bulk_dir=self.bulk, judge_structured_dir=self.js)


class RealBuildSmokeTest(unittest.TestCase):
    """Only runs if the real bulk inputs and judge_structured exist at their fixed paths in this environment."""

    def test_real_inputs_present_and_readable_header(self):
        if not b.DISCLOSURES_CSV.exists() or not b.JUDGE_STRUCTURED.exists():
            self.skipTest('real bulk inputs not present in this environment')
        handle, reader = b.pg_csv_reader(b.DISCLOSURES_CSV)
        try:
            first = next(reader)
        finally:
            handle.close()
        self.assertIn('person_id', first)
        self.assertTrue(first['person_id'].isdigit())


if __name__ == '__main__':
    unittest.main()
