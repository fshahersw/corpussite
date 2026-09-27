"""Supplement readiness must be reported honestly from validation envelopes without leaking paths or trusting unverified hashes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import supplements


def sha(data): return hashlib.sha256(data).hexdigest()


class SupplementStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'alpha_20260919'
        self.folder.mkdir()
        payload = b'{"a":1}\n{"a":2}\n'
        (self.folder / 'rows.jsonl').write_bytes(payload)
        (self.folder / 'README.md').write_text('# alpha', encoding='utf-8')
        self.envelope = {'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': '2026-09-19T05:00:00+00:00',
                         'data_files': [{'path': 'rows.jsonl', 'sha256': sha(payload), 'rows': 2}],
                         'counts': {'rows': 2}, 'checks': ['hash'], 'qualification': 'Test data only.', 'license_ref': 'public-domain'}
        self.write(self.envelope)

    def tearDown(self): self.tmp.cleanup()

    def write(self, envelope, folder=None):
        (folder or self.folder) .joinpath('validation.json').write_text(json.dumps(envelope), encoding='utf-8')

    def status(self): return supplements.status(self.root)

    def test_uniform_envelope_is_verified_and_path_free(self):
        result = self.status()
        item = next(i for i in result['items'] if i['name'] == 'alpha_20260919')
        self.assertEqual((item['envelope'], item['ready'], item['status']), ('uniform', True, 'passed'))
        self.assertEqual(item['data_files'][0], {'path': 'rows.jsonl', 'rows': 2, 'verified': True})
        self.assertEqual(item['counts'], {'rows': 2})
        self.assertTrue(item['readme'])
        self.assertEqual(item['license_ref'], 'public-domain')
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(result['ready'], 1)

    def test_hash_mismatch_and_missing_file_block_readiness(self):
        (self.folder / 'rows.jsonl').write_bytes(b'{"a":9}\n')
        item = self.status()['items'][0]
        self.assertFalse(item['ready'])
        self.assertEqual(item['data_files'][0]['verified'], False)
        self.assertTrue(any('hash' in issue for issue in item['issues']))
        self.envelope['data_files'][0]['path'] = 'missing.jsonl'
        self.write(self.envelope)
        item = self.status()['items'][0]
        self.assertFalse(item['ready'])
        self.assertTrue(any('missing' in issue for issue in item['issues']))

    def test_paths_outside_folder_are_never_opened(self):
        secret = self.root / 'secret.txt'
        secret.write_bytes(b'x')
        for bad in ('../secret.txt', str(secret), '/etc/passwd', 'C:\\Windows\\win.ini'):
            self.envelope['data_files'][0]['path'] = bad
            self.envelope['data_files'][0]['sha256'] = sha(b'x')
            self.write(self.envelope)
            item = self.status()['items'][0]
            self.assertFalse(item['ready'], bad)
            self.assertEqual(item['data_files'][0]['verified'], False, bad)
            self.assertNotIn('secret', json.dumps(item), bad)

    def test_legacy_envelopes_are_reported_not_trusted(self):
        legacy = self.root / 'legacy_20260918'
        legacy.mkdir()
        self.write({'status': 'passed', 'ready': True, 'counties_sha256': 'f' * 64, 'county_count': 3}, legacy)
        broken = self.root / 'broken_20260918'
        broken.mkdir()
        (broken / 'validation.json').write_text('{not json', encoding='utf-8')
        items = {i['name']: i for i in self.status()['items']}
        self.assertEqual((items['legacy_20260918']['envelope'], items['legacy_20260918']['ready'], items['legacy_20260918']['status']), ('legacy', True, 'passed'))
        self.assertEqual(items['legacy_20260918']['data_files'], [])
        self.assertIn('legacy envelope', ' '.join(items['legacy_20260918']['issues']))
        self.assertEqual((items['broken_20260918']['envelope'], items['broken_20260918']['ready']), ('invalid', False))
        self.assertFalse(items['broken_20260918']['readme'])

    def test_failed_status_or_unready_flag_is_not_ready(self):
        self.envelope['ready'] = False
        self.write(self.envelope)
        self.assertFalse(self.status()['items'][0]['ready'])
        self.envelope['ready'] = True
        self.envelope['status'] = 'failed'
        self.write(self.envelope)
        self.assertFalse(self.status()['items'][0]['ready'])

    def test_lookup_by_name(self):
        self.assertTrue(supplements.item('alpha_20260919', self.root)['ready'])
        self.assertIsNone(supplements.item('../alpha_20260919', self.root))
        self.assertIsNone(supplements.item('nope', self.root))


if __name__ == '__main__': unittest.main()
