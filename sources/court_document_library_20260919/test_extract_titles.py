"""Tests for extract_titles.first_page_title's line-acceptance rules. Written before the reject-list
fix (repair review defect: letterhead / pagination / address / placeholder lines were being accepted
as titles). Exercises the pure line-classification helper without touching any real PDF."""
import unittest

import extract_titles as et


def accept(line):
    """True if `line` alone (as the only line of page-1 text) would be accepted as a title."""
    return et._accept_title_line(line)


class RejectLetterhead(unittest.TestCase):
    def test_rejects_us_district_court(self):
        self.assertFalse(accept('UNITED STATES DISTRICT COURT'))

    def test_rejects_us_bankruptcy_court(self):
        self.assertFalse(accept('UNITED STATES BANKRUPTCY COURT'))

    def test_rejects_state_superior_court_letterhead(self):
        self.assertFalse(accept('COMMONWEALTH OF VIRGINIA CIRCUIT COURT'))


class RejectShortLines(unittest.TestCase):
    def test_rejects_short_fragment(self):
        self.assertFalse(accept('VIRGINIA:'))

    def test_rejects_in_the(self):
        self.assertFalse(accept('IN THE'))

    def test_rejects_whereas(self):
        self.assertFalse(accept('WHEREAS'))

    def test_rejects_short_date(self):
        self.assertFalse(accept('Jun 30'))

    def test_rejects_report(self):
        self.assertFalse(accept('REPORT'))


class RejectPaginationAndTables(unittest.TestCase):
    def test_rejects_page_of(self):
        self.assertFalse(accept('Page 1 of 5'))

    def test_rejects_total_numeric_row(self):
        self.assertFalse(accept('Total 346,520 292,159 278,799 359,880 312,004'))


class RejectAddresses(unittest.TestCase):
    def test_rejects_street_address(self):
        self.assertFalse(accept('240 North Third Street'))

    def test_rejects_suite_and_phone(self):
        self.assertFalse(accept('1501 West Washington Street, Suite 221 (602) 452-3311'))

    def test_rejects_law_firm_building_block(self):
        self.assertFalse(accept('Kirkpatrick Lockhart Payne Shoemaker Building 240 North Third Street'))


class RejectPlaceholder(unittest.TestCase):
    def test_rejects_please_wait(self):
        self.assertFalse(accept('Please wait...'))


class AcceptRealTitles(unittest.TestCase):
    def test_accepts_local_rules_title(self):
        self.assertTrue(accept('Local Rules of Civil Procedure for the District Court'))

    def test_accepts_standing_order_title(self):
        self.assertTrue(accept('Standing Order Regarding Electronic Filing Procedures'))

    def test_accepts_form_title(self):
        self.assertTrue(accept('Application to Proceed In Forma Pauperis'))


class FirstPageTitleScansPastRejects(unittest.TestCase):
    def test_skips_letterhead_then_takes_real_title(self):
        # first_page_title takes (path, pypdf); exercise the line-scanning logic directly via a fake
        # reader/page pair so no real PDF or pypdf install is required for this unit test.
        class FakePage:
            def extract_text(self):
                return 'UNITED STATES DISTRICT COURT\nPage 1 of 5\nLocal Rules of Civil Procedure\n'

        class FakeReader:
            def __init__(self, *a, **k):
                self.pages = [FakePage()]

        class FakePypdf:
            PdfReader = FakeReader

        title, text_extracted = et.first_page_title('irrelevant.pdf', FakePypdf())
        self.assertEqual(title, 'Local Rules of Civil Procedure')
        self.assertTrue(text_extracted)

    def test_all_lines_rejected_leaves_title_null_but_text_extracted(self):
        class FakePage:
            def extract_text(self):
                return 'UNITED STATES DISTRICT COURT\nPage 1 of 5\nVIRGINIA:\n'

        class FakeReader:
            def __init__(self, *a, **k):
                self.pages = [FakePage()]

        class FakePypdf:
            PdfReader = FakeReader

        title, text_extracted = et.first_page_title('irrelevant.pdf', FakePypdf())
        self.assertIsNone(title)
        self.assertTrue(text_extracted)


if __name__ == '__main__':
    unittest.main()
