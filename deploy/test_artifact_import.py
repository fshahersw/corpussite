"""Offline artifact scan, integrity, deduplication and publication-gate regressions."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import import_artifacts as subject


class ArtifactImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'collection'
        self.local = self.root / '_transfer_scratch/supabase_export'
        self.local.mkdir(parents=True)
        self.source = self.local / 'test.jsonl'
        self.addCleanup(patch.stopall)
        patch.object(subject, 'ROOT', self.root).start()
        patch.object(subject, 'LOCAL', self.local).start()
        patch.object(subject, '_THREAD', threading.local()).start()

    def asset(self, name='one.bin', raw=b'original', route='/api/file?id=one'):
        path = self.root / name
        path.write_bytes(raw)
        return {'local_path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                'bytes': len(raw), 'url': route, 'mime': 'application/octet-stream'}

    def write(self, *artifacts):
        self.source.write_text(''.join(json.dumps({'artifacts': [a]}) + '\n' for a in artifacts), encoding='utf8')

    def test_thousand_shared_entries_review_file_once_but_preserve_all_routes(self):
        a = self.asset()
        self.write(*({**a, 'url': '/api/file?id=' + str(i)} for i in range(1000)))
        with patch.object(subject, 'reviewed_file', wraps=subject.reviewed_file) as review:
            objects, routes = subject.collect_artifacts(self.source)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(len(objects), 1)
        self.assertEqual(len(routes), 1000)

    def test_changed_hash_is_reviewed_and_canonical_route_conflict_fails(self):
        a = self.asset(route='/api/file?b=2&a=1')
        self.write(a, {**a, 'url': '/api/file?a=1&b=2', 'sha256': '0' * 64})
        with patch.object(subject, 'reviewed_file', wraps=subject.reviewed_file) as review:
            with self.assertRaisesRegex(ValueError, 'same artifact route'):
                subject.collect_artifacts(self.source)
        self.assertEqual(review.call_count, 2)

    def test_changed_byte_declaration_is_not_hidden_by_cache(self):
        a = self.asset()
        self.write(a, {**a, 'bytes': a['bytes'] + 1, 'url': '/api/file?id=two'})
        with self.assertRaisesRegex(ValueError, 'byte count changed'):
            subject.collect_artifacts(self.source)

    def test_bool_bytes_cannot_alias_cached_integer_bytes(self):
        a = self.asset(raw=b'x')
        self.write(a, {**a, 'bytes': True})
        with self.assertRaisesRegex(ValueError, 'nonnegative source byte count'):
            subject.collect_artifacts(self.source)

    def test_conflicting_same_hash_lengths_are_rejected(self):
        a = self.asset()
        b = self.asset('two.bin', b'longer original', '/api/file?id=two')
        b['sha256'] = a['sha256']
        self.write(a, b)
        with self.assertRaisesRegex(ValueError, 'same content hash'):
            subject.collect_artifacts(self.source)

    def test_missing_file_fails_without_client(self):
        a = self.asset()
        Path(a['local_path']).unlink()
        self.write(a)
        with patch.object(subject, 'Client') as client:
            with self.assertRaises(FileNotFoundError):
                subject.collect_artifacts(self.source)
            client.assert_not_called()

    def test_upload_rehashes_file_changed_after_cached_scan_before_client(self):
        a = self.asset()
        self.write(a)
        objects, _ = subject.collect_artifacts(self.source)
        Path(a['local_path']).write_bytes(b'changed!')
        with patch.object(subject, 'Client') as client:
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                subject.upload(objects[a['sha256']])
            client.assert_not_called()

    def test_stdlib_json_fallback_preserves_scan_results(self):
        self.write(self.asset())
        expected = subject.collect_artifacts(self.source)
        with patch.object(subject, 'orjson', None):
            self.assertEqual(subject.collect_artifacts(self.source), expected)

    def test_large_file_uses_resumable_streaming_without_read_bytes(self):
        a = self.asset(raw=b'x' * (subject.CHUNK_BYTES + 1))
        with patch.object(subject, 'upload_file', return_value=('hash', a['bytes'], 'key')) as resumable, \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('No large read_bytes')):
            self.assertEqual(subject.upload(a), ('hash', a['bytes'], 'key'))
        self.assertEqual(resumable.call_args.args[0], Path(a['local_path']))
        self.assertEqual(resumable.call_args.args[2], self.local / 'tus_progress')

    def run_main(self, client, *args):
        with patch.object(subject, 'Client', return_value=client), \
             patch.object(sys, 'argv', ['import_artifacts', str(self.source), *args]), \
             patch.object(sys, 'stdout', io.StringIO()):
            # This fixture owns a temporary journal and fake client; never acquires the live import lock.
            subject.main.__wrapped__()
        return json.loads(self.source.with_suffix('.artifacts.json').read_text(encoding='utf8'))

    def test_object_dedup_and_checkpoint_reuse_leave_every_route_unpublished(self):
        a = self.asset()
        self.write(a, {**a, 'url': '/api/other?id=one'})
        client = FakeClient()
        result = self.run_main(client)
        self.assertEqual(len(client.uploads), 1)
        self.assertEqual(len(client.rows), 2)
        self.assertTrue(all(r['ready'] is False for r in client.rows))
        self.assertFalse(result['ready'])
        self.assertEqual(result['publication'], 'held_pending_independent_acceptance')
        self.assertEqual(result['status'], 'uploaded')
        second = FakeClient()
        result = self.run_main(second)
        self.assertEqual(second.uploads, [])
        self.assertEqual(len(second.rows), 2)
        self.assertTrue(all(r['ready'] is False for r in second.rows))

    def test_failed_upload_is_held_and_has_no_route(self):
        self.write(self.asset())
        client = FakeClient(fail=True)
        result = self.run_main(client)
        self.assertEqual(result['status'], 'held')
        self.assertEqual(len(result['failures']), 1)
        self.assertFalse(result['ready'])
        self.assertEqual(client.rows, [])

    def test_budget_failure_never_initializes_client(self):
        self.write(self.asset())
        with patch.object(subject, 'Client') as client, \
             patch.object(sys, 'argv', ['import_artifacts', str(self.source), '--max-total-gib', '0']):
            with self.assertRaisesRegex(RuntimeError, 'ceiling'):
                subject.main.__wrapped__()
            client.assert_not_called()


class FakeClient:
    def __init__(self, fail=False):
        self.uploads = []
        self.rows = []
        self.fail = fail

    def json(self, method, path, body=None):
        return [{'id': subject.BUCKET, 'public': False}]

    def call(self, method, path, **kwargs):
        if self.fail:
            raise RuntimeError('Simulated upload failure')
        self.uploads.append((path, kwargs['data']))

    def upsert(self, table, rows):
        assert table == 'corpus_artifacts'
        self.rows.extend(rows)


if __name__ == '__main__':
    unittest.main()
