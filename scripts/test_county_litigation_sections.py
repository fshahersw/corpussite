import unittest
from county_litigation_sections import extract_structure, normalize_date


class StructureTests(unittest.TestCase):
    def test_exact_offsets_unicode_and_crlf(self):
        text='  LOCAL RULES – COURT\r\n\n  Rule 180. Photographing in Court\r\nBody.'
        result=extract_structure(text,'local_rule')
        evidence=result['outline'][0]['evidence']
        self.assertEqual(text[evidence['start']:evidence['end']],evidence['excerpt'])
        self.assertEqual(result['outline'][0]['number'],'180')

    def test_capture_and_citation_dates_are_not_document_dates(self):
        text='Captured 2026-09-19\nThe statute was effective January 1, 2025.\nRule 300. Effective January 1, 2020\n'
        self.assertEqual(extract_structure(text,'local_rule')['date_observations'],[])

    def test_issued_and_effective_remain_distinct(self):
        text='(issued 07/01/25)\nEffective July 1, 2025\n'
        dates=extract_structure(text,'local_rule')['date_observations']
        self.assertEqual([d['label'] for d in dates],['issued','effective'])
        self.assertIsNone(dates[0]['iso_date'])
        self.assertEqual(dates[1]['iso_date'],'2025-07-01')

    def test_repealed_division_not_individual_entry(self):
        text='DIVISION 4\nCIVIL (CASES OVER $35,000) – Repealed\n400. Preparation of Forms – Repealed 07/01/09\n'
        flags=extract_structure(text,'local_rule')['status_observations']
        self.assertEqual(len(flags),1)
        self.assertEqual(flags[0]['status'],'repealed')

    def test_no_legal_outline_for_court_information(self):
        self.assertEqual(extract_structure('Rule 10.855 provides guidance.','court_information')['outline'],[])

    def test_invalid_dates_not_normalized(self):
        self.assertIsNone(normalize_date('02/31/2025'))
        self.assertIsNone(normalize_date('7/1/25'))


if __name__=='__main__':unittest.main()
