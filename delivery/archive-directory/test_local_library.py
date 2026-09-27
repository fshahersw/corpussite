import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('local_library_review', Path(__file__).with_name('local_library.py'))
library = importlib.util.module_from_spec(spec)
spec.loader.exec_module(library)


class LocalLibraryTests(unittest.TestCase):
    def tearDown(self):
        library._load.cache_clear()
        library._verified.cache_clear()

    def test_collection_counts_are_snapshot_and_preview_distinct(self):
        cards = {r['id']: r for r in library.collections()}
        self.assertEqual(cards['mdl-3080']['record_count'], 1612)
        self.assertEqual(cards['mdl-3080']['preview_count'], 24)
        self.assertEqual(cards['settlements']['record_count'], 848)
        self.assertEqual(cards['court-registries']['record_count'], 269)

    def test_pagination_search_and_registry_family(self):
        page = library.collection('mdl-3080', {'page': ['2'], 'page_size': ['20']})
        self.assertEqual(len(page['items']), 4)
        self.assertEqual(page['total'], 24)
        subset = library.collection('court-registries', {'family': ['local'], 'page_size': ['999']})
        self.assertEqual(subset['total'], 6)
        self.assertEqual(subset['page_size'], 50)
        search = library.collection('court-registries', {'q': ['Philadelphia']})
        self.assertEqual(search['total'], 1)

    def test_unrecognized_and_path_inputs_never_read_files(self):
        for value in ['../assets', 'C:/Windows/win.ini', '/etc/passwd', '%2e%2e', 'unknown']:
            self.assertIsNone(library.asset(value))
            self.assertIsNone(library.collection(value))
        self.assertIsNone(library.asset(None))
        self.assertIsNone(library.county_visual('../42101'))

    def test_county_identity_scope_and_small_icons(self):
        for geoid in ['42101', '36061', '06037', '06001', '06075']:
            row = library.county_visual(geoid)
            self.assertEqual(row['geoid'], geoid)
            self.assertFalse(row['is_hero'])
            self.assertIsNotNone(library.asset(row['asset_id']))
            self.assertIn('Census', row['identity_basis'])
        self.assertEqual(library.county_visual('36061')['identity_scope'], 'shared_state_judiciary_mark')
        self.assertEqual(library.county_visual('06001')['image_kind'], 'favicon')
        self.assertIsNone(library.county_visual('36047'))  # No statewide-logo propagation.

    def test_response_mutation_cannot_change_loaded_allowlist_or_records(self):
        cards = library.collections()
        cards[0]['id'] = 'changed'
        self.assertNotEqual(library.collections()[0]['id'], 'changed')
        row = library.county_visual('42101')
        row['asset_id'] = 'unknown'
        self.assertNotEqual(library.county_visual('42101')['asset_id'], 'unknown')

    def test_asset_is_bound_to_hash_and_declared_source_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / 'sample.pdf'
            file.write_bytes(b'%PDF-valid-original')
            data = root / 'data'
            data.mkdir()
            rec = dict(id='fixture-pdf', path=str(file), allowed_root=str(root), mime='application/pdf',
                       bytes=file.stat().st_size, sha256=hashlib.sha256(file.read_bytes()).hexdigest(), attachment=False)
            (data / 'assets.json').write_text(json.dumps([rec]), encoding='utf8')
            with patch.object(library, 'DATA', data):
                library._load.cache_clear()
                self.assertEqual(library.asset('fixture-pdf'), (file.resolve(), 'application/pdf', False))
                file.write_bytes(b'%PDF-bad---original')  # Same length; digest must reject.
                self.assertIsNone(library.asset('fixture-pdf'))
                rec['allowed_root'] = str(data)
                (data / 'assets.json').write_text(json.dumps([rec]), encoding='utf8')
                library._load.cache_clear()
                self.assertIsNone(library.asset('fixture-pdf'))

    def test_all_saved_preview_assets_pass_live_integrity(self):
        records = library._load('assets.json')
        self.assertEqual(len(records), 56)
        for row in records:
            self.assertIsNotNone(library.asset(row['id']), row['id'])
        mdl = library.collection('mdl-3080', {'page_size': '50'})
        self.assertTrue(all(r['asset_id'] and r['text_asset_id'] and r['excerpt'] for r in mdl['items']))

    def test_settlement_publisher_assertions_not_independent_verification(self):
        result = library.collection('settlements', {'page_size': 50})
        self.assertTrue(all(r['metadata']['publisher_assertions_are_independent_verification'] is False for r in result['items']))
        self.assertTrue(all(r['attribution'] and r['status_as_of'] for r in result['items']))
        self.assertTrue(any(r['documents'] for r in result['items']))


if __name__ == '__main__':
    unittest.main()
