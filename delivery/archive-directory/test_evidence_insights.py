import unittest
from evidence_dates import date_value, document_dates, latest_saved
from judge_insights import decorate, derive


class EvidenceTests(unittest.TestCase):
    def test_invalid_calendar_and_partial_precision(self):
        self.assertIsNone(date_value('2026-02-30'))
        self.assertIsNone(date_value('July 2026'))
        self.assertEqual(date_value('2024'), '2024')
        self.assertEqual(date_value('2024-02'), '2024-02')

    def test_saved_dates_do_not_become_effective_dates(self):
        result = document_dates({'captured_at': '2026-09-19T03:00:00Z'})
        self.assertIsNone(result['source_as_of'])
        self.assertIsNone(result['effective_date'])
        self.assertTrue(result['saved_at'].startswith('2026-09-19'))

    def test_retrieval_time_is_saved_evidence_with_its_field_named(self):
        result = document_dates({'retrieved_at': '2026-09-13T10:48:25+00:00', 'indexed_at': '2026-09-18T11:44:00+00:00',
                                 'raw_hash_verified_at': '2026-09-18T16:45:00+00:00', 'retrieval_time_basis': 'source_reported'})
        self.assertEqual(result['saved_at'], '2026-09-13T10:48:25+00:00')
        self.assertEqual(result['date_evidence']['saved_at_field'], 'retrieved_at')
        self.assertEqual(result['date_evidence']['saved_at_basis'], 'source_reported')
        self.assertIsNone(result['source_as_of'])
        self.assertIsNone(result['effective_date'])
        # Index/verification times and HTTP Last-Modified never stand in for a saved or legal date.
        other = document_dates({'indexed_at': '2026-09-18T11:44:00+00:00', 'http_last_modified': 'Sat, 19 Sep 2026 01:03:20 GMT'})
        self.assertIsNone(other['saved_at'])
        self.assertIsNone(other['effective_date'])
        both = document_dates({'captured_at': '2026-09-19T03:00:00Z', 'retrieved_at': '2026-09-13T10:48:25+00:00'})
        self.assertEqual(both['date_evidence']['saved_at_field'], 'captured_at')

    def test_latest_capture_uses_timezones_and_lists(self):
        values = ['2026-09-18T23:45:00-05:00', ['2026-09-19T03:00:00Z', None], '2024']
        self.assertEqual(latest_saved(values), '2026-09-18T23:45:00-05:00')

    def test_explicit_source_and_effective_date_remain_distinct(self):
        result = document_dates({'metadata': {'source_date': '2024-05', 'effective_date': '2024-07-01'}})
        self.assertEqual(result['source_as_of'], '2024-05')
        self.assertEqual(result['effective_date'], '2024-07-01')
        self.assertIsNone(result['saved_at'])

    def test_fragments_dedupe_sources_not_measure_counts(self):
        p = {'sources': [{'url':'https://court.gov/report.pdf#page=1'}, {'url':'https://court.gov/report.pdf#page=9'}],
             'analyses': [{'publisher':'Official commission','period':{'evaluation_cycle':2024},'sample_size':10},
                          {'publisher':'Official commission','period':{'evaluation_cycle':2024},'sample_size':10}],
             'courts':['Court A','Court A']}
        x = derive(p)
        self.assertEqual([m['value'] for m in x['metrics']], [1,1,2])
        self.assertEqual(x['analysis_scope']['periods'], ['Evaluation cycle: 2024'])
        self.assertEqual(x['analysis_scope']['records'], 2)

    def test_unknown_sample_sizes_and_current_status_not_inferred(self):
        x = derive({'analyses':[{'sample_size':True},{'sample_size':'100'},{'sample_size':0}]})
        self.assertEqual(x['analysis_scope']['with_sample_size'], 0)
        self.assertIn('Present-day judicial service is not verified.', x['gaps'])
        self.assertIn('3 analysis records have no stated reporting period.', x['gaps'])

    def test_unknown_dates_do_not_use_computation_or_photo_date(self):
        x = decorate({'sources':[{'url':'https://court.gov/judge'}], 'photo_provenance': {'captured_at':'2026-09-19'}}, '2026-09-19T01:00:00Z')
        self.assertIsNone(x['saved_at']); self.assertIsNone(x['source_as_of'])
        self.assertEqual(x['library_insights']['computed_at'], '2026-09-19T01:00:00Z')


if __name__ == '__main__': unittest.main()
