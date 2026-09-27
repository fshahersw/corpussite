import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
from publish_saved_checkpoint import refresh_classification


class IncrementalPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.scope = patch.object(build, 'HERE', self.folder)
        self.scope.start()

    def tearDown(self):
        self.scope.stop()
        self.temp.cleanup()

    def row(self, text, title='E-Filing Proposed Orders | Fourteenth Judicial Circuit', html=None):
        digest = hashlib.sha256(text.encode()).hexdigest()
        (self.folder / 'text.txt').write_bytes(text.encode('utf8'))
        raw = (html or '<h1>E-Filing Proposed Orders</h1>').encode()
        (self.folder / 'source.html').write_bytes(raw)
        return {'id': 'fixture', 'title': title, 'resource_kind': 'filing_guidance',
                'source_url': 'https://jud14.flcourts.org/e-filing-proposed-orders',
                'raw_path': 'source.html', 'text_path': 'text.txt', 'text_sha256': digest,
                'county_geoids': ['12005'], 'captured_at': '2026-09-19T00:00:00Z',
                'metadata': {'applicability': {'level': 'unknown', 'status': 'unresolved'},
                             'document_shape': 'guide', 'related_links': [],
                             'artifacts': [{'path': 'source.html', 'sha256': hashlib.sha256(raw).hexdigest(),
                                            'mime_type': 'text/html'}]}}

    def test_saved_literal_links_correct_index_without_changing_geography_or_dates(self):
        label = 'Attorney Instructions for Successful Submissions of Proposed Orders to Judiciary'
        target = 'https://jud14.flcourts.org/uploaded/media/Attorney%20Instructions.pdf'
        original = self.row('# E-Filing Proposed Orders\n\n' + label,
                            html='<h1>E-Filing Proposed Orders</h1><a href="' + target + '">' + label + '</a>')
        result = refresh_classification(original)
        self.assertEqual(result['metadata']['document_shape'], 'filing_guidance_index')
        self.assertFalse(result['metadata']['classification']['substantive'])
        self.assertEqual(result['metadata']['related_links'][0]['url'], target)
        self.assertEqual(result['county_geoids'], original['county_geoids'])
        self.assertEqual(result['metadata']['applicability'], original['metadata']['applicability'])
        self.assertEqual(result['captured_at'], original['captured_at'])
        self.assertEqual(original['metadata']['document_shape'], 'guide')

    def test_saved_hash_tampering_fails(self):
        row = self.row('Submit proposed orders through the court portal.')
        (self.folder / 'text.txt').write_text('different content', encoding='utf8')
        with self.assertRaises(ValueError):
            refresh_classification(row)

    def test_empty_text_does_not_become_substantive(self):
        result = refresh_classification(self.row(''))
        self.assertEqual(result['metadata']['document_shape'], 'unparsed_document')
        self.assertFalse(result['metadata']['classification']['substantive'])

    def test_prior_negative_review_is_preserved(self):
        row = self.row('Circuit Court election filing procedures')
        row['metadata']['semantic_review'] = {'reviewed': True, 'actual_resource_kind': 'election_filing'}
        result = refresh_classification(row)
        self.assertEqual(result['resource_kind'], 'source_directory')
        self.assertEqual(result['metadata']['classification']['status'], 'preserved_prior_negative_review')


if __name__ == '__main__':
    unittest.main()
