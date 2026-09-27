"""Failing-first tests for the state_coordinated_proceedings_20260919 build (TDD).

Run: python -m unittest discover -s <this dir> -p test_build.py
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402


class ParsingUnitTests(unittest.TestCase):
    """Rules that must hold before the build even runs."""

    def test_zero_pad_mdl_number_from_registry_field(self):
        self.assertEqual(build.parse_mdl_number('16-md-02741'), 2741)
        self.assertEqual(build.parse_mdl_number('05-md-01626'), 1626)
        self.assertEqual(build.parse_mdl_number(None), None)
        self.assertEqual(build.parse_mdl_number('not-an-mdl-string'), None)

    def test_njmcl_field_parses_to_first_candidate_before_parenthetical_note(self):
        self.assertEqual(build.parse_njmcl_candidate('roundup-products'), 'roundup-products')
        self.assertEqual(
            build.parse_njmcl_candidate('pinnacle-metal-metal-mom-hip-implants (NOTE: registry may already hold a depuy ASR entry)'),
            'pinnacle-metal-metal-mom-hip-implants')
        self.assertEqual(
            build.parse_njmcl_candidate('mirena-archived (generic levonorgestrel deliberately EXCLUDED from names)'),
            'mirena-archived')

    def test_ca_date_received_century_rule(self):
        # 00-26 -> 20xx (relative to a 2026 build), 27-99 -> 19xx; malformed -> None
        self.assertEqual(build.parse_ca_date_received('12/26/17'), '2017-12-26')
        self.assertEqual(build.parse_ca_date_received('1/26/99'), '1999-01-26')
        self.assertEqual(build.parse_ca_date_received('4/2/26'), '2026-04-02')
        self.assertIsNone(build.parse_ca_date_received('8/18/201'))
        self.assertIsNone(build.parse_ca_date_received('2/4/2010'))
        self.assertIsNone(build.parse_ca_date_received(''))
        self.assertIsNone(build.parse_ca_date_received(None))

    def test_ca_title_cut_at_first_noise_token(self):
        self.assertEqual(
            build.clean_ca_title('Electric Refund Cases LASC MJ, Carolyn B. LASC PJ, J. Stephen 7/07/07 LASC LASC '
                                  'Kuhl 6/5/07 Czuleger 8/13/07 Motion for BC369141 Voluntary dismissal per good '
                                  'Wendell 6/13/08 11/24/08 06AS02501 faith settlement, 2/4/14 - ', '4512'),
            ('Electric Refund Cases', True))
        self.assertEqual(
            build.clean_ca_title('Judith Black Asbestos Cases SF PJ �Hon. Hearing 5/11/11', '4665'),
            ('Judith Black Asbestos Cases', True))

    def test_ca_title_falls_back_when_no_clean_case_name(self):
        self.assertEqual(
            build.clean_ca_title('3/27/03 �Petitioning Atty. PETITION LASC Michael Williams withdraws '
                                  'WITHDRAWN BC290196 LASC action dismissed 03A300820 Memo sent to all counsel '
                                  'a nd clerks of each court.', '4295'),
            ('JCCP 4295 (no clean case name in the source)', True))
        self.assertEqual(build.clean_ca_title('', '9999'),
                          ('JCCP 9999 (no clean case name in the source)', True))
        self.assertEqual(build.clean_ca_title(None, '9999'),
                          ('JCCP 9999 (no clean case name in the source)', True))

    def test_ca_title_strips_trailing_connector_word(self):
        self.assertEqual(build.clean_ca_title("Jacobs Farm/Del Cabo Wage and", '1'),
                          ('Jacobs Farm/Del Cabo Wage', True))

    def test_ca_title_clean_passthrough_when_no_noise(self):
        self.assertEqual(build.clean_ca_title('Vioxx Cases', '1'), ('Vioxx Cases', False))

    def test_county_fips_exact_name_match_only(self):
        crosswalk = build.load_county_fips_crosswalk()
        self.assertEqual(build.fips_for_county(crosswalk, 'NJ', 'Atlantic'), '34001')
        self.assertEqual(build.fips_for_county(crosswalk, 'CA', 'Los Angeles'), '06037')
        self.assertIsNone(build.fips_for_county(crosswalk, 'NJ', 'Not A Real County'))
        self.assertIsNone(build.fips_for_county(crosswalk, 'NJ', ''))
        self.assertIsNone(build.fips_for_county(crosswalk, 'NJ', None))


class BuildOutputTests(unittest.TestCase):
    """Runs the real build against the real (read-only) inputs and checks measured counts."""

    @classmethod
    def setUpClass(cls):
        cls.result = build.run(write=False)

    def test_nj_row_count_and_fill_rates(self):
        nj = [r for r in self.result['proceedings'] if r['type'] == 'nj_mcl']
        self.assertEqual(len(nj), 40)
        with_county = sum(1 for r in nj if r['county'] is not None)
        with_judge = sum(1 for r in nj if r['judge_name_text'] is not None)
        with_designation = sum(1 for r in nj if r['designation_date_text'] is not None)
        self.assertEqual(with_county, 30)
        self.assertEqual(with_judge, 22)
        self.assertEqual(with_designation, 21)
        distinct_counties = {r['county'] for r in nj if r['county']}
        self.assertEqual(distinct_counties, {'Atlantic', 'Bergen', 'Middlesex'})

    def test_nj_never_publishes_a_judge_entity_link(self):
        nj = [r for r in self.result['proceedings'] if r['type'] == 'nj_mcl']
        for r in nj:
            self.assertNotIn('judge_entity_id', r)
            self.assertNotIn('cl_person_id', r)

    def test_ca_row_count_and_fill_rates(self):
        ca = [r for r in self.result['proceedings'] if r['type'] == 'ca_jccp']
        self.assertEqual(len(ca), 1392)
        distinct_jccp = {r['jccp_no'] for r in ca}
        self.assertEqual(len(distinct_jccp), 1392)
        with_counties = sum(1 for r in ca if r['counties'])
        with_case_numbers = sum(1 for r in ca if r['case_numbers'])
        self.assertEqual(with_counties, 1258)
        self.assertEqual(with_case_numbers, 1037)
        masstort = sum(1 for r in ca if r['category'] == 'masstort_candidate')
        self.assertEqual(masstort, 89)
        distinct_counties = {c for r in ca for c in r['counties']}
        self.assertEqual(len(distinct_counties), 58)

    def test_ca_judges_field_is_dropped_entirely(self):
        ca = [r for r in self.result['proceedings'] if r['type'] == 'ca_jccp']
        for r in ca:
            self.assertNotIn('judges', r)
            self.assertNotIn('judge_name_text', r)

    def test_ca_log_field_used_for_era_split_not_derived_date(self):
        ca = [r for r in self.result['proceedings'] if r['type'] == 'ca_jccp']
        older = sum(1 for r in ca if r['log'] == '2017-and-older')
        present = sum(1 for r in ca if r['log'] == '2018-present')
        self.assertEqual(older, 870)
        self.assertEqual(present, 522)

    def test_ca_malformed_dates_are_unresolved_not_guessed(self):
        ca_by_id = {r['jccp_no']: r for r in self.result['proceedings'] if r['type'] == 'ca_jccp'}
        for jccp_no in ('4638', '5480', '5351', '4616', '5035'):
            self.assertIsNone(ca_by_id[jccp_no]['date_received_iso'])
        reasons = [u['reason'] for u in self.result['unresolved'] if u.get('id', '').startswith('jccp:')]
        self.assertTrue(any('date_received' in r for r in reasons))

    def test_mdl_links_named_by_the_source_only(self):
        edges = self.result['edges']
        mdl_edges = [e for e in edges if e['relation'] == 'names_federal_mdl']
        self.assertEqual(len(mdl_edges), 13)
        distinct_numbers = {e['to']['id'] for e in mdl_edges}
        self.assertEqual(len(distinct_numbers), 13)
        resolved = [e for e in mdl_edges if e.get('registry_status') == 'pending']
        self.assertEqual(len(resolved), 7)
        resolved_numbers = {e['to']['id'] for e in resolved}
        self.assertEqual(resolved_numbers, {2741, 2738, 2740, 2848, 2782, 2768, 2921})

    def test_county_fips_join_is_exact_name_match_only(self):
        nj = [r for r in self.result['proceedings'] if r['type'] == 'nj_mcl']
        for r in nj:
            if r['county']:
                self.assertIsNotNone(r['county_fips'])
                self.assertEqual(len(r['county_fips']), 5)
            else:
                self.assertIsNone(r['county_fips'])

    def test_ca_title_is_cleaned_and_raw_is_preserved(self):
        ca_by_id = {r['jccp_no']: r for r in self.result['proceedings'] if r['type'] == 'ca_jccp'}
        noise_re = re.compile(r'\bLASC\b|\bOCSC\b|\bSF PJ\b|\bSac PJ\b|\bHon\.|\d{1,2}/\d{1,2}/\d{2,4}')
        row = ca_by_id['4512']
        self.assertEqual(row['title'], 'Electric Refund Cases')
        self.assertTrue(row['title_is_extraction_noise'])
        self.assertTrue(row['title_raw'].startswith('Electric Refund Cases LASC'))
        row = ca_by_id['4295']
        self.assertEqual(row['title'], 'JCCP 4295 (no clean case name in the source)')
        self.assertTrue(row['title_is_extraction_noise'])
        for r in self.result['proceedings']:
            if r['type'] != 'ca_jccp':
                continue
            self.assertIsNone(noise_re.search(r['title']))
            self.assertIn('title_raw', r)
            self.assertIn('title_is_extraction_noise', r)

    def test_no_null_is_ever_inferred(self):
        nj = [r for r in self.result['proceedings'] if r['type'] == 'nj_mcl']
        for r in nj:
            if r['county'] is None:
                self.assertIsNone(r['county_fips'])


if __name__ == '__main__':
    unittest.main()
