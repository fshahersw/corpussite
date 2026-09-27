import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('county_export_test_subject', HERE / 'county_litigation.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class CountyExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / 'source'
        self.output = Path(self.temp.name) / 'transfer' / 'county.jsonl'
        (self.folder / 'assets').mkdir(parents=True)
        (self.folder / 'text').mkdir()
        self.adapter = subject.adapter_module()

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self):
        text = 'SELF-HELP FORM PACKET\nSUPERIOR COURT OF CALIFORNIA\nCOUNTY OF ORANGE\nCal. Rules of Court, rules 3.51\nFW-001 Request to Waive Court Fees\nFill in court name and street address:\nCase Number:\nCase Name:'
        spec = importlib.util.spec_from_file_location('fixture_county_classifier', subject.ROOT / 'sources/county_litigation_20260919/classify.py')
        classifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(classifier)
        form = classifier.classify('*Packet - Fee Waiver PDF Document', text, 'https://www.occourts.org/system/files/selfhelp/shc-fw-01.pdf', 'application/pdf')
        self.assertEqual(form['resource_type'], 'court_form')
        artifacts = []
        raw = b'%PDF-1.7\nfixture original bytes'
        for name, data, mime, role in [('assets/original.pdf', raw, 'application/pdf', 'original'),
                                       ('text/copy.txt', text.encode('utf8'), 'text/plain; charset=utf-8', 'clean_text')]:
            (self.folder / name).write_bytes(data)
            artifacts.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'mime_type': mime, 'role': role})
        rows = []
        for ident, kind, shape in [('form', 'court_form', form['document_shape']), ('unknown', 'unknown', 'unclassified'),
                                   ('negative', 'source_directory', 'reviewed_non_litigation_reference'),
                                   ('held', 'court_information', 'information_page'), ('news', 'court_information', 'information_page')]:
            rows.append({'id': 'county-litigation:' + ident, 'title': '*Packet - Fee Waiver PDF Document' if ident == 'form' else ident,
                         'source_url': 'https://www.occourts.org/' + ident, 'state': 'CA', 'county': 'Orange County',
                         'county_geoids': ['06059'], 'resource_kind': kind, 'raw_path': artifacts[0]['path'],
                         'sha256': artifacts[0]['sha256'], 'text_path': artifacts[1]['path'], 'text_sha256': artifacts[1]['sha256'],
                         'captured_at': '2026-09-19T10:00:00Z', 'metadata': {'document_shape': shape,
                             'temporal': {'published_at': '2026-03-01', 'effective_at': None},
                             'classification': form if ident == 'form' else {}, 'private_path': 'C:/private/example',
                             'artifacts': artifacts}})
        rows[-1]['title'] = 'Court Graduation'
        rows[-1]['source_url'] = 'https://nebraskajudicial.gov/administration/media-releases/court-graduation'
        held = [{'id': 'county-litigation:held', 'reason': 'Review hold'}, {'id': 'county-litigation:previous-held', 'reason': 'Earlier reviewed hold'}]
        files = []
        for name, values in [('resources.jsonl', rows), ('artifacts.jsonl', artifacts)]:
            data = ''.join(json.dumps(row) + '\n' for row in values).encode('utf8')
            (self.folder / name).write_bytes(data)
            files.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'rows': len(values)})
        (self.folder / 'publication_holdbacks.jsonl').write_bytes(''.join(json.dumps(row) + '\n' for row in held).encode())
        (self.folder / 'validation.json').write_text(json.dumps({'ready': True, 'status': 'passed', 'data_files': files}), encoding='utf8')
        return rows

    def test_actual_form_classification_exports_public_contract_and_full_text(self):
        self.fixture()
        receipt = subject.export(self.output, self.folder, self.adapter)
        rows = [json.loads(line) for line in self.output.read_text(encoding='utf8').splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['category'], 'court_form')
        self.assertEqual(row['filters']['document_shape'], 'form_document')
        self.assertEqual(row['item']['saved_at'], '2026-09-19T10:00:00Z')
        self.assertEqual(row['detail']['published_at'], '2026-03-01')
        self.assertIsNone(row['detail']['effective_date'])
        self.assertNotIn('text', row['detail'])
        self.assertIn('FW-001', row['text'])
        self.assertEqual(len(row['artifacts']), 2)
        self.assertEqual(row['artifacts'][0]['url'], row['item']['original_url'])
        self.assertEqual(row['artifacts'][1]['url'], row['item']['text_url'])
        self.assertNotIn('C:/private', json.dumps(row['detail']))
        self.assertEqual(receipt['excluded_present_rows'], 4)
        self.assertEqual(receipt['excluded_total_with_prior_holdbacks'], 5)

    def test_unknown_reviewed_nonlitigation_and_held_rows_have_explicit_exclusions(self):
        self.fixture()
        subject.export(self.output, self.folder, self.adapter)
        rows = [json.loads(line) for line in self.output.with_suffix('.excluded.jsonl').read_text(encoding='utf8').splitlines()]
        reasons = {r['id']: r['reason'] for r in rows}
        self.assertEqual(reasons['county-litigation:unknown'], 'Uncategorized resource')
        self.assertEqual(reasons['county-litigation:held'], 'Explicit publication holdback')
        self.assertIn('non-litigation', reasons['county-litigation:negative'])

    def test_closed_gate_fails_without_transfer_output(self):
        self.fixture()
        (self.folder / 'validation.json').write_text(json.dumps({'status': 'publishing', 'ready': False}), encoding='utf8')
        with self.assertRaises(ValueError):
            subject.export(self.output, self.folder, self.adapter)
        self.assertFalse(self.output.exists())

    def test_changed_original_fails_without_transfer_output(self):
        self.fixture()
        (self.folder / 'assets/original.pdf').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            subject.export(self.output, self.folder, self.adapter)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
