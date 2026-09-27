"""Tests for the Trellis coverage adapter (written before the adapter)."""
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trellis_coverage as tc  # noqa: E402

LABEL = 'Trellis publisher-reported coverage metadata; internal research use; not for redistribution'
TEMPORAL = {
    'captured_at': '2026-09-19T05:40:00Z', 'captured_at_basis': 'agent clock reading in the same tool block as the connector call',
    'source_as_of': None, 'source_as_of_basis': None, 'published_at': None, 'published_at_basis': None,
    'effective_from': None, 'effective_from_basis': None, 'effective_to': None, 'effective_to_basis': None,
}


def county_row(state, state_name, name, fips, detail=False, docs=True, areas=(), venue=None, receipt='tc-0001'):
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    reported = None
    if detail:
        reported = {'population': 860807, 'area_sq_mi': 311, 'seat': 'New Brunswick', 'established_year': 1683,
                    'name_meaning': 'Named after Middlesex.', 'description': None, 'form_of_government': '',
                    'board_of_supervisor': '', 'website': 'https://www.example.gov/', 'phone_number': '732-745-3000',
                    'administration_address': '75 Bayard Street\r\nNew Brunswick, NJ 08901', 'court_address': None,
                    'additional_phones': []}
    return {
        'id': 'trellis_county:%s:%s' % (state, slug), 'record_type': 'trellis_county_coverage',
        'state': state, 'state_name': state_name, 'county': name, 'fips': fips,
        'fips_basis': 'exact county name + state match against the county inventory' if fips else None,
        'listed_in_state_coverage': True, 'courthouse_count_reported': 0, 'has_documents': docs,
        'detail_captured': detail, 'publisher_reported': reported,
        'courthouses_reported': [] if detail else None, 'courthouse_count_detail': 0 if detail else None,
        'practice_areas': list(areas) if detail else None,
        'practice_area_presence': {a: True for a in areas} if detail else None,
        'has_documents_detail': docs if detail else None, 'venue': venue,
        'receipts': {'state': receipt, 'county': 'tc-0002' if detail else None},
        'qualification': LABEL, 'license_ref': LABEL, **TEMPORAL,
    }


def state_row(state, state_name, counties, receipt='tc-0001'):
    return {
        'id': 'trellis_state:' + state, 'record_type': 'trellis_state_coverage', 'state': state, 'state_name': state_name,
        'coverage_flags': {'civil_cases': True, 'documents': True, 'tentative_rulings': False},
        'counties_listed': counties, 'counties_with_documents': counties, 'courthouse_count_total_reported': 0,
        'connector_error': None, 'receipt': receipt, 'qualification': LABEL, 'license_ref': LABEL, **TEMPORAL,
    }


def write_fixture(folder, status='passed', ready=True, corrupt=None):
    folder = Path(folder)
    (folder / 'raw').mkdir(parents=True, exist_ok=True)
    raw = b'{"error":null,"counties":[]}'
    (folder / 'raw/0001_get_state_coverage_nj.response.json').write_bytes(raw)
    receipts = [{'id': 'tc-0001', 'tool': 'get_state_coverage', 'arguments': {'state': 'nj'},
                 'called_at_utc': '2026-09-19T05:40:00Z', 'response_path': 'raw/0001_get_state_coverage_nj.response.json',
                 'response_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                 'record_kind': 'connector response, not original HTTP bytes'}]
    files = {
        'states.jsonl': [state_row('NJ', 'New Jersey', 2), state_row('MO', 'Missouri', 2)],
        'counties.jsonl': [
            county_row('NJ', 'New Jersey', 'Middlesex County', '34023', detail=True,
                       areas=('Civil', 'Consumer', 'Torts'), venue={'tier': 'primary', 'label': 'Middlesex County, NJ'}),
            county_row('NJ', 'New Jersey', 'Atlantic County', '34001', docs=False),
            county_row('MO', 'Missouri', 'St. Louis City', None),
            county_row('MO', 'Missouri', 'Jackson County', '29095', detail=True, areas=('Civil',)),
        ],
        'edges.jsonl': [{'from': {'type': 'fips', 'id': 'fips:34023'}, 'to': {'type': 'state', 'id': 'state:NJ'},
                         'relation': 'county_in_state', 'basis': 'listed', 'evidence': {'receipt': 'tc-0001'}}],
        'receipts.jsonl': receipts,
    }
    data_files = []
    for name, rows in files.items():
        payload = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode('utf-8')
        (folder / name).write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        if corrupt == name: digest = '0' * 64
        data_files.append({'path': name, 'sha256': digest, 'rows': len(rows)})
    gate = {'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T06:00:00Z',
            'data_files': data_files, 'counts': {'states': 2, 'counties': 4}, 'checks': [],
            'qualification': LABEL, 'license_ref': LABEL, 'inputs': []}
    (folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
    return folder


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for v in value: yield from walk(v)


class GateTests(unittest.TestCase):
    def test_missing_folder_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(tc.load(Path(tmp) / 'absent'), {})
            self.assertFalse(tc.summary(folder=Path(tmp) / 'absent')['available'])

    def test_failed_status_not_ready_and_bad_hash_fail_closed(self):
        for kwargs in ({'status': 'failed'}, {'ready': False}, {'corrupt': 'counties.jsonl'},
                       {'corrupt': 'states.jsonl'}, {'corrupt': 'receipts.jsonl'}):
            with tempfile.TemporaryDirectory() as tmp:
                folder = write_fixture(tmp, **kwargs)
                self.assertEqual(tc.load(folder), {}, kwargs)
                self.assertIsNone(tc.state('NJ', folder=folder))
                self.assertIsNone(tc.county(fips='34023', folder=folder))
                self.assertEqual(tc.listing(folder=folder)['total'], 0)
                self.assertFalse(tc.listing(folder=folder)['available'])

    def test_data_edited_after_validation_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_fixture(tmp)
            self.assertTrue(tc.load(folder))
            with open(folder / 'counties.jsonl', 'ab') as handle: handle.write(b'{"id":"x"}\n')
            self.assertEqual(tc.load(folder), {})

    def test_ready_must_be_boolean_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_fixture(tmp, ready='true')
            self.assertEqual(tc.load(folder), {})

    def test_cache_avoids_rehashing_unchanged_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=write_fixture(tmp)
            self.assertTrue(tc.load(folder))
            with patch.object(tc.hashlib,'sha256',side_effect=AssertionError('unchanged data rehashed')):
                self.assertEqual(tc.summary(folder=folder)['counties'],4)
                self.assertEqual(tc.county(fips='34023',folder=folder)['county'],'Middlesex County')

    def test_cached_missing_receipt_fails_closed_and_repair_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=write_fixture(tmp);self.assertTrue(tc.load(folder))
            receipt=folder/'raw/0001_get_state_coverage_nj.response.json'
            payload=receipt.read_bytes();receipt.unlink()
            self.assertEqual(tc.load(folder),{})
            receipt.write_bytes(payload)
            self.assertTrue(tc.load(folder))

    def test_cached_changed_gate_and_same_length_receipt_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=write_fixture(tmp);self.assertTrue(tc.load(folder))
            receipt=folder/'raw/0001_get_state_coverage_nj.response.json'
            receipt.write_bytes(receipt.read_bytes().replace(b'null',b'true'))
            self.assertEqual(tc.load(folder),{})
            write_fixture(folder);self.assertTrue(tc.load(folder))
            gate=json.loads((folder/'validation.json').read_text())
            gate['ready']=False
            (folder/'validation.json').write_text(json.dumps(gate))
            self.assertEqual(tc.load(folder),{})

    def test_progress_is_manifest_bound_and_invalidated_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=write_fixture(tmp);payload=json.dumps({'available':True,'observed_county_urls':4,'saved_county_urls':3,'remaining_county_urls':1,'states':[]}).encode()
            (folder/'progress.json').write_bytes(payload)
            gate=json.loads((folder/'validation.json').read_text())
            gate['data_files'].append({'path':'progress.json','sha256':hashlib.sha256(payload).hexdigest(),'rows':1})
            (folder/'validation.json').write_text(json.dumps(gate))
            self.assertEqual(tc.progress(folder=folder)['saved_county_urls'],3)
            (folder/'progress.json').write_bytes(payload.replace(b'3',b'4'))
            self.assertFalse(tc.progress(folder=folder)['available'])


class QueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = write_fixture(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_summary_label_and_counts(self):
        s = tc.summary(folder=self.folder)
        self.assertTrue(s['available'])
        self.assertEqual(s['qualification'], LABEL)
        self.assertEqual(s['license_ref'], LABEL)
        self.assertEqual(s['states'], 2)
        self.assertEqual(s['counties'], 4)
        self.assertEqual(s['counties_with_detail'], 2)
        self.assertEqual(s['counties_with_fips'], 3)
        self.assertEqual(s['counties_without_fips'], 1)
        self.assertEqual(s['venues_with_detail'], 1)

    def test_state_lookup_is_case_insensitive_and_strict(self):
        nj = tc.state('nj', folder=self.folder)
        self.assertEqual(nj['state'], 'NJ')
        self.assertFalse(nj['coverage_flags']['tentative_rulings'])
        self.assertEqual(nj['counties_listed'], 2)
        self.assertEqual([c['county'] for c in nj['counties']], ['Atlantic County', 'Middlesex County'])
        self.assertEqual(nj['qualification'], LABEL)
        for bad in ('ZZ', '', None, 'New Jersey', 'N', 5, '../x'):
            self.assertIsNone(tc.state(bad, folder=self.folder))

    def test_county_by_fips_and_by_state_name(self):
        row = tc.county(fips='34023', folder=self.folder)
        self.assertEqual(row['county'], 'Middlesex County')
        self.assertEqual(row['publisher_reported']['population'], 860807)
        self.assertEqual(row['courthouse_count_reported'], 0)
        self.assertEqual(row['practice_area_presence'], {'Civil': True, 'Consumer': True, 'Torts': True})
        self.assertEqual(row['captured_at'], '2026-09-19T05:40:00Z')
        same = tc.county(state='nj', name='middlesex county', folder=self.folder)
        self.assertEqual(same['id'], row['id'])
        city = tc.county(state='MO', name='St. Louis City', folder=self.folder)
        self.assertIsNone(city['fips'])
        self.assertIsNone(city['fips_basis'])
        for kwargs in ({'fips': '3402'}, {'fips': 34023}, {'fips': '99999'}, {'state': 'NJ'}, {'name': 'Middlesex County'},
                       {'state': 'NJ', 'name': 'Middlesex'}, {}):
            self.assertIsNone(tc.county(folder=self.folder, **kwargs), kwargs)

    def test_listing_filters_and_pagination(self):
        everything = tc.listing(folder=self.folder)
        self.assertTrue(everything['available'])
        self.assertEqual(everything['total'], 4)
        self.assertEqual([r['id'] for r in everything['items']],
                         ['trellis_county:MO:jackson-county', 'trellis_county:MO:st-louis-city',
                          'trellis_county:NJ:atlantic-county', 'trellis_county:NJ:middlesex-county'])
        self.assertEqual(tc.listing(state='nj', folder=self.folder)['total'], 2)
        self.assertEqual(tc.listing(detail=True, folder=self.folder)['total'], 2)
        self.assertEqual(tc.listing(detail=False, folder=self.folder)['total'], 2)
        self.assertEqual(tc.listing(has_documents=False, folder=self.folder)['total'], 1)
        self.assertEqual(tc.listing(practice_area='torts', folder=self.folder)['total'], 1)
        self.assertEqual(tc.listing(venue=True, folder=self.folder)['total'], 1)
        self.assertEqual(tc.listing(fips_resolved=False, folder=self.folder)['items'][0]['county'], 'St. Louis City')
        self.assertEqual(tc.listing(q='LOUIS', folder=self.folder)['total'], 1)
        page = tc.listing(limit=1, offset=1, folder=self.folder)
        self.assertEqual((page['limit'], page['offset'], len(page['items']), page['total']), (1, 1, 1, 4))
        self.assertEqual(page['items'][0]['county'], 'St. Louis City')
        self.assertEqual(tc.listing(limit=100000, folder=self.folder)['limit'], tc.MAX_LIMIT)
        self.assertEqual(tc.listing(limit='junk', offset=-4, folder=self.folder)['offset'], 0)
        self.assertEqual(tc.listing(state='ZZ', folder=self.folder)['total'], 0)

    def test_public_dicts_are_path_free(self):
        outputs = [tc.summary(folder=self.folder), tc.state('NJ', folder=self.folder),
                   tc.county(fips='34023', folder=self.folder), tc.listing(folder=self.folder)]
        for output in outputs:
            for key, value in walk(output):
                self.assertNotIn('path', str(key).lower())
                if isinstance(value, str):
                    self.assertNotRegex(value, r'^[A-Za-z]:[\\/]')
                    self.assertNotIn('raw/', value)

    def test_results_are_copies(self):
        tc.county(fips='34023', folder=self.folder)['county'] = 'changed'
        self.assertEqual(tc.county(fips='34023', folder=self.folder)['county'], 'Middlesex County')

    def test_receipt_bytes_are_hash_verified(self):
        payload, mime, meta = tc.receipt('tc-0001', folder=self.folder)
        self.assertEqual(payload, b'{"error":null,"counties":[]}')
        self.assertEqual(mime, 'application/json')
        self.assertEqual(meta['record_kind'], 'connector response, not original HTTP bytes')
        self.assertNotIn('response_path', meta)
        for bad in ('tc-9999', '../validation.json', 'raw/0001_get_state_coverage_nj.response.json', '', None):
            self.assertIsNone(tc.receipt(bad, folder=self.folder))

    def test_tampered_receipt_file_is_not_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_fixture(tmp)
            (folder / 'raw/0001_get_state_coverage_nj.response.json').write_bytes(b'{"tampered":true}')
            self.assertIsNone(tc.receipt('tc-0001', folder=folder))


class RealDataTests(unittest.TestCase):
    def setUp(self):
        self.data = tc.load()
        if not self.data: self.skipTest('published Trellis coverage supplement is not available')

    def test_real_supplement_shape(self):
        s = tc.summary()
        self.assertEqual(s['qualification'], LABEL)
        self.assertGreaterEqual(s['states'], 1)
        listing = tc.listing(limit=tc.MAX_LIMIT)
        for row in listing['items']:
            self.assertTrue(row['fips'] is None or re.fullmatch(r'\d{5}', row['fips']))
            self.assertEqual(row['qualification'], LABEL)
            self.assertIn('captured_at', row)
        nj = tc.state('NJ')
        if nj: self.assertEqual(tc.county(fips='34023')['state'], 'NJ')

    def test_real_browser_fields_and_unknown_coverage_preserved(self):
        jasper = tc.county(fips='48241')
        if not jasper or not jasper.get('detail_captured'): self.skipTest('New browser batch not published yet')
        self.assertEqual(jasper['publisher_reported']['website'], 'https://www.co.jasper.tx.us/')
        self.assertEqual(jasper['publisher_reported']['phone_number'], '(409) 384-2632')
        self.assertIn('121 N Austin Street', jasper['publisher_reported']['administration_address'])
        self.assertEqual(jasper['publisher_reported']['seat'], 'Jasper')
        self.assertIsNone(jasper['source_as_of'])
        self.assertIsNone(jasper['publisher_reported']['court_address'])
        self.assertEqual(jasper['detail_capture_kind'], 'rendered_dom_browser_profile')
        virginia = tc.state('VA')
        self.assertEqual(virginia['record_type'], 'trellis_state_browser_context')
        self.assertIsNone(virginia['coverage_flags'])
        self.assertIsNone(virginia['counties_listed'])

    def test_unmatched_source_names_are_not_guessed(self):
        chancery = tc.county(state='DE', name='Court of Chancery')
        if chancery:
            self.assertIsNone(chancery['fips'])
        ct = tc.county(state='CT', name='Fairfield County')
        if ct:
            self.assertIsNone(ct['fips'])

    def test_source_representation_kinds_remain_distinct(self):
        receipts = self.data['receipts']
        kinds = [r['record_kind'] for r in receipts]
        self.assertTrue(any('transcribed' in k for k in kinds))
        self.assertTrue(any('DOM' in k for k in kinds))
        self.assertTrue(any('saved connector wrapper' in k for k in kinds))

    def test_progress_uses_url_union_not_geographic_or_detail_count(self):
        progress=tc.progress()
        if not progress.get('available'):self.skipTest('Progress not published')
        self.assertEqual(progress['observed_county_urls'],2508-len(progress['excluded_website_link_artifacts']))
        self.assertEqual(progress['saved_county_urls']+progress['remaining_county_urls'],progress['observed_county_urls'])
        self.assertEqual(len(progress['remaining_urls']),progress['remaining_county_urls'])
        self.assertEqual(sum(r['saved_county_urls'] for r in progress['states']),progress['saved_county_urls'])
        self.assertEqual(tc.progress('Georgia'),tc.progress('GA'))


class ProviderBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parents[2]/'sources/trellis_coverage_20260919/build.py'
        spec=importlib.util.spec_from_file_location('coverage_builder_test',path)
        cls.builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.builder)
        cls.inventory=[{'state':'Missouri','usps':'MO','name':'St. Louis city','geoid':'29510'},
                       {'state':'Missouri','usps':'MO','name':'St. Louis County','geoid':'29189'}]

    def identity(self,label='St. Louis city Dockets',heading='St. Louis city Circuit Courts Records',title='St. Louis city, MO Case Search'):
        url='https://trellis.law/coverage/missouri/stlouiscity'
        proof={'url':url,'source_url':'https://trellis.law/coverage/missouri','label':label}
        return self.builder.provider_identity(url,heading,{'title':title},[proof],self.inventory)

    def test_city_heading_and_observed_label_never_join_county(self):
        self.assertEqual(self.identity()['fips'],'29510')
        self.assertIsNone(self.identity(label='St. Louis County')['fips'])
        self.assertIsNone(self.identity(title='St. Louis city Case Search')['fips'])
        self.assertIsNone(self.identity(heading='St. Louis County Circuit Courts Records')['fips'])

    def test_provider_profile_keeps_admin_address_and_exact_website_href(self):
        raw={'html':'<div class="top-county-info-block__container"><h1>St. Louis city Circuit Courts Records</h1><h2>Website</h2><h3><a href="https://example.gov/full">https://example.gov/…</a></h3><h2>Administration Address</h2><h3>Office<br>123 Road</h3><h2>Unrelated court website</h2><h3>Ignore as county scalar</h3></div>'}
        _,_,website,fields=self.builder.provider_section(raw)
        self.assertEqual(website,'https://example.gov/full')
        self.assertEqual(fields['website'],website)
        self.assertEqual(fields['administration_address'],'Office 123 Road')
        self.assertIsNone(fields['court_address'])
        self.assertNotIn('unrelated_court_website',fields)

    def test_provider_snapshot_requires_exact_ready_hash_count_and_unique_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);record={'id':'a','source_url':'https://trellis.law/coverage/missouri/stlouiscity'}
            payload=(json.dumps(record)+'\n').encode();(folder/'resources.jsonl').write_bytes(payload)
            gate={'ready':True,'status':'passed','manifest_path':'resources.jsonl','manifest_sha256':hashlib.sha256(payload).hexdigest(),'records':1,'provider_representation':True}
            (folder/'validation.json').write_text(json.dumps(gate))
            self.assertEqual(len(self.builder.provider_snapshot(folder)[1]),1)
            gate['ready']=False;(folder/'validation.json').write_text(json.dumps(gate))
            self.assertIsNone(self.builder.provider_snapshot(folder))
            gate['ready']=True;gate['manifest_sha256']='0'*64;(folder/'validation.json').write_text(json.dumps(gate))
            self.assertIsNone(self.builder.provider_snapshot(folder))
            gate['manifest_sha256']=hashlib.sha256(payload).hexdigest();gate['records']=2;(folder/'validation.json').write_text(json.dumps(gate))
            with self.assertRaises(ValueError):self.builder.provider_snapshot(folder)


if __name__ == '__main__':
    unittest.main()
