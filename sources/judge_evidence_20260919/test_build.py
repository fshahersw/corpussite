#!/usr/bin/env python3
"""Build-level tests for the judge evidence layer (offline)."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('judge_evidence_build', HERE / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def read(name):
    with open(HERE / name, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


class ParsingTests(unittest.TestCase):
    def test_printed_names(self):
        self.assertEqual(build.parse_printed_name('LACOUR, EDMUND G. JR.'), {'surname': 'LACOUR', 'given': ['EDMUND', 'G'], 'suffix': ['JR']})
        self.assertEqual(build.parse_printed_name('Larkins, John K., III')['suffix'], ['III'])
        self.assertEqual(build.parse_printed_name('Aenlle-rocha, Fernando L')['surname'], 'AENLLEROCHA')
        self.assertIsNone(build.parse_printed_name('Unassigned'))
        self.assertIsNone(build.parse_printed_name('CRIMINAL FUGITIVE'))

    def test_given_name_rule(self):
        entity = {'first': 'Margaret', 'middle': 'Catharine'}
        self.assertTrue(build.given_agrees(['M', 'C'], entity))
        # a differently spelled full middle name is a conflict, not a match: the row stays unresolved (never guessed)
        self.assertFalse(build.given_agrees(['M', 'CASEY'], entity))
        self.assertTrue(build.given_agrees(['MARGARET'], entity))
        self.assertFalse(build.given_agrees(['MARY'], entity))
        self.assertFalse(build.given_agrees(['M', 'T'], entity))
        self.assertFalse(build.given_agrees([], entity))

    def test_granularity_display(self):
        self.assertEqual(build.display_date('1986-01-01', '%Y'), '1986')
        self.assertEqual(build.display_date('1986-03-01', '%Y-%m'), '1986-03')
        self.assertEqual(build.display_date('2016-07-11', '%Y-%m-%d'), '2016-07-11')
        self.assertIsNone(build.display_date('', '%Y'))


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        cls.evidence = read('evidence.jsonl')
        cls.unresolved = read('unresolved.jsonl')

    def test_envelope_and_hashes(self):
        v = self.validation
        self.assertEqual((v['schema_version'], v['status'], v['ready']), ('1', 'passed', True))
        for key in ('validated_at', 'data_files', 'counts', 'checks', 'qualification', 'license_ref', 'inputs'):
            self.assertIn(key, v)
        self.assertTrue(all(c['passed'] for c in v['checks']))
        rows = {'evidence.jsonl': len(self.evidence), 'unresolved.jsonl': len(self.unresolved)}
        for entry in v['data_files']:
            self.assertEqual(hashlib.sha256((HERE / entry['path']).read_bytes()).hexdigest(), entry['sha256'])
            self.assertEqual(entry['rows'], rows[entry['path']])
        for entry in v['inputs']:
            self.assertNotRegex(entry['path'], r'^[A-Za-z]:')

    def test_blocks_attach_only_through_native_ids(self):
        for r in self.evidence:
            if r['assignments'] or r['education'] or r['positions']:
                self.assertTrue(r['ids']['cl_person_id'])
            if r['assignments']:
                self.assertEqual(r['assignments']['join']['cl_person_id'], r['ids']['cl_person_id'])
            if r['cjra']:
                self.assertTrue(r['ids']['fjc_nid'])
                for block in [r['cjra']] + r['cjra']['other_courts']:
                    self.assertEqual(block['match_basis']['fjc_nid'], r['ids']['fjc_nid'])
                    self.assertEqual(build.court_key(block['court_as_printed']), build.court_key(block['match_basis']['fjc_court']))
                    self.assertEqual(block['caveat'], build.CJRA_CAVEAT)
                    self.assertEqual(block['as_of'], '2026-03-31')

    def test_every_unresolved_row_has_a_reason(self):
        for r in self.unresolved:
            self.assertIn(r['kind'], ('cjra', 'sw_bulk_judge'))
            self.assertTrue(r['reason_code'] and r['reason'])
        self.assertEqual(len({r['unresolved_id'] for r in self.unresolved}), len(self.unresolved))

    def test_counts_match_files(self):
        c = self.validation['counts']
        self.assertEqual(c['entities_with_cjra'], sum(1 for r in self.evidence if r['cjra']))
        self.assertEqual(c['entities_with_assignments'], sum(1 for r in self.evidence if r['assignments']))
        self.assertEqual(c['cjra_rows_attached'] + c['cjra_rows_unresolved'], c['cjra_rows_total'])
        self.assertEqual(c['cjra_rows_total'], 6329)
        self.assertEqual(c['sw_bulk_matters_total'], 4159)


if __name__ == '__main__':
    unittest.main()
