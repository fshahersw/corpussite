"""Unit tests for the classifier/join/labelling rules in build.py, plus one real-build assertion.
Run: python -m unittest discover -s <this dir> -p test_build.py
"""
import json
import unittest
from pathlib import Path

import build as b

HERE = Path(__file__).resolve().parent


class RoleNormalization(unittest.TestCase):
    def test_all_9_raw_spellings_normalize(self):
        cases = {
            'attorney_to_be_noticed': 'Attorney to be noticed',
            'ATTORNEY TO BE NOTICED': 'Attorney to be noticed',
            'LEAD ATTORNEY': 'Lead attorney',
            'lead_attorney': 'Lead attorney',
            'PRO HAC VICE': 'Pro hac vice',
            'terminated': 'Terminated',
            '10': 'Unknown',
            'TERMINATED: 06/29/2010': 'Terminated',
            'unknown': 'Unknown',
        }
        for raw, expected in cases.items():
            normalized, _ = b.normalize_role(raw)
            self.assertEqual(normalized, expected, raw)

    def test_code_10_merges_with_literal_unknown(self):
        self.assertEqual(b.normalize_role('10')[0], b.normalize_role('unknown')[0])

    def test_terminated_date_is_parsed_out_separately(self):
        normalized, terminated_date = b.normalize_role('TERMINATED: 06/29/2010')
        self.assertEqual(normalized, 'Terminated')
        self.assertEqual(terminated_date, '06/29/2010')
        # a plain 'terminated' row has no date to extract
        self.assertIsNone(b.normalize_role('terminated')[1])

    def test_unrecognized_role_falls_back_to_unknown_not_a_crash(self):
        self.assertEqual(b.normalize_role('something-new')[0], 'Unknown')


class OrganizationHeuristic(unittest.TestCase):
    def test_measured_organization_defendants_are_detected(self):
        self.assertTrue(b.is_organization('Bayer Healthcare Pharmaceuticals, Inc.'))
        self.assertTrue(b.is_organization('Bayer Healthcare, LLC'))

    def test_natural_person_names_are_not_organizations(self):
        for name in ('Joseph Lanni', 'MICHAEL ROFF ADAMS', 'Nathaniel Cole Jr', 'Marisol  Barreto'):
            self.assertFalse(b.is_organization(name), name)

    def test_estate_wrapper_overrides_any_org_looking_token(self):
        # a person wrapped in a representative-capacity phrase is never an organisation, even if a
        # coincidental org-like token appeared in the text.
        self.assertFalse(b.is_organization('JOHN HUDSON as Anticipated Personal Representative for the Estate of HELENA HUDSON'))
        self.assertFalse(b.is_organization('Estate of Jane Doe, Inc. Building manager'))

    def test_conservative_default_is_person_when_unsure(self):
        self.assertFalse(b.is_organization('Smith Family Revocable Something'))


class PartyListability(unittest.TestCase):
    def test_organization_is_listable(self):
        listable, category = b.party_listable('Bayer Healthcare, LLC', ['Counter Claimant', 'Defendant'])
        self.assertTrue(listable)
        self.assertEqual(category, 'organization')

    def test_named_defendant_natural_person_is_listable(self):
        listable, category = b.party_listable('Jane Smith', ['Defendant'])
        self.assertTrue(listable)
        self.assertEqual(category, 'named_defendant')

    def test_natural_person_plaintiff_is_count_only(self):
        listable, category = b.party_listable('Joseph Lanni', ['Plaintiff'])
        self.assertFalse(listable)
        self.assertEqual(category, 'natural_person_count_only')

    def test_consol_plaintiff_natural_person_is_count_only(self):
        listable, category = b.party_listable('Stephanie R. Heinzatz', ['Consol Plaintiff'])
        self.assertFalse(listable)
        self.assertEqual(category, 'natural_person_count_only')


class CrosswalkExtension(unittest.TestCase):
    def test_direct_and_extended_maps_differ_and_extended_is_a_superset(self):
        direct, extended, used = b.load_crosswalk_extension()
        self.assertGreater(used, 0)
        self.assertTrue(set(direct.items()) <= set(extended.items()))
        self.assertGreater(len(extended), len(direct))


class AttorneyNameNormalization(unittest.TestCase):
    def test_normalize_attorney_name_strips_case_punctuation_and_spacing(self):
        self.assertEqual(b.normalize_attorney_name('Christopher A Seeger'), b.normalize_attorney_name('CHRISTOPHER A. SEEGER'))
        self.assertEqual(b.normalize_attorney_name('Christopher A Seeger'), b.normalize_attorney_name('Christopher A. Seeger'))
        self.assertNotEqual(b.normalize_attorney_name('Jane Doe'), b.normalize_attorney_name('John Doe'))


class MemberOfMdlParentUnresolvable(unittest.TestCase):
    def test_load_member_relationships_flags_parent_not_in_crosswalk(self):
        direct_map, extended_map, _used = b.load_crosswalk_extension()
        if not b.AWS_RELEASE.exists():
            self.skipTest('AWS release not present in this environment')
        bad_from = b.load_member_of_mdl_parent_unresolvable(direct_map)
        # measured: 13 member matters whose parent is not in the 59-matter crosswalk
        self.assertEqual(len(bad_from), 13)
        for member_id, parent_id in bad_from.items():
            self.assertNotIn(parent_id, direct_map)


class RealBuild(unittest.TestCase):
    """One real-data assertion: run the actual build and check the plan's headline counts, skipped if the
    AWS release is not reachable in this environment."""

    def test_real_build_matches_plan_counts(self):
        if not b.AWS_RELEASE.exists():
            self.skipTest('AWS release not present in this environment')
        result = b.build()
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['ready'])
        counts = result['counts']
        self.assertEqual(counts['appearances_total'], 878)
        self.assertEqual(counts['attorney_records_total'], 181)
        self.assertEqual(counts['attorney_records_distinct_normalized_names'], 74)
        self.assertEqual(counts['firms_total'], 12)
        self.assertEqual(counts['parties_total'], 503)
        self.assertEqual(counts['appearances_direct_mdl_linked'], 192)
        self.assertEqual(counts['appearances_extended_mdl_linked'], 607)
        self.assertEqual(counts['parties_direct_mdl_linked'], 67)
        self.assertEqual(counts['parties_extended_mdl_linked'], 369)
        self.assertEqual(counts['mdls_reached_direct'], 3)
        self.assertEqual(counts['mdls_reached_extended'], 14)
        self.assertEqual(counts['role_raw_spelling_count_distinct'], 9)
        self.assertEqual(counts['unresolved_member_of_mdl_parent_unresolvable']['appearances'], 61)
        self.assertEqual(counts['unresolved_member_of_mdl_parent_unresolvable']['parties'], 19)
        firm_counts = counts['appearance_firm_counts']
        self.assertEqual(firm_counts['seeger_weiss'], 478)
        self.assertEqual(firm_counts['weitz_luxenberg'], 141)
        self.assertNotIn('attorneys_total', counts)
        self.assertLess(len(result['qualification_short']), 500)
        # unresolved rows for the flagged matters must carry the distinct, more specific reason
        unresolved = b.read_jsonl(b.OUT / 'unresolved.jsonl')
        specific = [u for u in unresolved if u['reason'].startswith('member_of_mdl parent matter')]
        self.assertEqual(sum(1 for u in specific if u['type'] == 'counsel_appearance'), 61)
        self.assertEqual(sum(1 for u in specific if u['type'] == 'party'), 19)
        for u in specific:
            self.assertIn('evidence', u)
            self.assertIn('parent_matter_id', u['evidence'])


if __name__ == '__main__':
    unittest.main()
