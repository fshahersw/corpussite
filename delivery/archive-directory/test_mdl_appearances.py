"""Adapter tests for mdl_appearances.py (sources/mdl_counsel_appearances_20260919).

Fixture builds a tiny supplement in a temp folder with the same file set and uniform validation envelope
as the real one, so the hash gate, listing/detail shape and hooks are exercised without touching the
private live data. Real-data assertions at the end run against the actual built supplement (skipped if
not present).
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import mdl_appearances as m


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.attorneys = [
            {'attorney_id': 'a-1', 'name': 'Alice Attorney', 'appearance_count': 2, 'firm_ids': ['seeger_weiss'], 'mdl_numbers_reached_extended': [2789]},
            {'attorney_id': 'a-2', 'name': 'Bob Barrister', 'appearance_count': 1, 'firm_ids': ['weitz_luxenberg'], 'mdl_numbers_reached_extended': []},
        ]
        self.firms = [
            {'firm_id': 'seeger_weiss', 'canonical_name': 'Seeger Weiss', 'appearance_count': 2, 'mdl_numbers_reached_extended': [2789]},
            {'firm_id': 'weitz_luxenberg', 'canonical_name': 'Weitz & Luxenberg', 'appearance_count': 1, 'mdl_numbers_reached_extended': []},
        ]
        self.appearances = [
            {'appearance_id': 'app-1', 'matter_id': 'm-1', 'attorney_id': 'a-1', 'attorney_name': 'Alice Attorney',
             'firm_id': 'seeger_weiss', 'firm_canonical_name': 'Seeger Weiss', 'raw_firm_name': 'Seeger Weiss LLP',
             'party_id': 'p-1', 'party_side': 'plaintiff', 'role_raw': 'LEAD ATTORNEY', 'role_normalized': 'Lead attorney',
             'terminated_date_raw': None, 'mdl_number_direct': 2789, 'mdl_number_extended': 2789, 'mdl_match_basis': 'direct'},
            {'appearance_id': 'app-2', 'matter_id': 'm-1', 'attorney_id': 'a-1', 'attorney_name': 'Alice Attorney',
             'firm_id': 'seeger_weiss', 'firm_canonical_name': 'Seeger Weiss', 'raw_firm_name': 'Seeger Weiss LLP',
             'party_id': None, 'party_side': None, 'role_raw': 'terminated', 'role_normalized': 'Terminated',
             'terminated_date_raw': None, 'mdl_number_direct': 2789, 'mdl_number_extended': 2789, 'mdl_match_basis': 'direct'},
            {'appearance_id': 'app-3', 'matter_id': 'm-2', 'attorney_id': 'a-2', 'attorney_name': 'Bob Barrister',
             'firm_id': 'weitz_luxenberg', 'firm_canonical_name': 'Weitz & Luxenberg', 'raw_firm_name': 'Weitz & Luxenberg',
             'party_id': 'p-2', 'party_side': 'defendant', 'role_raw': '10', 'role_normalized': 'Unknown',
             'terminated_date_raw': None, 'mdl_number_direct': None, 'mdl_number_extended': None, 'mdl_match_basis': None},
        ]
        self.parties = [
            {'party_id': 'p-1', 'matter_id': 'm-1', 'party_types': ['Plaintiff'], 'category': 'natural_person_count_only',
             'listable': False, 'name': None, 'name_withheld_reason': 'natural-person plaintiff-side party',
             'mdl_number_direct': 2789, 'mdl_number_extended': 2789},
            {'party_id': 'p-2', 'matter_id': 'm-2', 'party_types': ['Defendant'], 'category': 'organization',
             'listable': True, 'name': 'Acme Pharma, Inc.', 'name_withheld_reason': None,
             'mdl_number_direct': None, 'mdl_number_extended': None},
        ]
        self.party_counts = [
            {'matter_id': 'm-1', 'total_parties': 1, 'listed_parties': 0, 'organization_parties': 0,
             'named_defendant_parties': 0, 'natural_person_plaintiff_count': 1},
            {'matter_id': 'm-2', 'total_parties': 1, 'listed_parties': 1, 'organization_parties': 1,
             'named_defendant_parties': 0, 'natural_person_plaintiff_count': 0},
        ]
        self.edges = [
            {'from': {'type': 'attorney', 'id': 'a-1'}, 'to': {'type': 'mdl', 'id': 2789}, 'relation': 'appeared_in',
             'basis': 'fixture', 'evidence': {'appearance_id': 'app-1', 'matter_id': 'm-1', 'firm_id': 'seeger_weiss', 'role_raw': 'LEAD ATTORNEY', 'matched_via': 'direct'}},
        ]
        self.unresolved = [
            {'type': 'counsel_appearance', 'appearance_id': 'app-3', 'matter_id': 'm-2', 'reason': 'fixture unresolved reason'},
        ]
        self._write()

    def _write(self):
        def jsonl(rows):
            return ''.join(json.dumps(r) + '\n' for r in rows).encode('utf-8')

        blobs = {
            'attorneys.jsonl': jsonl(self.attorneys), 'firms.jsonl': jsonl(self.firms),
            'appearances.jsonl': jsonl(self.appearances), 'parties.jsonl': jsonl(self.parties),
            'party_counts_by_matter.jsonl': jsonl(self.party_counts), 'edges.jsonl': jsonl(self.edges),
            'unresolved.jsonl': jsonl(self.unresolved),
        }
        data_files = []
        for name, blob in blobs.items():
            (self.folder / name).write_bytes(blob)
            data_files.append({'path': name, 'sha256': hashlib.sha256(blob).hexdigest(), 'rows': blob.decode('utf-8').count('\n')})
        gate = {'schema_version': '1', 'status': 'passed', 'ready': True, 'validated_at': '2026-09-19T00:00:00+00:00',
                'data_files': data_files, 'counts': {}, 'checks': [], 'qualification': 'fixture qualification sentence.',
                'qualification_short': 'fixture short qualification.',
                'license_ref': 'sw_bulk_private_firm_work_product', 'export_allowed': False, 'inputs': []}
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')

    def tamper_last_row(self):
        (self.folder / 'appearances.jsonl').write_text(
            (self.folder / 'appearances.jsonl').read_text(encoding='utf-8') + '{"appearance_id": "tampered"}\n', encoding='utf-8')


def _against(folder, fn, *args, **kwargs):
    original = m.DATA
    m.DATA = folder
    try:
        return fn(*args, **kwargs)
    finally:
        m.DATA = original
        m._CACHE.clear()


class HashGateTests(unittest.TestCase):
    def test_gate_fails_closed_on_tamper(self):
        fx = Fixture()
        fx.tamper_last_row()
        m._CACHE.clear()
        result = _against(fx.folder, m.listing, {})
        self.assertFalse(result['available'])
        self.assertIn('reason', result)

    def test_gate_fails_closed_on_missing_file(self):
        fx = Fixture()
        (fx.folder / 'unresolved.jsonl').unlink()
        m._CACHE.clear()
        result = _against(fx.folder, m.listing, {})
        self.assertFalse(result['available'])

    def test_detail_returns_none_when_gate_fails(self):
        fx = Fixture()
        fx.tamper_last_row()
        m._CACHE.clear()
        self.assertIsNone(_against(fx.folder, m.detail, 'app-1'))

    def test_for_mdl_returns_none_when_gate_fails(self):
        fx = Fixture()
        fx.tamper_last_row()
        m._CACHE.clear()
        self.assertIsNone(_against(fx.folder, m.for_mdl, 2789))


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def test_listing_shape(self):
        result = _against(self.fx.folder, m.listing, {})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 3)
        self.assertLessEqual(len(result['filters']), 7)
        self.assertLessEqual(len(result['columns']), 5)
        self.assertIn('linkage', {c['key'] for c in result['columns']})

    def test_listing_result_has_linkage_cell_and_badge(self):
        result = _against(self.fx.folder, m.listing, {})
        direct_row = next(r for r in result['results'] if r['id'] == 'app-1')
        self.assertEqual(direct_row['cells']['linkage'], 'Direct')
        self.assertIn('Direct MDL linkage', direct_row['badges'])
        unresolved_row = next(r for r in result['results'] if r['id'] == 'app-3')
        self.assertEqual(unresolved_row['cells']['linkage'], '—')

    def test_listing_filters_by_mdl_firm_role_side(self):
        self.assertEqual(_against(self.fx.folder, m.listing, {'mdl': '2789'})['total'], 2)
        self.assertEqual(_against(self.fx.folder, m.listing, {'firm': 'weitz_luxenberg'})['total'], 1)
        self.assertEqual(_against(self.fx.folder, m.listing, {'role': 'Lead attorney'})['total'], 1)
        self.assertEqual(_against(self.fx.folder, m.listing, {'side': 'defendant'})['total'], 1)

    def test_listing_search_filter(self):
        self.assertEqual(_against(self.fx.folder, m.listing, {'q': 'barrister'})['total'], 1)

    def test_detail_firm_prefix(self):
        d = _against(self.fx.folder, m.detail, 'firm:seeger_weiss')
        self.assertEqual(d['title'], 'Seeger Weiss')
        self.assertTrue(any('MDL' in s['heading'] for s in d['sections']))

    def test_detail_attorney_prefix(self):
        d = _against(self.fx.folder, m.detail, 'attorney:a-1')
        self.assertEqual(d['title'], 'Alice Attorney')
        self.assertEqual(len(d['sections'][0]['rows']), 2)
        self.assertIn(['Record scope',
                        'One source UUID; the same person may appear under other UUIDs in this dataset '
                        '— records are never merged by name'], d['facts'])

    def test_detail_bare_appearance_id(self):
        d = _against(self.fx.folder, m.detail, 'app-1')
        self.assertEqual(d['id'], 'app-1')
        self.assertIn(['Role (normalised)', 'Lead attorney'], d['facts'])

    def test_detail_unknown_returns_none(self):
        self.assertIsNone(_against(self.fx.folder, m.detail, 'no-such-id'))
        self.assertIsNone(_against(self.fx.folder, m.detail, 'firm:no-such-firm'))
        self.assertIsNone(_against(self.fx.folder, m.detail, 'attorney:no-such-attorney'))

    def test_for_mdl(self):
        result = _against(self.fx.folder, m.for_mdl, 2789)
        self.assertEqual(result['total_appearances_extended_linkage'], 2)
        self.assertEqual(result['total_appearances_direct_linkage'], 2)
        self.assertEqual(result['appearance_linkage_basis_counts'], {'direct': 2})
        self.assertEqual(result['total_parties'], 1)
        self.assertEqual(result['total_parties_direct_linkage'], 1)
        self.assertEqual(result['party_linkage_basis_counts'], {'direct': 1})
        self.assertEqual(result['firms'][0]['firm_id'], 'seeger_weiss')
        self.assertEqual(result['lead_attorney_appearance_count'], 1)
        self.assertEqual(result['party_counts_by_category']['natural_person_count_only'], 1)
        self.assertEqual(result['qualification'], 'fixture short qualification.')
        self.assertLess(len(result['qualification']), 500)
        self.assertNotIn('total_appearances', result)

    def test_for_mdl_returns_none_when_not_reached(self):
        self.assertIsNone(_against(self.fx.folder, m.for_mdl, 999999))

    def test_no_contact_fields_in_public_dicts(self):
        result = _against(self.fx.folder, m.listing, {})
        blob = json.dumps(result)
        for token in ('@', 'phone', 'email'):
            self.assertNotIn(token, blob.casefold().replace('email', 'email') if token != 'email' else blob)


class RealDataTests(unittest.TestCase):
    def setUp(self):
        if not (m.DATA / 'validation.json').exists():
            self.skipTest('real supplement not built yet')

    def test_real_data_headline_counts(self):
        m._CACHE.clear()
        result = m.listing({'limit': 1})
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 878)

    def test_real_data_for_mdl_and_firm_detail(self):
        m._CACHE.clear()
        result = m.for_mdl(2789)
        self.assertIsNotNone(result)
        firm_ids = {f['firm_id'] for f in result['firms']}
        self.assertIn('seeger_weiss', firm_ids)
        detail = m.detail('firm:seeger_weiss')
        self.assertEqual(detail['title'], 'Seeger Weiss')


if __name__ == '__main__':
    unittest.main()
