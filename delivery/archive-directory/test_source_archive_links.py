"""Links from source references to existing saved archive records must be exact, gated and read-only."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import source_archive_links as links


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ArchiveLinkTests(unittest.TestCase):
    URL_PAGE = 'https://example.gov/rules'
    URL_PORTAL = 'https://example.gov/courts'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'sources/source_archive_links_test'
        self.folder.mkdir(parents=True)
        retained = self.root / 'sources/official_courts'
        retained.mkdir(parents=True)
        (retained / 'raw.html').write_text('<html><p>Court portal</p></html>', encoding='utf-8')
        (retained / 'text.txt').write_text('Court portal', encoding='utf-8')
        self.db = self.root / 'directory.sqlite3'
        con = sqlite3.connect(self.db)
        con.executescript('CREATE TABLE records(id TEXT PRIMARY KEY, source_url TEXT, dataset TEXT, title TEXT);'
                          'CREATE TABLE files(id TEXT PRIMARY KEY, path TEXT);')
        con.executemany('INSERT INTO records VALUES(?,?,?,?)', [
            ('rec1', self.URL_PAGE, 'focused', 'Court Rules'),
            ('judge1', self.URL_PAGE, 'judge_entities', 'Doe, Jane')])
        con.executemany('INSERT INTO files VALUES(?,?)', [('orig1', 'corpus/x/raw.html'), ('text1', 'corpus/x/text.txt')])
        con.commit(); con.close()
        self.rows = [
            {'source_url': self.URL_PAGE, 'source_id': 'pld-1', 'registry_id': 'n1', 'match_basis': 'exact_source_url', 'targets': [
                {'kind': 'directory_record', 'record_id': 'rec1', 'dataset': 'focused', 'publication': 'published_focused_release',
                 'title': 'Court Rules', 'state': 'Alaska', 'county': '', 'record_kind': 'needs_content_review', 'quality': 'text_artifact_available_content_review_required',
                 'original_file_id': 'orig1', 'original_path': 'corpus/x/raw.html', 'original_sha256': 'a' * 64, 'original_bytes': 10, 'original_hash_verified': True,
                 'text_file_id': 'text1', 'text_path': 'corpus/x/text.txt', 'text_characters': 12, 'usable_text': True,
                 'captured_at': '2026-09-13T07:45:17+00:00', 'captured_at_basis': 'retrieved_at', 'review_flags': []},
                {'kind': 'judge_entity', 'record_id': 'judge1', 'entity_id': 'judge-entity-1', 'name': 'Doe, Jane', 'observation_count': 1,
                 'source_observation_ids': ['official:1'], 'current_service_verified': False, 'captured_at': '2026-09-13T07:37:36+00:00'}]},
            {'source_url': self.URL_PORTAL, 'source_id': 'pld-2', 'registry_id': 'n2', 'match_basis': 'exact_source_url', 'targets': [
                {'kind': 'retained_capture', 'version_id': 'v1', 'collection': 'official_courts', 'title': 'Courts portal',
                 'raw_path': 'sources/official_courts/raw.html', 'raw_sha256': sha(retained / 'raw.html'), 'raw_bytes': (retained / 'raw.html').stat().st_size,
                 'content_type': 'text/html', 'text_path': 'sources/official_courts/text.txt', 'text_sha256': sha(retained / 'text.txt'), 'text_characters': 12,
                 'retrieved_at': '2026-09-13T07:40:00+00:00', 'retrieval_time_basis': 'source_reported', 'index_text_status': 'searchable',
                 'publication': 'retained_capture_not_in_published_release', 'match_basis': 'exact_source_url', 'final_url': self.URL_PORTAL}]}]
        self.publish()

    def tearDown(self): self.tmp.cleanup()

    def publish(self, rows=None, status='passed', roots=('sources/official_courts',)):
        payload = '\n'.join(json.dumps(r) for r in (rows if rows is not None else self.rows)).encode('utf-8')
        (self.folder / 'links.jsonl').write_bytes(payload)
        (self.folder / 'validation.json').write_text(json.dumps({'status': status, 'ready': status == 'passed', 'links_sha256': hashlib.sha256(payload).hexdigest(),
                                                                'asset_roots': list(roots), 'generated_at': '2026-09-19T02:00:00+00:00'}), encoding='utf-8')

    def load(self): return links.load(self.folder, root=self.root)

    def test_manifest_gate_binds_links_and_urls(self):
        data = self.load()
        self.assertEqual(links.source_urls(data), {self.URL_PAGE, self.URL_PORTAL})
        self.assertEqual(links.link_counts(data)[self.URL_PAGE], 2)
        with (self.folder / 'links.jsonl').open('a') as out: out.write(' ')
        self.assertEqual(self.load(), {})
        self.publish(status='failed')
        self.assertEqual(self.load(), {})

    def test_duplicate_url_or_unknown_target_kind_fails_closed(self):
        self.publish(self.rows + [self.rows[0]])
        self.assertEqual(self.load(), {})
        bad = json.loads(json.dumps(self.rows)); bad[0]['targets'][0]['kind'] = 'invented'
        self.publish(bad)
        self.assertEqual(self.load(), {})

    def test_retained_capture_outside_registered_roots_fails_closed(self):
        bad = json.loads(json.dumps(self.rows)); bad[1]['targets'][0]['raw_path'] = 'RUNBOOK.md'
        self.publish(bad)
        self.assertEqual(self.load(), {})
        self.publish(roots=('corpus/other',))
        self.assertEqual(self.load(), {})

    def test_records_for_checks_the_live_directory(self):
        data = self.load()
        items = links.records_for(self.URL_PAGE, data, db=self.db)
        self.assertEqual([i['kind'] for i in items], ['directory_record', 'judge_entity'])
        record, judge = items
        self.assertEqual(record['original_url'], '/files/orig1')
        self.assertEqual(record['text_url'], '/api/text?id=rec1')
        self.assertEqual(record['record_id'], 'rec1')
        self.assertEqual(judge['profile_route'], 'judge/judge1')
        for item in items:
            self.assertTrue(item['qualification'])
            self.assertFalse({'original_path', 'text_path', 'raw_path'} & set(item))
        con = sqlite3.connect(self.db); con.execute("UPDATE records SET source_url='https://example.gov/other' WHERE id='rec1'"); con.execute("DELETE FROM records WHERE id='judge1'"); con.commit(); con.close()
        self.assertEqual(links.records_for(self.URL_PAGE, data, db=self.db), [])
        self.assertEqual(links.records_for(self.URL_PAGE + '/', data, db=self.db), [])

    def test_retained_capture_asset_serves_verified_bytes(self):
        data = self.load()
        item = links.records_for(self.URL_PORTAL, data, db=self.db)[0]
        self.assertEqual(item['kind'], 'retained_capture')
        self.assertEqual(item['original_url'], '/source-assets/archive:v1/original')
        self.assertEqual(item['text_url'], '/source-assets/archive:v1/text')
        body, mime, name = links.asset('archive:v1/original', data, root=self.root)
        self.assertEqual(body, b'<html><p>Court portal</p></html>')
        self.assertEqual((mime, name), ('application/octet-stream', 'raw.html'))
        self.assertEqual(links.asset('archive:v1/text', data, root=self.root)[1], 'text/plain; charset=utf-8')
        self.assertIsNone(links.asset('archive:v1/../raw', data, root=self.root))
        self.assertIsNone(links.asset('archive:missing/original', data, root=self.root))
        (self.root / 'sources/official_courts/raw.html').write_text('replaced', encoding='utf-8')
        self.assertIsNone(links.asset('archive:v1/original', data, root=self.root))

    def test_summary_counts_and_unavailable_state(self):
        summary = links.summary(self.load())
        self.assertTrue(summary['ready'])
        self.assertEqual((summary['linked_references'], summary['directory_records'], summary['judge_entities'], summary['retained_captures']), (2, 1, 1, 1))
        self.assertIn('not', summary['qualification'])
        self.assertFalse(links.summary({})['ready'])


if __name__ == '__main__': unittest.main()
