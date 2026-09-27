"""Builder tests: firm-line rule, privacy scan, validation envelope hashes. Offline."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402


class FirmRuleTests(unittest.TestCase):
    def test_first_line_is_firm(self):
        firm, city, state = build.parse_contact('Example & Sample LLP\n100 Main Street\nSuite 5\nSpringfield, IL 62701\n555-010-0000\nEmail: x@example.invalid\n')
        self.assertEqual((firm, city, state), ('Example & Sample LLP', 'Springfield', 'IL'))

    def test_admission_note_is_skipped(self):
        firm, _, state = build.parse_contact('COUNSEL NOT ADMITTED TO USDC-NJ BAR\nSAMPLE & CO\n1 A AVE SW\nWASHINGTON, DC 20024\n')
        self.assertEqual((firm, state), ('SAMPLE & CO', 'DC'))

    def test_address_first_line_gives_no_firm(self):
        self.assertIsNone(build.parse_contact('123 Main Street\nSpringfield, IL 62701\n')[0])
        self.assertIsNone(build.parse_contact('P.O. Box 12\nSpringfield, IL 62701\n')[0])
        self.assertIsNone(build.parse_contact('')[0])

    def test_firm_key_is_exact_not_fuzzy(self):
        self.assertEqual(build.firm_key('  Bowersox  Law Firm P.C. '), 'bowersox law firm p.c.')
        self.assertNotEqual(build.firm_key('Bowersox Law Firm P.C.'), build.firm_key('Bowersox Law Firm, P.C.'))

    def test_private_text_detector(self):
        self.assertIsNotNone(build.has_private('Firm LLP, 100 Main Street'))
        self.assertIsNotNone(build.has_private('call 555-010-0000'))
        self.assertIsNotNone(build.has_private('a@b.example'))
        self.assertIsNone(build.has_private('Norton Rose Fulbright US LLP, RBC Plaza'))

    def test_admission_note_segments_are_stripped(self):
        strip = build.strip_admission_note
        self.assertEqual(strip('COUNSEL NOT ADMITTED TO USDC NJ BAR, Sample Firm LLC'), 'Sample Firm LLC')
        self.assertEqual(strip('Counsel Not Admitted to Udsc-Nj Bar, Sample, Firm, Example, LLC'), 'Sample, Firm, Example, LLC')
        self.assertEqual(strip('Attorney Not Admitted to Usdc-Nj Bar, Sample Law Group Plc'), 'Sample Law Group Plc')
        self.assertEqual(strip('Counel No Admitted to Usdc-Nj Bar, Sample & Associates'), 'Sample & Associates')
        self.assertEqual(strip('Sample and Example, COUNSEL NOT ADMITTED TO USDC-NJ BAR'), 'Sample and Example')
        self.assertEqual(strip('Counsel Not Admitted to Usdc-Nj Bar.'), '')
        self.assertEqual(strip('Counsel Not Admitted Tou Usdc-Nj Bar'), '')
        # misspelled and differently worded notes seen in the source
        self.assertEqual(strip('COUNSEL NOT ADAMITTED TO USDC-NJ BAR, Sample & Example, LLC'), 'Sample & Example, LLC')
        self.assertEqual(strip('Counsel Not Admited to Usdc-Nj Bar, Sample & Example, Pc'), 'Sample & Example, Pc')
        self.assertEqual(strip('Counsel Not Admittted to Usdc-Nj Bar'), '')
        self.assertEqual(strip('Not a Member of Nj Bar, Sample, Example, LLC'), 'Sample, Example, LLC')
        self.assertEqual(strip('NOT MEMBER USDCNJ BAR, Sample Robb & Example'), 'Sample Robb & Example')
        self.assertEqual(strip('Mdl 2243, Sample and Example'), 'Sample and Example')
        # note and firm text in one segment cannot be separated by rule: nothing is published
        self.assertEqual(strip('Counsel Not Admitted to Usdc-Nj Bar the Sample Law Firm'), '')
        self.assertEqual(strip('Sample Firm LLC'), 'Sample Firm LLC')
        # both spellings now share one key
        self.assertEqual(build.firm_key(strip('COUNSEL NOT ADMITTED TO USDC-NJ BAR, Sample Firm LLC')), build.firm_key('SAMPLE FIRM LLC'))

    def test_firm_text_problem_rejects_litigant_address_lines(self):
        bad = ['# X-00000, Sample Correction Center', 'Sample Correctional Facility', 'Sample Federal Correctional Institution',
               'Sample County Jail', "C/O/ Sample's House", 'C/O A. Sample, Esq., Sample Example Firm', 'c/o the Sample Firm', 'Sample #',
               'Sample Firm LLC, One Sample Ctr., 17th Fl.', 'the Law Office of A. Sample-18th Floor', 'Sample Law Group, LLC, Suite A',
               'Sample Firm, P.C., 5 Sample Centre', 'The Sample Law Firm, 11A Sample Gade', 'Sample LLP, Two Sample Square',
               'Firm LLP, 100 Main Street', 'a@b.example', 'Sample Rose US LLP, ABC Plaza', 'Sample and Example, Sample Harbert Center',
               'Sample Traurig, LLP, Sample 200', 'Sample Co., Lpa, the Sample Building']
        for text in bad:
            self.assertIsNotNone(build.firm_text_problem(text), text)
        good = ['Sample Hardy & Example LLP - Tampa Fl', 'Sample Center for Law LLP', 'SEEGER WEISS LLP',
                'Sample City Law Department, Affirmative and Special Litigation Unit', 'Sample & Example LLP - 2', 'Sample, Example, Third, LLC']
        for text in good:
            self.assertIsNone(build.firm_text_problem(text), text)


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))

    def test_envelope_and_hashes(self):
        val = self.validation
        for key in ('schema_version', 'status', 'ready', 'validated_at', 'data_files', 'counts', 'checks', 'qualification', 'license_ref', 'inputs'):
            self.assertIn(key, val)
        self.assertEqual(val['status'], 'passed')
        self.assertIn('PARTIAL', val['qualification'])
        for item in val['data_files']:
            self.assertEqual(hashlib.sha256((HERE / item['path']).read_bytes()).hexdigest(), item['sha256'], item['path'])

    def test_receipts_match_manifest(self):
        manifest = json.loads((HERE / 'receipts' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(manifest['responses']), 40)
        for entry in manifest['responses']:
            self.assertEqual(hashlib.sha256((HERE / 'receipts' / entry['file']).read_bytes()).hexdigest(), entry['sha256'])
            self.assertTrue(entry['called_utc'] and entry['tool'] and 'arguments' in entry)

    def test_no_contact_details_in_data_files(self):
        for name in ('attorneys.jsonl', 'firms.jsonl', 'parties.jsonl', 'edges.jsonl'):
            text = (HERE / name).read_text(encoding='utf-8')
            self.assertIsNone(build.EMAIL.search(text), name)
            self.assertIsNone(build.PHONE.search(text), name)
            self.assertNotIn('"contact_raw"', text)

    def test_no_firm_name_is_an_admission_note_or_address_fragment(self):
        firms = [json.loads(l) for l in (HERE / 'firms.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        attorneys = [json.loads(l) for l in (HERE / 'attorneys.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        texts = [(f['id'], t) for f in firms for t in [f['name'], f['firm_key']] + f['name_variants']]
        texts += [(a['id'], a['firm_text']) for a in attorneys if a.get('firm_text')]
        self.assertGreater(len(firms), 1000)
        for row_id, text in texts:
            self.assertFalse(build.has_note(text), row_id)
            self.assertIsNone(build.firm_text_problem(text), row_id)
            self.assertIsNone(re.search(r'#|^c\s*/\s*o\b|\b(correction(al|s)?|prison|penitentiary|detention|inmate|jail)\b', text, re.I), row_id)
        for firm in firms:
            self.assertEqual(firm['firm_key'], build.firm_key(firm['name']))
            self.assertEqual(firm['admission_note_source_texts'], sum(1 for t in firm['source_texts'] if build.has_note(t)))

    def test_note_prefixed_spellings_fold_into_the_plain_firm_row(self):
        firms = {f['firm_key']: f for f in (json.loads(l) for l in (HERE / 'firms.jsonl').read_text(encoding='utf-8').splitlines() if l.strip())}
        motley = firms['motley rice llc']
        self.assertIn(2738, motley['mdl_numbers'])
        self.assertGreaterEqual(motley['admission_note_source_texts'], 1)
        self.assertEqual(self.validation['counts']['firm_rows'], len(firms))
        self.assertLess(len(firms), 2441)  # 2,441 before the admission-note and litigant-address repair

    def test_unresolved_rows_carry_reasons_not_text(self):
        rows = [json.loads(l) for l in (HERE / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertGreater(len(rows), 10)
        for row in rows:
            self.assertLessEqual(set(row), {'reason', 'mdl_number', 'receipt_file', 'cl_attorney_id', 'docket_id', 'count'})
            self.assertNotIn('#', row['reason'])

    def test_coverage_labels_and_real_rows(self):
        cov = json.loads((HERE / 'coverage.json').read_text(encoding='utf-8'))['mdls']
        self.assertEqual(sorted(cov), ['2666', '2738', '2789', '2846', '2873', '3060'])
        self.assertTrue(cov['2666']['attorney_coverage_label'].startswith('27 attorney records'))
        self.assertTrue(cov['2738']['party_names_withheld'])
        rows = [json.loads(l) for l in (HERE / 'attorneys.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        records = [r for r in rows if r['detail_level'] == 'attorney_record']
        self.assertEqual(len(records), 57)  # 27 full records (MDL 2666) + 30 by-id records (round 3, six per MDL)
        full = [r for r in records if r['record_scope'] == 'full_record_with_roles']
        slim = [r for r in records if r['record_scope'] == 'id_name_firm_line_only']
        self.assertEqual((len(full), len(slim)), (27, 30))
        self.assertTrue(all(isinstance(r['cl_attorney_id'], int) for r in records))
        self.assertTrue(all(r['roles'] and r['roles_note'] is None for r in full))
        self.assertTrue(all(r['roles'] == [] and r['roles_note'] and 'search-index attorney_id set' in r['mdl_link_basis'] for r in slim))
        for mdl in ('2738', '2846', '2873', '3060', '2789'):
            self.assertEqual(cov[mdl]['attorney_records_retrieved'], 6)
            self.assertTrue(cov[mdl]['attorney_coverage_label'].startswith('6 attorney records retrieved of '))
        seeger = [r for r in slim if r['cl_attorney_id'] == 3058321]
        self.assertEqual([(r['mdl_number'], r['firm_text'], r['state']) for r in seeger], [(2789, 'SEEGER WEISS LLP', 'NJ')])
        # same-docket exact-name suppression: a retrieved record never also appears as a name-only row of that MDL
        names = {(r['mdl_number'], r['name'].casefold()) for r in records}
        self.assertFalse([r['id'] for r in rows if r['detail_level'] != 'attorney_record' and (r['mdl_number'], r['name'].casefold()) in names])
        self.assertTrue(all(r['cl_attorney_id'] is None for r in rows if r['detail_level'] != 'attorney_record'))
        for row in rows[:50]:
            self.assertTrue(re.fullmatch(r'mdl:\d+', row['mdl_id']))
            self.assertIn('captured_at', row['temporal'])


if __name__ == '__main__':
    unittest.main()
