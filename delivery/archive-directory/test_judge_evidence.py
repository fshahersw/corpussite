#!/usr/bin/env python3
"""Adapter tests for judge_evidence.py (written before the adapter; real data, no network)."""
from __future__ import annotations
import csv
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import judge_evidence  # noqa: E402

FOLDER = ROOT / 'sources/judge_evidence_20260919'
MARTINOTTI = 'judge-entity-85b06ec4ad087308b3f7d0f4'
OVERLAY = ROOT / 'sources/judge_structured_20260919/overlay.jsonl'
FJC_CSV = ROOT / 'sources/judges/enrichment_20260914/federal_biographies/judges.csv'
CJRA = ROOT / 'sources/federal_court_statistics_20260919/cjra_rows.jsonl'
FORBIDDEN = re.compile(r'[A-Za-z]:[\\/]|/Users/|\\\\|@[a-z0-9-]+\.[a-z]{2,}', re.I)


def _build_module():
    spec = importlib.util.spec_from_file_location('judge_evidence_build', FOLDER / 'build.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _cjra_blocks(record):
    return ([record['cjra']] + record['cjra']['other_courts']) if record.get('cjra') else []


def _letters(value):
    import unicodedata
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')) if not unicodedata.combining(c))
    return re.sub(r'[^A-Z]', '', text.upper())


class GateTests(unittest.TestCase):
    def test_tamper_closes_the_gate(self):
        tmp = Path(tempfile.mkdtemp(prefix='judge_evidence_gate_'))
        try:
            for name in ('validation.json', 'evidence.jsonl', 'unresolved.jsonl'):
                shutil.copy2(FOLDER / name, tmp / name)
            self.assertTrue(judge_evidence.summary(folder=tmp)['available'])
            self.assertIsNotNone(judge_evidence.evidence_for(MARTINOTTI, folder=tmp))
            with open(tmp / 'evidence.jsonl', 'ab') as handle:
                handle.write(b'{"entity_id":"judge-entity-forged","cjra":{"tables":[]}}\n')
            closed = judge_evidence.summary(folder=tmp)
            self.assertFalse(closed['available'])
            self.assertIn('hash_mismatch', closed['reason'])
            self.assertIsNone(judge_evidence.evidence_for(MARTINOTTI, folder=tmp))
            self.assertIsNone(judge_evidence.evidence_for('judge-entity-forged', folder=tmp))
            self.assertFalse(judge_evidence.cjra_unresolved(folder=tmp)['available'])
            self.assertFalse(judge_evidence.listing({}, folder=tmp)['available'])
            self.assertIsNone(judge_evidence.detail(MARTINOTTI, folder=tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_not_ready_validation_closes_the_gate(self):
        tmp = Path(tempfile.mkdtemp(prefix='judge_evidence_gate_'))
        try:
            for name in ('evidence.jsonl', 'unresolved.jsonl'):
                shutil.copy2(FOLDER / name, tmp / name)
            validation = json.loads((FOLDER / 'validation.json').read_text(encoding='utf-8'))
            validation['ready'] = False
            (tmp / 'validation.json').write_text(json.dumps(validation), encoding='utf-8')
            self.assertFalse(judge_evidence.summary(folder=tmp)['available'])
            self.assertIsNone(judge_evidence.evidence_for(MARTINOTTI, folder=tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_folder_is_unavailable(self):
        self.assertFalse(judge_evidence.summary(folder=FOLDER / 'does-not-exist')['available'])


class RealDataTests(unittest.TestCase):
    def test_martinotti_assignments_via_courtlistener_person_8598(self):
        ev = judge_evidence.evidence_for(MARTINOTTI)
        self.assertIsNotNone(ev)
        self.assertEqual(ev['ids']['cl_person_id'], '8598')
        block = ev['assignments']
        self.assertEqual(block['join']['cl_person_id'], '8598')
        self.assertIn('cl_person_id', block['join']['basis'])
        self.assertGreater(block['assigned_count'] + block['referred_count'], 0)
        self.assertRegex(block['dataset_label'],
                         r'^matters in the saved docket dataset \(release built 2026-08-24; [\d,]+ of 4,159 matters\)$')
        self.assertLessEqual(len(block['recent_matters']), 25)
        self.assertTrue(any(c['court_id'] == 'njd' for c in block['by_court']))
        self.assertLessEqual(len(block['by_nature_of_suit']), 8)
        first = block['recent_matters'][0]
        for key in ('docket_number', 'case_name', 'court_id', 'date_filed', 'date_terminated', 'roles'):
            self.assertIn(key, first)
        dates = [m['date_filed'] or '' for m in block['recent_matters']]
        self.assertEqual(dates, sorted(dates, reverse=True))
        # independent recount from the release files
        import gzip
        rel = Path('C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc')
        with gzip.open(rel / 'judges.jsonl.gz', 'rt', encoding='utf-8') as fh:
            jids = {json.loads(l)['judge_id'] for l in fh if '"8598"' in l and json.loads(l).get('native_judge_id') == '8598'}
        assigned, referred = set(), set()
        with gzip.open(rel / 'judicial_assignments.jsonl.gz', 'rt', encoding='utf-8') as fh:
            for line in fh:
                row = json.loads(line)
                if row['judge_id'] in jids:
                    (assigned if row['role'] == 'assigned_judge' else referred).add(row['matter_id'])
        self.assertEqual((block['assigned_count'], block['referred_count']), (len(assigned), len(referred)))
        self.assertEqual(block['matters_for_this_judge'], len(assigned | referred))
        self.assertTrue(ev['education'])
        self.assertTrue(any(e['school'] == 'Seton Hall University' and e['degree_year'] == '1986' for e in ev['education']))
        self.assertTrue(any(p.get('date_start_granularity') == '%Y' and p.get('date_start_display') == '1986' for p in ev['positions']))

    def test_cjra_match_chain_rederived(self):
        ev = judge_evidence.evidence_for(MARTINOTTI)
        cjra = ev['cjra']
        self.assertIsNotNone(cjra, 'Martinotti is a serving D.N.J. district judge and is printed in the CJRA tables')
        self.assertEqual(cjra['as_of'], '2026-03-31')
        self.assertEqual(cjra['caveat'], 'publisher-reported counts for the period; MDL member cases inflate counts; never a rate')
        self.assertEqual(cjra['court_as_printed'], 'U.S. District Court for New Jersey')
        # 1. the printed rows: same court, same printed judge, counts as printed
        printed = _read(CJRA)
        mine = [r for r in printed if r['court_as_printed'] == 'U.S. District Court for New Jersey'
                and _letters(r['judge_name_as_printed'].split(',')[0]) == 'MARTINOTTI']
        self.assertTrue(mine)
        self.assertTrue(all(r['judge_type_as_printed'] == 'District Judge' for r in mine))
        self.assertEqual(sorted((t['table'], t['count']) for t in cjra['tables']),
                         sorted((r['table_number'], r['count']) for r in mine))
        given = mine[0]['judge_name_as_printed'].split(',')[1].split()
        self.assertEqual(given[0][0].upper(), 'B')
        # 2. the entity side: overlay ids -> FJC nid -> FJC export name + D.N.J. appointment
        qualifying = []
        fjc = {}
        with open(FJC_CSV, encoding='utf-8-sig', newline='') as fh:
            for row in csv.DictReader(fh):
                fjc[row['nid']] = row
        with open(OVERLAY, encoding='utf-8') as fh:
            for line in fh:
                o = json.loads(line)
                nid = (o.get('ids') or {}).get('fjc_nid')
                row = fjc.get(str(nid)) if nid else None
                if not row or _letters(row['Last Name']) != 'MARTINOTTI':
                    continue
                courts = [a.get('court') for a in (o.get('fjc') or {}).get('appointments') or []]
                if 'U.S. District Court for the District of New Jersey' in courts and row['First Name'].strip()[:1].upper() == 'B':
                    qualifying.append(o['entity_id'])
        self.assertEqual(qualifying, [MARTINOTTI])  # exactly one entity qualifies
        basis = cjra['match_basis']
        self.assertEqual(basis['fjc_nid'], '1394871')
        self.assertEqual(basis['fjc_court'], 'U.S. District Court for the District of New Jersey')
        self.assertEqual(basis['qualifying_entities'], 1)
        self.assertIn('surname', basis['rule'])

    def test_no_rates_predictions_or_rankings(self):
        keys = set()

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    keys.add(k.lower())
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        for record in _read(FOLDER / 'evidence.jsonl'):
            walk(record)
        for key in keys:
            for word in ('rate', 'percent', 'predict', 'rank', 'score', 'likelihood', 'average'):
                self.assertNotIn(word, key)

    def test_public_dicts_are_path_free(self):
        for payload in (judge_evidence.evidence_for(MARTINOTTI), judge_evidence.summary(),
                        judge_evidence.cjra_unresolved(page=1, limit=50), judge_evidence.listing({'limit': '50'}),
                        judge_evidence.detail(MARTINOTTI)):
            self.assertIsNone(FORBIDDEN.search(json.dumps(payload)), 'path or email leaked')

    def test_unknown_entity_is_none(self):
        self.assertIsNone(judge_evidence.evidence_for('judge-entity-nope'))
        self.assertIsNone(judge_evidence.evidence_for(None))
        self.assertIsNone(judge_evidence.detail('judge-entity-nope'))

    def test_summary_counts_agree_with_data(self):
        s = judge_evidence.summary()
        self.assertTrue(s['available'])
        self.assertEqual(s['counts']['entities_total'], 10669)
        rows = _read(FOLDER / 'evidence.jsonl')
        self.assertEqual(s['counts']['entities_with_any_block'], len(rows))
        self.assertEqual(s['counts']['entities_with_cjra'], sum(1 for r in rows if r.get('cjra')))
        self.assertEqual(s['counts']['entities_with_assignments'], sum(1 for r in rows if r.get('assignments')))
        self.assertEqual(s['counts']['entities_with_education'], sum(1 for r in rows if r.get('education')))
        self.assertEqual(s['counts']['entities_with_positions'], sum(1 for r in rows if r.get('positions')))
        self.assertEqual(len({r['entity_id'] for r in rows}), len(rows))

    def test_magistrate_rows_are_unresolved_never_attached(self):
        page = judge_evidence.cjra_unresolved(page=1, limit=100, reason='magistrate_judge_not_in_fjc_directory')
        self.assertTrue(page['available'])
        self.assertGreater(page['total'], 0)
        self.assertLessEqual(len(page['results']), 100)
        self.assertTrue(all(r['judge_type_as_printed'] == 'Magistrate Judge' for r in page['results']))
        rows = _read(FOLDER / 'evidence.jsonl')
        for r in rows:
            for block in _cjra_blocks(r):
                self.assertNotIn('Magistrate', block['judge_type_as_printed'])
                self.assertNotIn('Bankruptcy', block['judge_type_as_printed'])

    def test_judge_printed_in_two_courts_keeps_counts_separate(self):
        rows = _read(FOLDER / 'evidence.jsonl')
        multi = [r for r in rows if r.get('cjra') and r['cjra']['other_courts']]
        for r in multi:
            blocks = _cjra_blocks(r)
            self.assertEqual(len({b['court_as_printed'] for b in blocks}), len(blocks))
            self.assertEqual(len({b['match_basis']['fjc_court'] for b in blocks}), len(blocks))
            for b in blocks:
                self.assertEqual(len({t['table'] for t in b['tables']}), len(b['tables']))

    def test_listing_shows_every_printed_court_for_two_district_judges(self):
        """Regression: the list cell used only the first court, hiding non-zero counts printed for the second court."""
        wimes = 'judge-entity-e690777ff9ab7cf412d195a6'
        row = [r for r in judge_evidence.listing({'q': 'wimes'})['results'] if r['id'] == wimes][0]
        cell = row['cells']['cjra']
        self.assertIn('Missouri Eastern', cell)
        self.assertIn('Missouri Western', cell)
        self.assertIn('CJRA 7: 2', cell)
        self.assertEqual(cell, 'Missouri Eastern - CJRA 7: 0; CJRA 8: 0; CJRA 9: 0 | Missouri Western - CJRA 7: 2; CJRA 8: 0; CJRA 9: 0')
        # every two-court judge: one court-labelled segment per printed block, counts as printed, never summed
        multi = [r for r in _read(FOLDER / 'evidence.jsonl') if r.get('cjra') and r['cjra']['other_courts']]
        self.assertEqual(len(multi), 3)
        for record in multi:
            listed = [r for r in judge_evidence.listing({'q': record['name'], 'limit': '100'})['results'] if r['id'] == record['entity_id']][0]
            segments = listed['cells']['cjra'].split(' | ')
            blocks = _cjra_blocks(record)
            self.assertEqual(len(segments), len(blocks))
            for segment, block in zip(segments, blocks):
                label = block['court_as_printed'].replace('U.S. District Court for ', '', 1)
                self.assertEqual(segment, label + ' - ' + '; '.join('%s: %s' % (t['table'], format(t['count'], ',')) for t in block['tables']))
        # a judge printed in one court keeps the plain text (no court label, no separator)
        single = [r for r in judge_evidence.listing({'q': 'martinotti'})['results'] if r['id'] == MARTINOTTI][0]
        self.assertRegex(single['cells']['cjra'], r'^CJRA 7: [\d,]+; CJRA 8: [\d,]+; CJRA 9: [\d,]+$')

    def test_every_cjra_row_is_accounted_for_once(self):
        attached = []
        for r in _read(FOLDER / 'evidence.jsonl'):
            for block in _cjra_blocks(r):
                attached += block['row_ids']
        unresolved = []
        for r in _read(FOLDER / 'unresolved.jsonl'):
            if r['kind'] == 'cjra':
                unresolved += r['row_ids']
        self.assertEqual(len(attached) + len(unresolved), 6329)
        self.assertEqual(len(set(attached) | set(unresolved)), 6329)

    def test_ambiguous_real_rows_stay_unresolved(self):
        page = judge_evidence.cjra_unresolved(page=1, limit=100, reason='ambiguous_multiple_entities')
        attached = {}
        for r in _read(FOLDER / 'evidence.jsonl'):
            for block in _cjra_blocks(r):
                attached.setdefault(r['entity_id'], set()).update(block['row_ids'])
        self.assertGreater(page['total'], 0, 'father/son judges of one district are printed in the 2026-03-31 tables')
        for row in page['results']:
            self.assertGreaterEqual(len(row['candidate_entity_ids']), 2)
            for entity_id in row['candidate_entity_ids']:
                self.assertFalse(attached.get(entity_id, set()) & set(row['row_ids']))

    def test_listing_and_detail_shape(self):
        lst = judge_evidence.listing({'q': 'martinotti', 'limit': '10'})
        self.assertTrue(lst['available'])
        for key in ('total', 'page', 'limit', 'qualification', 'filters', 'columns', 'results'):
            self.assertIn(key, lst)
        self.assertLessEqual(len(lst['filters']), 7)
        self.assertLessEqual(len(lst['columns']), 5)
        self.assertIn(MARTINOTTI, [r['id'] for r in lst['results']])
        self.assertEqual(judge_evidence.listing({'limit': '5000'})['limit'], 100)
        det = judge_evidence.detail(MARTINOTTI)
        for key in ('id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'):
            self.assertIn(key, det)
        self.assertTrue(any(s['heading'].startswith('Matters in the saved docket dataset') for s in det['sections']))


class MatcherRuleTests(unittest.TestCase):
    """The matching rule itself, on synthetic entities (no real person is involved)."""

    @classmethod
    def setUpClass(cls):
        cls.build = _build_module()

    def _entity(self, entity_id, last, first, middle, court, termination=None):
        return {'entity_id': entity_id, 'fjc_nid': entity_id[-3:], 'last': last, 'first': first, 'middle': middle, 'suffix': '',
                'appointments': [{'court': court, 'court_type': 'U.S. District Court', 'termination_date': termination}]}

    def test_ambiguous_name_stays_unresolved(self):
        court = 'U.S. District Court for the District of New Jersey'
        index = self.build.build_entity_index([
            self._entity('judge-entity-001', 'Smith', 'John', 'A.', court),
            self._entity('judge-entity-002', 'Smith', 'James', 'A.', court)])
        out = self.build.match_printed_judge('Smith, J. A.', 'District Judge', 'U.S. District Court for New Jersey', index)
        self.assertIsNone(out['entity_id'])
        self.assertEqual(out['reason_code'], 'ambiguous_multiple_entities')
        self.assertEqual(sorted(out['candidate_entity_ids']), ['judge-entity-001', 'judge-entity-002'])

    def test_single_candidate_matches_and_other_court_does_not(self):
        index = self.build.build_entity_index([
            self._entity('judge-entity-001', 'Smith', 'John', 'A.', 'U.S. District Court for the District of New Jersey'),
            self._entity('judge-entity-002', 'Smith', 'John', 'A.', 'U.S. District Court for the Eastern District of New York')])
        out = self.build.match_printed_judge('SMITH, JOHN A', 'District Judge', 'U.S. District Court for New Jersey', index)
        self.assertEqual(out['entity_id'], 'judge-entity-001')
        miss = self.build.match_printed_judge('SMITH, JOHN A', 'District Judge', 'U.S. District Court for Vermont', index)
        self.assertIsNone(miss['entity_id'])
        self.assertEqual(miss['reason_code'], 'no_entity_with_same_surname_and_court')

    def test_initial_conflict_and_magistrate_and_long_terminated(self):
        court = 'U.S. District Court for the District of New Jersey'
        index = self.build.build_entity_index([
            self._entity('judge-entity-001', 'Smith', 'John', 'A.', court),
            self._entity('judge-entity-003', 'Jones', 'Mary', '', court, termination='1955-03-01')])
        self.assertEqual(self.build.match_printed_judge('Smith, Robert', 'District Judge', 'U.S. District Court for New Jersey', index)['reason_code'],
                         'given_name_does_not_agree')
        self.assertEqual(self.build.match_printed_judge('Smith, John B.', 'District Judge', 'U.S. District Court for New Jersey', index)['reason_code'],
                         'given_name_does_not_agree')
        self.assertEqual(self.build.match_printed_judge('Smith, John A.', 'Magistrate Judge', 'U.S. District Court for New Jersey', index)['reason_code'],
                         'magistrate_judge_not_in_fjc_directory')
        self.assertEqual(self.build.match_printed_judge('Jones, Mary', 'District Judge', 'U.S. District Court for New Jersey', index)['reason_code'],
                         'no_fjc_appointment_open_on_as_of_date')
        self.assertEqual(self.build.match_printed_judge('Unassigned', 'District Judge', 'U.S. District Court for New Jersey', index)['reason_code'],
                         'name_unparseable')

    def test_court_normalisation(self):
        norm = self.build.court_key
        self.assertEqual(norm('U.S. District Court for Alabama Middle'), norm('U.S. District Court for the Middle District of Alabama'))
        self.assertEqual(norm('U.S. District Court for District Of Columbia'), norm('U.S. District Court for the District of Columbia'))
        self.assertEqual(norm('U.S. District Court for New York Southern'), norm('U.S. District Court for the Southern District of New York'))
        self.assertNotEqual(norm('U.S. District Court for New York Southern'), norm('U.S. District Court for the Eastern District of New York'))
        self.assertNotEqual(norm('U.S. District Court for Virginia Western'), norm('U.S. District Court for the Western District of West Virginia'))


if __name__ == '__main__':
    unittest.main()
