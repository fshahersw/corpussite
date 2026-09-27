"""Source-preserving presentation boundaries for the personal research MVP."""
import unittest
import json
import categories
import judges


class PresentationBoundaries(unittest.TestCase):
    def test_categories_include_variants_and_leave_unknowns_unclassified(self):
        self.assertEqual(categories.classify('statutory_provision'), 'statutes')
        self.assertEqual(categories.classify('statutes'), 'statutes')
        self.assertEqual(categories.classify('local_rules; ocr_text'), 'rules')
        self.assertEqual(categories.classify('needs_content_review'), 'other')
        self.assertEqual(categories.classify('court_form_or_other_document'), 'forms')
        self.assertEqual(categories.condition('made_up', ['statutes']), ('0', []))

    def test_preferred_education_is_a_display_choice_not_identity_merge(self):
        entity = {'best_profile_member': 'rich', 'education': ['One degree', 'A variant'],
                  'field_provenance': {'education': [{'member_key': 'rich', 'value': 'One degree'},
                                                    {'member_key': 'other', 'value': 'A variant'}]}}
        self.assertEqual(judges.display_entries(entity, 'education'), [{'text': 'One degree'}])
        self.assertEqual(entity['education'], ['One degree', 'A variant'])

    def test_profiles_preserve_outcomes_and_do_not_claim_current_service(self):
        result = judges.listing({'q':'Sabraw'})
        self.assertEqual(result['total'], 1)
        profile = judges.profile(result['items'][0]['id'])
        self.assertEqual(len(profile['analyses']), 449)
        self.assertFalse(profile['current_service_verified'])
        self.assertEqual(len(profile['education']), 3)
        grants = [a for a in profile['analyses'] if a.get('motion_type')=='motion to dismiss' and a.get('outcome')=='granted']
        self.assertTrue(any(a.get('value')==262 and a.get('limitations') for a in grants))
        self.assertNotIn('field_provenance', profile)

    def test_photos_match_verified_manifest_and_remain_allowlisted(self):
        result = judges.listing({'has':'photo','limit':60})
        portraits=[json.loads(line) for line in (judges.FOLDER/'portraits.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        # The hash-gated PA overlay adds 29 separate official profiles/photos;
        # see sources/pa_judge_portraits_20260919/images_ready.json.
        official=[json.loads(line) for line in (judges.PA_FOLDER/'images.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        self.assertEqual(result['total'], len({row['entity_id'] for row in portraits})+len(official))
        self.assertEqual(result['total'],51)
        self.assertGreaterEqual(result['total'],5)
        self.assertEqual({row['entity_id'] for row in result['items'] if row.get('entity_id')},{row['entity_id'] for row in portraits})
        self.assertEqual({row['source_profile_id'] for row in result['items'] if row.get('profile_layer')=='official_source'},{row['source_profile_id'] for row in official})
        for row in result['items']:
            path,mime = judges.image_file(row['photo_url'].split('/')[-1])
            if row.get('profile_layer')=='official_source':
                self.assertTrue(path.is_relative_to(judges.PA_FOLDER/'images'))
                self.assertEqual(mime,next(item['mime'] for item in official if item['source_profile_id']==row['source_profile_id']))
            else:
                self.assertTrue(path.is_relative_to(judges.FOLDER/'images'))
                self.assertEqual(mime,'image/webp')
        self.assertIsNone(judges.image_file('../profiles.sqlite3'))

    def test_directory_scope_and_literal_search(self):
        full = judges.listing({'has':'all'})
        detail = judges.listing({'has':'details'})
        self.assertEqual(full['total'],10698)  # 10,669 consolidated + 29 separate official PA source profiles.
        self.assertEqual(detail['total'],5971)
        self.assertLess(detail['total'],full['total'])
        self.assertEqual(judges.listing({'q':"%' OR 1=1 --"})['total'],0)
        self.assertTrue(all('California' in r['states'] for r in judges.listing({'state':'California'})['items']))


if __name__=='__main__': unittest.main()
