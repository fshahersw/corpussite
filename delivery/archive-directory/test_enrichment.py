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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default=BASE)
    args, remaining = parser.parse_known_args()
    parsed = urllib.parse.urlsplit(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'}:
        parser.error('--base-url must be an HTTP loopback service')
    BASE = args.base_url.rstrip('/')
    unittest.main(argv=['test_enrichment.py', *remaining], verbosity=2)
