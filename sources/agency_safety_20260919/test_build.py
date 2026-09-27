"""Tests for build.py: streaming parser, normalisation helpers, a fixture build, and (when present) the real outputs."""
import hashlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('agency_safety_build', HERE / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def openfda_zip(endpoint, rows, last_updated='2026-09-16'):
    name = endpoint.replace('/', '-') + '-0001-of-0001.json'
    body = json.dumps({'meta': {'disclaimer': 'Do not rely on openFDA', 'terms': 'https://open.fda.gov/terms/', 'license': 'https://open.fda.gov/license/',
                                'last_updated': last_updated, 'results': {'skip': 0, 'limit': len(rows), 'total': len(rows)}}, 'results': rows}, indent=1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
    return name + '.zip', buffer.getvalue()


def enforcement(number, firm, **extra):
    row = {'status': 'Ongoing', 'city': 'Newark', 'state': 'NJ', 'country': 'United States', 'classification': 'Class II', 'openfda': {},
           'product_type': 'Drugs', 'event_id': '1', 'recalling_firm': firm, 'recall_number': number, 'product_description': 'Tablets',
           'reason_for_recall': 'Impurity', 'recall_initiation_date': '20240105', 'center_classification_date': '20240210', 'report_date': '20240221',
           'code_info': 'Lot 1'}
    row.update(extra)
    return row


def make_fixture(folder, tamper=None):
    import openpyxl
    raw = folder / 'raw'
    raw.mkdir()
    receipts, index = [], {'meta': {'disclaimer': 'Do not rely on openFDA', 'last_updated': '2026-09-18'}, 'results': {}}

    def save(seed, url, name, payload):
        if tamper == seed:
            payload = payload + b'!'
        (raw / name).write_bytes(payload)
        receipts.append({'seed_id': seed, 'packet': 1, 'url': url, 'status': 200, 'final_url': url, 'headers': {}, 'requested_at': '2026-09-19T05:35:00Z',
                         'completed_at': '2026-09-19T05:35:01Z', 'bytes': len(payload) - (1 if tamper == seed else 0),
                         'sha256': hashlib.sha256(payload[:-1] if tamper == seed else payload).hexdigest(), 'raw_path': 'raw/' + name, 'outcome': 'saved'})

    datasets = {
        'openfda-drug-enforcement': ('drug/enforcement', [enforcement('D-0001-2024', 'Acme Corp.', termination_date='20240901'),
                                                          enforcement('D-0001-2024', 'Acme Corp.'), enforcement('D-0002-2024', 'ACME CORP', recalling_firm='')]),
        'openfda-device-classification': ('device/classification', [
            {'product_code': 'DXY', 'device_name': 'Pacemaker', 'device_class': '3', 'regulation_number': '870.3610', 'openfda': {}},
            {'product_code': 'NIO', 'device_name': 'Stent', 'device_class': '3', 'regulation_number': '', 'openfda': {}},
            {'product_code': 'QQQ', 'device_name': 'Odd', 'device_class': 'U', 'regulation_number': 'n/a', 'openfda': {}}]),
        'openfda-drug-drugsfda': ('drug/drugsfda', [{'application_number': 'NDA000001', 'sponsor_name': 'Bread & Co', 'submissions': [
            {'submission_type': 'ORIG', 'submission_number': '1', 'submission_status': 'AP', 'submission_status_date': '19990101'}],
            'products': [{'product_number': '001', 'brand_name': 'X', 'active_ingredients': [{'name': 'Y', 'strength': '1MG'}]}]}]),
        'openfda-drug-orangebook': ('drug/orangebook', [{'approval_date': 'Approved Prior to Jan 1, 1982', 'product_number': '001', 'products': [
            {'brand_name': 'OLD', 'application_type': 'N', 'application_number': '000002', 'application_full_name': 'OLD CO'}]}]),
    }
    for seed, (endpoint, rows) in datasets.items():
        name, payload = openfda_zip(endpoint, rows)
        url = 'https://download.open.fda.gov/' + endpoint + '/' + name
        save(seed, url, name, payload)
        a, b = endpoint.split('/')
        index['results'].setdefault(a, {})[b] = {'export_date': '2026-09-16', 'total_records': len(rows), 'partitions': [{'file': url, 'records': len(rows)}]}
    save('openfda-download-index', 'https://api.fda.gov/download.json', 'download.json', json.dumps(index).encode())
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(('Posted Date', 'Letter Issue Date', 'Company Name', 'Issuing Office', 'Subject', 'Response Letter', 'Closeout Letter'))
    sheet.append(('06/11/2024', '05/16/2024', 'Acme Corp', 'CDER', 'CGMP', None, None))
    buffer = io.BytesIO()
    book.save(buffer)
    save('fda-warning-letters-xlsx', 'https://www.fda.gov/wl', 'fda-warning-letters-xlsx.xlsx', buffer.getvalue())
    (folder / 'receipts.jsonl').write_text('\n'.join(json.dumps(r) for r in receipts) + '\n', encoding='utf-8')
    csv_path = folder / 'Recalls_input.csv'
    csv_path.write_bytes(('"Disclaimer: CPSC does not guarantee"\r\nTitle,Date,Summary,Repair Number\r\nIKEA kit,7/22/2015,Repair,15190\r\n'
                          'Title,Date,Summary,Recall Number,Recall URL\r\nOvens,6/8/1973,Ovens ’hazard’,73003,https://cpsc.gov/x\r\n').encode('cp1252'))
    return csv_path


class HelperTests(unittest.TestCase):
    def test_norm_firm_is_exact_string_normalisation_only(self):
        self.assertEqual(build.norm_firm('Acme Corp.'), 'ACME CORP')
        self.assertEqual(build.norm_firm('Bread & Co'), 'BREAD AND CO')
        self.assertEqual(build.norm_firm("O'Neil's  Labs, Inc"), 'ONEILS LABS INC')
        self.assertNotEqual(build.norm_firm('ACME CORP'), build.norm_firm('ACME CORPORATION'))
        self.assertEqual(build.firm_id('ACME CORP'), 'firm:acme-corp')
        self.assertEqual(build.norm_firm(None), '')

    def test_iso_date_accepts_publisher_formats_and_rejects_the_rest(self):
        self.assertEqual(build.iso_date('20240105'), '2024-01-05')
        self.assertEqual(build.iso_date('2015-09-24'), '2015-09-24')
        self.assertEqual(build.iso_date('6/8/1973'), '1973-06-08')
        self.assertIsNone(build.iso_date('Approved Prior to Jan 1, 1982'))
        self.assertIsNone(build.iso_date('20241301'))
        self.assertIsNone(build.iso_date(''))
        self.assertIsNone(build.iso_date(None))

    def test_iter_openfda_streams_rows_across_buffer_boundaries(self):
        rows = [{'recall_number': 'D-%05d' % i, 'reason_for_recall': 'x' * 700, 'nested': {'a': [1, 2, {'b': ']'}]}} for i in range(4000)]
        name, payload = openfda_zip('drug/enforcement', rows)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(payload)
            items = list(build.iter_openfda(path))
        self.assertEqual(items[0][0], 'meta')
        self.assertEqual(items[0][1]['results']['total'], 4000)
        self.assertEqual(len(items) - 1, 4000)
        self.assertEqual(items[-1][1]['recall_number'], 'D-03999')
        self.assertEqual(items[1][1]['nested'], {'a': [1, 2, {'b': ']'}]})

    def test_read_cpsc_keeps_sections_and_decodes_cp1252(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            csv_path = make_fixture(folder)
            rows = list(build.read_cpsc(csv_path))
        self.assertEqual([r['_section'] for r in rows], ['repair', 'recall'])
        self.assertEqual(rows[1]['Summary'], 'Ovens ’hazard’')
        self.assertEqual(build.read_cpsc.last['encoding'], 'cp1252')
        self.assertTrue(build.read_cpsc.last['preamble'][0].startswith('Disclaimer'))


class FixtureBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name) / 'agency_safety'
        cls.folder.mkdir()
        cls.gate = build.build(cls.folder, cpsc_csv=make_fixture(cls.folder))
        cls.db = sqlite3.connect('file:' + (cls.folder / 'agency_safety.sqlite3').as_posix() + '?mode=ro', uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def test_envelope_and_hashes(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        self.assertEqual((gate['status'], gate['ready'], gate['schema_version']), ('passed', True, '1'))
        for entry in gate['data_files']:
            self.assertEqual(hashlib.sha256((self.folder / entry['path']).read_bytes()).hexdigest(), entry['sha256'], entry['path'])
        self.assertEqual({e['path'] for e in gate['data_files']}, {'agency_safety.sqlite3', 'firm_names.jsonl', 'edges.jsonl', 'unresolved.jsonl', 'files.json'})
        self.assertTrue(all(c['passed'] for c in gate['checks']))
        self.assertIn('no_faers_or_maude_event_files', [c['name'] for c in gate['checks']])
        self.assertIn('Do not rely on openFDA', gate['qualification'])
        self.assertTrue(any(i['path'].endswith('receipts.jsonl') for i in gate['inputs']))

    def test_counts_duplicates_and_skips(self):
        counts = self.gate['counts']
        self.assertEqual(counts['openfda_drug_enforcement'], 3)
        self.assertEqual(counts['duplicate_native_ids_suffixed'], {'openfda_drug_enforcement': 1})
        self.assertEqual(counts['date_values_not_calendar_dates_kept_raw'], {'openfda_orangebook.approval_date': 1})
        self.assertEqual(counts['records_total'], self.db.execute('select count(*) from record_index').fetchone()[0])
        skipped = {s['dataset'] for s in counts['datasets_skipped']}
        self.assertEqual(skipped, {'openfda_device_enforcement', 'openfda_food_enforcement', 'openfda_device_pma', 'openfda_crl', 'openfda_drug_shortages',
                                   'fda_press_recalls'})
        self.assertEqual(self.db.execute("select id from record_index where dataset='openfda_drug_enforcement' order by rid").fetchall(),
                         [('openfda:drug/enforcement:D-0001-2024',), ('openfda:drug/enforcement:D-0001-2024~2',), ('openfda:drug/enforcement:D-0002-2024',)])
        self.assertEqual(self.db.execute("select approval_date, approval_date_raw from orangebook_products").fetchone(), (None, 'Approved Prior to Jan 1, 1982'))
        self.assertEqual(self.db.execute("select endpoint from datasets where dataset='openfda_drugsfda'").fetchone(), ('drug/drugsfda',))

    def test_enforcement_dates_are_separate_columns_and_rows(self):
        row = self.db.execute("select recall_initiation_date, center_classification_date, report_date, termination_date from enforcement where rid=1").fetchone()
        self.assertEqual(row, ('2024-01-05', '2024-02-10', '2024-02-21', '2024-09-01'))
        kinds = dict(self.db.execute("select date_type, count(*) from record_dates where rid in (1,2,3) group by 1"))
        self.assertEqual(kinds, {'recall_initiation_date': 3, 'center_classification_date': 3, 'report_date': 3, 'termination_date': 1})

    def test_edges_have_evidence_and_unresolved_have_reasons(self):
        edges = [json.loads(l) for l in (self.folder / 'edges.jsonl').read_text(encoding='utf-8').splitlines()]
        unresolved = [json.loads(l) for l in (self.folder / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(sorted(e['relation'] for e in edges), ['application_sponsor', 'classified_under_regulation', 'recalling_firm', 'recalling_firm'])
        firm_edge = [e for e in edges if e['relation'] == 'recalling_firm'][0]
        self.assertEqual(firm_edge['to'], {'type': 'firm', 'id': 'firm:acme-corp'})
        self.assertEqual(firm_edge['evidence']['field'], 'recalling_firm')
        cfr = [e for e in edges if e['relation'] == 'classified_under_regulation'][0]
        self.assertEqual(cfr['to'], {'type': 'cfr', 'id': 'cfr:21:870.3610'})
        reasons = {u['from']['id']: u['reason'] for u in unresolved}
        self.assertIn('empty', reasons['openfda:device/classification:NIO'])
        self.assertIn('not in <part>.<section> form', reasons['openfda:device/classification:QQQ'])
        self.assertIn('recalling_firm is empty', reasons['openfda:drug/enforcement:D-0002-2024'])

    def test_firm_names_are_grouped_by_exact_normalised_string(self):
        names = {json.loads(l)['firm_norm']: json.loads(l) for l in (self.folder / 'firm_names.jsonl').read_text(encoding='utf-8').splitlines()}
        self.assertEqual(names['ACME CORP']['record_count'], 3)
        self.assertEqual({n['name'] for n in names['ACME CORP']['names']}, {'Acme Corp.', 'Acme Corp'})
        self.assertEqual(names['ACME CORP']['datasets'], {'openfda_drug_enforcement': 2, 'fda_warning_letters': 1})
        self.assertEqual(names['BREAD AND CO']['merge_rule'], 'exact normalised string equality only')

    def test_cpsc_dataset_is_labelled_unknown_provenance(self):
        row = self.db.execute("select label, captured_at, captured_at_basis, source_as_of from datasets where dataset='cpsc_recalls_local'").fetchone()
        self.assertIn('unknown provenance', row[0])
        self.assertIsNone(row[1]); self.assertIn('unknown', row[2]); self.assertIsNone(row[3])
        files = json.loads((self.folder / 'files.json').read_text(encoding='utf-8'))['files']
        local = [f for f in files if f['file_id'] == 'local-cpsc-recalls-csv'][0]
        self.assertEqual((local['publisher_url'], local['captured_at'], local['path']), (None, None, 'raw/local/Recalls.csv'))


class TamperedOriginalTests(unittest.TestCase):
    def test_original_that_does_not_match_its_receipt_is_skipped_and_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'agency_safety'
            folder.mkdir()
            gate = build.build(folder, cpsc_csv=make_fixture(folder, tamper='openfda-drug-enforcement'))
            self.assertEqual((gate['status'], gate['ready']), ('failed', False))
            failed = [c['name'] for c in gate['checks'] if not c['passed']]
            self.assertEqual(failed, ['original_matches_receipt:openfda-drug-enforcement'])
            self.assertIn({'dataset': 'openfda_drug_enforcement', 'reason': 'no saved, hash-matching original'}, gate['counts']['datasets_skipped'])


@unittest.skipUnless((HERE / 'agency_safety.sqlite3').is_file(), 'real build not present')
class RealOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        cls.db = sqlite3.connect('file:' + (HERE / 'agency_safety.sqlite3').as_posix() + '?mode=ro', uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_gate_hashes_match_files(self):
        self.assertEqual((self.gate['status'], self.gate['ready']), ('passed', True))
        for entry in self.gate['data_files']:
            digest = hashlib.sha256()
            with (HERE / entry['path']).open('rb') as stream:
                for block in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(block)
            self.assertEqual(digest.hexdigest(), entry['sha256'], entry['path'])

    def test_every_openfda_dataset_matches_download_json_totals(self):
        index = json.loads((HERE / 'raw/download.json').read_bytes())
        for dataset, endpoint, rows in self.db.execute('select dataset, endpoint, rows from datasets where endpoint is not null'):
            a, b = endpoint.split('/')
            self.assertEqual(rows, index['results'][a][b]['total_records'], dataset)
        self.assertEqual(self.db.execute('select count(*) from datasets').fetchone()[0], 12)
        self.assertEqual(self.gate['counts']['records_total'], self.db.execute('select count(*) from record_index').fetchone()[0])

    def test_no_event_files_and_receipts_are_bound(self):
        receipts = [json.loads(l) for l in (HERE / 'receipts.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertFalse(any('/event/' in r['url'] for r in receipts))
        self.assertEqual(len([r for r in receipts if r.get('outcome') == 'saved']), 11)
        self.assertTrue(any(i['path'].endswith('receipts.jsonl') for i in self.gate['inputs']))

    def test_real_enforcement_record_keeps_four_separate_dates(self):
        row = self.db.execute("select recall_number, recall_initiation_date, center_classification_date, report_date, termination_date from enforcement "
                              "where termination_date is not null and recall_initiation_date is not null limit 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(len({row[1], row[2], row[3], row[4]} - {None}), len([d for d in row[1:] if d]) if len(set(row[1:])) == 4 else len(set(row[1:])))
        kinds = dict(self.db.execute("select date_type, count(*) from record_dates where date_type in ('recall_initiation_date','center_classification_date',"
                                     "'report_date','termination_date') group by 1"))
        self.assertEqual(set(kinds), {'recall_initiation_date', 'center_classification_date', 'report_date', 'termination_date'})

    def test_warning_letter_dates_and_cap(self):
        self.assertEqual(self.gate['counts']['fda_warning_letters'], 1000)
        row = self.db.execute("select qualification from datasets where dataset='fda_warning_letters'").fetchone()[0]
        self.assertIn('caps the export', row)
        self.assertGreater(self.db.execute('select count(*) from warning_letters where posted_date != letter_issue_date').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
