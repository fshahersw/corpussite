"""Display-group importer safety checks, using only temporary files and a fake API."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import import_groups


class FakeClient:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail_batch = None
        self.extra_count = 0
        self.on_upsert = None

    def upsert(self, table, rows):
        self.calls.append((table, rows))
        if len(self.calls) == self.fail_batch:
            raise RuntimeError('write interrupted')
        if self.on_upsert:
            self.on_upsert()
        for row in rows:
            self.rows[row['id']] = row

    def call(self, method, path, **kwargs):
        assert method == 'GET' and path.startswith('/rest/v1/corpus_display_groups?')
        assert kwargs['headers']['Prefer'] == 'count=exact'
        return type('Response', (), {'headers': {'Content-Range': f'0-0/{len(self.rows) + self.extra_count}'}})()


class ImportGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'core/groups.table.jsonl'
        self.path.parent.mkdir()
        self.plan = self.root / 'migration_plan.json'
        self.client = FakeClient()
        self.rows = [{'id': f'doc:{i}', 'preferred_id': f'record:{i}',
                     'metadata': {'id': f'doc:{i}', 'preferred_id': f'record:{i}',
                                  'member_ids': [f'record:{i}'], 'source_count': 1}}
                    for i in range(5)]
        self.write_source()

    def write_source(self):
        self.path.write_text(''.join(json.dumps(row) + '\n' for row in self.rows), encoding='utf-8')
        self.descriptor = {'path': 'core/groups.table.jsonl', 'table': 'corpus_display_groups',
                           'rows': len(self.rows), 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
        self.plan.write_text(json.dumps({'project': import_groups.PROJECT, 'groups': self.descriptor}), encoding='utf-8')

    def run_import(self, **kwargs):
        return import_groups.import_file(self.path, plan=self.plan, export_root=self.root,
                                         client_factory=lambda: self.client, batch_rows=2, **kwargs)

    def test_small_idempotent_batches_and_readiness_untouched(self):
        receipt = self.run_import()
        self.assertEqual([len(rows) for _, rows in self.client.calls], [2, 2, 1])
        self.assertEqual(receipt['remote_count'], 5)
        self.assertEqual(receipt['readiness'], 'unchanged')
        self.assertTrue(all(table == 'corpus_display_groups' for table, _ in self.client.calls))
        self.assertEqual(self.client.rows['doc:0'], self.rows[0])
        self.run_import()
        self.assertEqual(len(self.client.calls), 3)

    def test_interrupted_batch_is_retried_but_completed_batch_is_not(self):
        self.client.fail_batch = 2
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.run_import()
        self.assertFalse(self.path.with_suffix('.import.json').exists())
        self.client.fail_batch = None
        self.run_import()
        self.assertEqual([batch[0]['id'] for _, batch in self.client.calls], ['doc:0', 'doc:2', 'doc:2', 'doc:4'])
        self.assertEqual(len(self.client.rows), 5)

    def test_tampered_export_never_constructs_client(self):
        self.path.write_text(self.path.read_text() + '\n')
        with patch.object(import_groups, 'Client', side_effect=AssertionError('remote access')):
            with self.assertRaisesRegex(ValueError, 'hash/count'):
                import_groups.import_file(self.path, plan=self.plan, export_root=self.root)

    def test_duplicate_identity_fails_before_any_write(self):
        self.rows[4] = self.rows[0]
        self.write_source()
        with self.assertRaisesRegex(ValueError, 'Duplicate group'):
            self.run_import()
        self.assertFalse(self.client.calls)

    def test_preferred_member_mismatch_fails_before_any_write(self):
        self.rows[4]['preferred_id'] = 'not-a-member'
        self.write_source()
        with self.assertRaisesRegex(ValueError, 'identity|member'):
            self.run_import()
        self.assertFalse(self.client.calls)

    def test_identity_only_members_do_not_inflate_source_count(self):
        self.rows[0]['metadata'].update(member_ids=['record:0', 'judge:identity'],
                                        retained_members=2, source_count=1)
        self.write_source()
        self.run_import()
        self.assertEqual(self.client.rows['doc:0']['metadata']['source_count'], 1)
        self.assertEqual(self.client.rows['doc:0']['metadata']['retained_members'], 2)

    def test_invalid_source_or_retained_counts_fail_before_write(self):
        for change in ({'source_count': 2}, {'source_count': -1}, {'retained_members': 2}):
            with self.subTest(change=change):
                original = dict(self.rows[0]['metadata'])
                self.rows[0]['metadata'].update(change)
                self.write_source()
                with self.assertRaisesRegex(ValueError, 'count'):
                    self.run_import()
                self.rows[0]['metadata'] = original
        self.assertFalse(self.client.calls)

    def test_remote_count_mismatch_does_not_create_success_receipt(self):
        self.client.extra_count = 1
        with self.assertRaisesRegex(RuntimeError, 'Remote count'):
            self.run_import()
        self.assertFalse(self.path.with_suffix('.import.json').exists())

    def test_change_during_upload_fails_final_hash_check(self):
        self.client.on_upsert = lambda: self.path.write_bytes(self.path.read_bytes().replace(b'doc:0', b'doc:X'))
        with self.assertRaisesRegex((RuntimeError, ValueError), 'changed|hash/count|JSON'):
            self.run_import()
        self.assertFalse(self.path.with_suffix('.import.json').exists())

    def test_explicit_pinned_receipt_works_without_plan(self):
        receipt = self.root / 'groups.reviewed.json'
        receipt.write_text(json.dumps(self.descriptor), encoding='utf-8')
        self.plan.unlink()
        result = self.run_import(receipt=receipt)
        self.assertEqual(result['rows'], 5)

    def test_wrong_project_or_table_or_path_rejected(self):
        for change in ({'table': 'corpus_datasets'}, {'path': '../groups.table.jsonl'}):
            with self.subTest(change=change):
                self.plan.write_text(json.dumps({'project': import_groups.PROJECT, 'groups': self.descriptor | change}))
                with self.assertRaises(ValueError):
                    self.run_import()
        self.plan.write_text(json.dumps({'project': 'another-project', 'groups': self.descriptor}))
        with self.assertRaisesRegex(ValueError, 'project'):
            self.run_import()
        self.assertFalse(self.client.calls)

    def test_missing_pin_fails_closed(self):
        self.plan.unlink()
        with self.assertRaisesRegex(ValueError, 'reviewed|plan'):
            self.run_import()

    def test_single_writer_decorator_is_present(self):
        self.assertTrue(hasattr(import_groups.main, '__wrapped__'))


if __name__ == '__main__':
    unittest.main()
