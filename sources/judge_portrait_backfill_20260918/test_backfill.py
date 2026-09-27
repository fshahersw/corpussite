"""Identity and evidence regressions for the bounded portrait backfill."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('portrait_backfill', ROOT / 'scripts/backfill_judge_portraits_20260918.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class IdentityEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entities = {e['entity_id']: e for e in m.rows(m.ENTITIES)}

    def test_orrick_son_matches_and_father_does_not(self):
        son, facts = m.BRIDGES['William H. Orrick']
        self.assertTrue(m.entity_matches(self.entities[son], facts))
        self.assertFalse(m.entity_matches(self.entities['judge-entity-066258644a6a10e543fc71eb'], facts))

    def test_same_name_and_court_without_career_evidence_does_not_match(self):
        key, facts = m.BRIDGES['Edward M. Chen']
        unsupported = dict(self.entities[key], education=[], appointments=[], service=[])
        self.assertFalse(m.entity_matches(unsupported, facts))

    def test_navigation_dates_without_identified_biography_rejected(self):
        with self.assertRaises(ValueError):
            m.scoped_biography('Judge William H. Orrick / 1976 / May 16, 2013', 'William H. Orrick')

    def test_all_nine_links_bind_exact_verified_evidence_and_image(self):
        accepted = m.rows(m.OUT / 'accepted_links.jsonl')
        self.assertEqual(len(accepted), 9)
        self.assertEqual(len({a['entity_id'] for a in accepted}), 9)
        for row in accepted:
            proof_path = ROOT / row['identity_evidence_path']
            self.assertEqual(m.sha(proof_path), row['identity_evidence_sha256'])
            self.assertEqual(m.sha(ROOT / row['verified_copy_path']), row['sha256'])
            self.assertEqual(m.sha(row['absolute_path']), row['sha256'])
            proof = json.loads(proof_path.read_text(encoding='utf-8'))
            self.assertEqual(proof['matched_entity_id'], row['entity_id'])
            capture = proof['official_profile_capture']
            self.assertEqual(m.sha(ROOT / capture['path']), capture['sha256'])
            self.assertGreaterEqual(len(proof['facts']), 2)
            self.assertFalse(proof['facial_identification_used'])
            self.assertFalse(proof['current_service_verified'])

    def test_decorative_gavel_is_not_a_new_portrait(self):
        scan = json.loads((m.OUT / 'additional_asset_scan.json').read_text(encoding='utf-8'))
        self.assertEqual(scan['eligible_new_portrait_candidates'], [])
        self.assertEqual(len(scan['previous_exclusions_retained']), 1)
        self.assertEqual(scan['previous_exclusions_retained'][0]['exclusion_reason'], 'Decorative graphic is not a judge photograph')


if __name__ == '__main__': unittest.main()
