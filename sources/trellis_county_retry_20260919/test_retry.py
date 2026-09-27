"""Offline checks for the retry batch. Run: python -m unittest discover -s <this dir> -p test_retry.py"""
import datetime as dt, hashlib, json, sys, unittest
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import collect_retry as cr

URL = 'https://trellis.law/coverage/oklahoma/major'
GOOD_HTML = ('<div class="top-county-info-block__container"><h1>Major County District Courts Records</h1>'
             '<h2>Population</h2><h3>7,527</h3><h2>County Seat</h2><h3>Fairview</h3>'
             '<h2>Website</h2><h3><a href="https://example.org/">example.org</a></h3></div>')
def response(status=200, html=GOOD_HTML, markdown='x' * 150, **meta):
    return {'html': html, 'markdown': markdown, 'metadata': {'statusCode': status, 'proxyUsed': 'basic', 'creditsUsed': 1, 'sourceURL': URL, 'url': URL, **meta}}
def readl(p): return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]


class Gate(unittest.TestCase):
    def test_accepts_substantive_profile(self):
        outcome, why, parsed = cr.inspect(response(), URL)
        self.assertEqual((outcome, why), ('accepted', None)); self.assertEqual(parsed['website_url'], 'https://example.org/')
    def test_rejects_404_thin_empty_login_redirect(self):
        self.assertEqual(cr.inspect(response(status=404, html='', markdown=''), URL)[1], 'provider_http_404')
        self.assertEqual(cr.inspect(response(markdown='# Major County District Courts Records'), URL)[1], 'incomplete_county_info_section')
        self.assertEqual(cr.inspect(response(html='', markdown=''), URL)[1], 'empty_body')
        self.assertEqual(cr.inspect(response(markdown='Please sign in to continue ' + 'x' * 120), URL)[1], 'login_shell')
        self.assertEqual(cr.inspect(response(url='https://trellis.law/login'), URL)[1], 'redirect_or_url_mismatch')
    def test_challenge_and_access_status_are_barriers(self):
        self.assertEqual(cr.inspect(response(markdown='Just a moment... ' + 'x' * 120), URL)[0], 'barrier')
        self.assertEqual(cr.inspect(response(status=403), URL)[0], 'barrier')
        self.assertEqual(cr.inspect(response(proxyUsed='stealth'), URL)[0], 'barrier')


class Frozen(unittest.TestCase):
    def test_seeds_are_exactly_the_prior_gaps_minus_artifacts(self):
        seeds = readl(HERE / 'seeds.jsonl'); gaps = readl(cr.PRIOR / 'unresolved_gaps.jsonl'); bad = {x['url'] for x in readl(cr.PRIOR / 'invalid_candidate_exclusions.jsonl')}
        self.assertEqual({s['url'] for s in seeds}, {g['url'] for g in gaps} - bad); self.assertEqual(len(seeds), 29)
        self.assertTrue(all(s['url'].startswith('https://trellis.law/coverage/') and s['url'].count('/') == 5 for s in seeds))
    def test_one_receipt_per_url_and_hashes_bind(self):
        path = HERE / 'receipts.jsonl'
        if not path.exists(): self.skipTest('network run not done yet')
        receipts = readl(path); seeds = {s['url'] for s in readl(HERE / 'seeds.jsonl')}
        self.assertEqual(len(receipts), len({r['url'] for r in receipts})); self.assertTrue({r['url'] for r in receipts} <= seeds)
        for r in receipts:
            if r.get('raw_sha256'): self.assertEqual(hashlib.sha256((ROOT / r['raw_path']).read_bytes()).hexdigest(), r['raw_sha256'])
            self.assertNotRegex(json.dumps(r), r'fc-[0-9a-fA-F]{20,}')
    def test_validation_envelope_binds_files(self):
        v = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        for key in ('schema_version', 'status', 'ready', 'validated_at', 'data_files', 'counts', 'checks', 'qualification', 'license_ref', 'inputs'): self.assertIn(key, v)
        if v['status'] != 'passed': self.skipTest('not finalized')
        for f in v['data_files']: self.assertEqual(hashlib.sha256((HERE / f['path']).read_bytes()).hexdigest(), f['sha256'], f['path'])
        self.assertLessEqual(v['counts']['credits_spent_account_delta'], 45); self.assertGreaterEqual(v['counts']['credits_at_end'], 500)
    def test_crosswalk_rows_match_inventory_exactly_once(self):
        path = HERE / 'county_name_crosswalk.json'
        if not path.exists(): self.skipTest('crosswalk not built yet')
        cw = json.loads(path.read_text(encoding='utf-8')); inv = readl(ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl')
        self.assertEqual(len(cw['resolved']) + len(cw['unresolved']), 11)
        for row in cw['resolved']:
            hits = [r for r in inv if r['usps'] == row['state'] and r['geoid'] == row['fips']]
            self.assertEqual(len(hits), 1); self.assertEqual(hits[0]['name'], row['census_county_name']); self.assertEqual(len(row['fips']), 5)
        for row in cw['unresolved']: self.assertIsNone(row['fips']); self.assertTrue(row['reason'])


MAJOR = 'https://trellis.law/coverage/oklahoma/major'
SEMINOLE = 'https://trellis.law/coverage/oklahoma/seminole'
LOWNDES = 'https://trellis.law/coverage/georgia/lowndes'
VA = 'https://trellis.law/coverage/virginia/'
SUPPLEMENT = 'sources/trellis_county_offline_supplement_20260919/resources.jsonl'
RUN_TIME = dt.datetime(2026, 9, 19, 12, 11, tzinfo=dt.timezone.utc)


class Guard(unittest.TestCase):
    """Repair regression: the retry must not spend on outcomes that local files already settle."""
    def gap(self, url, reason, done='2026-09-19T09:22:02+00:00'): return {'url': url, 'reason': reason, 'completed_at': done}
    def test_drops_urls_already_published_or_browser_checked(self):
        thin = 'incomplete_county_info_section'
        self.assertIn('already_published', cr.guard_refusal(self.gap(MAJOR, thin), '0', RUN_TIME, {MAJOR: SUPPLEMENT}, {}))
        self.assertIn('browser_check', cr.guard_refusal(self.gap(LOWNDES, thin), '0', RUN_TIME, {}, {LOWNDES: 'x.json'}))
        self.assertIsNone(cr.guard_refusal(self.gap(VA + 'somewhere', thin), '0', RUN_TIME, {MAJOR: SUPPLEMENT}, {LOWNDES: 'x.json'}))
    def test_refuses_prior_404_inside_max_age_window(self):
        g = self.gap(VA + 'norfolkcity', 'provider_http_404')
        self.assertIn('max_age', cr.guard_refusal(g, '172800000', RUN_TIME, {}, {}))
        self.assertIn('max_age', cr.guard_refusal({**g, 'completed_at': None}, '172800000', RUN_TIME, {}, {}))   # unknown age: refuse
        self.assertIsNone(cr.guard_refusal(g, '172800000', RUN_TIME + dt.timedelta(hours=49), {}, {}))
        self.assertIsNone(cr.guard_refusal(g, '0', RUN_TIME, {}, {}))
    def test_real_inputs_leave_nothing_to_send(self):
        seeds, excluded = cr.plan_seeds(RUN_TIME)
        self.assertEqual(seeds, []); self.assertEqual(len(excluded), 31)
        kinds = {}
        for x in excluded: kinds[x['guard']] = kinds.get(x['guard'], 0) + 1
        self.assertEqual(kinds, {'already_published_locally': 2, 'browser_check_already_settled': 1, 'prior_404_inside_max_age_window': 26, 'not_a_county_profile_url': 2})
    def test_prepare_never_overwrites_the_frozen_seed_history(self):
        before = hashlib.sha256((HERE / 'seeds.jsonl').read_bytes()).hexdigest()
        with self.assertRaises(SystemExit): cr.prepare()
        self.assertEqual(hashlib.sha256((HERE / 'seeds.jsonl').read_bytes()).hexdigest(), before)


class Reconciled(unittest.TestCase):
    """Repair regression: gaps/validation/README must agree with the already published coverage layer."""
    @classmethod
    def setUpClass(cls):
        cls.gaps = json.loads((HERE / 'gaps.json').read_text(encoding='utf-8')); cls.v = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        cls.progress = json.loads((ROOT / 'sources/trellis_coverage_20260919/progress.json').read_text(encoding='utf-8-sig'))
    def test_split_matches_coverage_layer(self):
        self.assertNotIn('still_failing_after_one_retry', self.gaps)
        genuine = self.gaps['genuine_gaps_after_retry']; done = self.gaps['already_recovered_elsewhere']
        self.assertEqual(len(genuine), 27); self.assertEqual({x['url'] for x in genuine}, set(self.progress['remaining_urls']))
        self.assertEqual(sorted(x['url'] for x in done), [MAJOR, SEMINOLE])
        for x in done:
            self.assertEqual(x['already_published_in']['path'], SUPPLEMENT); self.assertEqual(sorted(x['already_published_in']['fields']), ['county_seat', 'population'])
        self.assertTrue(all(x['already_published_in'] is None for x in genuine))
        lowndes = [x for x in genuine if x['url'] == LOWNDES][0]
        self.assertEqual(lowndes['browser_check']['path'], 'reports/trellis_refocus_20260919/lowndes_browser_check.json')
    def test_virginia_rows_carry_coverage_alternates(self):
        va = [x for x in self.gaps['genuine_gaps_after_retry'] if x['url'].startswith(VA)]; self.assertEqual(len(va), 26)
        self.assertEqual(sorted(x['url'] for x in va if not x['coverage_alternate_saved_urls']), [VA + 'richmondcity', VA + 'roanokecity'])
        by = {x['url']: x for x in va}
        self.assertEqual(by[VA + 'alexandriacity']['coverage_alternate_saved_urls'], [VA + 'alexandria'])
        self.assertEqual(by[VA + 'hopewellcity']['coverage_alternate_saved_urls'], [VA + 'hopewell']); self.assertEqual(by[VA + 'alexandriacity']['coverage_fips'], '51510')
    def test_counts_and_inputs(self):
        c = self.v['counts']; self.assertNotIn('still_failing', c)
        self.assertEqual((c['genuine_gaps_after_retry'], c['already_recovered_elsewhere'], c['retry_responses_rejected_by_gate']), (27, 2, 29))
        self.assertEqual((c['coverage_layer_alternate_profile_saved'], c['coverage_layer_no_alternate_profile']), (24, 2))
        self.assertEqual(c['outcomes_already_known_locally_before_retry'], 29)
        paths = {i['path'] for i in self.v['inputs']}
        for p in (SUPPLEMENT, 'sources/trellis_coverage_20260919/progress.json', 'reports/trellis_refocus_20260919/NEXT_RUN.md', 'reports/trellis_refocus_20260919/lowndes_browser_check.json'): self.assertIn(p, paths)
        for i in self.v['inputs']: self.assertEqual(hashlib.sha256((ROOT / i['path']).read_bytes()).hexdigest(), i['sha256'], i['path'])
        self.assertIn('should not be repeated', self.v['qualification'])
    def test_readme_discloses_and_has_no_regressive_steps(self):
        text = (HERE / 'README.md').read_text(encoding='utf-8')
        for needle in ('trellis_county_offline_supplement_20260919', 'NEXT_RUN.md', 'lowndes_browser_check.json', 'Do not run it again'): self.assertIn(needle, text)
        for banned in ('replace or extend the 29', 'thin profile', '29 still failing'): self.assertNotIn(banned, text)


if __name__ == '__main__': unittest.main()
