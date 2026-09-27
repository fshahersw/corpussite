import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

FLN = 'U.S. District Court for the Northern District of Florida'


def receipt(num, court, docket, person, name='X'):
    return {'file': 'receipts/search_mdl_%d.json' % num, 'file_sha256': 'b' * 64, 'response_sha256': 'c' * 64, 'requested_after_utc': '2026-09-19T11:41:11Z',
            'response': {'count': 1, 'results': [{'court_id': court, 'docketNumber': docket, 'docket_id': 1000 + num, 'assigned_to_id': person, 'assignedTo': name}]}}


def group(num, court='flnd', docket='3:25-md-3140', fjc_court=FLN):
    return {'mdl_number': num, 'master_docket': docket, 'cl_court_id': court, 'fjc_court_name': fjc_court, 'as_of': '2026-09-01'}


BRIDGE = {'2755': [{'entity_id': 'judge-entity-aaa', 'fjc_jid': '3043', 'fjc_nid': '1392051', 'fjc_name': 'Margaret Catharine Rodgers', 'fjc_courts': [FLN]}]}


class RuleTests(unittest.TestCase):
    def test_compare_names(self):
        self.assertEqual(build.compare_names('M. Casey Rodgers', 'Margaret Catharine Rodgers'),
                         {'surname_agrees': True, 'first_initial_agrees': True, 'printed_given_name_found_in_entity_given_names': True})
        beth = build.compare_names('Beth Phillips', 'Mary Elizabeth Phillips')
        self.assertEqual((beth['surname_agrees'], beth['first_initial_agrees'], beth['printed_given_name_found_in_entity_given_names']), (True, False, True))
        self.assertFalse(build.compare_names('Robert J. Shelby', 'James O. Browning')['surname_agrees'])
        self.assertTrue(build.compare_names('William H. Orrick, III', 'William Horsley Orrick III')['surname_agrees'])

    def test_native_bridge_resolves(self):
        aliases, unresolved = build.resolve({('M. Casey Rodgers', 'FLN'): [group(3140)]}, {3140: receipt(3140, 'flnd', '3:25-md-03140', 2755)}, BRIDGE, {})
        self.assertEqual(unresolved, [])
        a = aliases[0]
        self.assertEqual((a['alias'], a['entity_id'], a['relation'], a['mdls']), ('M. Casey Rodgers', 'judge-entity-aaa', 'transferee_judge', ['mdl:3140']))
        self.assertIn('native-id bridge: CourtListener person 2755 assigned to MDL 3140 master docket == FJC jid 3043', a['basis'])
        self.assertEqual(a['source'], 'JPML pending MDL report dated 2026-09-01')

    def test_other_court_is_designation_only(self):
        aliases, _ = build.resolve({('M. Casey Rodgers', 'DE'): [group(3140, 'ded', '1:25-md-3140', 'U.S. District Court for the District of Delaware')]},
                                   {3140: receipt(3140, 'ded', '1:25-md-03140', 2755)}, BRIDGE, {})
        self.assertEqual(aliases[0]['relation'], 'sitting_by_designation_or_intercircuit_assignment')
        self.assertFalse(aliases[0]['evidence']['court_agrees'])
        self.assertEqual(aliases[0]['evidence']['entity_fjc_courts'], [FLN])
        self.assertTrue(aliases[0]['relation_note'])

    def test_no_link_without_native_id(self):
        cases = {
            'no_saved_courtlistener_docket_response': {},
            'cl_docket_has_no_assigned_to_id': {3140: receipt(3140, 'flnd', '3:25-md-03140', None, 'M. Casey Rodgers')},
            'cl_person_has_no_native_fjc_bridge': {3140: receipt(3140, 'flnd', '3:25-md-03140', 99999)},
            'master_docket_not_found_in_saved_response': {3140: receipt(3140, 'flsd', '3:25-md-03140', 2755)},
        }
        for reason, receipts in cases.items():
            aliases, unresolved = build.resolve({('M. Casey Rodgers', 'FLN'): [group(3140)]}, receipts, BRIDGE, {})
            self.assertEqual(aliases, [], reason)
            self.assertEqual(unresolved[0]['reason'], reason)

    def test_surname_disagreement_and_split_persons_withhold(self):
        aliases, unresolved = build.resolve({('Robert J. Shelby', 'FLN'): [group(3140)]}, {3140: receipt(3140, 'flnd', '3:25-md-03140', 2755)}, BRIDGE, {})
        self.assertEqual((aliases, unresolved[0]['reason']), ([], 'cl_assigned_person_surname_differs_from_printed_name'))
        aliases, unresolved = build.resolve({('M. Casey Rodgers', 'FLN'): [group(3140), group(2885, docket='3:19-md-2885')]},
                                            {3140: receipt(3140, 'flnd', '3:25-md-03140', 2755), 2885: receipt(2885, 'flnd', '3:19-md-02885', 1)}, BRIDGE, {})
        self.assertEqual((aliases, unresolved[0]['reason']), ([], 'cl_assigned_persons_differ_across_mdls'))


class PublishedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        cls.aliases = [json.loads(l) for l in (HERE / 'aliases.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        cls.unresolved = [json.loads(l) for l in (HERE / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]

    def test_envelope_and_hashes(self):
        v = self.validation
        self.assertEqual((v['schema_version'], v['status'], v['ready']), ('1', 'passed', True))
        for f in v['data_files']:
            self.assertEqual(hashlib.sha256((HERE / f['path']).read_bytes()).hexdigest(), f['sha256'])
        self.assertEqual(v['counts']['aliases'] + v['counts']['unresolved'], v['counts']['candidate_printed_names'])
        self.assertLessEqual(v['counts']['connector_receipts_in_this_folder'], 12)

    def test_rodgers_and_evidence(self):
        rodgers = next(a for a in self.aliases if a['alias'] == 'M. Casey Rodgers')
        self.assertEqual(rodgers['entity_id'], 'judge-entity-24222174cb13b7162bfcfcbc')
        self.assertEqual(rodgers['mdls'], ['mdl:2885', 'mdl:3140'])
        for a in self.aliases:
            ev = a['evidence']
            self.assertTrue(ev['surname_agrees'] and ev['fjc_jid'] and ev['fjc_nid'] and ev['cl_person_id'])
            for d in ev['dockets']:
                self.assertEqual(d['assigned_to_id'], ev['cl_person_id'])
                path = HERE.parents[1] / d['receipt_file']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), d['receipt_sha256'])

    def test_unresolved_have_reasons(self):
        self.assertEqual({u['alias'] for u in self.unresolved}, {'Joshua D. Wolson', 'John P. Bailey', 'Robert J. Shelby', 'Raag Singhal'})
        self.assertTrue(all(u['reason'] for u in self.unresolved))


if __name__ == '__main__':
    unittest.main()
