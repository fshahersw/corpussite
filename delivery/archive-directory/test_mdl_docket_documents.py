import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import mdl_docket_documents as adapter


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FailClosedTests(unittest.TestCase):
    """Build a tiny tampered copy of the supplement and confirm the adapter fails closed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / 'mdl_docket_documents_20260919'
        self.folder.mkdir()
        docs = [
            {"id": "doc:1:1:1:0", "master_docket_id": 1, "docket_number": "1:00-md-1", "court": "xxd",
             "case_name": "In re Example", "entry_number": 1, "document_number": "1",
             "entry_date_filed": "2020-01-01", "raw_entry_description": "Case management order",
             "raw_document_description": "", "doc_type": "case_management_order",
             "source_doc_category": "case_management_order", "high_value": True, "page_count": 3,
             "is_sealed": False, "download_url": "https://storage.courtlistener.com/x.pdf",
             "courtlistener_url": "https://www.courtlistener.com/docket/1/1/", "sha1": "abc",
             "resolved": True, "mdl_number": 9001, "mdl_status": "pending", "mdl_title": "In re Example",
             "is_exact_duplicate": False, "exact_duplicate_of": None, "unresolved_reason": None},
            {"id": "doc:2:None:2:1", "master_docket_id": 2, "docket_number": "1:00-md-2", "court": "yyd",
             "case_name": "Unlinked matter", "entry_number": None, "document_number": "",
             "entry_date_filed": "2021-06-15", "raw_entry_description": "Notice of appearance",
             "raw_document_description": "", "doc_type": "other", "source_doc_category": "other",
             "high_value": False, "page_count": None, "is_sealed": False, "download_url": None,
             "courtlistener_url": None, "sha1": None, "resolved": False, "mdl_number": None,
             "mdl_status": None, "mdl_title": None, "is_exact_duplicate": False,
             "exact_duplicate_of": None, "unresolved_reason": "no mdl_number and master_docket_id 2 not resolved"},
        ]
        unresolved = [docs[1]]
        self._write_jsonl('documents.jsonl', docs)
        self._write_jsonl('unresolved.jsonl', unresolved)
        gate = {
            "schema_version": "1", "status": "passed", "ready": True, "validated_at": "2026-09-19T00:00:00Z",
            "data_files": [
                {"path": "documents.jsonl", "sha256": _digest((self.folder / 'documents.jsonl').read_bytes()), "rows": 2},
                {"path": "unresolved.jsonl", "sha256": _digest((self.folder / 'unresolved.jsonl').read_bytes()), "rows": 1},
            ],
            "counts": {}, "checks": [], "qualification": "test fixture qualification",
            "license_ref": "sw_bulk_private_firm_work_product", "export_allowed": False, "inputs": [],
        }
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        self._orig_data = adapter.DATA
        adapter.DATA = self.folder

    def tearDown(self):
        adapter.DATA = self._orig_data
        adapter._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_jsonl(self, name, rows):
        with open(self.folder / name, 'w', encoding='utf-8') as f:
            for row in rows:
                f.write(json.dumps(row))
                f.write('\n')

    def test_gate_open_listing_available(self):
        result = adapter.listing({})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 2)

    def test_detail_resolved_row(self):
        row = adapter.detail('doc:1:1:1:0')
        self.assertIsNotNone(row)
        self.assertIn('9001', dict(row['facts'])['MDL'])

    def test_detail_unresolved_row_labels_not_linked(self):
        row = adapter.detail('doc:2:None:2:1')
        self.assertIsNotNone(row)
        self.assertTrue(dict(row['facts'])['MDL'].startswith('Not linked'))

    def test_for_mdl(self):
        block = adapter.for_mdl(9001)
        self.assertIsNotNone(block)
        self.assertEqual(block['total'], 1)
        self.assertEqual(block['by_doc_type'], {'case_management_order': 1})

    def test_for_mdl_none_when_no_rows(self):
        self.assertIsNone(adapter.for_mdl(424242))

    def test_filter_by_mdl(self):
        result = adapter.listing({'mdl': '9001'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'doc:1:1:1:0')

    def test_filter_has_free_document(self):
        result = adapter.listing({'has_free_document': 'yes'})
        self.assertEqual(result['total'], 1)
        result_no = adapter.listing({'has_free_document': 'no'})
        self.assertEqual(result_no['total'], 1)

    def test_listing_title_never_publishes_case_name(self):
        result = adapter.listing({})
        titles = [r['title'] for r in result['results']]
        self.assertNotIn('In re Example', titles)
        self.assertNotIn('Unlinked matter', titles)
        # docket identity only
        self.assertTrue(any('1:00-md-1' in t for t in titles))

    def test_detail_title_never_publishes_case_name(self):
        row = adapter.detail('doc:1:1:1:0')
        self.assertNotEqual(row['title'], 'In re Example')
        self.assertIn('1:00-md-1', row['title'])

    def test_for_mdl_does_not_ship_case_name(self):
        block = adapter.for_mdl(9001)
        self.assertNotIn('case_name', block['latest_25'][0])

    def test_for_mdl_qualification_under_500_chars(self):
        block = adapter.for_mdl(9001)
        self.assertLess(len(block['qualification']), 500)

    def test_q_search_does_not_match_case_name_or_raw_docket_text(self):
        # 'Unlinked matter' is only the case_name of the unresolved row (no mdl_title to leak
        # through); it must not be searchable.
        result = adapter.listing({'q': 'Unlinked matter'})
        self.assertEqual(result['total'], 0)
        # 'Notice of appearance' is raw_entry_description text for the 'other'-typed row, distinct
        # from its doc_type label ('Other'); must not be searchable via the docket text.
        result2 = adapter.listing({'q': 'appearance'})
        self.assertEqual(result2['total'], 0)

    def test_q_search_matches_docket_identity(self):
        result = adapter.listing({'q': '1:00-md-1'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'doc:1:1:1:0')

    def test_never_serves_bytes(self):
        self.assertFalse(hasattr(adapter, 'original'))

    def test_gate_fails_closed_on_hash_tamper(self):
        (self.folder / 'documents.jsonl').write_text('{"id": "tampered"}\n', encoding='utf-8')
        adapter._CACHE.clear()
        result = adapter.listing({})
        self.assertFalse(result['available'])
        self.assertIsNone(adapter.detail('doc:1:1:1:0'))
        self.assertIsNone(adapter.for_mdl(9001))

    def test_gate_fails_closed_when_not_passed(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'draft'
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        result = adapter.listing({})
        self.assertFalse(result['available'])


class RealDataTest(unittest.TestCase):
    def test_real_supplement_if_built(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        result = adapter.listing({})
        if not result['available']:
            self.skipTest('supplement gate not open: %s' % result.get('reason'))
        self.assertGreater(result['total'], 30000)
        block = adapter.for_mdl(2804)
        self.assertIsNotNone(block)
        self.assertGreater(block['total'], 1000)
        self.assertLess(len(block['qualification']), 500)

    def test_mdl_2789_never_publishes_the_individual_plaintiff_caption(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        block = adapter.for_mdl(2789)
        if block is None:
            self.skipTest('MDL 2789 not resolved in this environment')
        self.assertGreater(block['total'], 1000)
        result = adapter.listing({'mdl': '2789'})
        if not result['available']:
            self.skipTest('supplement gate not open: %s' % result.get('reason'))
        for row in result['results']:
            self.assertNotIn('GOODSTEIN', row['title'].upper())
        q_result = adapter.listing({'q': 'Goodstein'})
        self.assertEqual(q_result['total'], 0)


if __name__ == '__main__':
    unittest.main()
