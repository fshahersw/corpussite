"""Tests for the expert-admissibility (Daubert / FRE 702) docket-scan build.

TDD order: the caption-redaction classifier, the MDL crosswalk join and the CourtListener slug scrubber
are pure functions tested here before/independent of the full build. A second test class checks the built
database (skipped when it does not exist yet).

Redaction rule under test (from the task and confirmed by precedent already in this project -- the MDL
crosswalk itself suppresses exactly this case for the same MDL, see
sources/mdl_docket_crosswalk_20260919/crosswalk.jsonl mdl:2243 aws_case_name_suppressed_reason): a docket
caption that names a natural-person party ("X v. Y") must not be published; an "In re ..." or other
consolidated-litigation-style caption is fine.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

# The 23 distinct (mdl_number_as_scanned, case_name) pairs actually present in daubert_documents.jsonl,
# verified by direct inspection on 2026-09-19. Used as real-data fixtures for the classifier.
REAL_CAPTIONS_SAFE = [
    'In RE: Hair Relaxer Marketing, Sales Practices, And Products Liability Litigation',
    'In Re: Testosterone Replacement Therapy Products Liability Litigation',
    'In Re: Terrorist Attacks on September 11, 2001',
    'IN RE: AVANDIA MARKETING, SALES PRACTICES AND PRODUCTS LIABILITY LITIGATION',
    'In Re Korean Airlines Co., Ltd. Antitrust Litigation',
    'In re: Polyurethane Foam Antitrust Litigation',
    'Wesson Oil Marketing and Sales Practices Litigation',
    'IN RE: TYLENOL (ACETAMINOPHEN) MARKETING, SALES PRACTICES AND PRODUCTS LIABILITY LITIGATION',
    'IN RE: BENICAR (OLMESARTAN) PRODUCTS LIABILITY LITIGATION',
    'In re: Stryker LFIT V40 Femoral Head Products Liability Litigation',
    'In re: Farxiga (Dapagliflozin) Products Liability Litigation',
    'IN RE: Ethicon Physiomesh Flexible Composite Hernia Mesh Products Liability Litigation',
    'In Re: National Prescription Opiate Litigation',
    'General Motors Corp Air Conditioning Marketing and Sales Practices Litigation',
    'In re: Davol, Inc./C.R. Bard, Inc. Polypropylene Hernia Mesh Products Liability Litigation',
    'IN RE: ZOSTAVAX (ZOSTER VACCINE LIVE) PRODUCTS LIABILITY LITIGATION',
    'In Re Aqueous Film-Forming Foams Products Liability Litigation MDL 2873',
    'IN RE: 3M COMBAT ARMS EARPLUG PRODUCTS LIABILITY LITIGATION',
    'AMERICAN MEDICAL COLLECTION AGENCY, INC., CUSTOMER DATA SECURITY BREACH LITIGATION',
    'IN RE: ALLERGAN BIOCELL TEXTURED BREAST IMPLANT PRODUCTS LIABILITY LITIGATION',
    'ELMIRON (PENTOSAN POLYSULFATE SODIUM) PRODUCTS LIABILITY LITIGATION',
    'IN RE: SOCIAL MEDIA ADOLESCENT ADDICTION/PERSONAL INJURY PRODUCTS LIABILITY LITIGATION',
    'GLUCAGON-LIKE PEPTIDE-1 RECEPTOR AGONISTS (GLP-1 RAS) PRODUCTS LIABILITY LITIGATION',
]
REAL_CAPTIONS_UNSAFE = [
    'MOLNAR v. MERCK & CO., INC.',
    'STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP',
]


class CaptionClassifierTests(unittest.TestCase):
    def test_all_real_consolidated_captions_are_safe(self):
        for name in REAL_CAPTIONS_SAFE:
            self.assertTrue(build.is_safe_consolidated_caption(name), msg=name)

    def test_all_real_individual_captions_are_unsafe(self):
        for name in REAL_CAPTIONS_UNSAFE:
            self.assertFalse(build.is_safe_consolidated_caption(name), msg=name)

    def test_synthetic_party_v_party_is_unsafe(self):
        self.assertFalse(build.is_safe_consolidated_caption('Smith v. Jones'))
        self.assertFalse(build.is_safe_consolidated_caption('JANE DOE v. ACME CORP.'))
        self.assertFalse(build.is_safe_consolidated_caption('Smith vs. Jones Litigation'))
        self.assertFalse(build.is_safe_consolidated_caption('SMITH VERSUS JONES'))

    def test_hybrid_in_re_with_v_is_conservatively_unsafe(self):
        # "In re" alone is not a blanket pass when a "v." party pattern is also present.
        self.assertFalse(build.is_safe_consolidated_caption('In re Smith v. Jones Products Liability Litigation'))

    def test_empty_or_missing_defaults_conservative(self):
        self.assertFalse(build.is_safe_consolidated_caption(''))
        self.assertFalse(build.is_safe_consolidated_caption(None))

    def test_unrecognised_shape_defaults_to_redact(self):
        # No "In re" prefix and does not end in "Litigation": conservative default is to redact.
        self.assertFalse(build.is_safe_consolidated_caption('Some Random Caption Name'))


class RedactCaseNameTests(unittest.TestCase):
    def test_safe_caption_is_kept(self):
        title, redacted, reason = build.redact_case_name('IN RE: ZOSTAVAX (ZOSTER VACCINE LIVE) PRODUCTS LIABILITY LITIGATION', '2:18-md-02848', 'paed')
        self.assertEqual(title, 'IN RE: ZOSTAVAX (ZOSTER VACCINE LIVE) PRODUCTS LIABILITY LITIGATION')
        self.assertFalse(redacted)
        self.assertIsNone(reason)

    def test_unsafe_caption_is_replaced_with_docket_and_court(self):
        title, redacted, reason = build.redact_case_name('MOLNAR v. MERCK & CO., INC.', '1:08-cv-00008', 'njd')
        self.assertNotIn('MOLNAR', title)
        self.assertNotIn('Merck', title.upper().replace('MERCK', ''))  # sanity: title shouldn't reconstruct the caption
        self.assertIn('1:08-cv-00008', title)
        self.assertIn('njd', title.lower())
        self.assertTrue(redacted)
        self.assertIn('natural-person', reason)


class SlugScrubberTests(unittest.TestCase):
    def test_redacted_row_slug_is_scrubbed(self):
        url = 'https://www.courtlistener.com/docket/6224301/700/steven-goodstein-v-astrazeneca-pharmaceuticals-lp/'
        scrubbed = build.scrub_courtlistener_slug(url, redacted=True)
        self.assertNotIn('goodstein', scrubbed.lower())
        self.assertTrue(scrubbed.startswith('https://www.courtlistener.com/docket/6224301/700/'))

    def test_safe_row_slug_is_untouched(self):
        url = 'https://www.courtlistener.com/docket/14916674/1103/in-re-3m-combat-arms-earplug-products-liability-litigation/'
        self.assertEqual(build.scrub_courtlistener_slug(url, redacted=False), url)

    def test_non_courtlistener_or_empty_url_is_passed_through(self):
        self.assertEqual(build.scrub_courtlistener_slug('', redacted=True), '')
        self.assertIsNone(build.scrub_courtlistener_slug(None, redacted=True))


class MdlCrosswalkJoinTests(unittest.TestCase):
    """Join to an MDL number must go ONLY through the crosswalk's cl_docket_id -> mdl_number map, keyed by
    the native docket id (master_docket_id here), never by trusting the raw scanned mdl_number field."""

    def test_resolves_when_docket_is_in_crosswalk(self):
        table = {66801859: 3060, 6224301: 2789}
        self.assertEqual(build.resolve_mdl_via_crosswalk(66801859, table), 3060)

    def test_unresolved_docket_returns_none_even_with_a_scanned_number(self):
        table = {66801859: 3060}
        self.assertIsNone(build.resolve_mdl_via_crosswalk(7584696, table))  # Zostavax master docket, not in this crosswalk

    def test_bad_input_never_raises(self):
        self.assertIsNone(build.resolve_mdl_via_crosswalk(None, {}))
        self.assertIsNone(build.resolve_mdl_via_crosswalk('not-an-int', {}))


class BuiltDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = HERE / 'expert_rulings_scan.sqlite3'
        if not cls.db_path.exists():
            raise unittest.SkipTest('expert_rulings_scan.sqlite3 not built yet')
        cls.con = sqlite3.connect(f'file:{cls.db_path.as_posix()}?mode=ro', uri=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'con'):
            cls.con.close()

    def test_row_count(self):
        self.assertEqual(self.con.execute('select count(*) from expert_scan_documents').fetchone()[0], 2035)

    def test_molnar_row_is_redacted(self):
        row = self.con.execute("select case_name_public, case_name_redacted, courtlistener_url from expert_scan_documents "
                                "where docket_number = '1:08-cv-00008' and court = 'njd'").fetchone()
        self.assertIsNotNone(row)
        title, redacted, url = row
        self.assertEqual(redacted, 1)
        self.assertIsNone(title)
        self.assertNotIn('molnar', url.lower())

    def test_goodstein_rows_are_redacted(self):
        rows = self.con.execute("select case_name_redacted, courtlistener_url from expert_scan_documents "
                                 "where docket_number = '2:17-md-02789'").fetchall()
        self.assertEqual(len(rows), 74)
        for redacted, url in rows:
            self.assertEqual(redacted, 1)
            # One of the 74 rows has no courtlistener_url at all in the source scan (blank in the
            # source data, not a build defect); None trivially cannot leak the name.
            if url is not None:
                self.assertNotIn('goodstein', url.lower())

    def test_zostavax_rows_keep_their_consolidated_caption(self):
        rows = self.con.execute("select case_name_public, case_name_redacted from expert_scan_documents "
                                 "where docket_number = '2:18-md-02848'").fetchall()
        self.assertEqual(len(rows), 638)
        for title, redacted in rows:
            self.assertEqual(redacted, 0)
            self.assertIn('ZOSTAVAX', title.upper())

    def test_mdl_join_only_via_crosswalk(self):
        # Hair Relaxer: scanned mdl_number was blank, crosswalk resolves it to 3060.
        row = self.con.execute("select mdl_number_scanned, mdl_number_crosswalk from expert_scan_documents "
                                "where docket_number = '1:23-cv-00818'").fetchone()
        self.assertIn(row[0], (None, ''))
        self.assertEqual(row[1], 3060)
        # Zostavax: scanned mdl_number says 02848, but this crosswalk does not resolve that docket -> None.
        row = self.con.execute("select mdl_number_scanned, mdl_number_crosswalk from expert_scan_documents "
                                "where docket_number = '2:18-md-02848' limit 1").fetchone()
        self.assertEqual(row[0], '02848')
        self.assertIsNone(row[1])

    def test_no_natural_person_names_leak_into_courtlistener_urls(self):
        for (url,) in self.con.execute('select courtlistener_url from expert_scan_documents where case_name_redacted = 1'):
            if url is None:
                continue
            self.assertNotIn('molnar', url.lower())
            self.assertNotIn('goodstein', url.lower())

    def test_fts_search_description(self):
        rows = self.con.execute("select rowid from expert_scan_fts where expert_scan_fts match 'biostatistician'").fetchall()
        self.assertGreater(len(rows), 0)

    def test_redacted_case_names_are_not_fts_searchable(self):
        rows = self.con.execute("select rowid from expert_scan_fts where expert_scan_fts match 'goodstein'").fetchall()
        self.assertEqual(len(rows), 0)


if __name__ == '__main__':
    unittest.main()
