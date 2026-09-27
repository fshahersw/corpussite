import json
import tempfile
import unittest
from pathlib import Path

import build as b


class NormalizeDocketTests(unittest.TestCase):
    def test_zero_pad_both_directions(self):
        self.assertEqual(b.normalize_docket('1:17-md-02804'), '1:17-md-2804')
        self.assertEqual(b.normalize_docket('1:17-md-2804'), '1:17-md-2804')

    def test_case_insensitive(self):
        self.assertEqual(b.normalize_docket('1:17-MD-02804'), b.normalize_docket('1:17-md-2804'))

    def test_no_dash_passthrough(self):
        self.assertEqual(b.normalize_docket('abc123'), 'abc123')

    def test_non_numeric_tail_passthrough(self):
        self.assertEqual(b.normalize_docket('1:17-md-abc'), '1:17-md-abc')

    def test_none_and_empty(self):
        self.assertIsNone(b.normalize_docket(None))
        self.assertIsNone(b.normalize_docket(''))


class ComputeTests(unittest.TestCase):
    """Exercise compute() against tiny synthetic fixtures so the join/labelling logic is verified
    independent of the real (large, private) corpus."""

    def setUp(self):
        self.registry = [
            {'mdl_number': 1001, 'status': 'pending', 'title': 'Widget MDL', 'cl_court_id': 'nysd',
             'master_docket': '1:20-md-1001', 'cl_links': {'docket_id': 555}},
            {'mdl_number': 1002, 'status': 'terminated', 'title': 'Gadget MDL', 'cl_court_id': 'cand',
             'master_docket': '2:19-md-1002', 'cl_links': {}},
            {'mdl_number': 1003, 'status': 'pending', 'title': 'Unmatched MDL', 'cl_court_id': 'txnd',
             'master_docket': '3:21-md-1003', 'cl_links': {}},
        ]
        self.matters = [
            {'matter_id': 'm-master-1001', 'court_id': 'nysd', 'docket_number': '1:20-md-01001',
             'case_name': 'In re Widget', 'case_status': 'pending', 'date_filed': '2020-01-01', 'date_terminated': None},
            {'matter_id': 'm-master-1002', 'court_id': 'cand', 'docket_number': '2:19-md-01002',
             'case_name': 'In re Gadget', 'case_status': 'terminated', 'date_filed': '2019-01-01', 'date_terminated': '2022-01-01'},
            {'matter_id': 'm-member-a', 'court_id': 'nysd', 'docket_number': '1:20-cv-00001',
             'case_name': 'Doe v. Widget Co', 'case_status': 'active', 'date_filed': '2020-06-01', 'date_terminated': None},
            {'matter_id': 'm-orphan-parent', 'court_id': 'flmd', 'docket_number': '4:15-md-09999',
             'case_name': 'In re Orphan', 'case_status': 'pending', 'date_filed': '2015-01-01', 'date_terminated': None},
            {'matter_id': 'm-orphan-member', 'court_id': 'flmd', 'docket_number': '4:15-cv-00002',
             'case_name': 'Roe v. Orphan Co', 'case_status': 'active', 'date_filed': '2015-06-01', 'date_terminated': None},
            {'matter_id': 'm-parked', 'court_id': 'nysd', 'docket_number': '1:20-cv-05550',
             'case_name': 'Parked Master', 'case_status': 'supporting_master', 'date_filed': '2020-02-01', 'date_terminated': None},
        ]
        self.aliases = [
            {'matter_id': 'm-master-1001', 'namespace': 'courtlistener_docket_id', 'value': '555', 'record_status': 'active'},
            {'matter_id': 'm-master-1002', 'namespace': 'courtlistener_docket_id', 'value': '777', 'record_status': 'active'},
        ]
        self.relationships = [
            {'record_id': 'rel-1', 'relationship_type': 'member_of_mdl', 'record_status': 'active',
             'from_matter_id': 'm-member-a', 'to_matter_id': 'm-master-1001'},
            {'record_id': 'rel-2', 'relationship_type': 'member_of_mdl', 'record_status': 'active',
             'from_matter_id': 'm-orphan-member', 'to_matter_id': 'm-orphan-parent'},
        ]
        self.manifest = {'descriptor': {'run_scope': {'requested_docket_aliases': 4186, 'returned_docket_aliases': 4160, 'selected_canonical_matters': 4129}}}
        self.catalog_masters = [
            {'master_docket_id': 555, 'docket_number': '1:20-md-01001', 'court': 'nysd',
             'case_name': 'In re Widget', 'mdl_number': '01001', 'document_count': 10, 'high_value_docs': 2},
            {'master_docket_id': 900, 'docket_number': '1:20-cv-05550', 'court': 'nysd',
             'case_name': 'Parked Master', 'mdl_number': None, 'document_count': 5, 'high_value_docs': 1},
            {'master_docket_id': 901, 'docket_number': '9:99-cv-00000', 'court': 'zzxx',
             'case_name': 'Truly Unresolvable', 'mdl_number': None, 'document_count': 1, 'high_value_docs': 0},
            {'master_docket_id': 902, 'docket_number': '5:00-md-05000', 'court': 'nvd',
             'case_name': 'Not In Registry', 'mdl_number': '05000', 'document_count': 3, 'high_value_docs': 0},
        ]
        self.catalog_matters = [{'docket_id': 555}, {'docket_id': 999999}]

    def test_master_matched_by_docket_number_normalization(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        by_num = {r['mdl_number']: r for r in crosswalk}
        self.assertEqual(set(by_num), {1001, 1002})
        self.assertEqual(by_num[1001]['aws_matter_id'], 'm-master-1001')
        self.assertEqual(by_num[1001]['cl_docket_id'], 555)
        self.assertIn('docket_number_normalized_and_court_match', by_num[1001]['basis'])

    def test_unmatched_registry_mdl_absent_from_crosswalk(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        self.assertNotIn(1003, {r['mdl_number'] for r in crosswalk})

    def test_parked_master_resolved_by_cl_docket_id_equality(self):
        # 'm-parked' catalog master has no native mdl_number, but its master_docket_id (900) does not
        # match any registry cl_links.docket_id, so it must land in unresolved, not be guessed.
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        reasons = [u for u in unresolved if u.get('catalog_master_docket_id') == 900]
        self.assertEqual(len(reasons), 1)
        self.assertIn('registry', reasons[0]['reason'])

    def test_catalog_master_with_native_mdl_number_not_in_registry_is_unresolved(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        reasons = [u for u in unresolved if u.get('catalog_master_docket_id') == 902]
        self.assertEqual(len(reasons), 1)
        self.assertIn('05000', reasons[0]['reason'])

    def test_member_of_mdl_edge_resolved(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]['from'], {'type': 'aws_matter', 'id': 'm-member-a'})
        self.assertEqual(edges[0]['to'], {'type': 'mdl', 'id': 1001})
        self.assertEqual(edges[0]['relation'], 'member_of_mdl')
        self.assertTrue(edges[0]['evidence']['relationship_record_id'])

    def test_orphan_member_of_mdl_edge_goes_to_unresolved_with_reason(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        orphan = [u for u in unresolved if u.get('type') == 'member_of_mdl_edge']
        self.assertEqual(len(orphan), 1)
        self.assertEqual(orphan[0]['member_matter_id'], 'm-orphan-member')
        self.assertIn('parent not resolvable', orphan[0]['reason'])

    def test_no_duplicate_mdl_numbers(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        numbers = [r['mdl_number'] for r in crosswalk]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_party_style_case_names_are_suppressed(self):
        # 'm-member-a' / 'Doe v. Widget Co' is a member matter (not a master), so it never becomes a
        # crosswalk row's aws_case_name; assert directly against the suppression helper plus a
        # synthetic master case to prove compute() suppresses when a master DOES carry a party caption.
        registry = self.registry + [
            {'mdl_number': 1004, 'status': 'pending', 'title': 'Party Caption MDL', 'cl_court_id': 'nysd',
             'master_docket': '1:20-md-1004', 'cl_links': {}},
        ]
        matters = self.matters + [
            {'matter_id': 'm-master-1004', 'court_id': 'nysd', 'docket_number': '1:20-md-01004',
             'case_name': 'Smith v. Widget Corp', 'case_status': 'pending', 'date_filed': '2020-01-01',
             'date_terminated': None},
        ]
        catalog_masters = self.catalog_masters + [
            {'master_docket_id': 1004, 'docket_number': '1:20-md-01004', 'court': 'nysd',
             'case_name': 'Smith v. Widget Corp', 'mdl_number': '01004', 'document_count': 1, 'high_value_docs': 0},
        ]
        crosswalk, edges, unresolved, counts, checks = b.compute(
            registry, matters, self.aliases, self.relationships, self.manifest,
            catalog_masters, self.catalog_matters)
        by_num = {r['mdl_number']: r for r in crosswalk}
        row = by_num[1004]
        self.assertIsNone(row['aws_case_name'])
        self.assertIsNone(row['catalog_master_case_name'])
        self.assertIn('party-style caption', row['aws_case_name_suppressed_reason'])
        self.assertIn('party-style caption', row['catalog_master_case_name_suppressed_reason'])
        # Untouched collective caption keeps its name and a null suppression reason.
        self.assertEqual(by_num[1001]['aws_case_name'], 'In re Widget')
        self.assertIsNone(by_num[1001]['aws_case_name_suppressed_reason'])

    def test_no_crosswalk_row_case_name_contains_party_v_pattern(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        for row in crosswalk:
            for field in ('aws_case_name', 'catalog_master_case_name'):
                value = row.get(field)
                if value:
                    self.assertNotIn(' v. ', value.lower())

    def test_checks_all_pass_shape(self):
        crosswalk, edges, unresolved, counts, checks = b.compute(
            self.registry, self.matters, self.aliases, self.relationships, self.manifest,
            self.catalog_masters, self.catalog_matters)
        names = {c['name'] for c in checks}
        self.assertIn('zero_pad_normalization_load_bearing', names)
        for c in checks:
            if c['name'] == 'zero_pad_normalization_load_bearing':
                self.assertTrue(c['passed'])


class RealBuildTests(unittest.TestCase):
    """Runs the real build against the actual (private, local) corpus and checks the plan's measured
    counts. Skips gracefully if the private inputs are not present on this machine."""

    def setUp(self):
        for path in b.INPUT_FILES.values():
            if not Path(path).exists():
                self.skipTest('private corpus input not present: %s' % path)

    def test_real_build_matches_plan_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = b.build(out_dir=tmp)
            self.assertEqual(result['status'], 'passed')
            self.assertTrue(result['ready'])
            counts = result['counts']
            self.assertEqual(counts['crosswalk_rows'], 59)
            self.assertEqual(counts['crosswalk_pending'], 57)
            self.assertEqual(counts['crosswalk_terminated'], 2)
            self.assertEqual(counts['member_of_mdl_edges_resolved'], 40)
            self.assertEqual(counts['member_of_mdl_edges_unresolved'], 13)
            self.assertEqual(counts['matter_relationships_total'], 53)
            self.assertEqual(counts['matter_aliases_total'], 4202)
            crosswalk = [json.loads(l) for l in (Path(tmp) / 'crosswalk.jsonl').read_text(encoding='utf-8').splitlines()]
            by_num = {r['mdl_number']: r for r in crosswalk}
            self.assertIn(3060, by_num)
            self.assertEqual(by_num[3060]['cl_docket_id'], 66801859)
            unresolved = [json.loads(l) for l in (Path(tmp) / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines()]
            testosterone = [u for u in unresolved if u.get('catalog_master_docket_id') == 4261857]
            self.assertEqual(len(testosterone), 1)
            for row in crosswalk:
                for field in ('aws_case_name', 'catalog_master_case_name'):
                    value = row.get(field)
                    if value:
                        self.assertNotIn(' v. ', value.lower())
            for num in (2243, 2789, 3026):
                self.assertIsNone(by_num[num]['aws_case_name'])
                self.assertIn('party-style caption', by_num[num]['aws_case_name_suppressed_reason'])
            self.assertIsNone(by_num[2789]['catalog_master_case_name'])
            self.assertIn('party-style caption', by_num[2789]['catalog_master_case_name_suppressed_reason'])


if __name__ == '__main__':
    unittest.main()
