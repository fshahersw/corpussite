"""Tests for the settlements adapter (generic view contract, fail-closed hash gate)."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settlements  # noqa: E402

REAL = settlements.DATA
NFL = 'settlement-2cba54e523008da5c5bf'
AFFF_MDL = 'mdl-docket-2873'


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='stl_gate_'))
        for name in ('validation.json', 'settlements.jsonl', 'documents.jsonl', 'rejected_captures.jsonl',
                     'court_documents.jsonl', 'edges.jsonl'):
            shutil.copy2(REAL / name, self.tmp / name)
        settlements.DATA = self.tmp
        settlements._CACHE.clear()

    def tearDown(self):
        settlements.DATA = REAL
        settlements._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = settlements.listing({})
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertNotIn('results', result)
        self.assertIsNone(settlements.detail(NFL))
        self.assertIsNone(settlements.original('stlfile-' + '0' * 20))

    def test_copy_without_tamper_opens(self):
        self.assertTrue(settlements.listing({})['available'])

    def test_tampered_data_file_closes_gate(self):
        with open(self.tmp / 'settlements.jsonl', 'ab') as handle:
            handle.write(b'{"settlement_id":"settlement-injected","title":"x"}\n')
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.tmp / 'validation.json').unlink()
        self.assertClosed()

    def test_tampered_court_documents_close_gate(self):
        with open(self.tmp / 'court_documents.jsonl', 'ab') as handle:
            handle.write(b'{"court_document_id":"cldoc-1-1","courtlistener_url":"https://example.invalid/"}\n')
        self.assertClosed()
        self.assertIsNone(settlements.detail(AFFF_MDL))

    def test_unregistered_data_file_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['data_files'] = [f for f in gate['data_files'] if f['path'] != 'documents.jsonl']
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()


class ContractTests(unittest.TestCase):
    def setUp(self):
        settlements.DATA = REAL
        settlements._CACHE.clear()

    def test_listing_shape(self):
        result = settlements.listing({'limit': '500', 'page': '1'})
        self.assertTrue(result['available'])
        self.assertEqual(result['limit'], 100)
        self.assertEqual(result['page'], 1)
        self.assertEqual(result['total'], 869)  # 848 publisher references + 13 saved-page rows + 8 MDL court-docket rows
        self.assertEqual(len(result['results']), 100)
        self.assertIn('SettleSignal', result['qualification'])
        self.assertIn('licen', result['qualification'].lower())
        self.assertEqual([c['label'] for c in result['columns']], ['Settlement', 'Family', 'Deadline', 'Documents'])
        names = [f['name'] for f in result['filters']]
        self.assertEqual(names, ['q', 'family', 'deadline_state', 'mass_tort', 'has_documents', 'has_court_documents', 'state',
                                 'doc_type', 'dfrom', 'dto'])
        for flt in result['filters']:
            if flt['type'] == 'select':
                self.assertTrue(flt['options'])
                for option in flt['options']:
                    self.assertEqual(set(option), {'value', 'label', 'count'})
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'title', 'subtitle', 'cells', 'badges', 'links'})
            self.assertEqual(set(row['cells']), {'settlement', 'family', 'deadline', 'documents'})

    def test_filters_on_real_data(self):
        self.assertEqual(settlements.listing({'has_documents': 'yes'})['total'], 35)
        family = settlements.listing({'family': 'mdl_mass_tort', 'limit': '100'})
        self.assertIn(NFL, [r['id'] for r in family['results']])
        within = settlements.listing({'deadline_state': 'within_30_days', 'limit': '100'})
        self.assertEqual(within['total'], 76)
        ranged = settlements.listing({'dfrom': '2026-09-19', 'dto': '2026-10-19', 'limit': '100'})
        self.assertEqual(ranged['total'], 76)
        self.assertEqual(settlements.listing({'q': 'national football'})['total'], 1)
        ca = settlements.listing({'state': 'CA'})
        self.assertEqual(ca['total'], 29)
        homes = settlements.listing({'doc_type': 'settlement_website_home'})
        self.assertEqual(homes['total'], 22)
        self.assertEqual(settlements.listing({'family': 'no-such-family'})['total'], 0)
        self.assertEqual(settlements.listing({'page': 'x', 'limit': '-3'})['page'], 1)

    def test_detail_and_original(self):
        self.assertIsNone(settlements.detail('settlement-does-not-exist'))
        self.assertIsNone(settlements.detail('../validation.json'))
        item = settlements.detail(NFL)
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'})
        labels = [fact[0] for fact in item['facts']]
        self.assertIn('Publisher status (publisher-reported)', labels)
        self.assertIn('Verification', labels)
        documents = [s for s in item['sections'] if s['heading'].startswith('Documents')][0]
        urls = [link['url'] for entry in documents['items'] for link in entry['links']]
        served = [u for u in urls if u.startswith('/supplement-files/settlements/stlfile-')]
        self.assertEqual(len(served), 1)
        self.assertIn('https://www.nflconcussionsettlement.com/', urls)
        file_id = served[0].rsplit('/', 1)[1]
        data, mime, filename = settlements.original(file_id)
        self.assertEqual(mime, 'text/html')
        self.assertTrue(filename.startswith('stlfile-'))
        self.assertEqual(hashlib.sha256(data).hexdigest()[:20], file_id.split('-', 1)[1])
        self.assertIsNone(settlements.original('stlfile-../../validation.json'))
        self.assertIsNone(settlements.original('stlfile-' + 'f' * 20))

    def test_public_dicts_carry_no_paths(self):
        blob = json.dumps([settlements.listing({'limit': '100'}), settlements.detail(NFL), settlements.detail(AFFF_MDL),
                           settlements.detail('mdl-docket-2875')])
        for needle in ('raw_path', 'receipt_path', 'packet1/', 'C:/', 'Downloads', 'receipts_court', '856-576'):
            self.assertNotIn(needle, blob)

    def test_mdl_court_docket_rows(self):
        court = settlements.listing({'has_court_documents': 'yes', 'limit': '100'})
        self.assertEqual(court['total'], 8)
        self.assertEqual(settlements.listing({'family': 'mdl_court_docket'})['total'], 8)
        self.assertEqual(settlements.listing({'has_court_documents': 'no'})['total'], 861)
        self.assertEqual(settlements.listing({'has_documents': 'yes', 'has_court_documents': 'yes'})['total'], 0)
        family = [f for f in court['filters'] if f['name'] == 'family'][0]
        self.assertIn('MDL / mass tort (court docket evidence)', [o['label'] for o in family['options']])
        row = [r for r in court['results'] if r['id'] == AFFF_MDL][0]
        self.assertEqual(row['title'], 'Settlement-phrase docket search for MDL 2873')
        self.assertIn('Docket-entry evidence (not a settlement record or amount)', row['badges'])
        self.assertIn('Settlement-specific phrase: 20 of 20 entries', row['badges'])
        self.assertIn('entries matching a settlement-specific phrase: 20 of 20', row['subtitle'])
        self.assertIn({'label': 'MDL 2873 page', 'url': '#mdl/2873'}, row['links'])
        item = settlements.detail(AFFF_MDL)
        self.assertEqual(set(item), {'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'})
        facts = dict((f[0], f[1]) for f in item['facts'])
        self.assertIn('not a settlement record or amount', facts['Record layer'])
        self.assertIn('first result page only', facts['Search coverage'])
        self.assertNotIn('Amount as printed in title', facts)
        section = [s for s in item['sections'] if s['heading'].startswith('Court docket entries')][0]
        self.assertEqual(len(section['items']), 20)
        for entry in section['items']:
            self.assertTrue(entry['title'].startswith('Date filed 20'))
            self.assertTrue(all(link['url'].startswith('https://www.courtlistener.com/') for link in entry['links']))
        first = section['items'][0]
        self.assertIn('Date filed 2026-09-10', first['title'])
        self.assertIn('Preliminary approval order', first['title'])
        self.assertIn('typed from the docket description text only', first['subtitle'])
        self.assertEqual(settlements.listing({'q': 'talcum'})['total'], 1)

    def test_mdls_without_settlement_specific_entry_are_not_shown_as_settlement_activity(self):
        # Regression (review 2026-09-19): MDL 3060 matched only "common benefit"; MDL 2738 only "common benefit" / "order approving".
        court = settlements.listing({'has_court_documents': 'yes', 'limit': '100'})
        for row in court['results']:
            self.assertNotIn('settlement activity', row['title'].lower())
        for number, total, only in ((3060, 17, 'common benefit'), (2738, 14, 'common benefit, order approving')):
            sid = 'mdl-docket-%d' % number
            row = [r for r in court['results'] if r['id'] == sid][0]
            self.assertEqual(row['title'], 'Settlement-phrase docket search for MDL %d: no settlement-specific entry found' % number)
            self.assertIn('No settlement-specific entry found (matched only: %s)' % only, row['badges'])
            self.assertIn('entries matching a settlement-specific phrase: 0 of %d' % total, row['subtitle'])
            self.assertIn('0 match a settlement-specific phrase', row['cells']['documents'])
            item = settlements.detail(sid)
            facts = dict((f[0], f[1]) for f in item['facts'])
            self.assertEqual(facts['Entries matching a settlement-specific phrase'], '0 of %d captured entries' % total)
            self.assertIn('No settlement-specific entry found', facts['Finding'])
            self.assertIn('common benefit', facts['Finding'])
            self.assertNotIn('settlement activity', facts['Record layer'].lower())
            headings = [s['heading'] for s in item['sections']]
            self.assertTrue(any(h.startswith('Court docket entries matching a settlement-specific phrase (0') for h in headings))
            other = [s for s in item['sections'] if s['heading'].startswith('Court docket entries matching only a non-specific phrase')][0]
            self.assertEqual(len(other['items']), total)
            for entry in other['items']:
                self.assertNotIn('settlement-administration', entry['title'])
                self.assertIn('non-specific phrase only', entry['subtitle'])
        self.assertIn('first result page only', dict((f[0], f[1]) for f in settlements.detail('mdl-docket-2738')['facts'])['Finding'])
        mixed = settlements.detail('mdl-docket-2846')
        self.assertEqual(dict((f[0], f[1]) for f in mixed['facts'])['Entries matching a settlement-specific phrase'], '4 of 7 captured entries')
        self.assertNotIn('Finding', dict((f[0], f[1]) for f in mixed['facts']))

    def test_page_reference_row_is_labelled(self):
        result = settlements.listing({'q': 'AFFF'})
        self.assertEqual(result['total'], 1)
        row = result['results'][0]
        self.assertIn('Saved official page (no catalog record)', row['badges'])
        item = settlements.detail(row['id'])
        self.assertIn('not verified', dict((f[0], f[1]) for f in item['facts'])['Verification'])


if __name__ == '__main__':
    unittest.main()
