"""Adapter tests for agency_safety. The fixture is produced by the real build.py from tiny inputs."""
import hashlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

import agency_safety as safety

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'sources/agency_safety_20260919/build.py'


def load_build():
    spec = importlib.util.spec_from_file_location('agency_safety_build', BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enforcement(number, firm, classification='Class II', status='Ongoing', init='20240105', cls='20240210',
                report='20240221', term='', product='Widget', reason='Defect'):
    row = {'status': status, 'city': 'Newark', 'state': 'NJ', 'country': 'United States', 'classification': classification,
           'openfda': {}, 'product_type': 'Devices', 'event_id': '9' + number[-4:], 'recalling_firm': firm,
           'recall_number': number, 'product_description': product, 'reason_for_recall': reason,
           'recall_initiation_date': init, 'center_classification_date': cls, 'report_date': report,
           'code_info': 'Lot 1', 'more_code_info': ''}
    if term:
        row['termination_date'] = term
    return row


FIXTURE = {
    'openfda-drug-enforcement': ('drug/enforcement', [
        enforcement('D-0001-2024', 'Acme Corp.', 'Class I', 'Terminated', term='20240901', product='Valsartan tablets', reason='NDMA impurity'),
        enforcement('D-0002-2024', 'ACME CORP', product='Losartan tablets')]),
    'openfda-device-enforcement': ('device/enforcement', [
        enforcement('Z-0003-2025', 'Acme Corporation', init='20250301', cls='20250401', report='20250409', product='Hernia mesh')]),
    'openfda-food-enforcement': ('food/enforcement', [enforcement('F-0004-2023', 'Bread & Co', init='20230101', cls='20230115', report='20230125')]),
    'openfda-drug-drugsfda': ('drug/drugsfda', [{
        'application_number': 'NDA021436', 'sponsor_name': 'ACME CORP',
        'submissions': [{'submission_type': 'ORIG', 'submission_number': '1', 'submission_status': 'AP', 'submission_status_date': '20021115'},
                        {'submission_type': 'SUPPL', 'submission_number': '2', 'submission_status': 'AP', 'submission_status_date': '20100301'}],
        'products': [{'product_number': '001', 'brand_name': 'ABILIFY', 'active_ingredients': [{'name': 'ARIPIPRAZOLE', 'strength': '5MG'}],
                      'dosage_form': 'TABLET', 'route': 'ORAL', 'marketing_status': 'Prescription'}]}]),
    'openfda-device-pma': ('device/pma', [{'pma_number': 'P090006', 'supplement_number': 'S016', 'applicant': 'Acme Corp', 'generic_name': 'STENT',
                                           'trade_name': 'COMPLETE SE', 'product_code': 'NIO', 'date_received': '2015-09-24',
                                           'decision_date': '2015-10-20', 'decision_code': 'OK30', 'state': 'CA',
                                           'openfda': {'device_class': '3', 'regulation_number': ''}}]),
    'openfda-device-classification': ('device/classification', [
        {'product_code': 'DXY', 'device_name': 'Implantable pacemaker pulse generator', 'device_class': '3', 'regulation_number': '870.3610',
         'medical_specialty_description': 'Cardiovascular', 'openfda': {}},
        {'product_code': 'DTB', 'device_name': 'Pacemaker lead', 'device_class': '3', 'regulation_number': '870.3680', 'openfda': {}},
        {'product_code': 'NIO', 'device_name': 'Stent, iliac', 'device_class': '3', 'regulation_number': '', 'openfda': {}}]),
    'openfda-transparency-crl': ('transparency/crl', [{'letter_date': '01/17/2025', 'file_name': 'CRL_NDA219112_20250117.pdf', 'approval_status': 'Unapproved',
                                                       'company_name': 'Hospira, Inc.', 'text': 'COMPLETE RESPONSE', 'application_number': ['NDA 219112'],
                                                       'letter_type': 'COMPLETE RESPONSE'}]),
    'openfda-drug-shortages': ('drug/shortages', [{'update_type': 'Revised', 'initial_posting_date': '02/20/2018', 'package_ndc': '0409-9046-01',
                                                   'generic_name': 'Bupivacaine', 'company_name': 'Hospira, Inc.', 'update_date': '09/09/2026', 'status': 'Current'}]),
    'openfda-drug-orangebook': ('drug/orangebook', [{'approval_date': '19930630', 'product_number': '002', 'products': [
        {'brand_name': 'METHAZOLAMIDE', 'application_name': 'ANI PHARMS', 'application_type': 'A', 'application_number': '040001',
         'application_full_name': 'ANI PHARMACEUTICALS INC', 'marketing_status': 'HUMAN PRESCRIPTION DRUG'}]}]),
}


def make_fixture(folder):
    import openpyxl
    raw = folder / 'raw'
    raw.mkdir()
    receipts, index = [], {'meta': {'last_updated': '2026-09-18'}, 'results': {}}

    def receipt(seed, url, name, payload):
        (raw / name).write_bytes(payload)
        receipts.append({'seed_id': seed, 'packet': 1, 'url': url, 'status': 200, 'final_url': url, 'headers': {},
                         'requested_at': '2026-09-19T05:35:00Z', 'completed_at': '2026-09-19T05:35:01Z', 'bytes': len(payload),
                         'sha256': hashlib.sha256(payload).hexdigest(), 'raw_path': 'raw/' + name, 'outcome': 'saved'})

    for seed, (endpoint, rows) in FIXTURE.items():
        name = endpoint.replace('/', '-') + '-0001-of-0001.json'
        body = json.dumps({'meta': {'disclaimer': 'Do not rely on openFDA', 'license': 'https://open.fda.gov/license/',
                                    'last_updated': '2026-09-16', 'results': {'total': len(rows)}}, 'results': rows}, indent=1)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, body)
        url = 'https://download.open.fda.gov/' + endpoint + '/' + name + '.zip'
        receipt(seed, url, name + '.zip', buffer.getvalue())
        a, b = endpoint.split('/')
        index['results'].setdefault(a, {})[b] = {'export_date': '2026-09-16', 'total_records': len(rows),
                                                 'partitions': [{'file': url, 'records': len(rows), 'size_mb': '0.01'}]}
    receipt('openfda-download-index', 'https://api.fda.gov/download.json', 'download.json', json.dumps(index).encode())
    for seed, header, rows in (
        ('fda-warning-letters-xlsx', ('Posted Date', 'Letter Issue Date', 'Company Name', 'Issuing Office', 'Subject', 'Response Letter', 'Closeout Letter'),
         [('06/11/2024', '05/16/2024', 'Acme Corp', 'Center for Drug Evaluation and Research', 'CGMP/Adulterated', None, None)]),
        ('fda-press-recalls-xlsx', ('Date', 'Brand-Names', 'Product-Description', 'Product-Types', 'Recall-Reason-Description', 'Company-Name', 'Terminated Recall'),
         [('03/20/2023', 'Kaytee', 'Wild Bird Food', 'Animal & Veterinary', 'Aflatoxin', 'Kaytee Products Inc.', 'Terminated')])):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        buffer = io.BytesIO()
        book.save(buffer)
        receipt(seed, 'https://www.fda.gov/' + seed, seed + '.xlsx', buffer.getvalue())
    (folder / 'receipts.jsonl').write_text('\n'.join(json.dumps(r) for r in receipts) + '\n', encoding='utf-8')
    csv_path = folder / 'Recalls_input.csv'
    csv_path.write_bytes(('"Disclaimer: CPSC does not guarantee"\r\nTitle,Date,Summary,Repair Number\r\nIKEA repair kit,7/22/2015,Repair program,15190\r\n'
                          'Title,Date,Summary,Recall Number,Recall URL\r\nTappan Ovens Warning,6/8/1973,Ovens ’hazard’,73003,https://cpsc.gov/Recalls/1973/x\r\n').encode('cp1252'))
    return csv_path


class AgencySafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name) / 'agency_safety'
        cls.folder.mkdir()
        csv_path = make_fixture(cls.folder)
        cls.report = load_build().build(cls.folder, cpsc_csv=csv_path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_datasets_are_listed_without_filesystem_paths(self):
        rows = safety.datasets(self.folder)
        self.assertEqual(len(rows), 12)
        by_id = {r['dataset']: r for r in rows}
        self.assertEqual(by_id['openfda_drug_enforcement']['rows'], 2)
        self.assertIn('unknown provenance', by_id['cpsc_recalls_local']['label'])
        self.assertIsNone(by_id['cpsc_recalls_local']['captured_at'])
        self.assertIn('Do not rely on openFDA', by_id['openfda_device_pma']['qualification'])
        self.assertTrue(by_id['openfda_device_pma']['original_files'][0]['url'].startswith('/agency-safety/files/'))
        dumped = json.dumps([rows, safety.search(folder=self.folder), safety.firm('Acme Corp', folder=self.folder)])
        self.assertNotIn(str(self.folder).replace('\\', '\\\\'), dumped)
        self.assertNotIn(self.folder.as_posix(), dumped)

    def test_search_filters_and_pagination(self):
        find = lambda **kw: safety.search(folder=self.folder, **kw)
        self.assertEqual(find(dataset='openfda_drug_enforcement')['total'], 2)
        self.assertEqual(find(dataset='enforcement')['total'], 4)
        self.assertEqual([r['native_id'] for r in find(q='valsartan')['results']], ['D-0001-2024'])
        self.assertEqual(find(q='"unterminated AND (')['total'], 0)
        self.assertEqual(find(dataset='enforcement', classification='class i')['total'], 1)
        self.assertEqual(find(dataset='enforcement', status='Terminated')['total'], 1)
        self.assertEqual({r['dataset'] for r in find(product_code='NIO')['results']}, {'openfda_device_pma', 'openfda_device_classification'})
        self.assertGreaterEqual(find(firm='acme')['total'], 5)
        page = find(dataset='enforcement', limit=1, page=2)
        self.assertEqual((page['total'], page['page'], page['limit'], page['pages'], len(page['results'])), (4, 2, 1, 4, 1))
        self.assertEqual(find(limit=100000)['limit'], 100)

    def test_each_enforcement_date_is_filtered_separately(self):
        find = lambda **kw: safety.search(folder=self.folder, dataset='enforcement', **kw)
        self.assertEqual(find(date_type='termination_date', dfrom='2024-01-01', dto='2024-12-31')['total'], 1)
        self.assertEqual(find(date_type='recall_initiation_date', dfrom='2025-01-01')['total'], 1)
        self.assertEqual(find(date_type='report_date', dfrom='2024-02-21', dto='2024-02-21')['total'], 2)
        self.assertEqual(find(date_type='center_classification_date', dto='2023-12-31')['total'], 1)
        self.assertEqual(find(date_type='not_a_date_type', dfrom='2020-01-01')['total'], 0)
        letters = safety.search(folder=self.folder, dataset='fda_warning_letters', date_type='letter_issue_date', dfrom='2024-05-01', dto='2024-05-31')
        self.assertEqual(letters['total'], 1)
        self.assertEqual(letters['results'][0]['dates'], {'posted_date': '2024-06-11', 'letter_issue_date': '2024-05-16'})
        self.assertEqual(safety.search(folder=self.folder, dataset='fda_warning_letters', date_type='posted_date', dto='2024-05-31')['total'], 0)

    def test_record_keeps_verbatim_publisher_fields_and_temporal_block(self):
        row = safety.record('openfda_drug_enforcement', 'D-0001-2024', folder=self.folder)
        self.assertEqual(row['id'], 'openfda:drug/enforcement:D-0001-2024')
        self.assertEqual(row['publisher_record']['recall_initiation_date'], '20240105')
        self.assertEqual(row['dates'], {'recall_initiation_date': '2024-01-05', 'center_classification_date': '2024-02-10',
                                        'report_date': '2024-02-21', 'termination_date': '2024-09-01'})
        temporal = row['temporal']
        for key in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to'):
            self.assertIn(key, temporal)
            self.assertIn(key + '_basis', temporal)
        self.assertEqual((temporal['source_as_of'], temporal['published_at'], temporal['effective_from']), ('2026-09-16', '2024-02-21', None))
        self.assertEqual(safety.record('openfda_drug_enforcement', 'openfda:drug/enforcement:D-0001-2024', folder=self.folder)['id'], row['id'])
        application = safety.record('openfda_drugsfda', 'NDA021436', folder=self.folder)
        self.assertEqual(application['fields']['original_approval_date'], '2002-11-15')
        self.assertEqual(len(application['submissions']), 2)
        self.assertEqual(application['products'][0]['brand_name'], 'ABILIFY')
        cpsc = safety.record('cpsc_recalls_local', '73003', folder=self.folder)
        self.assertEqual(cpsc['publisher_record']['Summary'], 'Ovens ’hazard’')
        self.assertIsNone(cpsc['temporal']['captured_at'])
        self.assertIsNone(safety.record('openfda_drug_enforcement', 'missing', folder=self.folder))
        self.assertIsNone(safety.record('no_such_dataset', 'D-0001-2024', folder=self.folder))

    def test_firm_is_exact_normalised_equality_only(self):
        firm = safety.firm('acme corp', folder=self.folder)
        self.assertEqual(firm['firm_norm'], 'ACME CORP')
        self.assertEqual({n['name'] for n in firm['names']}, {'Acme Corp.', 'ACME CORP', 'Acme Corp'})
        self.assertEqual(firm['datasets']['openfda_drug_enforcement'], 2)
        self.assertNotIn('openfda_device_enforcement', firm['datasets'])   # "Acme Corporation" is a different string
        self.assertEqual(safety.firm('Bread and Co', folder=self.folder)['total'], 1)
        self.assertEqual(safety.firm('Nobody Ltd', folder=self.folder)['total'], 0)

    def test_for_cfr_section_and_part(self):
        section = safety.for_cfr('21 CFR 870.3610', folder=self.folder)
        self.assertEqual(section['citation'], 'cfr:21:870.3610')
        self.assertEqual([r['native_id'] for r in section['classifications']], ['DXY'])
        self.assertEqual(safety.for_cfr('cfr:21:870', folder=self.folder)['total'], 2)
        self.assertEqual(safety.for_cfr('870.3680', folder=self.folder)['total'], 1)
        self.assertEqual(safety.for_cfr('cfr:40:63', folder=self.folder)['total'], 0)
        self.assertEqual(safety.for_cfr('../etc', folder=self.folder)['total'], 0)

    def test_edges_and_unresolved_files(self):
        edges = [json.loads(line) for line in (self.folder / 'edges.jsonl').read_text(encoding='utf-8').splitlines()]
        relations = {e['relation'] for e in edges}
        self.assertEqual(relations, {'recalling_firm', 'classified_under_regulation', 'application_sponsor'})
        self.assertIn({'type': 'cfr', 'id': 'cfr:21:870.3610'}, [e['to'] for e in edges])
        self.assertTrue(all(set(e) == {'from', 'to', 'relation', 'basis', 'evidence'} for e in edges))
        unresolved = [json.loads(line) for line in (self.folder / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertIn('openfda:device/classification:NIO', [u['from']['id'] for u in unresolved])

    def test_original_bytes_are_hash_verified(self):
        files = safety.datasets(self.folder)[0]['original_files']
        file_id = files[0]['file_id']
        payload, mime, name = safety.original(file_id, folder=self.folder)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), files[0]['sha256'])
        self.assertTrue(name.endswith('.zip'))
        self.assertIsNone(safety.original('../validation.json', folder=self.folder))
        self.assertIsNone(safety.original('missing', folder=self.folder))

    def test_envelope_is_uniform(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        for key in ('schema_version', 'status', 'ready', 'validated_at', 'data_files', 'counts', 'checks', 'qualification', 'license_ref', 'inputs'):
            self.assertIn(key, gate)
        self.assertEqual((gate['status'], gate['ready']), ('passed', True))
        self.assertIn('Do not rely on openFDA', gate['qualification'])


class FailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / 'agency_safety'
        self.folder.mkdir()
        load_build().build(self.folder, cpsc_csv=make_fixture(self.folder))

    def tearDown(self):
        self.tmp.cleanup()

    def assert_closed(self):
        self.assertEqual(safety.datasets(self.folder), [])
        result = safety.search(folder=self.folder)
        self.assertEqual((result['available'], result['total'], result['results']), (False, 0, []))
        self.assertIsNone(safety.record('openfda_drug_enforcement', 'D-0001-2024', folder=self.folder))
        self.assertEqual(safety.firm('Acme Corp', folder=self.folder)['total'], 0)
        self.assertEqual(safety.for_cfr('cfr:21:870', folder=self.folder)['total'], 0)

    def test_open_then_tampered_database_fails_closed(self):
        self.assertEqual(len(safety.datasets(self.folder)), 12)
        connection = sqlite3.connect(self.folder / 'agency_safety.sqlite3')
        connection.execute("update record_index set firm = 'Someone Else'")
        connection.commit(); connection.close()
        self.assert_closed()

    def test_tampered_edges_or_status_fail_closed(self):
        with (self.folder / 'edges.jsonl').open('a', encoding='utf-8') as out:
            out.write('{}\n')
        self.assert_closed()

    def test_not_ready_fails_closed(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assert_closed()

    def test_tampered_original_is_not_served_but_records_remain(self):
        file_id = safety.datasets(self.folder)[0]['original_files'][0]['file_id']
        self.assertIsNotNone(safety.original(file_id, folder=self.folder))
        registry = json.loads((self.folder / 'files.json').read_text(encoding='utf-8'))
        target = self.folder / [f for f in registry['files'] if f['file_id'] == file_id][0]['path']
        target.write_bytes(target.read_bytes() + b'x')
        self.assertIsNone(safety.original(file_id, folder=self.folder))
        self.assertEqual(len(safety.datasets(self.folder)), 12)


if __name__ == '__main__':
    unittest.main()
