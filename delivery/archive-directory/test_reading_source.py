"""Regressions for canonical OCR selection and stale native-reading caches.

These tests create tiny temporary SQLite fixtures, never open the live indexes,
and exercise the actual reading worker and record-detail code.
"""
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import server

spec = importlib.util.spec_from_file_location('reading_source_builder', ROOT / 'scripts/build_reading_views.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

OCR_TEXT = ('Rule 12. Service of documents\n\n'
            '(a) A party must respond within 21 days after service.\n\n'
            '(b) The filing fee is $125. This text was recovered from the scanned original.')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class CanonicalReadingSourceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='canonical-reading-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'catalog').mkdir()
        (self.root / 'corpus').mkdir()
        self.native = self.root / 'corpus/native.txt'
        self.native.write_text('', encoding='utf-8')
        self.raw = self.root / 'corpus/scan.pdf'
        self.raw.write_bytes(b'%PDF-1.4\nfixture scanned document')
        self.raw_sha = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.canonical_sha = digest(OCR_TEXT)
        self.catalog = self.root / 'catalog/documents.sqlite3'
        self.directory = self.root / 'directory.sqlite3'
        self.reading = self.root / 'reading.sqlite3'
        with database(self.catalog) as db:
            db.execute('CREATE TABLE contents(id INTEGER PRIMARY KEY,text TEXT,text_sha256 TEXT)')
            db.execute('INSERT INTO contents VALUES(?,?,?)', (7, OCR_TEXT, self.canonical_sha))
        self.payload = {'kind': 'court_rule', 'text_path': 'corpus/native.txt',
                        'raw_path': 'corpus/scan.pdf', 'raw_sha256': self.raw_sha,
                        'text_file_sha256': digest(''), 'indexed_text_sha256': self.canonical_sha,
                        'index_text_status': 'searchable_ocr'}
        with database(self.directory) as db:
            db.executescript('''
                CREATE TABLE records(id TEXT PRIMARY KEY,title TEXT,group_name TEXT,
                    dataset TEXT,state TEXT,county TEXT,kind TEXT,source_url TEXT,
                    quality TEXT,content_id INTEGER,payload TEXT,inline_text TEXT,
                    original_id TEXT,text_id TEXT);
                CREATE TABLE display_groups(id TEXT PRIMARY KEY,preferred_id TEXT,
                    title TEXT,state TEXT,county TEXT,source_count INTEGER,group_basis TEXT);
                CREATE TABLE files(id TEXT PRIMARY KEY,path TEXT);
            ''')
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('fixture-rule', 'Rule 12', 'laws', 'focused', 'Example State', '',
                        'court_rule', 'https://example.gov/rules/12.pdf', 'OCR source', 7,
                        json.dumps(self.payload), '', 'raw-file', 'native-file'))
            db.executemany('INSERT INTO files VALUES(?,?)',
                           [('raw-file', 'corpus/scan.pdf'), ('native-file', 'corpus/native.txt')])
        with database(self.reading) as db:
            db.execute('CREATE TABLE reading(record_id TEXT PRIMARY KEY,text TEXT,links TEXT,notes TEXT,version TEXT)')
        self.worker_patch = patch.object(builder, 'ROOT', self.root)
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.server_patch = patch.multiple(server, ROOT=self.root, DB=self.directory,
                                          CATALOG=self.catalog, READING=self.reading,
                                          PATH_CACHE=None)
        self.server_patch.start()
        self.addCleanup(self.server_patch.stop)

    def task(self):
        return ('fixture-rule', 'Rule 12', 'https://example.gov/rules/12.pdf',
                'corpus/native.txt', 'corpus/scan.pdf', self.raw_sha, False,
                self.canonical_sha, 7)

    def save_cache(self, text, expected_sha, *, version=None):
        notes = {'parent_raw_sha256': self.raw_sha, 'parent_text_sha256': expected_sha,
                 'input_text_sha256': expected_sha, 'section_text_from_exact_derivative': False}
        with database(self.reading) as db:
            db.execute('INSERT OR REPLACE INTO reading VALUES(?,?,?,?,?)',
                       ('fixture-rule', text, '[]', json.dumps(notes), version or server.READING_VERSION))

    def assert_ocr_survives(self, text):
        self.assertIn('within 21 days', text)
        self.assertIn('$125', text)
        self.assertIn('recovered from the scanned original', text)

    def test_worker_prefers_canonical_ocr_over_empty_native_file(self):
        before = self.raw.read_bytes()
        key, result = builder.task(self.task())
        self.assertEqual(key, 'fixture-rule')
        self.assert_ocr_survives(result['text'])
        self.assertEqual(result['notes']['input_text_sha256'], self.canonical_sha)
        self.assertEqual(result['notes']['parent_text_sha256'], self.canonical_sha)
        self.assertEqual(result['notes']['canonical_content_id'], 7)
        self.assertEqual(self.native.read_text(), '')
        self.assertEqual(self.raw.read_bytes(), before)

    def test_stale_empty_native_cache_cannot_hide_canonical_ocr(self):
        self.save_cache('', digest(''))
        result = server.record_detail('fixture-rule')
        self.assert_ocr_survives(result['text'])
        self.assertGreater(result['text_characters'], 0)
        self.assertTrue(result['has_text'])
        self.assertEqual(result['reading_notes']['input_text_sha256'], self.canonical_sha)

    def test_stale_nonempty_native_cache_cannot_replace_canonical_ocr(self):
        self.save_cache('STALE NATIVE EXCERPT', digest('STALE NATIVE EXCERPT'))
        result = server.record_detail('fixture-rule')
        self.assert_ocr_survives(result['text'])
        self.assertNotIn('STALE NATIVE EXCERPT', result['text'])

    def test_matching_canonical_cache_is_reused_despite_empty_native_hash(self):
        self.save_cache(OCR_TEXT, self.canonical_sha)
        with patch.object(server, 'reading_view', side_effect=AssertionError('Valid canonical cache should be reused')):
            result = server.record_detail('fixture-rule')
        self.assertEqual(result['text'], OCR_TEXT)

    def test_older_reader_cache_is_rejected_even_when_canonical_hash_matches(self):
        self.save_cache('', self.canonical_sha, version='obsolete-reader')
        self.assert_ocr_survives(server.record_detail('fixture-rule')['text'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
