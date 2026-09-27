"""Adapter tests for mdl_counsel (generic view contract, fail-closed hash gate, privacy)."""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mdl_counsel  # noqa: E402

EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?<!\d)\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)')
DATA_FILES = ('validation.json', 'attorneys.jsonl', 'firms.jsonl', 'parties.jsonl', 'coverage.json', 'edges.jsonl')


class GateTests(unittest.TestCase):
    def _copy(self):
        tmp = Path(tempfile.mkdtemp(prefix='mdl_counsel_test_'))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in DATA_FILES:
            shutil.copy2(mdl_counsel.SOURCE_DIR / name, tmp / name)
        return tmp

    def test_copy_passes_gate(self):
        out = mdl_counsel.listing({}, source_dir=self._copy())
        self.assertTrue(out['available'])

    def test_tampered_data_file_fails_closed(self):
        tmp = self._copy()
        with (tmp / 'firms.jsonl').open('a', encoding='utf-8') as handle:
            handle.write('{"id":"firm-evil","kind":"firm","name":"Injected LLP","mdls":[],"mdl_numbers":[]}\n')
        out = mdl_counsel.listing({}, source_dir=tmp)
        self.assertFalse(out['available'])
        self.assertIn('firms.jsonl', out['reason'])
        self.assertIsNone(mdl_counsel.detail('firm-evil', source_dir=tmp))

    def test_status_not_passed_fails_closed(self):
        tmp = self._copy()
        val = json.loads((tmp / 'validation.json').read_text(encoding='utf-8'))
        val['status'] = 'failed'
        (tmp / 'validation.json').write_text(json.dumps(val), encoding='utf-8')
        self.assertFalse(mdl_counsel.listing({}, source_dir=tmp)['available'])

    def test_missing_folder_fails_closed(self):
        out = mdl_counsel.listing({}, source_dir=HERE / 'no_such_folder_mdl_counsel')
        self.assertEqual(out['available'], False)
        self.assertTrue(out['reason'])


class ContractTests(unittest.TestCase):
    def test_default_kind_is_firm_and_shape(self):
        out = mdl_counsel.listing({})
        self.assertTrue(out['available'])
        for key in ('total', 'page', 'limit', 'qualification', 'filters', 'columns', 'results'):
            self.assertIn(key, out)
        self.assertEqual([c['label'] for c in out['columns']], ['Name', 'MDL', 'Role or Type', 'Count'])
        self.assertLessEqual(len(out['filters']), 7)
        self.assertEqual([f['name'] for f in out['filters']], ['q', 'kind', 'mdl'])
        kind = next(f for f in out['filters'] if f['name'] == 'kind')
        self.assertEqual({o['value'] for o in kind['options']}, {'firm', 'attorney', 'party'})
        self.assertTrue(all(r['id'].startswith('firm-') for r in out['results']))
        row = out['results'][0]
        for key in ('id', 'title', 'subtitle', 'cells', 'badges', 'links'):
            self.assertIn(key, row)
        self.assertEqual(set(row['cells']), {'name', 'mdl', 'role', 'count'})

    def test_pagination_and_limit_cap(self):
        out = mdl_counsel.listing({'kind': 'attorney', 'limit': '5000', 'page': '2'})
        self.assertEqual(out['limit'], 100)
        self.assertEqual(out['page'], 2)
        self.assertEqual(len(out['results']), 100)
        first = mdl_counsel.listing({'kind': 'attorney', 'limit': '100', 'page': '1'})
        self.assertFalse({r['id'] for r in first['results']} & {r['id'] for r in out['results']})

    def test_real_data_bair_hugger_attorney_record(self):
        out = mdl_counsel.listing({'kind': 'attorney', 'mdl': '2666', 'q': 'zimmerman'})
        self.assertEqual(out['total'], 1)
        row = out['results'][0]
        self.assertEqual(row['id'], 'clatt-7167601-mdl2666')
        self.assertIn('Lead attorney', row['cells']['role'])
        self.assertIn({'label': 'MDL 2666', 'url': '#mdl/2666'}, row['links'])
        det = mdl_counsel.detail(row['id'])
        self.assertEqual(det['title'], 'Genevieve M Zimmerman')
        facts = dict(det['facts'])
        self.assertEqual(facts['Firm line (as recorded in contact block)'], 'Meshbesher & Spence')
        self.assertEqual(facts['CourtListener attorney id'], '7167601')
        parties = next(s for s in det['sections'] if s['heading'].startswith('Parties represented'))
        self.assertIn("Plaintiff's Co-Lead Counsel", [r[0] for r in parties['rows']])

    def test_real_data_by_id_record_has_id_and_firm_line_but_no_roles(self):
        out = mdl_counsel.listing({'kind': 'attorney', 'mdl': '2738', 'q': "o'dell"})
        hit = [r for r in out['results'] if r['id'] == 'clatt-1679024-mdl2738']
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]['badges'], ['attorney record'])
        self.assertIn('not retrieved', hit[0]['cells']['role'])
        self.assertFalse([r for r in out['results'] if r['id'] != hit[0]['id'] and r['title'].casefold() == hit[0]['title'].casefold()])
        det = mdl_counsel.detail('clatt-1679024-mdl2738')
        facts = dict(det['facts'])
        self.assertEqual(facts['Firm line (as recorded in contact block)'], 'BEASLEY ALLEN CROW METHVIN PORTIS & MILES PC')
        self.assertEqual(facts['CourtListener attorney id'], '1679024')
        self.assertIn('search-index attorney_id set', facts['Basis of the MDL link'])
        self.assertTrue(any(s['heading'] == 'Roles and parties represented' and s['text'].startswith('Not retrieved') for s in det['sections']))
        blob = json.dumps(det)
        self.assertNotIn('@', blob)
        self.assertNotIn('COMMERCE STREET', blob.upper())
        firms = mdl_counsel.listing({'kind': 'firm', 'mdl': '2789', 'q': 'seeger weiss llp'})
        self.assertTrue(any(r['title'].casefold() == 'seeger weiss llp' and r['cells']['count'] == '1 attorney record' for r in firms['results']))

    def test_firm_detail_lists_attorneys_per_mdl(self):
        out = mdl_counsel.listing({'kind': 'firm', 'q': 'norton rose fulbright us llp', 'mdl': '2666'})
        exact = [r for r in out['results'] if r['title'] == 'Norton Rose Fulbright US LLP']
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]['cells']['count'], '3 attorney records')
        det = mdl_counsel.detail(exact[0]['id'])
        sec = next(s for s in det['sections'] if 'MDL 2666' in s['heading'])
        self.assertEqual(sorted(i['title'] for i in sec['items']), ['Benjamin W. Hulse', 'Mary S. Young', 'Nolan Leuthauser'])
        self.assertTrue(all({'label': 'MDL 2666', 'url': '#mdl/2666'} in i['links'] for i in sec['items']))

    def _all_firm_results(self):
        rows, page = [], 1
        while True:
            out = mdl_counsel.listing({'kind': 'firm', 'limit': '100', 'page': str(page)})
            rows += out['results']
            if page * 100 >= out['total']:
                return out['total'], rows
            page += 1

    def test_no_litigant_address_line_or_admission_note_is_served_as_a_firm(self):
        bad = re.compile(r'#|^c\s*/\s*o\b|\b(correction(al|s)?|prison|penitentiary|detention|inmate|jail)\b|'
                         r'\b(not|no)\s+(a\s+)?(ad[a-z]*m[a-z]*t[a-z]*d|member)\b|\bu[sd]{2}c|\b(suite|ste|floor)\b|\b\d+(st|nd|rd|th)\s+fl\b', re.I)
        total, rows = self._all_firm_results()
        self.assertEqual(total, len(rows))
        self.assertGreater(total, 1000)
        self.assertLess(total, 2441)  # 2,441 before the 2026-09-19 repair
        for row in rows:
            self.assertIsNone(bad.search(row['title']), row['id'])
        for query in ('Dixon', 'Correctional', "Rock's House", 'Not Admitted', 'Usdc'):
            self.assertEqual(mdl_counsel.listing({'kind': 'firm', 'q': query})['total'], 0, query)
        self.assertIsNone(mdl_counsel.detail('firm-b1da628a19c9c845'))  # former inmate-number row
        for row in rows[::40]:
            self.assertIsNone(bad.search(json.dumps(mdl_counsel.detail(row['id'])['facts'])), row['id'])

    def test_note_prefixed_spellings_are_one_firm_row_and_the_removal_is_disclosed(self):
        out = mdl_counsel.listing({'kind': 'firm', 'q': 'motley rice llc', 'mdl': '2738'})
        exact = [r for r in out['results'] if r['title'].casefold() == 'motley rice llc']
        self.assertEqual(len(exact), 1)
        det = mdl_counsel.detail(exact[0]['id'])
        note = [f for f in det['facts'] if f[0].startswith('Bar-admission note')]
        self.assertEqual(len(note), 1)
        self.assertRegex(note[0][1], r'^\d+ of \d+ source text')
        self.assertIn('note', det['qualification'])
        counts = {o['value']: o['count'] for o in next(f for f in out['filters'] if f['name'] == 'kind')['options']}
        self.assertEqual(counts['firm'], 849)  # MDL 2738 firm rows after the repair (951 before)

    def test_party_kind_and_type_as_recorded(self):
        out = mdl_counsel.listing({'kind': 'party', 'mdl': '2666', 'q': 'steering'})
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['results'][0]['cells']['role'], 'Plaintiff')
        self.assertEqual(out['results'][0]['cells']['count'], '13 attorney records')
        withheld = mdl_counsel.listing({'kind': 'party', 'mdl': '2738'})
        self.assertEqual(withheld['total'], 0)

    def test_unknown_ids_and_bad_params(self):
        self.assertIsNone(mdl_counsel.detail('nope'))
        self.assertIsNone(mdl_counsel.detail(''))
        out = mdl_counsel.listing({'kind': 'bogus', 'page': 'x', 'limit': '-3', 'mdl': 'abc'})
        self.assertTrue(out['available'])
        self.assertEqual(out['page'], 1)

    def test_no_contact_details_or_paths_in_public_dicts(self):
        blobs = []
        for kind in ('firm', 'attorney', 'party'):
            out = mdl_counsel.listing({'kind': kind, 'mdl': '2666', 'limit': '100'})
            blobs.append(json.dumps(out))
            for row in out['results'][:40]:
                blobs.append(json.dumps(mdl_counsel.detail(row['id'])))
        text = '\n'.join(blobs)
        self.assertIsNone(EMAIL.search(text))
        self.assertIsNone(PHONE.search(text))
        self.assertNotIn('C:\\\\', text)
        self.assertNotIn('C:/', text)
        self.assertNotIn('receipts/', text)
        self.assertNotIn('contact_raw', text)


if __name__ == '__main__':
    unittest.main()
