import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge_aliases as ja  # noqa: E402

RODGERS = 'judge-entity-24222174cb13b7162bfcfcbc'
SHA = 'a' * 64


def alias_row(alias='M. Casey Rodgers', entity=RODGERS, person=2755, **over):
    row = {
        'alias': alias, 'entity_id': entity, 'display_name': 'Margaret Catharine Rodgers', 'district_code': 'FLN',
        'source': 'JPML pending MDL report dated 2026-09-01',
        'basis': 'native-id bridge: CourtListener person %s assigned to MDL 3140 master docket == FJC jid 3043' % person,
        'relation': 'transferee_judge', 'mdls': ['mdl:3140'],
        'evidence': {'cl_person_id': person, 'fjc_jid': '3043', 'fjc_nid': '1392051',
                     'entity_fjc_courts': ['U.S. District Court for the Northern District of Florida'],
                     'jpml_printed_court': {'district_code': 'FLN', 'fjc_court_name': 'U.S. District Court for the Northern District of Florida', 'cl_court_id': 'flnd'},
                     'court_agrees': True, 'surname_agrees': True, 'first_initial_agrees': True,
                     'dockets': [{'mdl': 'mdl:3140', 'docket_id': 69674950, 'assigned_to_id': person, 'receipt_sha256': SHA}]},
    }
    row.update(over)
    return row


class Fixture:
    def __init__(self, rows):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.write(rows)

    def write(self, rows, status='passed', ready=True):
        files = []
        for name, items in (('aliases.jsonl', rows), ('unresolved.jsonl', [])):
            payload = ''.join(json.dumps(r) + '\n' for r in items).encode('utf-8')
            (self.folder / name).write_bytes(payload)
            files.append({'path': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'rows': len(items)})
        (self.folder / 'validation.json').write_text(json.dumps({'schema_version': '1', 'status': status, 'ready': ready, 'data_files': files}), encoding='utf-8')

    def close(self):
        self.tmp.cleanup()


class GateTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture([alias_row()])
        self.addCleanup(self.fx.close)

    def test_loads_and_resolves(self):
        self.assertTrue(ja.status(self.fx.folder)['available'])
        self.assertEqual(ja.entity_ids_for_query('casey rodgers', self.fx.folder), {RODGERS})
        self.assertEqual(ja.entity_ids_for_query('M. Casey Rodgers', self.fx.folder), {RODGERS})
        self.assertEqual(ja.entity_ids_for_query('  CASEY,  rodgers ', self.fx.folder), {RODGERS})
        self.assertEqual(ja.entity_ids_for_query('casey smith', self.fx.folder), set())
        self.assertEqual(ja.entity_ids_for_query('', self.fx.folder), set())
        self.assertEqual(ja.entity_ids_for_query('...', self.fx.folder), set())

    def test_aliases_for_is_path_free_and_small(self):
        out = ja.aliases_for(RODGERS, self.fx.folder)
        self.assertEqual(len(out), 1)
        self.assertEqual(set(out[0]), {'alias', 'source', 'basis'})
        self.assertEqual(out[0]['alias'], 'M. Casey Rodgers')
        self.assertIn('native-id bridge', out[0]['basis'])
        self.assertEqual(ja.aliases_for('judge-entity-none', self.fx.folder), [])

    def test_tamper_closes_gate(self):
        with (self.fx.folder / 'aliases.jsonl').open('ab') as fh:
            fh.write(json.dumps(alias_row(alias='Someone Else', entity='judge-entity-evil')).encode() + b'\n')
        self.assertFalse(ja.status(self.fx.folder)['available'])
        self.assertEqual(ja.entity_ids_for_query('casey rodgers', self.fx.folder), set())
        self.assertEqual(ja.aliases_for(RODGERS, self.fx.folder), [])

    def test_not_ready_or_missing_closes_gate(self):
        self.fx.write([alias_row()], status='failed', ready=False)
        self.assertFalse(ja.status(self.fx.folder)['available'])
        self.assertFalse(ja.status(self.fx.folder / 'missing')['available'])
        self.assertEqual(ja.entity_ids_for_query('casey', self.fx.folder / 'missing'), set())

    def test_name_only_alias_rejected(self):
        bad = alias_row()
        bad['basis'] = 'same surname at the same court'
        bad['evidence'] = {'surname_agrees': True}
        self.fx.write([bad])
        st = ja.status(self.fx.folder)
        self.assertFalse(st['available'])
        self.assertIn('native_id', st['reason'])
        self.assertEqual(ja.entity_ids_for_query('casey rodgers', self.fx.folder), set())

    def test_surname_disagreement_rejected(self):
        bad = alias_row()
        bad['evidence']['surname_agrees'] = False
        self.fx.write([bad])
        self.assertFalse(ja.status(self.fx.folder)['available'])

    def test_other_court_needs_designation_relation(self):
        bad = alias_row()
        bad['evidence']['court_agrees'] = False
        self.fx.write([bad])
        self.assertFalse(ja.status(self.fx.folder)['available'])
        ok = alias_row(relation='sitting_by_designation_or_intercircuit_assignment')
        ok['evidence']['court_agrees'] = False
        self.fx.write([ok])
        self.assertTrue(ja.status(self.fx.folder)['available'])


class RealDataTests(unittest.TestCase):
    def test_rodgers_alias_in_published_layer(self):
        st = ja.status()
        self.assertTrue(st['available'], st)
        self.assertIn(RODGERS, ja.entity_ids_for_query('Casey Rodgers'))
        self.assertIn('M. Casey Rodgers', [a['alias'] for a in ja.aliases_for(RODGERS)])
        self.assertEqual(ja.entity_ids_for_query('Robert J. Shelby'), set())


if __name__ == '__main__':
    unittest.main()
