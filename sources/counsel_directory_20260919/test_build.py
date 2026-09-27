"""Tests-first (TDD) for build.py's deterministic classifier/normalisation rules.

Every string used below was copied verbatim from the real inputs (SW-BULK catalog/parties_by_docket,
catalog/attorneys.json, catalog/firms.json, catalog/parties_report.csv, the AWS release b2b via
mdl_counsel_appearances_20260919, mdl_counsel_20260919, and the Philadelphia Mass Tort liaison-counsel
PDF text) so the rules are proven against the messy data they must actually handle, not invented cases.
Run: python -m unittest discover -s <this dir> -p test_build.py
"""
import unittest

import build as b


class FirmKeyGroupingTests(unittest.TestCase):
    """Real printed variants that MUST collapse to the same deterministic key."""

    def test_seeger_weiss_variants(self):
        keys = {b.firm_key('SEEGER WEISS LLP'), b.firm_key('Seeger Weiss LLP (Newark)'),
                b.firm_key('Seeger Weiss, LLP'), b.firm_key('Seeger Weiss LLP')}
        self.assertEqual(keys, {'seeger weiss'})

    def test_mccarter_english_ampersand_and_punctuation(self):
        self.assertEqual(b.firm_key('MCCARTER & ENGLISH, LLP'), b.firm_key('MCCARTER & ENGLISH LLP'))
        self.assertEqual(b.firm_key('MCCARTER & ENGLISH LLP'), 'mccarter and english')

    def test_fox_galvin_comma_and_suffix(self):
        self.assertEqual(b.firm_key('Fox Galvin, LLC'), b.firm_key('FOX GALVIN LLC'))
        self.assertEqual(b.firm_key('FOX GALVIN LLC'), 'fox galvin')

    def test_motley_rice_llp_vs_llc(self):
        self.assertEqual(b.firm_key('MOTLEY RICE LLP'), b.firm_key('MOTLEY RICE LLC'))

    def test_boehl_stopher_comma_placement(self):
        # 'Boehl Stopher & Graves, LLP' vs 'Boehl, Stopher & Graves' (parties_by_docket odd-firm sample)
        self.assertEqual(b.firm_key('Boehl Stopher & Graves, LLP'), b.firm_key('Boehl, Stopher & Graves'))
        self.assertEqual(b.firm_key('Boehl, Stopher & Graves'), 'boehl stopher and graves')

    def test_bryan_cave_comma_variant(self):
        self.assertEqual(b.firm_key('Bryan Cave LLP'), b.firm_key('Bryan Cave, LLP'))

    def test_beasley_allen_ampersand_vs_and_and_suffix(self):
        keys = {b.firm_key('BEASLEY ALLEN CROW METHVIN PORTIS & MILES, LLC'),
                b.firm_key('BEASLEY ALLEN CROW METHVIN PORTIS AND MILES LLC'),
                b.firm_key('BEASLEY ALLEN CROW METHVIN PORTIS & MILES, PC'),
                b.firm_key('BEASLEY ALLEN CROW METHVIN PORTIS AND MILES, PC')}
        self.assertEqual(keys, {'beasley allen crow methvin portis and miles'})

    def test_dla_piper_trailing_parenthetical_jurisdiction(self):
        self.assertEqual(b.firm_key('DLA Piper LLP (US)'), 'dla piper')

    def test_faegre_and_wooden_trailing_parenthetical_city(self):
        self.assertEqual(b.firm_key('FAEGRE BAKER DANIELS LLP (INDIANAPOLIS)'), 'faegre baker daniels')
        self.assertEqual(b.firm_key('WOODEN & MCLAUGHLIN LLP (INDIANAPOLIS)'), 'wooden and mclaughlin')

    def test_et_al_suffix_stripped(self):
        self.assertEqual(b.firm_key('PAUL WEISS RIFKIND ET AL'), 'paul weiss rifkind')
        self.assertEqual(b.firm_key('BARTLIT, BECK ET AL'), 'bartlit beck')
        self.assertEqual(b.firm_key('BURG SIMPSON ELDRIDGE ET AL'), 'burg simpson eldridge')
        self.assertEqual(b.firm_key('SCHLICHTER, BOGARD ET AL'), 'schlichter bogard')

    def test_dotted_llp_abbreviation_still_strips(self):
        # 'L.L.P.' must reduce to the same suffix as 'LLP', not fragment into single letters.
        self.assertEqual(b.firm_key('Fulbright & Jaworski L.L.P.'), b.firm_key('Fulbright & Jaworski'))
        self.assertEqual(b.firm_key('Fulbright & Jaworski'), 'fulbright and jaworski')

    def test_covington_and_burling_ampersand_variant(self):
        self.assertEqual(b.firm_key('COVINGTON AND BURLING LLP'), b.firm_key('COVINGTON & BURLING LLP'))

    def test_phila_seeger_weiss_matches_other_sources(self):
        # Philadelphia PDF prints 'Seeger Weiss, LLP' -- must land in the same group as the docket data.
        self.assertEqual(b.firm_key('Seeger Weiss, LLP'), b.firm_key('SEEGER WEISS LLP'))

    def test_phila_motley_rice_variants(self):
        self.assertEqual(b.firm_key('Motley Rice, LLC'), b.firm_key('MOTLEY RICE LLP'))


class FirmKeyNeverFuzzyMatchTests(unittest.TestCase):
    """Real near-miss strings that must NOT be grouped: typos and genuinely different names stay apart."""

    def test_typo_bois_vs_boies_stays_separate(self):
        self.assertNotEqual(b.firm_key('BOIS SCHILLER FLEXNER LLP'), b.firm_key('BOIES SCHILLER & FLEXNER LLP'))

    def test_typo_covingington_stays_separate(self):
        self.assertNotEqual(b.firm_key('COVINGINTON & BURLING'), b.firm_key('COVINGTON & BURLING'))

    def test_typo_jaorski_stays_separate(self):
        self.assertNotEqual(b.firm_key('Fulbright & Jaorski, L.L.P.'), b.firm_key('Fulbright & Jaworski'))

    def test_firm_rename_levin_papantonio_stays_separate(self):
        # Firm added a partner name over time; the two strings are genuinely different, not punctuation noise.
        self.assertNotEqual(b.firm_key('LEVIN PAPANTONIO'), b.firm_key('LEVIN PAPANTONIO RAFFERTY'))

    def test_different_partner_stays_separate(self):
        self.assertNotEqual(b.firm_key('Walraven & Lehman LLP'), b.firm_key('Walraven and Westerfeld LLP'))

    def test_et_al_truncation_does_not_reconstitute_full_name(self):
        # A truncated 'et al' form never re-merges with the full spelled-out name it stands for.
        self.assertNotEqual(b.firm_key('BARTLIT, BECK ET AL'), b.firm_key('Bartlit Beck Herman Palenchar & Scott'))


class NotAFirmTests(unittest.TestCase):
    """Real strings from catalog/parties_by_docket and catalog/attorneys.json that are not firms."""

    def test_street_addresses(self):
        for s in ('333 Main Street', '1102 Forest Avenue', '16 Waldo Avenue', '2300 W. Sahara Avenue',
                  '2438 S 2500 E', '425 W. Capitol Avenue', '4504 Savannah Holly Place',
                  '6660 NW Monticello Drive', '9444 SW 69th Ct.'):
            self.assertEqual(b.classify_not_a_firm(s), 'address', msg=s)

    def test_po_box(self):
        self.assertEqual(b.classify_not_a_firm('PO Box 1231'), 'address')
        self.assertEqual(b.classify_not_a_firm('PO Box 3268'), 'address')

    def test_pro_se(self):
        self.assertEqual(b.classify_not_a_firm('PRO SE'), 'pro_se')
        self.assertEqual(b.classify_not_a_firm('Pro Se'), 'pro_se')

    def test_bar_admission_note(self):
        self.assertEqual(b.classify_not_a_firm('COUNSEL NOT ADMITTED TO USDC-NJ BAR'), 'bar_admission_note')

    def test_data_artifact(self):
        self.assertEqual(b.classify_not_a_firm('UNDELIVERABLE EMAIL 4/10/2019'), 'data_artifact')
        self.assertEqual(b.classify_not_a_firm('ADDRESS EXPIRED / UNKNOWN'), 'data_artifact')

    def test_email_address(self):
        self.assertEqual(b.classify_not_a_firm('Email: adam.perlman@lw.com'), 'email_address')
        self.assertEqual(b.classify_not_a_firm('Email: asha.spencer@bartlit-beck.com'), 'email_address')

    def test_registered_agent_notation(self):
        self.assertEqual(b.classify_not_a_firm('c/o Lawyers Incorporating'), 'registered_agent_notation')

    def test_person_name_no_firm_words(self):
        self.assertEqual(b.classify_not_a_firm('PRENTISS W. HALLENBECK, JR'), 'person_name_no_firm_words')
        self.assertEqual(b.classify_not_a_firm('Prentiss W. Hallenbeck, Jr.'), 'person_name_no_firm_words')

    def test_real_firms_are_not_rejected(self):
        # Legitimate short/no-suffix firm names must NOT be caught by the person-name heuristic.
        for s in ('DLA Piper', 'Jones Day', 'Sidley Austin', 'Ice Miller', 'Beasley Allen', 'Bartlit Beck',
                  'McKool Smith', 'Smith Anderson', 'Paul Hastings', 'Dykema Gossett', 'Allen Matkins',
                  'Isaac Zaur', 'SEEGER WEISS LLP', 'Fish & Richardson P.C.', "Glazer's Distributors"):
            self.assertIsNone(b.classify_not_a_firm(s), msg=s)


class OrganizationPartyTests(unittest.TestCase):
    """Real party 'name' strings from catalog/parties_by_docket. Conservative: unsure defaults to person."""

    def test_natural_persons_are_not_organizations(self):
        for s in ('Roy Allen', 'DESSIE ALEXANDER', 'Sharon Beck', 'Dion Ambrogio', 'Joyce Deschaine',
                  'Philip A Deschaine, Jr.', 'Richard S. Sackler'):
            self.assertFalse(b.is_organization(s), msg=s)

    def test_named_defendant_corporations_are_organizations(self):
        for s in ('Pharmacia & Upjohn Company Inc.', 'Pfizer Inc.', 'FOREST RESEARCH INSTITUTE, INC.',
                  'Janssen Research & Development LLC', 'Bayer AG', 'Johnson & Johnson Company',
                  'AmerisourceBergen Corporation', 'SpecGX LLC', 'Mallinckrodt PLC', '3M COMPANY'):
            self.assertTrue(b.is_organization(s), msg=s)

    def test_government_entities_are_organizations(self):
        self.assertTrue(b.is_organization('Cannon County, Tennessee'))
        self.assertTrue(b.is_organization('City of Brundidge, Alabama'))
        self.assertTrue(b.is_organization('Ashland, Alabama, City of'))

    def test_richard_sackler_named_individual_defendant_is_never_listed(self):
        # A natural person keeps being a natural person even as a named Defendant (side is irrelevant).
        self.assertFalse(b.is_organization('Richard S. Sackler'))

    def test_wrapper_phrase_individually_does_not_override_a_real_corporate_name(self):
        # Real example: 'individually' here is a capacity qualifier on an unambiguous corporate name.
        self.assertTrue(b.is_organization(
            'The Chemours Company FC, LLC, individually and as successor in interest to DuPont Chemical Solutions Enterprise'))
        self.assertTrue(b.is_organization('Clariant Corporation, individually and as successor in interest to Sandoz Chemical Corporation'))

    def test_natural_person_with_capacity_wrapper_stays_a_person(self):
        # Real example: no organisation token anywhere -> stays a person (counted only).
        self.assertFalse(b.is_organization('Amber Herrera,  individually and as parent and next friend to minor Plaintiff B.H.G.'))


class RoleNormalizationTests(unittest.TestCase):
    """Real raw role spellings measured across catalog/parties_by_docket and the AWS release layer."""

    def test_known_spellings(self):
        self.assertEqual(b.normalize_role('attorney_to_be_noticed'), 'Attorney to be noticed')
        self.assertEqual(b.normalize_role('ATTORNEY TO BE NOTICED'), 'Attorney to be noticed')
        self.assertEqual(b.normalize_role('lead_attorney'), 'Lead attorney')
        self.assertEqual(b.normalize_role('LEAD ATTORNEY'), 'Lead attorney')
        self.assertEqual(b.normalize_role('inactive'), 'Inactive')
        self.assertEqual(b.normalize_role('PRO HAC VICE'), 'Pro hac vice')

    def test_courtlistener_role_code_10_is_unknown(self):
        self.assertEqual(b.normalize_role(10), 'Unknown')
        self.assertEqual(b.normalize_role('10'), 'Unknown')
        self.assertEqual(b.normalize_role('unknown'), 'Unknown')

    def test_terminated_with_and_without_date(self):
        self.assertEqual(b.normalize_role('terminated'), 'Terminated')
        self.assertEqual(b.normalize_role('TERMINATED: 06/29/2010'), 'Terminated')

    def test_not_stated(self):
        self.assertEqual(b.normalize_role(None), 'Not stated')
        self.assertEqual(b.normalize_role(''), 'Not stated')

    def test_full_courtlistener_role_code_table(self):
        # Real: sources/mdl_counsel_20260919 attorney records carry role_code as a bare integer 1-9
        # (role dicts), cross-checked against that source's own coverage.json role_labels table.
        self.assertEqual(b.normalize_role(1), 'Attorney to be noticed')
        self.assertEqual(b.normalize_role(2), 'Lead attorney')
        self.assertEqual(b.normalize_role(3), 'Attorney in sealed group')
        self.assertEqual(b.normalize_role(4), 'Pro hac vice')
        self.assertEqual(b.normalize_role(5), 'Self-terminated')
        self.assertEqual(b.normalize_role(6), 'Terminated')
        self.assertEqual(b.normalize_role(7), 'Suspended')
        self.assertEqual(b.normalize_role(8), 'Inactive')
        self.assertEqual(b.normalize_role(9), 'Disbarred')


class PublicCaseNameTests(unittest.TestCase):
    """Real docket case_name strings from catalog/parties_by_docket and catalog/masters.json."""

    def test_in_re_master_captions_are_safe_to_publish(self):
        text, suppressed, _ = b.public_case_name('IN RE: COOK MEDICAL INC.')
        self.assertFalse(suppressed)
        self.assertEqual(text, 'IN RE: COOK MEDICAL INC.')

    def test_person_v_company_is_suppressed(self):
        text, suppressed, _ = b.public_case_name('Allen v. Pfizer Inc.')
        self.assertTrue(suppressed)
        self.assertIsNone(text)

    def test_government_plaintiff_v_company_is_published(self):
        text, suppressed, _ = b.public_case_name('Cannon County, Tennessee v. Purdue Pharma L.P.')
        self.assertFalse(suppressed)
        self.assertEqual(text, 'Cannon County, Tennessee v. Purdue Pharma L.P.')

    def test_bare_person_list_is_suppressed(self):
        text, suppressed, _ = b.public_case_name('Alphonso Burton and Sandra Burton')
        self.assertTrue(suppressed)
        self.assertIsNone(text)

    def test_none_case_name(self):
        text, suppressed, _ = b.public_case_name(None)
        self.assertTrue(suppressed)
        self.assertIsNone(text)


class PhilaLiaisonParseTests(unittest.TestCase):
    """Real markdown fragments copied from
    returnedfiles/www.courts.phila.gov_pdf_cpcivil_Mass-Tort-Docket-and-Liaison-Counsel-List.pdf.json."""

    def test_heading_plus_body_asbestos_fragment(self):
        md = (
            "## (1) ASBESTOS (T1):\n\n"
            "## 861000001\n\n"
            "## Plaintiff Liaison: Larry Brown\n\n"
            "Plaintiff Liaison: **Larry Brown** (215) 569-4000\n"
            "Brookman, Rosenberg, Brown & Sandler (215) 569-2222 fax\n"
            "One Penn Square West, 30 South 15 Street, 17 Floor\n"
            "Philadelphia, PA 19102\n"
            "**Email:** [lbrown@brbs.com](mailto:lbrown@brbs.com)\n\n"
            "## Defense Liaison: Catherine Jasons\n\n"
            "(215) 854-0658\n"
            "Kelley, Jasons, McGowan, Spinelli, Hanna & Reber, LLP (215) 854-8434 fax\n"
            "Two Liberty Place, Suite 1900\n"
        )
        rows = b.parse_phila_liaison(md)
        by_name = {r['person_name']: r for r in rows}
        self.assertIn('Larry Brown', by_name)
        self.assertEqual(by_name['Larry Brown']['program_number'], 1)
        self.assertEqual(by_name['Larry Brown']['side'], 'plaintiff')
        self.assertEqual(by_name['Larry Brown']['firm_raw'], 'Brookman, Rosenberg, Brown & Sandler')
        self.assertIn('Catherine Jasons', by_name)
        self.assertEqual(by_name['Catherine Jasons']['side'], 'defendant')
        self.assertIn('Kelley, Jasons, McGowan, Spinelli, Hanna & Reber, LLP', by_name['Catherine Jasons']['firm_raw'])

    def test_table_row_elmiron_fragment(self):
        md = (
            "## (2) ELMIRON (XQ):\n\n"
            "## 220900119\n\n"
            "| Plaintiff Liaison: | Rosemary Pinto | (215) 546-2604 |\n"
            "| --- | --- | --- |\n"
            "| Feldman & Pinto |  | (267) 744-4475 fax |\n"
            "| 30 South 15th Street, 15th Floor |  |  |\n"
        )
        rows = b.parse_phila_liaison(md)
        by_name = {r['person_name']: r for r in rows}
        self.assertIn('Rosemary Pinto', by_name)
        self.assertEqual(by_name['Rosemary Pinto']['firm_raw'], 'Feldman & Pinto')
        self.assertEqual(by_name['Rosemary Pinto']['side'], 'plaintiff')

    def test_run_on_merged_line_after_email_link(self):
        md = (
            "## (2) ELMIRON (XQ):\n\n"
            "**Email:** [rpinto@feldmanpinto.com](mailto:rpinto@feldmanpinto.com)Plaintiffs\u2019 Co-Liaison: **Tobias L. Millrood** (215) 772-1000\n"
            "Kline & Specter, P.C. (215) 772-1359 fax\n"
            "1525 Locust Street\n"
        )
        rows = b.parse_phila_liaison(md)
        by_name = {r['person_name']: r for r in rows}
        self.assertIn('Tobias L. Millrood', by_name)
        self.assertEqual(by_name['Tobias L. Millrood']['firm_raw'], 'Kline & Specter, P.C.')
        # the email text must never leak into any field
        for row in rows:
            for value in row.values():
                if isinstance(value, str):
                    self.assertNotIn('@', value)

    def test_heading_only_role_then_separate_name_line(self):
        md = (
            "## (7) ZANTAC (XO):\n\n"
            "## Retailer Defendants\u2019 Co-Liaison Counsel\n\n"
            "**Brian M. Lands**\n"
            "SHOOK, HARDY & BACON L.L.P.\n"
            "2001 Market Street, Suite 3000\n"
            "Philadelphia, PA 19103\n"
            "Telephone: (215) 575-3112\n"
            "[blands@shb.com](mailto:blands@shb.com)\n\n"
            "_Attorney for Walmart, Inc._\n"
        )
        rows = b.parse_phila_liaison(md)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['person_name'], 'Brian M. Lands')
        self.assertEqual(rows[0]['firm_raw'], 'SHOOK, HARDY & BACON L.L.P.')
        self.assertEqual(rows[0]['side'], 'defendant')

    def test_discovery_master_table_is_not_a_liaison(self):
        md = (
            "## (6) VENA CAVA FILTER (XV):\n\n"
            "| VENA CAVA FILTER--DISCOVERY MASTER: |  |\n"
            "| --- | --- |\n"
            "| The Hon. Mark I. Bernstein | (267)324-6773or |\n"
        )
        rows = b.parse_phila_liaison(md)
        self.assertEqual(rows, [])

    def test_no_contact_details_ever_leak(self):
        # Defence-in-depth: whatever is extracted from the whole real markdown never carries an
        # e-mail address or a phone number in any field.
        import json
        with open('C:/Users/firas/Downloads/returnedfiles/'
                   'www.courts.phila.gov_pdf_cpcivil_Mass-Tort-Docket-and-Liaison-Counsel-List.pdf.json',
                   encoding='utf-8') as handle:
            doc = json.load(handle)
        rows = b.parse_phila_liaison(doc['markdown'])
        self.assertGreater(len(rows), 5)
        for row in rows:
            for value in row.values():
                if isinstance(value, str):
                    self.assertNotIn('@', value)
                    self.assertNotRegex(value, r'\(\d{3}\)\s*\d{3}[\s.-]?\d{4}')

    def test_firm_name_that_looks_like_a_person_name_is_not_skipped(self):
        # Real Paraquat (program 4) fragment: the firm lines 'Wagstaff Law Firm' / 'Motley Rice LLC' /
        # 'The Miller Firm LLC' are shaped just like a person's name (2-3 title-case words) and must still
        # be captured as the firm, not skipped past into the following city/state/zip line.
        md = (
            "## (4) PARAQUAT (XN)\n\n"
            "## Plaintiffs’ Co-Lead Counsel : Aimee Wagstaff\n\n"
            "Plaintiffs’ Co-Lead Counsel **: Aimee Wagstaff**\n"
            "Wagstaff Law Firm\n"
            "940 N. Lincoln Street\n"
            "Denver, CO 80203\n"
            "Email: [awagstaff@wagstafflawfirm.com](mailto:awagstaff@wagstafflawfirm.com)\n\n"
            "## Plaintiffs’ Co-Lead Counsel: Fidelma Fitzpatrick\n\n"
            "Motley Rice LLC\n"
            "55 Cedar Street\n"
            "Providence, RI 02903\n"
            "Email: [ffitzpatrick@motleyrice.com](mailto:ffitzpatrick@motleyrice.com)\n\n"
            "## Defense Co-Liaison Counsel: Barry H. Boise\n\n"
            "Defense Co-Liaison Counsel: **Barry H. Boise**\n"
            "Troutman Pepper\n"
            "3000 Two Logan Square\n"
            "Eighteenth and Arch Streets\n"
            "Philadelphia, PA 19103\n"
            "Email: [Barry.Boise@Troutman.com](mailto:Barry.Boise@Troutman.com)\n"
        )
        rows = b.parse_phila_liaison(md)
        by_name = {r['person_name']: r['firm_raw'] for r in rows}
        self.assertEqual(by_name.get('Aimee Wagstaff'), 'Wagstaff Law Firm')
        self.assertEqual(by_name.get('Fidelma Fitzpatrick'), 'Motley Rice LLC')
        self.assertEqual(by_name.get('Barry H. Boise'), 'Troutman Pepper')

    def test_real_file_finds_larry_brown_asbestos(self):
        import json
        with open('C:/Users/firas/Downloads/returnedfiles/'
                   'www.courts.phila.gov_pdf_cpcivil_Mass-Tort-Docket-and-Liaison-Counsel-List.pdf.json',
                   encoding='utf-8') as handle:
            doc = json.load(handle)
        rows = b.parse_phila_liaison(doc['markdown'])
        larry = [r for r in rows if r['person_name'] == 'Larry Brown']
        self.assertEqual(len(larry), 1)
        self.assertEqual(larry[0]['program_name'], 'ASBESTOS')
        self.assertEqual(larry[0]['firm_raw'], 'Brookman, Rosenberg, Brown & Sandler')


if __name__ == '__main__':
    unittest.main()
