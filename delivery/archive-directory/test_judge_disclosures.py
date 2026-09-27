"""Tests for the judge_disclosures adapter (generic view contract, fail-closed hash gate)."""
import bz2
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge_disclosures  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'sources/judge_financial_disclosures_20260919'))
import build as builder  # noqa: E402

REAL = judge_disclosures.DATA


def _write_pg_csv(path, header, rows):
    with bz2.open(path, 'wt', encoding='utf-8', newline='') as f:
        w = csv.writer(f, doublequote=False, escapechar=chr(92))
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _make_fixture(tmp):
    """Build a tiny real SQLite via the real build() (not a hand-rolled DB) so the adapter is tested against
    the actual schema build.py produces, not a re-typed copy of it."""
    bulk = tmp / 'bulk'
    bulk.mkdir()
    out = tmp / 'out'
    out.mkdir()
    js = tmp / 'judge_structured'
    js.mkdir()

    overlay_rows = [
        {'entity_id': 'e-alpha', 'name': 'Judge Alpha', 'ids': {'cl_person_id': '111', 'bridge_status': 'linked_native_id'}},
        {'entity_id': 'e-noholdings', 'name': 'Judge NoHoldings', 'ids': {'cl_person_id': '333', 'bridge_status': 'linked_native_id'}},
        {'entity_id': 'e-nofile', 'name': 'Judge NoFile', 'ids': {'cl_person_id': '222', 'bridge_status': 'linked_native_id'}},
        {'entity_id': 'e-earlyholdings', 'name': 'Judge EarlyHoldings', 'ids': {'cl_person_id': '444', 'bridge_status': 'linked_native_id'}},
        {'entity_id': 'e-unbridged-in-overlay', 'name': 'Judge Unbridged', 'ids': {'cl_person_id': '', 'bridge_status': 'withheld'}},
    ]
    data = '\n'.join(json.dumps(r) for r in overlay_rows) + '\n'
    (js / 'overlay.jsonl').write_bytes(data.encode('utf-8'))
    digest = hashlib.sha256(data.encode('utf-8')).hexdigest()
    (js / 'validation.json').write_text(json.dumps({
        'schema_version': '1', 'status': 'passed', 'ready': True,
        'data_files': [{'path': 'overlay.jsonl', 'sha256': digest, 'rows': len(overlay_rows)}],
    }), encoding='utf-8')

    _write_pg_csv(bulk / 'financial-disclosures-2026-06-30.csv.bz2',
                  ['id', 'date_created', 'date_modified', 'year', 'download_filepath', 'filepath', 'thumbnail',
                   'thumbnail_status', 'page_count', 'sha1', 'report_type', 'is_amended', 'addendum_content_raw',
                   'addendum_redacted', 'has_been_extracted', 'person_id'],
                  [
                      ['1', '', '', '2015', 'https://s3.example/alpha-2015.pdf', 'p/a.pdf', '', '0', '5', 'sha1a', '2', 't', '', 'f', 't', '111'],
                      ['2', '', '', '2018', 'https://s3.example/alpha-2018.pdf', 'p/a2.pdf', '', '0', '4', 'sha1c', '0', 'f', '', 'f', 't', '111'],
                      ['3', '', '', '2016', 'https://s3.example/noholdings.pdf', 'p/b.pdf', '', '0', '2', 'sha1b', '0', 'f', '', 'f', 't', '333'],
                      ['4', '', '', '2010', 'https://s3.example/early-2010.pdf', 'p/e1.pdf', '', '0', '3', 'sha1d', '0', 'f', '', 'f', 't', '444'],
                      ['5', '', '', '2020', 'https://s3.example/early-2020.pdf', 'p/e2.pdf', '', '0', '2', 'sha1e', '0', 'f', '', 'f', 't', '444'],
                  ])
    _write_pg_csv(bulk / 'financial-disclosure-investments-2026-06-30.csv.bz2',
                  ['id', 'date_created', 'date_modified', 'page_number', 'description', 'redacted',
                   'income_during_reporting_period_code', 'income_during_reporting_period_type', 'gross_value_code',
                   'gross_value_method', 'transaction_during_reporting_period', 'transaction_date_raw',
                   'transaction_date', 'transaction_value_code', 'transaction_gain_code', 'transaction_partner',
                   'has_inferred_values', 'financial_disclosure_id'],
                  [
                      ['10', '', '', '1', 'Pfizer common stock', 'f', 'A', 'Int/Div', 'K', 'T', '', '', '', '', '', '', 'f', '1'],
                      ['11', '', '', '1', 'Zebra Municipal Bond Fund', 'f', 'A', 'Int/Div', 'J', 'T', '', '', '', '', '', '', 'f', '2'],
                      ['12', '', '', '1', 'Acme Widgets common stock', 't', 'A', 'Int/Div', 'J', 'T', '', '', '', '', '', '', 't', '2'],
                      ['13', '', '', '1', 'Old Fund common stock', 'f', 'A', 'Int/Div', 'K', 'T', '', '', '', '', '', '', 'f', '4'],
                  ])
    for name, cols in [
        ('financial-disclosures-positions-2026-06-30.csv.bz2', ['id', 'date_created', 'date_modified', 'position', 'organization_name', 'redacted', 'financial_disclosure_id']),
        ('financial-disclosures-reimbursements-2026-06-30.csv.bz2', ['id', 'date_created', 'date_modified', 'source', 'date_raw', 'location', 'purpose', 'items_paid_or_provided', 'redacted', 'financial_disclosure_id']),
        ('financial-disclosures-gifts-2026-06-30.csv.bz2', ['id', 'date_created', 'date_modified', 'source', 'description', 'value', 'redacted', 'financial_disclosure_id']),
        ('financial-disclosures-debts-2026-06-30.csv.bz2', ['id', 'date_created', 'date_modified', 'creditor_name', 'description', 'value_code', 'redacted', 'financial_disclosure_id']),
        ('financial-disclosures-agreements-2026-06-30.csv.bz2', ['id', 'date_created', 'date_modified', 'date_raw', 'parties_and_terms', 'redacted', 'financial_disclosure_id']),
        ('financial-disclosures-non-investment-income-2026-06-30.csv.bz2', ['id', 'date_created', 'date_modified', 'date_raw', 'source_type', 'income_amount', 'redacted', 'financial_disclosure_id']),
    ]:
        _write_pg_csv(bulk / name, cols, [])

    expected_backup = dict(builder.EXPECTED_ROWS)
    builder.EXPECTED_ROWS.update({
        'financial_disclosures': 5, 'financial_disclosure_investments': 4,
        'financial_disclosures_positions': 0, 'financial_disclosures_reimbursements': 0,
        'financial_disclosures_gifts': 0, 'financial_disclosures_debts': 0,
        'financial_disclosures_agreements': 0, 'financial_disclosures_non_investment_income': 0,
    })
    sw_manifest_backup, conflicts_backup = builder.SW_MANIFEST, builder.CONFLICTS_LOCAL
    builder.SW_MANIFEST = tmp / 'no_manifest.json'
    builder.CONFLICTS_LOCAL = tmp / 'no_conflicts.json'
    try:
        builder.build(out_dir=out, bulk_dir=bulk, judge_structured_dir=js)
    finally:
        builder.EXPECTED_ROWS.clear()
        builder.EXPECTED_ROWS.update(expected_backup)
        builder.SW_MANIFEST, builder.CONFLICTS_LOCAL = sw_manifest_backup, conflicts_backup
    return out


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='jdisc_'))
        self.fixture = _make_fixture(self.tmp)
        judge_disclosures.DATA = self.fixture
        judge_disclosures._CACHE.clear()

    def tearDown(self):
        judge_disclosures.DATA = REAL
        judge_disclosures._CACHE.clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = judge_disclosures.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertIsNone(judge_disclosures.detail('1'))
        hook = judge_disclosures.for_judge('e-alpha')
        self.assertIsNone(hook)

    def test_untouched_fixture_opens(self):
        self.assertTrue(judge_disclosures.listing({})['available'])

    def test_tampered_db_closes_gate(self):
        with open(self.fixture / 'disclosures.sqlite3', 'ab') as fh:
            fh.write(b'\x00' * 16)
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.fixture / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.fixture / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.fixture / 'validation.json').unlink()
        self.assertClosed()

    def test_status_not_passed_closes_gate(self):
        gate = json.loads((self.fixture / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'failed'
        (self.fixture / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='jdisc_shape_'))
        self.fixture = _make_fixture(self.tmp)
        judge_disclosures.DATA = self.fixture
        judge_disclosures._CACHE.clear()

    def tearDown(self):
        judge_disclosures.DATA = REAL
        judge_disclosures._CACHE.clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_listing_shape_and_total(self):
        out = judge_disclosures.listing({})
        self.assertTrue(out['available'])
        self.assertEqual(out['total'], 5)  # alpha (2) + noholdings (1) + earlyholdings (2) bridge in
        for key in ('filters', 'columns', 'results'):
            self.assertIn(key, out)
        for r in out['results']:
            for key in ('id', 'title', 'subtitle', 'cells', 'badges', 'links'):
                self.assertIn(key, r)
            self.assertNotIn('$', r['subtitle'])

    def test_listing_year_filter(self):
        out = judge_disclosures.listing({'year': '2015'})
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['results'][0]['cells']['year'], '2015')

    def test_listing_has_holdings_filter(self):
        with_h = judge_disclosures.listing({'has_holdings': 'yes'})
        without_h = judge_disclosures.listing({'has_holdings': 'no'})
        self.assertEqual(with_h['total'], 3)
        self.assertEqual(without_h['total'], 2)

    def test_listing_bad_year_is_ignored(self):
        """A non-numeric year must never fail the whole listing (contract: 'bad values never raise')."""
        baseline = judge_disclosures.listing({})
        out = judge_disclosures.listing({'year': 'notayear'})
        self.assertTrue(out['available'])
        self.assertEqual(out['total'], baseline['total'])

    def test_listing_entity_id_filter(self):
        out = judge_disclosures.listing({'entity_id': 'e-alpha'})
        self.assertTrue(out['available'])
        self.assertEqual(out['total'], 2)
        self.assertTrue(all(r['id'] in ('1', '2') for r in out['results']))

    def test_listing_text_search_over_description(self):
        out = judge_disclosures.listing({'q': 'Pfizer'})
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['results'][0]['id'], '1')

    def test_listing_search_handles_punctuation_without_raising(self):
        out = judge_disclosures.listing({'q': 'Johnson & Johnson (Class A)'})
        self.assertTrue(out['available'])  # must not raise FTS5 syntax error

    def test_detail_hides_dollar_figures_and_shows_codes(self):
        d = judge_disclosures.detail('1')
        self.assertIsNotNone(d)
        text = json.dumps(d)
        self.assertNotIn('$', text)
        holdings_section = next(s for s in d['sections'] if 'Investment holdings' in s['heading'])
        self.assertIn('K', holdings_section['rows'][0])

    def test_detail_unknown_id_returns_none(self):
        self.assertIsNone(judge_disclosures.detail('999999'))

    def test_detail_rejects_non_numeric_id(self):
        self.assertIsNone(judge_disclosures.detail('drop table'))

    def test_for_judge_no_filing_state(self):
        hook = judge_disclosures.for_judge('e-nofile')
        self.assertTrue(hook['available'])
        self.assertEqual(hook['state'], 'no_filing_in_snapshot')
        self.assertEqual(hook['years_filed'], [])

    def test_for_judge_filing_with_no_holdings_state(self):
        hook = judge_disclosures.for_judge('e-noholdings')
        self.assertEqual(hook['state'], 'filing_with_no_reportable_holdings')
        self.assertEqual(hook['top_holdings_latest_year'], [])

    def test_for_judge_with_holdings(self):
        hook = judge_disclosures.for_judge('e-alpha')
        self.assertEqual(hook['state'], 'filing_with_reportable_holdings')
        self.assertEqual(hook['years_filed'], [2015, 2018])
        self.assertEqual(hook['latest_year'], 2018)
        self.assertLessEqual(len(hook['top_holdings_latest_year']), judge_disclosures.TOP_HOLDINGS_LIMIT)
        self.assertTrue(all(isinstance(h, dict) and 'description' in h and 'inferred' in h and 'redacted' in h
                             for h in hook['top_holdings_latest_year']))
        self.assertTrue(all('$' not in h['description'] for h in hook['top_holdings_latest_year']))

    def test_for_judge_no_holdings_in_latest_year_but_earlier_year_has_holdings(self):
        """A judge whose most recent filing has no holdings, but an earlier filing does, must not be labelled
        'no reportable holdings' -- state reflects total_holdings across all years, not just the latest filing."""
        hook = judge_disclosures.for_judge('e-earlyholdings')
        self.assertEqual(hook['state'], 'filing_with_reportable_holdings')
        self.assertEqual(hook['latest_year'], 2020)
        self.assertEqual(hook['top_holdings_latest_year'], [])
        self.assertIsNotNone(hook['top_holdings_latest_year_note'])
        self.assertEqual(hook['holdings_count_by_year']['2010'], 1)
        self.assertEqual(hook['holdings_count_by_year']['2020'], 0)
        self.assertEqual(hook['total_holdings'], 1)

    def test_for_judge_not_bridged_distinct_from_no_filing(self):
        """A syntactically valid but non-bridged entity_id must not be reported as a snapshot coverage gap for
        a real judge -- it is a different fact (never bridged at all)."""
        hook = judge_disclosures.for_judge('judge-entity-doesnotexist')
        self.assertTrue(hook['available'])
        self.assertEqual(hook['state'], 'not_bridged_to_courtlistener_person_id')
        no_file_hook = judge_disclosures.for_judge('e-nofile')
        self.assertEqual(no_file_hook['state'], 'no_filing_in_snapshot')
        self.assertNotEqual(hook['state'], no_file_hook['state'])

    def test_for_judge_rejects_bad_entity_id(self):
        self.assertIsNone(judge_disclosures.for_judge('../../etc/passwd'))
        self.assertIsNone(judge_disclosures.for_judge(''))
        self.assertIsNone(judge_disclosures.for_judge(None))


class RealDataSmokeTest(unittest.TestCase):
    def test_real_supplement_if_present(self):
        if not REAL.exists():
            self.skipTest('real supplement not built in this environment')
        judge_disclosures.DATA = REAL
        judge_disclosures._CACHE.clear()
        out = judge_disclosures.listing({})
        self.assertTrue(out['available'])
        self.assertGreater(out['total'], 0)
        first = judge_disclosures.detail(out['results'][0]['id'])
        self.assertIsNotNone(first)


if __name__ == '__main__':
    unittest.main()
