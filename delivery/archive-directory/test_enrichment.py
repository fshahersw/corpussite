"""Read-only checks for the enriched directory API after its validated rebuild.

Run against the local service with Python test_enrichment.py. No source or index
files are changed. Optional --base-url chooses another loopback test service.
"""
import argparse
import gzip
import json
import unittest
import urllib.parse
import urllib.request
import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch
import enrichment as additions

BASE = 'http://127.0.0.1:8769'


def request(path):
    with urllib.request.urlopen(urllib.request.Request(
            BASE + path, headers={'Accept-Encoding': 'gzip'}), timeout=60) as response:
        body = response.read()
        if response.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
        return dict(response.headers), body


def get(path, **params):
    if params:
        path += '?' + urllib.parse.urlencode(params)
    return json.loads(request(path)[1])


def valid_source(url):
    p = urllib.parse.urlsplit(url or '')
    return p.scheme in {'http', 'https'} and bool(p.netloc) and not p.username


class EnrichmentChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = get('/api/summary')

    def test_enrichment_counts_and_dedup_contract(self):
        enrichment = self.summary['enrichment']
        for name in ['open_us_law', 'seeger', 'judges', 'reading', 'deduplication']:
            self.assertIn(name, enrichment)
        self.assertGreater(enrichment['judges']['entities'], 0)
        self.assertGreater(enrichment['judges']['source_observations'],
                           enrichment['judges']['entities'])
        self.assertGreater(enrichment['reading']['records'], 0)
        dedup = enrichment['deduplication']
        self.assertGreater(dedup['groups_with_multiple_members'], 0)
        self.assertGreater(dedup['source_observations_total'], 0)
        self.assertLessEqual(dedup['display_groups'], dedup['source_records'])

    def test_grouped_and_source_views_preserve_originals(self):
        grouped = get('/api/documents', group='judges', limit=5)
        sources = get('/api/documents', group='judges', view='sources', limit=5)
        self.assertEqual(grouped['view'], 'grouped')
        self.assertEqual(sources['view'], 'sources')
        self.assertEqual(sources['total'], sources['source_total'])
        self.assertEqual(grouped['source_total'], sources['source_total'])
        self.assertLess(grouped['total'], sources['total'])
        self.assertTrue(all(x['dataset'] != 'judge_entities' for x in sources['items']))
        self.assertTrue(all('group_basis' in x and 'source_count' in x for x in grouped['items']))

    def test_sabraw_consolidation_preserves_449_sourced_analyses(self):
        results = get('/api/documents', group='judges', q='Sabraw', limit=100)
        profile = next(x for x in results['items'] if x['dataset'] == 'judge_entities')
        detail = get('/api/record', id=profile['id'])
        entity = detail['metadata']
        self.assertEqual(entity['name'], 'Dana Makoto Sabraw')
        self.assertEqual(entity['member_count'], 2)
        self.assertEqual(len(entity['analyses']), 449)
        self.assertFalse(entity['current_service_verified'])
        self.assertTrue(entity['education'])
        self.assertTrue(entity['appointments'])
        self.assertTrue(entity['field_provenance'])
        self.assertTrue(all(a['member_key'] and a['source_anchor'] for a in entity['analyses']))
        self.assertEqual({a['dataset'] for a in entity['analyses']}, {'judge_vendor'})
        grants = [a for a in entity['analyses'] if a.get('motion_type') == 'motion to dismiss' and a.get('outcome') == 'granted']
        self.assertTrue(any(a['value'] == 262 for a in grants))
        self.assertTrue(all(a.get('limitations') and a.get('captured_at') for a in grants))
        self.assertGreaterEqual(len(detail['source_records']), 2)
        self.assertTrue(any(valid_source(x.get('source_url')) for x in detail['source_records']))
        self.assertIn('Dana Makoto Sabraw', detail['text'])
        self.assertNotIn('"native_record":', detail['text'])
        self.assertTrue(detail['reading_notes'])
        originals = get('/api/documents', group='judges', q='Sabraw', view='sources', limit=100)
        self.assertTrue({'judge_enrichment', 'judge_vendor'} <= {x['dataset'] for x in originals['items']})

    def test_full_readable_text_and_original_extraction_are_separate(self):
        results = get('/api/documents', group='laws', q='due process', limit=5)
        self.assertGreater(results['total'], 0)
        item = next(x for x in results['items'] if x['has_text'])
        detail = get('/api/record', id=item['id'])
        self.assertTrue(detail['text'].strip())
        self.assertIsInstance(detail['links'], list)
        self.assertTrue(detail['source_records'])
        self.assertNotIn('<html', detail['text'][:500].lower())
        self.assertNotIn('"native_record":', detail['text'][:500])
        self.assertTrue(detail['text_url'].startswith('/api/text?'))
        headers, full = request(detail['text_url'])
        content = full.decode('utf-8')
        self.assertTrue(content.startswith(detail['text']))
        self.assertIn('text/plain', headers['Content-Type'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        if detail.get('extracted_url'):
            self.assertNotEqual(detail['text_url'], detail['extracted_url'])
            self.assertTrue(detail['extracted_url'].startswith('/files/'))

    def test_imported_dataset_routes_and_source_provenance(self):
        bulk = self.summary['enrichment']['open_us_law']
        self.assertTrue(bulk['ready'])
        for dataset in ['open_us_law', 'seeger']:
            result = get('/api/documents', group='laws', dataset=dataset, limit=3)
            self.assertGreater(result['total'], 0, dataset)
            row = result['items'][0]
            record = get('/api/record', id=row['id'])
            # Filtering matches source membership; the best representation may
            # be supplied by a different dataset in the same evidence group.
            retained = {x.get('dataset') for x in record.get('source_records', [])}
            self.assertTrue(row['dataset'] == dataset or dataset in retained, dataset)
            self.assertTrue(record['metadata'])
            self.assertTrue(valid_source(record.get('source_url')), dataset)
            self.assertTrue(record['text'].strip(), dataset)
            self.assertIn('reading_notes', record)
        self.assertGreater(bulk['records'], 0)
        self.assertGreater(bulk['files'], 0)
        self.assertTrue(bulk['snapshot'])


class EnrichmentSidecarChecks(unittest.TestCase):
    """Local isolated tests for the dated sidecar, requiring no live service."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.asset = self.folder / 'original.txt'
        self.asset.write_bytes(b'Official court text')
        sha = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.record = {'id': 'addition:one', 'title': 'Court rule', 'state': 'MI',
                       'resource_type': 'rules', 'original_file': 'original.txt', 'raw_sha256': sha,
                       'filed_at': '2026-08-07', 'jurisdiction': 'federal', 'qualification': 'Historical order',
                       'page_range': [3,5], 'legal_currency_verified': False,
                       'caption_as_printed': 'Official caption', 'source_evidence': {'source_url': 'https://court.gov/order', 'source_path': 'C:/Users/private/source.pdf'},
                       'date_evidence': {'filed_at': {'quote': 'Filed 08/07/26', 'source_sha256': sha, 'page': 1, 'source_path': 'local.txt'}},
                       'hierarchy': [{'title': 'Part 1', 'raw_path': 'C:/private/source.txt'}]}
        self.edge = {'source': 'county:26001', 'target': 'url:official', 'relation': 'listed_filing_source',
                     'scope': 'statewide', 'evidence': {'source_url': 'https://court.gov/order', 'quote': 'Official court text',
                     'source_path': 'C:/private/file', 'nested': {'metadata_path': '/tmp/private', 'source_file':'local.txt', 'value': 'file:///C:/private/file'}}}
        self.bundle = {'available': True, 'generated_at': '2026-09-27', 'summary': {}, 'qualification': 'Evidence only',
                       'resources': [self.record, {'id': 'addition:two', 'title': 'Other county', 'state': 'MI'}],
                       'nodes': [{'id': 'county:26001'}, {'id': 'url:official'}, {'id': 'addition:one'}, {'id': 'mdl:1234'}],
                       'edges': [self.edge, {'source': 'url:official', 'target': 'addition:one', 'relation': 'captured_as'},
                                 {'source': 'addition:one', 'target': 'mdl:1234', 'relation': 'cites_mdl'}]}
        self.write_bundle()
        # Avoid touching the real disk-hash memo while testing temporary data.
        memo = patch.object(additions.supplements, '_digest', lambda path, size, stamp: hashlib.sha256(Path(path).read_bytes()).hexdigest())
        memo.start(); self.addCleanup(memo.stop)
        additions._CACHE.clear()

    def write_bundle(self):
        (self.folder / 'bundle.json').write_text(json.dumps(self.bundle), encoding='utf-8')
        self.gate = {'schema_version': 1, 'status': 'passed', 'ready': True, 'data_files': [
            {'path': name, 'sha256': hashlib.sha256((self.folder/name).read_bytes()).hexdigest()}
            for name in ('bundle.json', 'original.txt')]}
        self.write_gate()

    def write_gate(self):
        (self.folder / 'validation.json').write_text(json.dumps(self.gate), encoding='utf-8')

    def test_exact_county_and_mdl_scope_without_state_widening(self):
        got = additions.listing({'county': '26001'}, self.folder)
        self.assertEqual([r['id'] for r in got['items']], ['addition:one'])
        self.assertEqual(additions.listing({'county': '26003'}, self.folder)['total'], 0)
        self.assertEqual(additions.listing({'mdl': '1234'}, self.folder)['total'], 1)
        self.assertEqual(additions.listing({'mdl': '123'}, self.folder)['total'], 0)

    def test_public_dates_context_and_provenance_preserved_without_paths(self):
        public = additions.public_record(self.record)
        for field in ('filed_at','jurisdiction','qualification','caption_as_printed','page_range','legal_currency_verified'):
            self.assertEqual(public[field], self.record[field])
        self.assertEqual(public['date_evidence']['filed_at']['quote'], 'Filed 08/07/26')
        self.assertEqual(public['source_evidence']['source_url'], 'https://court.gov/order')
        encoded = json.dumps(public)
        self.assertNotIn('source_path', encoded); self.assertNotIn('raw_path', encoded)
        self.assertNotIn('C:/', encoded); self.assertNotIn('original_file', encoded)

    def test_graph_evidence_scrubs_private_paths_but_preserves_scope_quote_hash(self):
        result = additions.graph({'entity': 'county:26001'}, self.folder)
        self.assertEqual(result['edges'][0]['scope'], 'statewide')
        self.assertEqual(result['edges'][0]['evidence']['quote'], 'Official court text')
        self.assertNotIn('C:/', json.dumps(result)); self.assertNotIn('/tmp/', json.dumps(result))
        self.assertEqual(additions.graph({}, self.folder)['total'], 0)

    def test_asset_mutation_invalidates_cached_gate(self):
        self.assertTrue(additions.state(self.folder))
        self.asset.write_bytes(b'Changed longer court text')
        self.assertIsNone(additions.state(self.folder))
        self.assertIsNone(additions.asset('addition:one', folder=self.folder))

    def test_missing_registered_bundle_and_unregistered_asset_fail_closed(self):
        self.gate['data_files'] = [self.gate['data_files'][1]]
        self.write_gate(); self.assertIsNone(additions.state(self.folder))
        self.write_bundle()
        self.gate['data_files'] = [self.gate['data_files'][0]]
        self.write_gate(); self.assertIsNone(additions.state(self.folder))

    def test_held_or_malformed_gate_is_unavailable(self):
        self.gate['ready'] = False; self.write_gate()
        self.assertFalse(additions.listing({}, self.folder)['available'])
        for bad in ('[]', '{malformed'):
            (self.folder / 'validation.json').write_text(bad)
            self.assertIsNone(additions.state(self.folder))

    def test_artifact_escape_is_rejected_even_if_bundle_hash_matches(self):
        self.record['original_file'] = '../outside.txt'; self.write_bundle()
        self.assertIsNone(additions.asset('addition:one', folder=self.folder))
        self.assertIsNone(additions.asset('addition:one', '../../private', self.folder))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default=BASE)
    args, remaining = parser.parse_known_args()
    parsed = urllib.parse.urlsplit(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'}:
        parser.error('--base-url must be an HTTP loopback service')
    BASE = args.base_url.rstrip('/')
    unittest.main(argv=['test_enrichment.py', *remaining], verbosity=2)
