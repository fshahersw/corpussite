"""Build tests for the JPML MDL registry: parser helpers on known tricky strings, then the published outputs."""
import hashlib
import json
import re
import unittest
from pathlib import Path

import build

HERE = Path(__file__).resolve().parent


class HelperTests(unittest.TestCase):
    def test_glued_thousands_pairs_split_uniquely(self):
        self.assertEqual(build.split_glued_pair('69,25071,935')[:2], (69250, 71935))
        self.assertEqual(build.split_glued_pair('206,182716,121')[:2], (206182, 716121))
        self.assertEqual(build.split_glued_pair('23,31625,213')[:2], (23316, 25213))
        self.assertIsNone(build.split_glued_pair('2121')[0])  # no separator: ambiguous, never guessed
        self.assertIsNone(build.split_glued_pair('1550221350')[0])

    def test_parse_pair_forms(self):
        self.assertEqual(build.parse_pair('3,734 3,980')[:2], (3734, 3980))
        self.assertEqual(build.parse_pair('5 466')[:2], (5, 466))
        self.assertEqual(build.parse_pair('2 391,225')[:2], (2, 391225))
        self.assertEqual(build.parse_pair('12,12916,561')[:2], (12129, 16561))

    def test_count_pct_split(self):
        self.assertEqual(build.split_count_pct('191,91394.11'), (191913, '94.11'))
        self.assertEqual(build.split_count_pct('700,33198.34'), (700331, '98.34'))

    def test_row_regex_handles_glue(self):
        line = '2951 IN RE: StubHub Refund Litigation Gilliam, Haywood S.CAN 4:20-md-2951 05/29/2020 08/06/2020'
        m = build.ROW_RE.match(line)
        self.assertEqual((m.group('num'), m.group('dist'), m.group('master'), m.group('filed')), ('2951', 'CAN', '4:20-md-2951', '05/29/2020'))
        line = '3080 IN RE: Insulin Pricing Litigation Martinotti, Brian R. NJ          2:23-md-308005/09/2023 08/03/2023'
        m = build.ROW_RE.match(line)
        self.assertEqual((m.group('master'), m.group('filed'), m.group('transferred')), ('2:23-md-3080', '05/09/2023', '08/03/2023'))
        line = '2406 IN RE: Blue Cross Blue Shield Antitrust Litigation Manasco, Anna M. ALN          2:25-md-1000009/06/2012 12/12/2012'
        self.assertEqual(build.ROW_RE.match(line).group('master'), '2:25-md-10000')
        line = '1358 IN RE: Methyl Tertiary Butyl Ether ("MTBE") Products Liability LitigationCote, Denise L. NYS 1:00-cv-1898 06/06/2000 10/10/2000'
        self.assertEqual(build.ROW_RE.match(line).group('dist'), 'NYS')
        line = '2591 IN RE: Syngenta AG MIR162 Corn Litigation Lungstrum, John W. KS 2:14-md-2591 10/07/2014 12/11/2014 01/13/2026'
        self.assertEqual(build.ROW_RE.match(line).group('closed'), '01/13/2026')

    def test_judge_split_uses_printed_name_then_caption_fallback(self):
        rest = 'Methyl Tertiary Butyl Ether ("MTBE") Products Liability LitigationCote, Denise L.'
        self.assertEqual(build.split_judge_from_rest(rest, 'Denise L. Cote')[:2], ('Methyl Tertiary Butyl Ether ("MTBE") Products Liability Litigation', 'Cote, Denise L.'))
        rest = 'Chiquita Brands International, Inc., Alien Tort Statute and ShareholdersDerivative Litigation Altman, Roy K.'
        self.assertEqual(build.split_judge_from_rest(rest, 'Roy K. Altman')[1], 'Altman, Roy K.')
        self.assertEqual(build.split_judge_from_rest('Norada Entities Securities Litigation Court, Michelle Williams', 'Michelle Williams Court')[1], 'Court, Michelle Williams')
        title, judge, method = build.split_judge_from_rest('Xarelto (Rivaroxaban) Products Liability LitigationFallon, Eldon E.', None)
        self.assertEqual((title, judge, method), ('Xarelto (Rivaroxaban) Products Liability Litigation', 'Fallon, Eldon E.', 'caption_ends_with_Litigation'))

    def test_name_match_kinds(self):
        t = build.name_tokens
        self.assertEqual(build.name_match_kind(t('Brian R. Martinotti'), t('Brian R. Martinotti')), 'exact')
        self.assertEqual(build.name_match_kind(t('Nancy J. Rosenstengel'), t('Nancy Jo Rosenstengel')), 'middle_initial')
        self.assertEqual(build.name_match_kind(t('Vince Chhabria'), t('Vince Girdhari Chhabria')), 'middle_omitted')
        self.assertEqual(build.name_match_kind(t('K. Michael Moore'), t('Kevin Michael Moore')), 'first_initial')
        self.assertIsNone(build.name_match_kind(t('M. Casey Rodgers'), t('Margaret Catharine Rodgers')))
        self.assertIsNone(build.name_match_kind(t('Nicholas G. Garaufis'), t('Nicholas Garaufis')))
        self.assertIsNone(build.name_match_kind(t('William H. Orrick, III'), t('William Horsley Orrick Jr.'), 'iii', 'jr'))
        self.assertEqual(build.name_match_kind(t('William H. Orrick, III'), t('William Horsley Orrick III'), 'iii', 'iii'), 'middle_initial')
        self.assertEqual(build.name_match_kind(t('Andre Birotte, Jr'), t('André Birotte Jr.'), 'jr', 'jr'), 'exact')

    def test_norm_docket_and_title_prefix(self):
        self.assertEqual(build.norm_docket('3:16-md-02738-MAS-RLS'), '3:16-md-2738')
        self.assertEqual(build.norm_docket('3:16-md-2738'), '3:16-md-2738')
        self.assertEqual(build.strip_title_prefix('Terrorist Attacks on September 11, 2001 371 381', build.nospace('Terrorist Attacks on September 11, 2001')),
                         ('Terrorist Attacks on September 11, 2001', ' 371 381'))
        self.assertIsNone(build.strip_title_prefix('Other caption 1 2', 'Terrorist'))

    def test_totals_parser(self):
        tail = ('Report Totals: 166 206,182716,121 Total Number of MDL Dockets: 166 Total Number of Transferee Districts: 50 '
                'Total Number of Transferee Judges: 14215 Chief Judge, USDC45 Sr. District Judge82 U.S. District Judge '
                'Docket Count Range of the Number of Actions PENDING in a Docket Percent ofDocketsAction CountPercent ofActions'
                '40 MDLs with between 0 and 10 Pending Actions 24.1% 157 0.08%21 MDLs with 1,000 or more Pending Actions 12.65%194,835 94.5%'
                'Docket Count Range of the Number of T OT AL Actions in a Docket Percent ofDocketsAction CountPercent ofActions'
                '40 MDLs with 1,000 or more Actions 24.1% 703,917 98.3%')
        t = build.parse_totals(tail)
        self.assertEqual(t['report_totals']['actions_pending'], 206182)
        self.assertEqual(t['transferee_judges'], 142)
        self.assertEqual(t['judge_titles']['Chief Judge, USDC'], 15)
        self.assertEqual([b['actions'] for b in t['buckets_pending']], [157, 194835])
        self.assertEqual(t['buckets_total'][0]['actions'], 703917)


class OutputTests(unittest.TestCase):
    """Runs against the published files; skipped until build.py has produced them."""

    @classmethod
    def setUpClass(cls):
        if not (HERE / 'mdls.jsonl').is_file():
            raise unittest.SkipTest('build outputs not present')
        cls.rows = [json.loads(l) for l in (HERE / 'mdls.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        cls.validation = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        cls.qa = json.loads((HERE / 'qa.json').read_text(encoding='utf-8'))
        cls.docs = [json.loads(l) for l in (HERE / 'documents.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        cls.edges = [json.loads(l) for l in (HERE / 'edges.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]

    def test_envelope_hashes_match_files(self):
        v = self.validation
        self.assertEqual(v['schema_version'], '1')
        self.assertEqual(v['status'], 'passed')
        self.assertTrue(v['ready'])
        self.assertEqual({f['path'] for f in v['data_files']}, {'mdls.jsonl', 'documents.jsonl', 'court_map.json', 'edges.jsonl', 'unresolved.jsonl', 'qa.json'})
        for f in v['data_files']:
            self.assertEqual(hashlib.sha256((HERE / f['path']).read_bytes()).hexdigest(), f['sha256'], f['path'])
        for key in ('validated_at', 'counts', 'checks', 'qualification', 'license_ref', 'inputs'):
            self.assertIn(key, v)

    def test_row_counts_and_totals_reconcile(self):
        pending = [r for r in self.rows if r['status'] == 'pending']
        self.assertEqual(len(pending), 166)
        self.assertEqual(sum(1 for r in self.rows if r['status'] == 'terminated'), 10)
        self.assertEqual(sum(r['actions_pending'] for r in pending), 206182)
        self.assertEqual(sum(r['total_actions'] for r in pending), 716121)
        self.assertEqual(self.qa['status'], 'passed')
        self.assertTrue(all(r['litigation_type'] for r in pending))
        self.assertTrue(all(r['title'].startswith('IN RE: ') for r in self.rows))

    def test_temporal_block_and_labels(self):
        for r in self.rows:
            for f in ('captured_at', 'source_as_of', 'published_at', 'effective_from', 'effective_to'):
                self.assertIn(f, r['temporal'])
                self.assertIn(f + '_basis', r['temporal'])
            self.assertTrue(r['counts_label'].startswith('as listed in the JPML report dated 20'))
            self.assertIsNone(r['temporal']['published_at'])
            for s in r['snapshots']:
                self.assertEqual(s['counts_label'], 'as listed in the JPML report dated %s' % s['as_of'])

    def test_judge_links_and_edges_grammar(self):
        grammar = re.compile(r'^(mdl:\d+|cl_court:[a-z]+|judge_entity:judge-entity-[0-9a-f]+|cl_person:\d+|cl_docket:\d+|local_collection:[a-z0-9\-]+)$')
        for e in self.edges:
            self.assertRegex(e['from']['id'], grammar)
            self.assertRegex(e['to']['id'], grammar)
            self.assertIn(e['basis'], ('jpml_district_code_crosswalk', 'jpml_name_court', 'cl_person_native_bridge', 'cl_docket_search', 'cl_assigned_to_id', 'mdl_number_only'))
            self.assertTrue(e['evidence'])
        for r in self.rows:
            for jl in r['judge_links']:
                self.assertIn(jl['basis'], ('jpml_name_court', 'cl_person_native_bridge'))
                if jl['basis'] == 'jpml_name_court':
                    self.assertIn(jl['match_kind'], ('exact', 'middle_initial', 'middle_omitted', 'first_initial'))
                else:  # printed name differs from the FJC name: linked by native ids only, never by name
                    self.assertEqual(jl['match_kind'], 'cl_person_native_bridge')
                    self.assertTrue(str(jl['cl_person_id']).isdigit() and jl['fjc_jid'] and jl['fjc_nid'])
                    self.assertIs(jl['surname_agrees'], True)
                    self.assertRegex(jl['connector_file_sha256'], r'^[0-9a-f]{64}$')
                    self.assertTrue(jl['cl_docket_id'])
                    self.assertIn(jl['relation'], ('transferee_judge', 'sitting_by_designation_or_intercircuit_assignment'))
                    if not jl['court_agrees']:
                        self.assertEqual(jl['relation'], 'sitting_by_designation_or_intercircuit_assignment')
            if not r['judge_links']:
                self.assertTrue(r['unresolved'], r['mdl_number'])
            else:
                self.assertFalse(r['unresolved'], r['mdl_number'])
        linked = {e['from']['id'] for e in self.edges if e['basis'] in ('jpml_name_court', 'cl_person_native_bridge')}
        self.assertEqual(linked, {r['id'] for r in self.rows if r['judge_links']})

    def test_native_bridge_resolves_rodgers_and_leaves_no_native_id_cases_unresolved(self):
        by_num = {r['mdl_number']: r for r in self.rows}
        for num in (2885, 3140):
            jl = by_num[num]['judge_links'][0]
            self.assertEqual(by_num[num]['transferee_judge']['name_as_printed'], 'M. Casey Rodgers')
            self.assertEqual((jl['entity_id'], jl['cl_person_id'], jl['basis']), ('judge-entity-24222174cb13b7162bfcfcbc', 2755, 'cl_person_native_bridge'))
        for num in (2358, 2879, 2695, 3015):  # no CourtListener person id, a different assigned person, or no FJC bridge
            self.assertEqual(by_num[num]['judge_links'], [])
        unresolved = [json.loads(l) for l in (HERE / 'unresolved.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertNotIn('M. Casey Rodgers', {u['judge_name_as_printed'] for u in unresolved})
        jj = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))['counts']['judge_join']
        self.assertEqual(jj['resolved_keys'] + jj['unresolved_keys'], jj['distinct_judge_keys'])
        self.assertEqual(jj['unresolved_keys'], len(unresolved))
        self.assertEqual(jj['resolved_mdls'] + jj['unresolved_mdls'], len(self.rows))

    def test_documents_match_raw_bytes(self):
        self.assertEqual(len(self.docs), 16)
        for d in self.docs:
            data = (HERE / d['raw_path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), d['sha256'])
            self.assertEqual(len(data), d['bytes'])
            self.assertTrue(d['document_id'].startswith('jpmldoc-'))
        pdfs = [d for d in self.docs if d['kind'] == 'report_pdf']
        self.assertEqual(len(pdfs), 11)
        self.assertEqual(sum(1 for d in pdfs if d['report_date'] == '2026-09-01'), 5)

    def test_local_collection_and_connector(self):
        row = next(r for r in self.rows if r['mdl_number'] == 3080)
        self.assertEqual(row['local_collections'][0]['collection_id'], 'mdl-3080')
        self.assertEqual(row['local_collections'][0]['basis'], 'mdl_number_only')
        for r in self.rows:
            if r.get('cl_links'):
                self.assertIn('connector response', r['cl_links']['label'])
                self.assertTrue(r['cl_links']['connector_file_sha256'])


if __name__ == '__main__':
    unittest.main()
