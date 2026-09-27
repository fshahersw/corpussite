"""Actual cross-process lock tests; these never instantiate a remote client."""
from contextlib import redirect_stderr
import importlib
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import import_lock
from import_lock import ImportWriterLock, ImportWriterBusy, import_writer

HERE = Path(__file__).resolve().parent
HOLDER = '''
import sys
from import_lock import ImportWriterLock
with ImportWriterLock(sys.argv[1]):
    print('locked', flush=True)
    sys.stdin.readline()
'''
CONTENDER = '''
import sys
from import_lock import ImportWriterLock, ImportWriterBusy
try:
    with ImportWriterLock(sys.argv[1]): pass
except ImportWriterBusy:
    raise SystemExit(7)
'''


class ImportWriterLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'shared.lock'
        self.addCleanup(self.temp.cleanup)

    def contender(self):
        return subprocess.run([sys.executable, '-c', CONTENDER, str(self.path)], cwd=HERE,
                              capture_output=True, text=True, timeout=10)

    def test_second_process_is_rejected_and_existing_file_survives_release(self):
        self.path.write_text('Old metadata is not ownership.', encoding='utf8')
        with ImportWriterLock(self.path):
            before = self.path.stat().st_ino
            self.assertEqual(self.contender().returncode, 7)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.stat().st_ino, before)
        self.assertEqual(self.path.read_text(encoding='utf8'), 'Old metadata is not ownership.')
        self.assertEqual(self.contender().returncode, 0)

    def test_os_releases_lock_when_holder_is_terminated(self):
        child = subprocess.Popen([sys.executable, '-u', '-c', HOLDER, str(self.path)], cwd=HERE,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            self.assertEqual(self.contender().returncode, 7)
            child.terminate()
            child.communicate(timeout=10)
            self.assertEqual(self.contender().returncode, 0)
            self.assertTrue(self.path.exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    def test_same_process_cannot_open_a_second_writer_and_exception_releases(self):
        with self.assertRaisesRegex(ValueError, 'test failure'):
            with ImportWriterLock(self.path):
                with self.assertRaises(ImportWriterBusy):
                    with ImportWriterLock(self.path):
                        self.fail('Second writer acquired the lock')
                raise ValueError('test failure')
        with ImportWriterLock(self.path) as lock:
            self.assertFalse(os.get_inheritable(lock._fd))

    def test_all_four_entrypoints_fail_before_argument_parsing_or_client_creation(self):
        with patch.object(import_lock, 'LOCK_PATH', self.path), ImportWriterLock(self.path):
            for name in ('import_catalog', 'import_artifacts', 'import_context', 'import_law_outline'):
                module = importlib.import_module(name)
                with self.subTest(module=name), patch.object(module, 'Client') as client, \
                     patch.object(sys, 'argv', [name, '--help']), redirect_stderr(io.StringIO()) as errors:
                    with self.assertRaises(SystemExit) as stopped:
                        module.main()
                    self.assertEqual(stopped.exception.code, 2)
                    self.assertIn('Another migration writer', errors.getvalue())
                    client.assert_not_called()

    def test_decorator_preserves_return_value_and_releases_on_completion(self):
        @import_writer
        def operation(value):
            return value + 1
        with patch.object(import_lock, 'LOCK_PATH', self.path):
            self.assertEqual(operation(4), 5)
            with ImportWriterLock(self.path):
                pass

    def test_catalog_default_is_one_worker_without_remote_operations(self):
        module = importlib.import_module('import_catalog')
        folder = Path(self.temp.name)
        source = folder / 'sample.jsonl'
        raw = (json.dumps({'id': 'one', 'dataset': 'test', 'category': 'court_rules'}) + '\n').encode()
        source.write_bytes(raw)
        source.with_suffix('.dataset.json').write_text(json.dumps({'id': 'test', 'expected_records': 1,
            'export_jsonl_sha256': hashlib.sha256(raw).hexdigest()}), encoding='utf8')
        executor = concurrent.futures.ThreadPoolExecutor
        with patch.object(module, 'LOCAL', folder), patch.object(import_lock, 'LOCK_PATH', self.path), \
             patch.object(module, 'Client') as client, patch.object(sys, 'argv', ['import_catalog', str(source)]), \
             patch.object(module.sqlite3, 'connect'), \
             patch.object(module.concurrent.futures, 'ThreadPoolExecutor', wraps=executor) as pool, \
             patch.object(sys, 'stdout', io.StringIO()):
            client.return_value.call.return_value = SimpleNamespace(headers={'Content-Range': '0-0/1'})
            module.main()
            self.assertEqual(pool.call_args.kwargs['max_workers'], 1)

    def test_artifact_workers_are_capped_at_four_without_remote_operations(self):
        module = importlib.import_module('import_artifacts')
        folder = Path(self.temp.name)
        source = folder / 'sample.jsonl'
        source.write_text('{}\n', encoding='utf8')
        executor = concurrent.futures.ThreadPoolExecutor
        with patch.object(module, 'LOCAL', folder), patch.object(import_lock, 'LOCK_PATH', self.path), \
             patch.object(module, 'Client') as client, \
             patch.object(module.sqlite3, 'connect'), \
             patch.object(sys, 'argv', ['import_artifacts', str(source), '--workers', '100']), \
             patch.object(module.concurrent.futures, 'ThreadPoolExecutor', wraps=executor) as pool, \
             patch.object(sys, 'stdout', io.StringIO()):
            client.return_value.json.return_value = []
            module.main()
            self.assertEqual(pool.call_args.kwargs['max_workers'], 4)


if __name__ == '__main__': unittest.main()
