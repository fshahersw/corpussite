"""Offline checks on build.py helpers and on the published data files."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402


def rows(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding='utf-8').splitlines() if line.strip()]


class HelperTests(unittest.TestCase):
    def test_caption_only_when_printed(self):
        cap = build.caption_from_title('NYC Senior Care $15 Co-Pay Settlement (Bianculli v. City of New York)')
        self.assertEqual((cap['plaintiff_side_as_printed'], cap['defendant_side_as_printed']), ('Bianculli', 'City of New York'))
        self.assertEqual(cap['reported_by'], 'title text')
        flagged = build.caption_from_title('Hasson v. Comcast Data Breach Settlement')
        self.assertTrue(flagged['side_text_may_include_descriptive_words'])
        self.assertEqual(build.caption_from_title('In re Lemonade, Inc. Data Disclosure Litigation')['kind'], 'in_re')
        self.assertIsNone(build.caption_from_title('Patelco Credit Union $7.25M Data Breach Settlement'))

    def test_amount_only_when_printed(self):
        self.assertEqual(build.amounts_from_title('Wilshire Law Firm $5.98 Million TCPA Class Action Settlement')['as_printed'], ['$5.98 Million'])
        self.assertEqual(build.amounts_from_title('First Chatham Bank $475K Data Breach')['as_printed'], ['$475K'])
        self.assertEqual(build.amounts_from_title('Eisner Advisory Group $1,050,000 Data Breach Settlement')['as_printed'], ['$1,050,000'])
        self.assertIsNone(build.amounts_from_title('John Deere Repair Services Antitrust Settlement'))

    def test_deadline_state_against_as_of(self):
        self.assertEqual(build.deadline_state('2026-09-18'), 'passed')
        self.assertEqual(build.deadline_state('2026-09-19'), 'within_30_days')
        self.assertEqual(build.deadline_state('2026-10-19'), 'within_30_days')
        self.assertEqual(build.deadline_state('2026-10-20'), 'future')
        self.assertEqual(build.deadline_state(None), 'unknown')
        self.assertEqual(build.deadline_state('soon'), 'unknown')

    def test_keyword_suppression(self):
        self.assertEqual(build.signals('Travelers Personal Injury Protection Settlement', 't'), [])
        self.assertTrue(build.signals('Aqueous Film-Forming Foam (AFFF) Products Liability Litigation (MDL 2873)', 't'))


class PublishedDataTests(unittest.TestCase):
    def test_envelope_hashes_match(self):
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        self.assertEqual((gate['status'], gate['ready']), ('passed', True))
        for entry in gate['data_files']:
            self.assertEqual(hashlib.sha256((HERE / entry['path']).read_bytes()).hexdigest(), entry['sha256'])

    def test_counts_and_links(self):
        settlements, documents = rows('settlements.jsonl'), rows('documents.jsonl')
        self.assertEqual(sum(1 for s in settlements if s['record_layer'] == 'publisher_reference'), 848)
        known = {s['settlement_id'] for s in settlements}
        for doc in documents:
            self.assertTrue(doc['settlement_ids'] and set(doc['settlement_ids']) <= known)
            self.assertEqual(hashlib.sha256((HERE / doc['raw_path']).read_bytes()).hexdigest(), doc['sha256'])
        official = {s['settlement_id']: s['official_url'] for s in settlements}
        for doc in documents:
            for sid in doc['settlement_ids']:
                self.assertEqual(official[sid], doc['url'])

    def test_rejected_shells_are_not_documents(self):
        urls = {d['url'] for d in rows('documents.jsonl')}
        for bad in ('https://www.seniorcarecopaysettlement.com/', 'https://www.vwcourtsettlement.com/',
                    'https://www.genericdrugsendpayersettlement.com/', 'https://www.scoutingsettlementtrust.com/s/'):
            self.assertNotIn(bad, urls)
        reasons = {r['url']: r['reason'] for r in rows('rejected_captures.jsonl')}
        self.assertIn('redirect_shell', reasons['https://www.vwcourtsettlement.com/'])
        self.assertIn('403', reasons['https://www.seniorcarecopaysettlement.com/'])


if __name__ == '__main__':
    unittest.main()
