"""Tests-first (TDD) for build.py: amount parsing, amount banding, caption redaction (privacy-critical),
MDL linking and id generation. Written against real captions pulled from the input dataset so the
redaction rule is checked against the actual data it will run over, not only invented examples.

Run: C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe -m unittest discover -s <this dir> -p test_build.py
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import build


class ParseAmountTests(unittest.TestCase):
    def test_clean_dollar_amount(self):
        self.assertEqual(build.parse_amount_usd('$5,557,465,180.00'), 5557465180.00)

    def test_amount_with_cents(self):
        self.assertEqual(build.parse_amount_usd('$4,707,259,944.64'), 4707259944.64)

    def test_small_amount(self):
        self.assertEqual(build.parse_amount_usd('$1.00'), 1.00)

    def test_no_dollar_sign_is_ambiguous(self):
        # Per the build contract: keep a parsed number only when the raw string is unambiguous.
        # A bare number with no currency marker is not accepted.
        self.assertIsNone(build.parse_amount_usd('5,557,465,180'))

    def test_range_is_ambiguous(self):
        self.assertIsNone(build.parse_amount_usd('$1,000,000 - $2,000,000'))

    def test_confidential_is_ambiguous(self):
        self.assertIsNone(build.parse_amount_usd('Confidential'))
        self.assertIsNone(build.parse_amount_usd('Undisclosed'))

    def test_empty_or_none(self):
        self.assertIsNone(build.parse_amount_usd(''))
        self.assertIsNone(build.parse_amount_usd(None))

    def test_never_raises_on_garbage(self):
        for garbage in ('$$$', '  ', '$-5', object(), 12345, ['$1']):
            try:
                build.parse_amount_usd(garbage)
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(f'parse_amount_usd raised on {garbage!r}: {exc!r}')


class AmountBandTests(unittest.TestCase):
    def test_not_stated(self):
        self.assertEqual(build.amount_band(None), 'not_stated')

    def test_under_1m_boundary(self):
        self.assertEqual(build.amount_band(999999.99), 'under_1m')
        self.assertEqual(build.amount_band(0), 'under_1m')

    def test_1m_10m_boundary(self):
        self.assertEqual(build.amount_band(1000000), '1m_10m')
        self.assertEqual(build.amount_band(9999999.99), '1m_10m')

    def test_10m_100m_boundary(self):
        self.assertEqual(build.amount_band(10000000), '10m_100m')
        self.assertEqual(build.amount_band(99999999.99), '10m_100m')

    def test_over_100m_boundary(self):
        self.assertEqual(build.amount_band(100000000), 'over_100m')
        self.assertEqual(build.amount_band(5557465180.00), 'over_100m')

    def test_every_band_key_has_a_label(self):
        for band in ('not_stated', 'under_1m', '1m_10m', '10m_100m', 'over_100m'):
            self.assertIn(band, build.AMOUNT_BAND_LABELS)


class CaptionRedactionTests(unittest.TestCase):
    """Real captions copied verbatim from verdict_lead_index.json / the report's own examples.
    The overriding invariant: a natural-person name is NEVER present in the returned title, whether it
    appears on the plaintiff side (the literal rule) or the defendant side (this build's conservative
    extension of "natural-person names are never published" -- see README, and the
    'Jane Doe vs. Alkiviades David' / 'Jean Michele Cross Revocable Trust' cases below for why the
    extension is necessary on real data)."""

    def test_in_re_mass_tort_caption_is_kept_verbatim(self):
        title, redacted, basis = build.redact_caption('In Re NFL Sunday Ticket Antitrust Litig.')
        self.assertEqual(title, 'In Re NFL Sunday Ticket Antitrust Litig.')
        self.assertFalse(redacted)
        self.assertEqual(basis, 'in_re_caption_kept')

    def test_in_re_diocese_caption_is_kept(self):
        title, redacted, _ = build.redact_caption('In Re Diocese of Rockville Centre Child Sex Abuse Victims')
        self.assertFalse(redacted)
        self.assertEqual(title, 'In Re Diocese of Rockville Centre Child Sex Abuse Victims')

    def test_org_vs_org_caption_is_kept_verbatim(self):
        case = 'BML Properties Ltd. v. China Construction America Inc., et al.'
        title, redacted, basis = build.redact_caption(case)
        self.assertEqual(title, case)
        self.assertFalse(redacted)
        self.assertEqual(basis, 'both_sides_org_kept')

    def test_person_plaintiff_vs_organisation_defendant(self):
        # The paradigm case named in the build contract's own example format.
        title, redacted, basis = build.redact_caption('McKivison v. Monsanto Co.')
        self.assertEqual(title, 'Individual plaintiff v. Monsanto Co.')
        self.assertTrue(redacted)
        self.assertEqual(basis, 'plaintiff_redacted')

    def test_person_plaintiff_vs_government_defendant_with_et_al(self):
        case = "Jane Roe 390 A.U., et al. v. County of Los Angeles, et al."
        title, redacted, _ = build.redact_caption(case)
        self.assertEqual(title, 'Individual plaintiffs v. County of Los Angeles, et al.')
        self.assertTrue(redacted)
        self.assertNotIn('Roe', title)

    def test_estate_of_plaintiff_is_redacted(self):
        title, redacted, _ = build.redact_caption('Estate of Loree v. TNT Crane & Rigging, Inc.')
        self.assertEqual(title, 'Individual plaintiff v. TNT Crane & Rigging, Inc.')
        self.assertTrue(redacted)
        self.assertNotIn('Loree', title)

    def test_collective_plaintiff_descriptor_is_conservatively_redacted(self):
        # "AB218 Plaintiffs" names no specific person, but the classifier has no organisation signal
        # to key on either, so it redacts (accepted false positive; see README "when unsure, redact").
        title, redacted, _ = build.redact_caption('AB218 Plaintiffs v. County of Los Angeles')
        self.assertEqual(title, 'Individual plaintiffs v. County of Los Angeles')
        self.assertTrue(redacted)

    def test_both_sides_person_like_are_both_redacted(self):
        # Real case: a family-business dispute where neither side is an organisation.
        title, redacted, basis = build.redact_caption('Jogani v. Jogani, et al.')
        self.assertEqual(title, 'Individual plaintiff v. Individual defendants')
        self.assertTrue(redacted)
        self.assertEqual(basis, 'plaintiff_redacted+defendant_redacted')
        self.assertNotIn('Jogani', title)

    def test_named_individual_defendant_is_also_redacted(self):
        # Regression test for a real leak found while building this classifier: the defendant here is
        # a named individual (a real person, not a company), even though the rule as literally stated
        # only calls out redacting the plaintiff side. Publishing 'Alkiviades David' would violate the
        # overarching "natural-person names are never published" rule, so the defendant side is
        # redacted too whenever it does not carry an organisation/government signal.
        title, redacted, _ = build.redact_caption('Jane Doe vs. Alkiviades David, et al.')
        self.assertNotIn('Alkiviades', title)
        self.assertNotIn('David', title)
        self.assertTrue(redacted)

    def test_personal_revocable_trust_caption_is_redacted(self):
        # Regression test: a personal estate-planning trust literally carries the settlor's full name
        # ("Jean Michele Cross Revocable Trust"). 'trust' is deliberately NOT treated as an
        # organisation signal for this reason (unlike 'llc'/'inc'/'corp').
        title, redacted, _ = build.redact_caption("Jean Michele Cross Revocable Trust v. Four P's Grp. LLC, et al.")
        self.assertNotIn('Cross', title)
        self.assertNotIn('Jean', title)
        self.assertTrue(redacted)

    def test_ampersand_pair_without_other_signal_is_redacted(self):
        # Regression test: a bare 'X & Y' caption is ambiguous between a firm/company name (kept) and
        # two individual co-plaintiffs (must be redacted); '&' alone is not treated as an organisation
        # signal, so 'Johnson & Johnson' printed with no other corporate marker is conservatively
        # redacted too. This is an accepted, documented trade-off (see README) -- the alternative
        # (treating any 'X & Y' as an organisation) would publish real co-plaintiff names such as
        # 'Lincome & Bishop'.
        title, redacted, _ = build.redact_caption('Moore v. Johnson & Johnson')
        self.assertEqual(title, 'Individual plaintiff v. Individual defendant')
        self.assertTrue(redacted)
        title2, redacted2, _ = build.redact_caption('Lincome & Bishop v. Four-Season Travel LLC, et al.')
        self.assertNotIn('Lincome', title2)
        self.assertNotIn('Bishop', title2)
        self.assertTrue(redacted2)

    def test_in_re_probate_matter_naming_an_individual_is_redacted(self):
        # Regression test: not every 'In re' caption is a mass-tort/collective caption -- probate
        # captions ('In re Durable Power of Attorney of <name>') name one specific private person.
        title, redacted, basis = build.redact_caption('In re Durable Power of Attorney of Dock Dean')
        self.assertNotIn('Dean', title)
        self.assertTrue(redacted)
        self.assertEqual(basis, 'in_re_individual_matter_redacted')

    def test_in_re_personal_trust_amendment_is_redacted(self):
        title, redacted, _ = build.redact_caption(
            'In Re: Amendment & Complete Restatement of the Arnold Rosenblatt Revocable')
        self.assertNotIn('Rosenblatt', title)
        self.assertTrue(redacted)

    def test_bare_in_re_surname_is_redacted_when_unsure(self):
        title, redacted, _ = build.redact_caption('In re Saunders')
        self.assertNotIn('Saunders', title)
        self.assertTrue(redacted)

    def test_in_re_institution_is_kept(self):
        title, redacted, _ = build.redact_caption('In re Chiquita Brands Int\u2019l Inc. Litig.')
        self.assertFalse(redacted)

    def test_multi_case_blob_is_fully_redacted(self):
        # Regression test for a genuine, measured publisher data-quality defect: ~20% of source rows
        # concatenate several distinct case captions into one 'case' string with no reliable
        # delimiter. Rather than guess where one caption ends and the next begins (and risk leaking a
        # name hidden in the middle of the blob), any caption with 2+ "v./vs." occurrences is redacted
        # wholesale.
        case = ('London Luxury LLC v. Walmart Inc. International Constr. Prods. LLC v. Caterpillar Inc. '
                'Latorre, et al. v. Mendez, et al. Lubben v. Lopez Estate of Metcalf v. Erie County, et al. '
                'Rose, et al. v. Pharmacia LLC, et al.')
        title, redacted, basis = build.redact_caption(case)
        self.assertTrue(redacted)
        self.assertEqual(basis, 'multi_case_blob_redacted')
        for name in ('Latorre', 'Mendez', 'Lubben', 'Lopez', 'Metcalf', 'Rose', 'Walmart'):
            self.assertNotIn(name, title)

    def test_never_raises_on_missing_or_odd_input(self):
        for value in (None, '', '   ', 123, ['v.']):
            try:
                build.redact_caption(value)
            except Exception as exc:  # pragma: no cover
                self.fail(f'redact_caption raised on {value!r}: {exc!r}')

    def test_no_known_natural_person_surname_survives_across_a_sample_of_real_captions(self):
        # Broad regression net over a larger sample of real, verbatim captions from the input file.
        banned_surnames = ('Jogani', 'Alkiviades', 'Rosenblatt', 'Latorre', 'Lubben', 'Saunders',
                            'Goodstein', 'Cross', 'Bishop', 'Lincome')
        sample_captions = [
            'Jogani v. Jogani, et al.',
            'Brown v. Affinitylifestylescom Inc.',
            'Jane Roe 390 A.U., et al. v. County of Los Angeles, et al.',
            'Monahan, et al. v. Toback, et al.',
            'Jane Doe vs. Alkiviades David, et al.',
            "Jean Michele Cross Revocable Trust v. Four P's Grp. LLC, et al.",
            'Lincome & Bishop v. Four-Season Travel LLC, et al.',
            'In re Durable Power of Attorney of Dock Dean',
            'In Re: Amendment & Complete Restatement of the Arnold Rosenblatt Revocable',
            'In re Saunders',
            'Hadden Survivors v. Columbia University',
        ]
        for case in sample_captions:
            title, _, _ = build.redact_caption(case)
            for surname in banned_surnames:
                self.assertNotIn(surname, title, msg=f'{surname!r} leaked from {case!r} -> {title!r}')


class MdlLinkingTests(unittest.TestCase):
    def setUp(self):
        self.by_title = {
            build.normalize_caption('IN RE: Example Products Liability Litigation'): (9001, 'pending'),
        }
        self.by_number = {9001: 'pending'}

    def test_explicit_mdl_number_in_case_text(self):
        self.assertEqual(build.extract_explicit_mdl_number('In re Widget (MDL No. 2741)'), 2741)
        self.assertEqual(build.extract_explicit_mdl_number('Some case (MDL 1234)'), 1234)
        self.assertEqual(build.extract_explicit_mdl_number('Case re: MDL-1358 matter'), 1358)

    def test_no_explicit_number_returns_none(self):
        self.assertIsNone(build.extract_explicit_mdl_number('Smith v. Jones Corp.'))

    def test_caption_matches_registry_after_normalization(self):
        # Whitespace/case differences only -- still a match.
        number, basis, status = build.link_mdl(
            'in   re:  example   products liability   litigation', '', self.by_title, self.by_number)
        self.assertEqual(number, 9001)
        self.assertEqual(basis, 'caption_matches_jpml_registry_title')
        self.assertEqual(status, 'pending')

    def test_caption_near_miss_does_not_link(self):
        # Not an exact match after normalization (extra word) -- must not link.
        number, basis, status = build.link_mdl(
            'In re: Example Products Liability Litigation (No. II)', '', self.by_title, self.by_number)
        self.assertIsNone(number)
        self.assertIsNone(basis)

    def test_explicit_number_wins_and_reports_registry_status(self):
        number, basis, status = build.link_mdl('In re Widget (MDL No. 9001)', '', self.by_title, self.by_number)
        self.assertEqual(number, 9001)
        self.assertEqual(basis, 'report_named_mdl_number')
        self.assertEqual(status, 'pending')

    def test_explicit_number_not_in_registry_still_links_but_flags_status(self):
        number, basis, status = build.link_mdl('In re Widget (MDL No. 4242)', '', self.by_title, self.by_number)
        self.assertEqual(number, 4242)
        self.assertEqual(basis, 'report_named_mdl_number')
        self.assertEqual(status, 'not_in_registry')

    def test_no_signal_returns_none(self):
        number, basis, status = build.link_mdl('Smith v. Acme Corp.', 'Breach of Contract', self.by_title, self.by_number)
        self.assertIsNone(number)
        self.assertIsNone(basis)
        self.assertIsNone(status)

    def test_real_registry_has_zero_exact_caption_matches(self):
        # Documents a measured, expected finding: the strict (report names a number, or the caption is
        # an exact normalized match) rule links zero of the 3,312 real rows, because the publisher's
        # captions are abbreviated/paraphrased relative to the JPML's own caption text. This is
        # intentional conservatism, not a bug -- see README.
        if not build.JPML_MDLS.exists() or not build.INPUT_JSON.exists():
            self.skipTest('local inputs not present in this environment')
        by_title, by_number = build.load_jpml_registry(build.JPML_MDLS)
        records = json.loads(build.INPUT_JSON.read_text(encoding='utf-8'))['records']
        linked = [r for r in records if build.link_mdl(r.get('case') or '', r.get('type') or '', by_title, by_number)[0]]
        self.assertEqual(len(linked), 0)


class IdGenerationTests(unittest.TestCase):
    def test_deterministic(self):
        a = build.compute_id('https://topverdict.com/lists/2024/x', 1, 'Smith v. Jones')
        b = build.compute_id('https://topverdict.com/lists/2024/x', 1, 'Smith v. Jones')
        self.assertEqual(a, b)

    def test_distinguishes_different_rows(self):
        a = build.compute_id('https://topverdict.com/lists/2024/x', 1, 'Smith v. Jones')
        b = build.compute_id('https://topverdict.com/lists/2024/x', 2, 'Smith v. Jones')
        c = build.compute_id('https://topverdict.com/lists/2024/y', 1, 'Smith v. Jones')
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_stable_prefix_and_shape(self):
        value = build.compute_id('https://topverdict.com/lists/2024/x', 1, 'Smith v. Jones')
        self.assertTrue(value.startswith('vsr-'))


class RealDataSmokeTest(unittest.TestCase):
    """Runs the pure per-record pipeline (no DB/file writes) over the real 3,312-row input, if present,
    and checks the headline measured counts from the input report so a future re-run of build.py can be
    trusted to reproduce them."""

    def test_full_pipeline_over_real_records(self):
        if not build.INPUT_JSON.exists():
            self.skipTest('real input not present in this environment')
        payload = json.loads(build.INPUT_JSON.read_text(encoding='utf-8'))
        records = payload['records']
        self.assertEqual(len(records), 3312)
        by_title, by_number = build.load_jpml_registry(build.JPML_MDLS) if build.JPML_MDLS.exists() else ({}, {})
        mass_tort = 0
        seen_ids = set()
        for ordinal, record in enumerate(records):
            row = build.build_row(record, ordinal, by_title, by_number)
            self.assertNotIn(row['id'], seen_ids)
            seen_ids.add(row['id'])
            if row['is_mass_tort']:
                mass_tort += 1
            # Privacy invariant on real data: the stored title never contains a comma-and-'et al.'-free
            # bare surname from the plaintiff position of an unredacted, non-organisation caption. We
            # cannot enumerate every possible name, but we can assert the redaction machinery actually
            # ran (title differs from raw case) whenever the basis says it should have.
            if row['title_redacted']:
                self.assertNotEqual(row['title'], record.get('case'))
        self.assertEqual(mass_tort, 670)


if __name__ == '__main__':
    unittest.main()
