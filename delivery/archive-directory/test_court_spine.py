"""Adapter tests for the court spine supplement (generic view contract, round 3). Written before the adapter."""
import hashlib
import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('court_spine_adapter', HERE / 'court_spine.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

PHONE = re.compile(r'\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b')


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def assert_public(test, payload):
    for key, value in walk(payload):
        test.assertNotIn('path', key)
        if isinstance(value, str):
            test.assertNotIn(':\\', value)
            test.assertNotIn('Users/', value)
            test.assertNotIn('sources/court_spine', value)
            test.assertNotIn('SCRAPE', value)
            test.assertNotIn('Court-Document-Library', value)
            test.assertNotIn('mailto:', value)
            test.assertIsNone(PHONE.search(value), value)


class ListingTests(unittest.TestCase):
    def test_shape_and_total(self):
        d = a.listing({'limit': '5'})
        self.assertTrue(d['available'])
        self.assertTrue(d['total'] >= 3361)
        self.assertEqual((d['page'], d['limit'], len(d['results'])), (1, 5, 5))
        self.assertTrue(d['qualification'])
        self.assertEqual([f['name'] for f in d['filters']], ['q', 'system', 'type', 'state', 'has_logo', 'has_mdls'])
        self.assertEqual([c['label'] for c in d['columns']], ['Court', 'System', 'State', 'MDLs'])
        for f in d['filters'][1:]:
            self.assertEqual(f['type'], 'select')
            self.assertTrue(all({'value', 'label', 'count'} <= set(o) for o in f['options']))
        keys = [c['key'] for c in d['columns']]
        for r in d['results']:
            self.assertTrue({'id', 'title', 'subtitle', 'cells', 'badges', 'links'} <= set(r))
            self.assertEqual(set(r['cells']), set(keys))
            self.assertTrue(all(isinstance(v, str) for v in r['cells'].values()))

    def test_filters(self):
        fd = a.listing({'type': 'FD', 'limit': '100'})
        self.assertEqual(fd['total'], 125)
        self.assertTrue(all(r['cells']['system'] == 'Federal' for r in fd['results']))
        cl = a.listing({'type': 'FD'})['total'] + a.listing({'type': 'ST'})['total']
        self.assertEqual(cl, 125 + 2618)
        nj = a.listing({'state': 'NJ', 'limit': '100'})
        self.assertIn('njd', {r['id'] for r in nj['results']})
        self.assertTrue(all(r['cells']['state'] == 'NJ' for r in nj['results']))
        self.assertEqual(a.listing({'state': 'nj'})['total'], nj['total'])
        logos = a.listing({'has_logo': 'yes', 'limit': '1'})
        self.assertTrue(150 <= logos['total'] <= 240)
        self.assertEqual(a.listing({'has_logo': 'no'})['total'] + logos['total'], a.listing({})['total'])
        mdls = a.listing({'has_mdls': 'yes', 'limit': '100'})
        self.assertTrue(30 <= mdls['total'] <= 94)
        self.assertTrue(all(int(r['cells']['mdls'].split()[0]) > 0 for r in mdls['results']))
        self.assertTrue(all(any(l['url'].startswith('#mdls?court=') for l in r['links']) for r in mdls['results']))
        self.assertEqual(a.listing({'state': 'ZZ'})['total'], 0)
        self.assertEqual(a.listing({'type': "FD' OR 1=1"})['total'], 0)
        self.assertEqual(a.listing({'system': 'federal', 'has_mdls': 'yes'})['total'], mdls['total'])

    def test_search_and_paging(self):
        hits = a.listing({'q': 'new jersey', 'limit': '100'})
        self.assertIn('njd', {r['id'] for r in hits['results']})
        first = a.listing({'type': 'FD', 'limit': ['2'], 'page': ['1']})
        second = a.listing({'type': 'FD', 'limit': '2', 'page': '2'})
        self.assertFalse({r['id'] for r in first['results']} & {r['id'] for r in second['results']})
        self.assertEqual(a.listing({'limit': '5000'})['limit'], 100)
        self.assertEqual(a.listing({'limit': 'x', 'page': '-1'})['page'], 1)
        self.assertEqual(a.listing({'q': "' OR 1=1; --"})['total'], 0)
        self.assertEqual(a.listing(None)['page'], 1)

    def test_public_dicts(self):
        for payload in (a.listing({'has_logo': 'yes', 'limit': '100'}), a.listing({'state': 'TX', 'limit': '100'}),
                        a.listing({'state': 'KY', 'limit': '100', 'page': '2'}), a.detail('njd'), a.detail('pactcomplphilad'),
                        a.detail('reg-ST-nj_state'), a.detail('reg-LC-mo_st_louis')):
            self.assertIsNotNone(payload)
            assert_public(self, payload)


class DetailTests(unittest.TestCase):
    def test_real_data_federal_district(self):
        d = a.detail('njd')
        self.assertEqual(d['id'], 'njd')
        self.assertEqual(d['title'], 'District Court, D. New Jersey')
        facts = dict((f[0], f[1]) for f in d['facts'])
        self.assertEqual(facts['CourtListener court id'], 'njd')
        self.assertEqual(facts['System'], 'Federal')
        self.assertEqual(facts['State'][:2], 'NJ')
        self.assertTrue(any(k.startswith('Established') and v == '1789-09-24' for k, v in facts.items()))
        self.assertTrue(any('2026-06-30' in k or '2026-06-30' in v for k, v in facts.items()))
        urls = [l['url'] for l in d['links']]
        self.assertIn('#documents?q=District%20Court%2C%20D.%20New%20Jersey', urls)
        self.assertIn('http://www.njd.uscourts.gov/', urls)
        self.assertTrue(any(u.startswith('/supplement-files/court_spine/logo_') for u in urls))
        pending = [f for f in d['facts'] if f[0].startswith('Pending MDLs')]
        self.assertTrue(pending and int(pending[0][1]) >= 1)
        self.assertIn('#mdls?court=njd', urls)
        self.assertTrue(d['qualification'])

    def test_no_mdl_link_without_mdls(self):
        d = a.detail('ca3')
        self.assertFalse(any(l['url'].startswith('#mdls') for l in d['links']))
        self.assertNotIn('State', dict((f[0], f[1]) for f in d['facts']))

    def test_reviewed_crosswalk_and_unresolved(self):
        d = a.detail('pactcomplphilad')
        facts = dict((f[0], f[1]) for f in d['facts'])
        self.assertIn('LC:pa_philadelphia', facts['Local registry key'])
        self.assertEqual(facts['County FIPS'][:5], '42101')
        self.assertIn('LC:', dict((f[0], f[1]) for f in a.detail('reg-LC-mo_st_louis')['facts'])['Local registry key'])
        self.assertIn('FB:azb', dict((f[0], f[1]) for f in a.detail('arb')['facts'])['Local registry key'])
        st = a.detail('reg-ST-nj_state')
        self.assertIn('not a CourtListener court', st['qualification'])

    def test_county_registry_locations(self):
        hits = a.listing({'q': '29th Judicial Circuit', 'state': 'KY', 'type': 'CR:Circuit Court'})
        self.assertEqual(hits['total'], 1)
        d = a.detail(hits['results'][0]['id'])
        sec = next(s for s in d['sections'] if s['heading'].startswith('Locations'))
        self.assertEqual(sec['header'][0], 'County FIPS')
        self.assertEqual(sorted(r[0] for r in sec['rows']), ['21001', '21045'])
        harris = a.listing({'q': 'Harris County', 'state': 'TX', 'limit': '100'})
        self.assertTrue(harris['total'] >= 10)

    def test_hostile_ids(self):
        for value in ('zzz', '', None, "njd' OR 1=1", '../courts.jsonl', 17, 'NJD', 'x' * 500):
            self.assertIsNone(a.detail(value))


class OriginalTests(unittest.TestCase):
    def _file_id(self):
        url = next(l['url'] for l in a.detail('njd')['links'] if l['url'].startswith('/supplement-files/court_spine/'))
        return url.rsplit('/', 1)[1]

    def test_verified_raster_bytes(self):
        fid = self._file_id()
        data, mime, name = a.original(fid)
        self.assertIn(mime, ('image/png', 'image/jpeg', 'image/gif', 'image/webp'))
        self.assertEqual('logo_' + hashlib.sha256(data).hexdigest()[:16], fid)
        self.assertTrue(name.startswith('logo_') and '/' not in name and '\\' not in name)
        for bad in ('../validation.json', 'logo_', None, 5, 'logo_zzzzzzzzzzzzzzzz', 'assets/x.png', fid + '.png'):
            self.assertIsNone(a.original(bad))

    def test_tampered_file_is_not_served(self):
        fid = self._file_id()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'copy'
            shutil.copytree(a.DATA, folder, ignore=shutil.ignore_patterns('__pycache__', '_inspect'))
            original = a.DATA
            try:
                a.DATA = folder
                self.assertIsNotNone(a.original(fid))
                target = next(p for p in (folder / 'assets').iterdir() if p.stem.startswith(fid[5:]))
                target.write_bytes(target.read_bytes() + b'\n')
                self.assertIsNone(a.original(fid))
            finally:
                a.DATA = original

    def test_no_svg_registered(self):
        rows = [json.loads(l) for l in (a.DATA / 'logos.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertTrue(rows)
        self.assertTrue(all(r['mime'] in ('image/png', 'image/jpeg', 'image/gif', 'image/webp') for r in rows))
        self.assertTrue(all(r['permission_status'] == 'not_established' and isinstance(r['shared_mark'], bool) for r in rows))


class GateTests(unittest.TestCase):
    SOURCE = a.DATA

    def _copy(self, tmp):
        folder = Path(tmp) / 'copy'
        shutil.copytree(self.SOURCE, folder, ignore=shutil.ignore_patterns('__pycache__', 'assets', '_inspect'))
        return folder

    def test_gate_fails_closed(self):
        original = a.DATA
        try:
            with tempfile.TemporaryDirectory() as tmp:
                a.DATA = self._copy(tmp)
                self.assertTrue(a.listing({})['available'])
                with open(a.DATA / 'courts.jsonl', 'ab') as handle:
                    handle.write(b'{"id": "injected"}\n')
                d = a.listing({})
                self.assertFalse(d['available'])
                self.assertTrue(d['reason'])
                self.assertIsNone(a.detail('njd'))
                self.assertIsNone(a.original('logo_0000000000000000'))
            with tempfile.TemporaryDirectory() as tmp:
                a.DATA = self._copy(tmp)
                gate = json.loads((a.DATA / 'validation.json').read_text(encoding='utf-8'))
                gate['ready'] = False
                (a.DATA / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
                self.assertFalse(a.listing({})['available'])
            a.DATA = Path(tempfile.gettempdir()) / 'court-spine-does-not-exist'
            self.assertFalse(a.listing({})['available'])
            self.assertIsNone(a.detail('njd'))
        finally:
            a.DATA = original


if __name__ == '__main__':
    unittest.main()
