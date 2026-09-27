"""Offline checks on the court-docket evidence layer (typing guard, receipts, published rows)."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import court_docs  # noqa: E402


def rows(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding='utf-8').splitlines() if line.strip()]


class TypingTests(unittest.TestCase):
    def typed(self, text):
        return court_docs.type_from_description(text)['type']

    def test_orders_and_motions(self):
        self.assertEqual(self.typed('ORDER GRANTING PRELIMINARY APPROVAL OF PUBLIC WATER SYSTEM SETTLEMENT BETWEEN X AND Y re 1'), 'preliminary_approval_order')
        self.assertEqual(self.typed('ORDER granting 3104 MOTION for Preliminary Approval of the Proposed Settlement. Signed by Judge X.'), 'preliminary_approval_order')
        self.assertEqual(self.typed('FINAL APPROVAL ORDER of Hetero Valsartan Economic Loss Class Action Settlement. Signed by X.'), 'final_approval_order')
        self.assertEqual(self.typed('MOTION for Settlement Motion for Final Approval of Certain Class Settlements by A, B.'), 'approval_motion')
        self.assertEqual(self.typed('CASE MANAGEMENT ORDER NO. 56 (Establishment of Qualified Settlement Fund) Signed by Judge X'), 'case_management_or_settlement_order')

    def test_wrappers_and_procedural_entries_stay_unclassified(self):
        for text in ('ORDER Approving proposed schedule. Signed by Magistrate Judge X on 12/15/2025.',
                     'ORDER APPROVING SETTLEMENT AGREEMENT AND RELEASE This document relates to: A et al. v. B',
                     'TEXT ORDER Any settlement class member may speak as set forth in the Order Granting Preliminary Approval of Settlement Agreement',
                     'Consent MOTION for Leave to File Excess Pages for Memorandum in Support of their Motion for Final Approval by A',
                     'Set Deadlines as to 3303 MOTION for Settlement Motion for Final Approval of Certain Class Settlements.',
                     'RESPONSE by Plaintiffs to objections 927 to Proposed Common Benefit Order',
                     'Transcript of Fairness Hearing on Motion for Final Approval of Proposed Settlements held on 6/30/2026',
                     'ORDER GRANTING JOINT MOTION TO AMEND THE PRELIMINARILY APPROVED SETTLEMENT AGREEMENTS'):
            self.assertEqual(self.typed(text), 'unclassified', text)


class PhraseSplitTests(unittest.TestCase):
    def test_term_lists(self):
        self.assertEqual(court_docs.NON_SPECIFIC_TERMS, ['common benefit', 'order approving'])
        self.assertEqual(sorted(court_docs.SETTLEMENT_SPECIFIC_TERMS),
                         sorted(['settlement agreement', 'master settlement', 'preliminary approval', 'final approval',
                                 'qualified settlement fund', 'claims administrator', 'notice plan', 'plan of allocation']))
        self.assertEqual(sorted(court_docs.SEARCH_TERMS), sorted(court_docs.SETTLEMENT_SPECIFIC_TERMS + court_docs.NON_SPECIFIC_TERMS))
        for file in sorted((HERE / 'receipts_court').glob('search_mdl_*.json')):
            query = json.loads(file.read_text(encoding='utf-8'))['arguments']['q'].lower()
            for term in court_docs.SEARCH_TERMS:
                self.assertIn(term, query, file.name)

    def test_phrase_hits(self):
        self.assertEqual(court_docs.phrase_hits('CASE MANAGEMENT ORDER NO. 14 (Establishing Common Benefit Fee and Expense Protocols)'),
                         ([], ['common benefit']))
        self.assertEqual(court_docs.phrase_hits('ORDER Approving proposed schedule.'), ([], ['order approving']))
        self.assertEqual(court_docs.phrase_hits('ORDER APPROVING SETTLEMENT AGREEMENT AND RELEASE'), (['settlement agreement'], ['order approving']))
        self.assertEqual(court_docs.phrase_hits('Minute entry'), ([], []))

    def test_titles(self):
        self.assertEqual(court_docs.row_title(2873, 20), 'Settlement-phrase docket search for MDL 2873')
        self.assertEqual(court_docs.row_title(3060, 0), 'Settlement-phrase docket search for MDL 3060: no settlement-specific entry found')

    def test_case_management_label_needs_a_settlement_specific_phrase(self):
        plain = court_docs.court_type('case_management_or_settlement_order', False)
        self.assertEqual(plain[0], 'case_management_order')
        self.assertNotIn('settlement-administration', plain[1])
        kept = court_docs.court_type('case_management_or_settlement_order', True)
        self.assertEqual(kept[0], 'case_management_or_settlement_order')
        self.assertEqual(court_docs.court_type('court_opinion', False)[0], 'court_opinion')


class PublishedDataTests(unittest.TestCase):
    def test_receipts_hashes_and_rows(self):
        docs, mdl_rows = rows('court_documents.jsonl'), [r for r in rows('settlements.jsonl') if r['record_layer'] == court_docs.LAYER]
        self.assertEqual(len(mdl_rows), 8)
        self.assertEqual(len(docs), 92)
        for doc in docs:
            receipt = (HERE / doc['receipt_file']).read_bytes()
            self.assertEqual(hashlib.sha256(receipt).hexdigest(), doc['receipt_sha256'])
            payload = json.loads(receipt.decode('utf-8'))
            self.assertIn('not original HTTP bytes', payload['label'])
            self.assertEqual(payload['docket_id'], doc['docket_id'])
            self.assertTrue(any(item['description'] == doc['description_as_recorded'] for item in payload['response']['results']))
            self.assertFalse(doc['documents_downloaded'])
            self.assertTrue(doc['matched_terms'])
        for row in mdl_rows:
            self.assertEqual(row['title'], court_docs.row_title(row['mdl_number'], row['settlement_specific_entries']))
            self.assertTrue(row['title'].startswith('Settlement-phrase docket search for MDL %d' % row['mdl_number']))
            self.assertNotIn('settlement activity', row['title'].lower())
            self.assertEqual(row['mdl_ref'], '#mdl/%d' % row['mdl_number'])
            self.assertIsNone(row['amount'])
            self.assertIn('Not a settlement record', row['evidence_label'])
        edges = rows('edges.jsonl')
        self.assertEqual({e['to']['id'] for e in edges},
                         {'mdl:%d' % r['mdl_number'] for r in mdl_rows if r['settlement_specific_entries'] > 0})

    def test_phrase_split_counts_per_row(self):
        docs, mdl_rows = rows('court_documents.jsonl'), [r for r in rows('settlements.jsonl') if r['record_layer'] == court_docs.LAYER]
        for row in mdl_rows:
            mine = [d for d in docs if d['mdl_number'] == row['mdl_number']]
            specific = [d for d in mine if d['settlement_specific']]
            self.assertEqual(row['entries_total'], len(mine))
            self.assertEqual(row['settlement_specific_entries'], len(specific))
            self.assertEqual(row['settlement_specific_label'],
                             'entries matching a settlement-specific phrase: %d of %d' % (len(specific), len(mine)))
        for doc in docs:
            self.assertEqual(doc['settlement_specific'], bool(doc['matched_settlement_specific_terms']))
            self.assertEqual(sorted(doc['matched_terms']),
                             sorted(doc['matched_settlement_specific_terms'] + doc['matched_non_specific_terms']))
            self.assertTrue(set(doc['matched_settlement_specific_terms']) <= set(court_docs.SETTLEMENT_SPECIFIC_TERMS))
            self.assertTrue(set(doc['matched_non_specific_terms']) <= set(court_docs.NON_SPECIFIC_TERMS))
        self.assertEqual({r['mdl_number']: r['settlement_specific_entries'] for r in mdl_rows},
                         {2666: 4, 2738: 0, 2789: 3, 2846: 4, 2873: 20, 2875: 20, 3004: 4, 3060: 0})

    def test_mdls_without_settlement_specific_entry_are_not_published_as_settlement_activity(self):
        # Regression (review 2026-09-19): MDL 3060 matched only "common benefit"; MDL 2738 only "common benefit" / "order approving".
        docs, all_rows = rows('court_documents.jsonl'), rows('settlements.jsonl')
        edges = rows('edges.jsonl')
        blob = (HERE / 'edges.jsonl').read_text(encoding='utf-8')
        for number in (3060, 2738):
            row = [r for r in all_rows if r['settlement_id'] == 'mdl-docket-%d' % number][0]
            self.assertEqual(row['settlement_specific_entries'], 0)
            self.assertIn('no settlement-specific entry found', row['title'])
            self.assertIn('No settlement-specific entry found', row['settlement_specific_finding'])
            self.assertIn('common benefit', row['settlement_specific_finding'])
            self.assertNotIn('settlement:mdl-docket-%d' % number, blob)
            self.assertFalse([e for e in edges if e['to']['id'] == 'mdl:%d' % number])
            for doc in (d for d in docs if d['mdl_number'] == number):
                self.assertFalse(doc['settlement_specific'])
                self.assertEqual(doc['matched_settlement_specific_terms'], [])
                self.assertNotIn('settlement', doc['type_label'].lower().replace('no settlement-specific phrase', ''), doc['court_document_id'])
                self.assertNotIn('settlement', doc['type'], doc['court_document_id'])
        first_page_only = [r for r in all_rows if r['settlement_id'] == 'mdl-docket-2738'][0]
        self.assertIn('first result page only', first_page_only['settlement_specific_finding'])
        for edge in edges:
            self.assertEqual(edge['relation'], 'docket_phrase_search_for')
            self.assertGreater(edge['evidence']['settlement_specific_entries'], 0)
            self.assertGreaterEqual(edge['evidence']['entries_total'], edge['evidence']['settlement_specific_entries'])
        self.assertEqual(len(edges), 6)
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        self.assertEqual(gate['counts']['mdl_rows_without_settlement_specific_entry'], ['2738', '3060'])

    def test_licence_conflict_text_kept(self):
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        self.assertIn('Licence conflict unresolved', gate['qualification'])
        self.assertIn('CONFLICTS', gate['license_ref'])
        self.assertIn('docket-entry evidence only', gate['qualification'])


if __name__ == '__main__':
    unittest.main()
