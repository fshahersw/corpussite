import unittest
from references_mdl import ReferenceExporter, categorized_docket_block


class ReferenceExportTests(unittest.TestCase):
    def test_public_preserves_semantic_section_labels_but_removes_private_locations(self):
        exporter = ReferenceExporter.__new__(ReferenceExporter)
        result = exporter.public({'section_path': 'Federal Courts > District courts',
                                  'local_path': 'C:/Users/private/file',
                                  'nested': [{'label': 'C:/Users/private/file', 'url': '/api/text?id=known'}]})
        self.assertEqual(result['section_path'], 'Federal Courts > District courts')
        self.assertNotIn('local_path', result)
        self.assertNotIn('C:/Users/', str(result))
        self.assertEqual(result['nested'][0]['url'], '/api/text?id=known')

    def test_mdl_document_links_exclude_uncategorized_and_refill_from_retained_rows(self):
        rows = [{'id': str(i), 'doc_type': 'other' if i < 30 else 'pretrial_order'} for i in range(60)]
        block = {'total': 60, 'latest_25': rows[:25], 'qualification': 'Saved evidence.'}
        value = categorized_docket_block(block, rows, lambda row: dict(row))
        self.assertEqual(value['total'], 30)
        self.assertEqual(value['source_snapshot_total'], 60)
        self.assertEqual(value['excluded_uncategorized'], 30)
        self.assertEqual(value['by_doc_type'], {'pretrial_order': 30})
        self.assertEqual([row['id'] for row in value['latest_25']], [str(i) for i in range(30, 55)])
        self.assertEqual(block['total'], 60)

    def test_mdl_missing_block_stays_missing(self):
        self.assertIsNone(categorized_docket_block(None, [], lambda row: row))


if __name__ == '__main__': unittest.main()
