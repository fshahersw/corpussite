"""Source-profile identity/media regressions, using only small disposable fixtures."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

SPEC = importlib.util.spec_from_file_location('official_judge_subject', Path(__file__).with_name('judges.py'))
judges = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(judges)


class OfficialSourceFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='official-judge-review-')
        self.root = Path(self.temp.name)
        self.pa = self.root / 'sources/pa_judge_portraits_20260919'
        self.folder = self.root / 'sources/judge_presentation_20260918'
        self.capture_root = self.root / 'returnedfiles'
        for directory in (self.pa / 'evidence', self.pa / 'images', self.folder / 'images', self.capture_root):
            directory.mkdir(parents=True)
        self.patch = patch.multiple(judges, ROOT=self.root, PA_FOLDER=self.pa, FOLDER=self.folder,
                                   CAPTURE_ROOT=self.capture_root, DB=self.folder / 'profiles.sqlite3',
                                   DIRECTORY=self.root / 'absent.sqlite3')
        self.patch.start()
        self.url = 'https://www.pacourts.us/courts/superior-court/superior-court-judges/judge-megan-sullivan'
        self.key = 'pa-official-profile:' + hashlib.sha256(self.url.encode()).hexdigest()[:24]
        self.image_url = 'https://www.pacourts.us/Storage/media/images/20260101/sullivan.jpg'
        heading = 'President Judge Emerita Megan Sullivan'
        markdown = f'# {heading}\n![]({self.image_url})\nJanuary 2020 to December 2029\nA University, B.S., 1980\nJudicial service, 2000-present\n'
        capture = self.capture_root / 'capture.json'
        self.dump(capture, {'markdown': markdown, 'metadata': {'sourceURL': self.url, 'statusCode': 200}})
        evidence = self.pa / 'evidence/review.json'
        self.dump(evidence, {'source_profile_id': self.key, 'official_profile_url': self.url, 'name_as_published': 'Megan Sullivan',
            'capture_sha256': judges.file_digest(capture), 'image_url': self.image_url,
            'capture_checks': {key: True for key in ('capture_hash_matches_reference', 'image_url_in_saved_markdown',
                'profile_heading_in_saved_markdown', 'official_profile_url_in_metadata', 'prior_profile_section_association_verified')},
            'official_role_heading': heading, 'official_term': 'January 2020 to December 2029',
            'education_as_published': ['A University, B.S., 1980'], 'career_as_published': ['Judicial service, 2000-present']})
        self.row = {'schema_version': 'official-pa-judge-profile-source.v1', 'source_profile_id': self.key,
            'source_name': 'Pennsylvania Unified Judicial System', 'source_url': self.url,
            'canonical_identity_basis': 'exact_official_individual_profile_url', 'entity_id': None,
            'name': 'Megan Sullivan', 'profile_heading_as_published': heading, 'state': 'Pennsylvania', 'state_code': 'PA',
            'court': 'Superior Court of Pennsylvania', 'term_as_published': 'January 2020 to December 2029',
            'education_as_published': ['A University, B.S., 1980'], 'career_as_published': ['Judicial service, 2000-present'],
            'emeritus_or_emerita_in_source_heading': True, 'current_service_verified': False,
            'capture': {'path': str(capture), 'sha256': judges.file_digest(capture), 'metadata_source_url': self.url},
            'image_reference': {'url': self.image_url, 'local_path': None, 'image_bytes_verified': False},
            'identity_review_evidence_path': str(evidence), 'identity_review_evidence_sha256': judges.file_digest(evidence),
            'eligible_as_separate_source_profile': True, 'existing_entity_merge_performed': False}
        self.write_profiles([self.row])

    def tearDown(self):
        self.patch.stop()
        judges._image_verified.cache_clear()
        self.temp.cleanup()

    def dump(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')

    def write_profiles(self, rows):
        manifest = self.pa / 'official_profiles.jsonl'
        manifest.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf8')
        self.dump(self.pa / 'summary.json', {'capture_verification_errors': [], 'accepted_existing_entity_links': 0,
            'eligible_separate_official_source_profiles': len(rows), 'files': {'official_profiles.jsonl': {
                'path': str(manifest), 'sha256': judges.file_digest(manifest), 'bytes': manifest.stat().st_size}}})

    def make_image(self):
        path = self.pa / 'images/photo.png'
        Image.new('RGB', (12, 14), color='red').save(path)
        return {'id': 'pa-test-photo', 'source_profile_id': self.key, 'source_url': self.url,
                'image_source_url': self.image_url, 'path': str(path.relative_to(self.root)),
                'sha256': judges.file_digest(path), 'mime': 'image/png', 'bytes': path.stat().st_size,
                'width': 12, 'height': 14, 'verification': {'decoded': True}, 'provenance': {'source_url': self.url}}

    def write_images(self, rows, failures=None):
        failures = failures or []
        (self.pa / 'images.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf8')
        (self.pa / 'image_failures.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in failures), encoding='utf8')
        self.dump(self.pa / 'image_validation.json', {'passed': True})
        self.dump(self.pa / 'images_ready.json', {'ready': True, 'schema_version': 'official-pa-portrait-publication.v1',
            'images_manifest_sha256': judges.file_digest(self.pa / 'images.jsonl'),
            'profile_manifest_sha256': judges.file_digest(self.pa / 'official_profiles.jsonl'),
            'validation_sha256': judges.file_digest(self.pa / 'image_validation.json'),
            'failures_sha256': judges.file_digest(self.pa / 'image_failures.jsonl'),
            'profiles': 1, 'requested': len(rows) + len(failures), 'downloaded': len(rows), 'failed': len(failures)})

    def test_source_profile_without_bytes_has_no_photo_or_record_link(self):
        rows, images, receipt = judges.load_official_profiles()
        card, profile = judges.official_profile_view(rows[0])
        self.assertEqual(images, {})
        self.assertEqual(receipt['profiles'], 1)
        self.assertEqual(card['photo_url'], '')
        self.assertIsNone(card['entity_id'])
        self.assertEqual(card['id'], judges.sid('official-profile:' + self.url))
        self.assertNotIn('record_id', profile)
        self.assertNotIn('provenance_url', profile)

    def test_emerita_term_and_source_present_wording_are_preserved_without_current_claim(self):
        card, profile = judges.official_profile_view(self.row)
        self.assertEqual(card['role'], 'President Judge Emerita')
        self.assertTrue(card['emeritus_or_emerita_in_source_heading'])
        self.assertFalse(card['current_service_verified'])
        self.assertEqual(profile['term_as_published'], self.row['term_as_published'])
        self.assertEqual(profile['professional_career'][0]['text'], 'Judicial service, 2000-present')
        self.assertIn('has not been independently verified', profile['career_note'])

    def test_duplicate_source_identity_rejected(self):
        self.write_profiles([self.row, self.row])
        with self.assertRaisesRegex(ValueError, 'identity'):
            judges.load_official_profiles()

    def test_source_namesake_cannot_be_attached_to_existing_entity(self):
        row = copy.deepcopy(self.row); row['entity_id'] = 'existing-entity'
        self.write_profiles([row])
        with self.assertRaisesRegex(ValueError, 'identity'):
            judges.load_official_profiles()

    def test_changed_capture_bytes_fail_closed(self):
        with Path(self.row['capture']['path']).open('a', encoding='utf8') as source: source.write(' ')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            judges.load_official_profiles()

    def test_unreviewed_profile_field_fails_closed(self):
        row = copy.deepcopy(self.row); row['term_as_published'] = 'Current for life'
        self.write_profiles([row])
        with self.assertRaisesRegex(ValueError, 'reviewed evidence'):
            judges.load_official_profiles()

    def test_plural_roster_or_alternate_host_not_accepted_as_profile(self):
        for url in ('https://www.pacourts.us/courts/superior-court/superior-court-judges/',
                    'https://www.pacourts.us.evil.test/courts/superior-court/superior-court-judges/judge-megan-sullivan'):
            row = copy.deepcopy(self.row); row['source_url'] = url
            self.write_profiles([row])
            with self.assertRaises(ValueError): judges.load_official_profiles()

    def test_image_requires_final_readiness_marker(self):
        (self.pa / 'images.jsonl').write_text('', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'completed validation'):
            judges.load_official_profiles()

    def test_verified_image_joins_only_exact_profile_url_and_id(self):
        image = self.make_image(); self.write_images([image])
        rows, images, receipt = judges.load_official_profiles()
        card, profile = judges.official_profile_view(rows[0], images[self.key])
        self.assertEqual(receipt['images'], 1)
        self.assertEqual(card['photo_url'], '/judge-images/pa-test-photo')
        image['source_url'] += '-namesake'; self.write_images([image])
        with self.assertRaisesRegex(ValueError, 'identity link'): judges.load_official_profiles()

    def test_mislabeled_html_or_incorrect_mime_never_creates_photo(self):
        image = self.make_image(); image['mime'] = 'image/jpeg'; self.write_images([image])
        with self.assertRaisesRegex(ValueError, 'format'): judges.load_official_profiles()
        path = self.root / image['path']; path.write_bytes(b'<html>not an image</html>')
        image.update(sha256=judges.file_digest(path), bytes=path.stat().st_size, mime='image/png')
        self.write_images([image])
        with self.assertRaises((ValueError, OSError)): judges.load_official_profiles()

    def test_image_path_outside_exact_media_root_is_rejected(self):
        image = self.make_image(); other = self.root / 'outside.png'
        other.write_bytes((self.root / image['path']).read_bytes()); image['path'] = str(other)
        self.write_images([image])
        with self.assertRaisesRegex(ValueError, 'outside'): judges.load_official_profiles()

    def test_image_tampering_after_ready_is_rejected(self):
        image = self.make_image(); self.write_images([image])
        with (self.root / image['path']).open('ab') as output: output.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): judges.load_official_profiles()

    def test_failed_projection_preserves_existing_database_and_closes_temporary(self):
        judges.DB.write_bytes(b'existing projection must survive')
        original = judges.DB.read_bytes()
        with patch.object(judges, '_populate_projection', side_effect=ValueError('fixture failure')):
            with self.assertRaisesRegex(ValueError, 'fixture failure'): judges.build()
        self.assertEqual(judges.DB.read_bytes(), original)
        self.assertFalse((self.folder / 'profiles.building.sqlite3').exists())

    def test_partial_completed_photo_batch_keeps_profile_without_inventing_image(self):
        self.write_images([], [{'source_profile_id': self.key, 'reason': 'Unavailable'}])
        rows, images, receipt = judges.load_official_profiles()
        self.assertEqual((len(rows), len(images)), (1, 0))

    def test_tiny_fixture_build_keeps_same_name_identities_and_summary_layers_separate(self):
        original = self.root / 'sources/judge_entities_20260918/entities.jsonl'
        original.parent.mkdir(parents=True)
        original.write_text(json.dumps({'entity_id': 'original-person', 'name': 'Megan Sullivan', 'courts': ['Other Court'],
            'state_labels': ['Ohio'], 'judge_systems': ['state'], 'members': [], 'biography': 'Existing recorded biography.'}) + '\n', encoding='utf8')
        before = original.read_bytes()
        image = self.make_image(); self.write_images([image])
        summary = judges.build()
        self.assertEqual((summary['profiles'], summary['consolidated_profiles'], summary['official_source_profiles']), (2, 1, 1))
        self.assertEqual((summary['consolidated_profiles_with_photos'], summary['official_source_profiles_with_photos']), (0, 1))
        self.assertEqual(summary['identity_merges_added'], 0)
        self.assertEqual(original.read_bytes(), before)
        result = judges.listing({'q': 'Megan Sullivan', 'has': 'all'})
        self.assertEqual(result['total'], 2)
        profile = judges.profile(judges.sid('official-profile:' + self.url))
        self.assertIsNone(profile['entity_id']); self.assertNotIn('record_id', profile)
        found = judges.image_file(image['id'])
        self.assertEqual(found[0], (self.root / image['path']).resolve())
        self.assertEqual(found[1], 'image/png')
        with (self.root / image['path']).open('ab') as output: output.write(b'changed')
        self.assertIsNone(judges.image_file(image['id']))


if __name__ == '__main__': unittest.main(verbosity=2)
