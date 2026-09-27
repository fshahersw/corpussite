"""Classifier tests: NAAG filename set used by the audit (pass 2) plus pass ordering."""
import json
import unittest
from collections import Counter
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from classifier import DOCUMENT_TYPES, TAXONOMY, UNCLASSIFIED, classify, mass_tort_signals  # noqa: E402


class TaxonomyTest(unittest.TestCase):
    def test_29_rows_and_unique_values(self):
        self.assertEqual(len(TAXONOMY), 29)
        self.assertEqual(len(DOCUMENT_TYPES), len(set(DOCUMENT_TYPES)))
        self.assertNotIn(UNCLASSIFIED, DOCUMENT_TYPES)


class NaagFilenameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((HERE / 'fixtures/naag_filenames.json').read_text(encoding='utf-8'))
        cls.items = fixture['items']
        cls.results = {item['filename']: classify(filename=item['filename']) for item in cls.items}

    def test_fixture_is_the_audit_set(self):
        self.assertEqual(len(self.items), 240)
        self.assertTrue(all(item['url'].startswith('https://www.naag.org/') for item in self.items))

    def test_counts_are_pinned(self):
        counts = Counter(result['type'] for result in self.results.values())
        self.assertEqual(counts[UNCLASSIFIED], 19)
        self.assertEqual(sum(counts.values()) - counts[UNCLASSIFIED], 221)
        self.assertEqual(counts['consent_judgment_or_decree'], 68)
        self.assertEqual(counts['settlement_agreement'], 68)
        self.assertEqual(counts['assurance_of_voluntary_compliance'], 30)
        self.assertEqual(counts['complaint_or_petition'], 27)
        self.assertEqual(counts['administrative_order'], 6)

    def test_every_result_has_basis_and_evidence(self):
        for name, result in self.results.items():
            self.assertEqual(set(result), {'type', 'type_basis', 'type_evidence'})
            if result['type'] == UNCLASSIFIED:
                self.assertEqual(result['type_basis'], 'none')
                self.assertIsNone(result['type_evidence'])
            else:
                self.assertEqual(result['type_basis'], 'filename')
                self.assertTrue(result['type_evidence']['matched'])
                self.assertIn(result['type'], DOCUMENT_TYPES)

    def test_audit_residue_fixes(self):
        expect = {
            '546-2014.08.27-Multistate-Tyson-Foods-Final-Judgement.pdf': 'final_judgment',
            '481-2012.02.09-Multistate-Wells-Fargo-Consent-Judgement.pdf': 'consent_judgment_or_decree',
            '597-2016.05.10-Multistate-Staples-Memorandum-Opinion.pdf': 'court_opinion',
            '584-2015.10.29-Multistate-Transocean-Order-of-Dismissal.pdf': 'dismissal_order',
            '571-2015.05.23-FTC-US-Foods-Order-Dismissing-Complaint.pdf': 'dismissal_order',
            '561-2015.02.11-CA-Safeway-Amended-Order.pdf': UNCLASSIFIED,
            '570-2015.05.20-Multistate-Radioshack-Notice-of-Agreement.pdf': UNCLASSIFIED,
        }
        for name, kind in expect.items():
            self.assertEqual(self.results[name]['type'], kind, name)

    def test_specific_instruments(self):
        expect = {
            '2022.06.16-Mallinckrodt-NOAT-II-Trust-Distribution-Procedures.pdf': 'trust_distribution_procedures',
            '2022.06.16-Mallinckrodt-National-Opioid-Abatement-Trust-II-Trust-Agreement.pdf': 'trust_agreement',
            '2022.06.06-Mallinckrodt-Chapter-11-Plan-of-Reorganization-of-Mallinckrodt-plc.pdf': 'bankruptcy_plan',
            '2022.03.02-Mallinckrodt-U.S.-Bankruptcy-Courts-Confirmation-Order.pdf': 'plan_confirmation_order',
            '2022.02.13-Executive-Summary-of-National-Opioid-Settlement-1.pdf': 'executive_summary',
            '2021.07.21-NC-FAQs.pdf': 'faq_page',
            '559-2015.01.21-SEC-Standard-and-Poors-Order-Instituting-Administrative-and-Cease-and-Desist-Proceedings.pdf': 'administrative_order',
            '631-2017.09.05-FTC-Lenovo-Inc.-Consent-Order.pdf': 'administrative_order',
            '632-2017.09.28-GA-Ocwen-Financial-Consent-Order.pdf': 'consent_judgment_or_decree',
            '635-2017.10.31-VT-Hilton-Hotels-Assurance-of-Discontinuance.pdf': 'assurance_of_voluntary_compliance',
            '626-2017.05.24-CT-Johnson-and-Johnson-Final-Judgment-Upon-Stipulation.pdf': 'consent_judgment_or_decree',
            '2021.09.18-Distributor-Settlement-Agreement.pdf': 'settlement_agreement',
            '686-2020.09.24-AZ-C.R.-Bard-Inc-Complaint.pdf': 'complaint_or_petition',
        }
        for name, kind in expect.items():
            self.assertEqual(self.results[name]['type'], kind, name)

    def test_wrapper_documents_are_not_the_embedded_instrument(self):
        self.assertEqual(self.results['2020.09.20-NE-Order-Approving-AVC.pdf']['type'], UNCLASSIFIED)
        self.assertEqual(self.results['2020.09.15-NE-Application-for-Approval-of-AVC.pdf']['type'], UNCLASSIFIED)


class PassOrderTest(unittest.TestCase):
    def test_link_text_decides_before_filename(self):
        result = classify(link_text='Long Form Notice', filename='doc_1234.pdf')
        self.assertEqual((result['type'], result['type_basis']), ('long_form_notice', 'link_text'))
        result = classify(link_text='Settlement Agreement', filename='claim-form.pdf')
        self.assertEqual((result['type'], result['type_basis']), ('settlement_agreement', 'link_text'))

    def test_filename_when_link_text_is_silent(self):
        result = classify(link_text='Download', url='https://example.org/docs/Preliminary%20Approval%20Order.pdf')
        self.assertEqual((result['type'], result['type_basis']), ('preliminary_approval_order', 'filename'))

    def test_first_page_text_when_both_silent(self):
        text = 'UNITED STATES DISTRICT COURT\nEASTERN DISTRICT OF PENNSYLVANIA\n\nCLASS ACTION SETTLEMENT AGREEMENT\n\nThis agreement...'
        result = classify(link_text='Document 12', filename='0012.pdf', first_page_text=text)
        self.assertEqual((result['type'], result['type_basis']), ('settlement_agreement', 'first_page_text'))
        self.assertEqual(result['type_evidence']['where'], 'first_page_caption_title')

    def test_caption_title_overrules_and_records_it(self):
        text = 'IN THE DISTRICT COURT\n\nCONSENT JUDGMENT\n\nPlaintiff, the State ...'
        result = classify(filename='2020-Complaint.pdf', first_page_text=text)
        self.assertEqual(result['type'], 'consent_judgment_or_decree')
        self.assertEqual(result['type_evidence']['overruled']['type'], 'complaint_or_petition')

    def test_body_text_without_caption_cannot_overrule(self):
        text = 'the parties refer to the consent judgment entered last year in a related case.'
        result = classify(filename='2020-Complaint.pdf', first_page_text=text)
        self.assertEqual((result['type'], result['type_basis']), ('complaint_or_petition', 'filename'))

    def test_notice_length_rule(self):
        text = 'A court authorized this notice. This is not a solicitation from a lawyer.'
        self.assertEqual(classify(first_page_text=text, page_count=12)['type'], 'long_form_notice')
        self.assertEqual(classify(first_page_text=text, page_count=2)['type'], 'short_form_notice')
        self.assertEqual(classify(first_page_text=text, page_count=None)['type'], UNCLASSIFIED)

    def test_pages(self):
        self.assertEqual(classify(url='https://www.examplesettlement.com/', is_html=True)['type'], 'settlement_website_home')
        self.assertEqual(classify(link_text='FAQs', url='https://www.examplesettlement.com/faq', is_html=True)['type'], 'faq_page')
        self.assertEqual(classify(link_text='Important Dates', is_html=True)['type'], 'deadlines_page')
        self.assertEqual(classify(url='https://www.example.gov/about/mission', is_html=True)['type'], UNCLASSIFIED)

    def test_nothing_is_guessed(self):
        self.assertEqual(classify(link_text='Click here', filename='a1b2c3.pdf', first_page_text='lorem ipsum')['type'], UNCLASSIFIED)


class MassTortSignalTest(unittest.TestCase):
    def test_terms(self):
        signals = mass_tort_signals('In re: Valsartan Products Liability Litigation, MDL No. 2875')
        categories = {s['category'] for s in signals}
        self.assertTrue({'mdl', 'product_liability', 'pharmaceutical'} <= categories)
        self.assertEqual(mass_tort_signals('Lead plaintiff wage settlement for drivers'), [])


if __name__ == '__main__':
    unittest.main()
