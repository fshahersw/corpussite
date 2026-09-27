"""Tests for the generic-contract adapter court_statistics.py (listing / detail / original + fail-closed hash gate)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import court_statistics as stats


def sha(data):
    return hashlib.sha256(data).hexdigest()


TEMPORAL = {
    'captured_at': '2026-09-19T05:37:08Z', 'captured_at_basis': 'HTTP request timestamp in the fetch receipt',
    'source_as_of': None, 'source_as_of_basis': None, 'published_at': None, 'published_at_basis': None,
    'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None,
}


class CourtStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / 'supplement'
        (self.folder / 'raw/A').mkdir(parents=True)
        (self.folder / 'raw/B').mkdir(parents=True)
        self.xlsx = b'PK\x03\x04 fake workbook bytes'
        self.pdf = b'%PDF-1.7 fake cjra table'
        (self.folder / 'raw/A/A009__stfj_c3.xlsx').write_bytes(self.xlsx)
        (self.folder / 'raw/B/B004__cjra_8.pdf').write_bytes(self.pdf)
        self.tables = [
            {'table_id': 'stfj:C-3:2026-06-30:xlsx', 'seed_id': 'A009', 'table_number': 'C-3',
             'title': 'U.S. District Courts - Civil Cases Filed, by Jurisdiction, Nature of Suit and District', 'title_in_file': 'Table C-3. ...',
             'series': 'Statistical Tables', 'series_code': 'stfj', 'publication_as_listed': 'Statistical Tables For The Federal Judiciary',
             'topic': 'Civil caseload', 'period_label': '12-Month Period Ending June 30, 2026', 'period_start': '2025-07-01',
             'period_end': '2026-06-30', 'period_basis': 'title line inside the workbook (first sheet)', 'format': 'xlsx',
             'bytes': len(self.xlsx), 'sha256': sha(self.xlsx), 'file_id': 'a009-' + sha(self.xlsx)[:12], 'download_name': 'A009_stfj_c3.xlsx',
             'source_page': 'https://www.uscourts.gov/data-news/data-tables/2026/06/30/statistical-tables-federal-judiciary/c-3',
             'source_url': 'https://www.uscourts.gov/sites/default/files/document/stfj_c3_630.2026.xlsx', 'sheet_names': ['Table C-3'],
             'page_count': None,
             'preview': [{'sheet': 'Table C-3', 'title_lines': ['Table C-3. ...'], 'header_rows': [['Circuit and District', 'Total Civil Cases', 'U.S. Cases'], [None, None, 'Contract']],
                          'data_rows': [['Total', 359059, 104957.5], ['DC', 4685, None]], 'non_empty_rows_in_sheet': 110, 'data_rows_in_sheet': 107,
                          'truncated': True, 'columns_truncated': False}],
             'raw_path': 'raw/A/A009__stfj_c3.xlsx', 'figure_label': 'publisher-reported counts for 12-Month Period Ending June 30, 2026; never a rate computed by this archive',
             **TEMPORAL},
            {'table_id': 'cjra:CJRA-8:2026-03-31:pdf', 'seed_id': 'B004', 'table_number': 'CJRA 8',
             'title': 'U.S. District Courts - Motions Pending More Than Six Months', 'title_in_file': None, 'series': 'CJRA', 'series_code': 'cjra',
             'publication_as_listed': 'Civil Justice Reform Act (CJRA)', 'topic': 'CJRA per-judge', 'period_label': 'As of March 31, 2026',
             'period_start': None, 'period_end': '2026-03-31', 'period_basis': '"As of" line printed on page 1 of the PDF', 'format': 'pdf',
             'bytes': len(self.pdf), 'sha256': sha(self.pdf), 'file_id': 'b004-' + sha(self.pdf)[:12], 'download_name': 'B004_cjra_8.pdf',
             'source_page': 'https://www.uscourts.gov/data-news/data-tables/2026/03/31/civil-justice-reform-act-cjra/cjra-8',
             'source_url': 'https://www.uscourts.gov/sites/default/files/document/cjra_8_0331.2026.pdf', 'sheet_names': [], 'page_count': 2,
             'preview': [], 'raw_path': 'raw/B/B004__cjra_8.pdf', 'figure_label': 'publisher-reported counts for As of March 31, 2026',
             'cjra_per_judge': {'judge_rows': 2, 'sum_of_counts': 13, 'structural_ok': True, 'reconciled_circuits': 1, 'circuits_compared': 1,
                                'reconciliation_vs_cjra_table_2': [{'circuit': 'Second', 'sum_of_per_judge_totals': 13, 'cjra_table_2_printed': 13, 'match': True}]},
             **{**TEMPORAL, 'source_as_of': '2026-03-31', 'source_as_of_basis': '"As of" line printed on page 1 of the PDF'}},
        ]
        self.cjra = [
            {'row_id': 'cjra8:00001', 'table_id': 'cjra:CJRA-8:2026-03-31:pdf', 'table_number': 'CJRA 8', 'circuit_as_printed': '2nd Circuit',
             'court_as_printed': 'U.S. District Court for New York Southern', 'judge_type_as_printed': 'District Judge',
             'judge_name_as_printed': 'Doe, Jane Q.', 'count_label': 'motions pending more than six months', 'count': 4, 'pdf_pages': [1, 1],
             'period_label': 'As of March 31, 2026', 'period_end': '2026-03-31', 'judge_match': {'status': 'not_attempted'}, **TEMPORAL},
            {'row_id': 'cjra8:00002', 'table_id': 'cjra:CJRA-8:2026-03-31:pdf', 'table_number': 'CJRA 8', 'circuit_as_printed': '2nd Circuit',
             'court_as_printed': 'U.S. District Court for New York Southern', 'judge_type_as_printed': 'Magistrate Judge',
             'judge_name_as_printed': 'Roe, Sam', 'count_label': 'motions pending more than six months', 'count': 9, 'pdf_pages': [2, 2],
             'period_label': 'As of March 31, 2026', 'period_end': '2026-03-31', 'judge_match': {'status': 'not_attempted'}, **TEMPORAL},
        ]
        self.publish()

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, status='passed', ready=True):
        files = []
        for name, rows in (('tables.jsonl', self.tables), ('cjra_rows.jsonl', self.cjra)):
            payload = ''.join(json.dumps(r) + '\n' for r in rows).encode()
            (self.folder / name).write_bytes(payload)
            files.append({'path': name, 'sha256': sha(payload), 'rows': len(rows)})
        (self.folder / 'validation.json').write_text(json.dumps({
            'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T06:00:00Z', 'data_files': files,
            'counts': {}, 'checks': [], 'qualification': 'Test qualification.', 'license_ref': '', 'inputs': []}), encoding='utf-8')

    def assert_public(self, value):
        text = json.dumps(value)
        self.assertNotIn('raw/A', text)
        self.assertNotIn('raw_path', text)
        self.assertNotIn(str(self.folder).replace('\\', '\\\\'), text)

    def test_listing_shape_filters_and_pagination(self):
        out = stats.listing({}, folder=self.folder)
        self.assertTrue(out['available'])
        self.assertEqual((out['total'], out['page'], out['limit']), (2, 1, 25))
        self.assertEqual([c['label'] for c in out['columns']], ['Table', 'Series', 'Period', 'Format'])
        self.assertLessEqual(len(out['filters']), 7)
        self.assertEqual([f['name'] for f in out['filters']], ['q', 'series', 'topic', 'format', 'period'])
        series = next(f for f in out['filters'] if f['name'] == 'series')
        self.assertIn({'value': 'stfj', 'label': 'Statistical Tables', 'count': 1}, series['options'])
        first = out['results'][0]
        self.assertEqual(first['id'], 'stfj:C-3:2026-06-30:xlsx')  # newest period first
        self.assertEqual(set(first['cells']), {'table', 'series', 'period', 'format'})
        self.assertEqual(first['cells']['table'], 'C-3')
        self.assertIn('June 30, 2026', first['cells']['period'])
        self.assertTrue(any(l['url'] == '/supplement-files/court_statistics/' + self.tables[0]['file_id'] for l in first['links']))
        self.assertIn('never a rate', out['qualification'])
        self.assert_public(out)
        self.assertEqual(stats.listing({'series': 'cjra'}, folder=self.folder)['total'], 1)
        self.assertEqual(stats.listing({'topic': 'Civil caseload'}, folder=self.folder)['total'], 1)
        self.assertEqual(stats.listing({'format': 'pdf'}, folder=self.folder)['results'][0]['id'], 'cjra:CJRA-8:2026-03-31:pdf')
        self.assertEqual(stats.listing({'period': '2026-03-31'}, folder=self.folder)['total'], 1)
        self.assertEqual(stats.listing({'q': 'motions pending'}, folder=self.folder)['total'], 1)
        self.assertEqual(stats.listing({'q': 'c-3'}, folder=self.folder)['total'], 1)
        self.assertEqual(stats.listing({'series': 'nope'}, folder=self.folder)['total'], 0)
        page = stats.listing({'limit': '1', 'page': '2'}, folder=self.folder)
        self.assertEqual((page['total'], len(page['results']), page['page'], page['limit']), (2, 1, 2, 1))
        self.assertEqual(stats.listing({'limit': '100000', 'page': 'x'}, folder=self.folder)['limit'], 100)

    def test_detail_xlsx_has_facts_preview_and_links(self):
        item = stats.detail('stfj:C-3:2026-06-30:xlsx', folder=self.folder)
        facts = dict((f[0], f[1]) for f in item['facts'])
        self.assertIn('12-Month Period Ending June 30, 2026', facts['Reporting period (as printed)'])
        self.assertEqual(facts['Size'], '%d bytes' % len(self.xlsx))
        self.assertTrue(facts['SHA-256 (prefix)'].startswith(sha(self.xlsx)[:16]))
        self.assertTrue(any('Saved' in f[0] for f in item['facts']))
        self.assertEqual(facts['Source page'], self.tables[0]['source_page'])
        preview = next(s for s in item['sections'] if s['heading'].startswith('Preview'))
        self.assertEqual(preview['header'][2], 'U.S. Cases / Contract')
        self.assertEqual(preview['rows'][0], ['Total', '359,059', '104,957.5'])
        self.assertEqual(preview['rows'][1], ['DC', '4,685', ''])
        urls = [l['url'] for l in item['links']]
        self.assertIn('/supplement-files/court_statistics/' + self.tables[0]['file_id'], urls)
        self.assertIn(self.tables[0]['source_page'], urls)
        self.assertIn('never a rate', item['qualification'])
        self.assert_public(item)
        self.assertIsNone(stats.detail('missing', folder=self.folder))
        self.assertIsNone(stats.detail('', folder=self.folder))

    def test_detail_cjra_rows_names_as_printed_and_court_view(self):
        item = stats.detail('cjra:CJRA-8:2026-03-31:pdf', folder=self.folder)
        rows = next(s for s in item['sections'] if 'Per-judge' in s['heading'])
        self.assertEqual(rows['rows'][0][:4], ['U.S. District Court for New York Southern', 'District Judge', 'Doe, Jane Q.', '4'])
        recon = next(s for s in item['sections'] if 'Reconciliation' in s['heading'])
        self.assertEqual(recon['rows'][0], ['Second', '13', '13', 'matches'])
        courts = next(s for s in item['sections'] if s['heading'].startswith('Courts'))
        court_id = courts['items'][0]['id']
        view = stats.detail(court_id, folder=self.folder)
        self.assertEqual(len(next(s for s in view['sections'] if 'Per-judge' in s['heading'])['rows']), 2)
        self.assertIn('not linked to judge profiles', view['qualification'])
        self.assert_public(view)

    def test_original_serves_only_verified_bytes_inside_raw(self):
        payload, mime, name = stats.original(self.tables[0]['file_id'], folder=self.folder)
        self.assertEqual((payload, mime, name), (self.xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'A009_stfj_c3.xlsx'))
        self.assertEqual(stats.original(self.tables[1]['file_id'], folder=self.folder)[1], 'application/pdf')
        for bad in ('', 'missing', '../validation.json', self.tables[0]['file_id'] + '/x', 'raw/A/A009__stfj_c3.xlsx'):
            self.assertIsNone(stats.original(bad, folder=self.folder))
        (self.folder / 'raw/A/A009__stfj_c3.xlsx').write_bytes(b'PK changed')
        self.assertIsNone(stats.original(self.tables[0]['file_id'], folder=self.folder))

    def test_raw_path_outside_raw_is_rejected(self):
        outside = self.folder / 'outside.xlsx'
        outside.write_bytes(self.xlsx)
        self.tables[0]['raw_path'] = 'outside.xlsx'
        self.publish()
        self.assertIsNone(stats.original(self.tables[0]['file_id'], folder=self.folder))
        self.tables[0]['raw_path'] = 'raw/A/../../outside.xlsx'
        self.publish()
        self.assertIsNone(stats.original(self.tables[0]['file_id'], folder=self.folder))

    def test_gate_fails_closed_on_tamper_status_and_duplicates(self):
        with (self.folder / 'cjra_rows.jsonl').open('a') as out:
            out.write(' ')
        closed = stats.listing({}, folder=self.folder)
        self.assertEqual(closed['available'], False)
        self.assertIn('reason', closed)
        self.assertIsNone(stats.detail('stfj:C-3:2026-06-30:xlsx', folder=self.folder))
        self.assertIsNone(stats.original(self.tables[0]['file_id'], folder=self.folder))
        self.publish(status='failed')
        self.assertFalse(stats.listing({}, folder=self.folder)['available'])
        self.publish(ready=False)
        self.assertFalse(stats.listing({}, folder=self.folder)['available'])
        self.tables.append(dict(self.tables[0]))
        self.publish()
        self.assertFalse(stats.listing({}, folder=self.folder)['available'])
        self.tables.pop()
        self.publish()
        self.assertTrue(stats.listing({}, folder=self.folder)['available'])
        self.assertFalse(stats.listing({}, folder=Path(self.tmp.name) / 'absent')['available'])


class RealDataTests(unittest.TestCase):
    def test_published_supplement(self):
        out = stats.listing({'series': 'stfj', 'q': 'C-3'})
        if not out.get('available'):
            self.skipTest('supplement not published: %s' % out.get('reason'))
        self.assertEqual(out['results'][0]['id'], 'stfj:C-3:2026-06-30:xlsx')
        item = stats.detail('stfj:C-3:2026-06-30:xlsx')
        preview = next(s for s in item['sections'] if s['heading'].startswith('Preview'))
        self.assertEqual(preview['rows'][0][:2], ['Total', '359,059'])  # publisher-reported, 12-month period ending June 30, 2026
        file_id = item['links'][0]['url'].rsplit('/', 1)[-1]
        payload, mime, name = stats.original(file_id)
        self.assertEqual(sha(payload)[:12], file_id.split('-')[1])
        everything = stats.listing({'limit': '100'})
        self.assertEqual(everything['total'], 92)
        cjra = stats.detail('cjra:CJRA-7:2026-03-31:pdf')
        self.assertTrue(any('Reconciliation' in s['heading'] for s in cjra['sections']))


if __name__ == '__main__':
    unittest.main()
