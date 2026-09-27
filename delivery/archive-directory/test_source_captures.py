import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import source_captures as captures


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / 'raw.html').write_text('<html><p>Original rule page</p></html>', encoding='utf-8')
        (self.folder / 'reading.txt').write_text('Original rule page', encoding='utf-8')
        self.row = {'id': 'test-source', 'source_url': 'https://example.gov/rules', 'title': 'Rules',
                    'raw_path': str(self.folder/'raw.html'), 'text_path': str(self.folder/'reading.txt'),
                    'raw_sha256': hashlib.sha256((self.folder/'raw.html').read_bytes()).hexdigest(),
                    'text_sha256': hashlib.sha256((self.folder/'reading.txt').read_bytes()).hexdigest()}
        self.publish()

    def tearDown(self): self.tmp.cleanup()

    def publish(self, rows=None):
        payload = '\n'.join(json.dumps(r) for r in (rows or [self.row])).encode()
        (self.folder/'resources.jsonl').write_bytes(payload)
        (self.folder/'validation.json').write_text(json.dumps({'status': 'passed', 'resources_sha256': hashlib.sha256(payload).hexdigest()}))

    def test_exact_url_binding_and_readable_text(self):
        data = captures.load(self.folder)
        self.assertEqual(captures.source_urls(data), {'https://example.gov/rules'})
        self.assertEqual(len(captures.attachments('https://example.gov/rules', data)), 1)
        self.assertEqual(captures.attachments('https://example.gov/rules/', data), [])
        self.assertTrue(captures.attachments('https://example.gov/rules', data)[0]['text_url'])

    def test_namespaced_id(self):
        self.row['id'] = 'public-law:012345abc'
        self.publish()
        self.assertIn(self.row['id'], captures.load(self.folder))

    def test_manifest_tamper_fails_closed(self):
        with (self.folder/'resources.jsonl').open('a') as out: out.write(' ')
        self.assertEqual(captures.load(self.folder), {})

    def test_original_tamper_removes_saved_claim(self):
        (self.folder/'raw.html').write_text('changed')
        self.assertEqual(captures.load(self.folder), {})

    def test_text_tamper_preserves_original_but_not_text(self):
        (self.folder/'reading.txt').write_text('changed')
        data = captures.load(self.folder)
        self.assertEqual(len(data), 1)
        self.assertIsNone(captures.attachments(self.row['source_url'], data)[0]['text_url'])

    def test_outside_root_and_duplicate_ids_rejected(self):
        self.row['raw_path'] = str(self.folder.parent/'outside.html')
        self.publish()
        self.assertEqual(captures.load(self.folder), {})
        self.publish([self.row, self.row])
        self.assertEqual(captures.load(self.folder), {})

    def test_html_attachment_and_unrecognized_asset(self):
        data = captures.load(self.folder)
        with patch.object(captures, 'load', return_value=data):
            self.assertEqual(captures.asset('test-source/original')[1:], ('application/octet-stream', 'raw.html'))
            self.assertEqual(captures.asset('test-source/text')[1:], ('text/plain; charset=utf-8', None))
            self.assertIsNone(captures.asset('test-source/../../secrets'))
            self.assertIsNone(captures.asset('missing/text'))

    def test_concurrent_rebuild_cannot_change_served_bytes(self):
        data = captures.load(self.folder)
        with patch.object(captures, 'load', return_value=data):
            saved = captures.asset('test-source/text')
            self.assertEqual(hashlib.sha256(saved[0]).hexdigest(), self.row['text_sha256'])
            (self.folder/'reading.txt').write_text('replacement content')
            self.assertEqual(saved[0], b'Original rule page')
            self.assertIsNone(captures.asset('test-source/text'))


class RegistryTests(unittest.TestCase):
    """Registered capture batches: built-in pilot plus hash-gated registry entries."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.pilot = self.root / 'sources/pilot'
        self.extra = self.root / 'sources/extra'
        self.registry = self.extra / 'capture_registry.json'
        self.pilot_row = self.batch(self.pilot, 'public-law:aaa', 'https://example.gov/pilot', b'<html><main>Pilot page</main></html>', 'Pilot page')
        temporal = {'captured_at': '2026-09-19T10:00:00+00:00', 'captured_at_basis': 'HTTP receipt', 'source_as_of': None, 'source_as_of_basis': None,
                    'published_at': None, 'published_at_basis': None, 'effective_from': None, 'effective_from_basis': None,
                    'effective_to': None, 'effective_to_basis': None}
        self.extra_row = self.batch(self.extra, 'gap-fill:bbb', 'https://courts.example.gov/rules.pdf', b'%PDF-1.7 rule body', 'Rule body', uniform=True,
                                    extra={'state': 'NJ', 'gap_type': 'complex_litigation_program', 'law_family': 'court_rules', 'doc_kind': 'pdf_document', 'temporal': temporal})
        self.write_registry([{'name': 'extra', 'folder': 'sources/extra', 'id_prefix': 'gap-fill:'}])
        self.patches = [patch.object(captures, 'ROOT', self.root), patch.object(captures, 'DATA', self.pilot), patch.object(captures, 'REGISTRY', self.registry)]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in self.patches: item.stop()
        self.tmp.cleanup()

    def batch(self, folder, ident, url, raw, text, extra=None, uniform=False):
        folder.mkdir(parents=True, exist_ok=True)
        name = ident.split(':')[1]
        (folder / (name + '.bin')).write_bytes(raw)
        (folder / (name + '.txt')).write_text(text, encoding='utf-8')
        row = {'id': ident, 'source_url': url, 'title': 'Title ' + name, 'kind': 'court_rules',
               'raw_path': (folder / (name + '.bin')).relative_to(self.root).as_posix(), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
               'text_path': (folder / (name + '.txt')).relative_to(self.root).as_posix(), 'text_sha256': hashlib.sha256((folder / (name + '.txt')).read_bytes()).hexdigest(),
               **(extra or {})}
        self.publish(folder, [row], uniform)
        return row

    def publish(self, folder, rows, uniform=False, ready=True, others=()):
        payload = ''.join(json.dumps(r) + '\n' for r in rows).encode()
        (folder / 'resources.jsonl').write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        if uniform:
            files = [{'path': 'resources.jsonl', 'sha256': digest, 'rows': len(rows)}, *others]
            gate = {'schema_version': '1', 'status': 'passed', 'ready': ready, 'data_files': files}
        else:
            gate = {'status': 'passed', 'resources_sha256': digest}
        (folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')

    def write_registry(self, batches, pin=True):
        payload = json.dumps({'schema_version': '1', 'batches': batches}).encode()
        self.registry.write_bytes(payload)
        gate = {'schema_version': '1', 'status': 'passed', 'ready': True,
                'data_files': [{'path': 'capture_registry.json', 'sha256': hashlib.sha256(payload).hexdigest() if pin else '0' * 64, 'rows': len(batches)}]}
        (self.registry.parent / 'capture_registry.validation.json').write_text(json.dumps(gate), encoding='utf-8')

    def test_default_load_merges_pilot_and_registered_batches(self):
        data = captures.load()
        self.assertEqual(set(data), {'public-law:aaa', 'gap-fill:bbb'})
        self.assertEqual(captures.source_urls(data), {'https://example.gov/pilot', 'https://courts.example.gov/rules.pdf'})
        self.assertEqual(captures.attachments('https://courts.example.gov/rules.pdf')[0]['batch'], 'extra')
        self.assertEqual(captures.attachments('https://courts.example.gov/rules.pdf/'), [])
        self.assertEqual([(b['name'], b['status'], b['resources']) for b in captures.batches()],
                         [('pilot', 'loaded', 1), ('extra', 'loaded', 1)])

    def test_single_folder_load_is_unchanged(self):
        self.assertEqual(set(captures.load(self.extra)), {'gap-fill:bbb'})
        self.assertEqual(set(captures.load(self.pilot)), {'public-law:aaa'})

    def test_registry_tamper_keeps_only_builtin_batch(self):
        self.write_registry([{'name': 'extra', 'folder': 'sources/extra'}], pin=False)
        self.assertEqual(set(captures.load()), {'public-law:aaa'})
        (self.registry.parent / 'capture_registry.validation.json').unlink()
        self.assertEqual(set(captures.load()), {'public-law:aaa'})

    def test_missing_registry_keeps_pilot(self):
        self.registry.unlink()
        self.assertEqual(set(captures.load()), {'public-law:aaa'})

    def test_bad_batch_fails_closed_alone(self):
        with (self.extra / 'resources.jsonl').open('a') as out: out.write(' ')
        self.assertEqual(set(captures.load()), {'public-law:aaa'})
        status = {b['name']: b for b in captures.batches()}
        self.assertEqual(status['extra']['status'], 'rejected')
        self.assertEqual(status['extra']['resources'], 0)
        self.assertEqual(status['pilot']['status'], 'loaded')
        # A failed built-in batch does not take registered batches down with it.
        self.publish(self.extra, [self.extra_row], uniform=True)
        (self.pilot / 'validation.json').write_text('{"status": "quality_review_pending"}')
        self.assertEqual(set(captures.load()), {'gap-fill:bbb'})

    def test_uniform_envelope_needs_ready_and_every_data_file_hash(self):
        self.publish(self.extra, [self.extra_row], uniform=True, ready=False)
        self.assertEqual(captures.load(self.extra), {})
        (self.extra / 'gaps.json').write_text('{"items": []}')
        good = {'path': 'gaps.json', 'sha256': hashlib.sha256((self.extra / 'gaps.json').read_bytes()).hexdigest(), 'rows': 0}
        self.publish(self.extra, [self.extra_row], uniform=True, others=[good])
        self.assertEqual(len(captures.load(self.extra)), 1)
        (self.extra / 'gaps.json').write_text('{"items": [1]}')
        self.assertEqual(captures.load(self.extra), {})
        self.publish(self.extra, [self.extra_row], uniform=True, others=[{'path': '../pilot/resources.jsonl', 'sha256': '0' * 64}])
        self.assertEqual(captures.load(self.extra), {})
        # A uniform envelope that does not pin resources.jsonl is not a gate.
        (self.extra / 'validation.json').write_text(json.dumps({'schema_version': '1', 'status': 'passed', 'ready': True, 'data_files': []}))
        self.assertEqual(captures.load(self.extra), {})

    def test_ids_unique_across_batches(self):
        clash = dict(self.extra_row, id='public-law:aaa')
        self.publish(self.extra, [clash], uniform=True)
        self.write_registry([{'name': 'extra', 'folder': 'sources/extra'}])
        data = captures.load()
        self.assertEqual(set(data), {'public-law:aaa'})
        self.assertEqual(data['public-law:aaa']['source_url'], 'https://example.gov/pilot')
        self.assertEqual({b['name']: b['status'] for b in captures.batches()}['extra'], 'rejected')

    def test_id_prefix_is_enforced(self):
        self.write_registry([{'name': 'extra', 'folder': 'sources/extra', 'id_prefix': 'other:'}])
        self.assertEqual(set(captures.load()), {'public-law:aaa'})

    def test_per_batch_root_confinement(self):
        stolen = dict(self.extra_row, raw_path=self.pilot_row['raw_path'], raw_sha256=self.pilot_row['raw_sha256'])
        self.publish(self.extra, [stolen], uniform=True)
        self.assertEqual(set(captures.load()), {'public-law:aaa'})

    def test_registry_cannot_register_folders_outside_sources(self):
        outside = self.root / 'elsewhere'
        self.batch(outside, 'gap-fill:ccc', 'https://example.gov/outside', b'outside', 'outside', uniform=True)
        for folder in ('elsewhere', '../' + self.root.name + '/elsewhere', str(outside), 'sources/../elsewhere', 'sources', 'sources/pilot'):
            self.write_registry([{'name': 'bad', 'folder': folder}])
            self.assertEqual(set(captures.load()), {'public-law:aaa'}, folder)
        self.write_registry([{'name': 'extra', 'folder': 'sources/extra'}, {'name': 'extra', 'folder': 'sources/extra'}])
        self.assertEqual(set(captures.load()), {'public-law:aaa'})

    def test_public_dicts_carry_new_fields_and_no_paths(self):
        item = captures.attachments('https://courts.example.gov/rules.pdf')[0]
        self.assertEqual((item['state'], item['gap_type'], item['law_family'], item['doc_kind']),
                         ('NJ', 'complex_litigation_program', 'court_rules', 'pdf_document'))
        self.assertEqual(item['temporal']['captured_at'], '2026-09-19T10:00:00+00:00')
        self.assertIsNone(item['temporal']['effective_from'])
        legacy = captures.attachments('https://example.gov/pilot')[0]
        self.assertIsNone(legacy['state'])
        self.assertEqual(set(legacy['temporal']), set(item['temporal']))
        dumped = json.dumps([item, legacy, captures.listing(), captures.batches()])
        self.assertNotIn('_path', dumped)
        self.assertNotIn(self.root.name, dumped)

    def test_listing_filters_and_pagination(self):
        self.assertEqual(captures.listing()['total'], 2)
        self.assertEqual([x['id'] for x in captures.listing(state='nj')['items']], ['gap-fill:bbb'])
        self.assertEqual(captures.listing(batch='pilot')['total'], 1)
        self.assertEqual(captures.listing(doc_kind='pdf_document', law_family='court_rules', gap_type='complex_litigation_program')['total'], 1)
        self.assertEqual(captures.listing(q='courts.example')['total'], 1)
        self.assertEqual(captures.listing(state='TX')['items'], [])
        page = captures.listing(limit=1, offset=1)
        self.assertEqual((page['total'], len(page['items']), page['offset'], page['limit']), (2, 1, 1, 1))
        self.assertEqual(captures.listing(limit='x', offset=-5)['offset'], 0)
        self.assertLessEqual(captures.listing(limit=100000)['limit'], 200)
        self.assertEqual(captures.listing()['facets']['state'], {'NJ': 1})

    def test_asset_serves_verified_bytes_from_any_registered_batch(self):
        self.assertEqual(captures.asset('gap-fill:bbb/original'), (b'%PDF-1.7 rule body', 'application/pdf', None))
        self.assertEqual(captures.asset('public-law:aaa/original')[1:], ('application/octet-stream', 'aaa.bin'))
        self.assertEqual(captures.asset('gap-fill:bbb/text')[0], b'Rule body')
        (self.extra / 'bbb.txt').write_text('swapped')
        self.assertIsNone(captures.asset('gap-fill:bbb/text'))
        self.assertIsNotNone(captures.asset('gap-fill:bbb/original'))

    def test_cached_default_load_notices_changes(self):
        self.assertEqual(len(captures.load()), 2)
        self.assertIs(captures.load(), captures.load())
        (self.extra / 'bbb.bin').write_bytes(b'%PDF-1.7 altered body!')
        self.assertEqual(set(captures.load()), {'public-law:aaa'})
        self.assertIsNone(captures.asset('gap-fill:bbb/original'))


if __name__ == '__main__': unittest.main()
