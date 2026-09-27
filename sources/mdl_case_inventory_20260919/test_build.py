"""Tests for the mdl_case_inventory_20260919 build.

Rule tests (classifier / join / labelling) run without the built data files.
Data tests are skipped when the build has not run yet.
"""
import json
import os
import unittest

import build

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, 'cases.jsonl')
EDGES = os.path.join(HERE, 'graph_edges.jsonl')
UNRESOLVED = os.path.join(HERE, 'unresolved.jsonl')
VALIDATION = os.path.join(HERE, 'validation.json')


def _read_jsonl(path):
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


class CaptionRuleTests(unittest.TestCase):
    """Party names: natural-person plaintiffs are never listed. Only collective
    'In re ...' captions may be displayed; every other caption is suppressed."""

    def test_in_re_captions_are_displayable(self):
        self.assertTrue(build.is_displayable_caption('In Re: Mirena IUD Products Liability Litigation'))
        self.assertTrue(build.is_displayable_caption('IN RE: BENICAR (OLMESARTAN) PRODUCTS LIABILITY LITIGATION'))
        self.assertTrue(build.is_displayable_caption('In re Roundup Products Liability Litigation'))

    def test_party_style_captions_are_suppressed(self):
        self.assertFalse(build.is_displayable_caption('JOY v. DOLGENCORP, INC.'))
        self.assertFalse(build.is_displayable_caption('City of Georgiana, Alabama v. Purdue Pharma L.P.'))

    def test_bare_person_name_caption_is_suppressed(self):
        # Bankruptcy-style caption with no ' v. ' at all: still a natural person.
        self.assertFalse(build.is_displayable_caption('Alphonso Burton and Sandra Burton'))

    def test_empty_caption_is_not_displayable(self):
        self.assertFalse(build.is_displayable_caption(None))
        self.assertFalse(build.is_displayable_caption(''))

    def test_in_re_prefixed_member_case_caption_with_party_separator_is_suppressed(self):
        # An individual member-case caption that merely begins with "In re" still names a
        # natural-person plaintiff and must not be published.
        self.assertFalse(build.is_displayable_caption(
            'In re: Samuel Keller v. Electronic Arts Inc.'))
        self.assertFalse(build.is_displayable_caption('In re: Jason Hill v. Volkswagen, AG'))
        self.assertFalse(build.is_displayable_caption('In re: Mary Carr v. Google LLC'))
        self.assertFalse(build.is_displayable_caption(
            'IN RE Humphrys v. TD Ameritrade Holding Corporation'))

    def test_no_published_caption_matches_the_party_separator_regex(self):
        for name in ('In Re: Mirena IUD Products Liability Litigation',
                     'In re Roundup Products Liability Litigation',
                     'IN RE: BENICAR (OLMESARTAN) PRODUCTS LIABILITY LITIGATION'):
            self.assertTrue(build.is_displayable_caption(name))
            self.assertIsNone(build._PARTY_SEPARATOR_RE.search(name))
        for name in ('In re: Samuel Keller v. Electronic Arts Inc.',
                     'JOY v. DOLGENCORP, INC.'):
            self.assertIsNotNone(build._PARTY_SEPARATOR_RE.search(name))


class CourtJoinRuleTests(unittest.TestCase):
    """Court joins are exact CourtListener court id matches only; never by name."""

    def test_exact_id_match(self):
        spine = {'nysd': {'id': 'nysd', 'name': 'S.D. New York'}}
        got, reason = build.resolve_court('nysd', spine)
        self.assertEqual(got['id'], 'nysd')
        self.assertIsNone(reason)

    def test_unknown_id_is_unlinked_with_a_reason_not_guessed(self):
        spine = {'nysd': {'id': 'nysd', 'name': 'S.D. New York'}}
        got, reason = build.resolve_court('zzz', spine)
        self.assertIsNone(got)
        self.assertIn('zzz', reason)

    def test_name_is_never_used_to_join(self):
        spine = {'nysd': {'id': 'nysd', 'name': 'S.D. New York'}}
        got, reason = build.resolve_court('S.D. New York', spine)
        self.assertIsNone(got)
        self.assertIsNotNone(reason)


class JudgeJoinRuleTests(unittest.TestCase):
    """Judge joins reach the MVP judge entity ONLY via cl_person_id."""

    OVERLAY = {'611': 'judge-entity-aaa'}

    def test_native_judge_id_bridges_via_cl_person_id(self):
        entity, reason = build.resolve_judge_entity('611', self.OVERLAY)
        self.assertEqual(entity, 'judge-entity-aaa')
        self.assertIsNone(reason)

    def test_unbridged_person_stays_unlinked_with_a_reason(self):
        entity, reason = build.resolve_judge_entity('99999', self.OVERLAY)
        self.assertIsNone(entity)
        self.assertIn('99999', reason)

    def test_missing_native_id_is_unlinked(self):
        entity, reason = build.resolve_judge_entity(None, self.OVERLAY)
        self.assertIsNone(entity)
        self.assertIsNotNone(reason)


class MdlMembershipRuleTests(unittest.TestCase):
    """Precedence: the matter IS the master docket > member_of_mdl edge > catalog
    mdl_master_docket_id pointing at a resolved master. Never guessed."""

    def test_master_wins(self):
        kind, num, ev = build.classify_mdl_membership(
            matter_id='m1', cl_docket_id=10,
            master_by_matter={'m1': 2804}, member_by_matter={'m1': 2913},
            catalog_master_link=2885)
        self.assertEqual(kind, 'master_docket_of_mdl')
        self.assertEqual(num, 2804)
        self.assertIn('2804', ev)

    def test_member_edge_beats_catalog_link(self):
        kind, num, ev = build.classify_mdl_membership(
            matter_id='m1', cl_docket_id=10,
            master_by_matter={}, member_by_matter={'m1': 2913},
            catalog_master_link=2885)
        self.assertEqual(kind, 'member_of_mdl')
        self.assertEqual(num, 2913)

    def test_catalog_link_used_when_nothing_native_applies(self):
        kind, num, ev = build.classify_mdl_membership(
            matter_id='m1', cl_docket_id=10,
            master_by_matter={}, member_by_matter={}, catalog_master_link=2885)
        self.assertEqual(kind, 'catalog_mdl_master_docket_id')
        self.assertEqual(num, 2885)

    def test_no_membership_is_none_not_a_guess(self):
        kind, num, ev = build.classify_mdl_membership(
            matter_id='m1', cl_docket_id=10,
            master_by_matter={}, member_by_matter={}, catalog_master_link=None)
        self.assertIsNone(kind)
        self.assertIsNone(num)
        self.assertIsNotNone(ev)


class BuiltDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CASES):
            raise unittest.SkipTest('build has not run yet')
        cls.cases = _read_jsonl(CASES)
        cls.edges = _read_jsonl(EDGES)
        cls.unresolved = _read_jsonl(UNRESOLVED)
        cls.validation = json.load(open(VALIDATION, encoding='utf-8'))

    def test_key_claim_4159_aws_matters_three_node_roles(self):
        c = self.validation['counts']
        self.assertEqual(c['aws_matters_total'], 4159)
        self.assertEqual(c['node_role_public_docket'], 4086)
        self.assertEqual(c['node_role_target_matter'], 57)
        self.assertEqual(c['node_role_support_master'], 16)

    def test_key_claim_catalog_2122_with_977_mdl_master_links(self):
        c = self.validation['counts']
        self.assertEqual(c['catalog_dockets_total'], 2122)
        self.assertEqual(c['catalog_dockets_with_mdl_master_docket_id'], 977)

    def test_ids_are_unique_and_stable(self):
        ids = [r['id'] for r in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for r in self.cases[:50]:
            self.assertTrue(r['id'].startswith('cl_docket:') or r['id'].startswith('aws_matter:'))

    def test_no_suppressed_caption_is_published(self):
        for r in self.cases:
            if r['case_name'] is not None:
                self.assertTrue(build.is_displayable_caption(r['case_name']), r['id'])
            else:
                self.assertIsNotNone(r['case_name_suppressed_reason'])

    def test_every_mdl_linked_row_carries_evidence(self):
        for r in self.cases:
            if r['mdl'] is not None:
                self.assertIn(r['mdl']['membership_kind'],
                              ('master_docket_of_mdl', 'member_of_mdl', 'catalog_mdl_master_docket_id'))
                self.assertTrue(r['mdl']['evidence'])
            else:
                self.assertTrue(r['mdl_unlinked_reason'])

    def test_judge_entity_only_present_with_a_cl_person_id(self):
        for r in self.cases:
            for j in r['judges']:
                if j['judge_entity_id'] is not None:
                    self.assertIsNotNone(j['cl_person_id'])
                else:
                    self.assertTrue(j['judge_entity_unlinked_reason'])

    def test_edges_have_from_to_relation_basis_evidence(self):
        self.assertGreater(len(self.edges), 0)
        rels = set()
        for e in self.edges:
            for k in ('from', 'to', 'relation', 'basis', 'evidence'):
                self.assertIn(k, e)
            self.assertIn(e['from']['type'], ('cl_docket', 'aws_matter'))
            self.assertIn(e['to']['type'], ('cl_court', 'cl_person', 'judge_entity', 'mdl'))
            rels.add(e['relation'])
        self.assertIn('filed_in_court', rels)
        self.assertIn('assigned_judge', rels)

    def test_rollups_are_recomputed_from_rows(self):
        c = self.validation['counts']
        from collections import Counter
        courts = Counter(r['court']['court_spine_id'] for r in self.cases if r['court']['court_spine_id'])
        self.assertEqual(c['distinct_courts_linked'], len(courts))
        mdl_rows = sum(1 for r in self.cases if r['mdl'])
        self.assertEqual(c['cases_with_mdl'], mdl_rows)

    def test_qualification_states_it_is_a_firm_sample(self):
        q = self.validation['qualification'].lower()
        self.assertIn('sample', q)
        self.assertIn('not', q)
        self.assertIn('jpml', q)

    def test_validation_envelope(self):
        v = self.validation
        self.assertEqual(v['schema_version'], '1')
        self.assertEqual(v['status'], 'passed')
        self.assertTrue(v['ready'])
        self.assertEqual(v['license_ref'], 'sw_bulk_private_firm_work_product')
        self.assertFalse(v['export_allowed'])
        self.assertTrue(v['data_files'])
        self.assertTrue(v['inputs'])

    def test_no_case_name_matches_the_party_separator_regex(self):
        for r in self.cases:
            if r['case_name'] is not None:
                self.assertIsNone(build._PARTY_SEPARATOR_RE.search(r['case_name']), r['id'])

    def test_no_fraction_or_rate_key_is_emitted_anywhere_in_coverage(self):
        import json as _json
        coverage = _json.load(open(os.path.join(HERE, 'mdl_coverage.json'), encoding='utf-8'))
        for v in coverage.values():
            for k in v:
                self.assertNotIn('fraction', k.lower())
                self.assertNotIn('rate', k.lower())

    def test_qualification_short_is_present_and_short(self):
        self.assertIn('qualification_short', self.validation)
        self.assertLessEqual(len(self.validation['qualification_short']), 500)

    def test_all_catalog_dockets_join_to_a_matter(self):
        c = self.validation['counts']
        self.assertEqual(c['catalog_dockets_joined_to_a_matter'], 2122)
        self.assertEqual(c['catalog_dockets_without_a_matter'], 0)
        self.assertFalse(any(u['type'] == 'catalog_docket_without_aws_matter'
                             for u in self.unresolved))

    def test_every_from_matter_id_in_matter_relationships_is_accounted_for(self):
        # Every matter that carries a member_of_mdl relationship in the AWS release either gets
        # an MDL link or the distinct member_of_mdl_parent_not_resolvable reason -- never the
        # generic "no relationship" reason, and never silently dropped.
        import gzip as _gzip
        import json as _json
        release = os.path.join(r'C:\Users\firas\Downloads\SW-BULK', 'AWS-BATCH1-DOCKETS',
                               'releases', 'b2b-cdbb8d040b95c7b65cfc')
        path = os.path.join(release, 'matter_relationships.jsonl.gz')
        if not os.path.exists(path):
            raise unittest.SkipTest('SW-BULK release not present in this environment')
        with _gzip.open(path, 'rt', encoding='utf-8') as f:
            rels = [_json.loads(line) for line in f if line.strip()]
        member_from_ids = {r['from_matter_id'] for r in rels
                           if r.get('relationship_type') == 'member_of_mdl'
                           and r.get('record_status') == 'active'}
        by_matter = {}
        for r in self.cases:
            by_matter.setdefault(r['aws_matter_id'], []).append(r)
        parent_not_resolvable = {u['aws_matter_id'] for u in self.unresolved
                                 if u['type'] == 'member_of_mdl_parent_not_resolvable'}
        for matter_id in member_from_ids:
            rows = by_matter.get(matter_id, [])
            if not rows:
                continue
            has_mdl_link = any(r.get('mdl') for r in rows)
            self.assertTrue(has_mdl_link or matter_id in parent_not_resolvable, matter_id)
            if not has_mdl_link:
                for r in rows:
                    self.assertNotEqual(
                        r.get('mdl_unlinked_reason'),
                        None)
                    self.assertNotIn('no member_of_mdl relationship',
                                     r.get('mdl_unlinked_reason') or '')


if __name__ == '__main__':
    unittest.main()
