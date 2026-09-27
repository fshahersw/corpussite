"""Adapter tests for mdl_crosswalk.py (sources/mdl_docket_crosswalk_20260919).

The fixture builds a tiny supplement in a temp folder with the same file set and uniform validation
envelope as the real one, so the hash gate, listing/detail shape and lookup helpers are exercised
without touching the private live data. One real-data assertion at the end runs against the actual
built supplement (skipped if it is not present).
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import mdl_crosswalk as m


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.crosswalk = [
            {'id': 'mdl:1001', 'mdl_number': 1001, 'mdl_status': 'pending', 'mdl_title': 'IN RE: Widget MDL',
             'cl_court_id': 'nysd', 'master_docket_number_registry': '1:20-md-1001',
             'master_docket_number_aws': '1:20-md-01001', 'aws_matter_id': 'm-master-1001',
             'aws_case_name': 'In re Widget', 'aws_case_status': 'pending', 'aws_date_filed': '2020-01-01',
             'aws_date_terminated': None, 'cl_docket_id': 555, 'cl_docket_id_basis': 'matter_aliases.courtlistener_docket_id',
             'catalog_master_docket_id': 555, 'catalog_master_docket_number': '1:20-md-01001',
             'catalog_master_case_name': 'In re Widget', 'catalog_master_document_count': 10,
             'catalog_master_high_value_docs': 2, 'basis': 'docket_number_normalized_and_court_match(aws_matters<->registry)'},
            {'id': 'mdl:1002', 'mdl_number': 1002, 'mdl_status': 'terminated', 'mdl_title': 'IN RE: Gadget MDL',
             'cl_court_id': 'cand', 'master_docket_number_registry': '2:19-md-1002', 'master_docket_number_aws': None,
             'aws_matter_id': None, 'aws_case_name': None, 'aws_case_status': None, 'aws_date_filed': None,
             'aws_date_terminated': None, 'cl_docket_id': None, 'cl_docket_id_basis': None,
             'catalog_master_docket_id': None, 'catalog_master_docket_number': None, 'catalog_master_case_name': None,
             'catalog_master_document_count': None, 'catalog_master_high_value_docs': None, 'basis': 'catalog/masters.json.mdl_number (native field)'},
        ]
        self.edges = [
            {'from': {'type': 'aws_matter', 'id': 'm-member-a'}, 'to': {'type': 'mdl', 'id': 1001},
             'relation': 'member_of_mdl', 'basis': 'fixture', 'evidence': {'relationship_record_id': 'rel-1',
             'parent_matter_id': 'm-master-1001', 'parent_docket_number': '1:20-md-01001', 'parent_court_id': 'nysd',
             'member_docket_number': '1:20-cv-00001', 'member_court_id': 'nysd'}},
        ]
        self.unresolved = [
            {'type': 'catalog_master', 'catalog_master_docket_id': 999, 'reason': 'fixture unresolved reason'},
        ]
        self._write()

    def _write(self):
        def jsonl(rows):
            return ''.join(json.dumps(r) + '\n' for r in rows).encode('utf-8')

        blobs = {'crosswalk.jsonl': jsonl(self.crosswalk), 'edges.jsonl': jsonl(self.edges), 'unresolved.jsonl': jsonl(self.unresolved)}
        data_files = []
        for name, blob in blobs.items():
            (self.folder / name).write_bytes(blob)
            data_files.append({'path': name, 'sha256': hashlib.sha256(blob).hexdigest(), 'rows': blob.decode('utf-8').count('\n')})
        gate = {'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': '2026-09-19T00:00:00+00:00',
                'data_files': data_files, 'counts': {}, 'checks': [], 'qualification': 'fixture qualification sentence.',
                'license_ref': 'sw_bulk_private_firm_work_product', 'export_allowed': False, 'inputs': []}
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')

    def tamper_last_row(self):
        (self.folder / 'crosswalk.jsonl').write_text(
            (self.folder / 'crosswalk.jsonl').read_text(encoding='utf-8') + '{"mdl_number": 9999}\n', encoding='utf-8')


class HashGateTests(unittest.TestCase):
    def test_gate_fails_closed_on_tamper(self):
        fx = Fixture()
        fx.tamper_last_row()
        m._CACHE.clear()
        result = _listing_against(fx.folder, {})
        self.assertFalse(result['available'])
        self.assertIn('reason', result)

    def test_gate_fails_closed_on_missing_file(self):
        fx = Fixture()
        (fx.folder / 'unresolved.jsonl').unlink()
        m._CACHE.clear()
        result = _listing_against(fx.folder, {})
        self.assertFalse(result['available'])


def _listing_against(folder, params):
    """Call listing() against a specific fixture folder by monkeypatching DATA, restoring it after."""
    original = m.DATA
    m.DATA = folder
    try:
        return m.listing(params)
    finally:
        m.DATA = original
        m._CACHE.clear()


def _detail_against(folder, mdl_id):
    original = m.DATA
    m.DATA = folder
    try:
        return m.detail(mdl_id)
    finally:
        m.DATA = original
        m._CACHE.clear()


def _call_against(folder, fn, *args):
    original = m.DATA
    m.DATA = folder
    try:
        return fn(*args)
    finally:
        m.DATA = original
        m._CACHE.clear()


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def test_listing_shape_and_qualification(self):
        result = _listing_against(self.fx.folder, {})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['qualification'], 'fixture qualification sentence.')
        self.assertLessEqual(len(result['filters']), 7)
        self.assertLessEqual(len(result['columns']), 5)

    def test_listing_status_filter(self):
        result = _listing_against(self.fx.folder, {'status': 'terminated'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['results'][0]['id'], 'mdl:1002')

    def test_listing_search_filter(self):
        result = _listing_against(self.fx.folder, {'q': 'widget'})
        self.assertEqual(result['total'], 1)

    def test_detail_by_bare_number_and_mdl_id(self):
        d1 = _detail_against(self.fx.folder, 'mdl:1001')
        d2 = _detail_against(self.fx.folder, '1001')
        self.assertEqual(d1['id'], 'mdl:1001')
        self.assertEqual(d1['id'], d2['id'])

    def test_detail_unknown_returns_none(self):
        self.assertIsNone(_detail_against(self.fx.folder, 'mdl:9999'))

    def test_detail_shows_member_section(self):
        d = _detail_against(self.fx.folder, 'mdl:1001')
        self.assertEqual(len(d['sections']), 1)
        self.assertIn('member_of_mdl', d['sections'][0]['heading'])
        self.assertEqual(d['sections'][0]['rows'][0][0], 'm-member-a')

    def test_for_mdl(self):
        result = _call_against(self.fx.folder, m.for_mdl, 1001)
        self.assertEqual(result['cl_docket_id'], 555)
        self.assertEqual(result['member_matter_count'], 1)
        self.assertTrue(result['has_aws_matter'])
        self.assertIsNone(_call_against(self.fx.folder, m.for_mdl, 424242))

    def test_mdl_for_docket(self):
        self.assertEqual(_call_against(self.fx.folder, m.mdl_for_docket, 555), 1001)
        self.assertIsNone(_call_against(self.fx.folder, m.mdl_for_docket, 999999))

    def test_mdl_for_matter_master_and_member(self):
        self.assertEqual(_call_against(self.fx.folder, m.mdl_for_matter, 'm-master-1001'), 1001)
        self.assertEqual(_call_against(self.fx.folder, m.mdl_for_matter, 'm-member-a'), 1001)
        self.assertIsNone(_call_against(self.fx.folder, m.mdl_for_matter, 'no-such-matter'))

    def test_members(self):
        result = _call_against(self.fx.folder, m.members, 1001)
        self.assertEqual(result['total_members'], 1)
        self.assertEqual(result['master']['matter_id'], 'm-master-1001')
        self.assertEqual(result['members'][0]['matter_id'], 'm-member-a')
        self.assertIsNone(_call_against(self.fx.folder, m.members, 424242))


class RealDataTests(unittest.TestCase):
    def setUp(self):
        if not (m.DATA / 'validation.json').exists():
            self.skipTest('real supplement not built yet')

    def test_real_data_one_assertion(self):
        m._CACHE.clear()
        result = m.for_mdl(3060)
        self.assertIsNotNone(result)
        self.assertEqual(result['cl_docket_id'], 66801859)
        self.assertEqual(result['cl_court_id'], 'ilnd')
        self.assertEqual(m.mdl_for_docket(66801859), 3060)


if __name__ == '__main__':
    unittest.main()
