"""TDD tests for the classifier/join/labelling rules used by build.py, plus one real-build smoke test.
Run: python -m unittest discover -s <this dir> -p test_build.py
"""
import json
import unittest
from pathlib import Path

import build


class ClassifyEntryTypeTests(unittest.TestCase):
    def test_settlement_wins_over_case_management_order(self):
        text = "Case Management Order No. 12 Regarding Settlement Fund Administration (Entered: 01/02/2020)"
        self.assertEqual(build.classify_entry_type(text), 'settlement')

    def test_bellwether(self):
        self.assertEqual(build.classify_entry_type("Order setting the third bellwether trial"), 'bellwether')

    def test_daubert(self):
        self.assertEqual(build.classify_entry_type("Order on Daubert motions"), 'daubert')

    def test_common_benefit(self):
        self.assertEqual(build.classify_entry_type("Order re: common benefit fund assessment"), 'common_benefit')

    def test_steering_committee(self):
        self.assertEqual(build.classify_entry_type("Order appointing Plaintiffs' Steering Committee"), 'steering_committee')

    def test_leadership(self):
        self.assertEqual(build.classify_entry_type("Order on leadership applications"), 'leadership')

    def test_pretrial_order(self):
        self.assertEqual(build.classify_entry_type("Pretrial Order No. 4"), 'pretrial_order')

    def test_case_management_order(self):
        self.assertEqual(build.classify_entry_type("Case Management Order No. 1"), 'case_management_order')

    def test_other(self):
        self.assertEqual(build.classify_entry_type("Letter from counsel regarding scheduling"), 'other')

    def test_empty_text_is_other(self):
        self.assertEqual(build.classify_entry_type(""), 'other')
        self.assertEqual(build.classify_entry_type(None), 'other')

    def test_case_insensitive(self):
        self.assertEqual(build.classify_entry_type("SETTLEMENT conference set"), 'settlement')


class ParseEnteredDateTests(unittest.TestCase):
    def test_parses_trailing_entered_date(self):
        iso, basis = build.parse_entered_date("Letter from counsel (SEEGER, CHRISTOPHER) (Entered: 12/16/2019)")
        self.assertEqual(iso, '2019-12-16')
        self.assertIn('Entered', basis)

    def test_returns_none_when_absent(self):
        iso, basis = build.parse_entered_date("Order with no date pattern")
        self.assertIsNone(iso)
        self.assertIsNone(basis)

    def test_returns_none_on_bad_calendar_date(self):
        iso, basis = build.parse_entered_date("Some entry (Entered: 02/30/2020)")
        self.assertIsNone(iso)

    def test_none_input(self):
        iso, basis = build.parse_entered_date(None)
        self.assertIsNone(iso)


class NormalizeEntryNumberTests(unittest.TestCase):
    def test_numeric_string(self):
        text, num = build.normalize_entry_number("526")
        self.assertEqual(text, '526')
        self.assertEqual(num, 526)

    def test_non_numeric_string(self):
        text, num = build.normalize_entry_number("526-1")
        self.assertEqual(text, '526-1')
        self.assertIsNone(num)

    def test_none(self):
        text, num = build.normalize_entry_number(None)
        self.assertEqual(text, '')
        self.assertIsNone(num)


class RedactDescriptionTests(unittest.TestCase):
    def test_redacts_short_form_complaint_caption(self):
        text = 'Short Form Complaint - Brett Basanez by PLAINTIFF(S). (THOMPSON, JULIE) (Entered: 07/06/2012)'
        redacted, was_redacted = build.redact_description(text)
        self.assertTrue(was_redacted)
        self.assertNotIn('Brett Basanez', redacted)
        self.assertIn('[plaintiff name withheld]', redacted)
        # attorney/filer parenthetical stays verbatim
        self.assertIn('(THOMPSON, JULIE)', redacted)
        self.assertIn('(Entered: 07/06/2012)', redacted)

    def test_redacts_amended_short_form_complaint_without_hyphen(self):
        text = 'Short Form Complaint Charles Ray Easterling by PLAINTIFF(S). (COBEN, LARRY) (Entered: 07/11/2012)'
        redacted, was_redacted = build.redact_description(text)
        self.assertTrue(was_redacted)
        self.assertNotIn('Charles Ray Easterling', redacted)

    def test_redacts_plaintiff_decedent_reference(self):
        text = ('First MOTION for Extension on behalf of Plaintiff/Decedent Doreen L. Stepp by '
                'Plaintiff. (Brady, Steven) (Entered: 10/05/2015)')
        redacted, was_redacted = build.redact_description(text)
        self.assertTrue(was_redacted)
        self.assertNotIn('Doreen L. Stepp', redacted)

    def test_redacts_plaintiff_side_of_party_v_party_caption(self):
        text = "FIRST MEMORANDUM in Opposition. Document filed by Kathleen Ashton. (Kreindler, James) (Entered: 06/11/2004)"
        # not itself a caption pattern (no 'v.'); confirm a real caption example instead
        text2 = 'Ashton v. Al Qaeda Islamic, et al.. Document filed by Mohammed Al Faisal Al Saud. (Entered: 03/19/2004)'
        redacted, was_redacted = build.redact_description(text2)
        self.assertTrue(was_redacted)
        self.assertNotIn('Ashton', redacted)
        # defendant side of the caption is left intact
        self.assertIn('Al Qaeda', redacted)

    def test_leaves_ordinary_text_unchanged(self):
        text = 'Letter from counsel (SEEGER, CHRISTOPHER) (Entered: 12/16/2019)'
        redacted, was_redacted = build.redact_description(text)
        self.assertFalse(was_redacted)
        self.assertEqual(redacted, text)

    def test_none_and_empty(self):
        self.assertEqual(build.redact_description(None), (None, False))
        self.assertEqual(build.redact_description(''), ('', False))

    def test_description_has_unredacted_caption_true_before_redaction(self):
        text = 'Short Form Complaint - Brett Basanez by PLAINTIFF(S). (Entered: 07/06/2012)'
        self.assertTrue(build.description_has_unredacted_caption(text))

    def test_description_has_unredacted_caption_false_after_redaction(self):
        text = 'Short Form Complaint - Brett Basanez by PLAINTIFF(S). (Entered: 07/06/2012)'
        redacted, _ = build.redact_description(text)
        self.assertFalse(build.description_has_unredacted_caption(redacted))


class ComputeCappedTests(unittest.TestCase):
    def test_capped_true_on_large_gap(self):
        capped, basis = build.compute_capped(min_en=1, max_en=3138, count=2000)
        self.assertTrue(capped)
        self.assertIn('3138', basis)

    def test_capped_false_on_contiguous_range(self):
        capped, basis = build.compute_capped(min_en=1, max_en=1920, count=1920)
        self.assertFalse(capped)

    def test_capped_false_on_small_gap(self):
        # a handful of stricken/renumbered entries is not evidence of truncation
        capped, basis = build.compute_capped(min_en=0, max_en=1968, count=1967)
        self.assertFalse(capped)

    def test_capped_unknown_when_no_numeric_entries(self):
        capped, basis = build.compute_capped(min_en=None, max_en=None, count=5)
        self.assertIsNone(capped)


class RealBuildSmokeTest(unittest.TestCase):
    """Runs the actual build against the real local inputs and checks the plan's key measured numbers.
    Skips (does not fail) if the AWS release or the crosswalk supplement is not present in this environment."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        if not (build.RELEASE_DIR / 'docket_entries.jsonl.gz').is_file():
            raise unittest.SkipTest('AWS release not present in this environment')
        if not (build.CROSSWALK_DIR / 'validation.json').is_file():
            raise unittest.SkipTest('mdl_docket_crosswalk_20260919 not present in this environment')
        cls.result = build.run(cls.root)

    def test_total_rows_split(self):
        self.assertEqual(self.result['counts']['docket_entries_total'], 41946)
        self.assertEqual(self.result['counts']['docket_entries_matched_to_mdl'], 26549)
        self.assertEqual(self.result['counts']['docket_entries_unresolved'], 41946 - 26549)

    def test_distinct_mdls(self):
        self.assertEqual(self.result['counts']['distinct_mdl_numbers'], 36)

    def test_six_unique_mdl_counts(self):
        expected = {2323: 2000, 3060: 1980, 2243: 1920, 1570: 1835, 2904: 936, 2921: 764}
        got = self.result['counts']['unique_mdl_entry_counts']
        self.assertEqual(got, expected)

    def test_validation_file_written_and_passed(self):
        validation = json.loads((self.root / 'validation.json').read_text(encoding='utf-8'))
        self.assertEqual(validation['status'], 'passed')
        self.assertTrue(validation['ready'])

    def test_qualification_short_present_and_bounded(self):
        validation = json.loads((self.root / 'validation.json').read_text(encoding='utf-8'))
        self.assertIn('qualification_short', validation)
        self.assertLess(len(validation['qualification_short']), 480)
        # old truncation bug used to cut this qualification off mid-sentence at 499 chars
        self.assertGreater(len(validation['qualification']), 500)

    def test_no_natural_person_plaintiff_names_published(self):
        with open(self.root / 'entries.jsonl', encoding='utf-8') as handle:
            rows = [json.loads(line) for line in handle]
        for row in rows:
            self.assertFalse(build.description_has_unredacted_caption(row['description']),
                              msg=row['description'])
        self.assertFalse(any('Brett Basanez' in (r['description'] or '') for r in rows))
        redacted_count = sum(1 for r in rows if r.get('description_redacted'))
        self.assertGreater(redacted_count, 1500)

    def test_entries_carry_member_docket_and_cl_link_fields(self):
        with open(self.root / 'entries.jsonl', encoding='utf-8') as handle:
            rows = [json.loads(line) for line in handle]
        with_docket = sum(1 for r in rows if r.get('member_docket_number') and r.get('court_id'))
        with_cl_id = sum(1 for r in rows if r.get('cl_docket_id') is not None)
        self.assertGreater(with_docket, 20000)
        self.assertGreater(with_cl_id, 20000)


if __name__ == '__main__':
    unittest.main()
