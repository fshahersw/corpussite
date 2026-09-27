"""Regressions from observed county navigation false positives."""
import unittest
from prepare_county_litigation_seeds_20260919 import topic, canonical


class SeedReviewTests(unittest.TestCase):
    def test_information_is_not_form(self):
        self.assertEqual(topic('Court Information','https://county.gov/court-information')[0],'court_information')

    def test_quorum_and_misspelled_quorum_are_not_judicial(self):
        for title in ['Quorum Court','Quroum Court Districts']:
            self.assertIsNone(topic(title,'https://county.gov/court/districts.pdf'))

    def test_law_enforcement_not_selected_from_court_parent_path(self):
        self.assertIsNone(topic('Criminal Investigations','https://county.gov/courts-law-enforcement/sheriffs-department/criminal-investigations/'))

    def test_vehicle_titles_not_court_filings(self):
        self.assertIsNone(topic('Titles','https://county.gov/clerk-of-courts/titles/'))

    def test_direct_official_fee_pdf_remains_selected(self):
        self.assertEqual(topic('Probate Court Filing Fees','https://county.gov/files/court-costs.pdf'),('fee_schedule',2))

    def test_nonweb_and_credential_urls_are_rejected(self):
        self.assertIsNone(canonical('javascript:alert(1)'))
        self.assertIsNone(canonical('https://secret:secret@county.gov/'))


if __name__=='__main__':unittest.main()
