"""Offline round trip of the transfer tools on a real collection folder: package -> parts -> restore -> compare.

No network: parts stay on disk and bootstrap.fetch is replaced by a local copy. Checks byte equality by SHA-256, nanosecond
modification times, multi-part streams (parts are forced down to 1 MB) and that a damaged part is refused.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import bootstrap  # noqa: E402
import push_data  # noqa: E402

PLAN = None  # built once for both cases
WORK = push_data.SCRATCH / 'roundtrip'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Stored(unittest.TestCase):
    UNIT_KEY = 'sources/upstream_open_data_20260920'   # mostly images and archives: plain tar

    @classmethod
    def setUpClass(cls):
        global PLAN
        shutil.rmtree(WORK, ignore_errors=True)
        (WORK / 'kept').mkdir(parents=True)
        PLAN = PLAN or push_data.build_plan()
        cls.row = next(row for row in PLAN if row['key'] == cls.UNIT_KEY)
        cls.parts = []

        def keep(path, index, size, sha):
            shutil.move(str(path), WORK / 'kept' / path.name)
            cls.parts.append({'name': path.name, 'bytes': size, 'sha256': sha})
        cls.count, cls.total, cls.skipped, cls.listing_sha = push_data.package(cls.row, 1024 * 1024, keep)
        bootstrap.ROOT = WORK / 'root'
        bootstrap.SCRATCH = WORK / 'scratch'
        bootstrap.LISTINGS = WORK / 'scratch' / 'listings'
        bootstrap.fetch = lambda name, target, tag=None: (target.parent.mkdir(parents=True, exist_ok=True), shutil.copyfile(WORK / 'kept' / name, target))
        cls.unit = {'key': cls.UNIT_KEY, 'name': cls.row['name'], 'mode': cls.row['mode'], 'parts': cls.parts, 'file_listing_sha256': cls.listing_sha}

    def test_1_stream_is_cut_into_several_parts(self):
        self.assertGreater(len(self.parts), 1)
        self.assertEqual(self.count, self.row['files'])
        self.assertEqual(self.skipped, [])

    def test_2_restore_reproduces_bytes_and_times(self):
        self.assertEqual(bootstrap.install(self.unit, keep=False), 0)
        for relative, source, size in self.row['_files']:
            restored = bootstrap.ROOT / relative
            self.assertEqual(restored.stat().st_size, size, relative)
            self.assertEqual(digest(restored), digest(source), relative)
            self.assertEqual(restored.stat().st_mtime_ns, os.stat(source).st_mtime_ns, relative)
        restored_files = [path for path in (bootstrap.ROOT).rglob('*') if path.is_file()]
        self.assertEqual(len(restored_files), self.row['files'])
        self.assertFalse(any((bootstrap.SCRATCH / 'parts').glob('*')), 'parts are removed after use')

    def test_3_damaged_part_is_refused(self):
        victim = WORK / 'kept' / self.parts[0]['name']
        data = bytearray(victim.read_bytes())
        data[len(data) // 2] ^= 0xFF
        victim.write_bytes(bytes(data))
        with self.assertRaises(RuntimeError):
            bootstrap.install(self.unit, keep=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(WORK, ignore_errors=True)


class Compressed(Stored):
    UNIT_KEY = 'sources/county_filing_sources_20260919'  # text and JSON: tar.gz


if __name__ == '__main__':
    unittest.main(verbosity=2)
