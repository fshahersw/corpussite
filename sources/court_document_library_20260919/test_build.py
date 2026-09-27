import json
import sqlite3
import unittest
from pathlib import Path

import build


class ClassifyDocType(unittest.TestCase):
    def test_family_priority_fee_over_order(self):
        doc_type, basis = build.classify_doc_type('https://x/y.pdf', 'y.pdf',
                                                    ['fees_costs_payments_and_fee_schedules', 'orders_administrative_orders_and_notices'])
        self.assertEqual(doc_type, build.FEE_SCHEDULE)
        self.assertIn('source_families', basis)

    def test_family_local_rule(self):
        doc_type, _ = build.classify_doc_type('https://x/y.pdf', 'y.pdf', ['rules_local_rules_and_procedures'])
        self.assertEqual(doc_type, build.LOCAL_RULE)

    def test_family_court_form(self):
        doc_type, _ = build.classify_doc_type('https://x/y.pdf', 'y.pdf', ['forms_instructions_and_filing_packets'])
        self.assertEqual(doc_type, build.COURT_FORM)

    def test_unmapped_family_falls_back_to_pattern(self):
        doc_type, basis = build.classify_doc_type('https://x/local-rule-6.pdf', 'local-rule-6.pdf',
                                                    ['judges_justices_magistrates_and_chambers'])
        self.assertEqual(doc_type, build.LOCAL_RULE)
        self.assertIn('filename_url_pattern', basis)

    def test_no_family_pattern_fee_schedule(self):
        doc_type, _ = build.classify_doc_type('https://court.gov/2026-fee-schedule.pdf', '2026-fee-schedule.pdf', [])
        self.assertEqual(doc_type, build.FEE_SCHEDULE)

    def test_no_family_pattern_standing_order(self):
        doc_type, _ = build.classify_doc_type('https://court.gov/standing-order-25.pdf', 'standing-order-25.pdf', [])
        self.assertEqual(doc_type, build.STANDING_OR_GENERAL_ORDER)

    def test_no_family_pattern_form(self):
        doc_type, _ = build.classify_doc_type('https://court.gov/petition-form.pdf', 'petition-form.pdf', [])
        self.assertEqual(doc_type, build.COURT_FORM)

    def test_no_family_pattern_opinion(self):
        doc_type, _ = build.classify_doc_type('https://court.gov/tentative-ruling-2026.pdf', 'tentative-ruling-2026.pdf', [])
        self.assertEqual(doc_type, build.OPINION)

    def test_no_signal_is_other_unknown(self):
        doc_type, basis = build.classify_doc_type('https://court.gov/misc123.pdf', 'misc123.pdf', [])
        self.assertEqual(doc_type, build.OTHER_UNKNOWN)
        self.assertEqual(basis, 'no_family_no_pattern_match')

    def test_never_uses_pdf_metadata(self):
        # classify_doc_type takes no metadata argument at all; this documents the contract.
        import inspect
        sig = inspect.signature(build.classify_doc_type)
        self.assertNotIn('metadata', sig.parameters)
        self.assertNotIn('author', sig.parameters)


class CourtJoin(unittest.TestCase):
    def setUp(self):
        self.courts = [
            {'id': 'txsd', 'website': 'http://www.txs.uscourts.gov/'},
            {'id': 'txsb', 'website': 'http://www.txs.uscourts.gov/bankruptcy/'},
            {'id': 'cand', 'website': 'https://www.cand.uscourts.gov/'},
            {'id': 'californiad', 'website': 'http://www.uscourts.gov/'},
        ]
        self.host_map = build.build_host_map(self.courts)

    def test_single_court_host_matches(self):
        status, court_id, candidates = build.classify_link(self.host_map, 'www.cand.uscourts.gov')
        self.assertEqual(status, 'matched')
        self.assertEqual(court_id, 'cand')
        self.assertEqual(candidates, [])

    def test_shared_host_is_ambiguous_with_candidates(self):
        status, court_id, candidates = build.classify_link(self.host_map, 'www.txs.uscourts.gov')
        self.assertEqual(status, 'ambiguous')
        self.assertIsNone(court_id)
        self.assertEqual(candidates, ['txsb', 'txsd'])

    def test_bare_uscourts_gov_placeholder_never_matches(self):
        status, court_id, candidates = build.classify_link(self.host_map, 'www.uscourts.gov')
        self.assertEqual(status, 'none')
        self.assertIsNone(court_id)
        self.assertEqual(candidates, [])

    def test_unknown_host_is_none(self):
        status, court_id, candidates = build.classify_link(self.host_map, 'example.com')
        self.assertEqual(status, 'none')

    def test_no_host_is_none(self):
        status, court_id, candidates = build.classify_link(self.host_map, '')
        self.assertEqual(status, 'none')

    def test_placeholder_excluded_from_map_entirely(self):
        self.assertNotIn('uscourts.gov', self.host_map)


class PathHandling(unittest.TestCase):
    def test_relative_staging_prefixed_path(self):
        raw = 'staging\\court_expansion_file_download_v2_4_2026-08-20\\wave_001\\files\\a.pdf'
        self.assertEqual(build.relative_to_staging(raw), 'court_expansion_file_download_v2_4_2026-08-20/wave_001/files/a.pdf')

    def test_absolute_staging_path(self):
        raw = str(build.STAGING) + '\\state_trial_court_acquisition_v1_2026-08-20\\files\\ga\\x.doc'
        rel = build.relative_to_staging(raw)
        self.assertEqual(rel, 'state_trial_court_acquisition_v1_2026-08-20/files/ga/x.doc')

    def test_absolute_path_outside_staging_is_refused(self):
        self.assertIsNone(build.relative_to_staging('C:\\Users\\firas\\Downloads\\other\\x.pdf'))

    def test_empty_path(self):
        self.assertIsNone(build.relative_to_staging(''))
        self.assertIsNone(build.relative_to_staging(None))

    def test_filename_decodes_url_encoding(self):
        self.assertEqual(build.filename_of('https://x/y/Probate%20Court%20Rules.pdf'), 'Probate Court Rules.pdf')

    def test_extension_of(self):
        self.assertEqual(build.extension_of('a.PDF'), 'pdf')
        self.assertEqual(build.extension_of('a'), '')
        self.assertEqual(build.extension_of(''), '')


class StateOf(unittest.TestCase):
    def test_single_valid_code(self):
        self.assertEqual(build.state_of(['ct']), 'CT')

    def test_multiple_codes_is_null(self):
        self.assertIsNone(build.state_of(['ct', 'ny']))

    def test_empty_is_null(self):
        self.assertIsNone(build.state_of([]))
        self.assertIsNone(build.state_of(None))

    def test_invalid_code_is_null(self):
        self.assertIsNone(build.state_of(['zz']))


class NormalizedRow(unittest.TestCase):
    def setUp(self):
        self.courts = [{'id': 'ctprobate', 'website': 'https://www.ctprobate.gov/', 'state': 'CT'},
                       {'id': 'txsd', 'website': 'http://www.txs.uscourts.gov/', 'state': 'TX'},
                       {'id': 'txsb', 'website': 'http://www.txs.uscourts.gov/bankruptcy/', 'state': 'TX'}]
        self.host_map = build.build_host_map(self.courts)
        self.id_to_state = build.build_id_to_state(self.courts)
        self.families = {'abc123': ['rules_local_rules_and_procedures']}

    def test_dropped_when_not_downloaded(self):
        raw = {'download_status': 'download_error', 'artifact_id': 'court-expansion-artifact:abc123', 'sha256': 'a' * 64}
        self.assertIsNone(build.normalized_row(raw, 'm', 'wave_001', self.host_map, self.families, self.id_to_state))

    def test_dropped_when_sha256_malformed(self):
        raw = {'download_status': 'downloaded', 'artifact_id': 'court-expansion-artifact:abc123', 'sha256': 'not-a-hash'}
        self.assertIsNone(build.normalized_row(raw, 'm', 'wave_001', self.host_map, self.families, self.id_to_state))

    def test_happy_path(self):
        raw = {
            'download_status': 'downloaded', 'artifact_id': 'court-expansion-artifact:abc123', 'sha256': 'a' * 64,
            'source_url': 'https://www.ctprobate.gov/Documents/Rules.pdf', 'final_url': 'https://www.ctprobate.gov/Documents/Rules.pdf',
            'local_path': 'staging\\court_expansion_file_download_v2_4_2026-08-20\\wave_001\\files\\x.pdf',
            'bytes': 100, 'content_type': 'application/pdf', 'jurisdiction_codes': ['ct'], 'authority_lanes': ['state_or_dc_judiciary'],
            'started_at': '2026-08-20T00:00:00Z', 'completed_at': '2026-08-20T00:00:01Z', 'http_status': 200,
        }
        row = build.normalized_row(raw, 'court_expansion_file_download_v2_4_2026-08-20', 'wave_001', self.host_map, self.families, self.id_to_state)
        self.assertEqual(row['id'], 'court-expansion-artifact:abc123')
        self.assertEqual(row['doc_type'], build.LOCAL_RULE)
        self.assertEqual(row['link_status'], 'matched')
        self.assertEqual(row['court_id'], 'ctprobate')
        self.assertEqual(row['state'], 'CT')
        self.assertEqual(row['local_rel_path'], 'court_expansion_file_download_v2_4_2026-08-20/wave_001/files/x.pdf')
        self.assertIsNone(row['title'])

    def test_court_state_populated_from_matched_court_even_without_jurisdiction_code(self):
        """Repair review defect: 8,099 matched rows carry state NULL because jurisdiction_codes was
        absent/ambiguous even though the matched court_spine row itself has a state. court_state must
        be populated from the matched court's own native state field, independent of `state`."""
        raw = {
            'download_status': 'downloaded', 'artifact_id': 'court-expansion-artifact:abc123', 'sha256': 'a' * 64,
            'source_url': 'https://www.ctprobate.gov/Documents/Rules.pdf', 'final_url': 'https://www.ctprobate.gov/Documents/Rules.pdf',
            'local_path': 'staging\\x.pdf', 'bytes': 100, 'content_type': 'application/pdf',
            'jurisdiction_codes': [],  # no jurisdiction code at all -> state is None
        }
        row = build.normalized_row(raw, 'm', 'wave_001', self.host_map, self.families, self.id_to_state)
        self.assertIsNone(row['state'])
        self.assertEqual(row['court_state'], 'CT')

    def test_court_state_null_when_ambiguous(self):
        raw = {
            'download_status': 'downloaded', 'artifact_id': 'court-expansion-artifact:def456', 'sha256': 'b' * 64,
            'source_url': 'http://www.txs.uscourts.gov/foo.pdf', 'final_url': 'http://www.txs.uscourts.gov/foo.pdf',
            'local_path': 'staging\\y.pdf', 'bytes': 5,
        }
        row = build.normalized_row(raw, 'm', 'wave_001', self.host_map, self.families, self.id_to_state)
        self.assertEqual(row['link_status'], 'ambiguous')
        self.assertIsNone(row['court_state'])


class BuildIntegration(unittest.TestCase):
    """Runs the real build against the real inputs; skipped if the local bulk store is absent."""

    def test_index_builds_and_hash_gate_is_consistent(self):
        if not build.COURT_EXPANSION_DIR.exists() or not build.STATE_TRIAL_DIR.exists():
            self.skipTest('SW-BULK staging inputs not present in this environment')
        db_path = Path(__file__).resolve().parent / '_test_index.sqlite3'
        try:
            counts = build.build(db_path=db_path)
            self.assertGreater(sum(counts['manifests'].values()), 40000)
            con = sqlite3.connect(str(db_path))
            total_rows = con.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
            self.assertEqual(total_rows, sum(counts['manifests'].values()))
            distinct_ids = con.execute('SELECT COUNT(DISTINCT id) FROM documents').fetchone()[0]
            self.assertEqual(distinct_ids, total_rows)
            bad_sha = con.execute("SELECT COUNT(*) FROM documents WHERE sha256 IS NULL OR length(sha256) != 64").fetchone()[0]
            self.assertEqual(bad_sha, 0)
            ambiguous_without_candidates = con.execute(
                "SELECT COUNT(*) FROM documents WHERE link_status='ambiguous' AND candidate_court_ids='[]'").fetchone()[0]
            self.assertEqual(ambiguous_without_candidates, 0)
            matched_with_candidates = con.execute(
                "SELECT COUNT(*) FROM documents WHERE link_status='matched' AND court_id IS NULL").fetchone()[0]
            self.assertEqual(matched_with_candidates, 0)
            con.close()
        finally:
            if db_path.exists():
                db_path.unlink()


if __name__ == '__main__':
    unittest.main()
