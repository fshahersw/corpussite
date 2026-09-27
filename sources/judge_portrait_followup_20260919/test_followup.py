from pathlib import Path
import json
import unittest
from finalize_followup import HERE, ROOT, BRIDGES, reviewed_match, validate_image
from finalize import rows, sha


class FollowupEvidenceTests(unittest.TestCase):
    def test_two_expected_existing_identities_have_three_facts_each(self):
        accepted=rows(HERE/'accepted_links.jsonl')
        self.assertEqual({r['entity_id'] for r in accepted},{x[0] for x in BRIDGES.values()})
        self.assertEqual(len(accepted),2)
        for row in accepted:
            evidence=(ROOT/row['identity_evidence_path']).read_bytes()
            self.assertEqual(sha(evidence),row['identity_evidence_sha256'])
            proof=json.loads(evidence)
            entity,_=reviewed_match(proof['source_profile'],[proof['entity_record']],BRIDGES[row['name']])
            self.assertEqual(entity['entity_id'],row['entity_id'])
            self.assertEqual(len(proof['facts']),3)

    def test_same_name_with_other_career_is_rejected(self):
        row=rows(HERE/'accepted_links.jsonl')[1]
        proof=json.loads((ROOT/row['identity_evidence_path']).read_text(encoding='utf-8'))
        changed={**proof['entity_record'],'professional_career':['Different career history']}
        with self.assertRaises(ValueError): reviewed_match(proof['source_profile'],[changed],BRIDGES[row['name']])

    def test_original_media_receipts_match_decode(self):
        for image in rows(HERE/'images.jsonl'):
            data=(ROOT/image['path']).read_bytes()
            receipt=json.loads((ROOT/image['image_receipt']['path']).read_text(encoding='utf-8'))
            record={'url':image['image_source_url'],'status':'downloaded','sha256':image['sha256']}
            result=validate_image(data,record,receipt)
            self.assertEqual(result['mime'],image['mime'])
            self.assertEqual(result['width'],image['width'])

    def test_validation_gate_binds_all_published_manifest_bytes(self):
        gate=json.loads((HERE/'validation.json').read_text())
        self.assertTrue(gate['passed'])
        for name,evidence in gate['files'].items():
            data=(HERE/name).read_bytes()
            self.assertEqual(sha(data),evidence['sha256'])
            self.assertEqual(len(data),evidence['bytes'])


if __name__=='__main__':unittest.main()
