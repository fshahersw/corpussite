"""Unit tests for the parsing/classification rules in build.py, using real sample rows from the staging inputs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402


class TestParseCongressNumber(unittest.TestCase):
    def test_standard_public_law_id(self):
        # real row: PLAW-113publ1, document_number "1", congress 113
        congress, number = build.parse_congress_number('PLAW-113publ1', '1', 113)
        self.assertEqual(congress, 113)
        self.assertEqual(number, '1')

    def test_two_digit_law_number(self):
        congress, number = build.parse_congress_number('PLAW-113publ10', '10', 113)
        self.assertEqual((congress, number), (113, '10'))

    def test_private_law_id(self):
        congress, number = build.parse_congress_number('PLAW-113priv2', '2', 113)
        self.assertEqual((congress, number), (113, '2'))

    def test_falls_back_to_metadata_when_id_irregular(self):
        congress, number = build.parse_congress_number('not-a-plaw-id', '', 118)
        self.assertEqual(congress, 118)
        self.assertEqual(number, '')

    def test_missing_congress_and_bad_id_yields_none(self):
        congress, number = build.parse_congress_number('garbage', None, None)
        self.assertIsNone(congress)
        self.assertEqual(number, '')


class TestClassifyAction(unittest.TestCase):
    def test_amends_is_normalized(self):
        self.assertEqual(build.classify_action('amends'), 'amends')

    def test_reference_only_passthrough(self):
        self.assertEqual(build.classify_action('reference_only'), 'reference_only')

    def test_case_and_whitespace_normalized(self):
        self.assertEqual(build.classify_action('  Amends  '), 'amends')

    def test_missing_value_becomes_unclassified(self):
        self.assertEqual(build.classify_action(None), 'unclassified')
        self.assertEqual(build.classify_action(''), 'unclassified')


class TestActionTypeSet(unittest.TestCase):
    def test_amends_is_an_action(self):
        self.assertIn('amends', build.ACTION_TYPES)

    def test_reference_only_is_not_an_action(self):
        self.assertNotIn('reference_only', build.ACTION_TYPES)

    def test_adds_repeals_redesignates_transfers_are_actions(self):
        for action in ('adds', 'repeals', 'redesignates', 'transfers', 'appropriates', 'mixed_direct_actions'):
            self.assertIn(action, build.ACTION_TYPES)

    def test_mixed_contextual_actions_is_not_a_direct_action(self):
        self.assertNotIn('mixed_contextual_actions', build.ACTION_TYPES)


class TestLawIdFor(unittest.TestCase):
    def test_stable_and_stripped(self):
        self.assertEqual(build.law_id_for(' PLAW-113publ1 '), 'PLAW-113publ1')

    def test_empty_input(self):
        self.assertEqual(build.law_id_for(None), '')


if __name__ == '__main__':
    unittest.main()
