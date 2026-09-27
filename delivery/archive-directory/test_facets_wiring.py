"""The derived-facet sidecar must drive document categories, filters, titles and dates without touching the base tables."""
import unittest

import record_facets
import server
import explore


@unittest.skipUnless(record_facets.status()['ready'], 'facets sidecar not published')
class FacetWiringTests(unittest.TestCase):
    def test_category_filter_uses_derived_categories(self):
        statutes = server.query_documents({'group': 'laws', 'category': 'statutes', 'limit': '1', 'view': 'sources'})
        self.assertGreater(statutes['source_total'], 12000)  # 7,426 by kind before the sidecar; 15,812 derived
        # Directory records in 'other' fall from 30,432 to a handful; Open US Law publisher rows classified by kind remain counted.
        other = server.query_documents({'group': 'all', 'category': 'other', 'limit': '1', 'view': 'sources'})
        self.assertLess(other['source_total'], 12000)
        local_other = server.query_documents({'group': 'all', 'category': 'other', 'validity': 'any', 'record_type': 'other', 'limit': '1', 'view': 'sources'})
        self.assertLess(local_other['source_total'], 100)
        judges = server.query_documents({'group': 'all', 'category': 'judges', 'limit': '1', 'view': 'sources'})
        self.assertGreater(judges['source_total'], 10000)
        self.assertEqual(server.query_documents({'group': 'all', 'category': 'invented', 'limit': '1'})['total'], 0)

    def test_new_filters_and_item_facets(self):
        result = server.query_documents({'group': 'all', 'file_type': 'pdf', 'review': 'reviewed', 'limit': '3', 'view': 'sources'})
        self.assertGreater(result['total'], 0)
        for item in result['items']:
            self.assertEqual(item['facets']['file_type'], 'pdf')
            self.assertEqual(item['facets']['review_state'], 'reviewed')
            self.assertEqual(item['category'], item['facets']['derived_category'])
            self.assertIn('display_title', item['facets'])
            self.assertIn('saved_at', item['facets']['dates'])
        self.assertEqual(server.query_documents({'group': 'all', 'file_type': 'nope', 'limit': '1'})['total'], 0)
        dated = server.query_documents({'group': 'laws', 'date_type': 'saved', 'dfrom': '2026-09-13', 'dto': '2026-09-13', 'undated': '0', 'limit': '2', 'view': 'sources'})
        self.assertGreater(dated['total'], 0)
        self.assertTrue(all((i['facets']['dates']['saved_at'] or '').startswith('2026-09-13') for i in dated['items']))
        self.assertTrue(result['facets_ready'])
        self.assertIn('file_type', result['facet_options'])

    def test_invalid_captures_leave_default_results_but_stay_reachable(self):
        parked = server.query_documents({'group': 'counties', 'validity': 'parked_redirect', 'limit': '5', 'view': 'sources'})
        self.assertGreater(parked['total'], 0)
        default = server.query_documents({'group': 'counties', 'limit': '1', 'view': 'sources'})
        everything = server.query_documents({'group': 'counties', 'validity': 'any', 'limit': '1', 'view': 'sources'})
        self.assertLess(default['source_total'], everything['source_total'])
        # Link-only federal resources are valid records without a capture and must stay in the default view.
        federal = server.query_documents({'group': 'federal', 'dataset': 'federal', 'limit': '1', 'view': 'sources'})
        self.assertEqual(federal['total'], 591)

    def test_grouped_view_keeps_display_ids_and_adds_facets(self):
        result = server.query_documents({'group': 'laws', 'q': 'due process', 'limit': '3'})
        self.assertGreater(result['total'], 0)
        local = [i for i in result['items'] if i.get('facets')]
        self.assertTrue(local)
        detail = server.record_detail(local[0]['id'])
        self.assertEqual(detail['facets']['derived_category'], local[0]['facets']['derived_category'])
        self.assertTrue(detail['facets']['display_title'])

    def test_explore_uses_derived_categories(self):
        summary = explore.summary({'group': 'laws'})
        cats = {c['id']: c for c in summary['categories']}
        self.assertGreater(cats['statutes']['local_documents'], 5000)
        self.assertLess(cats['other']['local_documents'], 100)


if __name__ == '__main__': unittest.main()
