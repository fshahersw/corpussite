"""Tests for the jurisdiction coverage adapter (written before the adapter; fixture data only)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import jurisdiction_coverage as jc

TEMPORAL = {k: None for k in (
    'captured_at', 'captured_at_basis', 'source_as_of', 'source_as_of_basis', 'published_at', 'published_at_basis',
    'effective_from', 'effective_from_basis', 'effective_to', 'effective_to_basis')}


def _coverage():
    def fam(official=0, imported=0, third=0):
        return {'official_capture': {'total': official, 'body': official, 'unreviewed': 0, 'navigation': 0},
                'imported_collection': imported, 'third_party_snapshot': third, 'pending_publication': 0}
    def state(name, abbr, kind, statutes, gaps):
        return {'name': name, 'abbr': abbr, 'jurisdiction_kind': kind,
                'families': {'statutes': statutes, 'constitution': fam(), 'regulations': fam(), 'court_rules': fam(0, 0, 5)},
                'reviewed_tier': {'statute_body': 2, 'hub_or_index': 1}, 'gaps': gaps,
                'county_layer': {'counties_total': 3}, 'trellis_county_profiles': {'observed': 3, 'saved': 1, 'unsaved': 2}}
    return {
        'schema_version': '1', 'generated_at': '2026-09-19T00:00:00+00:00',
        'qualification': 'Counts are saved records or rows, never unique laws.',
        'definitions': {'official_capture': 'saved official pages'},
        'jurisdictions': {
            'GA': state('Georgia', 'GA', 'state', fam(0, 0, 0), ['statutes_none_any_source']),
            'PA': state('Pennsylvania', 'PA', 'state', fam(375, 0, 14571), ['regulations_none_any_source']),
            'NJ': state('New Jersey', 'NJ', 'state', fam(0, 5856, 55993), []),
            'GU': state('Guam', 'GU', 'territory', fam(0, 26, 0), []),
        },
        'gaps': {'statutes_none_any_source': ['GA'], 'regulations_none_any_source': ['PA', 'GA'], 'court_rules_none_any_source': ['MO', 'OK']},
        'venues': [{'fips': '42101', 'venue': 'Philadelphia County, PA', 'state': 'PA', 'tier': 'primary',
                    'trellis_profile_status': 'observed_not_saved', 'county_linked_total': 0}],
        'trellis_unsaved_profiles': {'total': 2, 'by_state': {'PA': {'count': 2, 'urls': ['https://trellis.law/coverage/pennsylvania/york', 'https://trellis.law/coverage/pennsylvania/philadelphia']}}},
        'totals': {'jurisdictions': 4},
    }


def _topics():
    rows = []
    for i in range(7):
        rows.append({'provision_id': 'open_us_law_row:oul:%064d' % i, 'source_dataset': 'open_us_law', 'source_tier': 'third_party_snapshot',
                     'row_id': 'oul:%064d' % i, 'state': 'PA', 'family': 'statutes', 'citation': '42 Pa.C.S. § 55%02d' % i,
                     'title': 'Limitation %d' % i, 'source_url': None, 'publisher_status': 'in_force',
                     'topics': [{'topic': 'sol', 'match': 'title' if i < 2 else 'text', 'query_id': 'sol_any'}], 'temporal': dict(TEMPORAL)})
    rows.append({'provision_id': 'record:abc123', 'source_dataset': 'directory_imported', 'source_tier': 'imported_collection',
                 'record_id': 'abc123', 'state': 'NJ', 'family': 'statutes', 'citation': '2A:14-2', 'title': 'Actions for injuries',
                 'source_url': 'https://pub.njleg.state.nj.us/Statutes/STATUTES-TEXT.zip', 'publisher_status': None,
                 'topics': [{'topic': 'sol', 'match': 'text', 'query_id': 'sol_any'}, {'topic': 'product_liability', 'match': 'text', 'query_id': 'product_liability'}],
                 'temporal': dict(TEMPORAL)})
    return rows


def _labels():
    return [
        {'record_id': 'r1', 'state': 'PA', 'title': 'Title 42', 'source_url': 'https://www.palegis.us/x', 'categories': ['statutes'],
         'law_body_class': 'statute_body', 'class_basis': 'modal density', 'confidence': 'high', 'rule_set': None, 'rule_set_basis': None,
         'features': {'text_chars': 50000}, 'legal_currency_asserted': False, 'temporal': dict(TEMPORAL)},
        {'record_id': 'r2', 'state': 'PA', 'title': 'Rules of Evidence', 'source_url': 'https://www.pacourts.us/x', 'categories': ['court_rules'],
         'law_body_class': 'hub_or_index', 'class_basis': 'link ratio', 'confidence': 'medium', 'rule_set': 'evidence', 'rule_set_basis': 'title',
         'features': {'text_chars': 900}, 'legal_currency_asserted': False, 'temporal': dict(TEMPORAL)},
    ]


def _write(folder, status='passed', ready=True, tamper=None):
    folder = Path(folder)
    files = {
        'coverage.json': json.dumps(_coverage()).encode(),
        'topic_index.jsonl': ''.join(json.dumps(r) + '\n' for r in _topics()).encode(),
        'record_labels.jsonl': ''.join(json.dumps(r) + '\n' for r in _labels()).encode(),
        'oul_rule_sets.jsonl': (json.dumps({'id': 'oul:' + '1' * 64, 'state': 'PA', 'rule_set': 'evidence', 'detail': None, 'basis': 'chapter_name'}) + '\n').encode(),
        'topics.json': json.dumps({'label': 'search-derived candidates, not legal advice or a complete survey',
                                   'topics': {'sol': {'label': 'Statute of limitations', 'group': 'statute_of_limitations', 'queries': {'sol_any': '"statute of limitations"'}},
                                              'product_liability': {'label': 'Product liability', 'group': 'product_liability', 'queries': {'product_liability': '"product liability"'}}}}).encode(),
    }
    data_files = []
    for name, payload in files.items():
        (folder / name).write_bytes(payload)
        data_files.append({'path': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'rows': payload.count(b'\n') or 1})
    if tamper:
        (folder / tamper).write_bytes(files[tamper] + b' ')
    gate = {'schema_version': '1', 'status': status, 'ready': ready, 'validated_at': '2026-09-19T00:00:00+00:00',
            'data_files': data_files, 'counts': {}, 'checks': [], 'qualification': 'fixture', 'license_ref': '', 'inputs': []}
    (folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
    return folder


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


class GateTests(unittest.TestCase):
    def test_fails_closed_when_gate_not_passed_or_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(jc.load(_write(tmp, status='failed')))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(jc.load(_write(tmp, ready=False)))

    def test_fails_closed_on_any_altered_data_file(self):
        for name in ('coverage.json', 'topic_index.jsonl', 'record_labels.jsonl', 'oul_rule_sets.jsonl', 'topics.json'):
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(jc.load(_write(tmp, tamper=name)), name)

    def test_fails_closed_on_missing_folder_or_unsafe_gate_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(jc.load(Path(tmp) / 'absent'))
            folder = _write(tmp)
            gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
            gate['data_files'][0]['path'] = '../coverage.json'
            (folder / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
            self.assertIsNone(jc.load(folder))

    def test_unavailable_adapter_returns_closed_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write(tmp, status='failed')
            self.assertEqual(jc.matrix(folder)['available'], False)
            self.assertIsNone(jc.state_detail('PA', folder))
            self.assertEqual(jc.topics(folder=folder)['items'], [])
            self.assertEqual(jc.venues(folder)['items'], [])


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = _write(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_matrix_lists_every_jurisdiction_with_tiers_and_gaps(self):
        out = jc.matrix(self.folder)
        self.assertTrue(out['available'])
        self.assertEqual([r['abbr'] for r in out['rows']], ['GA', 'NJ', 'PA', 'GU'])  # states first (alphabetical by name), territories last
        pa = next(r for r in out['rows'] if r['abbr'] == 'PA')
        self.assertEqual(pa['families']['statutes']['official_capture']['total'], 375)
        self.assertEqual(pa['families']['statutes']['third_party_snapshot'], 14571)
        self.assertEqual(out['gaps']['court_rules_none_any_source'], ['MO', 'OK'])
        self.assertIn('never unique laws', out['qualification'])

    def test_state_detail_accepts_abbreviation_or_name_and_adds_derived_counts(self):
        by_abbr = jc.state_detail('pa', self.folder)
        by_name = jc.state_detail('Pennsylvania', self.folder)
        self.assertEqual(by_abbr, by_name)
        self.assertEqual(by_abbr['abbr'], 'PA')
        self.assertEqual(by_abbr['topic_counts'], {'sol': 7})
        self.assertEqual(by_abbr['reviewed_labels']['by_class'], {'hub_or_index': 1, 'statute_body': 1})
        self.assertEqual(by_abbr['reviewed_labels']['by_rule_set'], {'evidence': 1})
        self.assertEqual(by_abbr['open_us_law_rule_sets'], {'evidence': 1})
        self.assertEqual(by_abbr['trellis_unsaved_profiles']['count'], 2)
        self.assertEqual([v['fips'] for v in by_abbr['venues']], ['42101'])
        self.assertIsNone(jc.state_detail('Atlantis', self.folder))
        self.assertIsNone(jc.state_detail('../PA', self.folder))

    def test_topics_filter_paginate_and_order_title_matches_first(self):
        out = jc.topics(state='PA', topic='sol', page=1, limit=3, folder=self.folder)
        self.assertEqual(out['total'], 7)
        self.assertEqual(out['pages'], 3)
        self.assertEqual(len(out['items']), 3)
        self.assertEqual([i['match'] for i in out['items']], ['title', 'title', 'text'])
        self.assertEqual(out['label'], 'search-derived candidates, not legal advice or a complete survey')
        last = jc.topics(state='PA', topic='sol', page=3, limit=3, folder=self.folder)
        self.assertEqual(len(last['items']), 1)
        self.assertEqual(jc.topics(topic='product_liability', folder=self.folder)['total'], 1)
        self.assertEqual(jc.topics(folder=self.folder)['total'], 8)
        self.assertEqual(jc.topics(state='NJ', folder=self.folder)['items'][0]['topics'], ['sol', 'product_liability'])

    def test_topics_rejects_bad_parameters_without_raising(self):
        self.assertEqual(jc.topics(topic='nope', folder=self.folder)['total'], 0)
        self.assertEqual(jc.topics(state='ZZ', folder=self.folder)['total'], 0)
        out = jc.topics(page='x', limit=100000, folder=self.folder)
        self.assertEqual(out['page'], 1)
        self.assertEqual(out['limit'], jc.MAX_LIMIT)
        self.assertEqual(jc.topics(page=-4, limit=0, folder=self.folder)['limit'], 1)

    def test_topics_summary_lists_each_topic_with_query_provenance(self):
        out = jc.topics(folder=self.folder)
        self.assertEqual(out['topic_catalog']['sol']['queries'], {'sol_any': '"statute of limitations"'})
        self.assertEqual(out['topic_catalog']['sol']['provisions'], 8)

    def test_venues_returns_the_venue_rows(self):
        out = jc.venues(self.folder)
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['items'][0]['trellis_profile_status'], 'observed_not_saved')

    def test_labels_filter_and_rule_set_lookup(self):
        out = jc.labels(state='PA', law_body_class='statute_body', folder=self.folder)
        self.assertEqual([i['record_id'] for i in out['items']], ['r1'])
        self.assertFalse(out['items'][0]['legal_currency_asserted'])
        self.assertEqual(jc.labels(rule_set='evidence', folder=self.folder)['total'], 1)
        self.assertEqual(jc.rule_set('oul:' + '1' * 64, self.folder)['rule_set'], 'evidence')
        self.assertIsNone(jc.rule_set('oul:' + '2' * 64, self.folder))
        self.assertIsNone(jc.rule_set(None, self.folder))

    def test_public_dicts_are_path_free(self):
        blobs = [jc.matrix(self.folder), jc.state_detail('PA', self.folder), jc.topics(folder=self.folder),
                 jc.venues(self.folder), jc.labels(folder=self.folder)]
        for blob in blobs:
            for key, value in _walk(blob):
                self.assertNotIn('path', str(key).lower())
                if isinstance(value, str):
                    self.assertNotRegex(value, r'^[A-Za-z]:[\\/]')
                    self.assertNotIn('\\', value)

    def test_results_are_copies(self):
        jc.matrix(self.folder)['rows'][0]['families']['statutes']['third_party_snapshot'] = -1
        self.assertNotEqual(jc.matrix(self.folder)['rows'][0]['families']['statutes']['third_party_snapshot'], -1)


class RefreshedProfileTests(unittest.TestCase):
    def test_completed_backfill_clears_stale_gap_without_changing_law_snapshot(self):
        row={'gaps':['court_rules_none_any_source','trellis_county_profiles_partial'],
             'families':{'court_rules':{'official_capture':{'total':0}}},
             'county_layer':{'counties_total':3,'with_saved_trellis_profile':1}}
        before=json.dumps(row,sort_keys=True)
        progress={'observed_county_urls':3,'saved_county_urls':3,'remaining_county_urls':0,
                  'remaining_urls':[],'unique_resolved_fips':2}
        out=jc._refresh_trellis(row,progress)
        self.assertEqual(out['gaps'],['court_rules_none_any_source'])
        self.assertEqual(out['families'],row['families'])
        self.assertEqual(out['county_layer']['with_saved_trellis_profile'],2)
        self.assertEqual(out['trellis_county_profiles']['saved'],3)
        self.assertEqual(json.dumps(row,sort_keys=True),before)

    def test_remaining_url_keeps_gap_even_when_other_profiles_are_saved(self):
        progress={'observed_county_urls':3,'saved_county_urls':2,'remaining_county_urls':1,
                  'remaining_urls':['https://trellis.law/coverage/virginia/roanokecity'],'unique_resolved_fips':2}
        out=jc._refresh_trellis({'gaps':['county_local_rules_none']},progress)
        self.assertIn('trellis_county_profiles_partial',out['gaps'])
        self.assertEqual(out['trellis_unsaved_profiles']['urls'],progress['remaining_urls'])


class BuiltDataTests(unittest.TestCase):
    """Runs against the real supplement when it has been built; skipped otherwise."""

    def setUp(self):
        if jc.load() is None:
            self.skipTest('law_tier_20260919 has not been built or its gate is closed')

    def test_real_matrix_has_51_jurisdictions_plus_territories_and_named_gaps(self):
        out = jc.matrix()
        states = [r for r in out['rows'] if r['jurisdiction_kind'] != 'territory']
        self.assertEqual(len(states), 51)
        self.assertIn('GA', out['gaps']['statutes_none_any_source'])
        self.assertEqual(len(out['gaps']['regulations_none_any_source']), 34)
        self.assertEqual(out['gaps']['court_rules_none_any_source'], ['MO', 'OK'])

    def test_real_venues_and_topics(self):
        self.assertEqual(jc.venues()['total'], 28)
        out = jc.topics(state='PA', topic='sol', limit=5)
        self.assertGreater(out['total'], 0)
        self.assertTrue(all(i['state'] == 'PA' for i in out['items']))
        # Open US Law withdrew Georgia statutes only; its Georgia court rules remain and may match. No statute row may appear.
        georgia = jc.topics(state='GA', topic='sol', limit=jc.MAX_LIMIT)
        self.assertEqual([i for i in georgia['items'] if i['source_tier'] == 'third_party_snapshot' and i['family'] == 'statutes'], [])
        self.assertTrue(all(i['family'] != 'statutes' or i['source_tier'] != 'third_party_snapshot' for i in georgia['items']))


if __name__ == '__main__':
    unittest.main()
