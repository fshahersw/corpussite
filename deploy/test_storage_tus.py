"""Offline TUS protocol/security tests using a deterministic in-memory server."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from requests.structures import CaseInsensitiveDict
import storage_tus as subject


class Response:
    def __init__(self, code, headers=None, text=''):
        self.status_code = code
        self.headers = CaseInsensitiveDict({'Tus-Resumable': subject.VERSION, **(headers or {})})
        self.text = text


class Server:
    def __init__(self):
        self.uploads = {}
        self.calls = []
        self.patches = []
        self.mode = None
        self.foreign_location = None
        self.expire = False
        self.on_create = None
        self.fail_from_offset = None

    def request(self, method, url, *, data, headers, timeout, allow_redirects):
        assert allow_redirects is False
        assert headers['Tus-Resumable'] == '1.0.0'
        assert headers['x-upsert'] == 'true'
        self.calls.append((method, url))
        if method == 'POST':
            location = subject.ENDPOINT + '/' + str(len(self.uploads) + 1)
            self.uploads[location] = {'data': bytearray(), 'length': int(headers['Upload-Length']),
                                      'metadata': headers['Upload-Metadata']}
            if self.on_create:
                self.on_create()
            return Response(201, {'Location': self.foreign_location or location})
        item = self.uploads.get(url)
        if item is None:
            return Response(404)
        if method == 'HEAD':
            if self.expire:
                self.expire = False
                return Response(410)
            if self.mode == 'wrong_metadata':
                return Response(200, {'Upload-Offset': str(len(item['data'])), 'Upload-Length': str(item['length']),
                    'Upload-Metadata': 'bucketName ZGlmZmVyZW50'})
            return Response(200, {'Upload-Offset': str(len(item['data'])), 'Upload-Length': str(item['length']),
                                   'Upload-Metadata': item['metadata']})
        assert method == 'PATCH'
        offset = int(headers['Upload-Offset'])
        self.patches.append((offset, bytes(data)))
        assert headers['Content-Type'] == 'application/offset+octet-stream'
        if self.fail_from_offset is not None and offset >= self.fail_from_offset:
            return Response(401)
        assert offset == len(item['data'])
        if self.mode == 'no_progress':
            raise requests.Timeout('DO NOT LOG secret server URL')
        if self.mode == 'partial_timeout':
            item['data'].extend(data[:123])
            self.mode = None
            raise requests.ConnectionError('DO NOT LOG credentials')
        item['data'].extend(data)
        if self.mode == 'committed_timeout':
            self.mode = None
            raise requests.Timeout('DO NOT LOG secret server URL')
        if self.mode == 'bad_ack':
            return Response(204, {'Upload-Offset': str(len(item['data']) + 1)})
        return Response(204, {'Upload-Offset': str(len(item['data']))})


class TusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'original.bin'
        self.raw = b'abc123' * (subject.CHUNK_BYTES // 6) + b'final chunk'
        self.path.write_bytes(self.raw)
        self.asset = {'sha256': hashlib.sha256(self.raw).hexdigest(), 'bytes': len(self.raw), 'mime': 'application/octet-stream'}
        self.server = Server()
        self.factory = Mock(return_value=SimpleNamespace(session=self.server))
        self.journals = self.folder / 'private_progress'

    @property
    def journal(self):
        return self.journals / (self.asset['sha256'] + '.json')

    def upload(self):
        return subject.upload_file(self.path, self.asset, self.journals,
                                   client_factory=self.factory, sleep=lambda _: None)

    def assert_complete(self):
        self.assertEqual(bytes(list(self.server.uploads.values())[-1]['data']), self.raw)
        self.assertTrue(json.loads(self.journal.read_text())['complete'])

    def test_chunked_upload_never_reads_whole_file_and_finishes_with_head(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('No whole-file materialization')):
            result = self.upload()
        self.assertEqual(result, (self.asset['sha256'], len(self.raw), self.asset['sha256'][:2] + '/' + self.asset['sha256']))
        self.assertEqual([len(data) for _, data in self.server.patches], [subject.CHUNK_BYTES, 11])
        self.assertEqual(self.server.calls[-1][0], 'HEAD')
        self.assert_complete()

    def test_uncertain_committed_patch_head_prevents_duplicate_append(self):
        self.server.mode = 'committed_timeout'
        self.upload()
        self.assertEqual([offset for offset, _ in self.server.patches], [0, subject.CHUNK_BYTES])
        self.assertEqual([m for m, _ in self.server.calls], ['POST', 'HEAD', 'PATCH', 'HEAD', 'PATCH', 'HEAD'])
        self.assert_complete()

    def test_partial_patch_resumes_exact_uncommitted_tail_after_head(self):
        self.server.mode = 'partial_timeout'
        self.upload()
        self.assertEqual([offset for offset, _ in self.server.patches], [0, 123, subject.CHUNK_BYTES])
        self.assertEqual(len(self.server.patches[1][1]), subject.CHUNK_BYTES - 123)
        self.assert_complete()

    def test_interrupted_run_resumes_journal_with_head_and_no_new_post(self):
        self.server.fail_from_offset = subject.CHUNK_BYTES
        with self.assertRaisesRegex(subject.TusError, 'HTTP 401'):
            self.upload()
        self.assertEqual(json.loads(self.journal.read_text())['offset'], subject.CHUNK_BYTES)
        previous = len(self.server.calls)
        self.server.fail_from_offset = None
        self.upload()
        self.assertEqual(self.server.calls[previous][0], 'HEAD')
        self.assertEqual(sum(m == 'POST' for m, _ in self.server.calls), 1)
        self.assert_complete()

    def test_server_confirmed_expired_journal_creates_fresh_upload(self):
        self.server.fail_from_offset = 0
        with self.assertRaises(subject.TusError):
            self.upload()
        self.server.fail_from_offset = None
        self.server.expire = True
        self.upload()
        self.assertEqual(sum(m == 'POST' for m, _ in self.server.calls), 2)
        self.assert_complete()

    def test_foreign_creation_location_never_receives_authenticated_request(self):
        self.server.foreign_location = 'https://attacker.invalid/storage/v1/upload/resumable/stolen'
        with self.assertRaisesRegex(subject.TusError, 'Unapproved'):
            self.upload()
        self.assertEqual(self.server.calls, [('POST', subject.ENDPOINT)])

    def test_journal_foreign_location_or_pin_fails_before_client_construction(self):
        self.server.fail_from_offset = 0
        with self.assertRaises(subject.TusError):
            self.upload()
        original = json.loads(self.journal.read_text())
        for key, value in [('location', 'https://attacker.invalid/storage/v1/upload/resumable/one'),
                           ('bucket', 'wrong'), ('bytes', 2), ('origin', 'https://other.supabase.co')]:
            self.journal.write_text(json.dumps({**original, key: value}))
            self.factory.reset_mock()
            with self.subTest(key=key), self.assertRaises(subject.TusError):
                self.upload()
            self.factory.assert_not_called()

    def test_source_corruption_fails_before_client_construction(self):
        self.path.write_bytes(b'Z' + self.raw[1:])
        with self.assertRaisesRegex(subject.TusError, 'hash changed'):
            self.upload()
        self.factory.assert_not_called()

    def test_source_mutation_during_upload_fails_before_any_patch(self):
        self.server.on_create = lambda: self.path.write_bytes(b'Z' + self.raw[1:])
        with self.assertRaisesRegex(subject.TusError, 'changed during upload'):
            self.upload()
        self.assertEqual(self.server.patches, [])

    def test_incorrect_acknowledgement_holds_journal_and_resumes_by_head(self):
        self.server.mode = 'bad_ack'
        with self.assertRaisesRegex(subject.TusError, 'incorrect offset'):
            self.upload()
        state = json.loads(self.journal.read_text())
        self.assertEqual(state['offset'], 0)
        self.assertEqual(state['pending_end'], subject.CHUNK_BYTES)
        self.server.mode = None
        self.upload()
        self.assert_complete()

    def test_wrong_remote_metadata_fails_before_patch(self):
        self.server.mode = 'wrong_metadata'
        with self.assertRaisesRegex(subject.TusError, 'target metadata mismatch'):
            self.upload()
        self.assertEqual(self.server.patches, [])

    def test_unexpected_remote_offset_does_not_skip_source_bytes(self):
        self.server.fail_from_offset = 0
        with self.assertRaises(subject.TusError):
            self.upload()
        list(self.server.uploads.values())[0]['data'].extend(self.raw)
        with self.assertRaisesRegex(subject.TusError, 'disagrees with journal'):
            self.upload()

    def test_repeated_network_failure_is_bounded_and_error_omits_secrets(self):
        self.server.mode = 'no_progress'
        with self.assertRaisesRegex(subject.TusError, 'bounded retries') as error:
            self.upload()
        self.assertEqual(len(self.server.patches), subject.RETRIES)
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('http', str(error.exception))

    def test_redirect_is_rejected_without_following(self):
        self.factory.return_value.session = SimpleNamespace(request=Mock(return_value=Response(307, {'Location': 'https://attacker.invalid'})))
        with self.assertRaisesRegex(subject.TusError, 'redirect rejected'):
            self.upload()
        self.assertEqual(self.factory.return_value.session.request.call_count, 1)

    def test_location_security_rejects_wrong_hosts_paths_queries_and_userinfo(self):
        bad = [f'http://{subject.PROJECT}.supabase.co{subject.PREFIX}/one',
               f'https://{subject.PROJECT}.supabase.co.evil.test{subject.PREFIX}/one',
               f'https://user@{subject.PROJECT}.supabase.co{subject.PREFIX}/one',
               f'https://{subject.PROJECT}.supabase.co:444{subject.PREFIX}/one',
               subject.ENDPOINT + '/%2e%2e/admin', subject.ENDPOINT + '/one?token=hidden',
               subject.ENDPOINT + '/one#hidden', subject.STORAGE_ORIGIN + '/storage/v1/object/one',
               subject.ENDPOINT + '/%5cevil', subject.ENDPOINT + '/%0d%0aHeader']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(subject.TusError):
                subject.safe_location(value)
        self.assertEqual(subject.safe_location(subject.PREFIX + '/safe'), subject.ENDPOINT + '/safe')
        self.assertEqual(subject.safe_location(subject.ORIGIN + subject.PREFIX + '/safe'), subject.ORIGIN + subject.PREFIX + '/safe')


if __name__ == '__main__':
    unittest.main()
