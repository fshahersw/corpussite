"""Adapter tests for the JPML MDL registry (sources/jpml_mdl_20260919).

The fixture builds a tiny supplement in a temp folder with the same file set and
uniform validation envelope as the real one, so the hash gate, filters, joins and
original-file serving are exercised without touching the live data.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import mdl_registry as reg

AS_OF = '2026-09-01'
LABEL = 'as listed in the JPML report dated 2026-09-01'


def _row(num, title, code, cl, pending, total, ltype, judge, entity=None, status='pending', circuit='Third Circuit'):
    return {
        'id': 'mdl:%d' % num, 'mdl_number': num, 'title': title, 'status': status,
        'district_code': code, 'cl_court_id': cl, 'court_name': None, 'circuit': circuit,
        'transferee_judge': {'name_as_printed': judge, 'title_as_printed': 'U.S. District Judge', 'name_by_number_report': None},
        'actions_pending': pending, 'total_actions': total, 'counts_label': LABEL,
        'litigation_type': ltype, 'master_docket': '2:23-md-%d' % num, 'date_filed': '2023-05-09',
        'date_transferred': '2023-08-03', 'date_closed': None, 'snapshots': [],
        'judge_links': ([{'entity_id': entity, 'display_name': judge, 'basis': 'jpml_name_court', 'match_kind': 'exact', 'fjc_nid': '1'}] if entity else []),
        'cl_links': ({'docket_id': 900 + num, 'court_id': cl, 'assigned_to_id': 500 + num, 'basis': 'cl_docket_search'} if entity else None),
        'local_collections': ([{'collection_id': 'mdl-3080', 'label': 'MDL 3080 collection', 'records': 24, 'basis': 'mdl_number_only'}] if num == 3080 else []),
        'reports': [{'report_kind': 'by_mdl_number', 'document_id': 'jpmldoc-abc', 'as_of': AS_OF}],
        'temporal': {'captured_at': '2026-09-19T05:44:00+00:00', 'captured_at_basis': 'receipt', 'source_as_of': AS_OF,
                     'source_as_of_basis': 'Report Date printed in the report header', 'published_at': None, 'published_at_basis': None,
                     'effective_from': '2023-08-03', 'effective_from_basis': 'DateTransferred column', 'effective_to': None, 'effective_to_basis': None},
        'unresolved': ([] if entity else [{'kind': 'judge', 'reason': 'no_fjc_name_match_at_court'}]),
        'raw_path': 'should-never-leak',
    }


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / 'raw').mkdir()
        self.pdf = self.folder / 'raw' / '07_report.pdf'
        self.pdf.write_bytes(b'%PDF-1.4 fixture bytes')
        self.rows = [
            _row(3080, 'IN RE: Insulin Pricing Litigation', 'NJ', 'njd', 100, 120, 'Antitrust', 'Brian R. Martinotti', 'judge-entity-aaa'),
            _row(2738, 'IN RE: Johnson & Johnson Talcum Powder Products Liability Litigation', 'NJ', 'njd', 69250, 71935, 'Products Liability', 'Michael A. Shipp', 'judge-entity-bbb'),
            _row(2741, 'IN RE: Roundup Products Liability Litigation', 'CAN', 'cand', 3925, 5323, 'Products Liability', 'Vince Chhabria', None, circuit='Ninth Circuit'),
            _row(2591, 'IN RE: Syngenta AG MIR162 Corn Litigation', 'KS', 'ksd', None, None, None, 'John W. Lungstrum', None, status='terminated', circuit='Tenth Circuit'),
        ]
        self.rows[3]['date_closed'] = '2026-01-13'
        self.docs = [{
            'document_id': 'jpmldoc-abc', 'seq': 7, 'kind': 'report_pdf', 'report_kind': 'by_mdl_number',
            'title': 'MDL Statistics Report - Docket Summary Listing', 'filename': '07_report.pdf', 'mime': 'application/pdf',
            'bytes': self.pdf.stat().st_size, 'sha256': hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
            'seed_url': 'https://www.jpml.uscourts.gov/sites/jpml/files/x.pdf', 'final_url': 'https://www.jpml.uscourts.gov/sites/jpml/files/x.pdf',
            'parent_page': 'https://www.jpml.uscourts.gov/pending-mdls-0', 'period_label': 'As of September 1, 2026',
            'report_date': AS_OF, 'report_date_basis': 'printed Report Date', 'captured_at': '2026-09-19T05:44:00+00:00',
            'raw_path': 'raw/07_report.pdf', 'pages': 6,
        }]
        self.court_map = {'NJ': {'cl_court_id': 'njd', 'circuit': 'Third Circuit'}, 'CAN': {'cl_court_id': 'cand', 'circuit': 'Ninth Circuit'},
                          'KS': {'cl_court_id': 'ksd', 'circuit': 'Tenth Circuit'}}
        self.edges = [{'from': {'type': 'mdl', 'id': 'mdl:3080'}, 'to': {'type': 'judge_entity', 'id': 'judge-entity-aaa'}, 'relation': 'transferee_judge', 'basis': 'jpml_name_court', 'evidence': {}}]
        self.unresolved = [{'kind': 'judge', 'mdl': 'mdl:2741', 'reason': 'no_fjc_name_match_at_court'}]
        self.qa = {'status': 'passed', 'mismatches': [], 'checks': []}
        self.publish()

    def _write(self, name, payload):
        (self.folder / name).write_bytes(payload)
        return {'path': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'rows': payload.count(b'\n')}

    def publish(self, status='passed', ready=True):
        files = [
            self._write('mdls.jsonl', ''.join(json.dumps(r) + '\n' for r in self.rows).encode()),
            self._write('documents.jsonl', ''.join(json.dumps(d) + '\n' for d in self.docs).encode()),
            self._write('court_map.json', json.dumps(self.court_map).encode()),
            self._write('edges.jsonl', ''.join(json.dumps(e) + '\n' for e in self.edges).encode()),
            self._write('unresolved.jsonl', ''.join(json.dumps(u) + '\n' for u in self.unresolved).encode()),
            self._write('qa.json', json.dumps(self.qa).encode()),
        ]
        gate = {'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T08:00:00+00:00',
                'data_files': files, 'counts': {'mdls_pending': 3, 'mdls_terminated': 1}, 'checks': [],
                'qualification': 'fixture', 'license_ref': 'U.S. Government work', 'inputs': []}
        (self.folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')

    def cleanup(self):
        self.tmp.cleanup()


class GateTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_summary_available_and_labelled(self):
        s = reg.summary(self.fx.folder)
        self.assertTrue(s['available'])
        self.assertEqual(s['as_of'], AS_OF)
        self.assertEqual(s['counts_label'], LABEL)
        self.assertEqual(s['counts']['mdls_pending'], 3)
        self.assertEqual(s['counts']['mdls_terminated'], 1)
        self.assertEqual(s['counts']['actions_pending'], 100 + 69250 + 3925)
        self.assertEqual(s['counts']['judges_resolved_mdls'], 2)
        self.assertEqual(s['counts']['judges_unresolved_mdls'], 1)

    def test_tampered_data_file_closes_gate(self):
        with (self.fx.folder / 'mdls.jsonl').open('ab') as out:
            out.write(b'\n')
        s = reg.summary(self.fx.folder)
        self.assertFalse(s['available'])
        self.assertEqual(reg.listing({}, self.fx.folder)['total'], 0)
        self.assertIsNone(reg.detail(3080, self.fx.folder))
        self.assertIsNone(reg.original('jpmldoc-abc', self.fx.folder))

    def test_not_ready_closes_gate(self):
        self.fx.publish(status='passed', ready=False)
        self.assertFalse(reg.summary(self.fx.folder)['available'])
        self.fx.publish(status='in_progress', ready=True)
        self.assertFalse(reg.summary(self.fx.folder)['available'])

    def test_missing_folder_is_unavailable_not_exception(self):
        s = reg.summary(self.fx.folder / 'nope')
        self.assertFalse(s['available'])
        self.assertEqual(reg.listing({}, self.fx.folder / 'nope')['results'], [])


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_default_sort_pending_desc_and_pending_only(self):
        out = reg.listing({}, self.fx.folder)
        self.assertEqual([r['mdl_number'] for r in out['results']], [2738, 2741, 3080])
        self.assertEqual(out['total'], 3)
        self.assertEqual(out['counts_label'], LABEL)
        for r in out['results']:
            self.assertNotIn('raw_path', r)
            self.assertEqual(r['counts_label'], LABEL)

    def test_status_filter(self):
        self.assertEqual([r['mdl_number'] for r in reg.listing({'status': 'terminated'}, self.fx.folder)['results']], [2591])
        self.assertEqual(reg.listing({'status': 'all'}, self.fx.folder)['total'], 4)

    def test_filters(self):
        self.assertEqual([r['mdl_number'] for r in reg.listing({'q': 'roundup'}, self.fx.folder)['results']], [2741])
        self.assertEqual([r['mdl_number'] for r in reg.listing({'q': 'shipp'}, self.fx.folder)['results']], [2738])
        self.assertEqual(reg.listing({'court': 'njd'}, self.fx.folder)['total'], 2)
        self.assertEqual(reg.listing({'court': 'NJ'}, self.fx.folder)['total'], 2)
        self.assertEqual(reg.listing({'circuit': 'Ninth Circuit'}, self.fx.folder)['total'], 1)
        self.assertEqual(reg.listing({'litigation_type': 'products liability'}, self.fx.folder)['total'], 2)
        self.assertEqual([r['mdl_number'] for r in reg.listing({'judge_resolved': 'false'}, self.fx.folder)['results']], [2741])
        self.assertEqual(reg.listing({'judge_resolved': True}, self.fx.folder)['total'], 2)
        self.assertEqual([r['mdl_number'] for r in reg.listing({'min_pending': 1000}, self.fx.folder)['results']], [2738, 2741])
        self.assertEqual(reg.listing({'min_pending': 'x'}, self.fx.folder)['total'], 3)

    def test_sorts_and_pagination(self):
        self.assertEqual([r['mdl_number'] for r in reg.listing({'sort': 'mdl_number'}, self.fx.folder)['results']], [2738, 2741, 3080])
        self.assertEqual([r['mdl_number'] for r in reg.listing({'sort': 'title'}, self.fx.folder)['results']], [3080, 2738, 2741])
        page = reg.listing({'limit': 2, 'page': 2}, self.fx.folder)
        self.assertEqual([r['mdl_number'] for r in page['results']], [3080])
        self.assertEqual((page['page'], page['pages'], page['limit']), (2, 2, 2))
        self.assertEqual(reg.listing({'limit': 10000}, self.fx.folder)['limit'], reg.MAX_LIMIT)
        self.assertEqual(reg.listing({'page': -3}, self.fx.folder)['page'], 1)

    def test_facets_present(self):
        out = reg.listing({}, self.fx.folder)
        self.assertIn('Products Liability', dict(out['facets']['litigation_type']))
        self.assertIn('Ninth Circuit', dict(out['facets']['circuit']))


class DetailTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_detail_joins_and_provenance(self):
        d = reg.detail('3080', self.fx.folder)
        self.assertEqual(d['mdl_number'], 3080)
        self.assertNotIn('raw_path', json.dumps(d))
        self.assertEqual(d['judge_links'][0]['entity_id'], 'judge-entity-aaa')
        self.assertEqual(d['judge_links'][0]['basis'], 'jpml_name_court')
        self.assertEqual(d['documents'][0]['document_id'], 'jpmldoc-abc')
        self.assertEqual(d['documents'][0]['url'], '/mdl-files/jpmldoc-abc')
        self.assertEqual(d['local_collections'][0]['collection_id'], 'mdl-3080')
        self.assertEqual(d['counts_label'], LABEL)
        self.assertEqual(d['provenance']['source_as_of'], AS_OF)
        self.assertEqual(len(d['edges']), 1)

    def test_detail_unknown(self):
        self.assertIsNone(reg.detail(1, self.fx.folder))
        self.assertIsNone(reg.detail('abc', self.fx.folder))

    def test_detail_unresolved_judge_listed(self):
        d = reg.detail(2741, self.fx.folder)
        self.assertEqual(d['judge_links'], [])
        self.assertEqual(d['unresolved'][0]['reason'], 'no_fjc_name_match_at_court')

    def test_for_judge_and_person(self):
        self.assertEqual([r['mdl_number'] for r in reg.for_judge('judge-entity-aaa', self.fx.folder)['results']], [3080])
        self.assertEqual(reg.for_judge('judge-entity-zzz', self.fx.folder)['results'], [])
        self.assertEqual([r['mdl_number'] for r in reg.for_person(500 + 2738, self.fx.folder)['results']], [2738])
        self.assertEqual(reg.for_person('bogus', self.fx.folder)['results'], [])


class OriginalTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_original_served_after_rehash(self):
        data, mime, name = reg.original('jpmldoc-abc', self.fx.folder)
        self.assertEqual(data, self.fx.pdf.read_bytes())
        self.assertEqual(mime, 'application/pdf')
        self.assertEqual(name, '07_report.pdf')

    def test_original_rejects_altered_bytes_and_bad_ids(self):
        self.fx.pdf.write_bytes(b'%PDF-1.4 altered')
        self.assertIsNone(reg.original('jpmldoc-abc', self.fx.folder))
        self.assertIsNone(reg.original('../validation.json', self.fx.folder))
        self.assertIsNone(reg.original('', self.fx.folder))

    def test_documents_listing_is_path_free(self):
        docs = reg.documents(self.fx.folder)
        self.assertEqual(len(docs), 1)
        self.assertNotIn('raw_path', docs[0])
        self.assertEqual(docs[0]['url'], '/mdl-files/jpmldoc-abc')


class LiveSupplementTests(unittest.TestCase):
    """Runs against the real supplement when it is published; skipped otherwise."""

    def test_live_gate_and_counts(self):
        s = reg.summary()
        if not s['available']:
            self.skipTest('live supplement not ready: %s' % s.get('reason'))
        self.assertEqual(s['as_of'], AS_OF)
        self.assertEqual(s['counts']['mdls_pending'], 166)
        self.assertEqual(s['counts']['actions_pending'], 206182)
        self.assertEqual(s['counts']['total_actions'], 716121)
        top = reg.listing({'limit': 1})['results'][0]
        self.assertEqual(top['mdl_number'], 2738)
        d = reg.detail(3080)
        self.assertEqual(d['local_collections'][0]['collection_id'], 'mdl-3080')
        self.assertTrue(all(doc['url'].startswith('/mdl-files/') for doc in d['documents']))
        blob = reg.original(d['documents'][0]['document_id'])
        self.assertIsNotNone(blob)
        self.assertEqual(blob[1], 'application/pdf')


if __name__ == '__main__':
    unittest.main()
