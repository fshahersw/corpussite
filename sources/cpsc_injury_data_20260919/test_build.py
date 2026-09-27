"""Tests for the CPSC injury data build (NEISS 2025 + SaferProducts.gov incident reports).

TDD order: these tests are written before build.py's helper functions and must fail (ImportError or
AssertionError) until build.py implements them. The parser/labelling helpers are pure functions with no
filesystem dependency so they can be tested without running the full build. A second test class checks the
built database when it exists (skipped otherwise, matching sources/agency_safety_20260919/test_build.py).
"""
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402


class NeissFormatLookupTests(unittest.TestCase):
    """The NEISS_FMT sheet is a SAS-style format table: (Format name, Starting value, Ending value,
    Format value label). Only exact-value rows (start == end, both integers) are usable as a code -> label
    lookup; the one true range (AGELTTWO 2-120) is not a per-value label and must not be used as one."""

    SAMPLE_FMT_ROWS = [
        ('SEX', '               0', '               0', 'UNKNOWN'),
        ('SEX', '               1', '               1', 'MALE'),
        ('SEX', '               2', '               2', 'FEMALE'),
        ('BDYPT', '              75', '              75', '75 - HEAD'),
        ('AGELTTWO', '               0', '               0', 'UNK'),
        ('AGELTTWO', '               2', '             120', '2 YEARS AND OLDER'),  # a range: not a label
        ('ALC_DRUG', '               .', '               .', 'NA before 2019'),     # non-numeric: skip
        ('PROD', '            1807', '            1807', '1807 - RUGS OR CARPETS, NOT SPECIFIED'),
    ]

    def setUp(self):
        self.fmt = build.load_neiss_fmt(self.SAMPLE_FMT_ROWS)

    def test_exact_value_rows_are_indexed(self):
        self.assertEqual(build.neiss_label(self.fmt, 'SEX', 1), 'MALE')
        self.assertEqual(build.neiss_label(self.fmt, 'SEX', 2), 'FEMALE')
        self.assertEqual(build.neiss_label(self.fmt, 'BDYPT', 75), '75 - HEAD')
        self.assertEqual(build.neiss_label(self.fmt, 'PROD', 1807), '1807 - RUGS OR CARPETS, NOT SPECIFIED')

    def test_range_row_is_not_used_as_a_label(self):
        # AGELTTWO 2-120 is a flag range ("2 years and older"), not a per-value label; looking up e.g. 47
        # must not silently return "2 YEARS AND OLDER" as if it were 47's label.
        self.assertIsNone(build.neiss_label(self.fmt, 'AGELTTWO', 47))

    def test_non_numeric_rows_are_skipped(self):
        self.assertIsNone(build.neiss_label(self.fmt, 'ALC_DRUG', 0))

    def test_unknown_code_or_format_leaves_code_unlabelled(self):
        self.assertIsNone(build.neiss_label(self.fmt, 'SEX', 9))
        self.assertIsNone(build.neiss_label(self.fmt, 'NO_SUCH_FORMAT', 1))
        self.assertIsNone(build.neiss_label(self.fmt, 'SEX', None))


class AgeDerivationTests(unittest.TestCase):
    """NEISS codes age 0 = unknown, 2-120 = whole years, 201-223 = 1-23 months (children under 2).
    age_display/age_band are pure, deterministic functions of the coded value: no imputation, no
    population-level statistic, just a per-row mechanical translation of the publisher's own coding."""

    def test_unknown(self):
        self.assertEqual(build.age_display(0), 'Unknown')
        self.assertEqual(build.age_band(0), 'Unknown')

    def test_whole_years(self):
        self.assertEqual(build.age_display(47), '47 years')
        self.assertEqual(build.age_display(2), '2 years')
        self.assertEqual(build.age_display(120), '120 years')

    def test_months_under_two(self):
        self.assertEqual(build.age_display(201), '1 month')
        self.assertEqual(build.age_display(206), '6 months')
        self.assertEqual(build.age_display(212), '12 months')
        self.assertEqual(build.age_display(223), '23 months')

    def test_out_of_range_code_is_labelled_not_guessed(self):
        self.assertEqual(build.age_display(999), 'Unknown (code 999)')
        self.assertEqual(build.age_band(999), 'Unknown')
        self.assertIsNone(build.age_display(None))

    def test_age_bands(self):
        self.assertEqual(build.age_band(0), 'Unknown')       # code 0 = unknown
        self.assertEqual(build.age_band(201), 'Under 1 year')  # 1 month
        self.assertEqual(build.age_band(211), 'Under 1 year')  # 11 months
        self.assertEqual(build.age_band(212), '1-4 years')     # 12 months = 1 year old
        self.assertEqual(build.age_band(223), '1-4 years')     # 23 months
        self.assertEqual(build.age_band(2), '1-4 years')
        self.assertEqual(build.age_band(4), '1-4 years')
        self.assertEqual(build.age_band(5), '5-9 years')
        self.assertEqual(build.age_band(17), '15-19 years')
        self.assertEqual(build.age_band(64), '55-64 years')
        self.assertEqual(build.age_band(75), '75+ years')
        self.assertEqual(build.age_band(120), '75+ years')


class SaferProductsPiiColumnTests(unittest.TestCase):
    """Build-time safety net: any column whose header looks like a submitter name/e-mail/phone field must
    be identified and dropped, and the drop must be counted (never silently included, never silently
    dropped without a count). The as-downloaded SaferProducts.gov export carries no such column, but the
    check must still run so a future export gaining one cannot slip through unnoticed."""

    REAL_HEADER = [
        'Report No.', 'Report Date', 'Sent to Manufacturer / Importer / Private Labeler', 'Publication Date',
        'Category of Submitter', 'Product Description', 'Product Category', 'Product Sub Category',
        'Product Type', 'Product Code', 'Manufacturer / Importer / Private Labeler Name', 'Brand',
        'Model Name or Number', 'Serial Number', 'UPC', 'Date Manufactured', 'Manufacturer Date Code',
        'Retailer', 'Retailer State', 'Purchase Date', 'Purchase Date Is Estimate', 'Incident Description',
        'City', 'State', 'ZIP', 'Location', '(Primary) Victim Severity', "(Primary) Victim's Sex",
        'My Relation To The (Primary) Victim', "(Primary) Victim's Age (years)", 'Submitter Has Product',
        'Product Was Damaged Before Incident', 'Damage Description', 'Damage Repaired',
        'Product Was Modified Before Incident', 'Have You Contacted The Manufacturer', 'If Not Do You Plan To',
        'Answer Explanation', 'Company Comments', 'Associated Report Numbers',
    ]

    def test_real_header_drops_nothing(self):
        kept, dropped = build.classify_saferproducts_columns(self.REAL_HEADER)
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), len(self.REAL_HEADER))

    def test_submitter_identity_columns_are_flagged_and_dropped(self):
        header = self.REAL_HEADER + ['Submitter Name', 'Submitter Email Address', 'Submitter Phone Number']
        kept, dropped = build.classify_saferproducts_columns(header)
        self.assertEqual(set(dropped), {'Submitter Name', 'Submitter Email Address', 'Submitter Phone Number'})
        self.assertNotIn('Submitter Name', kept)
        # Manufacturer/brand "name" fields are product/company data, not submitter identity: keep them.
        self.assertIn('Manufacturer / Importer / Private Labeler Name', kept)
        self.assertIn('Brand', kept)


class BuiltDatabaseTests(unittest.TestCase):
    """Assertions against the real built database. Skipped when it has not been built yet."""

    @classmethod
    def setUpClass(cls):
        cls.db_path = HERE / 'cpsc_injury_data.sqlite3'
        if not cls.db_path.exists():
            raise unittest.SkipTest('cpsc_injury_data.sqlite3 not built yet')
        cls.con = sqlite3.connect(f'file:{cls.db_path.as_posix()}?mode=ro', uri=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'con'):
            cls.con.close()

    def test_neiss_row_count(self):
        n = self.con.execute('select count(*) from neiss_cases').fetchone()[0]
        self.assertEqual(n, 410201)

    def test_saferproducts_row_count(self):
        n = self.con.execute('select count(*) from saferproducts_incidents').fetchone()[0]
        self.assertEqual(n, 69333)

    def test_neiss_fmt_row_count(self):
        n = self.con.execute('select count(*) from neiss_fmt').fetchone()[0]
        self.assertEqual(n, 1250)

    def test_neiss_weight_column_is_untouched_publisher_value(self):
        row = self.con.execute('select weight from neiss_cases where case_number = ?', ('250102066',)).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], 46.4246, places=4)

    def test_neiss_product_label_from_embedded_code_table(self):
        row = self.con.execute("select product_1_label from neiss_cases where case_number = ?", ('250102066',)).fetchone()
        self.assertEqual(row[0], '4076 - BEDS OR BEDFRAMES, OTHER OR NOT SPECIFIED')

    def test_no_submitter_pii_columns_in_saferproducts_table(self):
        cols = [r[1] for r in self.con.execute('pragma table_info(saferproducts_incidents)')]
        for banned in ('submitter_name', 'submitter_email', 'submitter_phone', 'email', 'phone'):
            self.assertNotIn(banned, cols)

    def test_fts_search_neiss_narrative(self):
        rows = self.con.execute("select rowid from neiss_fts where neiss_fts match 'laceration'").fetchall()
        self.assertGreater(len(rows), 0)

    def test_fts_search_saferproducts(self):
        rows = self.con.execute("select rowid from saferproducts_fts where saferproducts_fts match 'scooter'").fetchall()
        self.assertGreater(len(rows), 0)


if __name__ == '__main__':
    unittest.main()
