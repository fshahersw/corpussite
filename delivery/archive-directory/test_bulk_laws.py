"""Offline adapter regression checks; never opens a production corpus database."""
import hashlib
import gc
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import bulk_laws


# Mirror the importer's actual tables, including the external-content FTS index.
SCHEMA = """
CREATE TABLE records(id TEXT UNIQUE NOT NULL,source_id TEXT,title TEXT,state TEXT,kind TEXT,source_url TEXT,text TEXT,payload TEXT,file_id TEXT,row_index INTEGER,content_hash TEXT,snapshot TEXT,citation TEXT,status TEXT,UNIQUE(file_id,row_index));
CREATE TABLE files(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,bytes INTEGER,rows INTEGER,state TEXT,kind TEXT,indexed_at TEXT,schema_json TEXT);
CREATE TABLE import_meta(key TEXT PRIMARY KEY,value TEXT);
CREATE VIRTUAL TABLE records_fts USING fts5(title,citation,text,content='records',content_rowid='rowid',tokenize='unicode61');
"""


class BulkLawAdapterChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bulk-law-adapter-test-')
        self.addCleanup(self.tmp.cleanup)
        # SQLite context managers commit/rollback but do not close connections.
        # Collect adapter-local connections before Windows removes the fixture.
        self.addCleanup(gc.collect)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root / 'sources/open_us_law_20260918'
        self.folder.mkdir(parents=True)
        self.db = self.folder / 'catalog.sqlite3'
        counties = self.root / 'delivery/focused_legal_corpus/counties/counties.jsonl'
        counties.parent.mkdir(parents=True)
        counties.write_text('\n'.join(json.dumps(x) for x in [
            {'usps': 'NY', 'state': 'New York'},
            {'usps': 'WA', 'state': 'Washington'},
        ]), encoding='utf-8')
        patcher = patch.multiple(bulk_laws, ROOT=self.root, FOLDER=self.folder, DB=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        bulk_laws.state_names.cache_clear()
        self.addCleanup(bulk_laws.state_names.cache_clear)
        self.raw_ny_text = 'Section 1.\\nA party shall provide "written notice".\\nEffective September 1.'
        self.records = [
            ('ny-statute', 'NY-ACT-1', 'Notice requirement', 'NY', 'statutes', self.raw_ny_text, 'ny-statutes', 'N.Y. Example Law § 1', 'in_force'),
            ('ny-rule', 'NY-RULE-2', 'Reserved procedure', 'NY', 'court_rules', 'This rule is reserved. Literal token \\n remains unchanged.', 'ny-rules', '22 NYCRR § 2', 'reserved'),
            ('ny-guidance', 'NY-GUIDE-3', 'Rescinded notice', 'NY', 'guidance', 'This notice was rescinded.', 'ny-guidance', 'Publisher Bulletin 3', 'rescinded'),
            ('wa-law', 'WA-ACT-1', 'Washington notice', 'WA', 'statutes', 'Written prompt notice is required.', 'wa-statutes', 'RCW Example 1', 'repealed'),
            ('federal-rule', 'FED-RULE-1', 'Federal procedure', 'FEDERAL', 'regulations', 'A federal hearing shall be held.', 'federal-regulations', '1 CFR Example', 'in_force'),
        ]
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript(SCHEMA)
            for rid, sid, title, state, kind, text, fid, citation, status in self.records:
                artifact = self.folder / (fid + '.parquet')
                # Adapter tests need bytes in a registered file, not a Parquet engine.
                blob = ('fixture original ' + fid).encode()
                artifact.write_bytes(blob)
                c.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)', (
                    fid, artifact.relative_to(self.root).as_posix(), hashlib.sha256(blob).hexdigest(), len(blob), 1, state, kind, '2026-09-18T00:00:00Z', '{}'))
                payload = {'publisher_note': 'Preserve this exact source assertion', 'effective_date': '2026-09-01'}
                c.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    rid, sid, title, state, kind, 'https://example.gov/' + rid, text, json.dumps(payload), fid, 0, hashlib.sha256(text.encode()).hexdigest(), 'v2026.08', citation, status))
            c.execute("INSERT INTO records_fts(records_fts) VALUES ('rebuild')")
        self.write_ready(True)
        (self.folder / 'import_summary.json').write_text(json.dumps({'records': 5, 'snapshot': 'v2026.08'}), encoding='utf-8')

    def write_ready(self, value):
        (self.folder / 'ready.json').write_text(json.dumps({'ready': value, 'records': 5}), encoding='utf-8')

    def test_readiness_requires_json_boolean_true(self):
        self.assertTrue(bulk_laws.info()['ready'])
        for value in (False, None, 0, 1, 'true', 'false'):
            with self.subTest(value=value):
                self.write_ready(value)
                self.assertEqual(bulk_laws.info(), {'ready': False, 'records': 0})
        (self.folder / 'ready.json').write_text('{}', encoding='utf-8')
        self.assertEqual(bulk_laws.info(), {'ready': False, 'records': 0})
        (self.folder / 'ready.json').unlink()
        pending=bulk_laws.info();self.assertIs(pending['ready'],False);self.assertEqual(pending['records'],0)
        self.assertEqual(pending['snapshot'],'v2026.08')
        self.write_ready(True)
        self.db.unlink()
        pending=bulk_laws.info();self.assertIs(pending['ready'],False);self.assertEqual(pending['records'],0)

    def test_not_ready_is_zero_counter_stub_without_database_reads(self):
        self.write_ready(False)
        with patch.object(bulk_laws, 'connect', side_effect=AssertionError('Not-ready adapter opened database')):
            self.assertEqual(bulk_laws.count({}), 0)
            self.assertEqual(bulk_laws.query({}, 0, 10), [])
            self.assertEqual(bulk_laws.filters(), ([], []))
            self.assertIsNone(bulk_laws.detail('ny-statute', full=True))
            self.assertIsNone(bulk_laws.artifact('ny-statutes'))

    def test_state_codes_and_names_select_identical_records(self):
        for code, name, count in [('NY', 'New York', 3), ('WA', 'Washington', 1), ('FEDERAL', 'Federal', 1)]:
            with self.subTest(state=code):
                self.assertEqual(bulk_laws.count({'state': code}), count)
                self.assertEqual(bulk_laws.query({'state': code}, 0, 10), bulk_laws.query({'state': name}, 0, 10))
                self.assertTrue(all(r['state'] == name for r in bulk_laws.query({'state': name}, 0, 10)))
        self.assertEqual(bulk_laws.state_names()['PR'], 'Puerto Rico')
        states, kinds = bulk_laws.filters()
        self.assertEqual(set(states), {'Federal', 'New York', 'Washington'})
        self.assertEqual(set(kinds), {'statutes', 'court_rules', 'guidance', 'regulations'})

    def test_group_dataset_and_county_eligibility_boundaries(self):
        for params in ({}, {'group': 'all'}, {'group': 'laws'}, {'dataset': 'open_us_law'}, {'group': 'laws', 'dataset': 'open_us_law'}):
            with self.subTest(allowed=params):
                self.assertTrue(bulk_laws.eligible(params))
                self.assertEqual(bulk_laws.count(params), 5)
        for params in ({'group': 'counties'}, {'group': 'judges'}, {'group': 'federal'}, {'dataset': 'seeger'}, {'county': 'Example County'}):
            with self.subTest(excluded=params):
                self.assertFalse(bulk_laws.eligible(params))
                self.assertEqual(bulk_laws.count(params), 0)
                self.assertEqual(bulk_laws.query(params, 0, 10), [])

    def test_fts_search_is_a_quoted_phrase_and_escapes_user_quotes(self):
        self.assertEqual([r['id'] for r in bulk_laws.query({'q': 'written notice'}, 0, 10)], ['ny-statute'])
        self.assertEqual(bulk_laws.count({'q': '"written notice"'}), 1)
        clause, args = bulk_laws.clauses({'q': 'written "notice" OR hearing'})
        self.assertIn('records_fts MATCH ?', clause)
        self.assertEqual(args, ['"written ""notice"" OR hearing"'])
        self.assertEqual(bulk_laws.count({'q': 'written "notice" OR hearing'}), 0)
        self.assertEqual(bulk_laws.count({'q': '" OR *'}), 0)
        self.assertEqual(bulk_laws.count({'q': 'written notice', 'state': 'Washington'}), 0)
        self.assertEqual(bulk_laws.count({'q': '   '}), 5)

    def test_ny_display_newlines_preserve_database_text_and_hash(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            before = c.execute('SELECT text,content_hash FROM records WHERE id=?', ('ny-statute',)).fetchone()
        result = bulk_laws.detail('ny-statute', full=True)
        expected = self.raw_ny_text.replace('\\n', '\n')
        self.assertEqual(result['text'], expected)
        self.assertTrue(result['reading_notes']['publisher_newline_escapes_rendered'])
        self.assertTrue(result['reading_notes']['original_preserved'])
        self.assertFalse(result['reading_notes']['currency_verified'])
        self.assertEqual(result['reading_notes']['source_text_sha256'], before[1])
        self.assertEqual(result['reading_notes']['display_text_sha256'], hashlib.sha256(expected.encode()).hexdigest())
        self.assertNotEqual(result['reading_notes']['display_text_sha256'], before[1])
        with closing(sqlite3.connect(self.db)) as c, c:
            after = c.execute('SELECT text,content_hash FROM records WHERE id=?', ('ny-statute',)).fetchone()
        self.assertEqual(before, after)
        self.assertIn('\\n', after[0])
        rule = bulk_laws.detail('ny-rule', full=True)
        self.assertIn('\\n', rule['text'])
        self.assertFalse(rule['reading_notes']['publisher_newline_escapes_rendered'])

    def test_status_citation_source_identity_and_attribution_retained(self):
        for key, status, citation in [('ny-rule', 'reserved', '22 NYCRR § 2'), ('ny-guidance', 'rescinded', 'Publisher Bulletin 3'), ('wa-law', 'repealed', 'RCW Example 1')]:
            with self.subTest(key=key):
                result = bulk_laws.detail(key)
                metadata = result['metadata']
                self.assertEqual(metadata['status'], status)
                self.assertEqual(metadata['citation'], citation)
                self.assertEqual(metadata['snapshot'], 'v2026.08')
                self.assertEqual(metadata['row_index'], 0)
                self.assertEqual(metadata['publisher_record']['publisher_note'], 'Preserve this exact source assertion')
                self.assertIn('Vaquill AI', metadata['attribution'])
                self.assertIn('CC BY 4.0', metadata['attribution'])
                self.assertIn('verify edition and current legal status', result['quality'])
                self.assertEqual(result['source_url'], 'https://example.gov/' + key)
                self.assertTrue(metadata['file']['sha256'])
                self.assertTrue(metadata['source_id'])
        self.assertIsNone(bulk_laws.detail('unknown'))

    def test_pagination_and_preview_do_not_hide_full_text(self):
        self.assertEqual([r['id'] for r in bulk_laws.query({}, 1, 2)], ['ny-rule', 'ny-guidance'])
        self.assertEqual(bulk_laws.query({}, 0, 0), [])
        long_text = 'A' * 60001
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE records SET text=?,content_hash=? WHERE id=?', (long_text, hashlib.sha256(long_text.encode()).hexdigest(), 'wa-law'))
        preview = bulk_laws.detail('wa-law')
        full = bulk_laws.detail('wa-law', full=True)
        self.assertTrue(preview['text_truncated'])
        self.assertEqual(len(preview['text']), 60000)
        self.assertEqual(preview['text_characters'], 60001)
        self.assertFalse(full['text_truncated'])
        self.assertEqual(full['text'], long_text)

    def test_artifacts_require_registered_id_and_scoped_existing_parquet(self):
        valid = self.folder / 'ny-statutes.parquet'
        self.assertEqual(bulk_laws.artifact('ny-statutes'), valid)
        self.assertIsNone(bulk_laws.artifact(str(valid)))
        self.assertIsNone(bulk_laws.artifact("ny-statutes' OR 1=1 --"))
        unlisted = self.folder / 'unlisted.parquet'
        unlisted.write_bytes(b'unregistered')
        self.assertIsNone(bulk_laws.artifact('unlisted'))
        outside = self.root / 'outside.parquet'
        outside.write_bytes(b'outside verified dataset folder')
        wrong_type = self.folder / 'not-parquet.txt'
        wrong_type.write_text('text', encoding='utf-8')
        for path in [str(outside), 'sources/open_us_law_20260918/../../outside.parquet', str(wrong_type), str(self.folder / 'missing.parquet'), str(self.folder)]:
            with self.subTest(path=path):
                with closing(sqlite3.connect(self.db)) as c, c:
                    c.execute('UPDATE files SET path=? WHERE id=?', (path, 'ny-statutes'))
                self.assertIsNone(bulk_laws.artifact('ny-statutes'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
