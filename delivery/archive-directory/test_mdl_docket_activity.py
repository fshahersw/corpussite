import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import mdl_docket_activity as adapter


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FailClosedTests(unittest.TestCase):
    """Build a tiny tampered copy of the supplement and confirm the adapter fails closed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / 'mdl_docket_activity_20260919'
        self.folder.mkdir()
        entries = [
            {"id": "e1", "matter_id": "m1", "mdl_number": 9001, "entry_number": "1", "entry_number_int": 1,
             "description": "Settlement agreement approved by the court. (Entered: 01/08/2020)",
             "description_redacted": False, "redaction_basis": None,
             "member_docket_number": "1:20-md-09001", "court_id": "nysd",
             "cl_docket_id": 12345, "cl_docket_id_basis": "matter_aliases.courtlistener_docket_id",
             "entry_type": "settlement", "published_at": "2020-01-08",
             "published_at_basis": "entered date parsed from the trailing '(Entered: ...)' text",
             "has_verified_document": True, "verified_document_count": 2, "record_status": "active",
             "schema_version_source": "docket_entries.v1"},
            {"id": "e2", "matter_id": "m1", "mdl_number": 9001, "entry_number": "2", "entry_number_int": 2,
             "description": "Short Form Complaint - [plaintiff name withheld] by PLAINTIFF(S). (THOMPSON, JULIE) (Entered: 02/05/2021)",
             "description_redacted": True,
             "redaction_basis": "individual member-case plaintiff caption withheld per the party-name rule",
             "member_docket_number": "2:20-cv-04242", "court_id": "nysd",
             "cl_docket_id": None, "cl_docket_id_basis": None,
             "entry_type": "other", "published_at": "2021-02-05",
             "published_at_basis": "entered date parsed from the trailing '(Entered: ...)' text",
             "has_verified_document": False, "verified_document_count": 0, "record_status": "active",
             "schema_version_source": "docket_entries.v1"},
        ]
        unresolved = [
            {"id": "e3", "matter_id": "m2", "entry_number": "3",
             "reason": "matter_id has no MDL link in mdl_docket_crosswalk_20260919"},
        ]
        summary = [
            {"mdl_number": 9001, "mdl_title": "In re Example", "mdl_status": "pending",
             "is_unique_to_this_slice": True, "total_entries": 2,
             "counts_by_type": {"settlement": 1, "other": 1}, "published_at_min": "2020-01-08",
             "published_at_max": "2021-02-05", "entries_with_no_parsed_date": 0,
             "primary_matter_id": "m1", "matters": [{"matter_id": "m1", "entries_count": 2,
                                                      "min_entry_number": 1, "max_entry_number": 2,
                                                      "entries_capped": False,
                                                      "entries_capped_basis": "no gap"}],
             "entries_capped": False, "entries_capped_basis": "no gap",
             "latest_25_ids": ["e2", "e1"]},
        ]
        self._write_jsonl('entries.jsonl', entries)
        self._write_jsonl('unresolved.jsonl', unresolved)
        self._write_jsonl('mdl_summary.jsonl', summary)
        gate = {
            "schema_version": "1", "status": "passed", "ready": True, "validated_at": "2026-09-19T00:00:00Z",
            "data_files": [
                {"path": "entries.jsonl", "sha256": _digest((self.folder / 'entries.jsonl').read_bytes()), "rows": 2},
                {"path": "unresolved.jsonl", "sha256": _digest((self.folder / 'unresolved.jsonl').read_bytes()), "rows": 1},
                {"path": "mdl_summary.jsonl", "sha256": _digest((self.folder / 'mdl_summary.jsonl').read_bytes()), "rows": 1},
            ],
            "counts": {}, "checks": [],
            "qualification": "test fixture qualification " + ("x" * 500),
            "qualification_short": "short test fixture qualification",
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

    def test_detail_known_row(self):
        row = adapter.detail('e1')
        self.assertIsNotNone(row)
        self.assertIn('9001', dict(row['facts'])['MDL'])

    def test_detail_unknown_row_returns_none(self):
        self.assertIsNone(adapter.detail('does-not-exist'))

    def test_filter_by_mdl(self):
        result = adapter.listing({'mdl': '9001'})
        self.assertEqual(result['total'], 2)
        result_none = adapter.listing({'mdl': '424242'})
        self.assertEqual(result_none['total'], 0)

    def test_filter_by_entry_type(self):
        result = adapter.listing({'entry_type': 'settlement'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'e1')

    def test_filter_by_year(self):
        result = adapter.listing({'year': '2020'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'e1')

    def test_filter_by_text_query(self):
        result = adapter.listing({'q': 'settlement'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'e1')

    def test_never_serves_bytes(self):
        self.assertFalse(hasattr(adapter, 'original'))

    def test_for_mdl(self):
        block = adapter.for_mdl(9001)
        self.assertIsNotNone(block)
        self.assertEqual(block['total'], 2)
        self.assertEqual(block['by_entry_type'], {'settlement': 1, 'other': 1})
        self.assertEqual(block['date_first'], '2020-01-08')
        self.assertEqual(block['date_last'], '2021-02-05')
        self.assertFalse(block['capped'])
        self.assertEqual(block['link'], '#mdl-activity?mdl=9001')
        self.assertLess(len(block['qualification']), 500)
        self.assertEqual(len(block['latest']), 2)
        self.assertEqual(block['latest'][0]['id'], 'e2')
        for row in block['latest']:
            self.assertIn('id', row)
            self.assertIn('title', row)
            self.assertIn('date', row)
            self.assertIn('entry_type', row)

    def test_for_mdl_none_when_no_rows(self):
        self.assertIsNone(adapter.for_mdl(424242))

    def test_for_mdl_caps_at_25(self):
        many_ids = ['id-%d' % i for i in range(30)]
        rows = [{"id": rid, "matter_id": "m9", "mdl_number": 9002, "entry_number": str(i),
                 "entry_number_int": i, "description": "other entry", "entry_type": "other",
                 "published_at": "2022-01-01", "published_at_basis": "x",
                 "has_verified_document": False, "verified_document_count": 0,
                 "record_status": "active", "schema_version_source": "docket_entries.v1"}
                for i, rid in enumerate(many_ids)]
        self._write_jsonl('entries.jsonl', rows)
        summary = [{"mdl_number": 9002, "mdl_title": "Big MDL", "mdl_status": "pending",
                    "is_unique_to_this_slice": True, "total_entries": 30,
                    "counts_by_type": {"other": 30}, "published_at_min": "2022-01-01",
                    "published_at_max": "2022-01-01", "entries_with_no_parsed_date": 0,
                    "primary_matter_id": "m9", "matters": [], "entries_capped": True,
                    "entries_capped_basis": "gap", "latest_25_ids": many_ids[:25]}]
        self._write_jsonl('mdl_summary.jsonl', summary)
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        gate['data_files'] = [
            {"path": "entries.jsonl", "sha256": _digest((self.folder / 'entries.jsonl').read_bytes()), "rows": 30},
            {"path": "unresolved.jsonl", "sha256": _digest((self.folder / 'unresolved.jsonl').read_bytes()), "rows": 1},
            {"path": "mdl_summary.jsonl", "sha256": _digest((self.folder / 'mdl_summary.jsonl').read_bytes()), "rows": 1},
        ]
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        block = adapter.for_mdl(9002)
        self.assertIsNotNone(block)
        self.assertTrue(block['capped'])
        self.assertLessEqual(len(block['latest']), 25)

    def test_gate_fails_closed_on_hash_tamper(self):
        (self.folder / 'entries.jsonl').write_text('{"id": "tampered"}\n', encoding='utf-8')
        adapter._CACHE.clear()
        result = adapter.listing({})
        self.assertFalse(result['available'])
        self.assertIsNone(adapter.detail('e1'))
        self.assertIsNone(adapter.for_mdl(9001))

    def test_gate_fails_closed_when_not_passed(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'draft'
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        adapter._CACHE.clear()
        result = adapter.listing({})
        self.assertFalse(result['available'])

    def test_gate_fails_closed_when_data_file_missing(self):
        (self.folder / 'mdl_summary.jsonl').unlink()
        adapter._CACHE.clear()
        result = adapter.listing({})
        self.assertFalse(result['available'])

    def test_listing_never_raises_on_non_dict_params(self):
        for bad in ('x', 5, ['a'], None, 3.14, object()):
            result = adapter.listing(bad)
            self.assertIn('available', result)
            if result['available']:
                self.assertIn('total', result)

    def test_for_mdl_uses_qualification_short_not_truncated_sentence(self):
        block = adapter.for_mdl(9001)
        self.assertEqual(block['qualification'], 'short test fixture qualification')
        self.assertLess(len(block['qualification']), 500)

    def test_redacted_caption_is_never_searchable_by_name(self):
        # 'Brett Basanez' style name is redacted out of the description; searching for it must miss.
        result = adapter.listing({'q': 'THOMPSON'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'e2')
        result_name = adapter.listing({'q': 'some withheld plaintiff name that never appears'})
        self.assertEqual(result_name['total'], 0)

    def test_redacted_row_subtitle_shows_member_docket_not_placeholder_text(self):
        result = adapter.listing({})
        redacted_result = next(r for r in result['results'] if r['id'] == 'e2')
        self.assertEqual(redacted_result['subtitle'], '2:20-cv-04242 (nysd)')

    def test_detail_shows_member_docket_and_redaction_fact(self):
        row = adapter.detail('e2')
        facts = dict(row['facts'])
        self.assertIn('2:20-cv-04242', [v for k, v in facts.items() if 'Member docket' in k][0])
        redaction_fact = [v for k, v in facts.items() if 'redaction' in k.lower()][0]
        self.assertIn('party-name rule', redaction_fact)

    def test_links_include_courtlistener_url_when_cl_docket_id_present(self):
        row = adapter.detail('e1')
        urls = [l['url'] for l in row['links']]
        self.assertTrue(any('courtlistener.com/docket/12345' in u for u in urls))
        # never a raw storage/S3 byte URL
        self.assertFalse(any('storage.courtlistener.com' in u or 's3' in u.lower() for u in urls))

    def test_no_link_and_badge_reason_when_cl_docket_id_absent(self):
        row = adapter.detail('e2')
        urls = [l['url'] for l in row['links']]
        self.assertFalse(any('courtlistener.com' in u for u in urls))
        listing_result = adapter.listing({})
        badges = next(r['badges'] for r in listing_result['results'] if r['id'] == 'e2')
        self.assertIn('no CourtListener docket id for this matter', badges)

    def test_at_least_one_listing_result_carries_courtlistener_url(self):
        result = adapter.listing({})
        all_urls = [l['url'] for r in result['results'] for l in r['links']]
        self.assertTrue(any('courtlistener.com' in u for u in all_urls))
        self.assertFalse(any('storage.courtlistener.com' in u or 's3' in u.lower() for u in all_urls))


class RealDataTest(unittest.TestCase):
    def test_real_supplement_if_built(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        result = adapter.listing({})
        if not result['available']:
            self.skipTest('supplement gate not open: %s' % result.get('reason'))
        self.assertGreater(result['total'], 20000)
        block = adapter.for_mdl(1570)
        self.assertIsNotNone(block)
        self.assertGreater(block['total'], 1000)
        self.assertTrue(block['capped'])
        self.assertLess(len(block['qualification']), 500)

    def test_real_data_plaintiff_name_search_finds_nothing(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        result = adapter.listing({'q': 'Brett Basanez'})
        if not result['available']:
            self.skipTest('supplement gate not open: %s' % result.get('reason'))
        self.assertEqual(result['total'], 0)

    def test_real_data_has_courtlistener_links_never_byte_urls(self):
        if not adapter.DATA.exists():
            self.skipTest('supplement not built in this environment')
        result = adapter.listing({'limit': 100})
        if not result['available']:
            self.skipTest('supplement gate not open: %s' % result.get('reason'))
        all_urls = [l['url'] for r in result['results'] for l in r['links']]
        self.assertTrue(any('courtlistener.com/docket/' in u for u in all_urls))
        self.assertFalse(any('storage.courtlistener.com' in u or 's3' in u.lower() for u in all_urls))


if __name__ == '__main__':
    unittest.main()
