"""Offline tests: policy functions, frozen seeds, saved artifacts, and the source_captures batch gate."""
import hashlib, json, shutil, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'delivery/archive-directory')]
import gapfill
import source_captures


def rows(name): return [json.loads(x) for x in (HERE / name).read_text(encoding='utf-8').splitlines() if x.strip()]
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Policy(unittest.TestCase):
    def test_soft_404_challenge_login_and_empty_are_rejected(self):
        body = 'x ' * 300
        self.assertEqual(gapfill.reject_reason('Court Rules', 'short'), 'empty_or_insufficient_text')
        self.assertEqual(gapfill.reject_reason('404 - File not found', body), 'error_title_or_heading')
        self.assertEqual(gapfill.reject_reason('Courts', '# Sorry\n\nThe page you requested is gone. ' + body), 'soft_404_or_error_text')
        self.assertEqual(gapfill.reject_reason('Courts', 'Please verify you are a human to continue. ' + body), 'challenge_shell')
        self.assertEqual(gapfill.reject_reason('Sign in - Portal', body), 'login_shell')
        self.assertEqual(gapfill.reject_reason('Codified Law', '# Codified Laws Your browser is not supported Please update your browser. ' + 'x ' * 200), 'script_shell_not_rendered')

    def test_known_shells_and_redirect_aliases_are_gaps_not_resources(self):
        saved = {r['source_url'] for r in rows('resources.jsonl')}
        for url in ('https://sdlegislature.gov/Statutes/15', 'https://sdlegislature.gov/Statutes/21', 'https://www.nysenate.gov/legislation/laws/all',
                    'https://www.courtswv.gov/lower-courts/mass-litigation-panel/supreme-court-orders'):
            self.assertNotIn(url, saved)
        texts = [r['text_sha256'] for r in rows('resources.jsonl') if r['text_sha256']]
        self.assertEqual(len(texts), len(set(texts)))

    def test_real_rule_text_mentioning_404_is_kept(self):
        text = '# Illinois Rules of Evidence\n\nRule 404. Character Evidence Not Admissible to Prove Conduct; Exceptions; Other Crimes. ' + 'text ' * 100
        self.assertIsNone(gapfill.reject_reason('Illinois Rules of Evidence', text))

    def test_barriers_are_skipped_not_rerouted(self):
        row = {'host': 'ww2.nycourts.gov', 'directory_verification': {'http_status': '200'}}
        self.assertEqual(gapfill.barrier_decision(row)[0], 'skip_barrier')
        row = {'host': 'www.example-court.gov', 'directory_verification': {'http_status': '403'}}
        self.assertEqual(gapfill.barrier_decision(row)[0], 'skip_barrier')
        row = {'host': 'www.lexisnexis.com', 'directory_verification': {'http_status': '200'}}
        self.assertEqual(gapfill.barrier_decision(row)[0], 'skip_terms_review')
        row = {'host': 'www.courtswv.gov', 'directory_verification': {'http_status': None}}
        self.assertEqual(gapfill.barrier_decision(row), (None, None))
        self.assertFalse(gapfill.host_in('notmass.gov', gapfill.BARRIER_HOSTS))

    def test_loose_url_identity(self):
        self.assertEqual(gapfill.loose('https://www.Courts.CA.gov/x/'), gapfill.loose('http://courts.ca.gov/x'))
        self.assertNotEqual(gapfill.loose('https://a.gov/x?id=1'), gapfill.loose('https://a.gov/x?id=2'))


class Data(unittest.TestCase):
    def test_seeds_are_frozen_and_classified(self):
        seeds = gapfill.frozen_seeds()
        self.assertEqual(len(seeds), 99)
        self.assertEqual(len({s['url'] for s in seeds}), 99)
        self.assertTrue(all(s['state'] and s['gap_type'] and s['family'] and s['decision'] for s in seeds))
        self.assertFalse([s for s in seeds if s['decision'].startswith('fetch') and gapfill.host_in(s['host'], gapfill.BARRIER_HOSTS)])

    def test_resources_bind_to_seeds_and_files_verify(self):
        seeds = {s['url']: s for s in gapfill.frozen_seeds()}
        resources = rows('resources.jsonl')
        self.assertTrue(resources)
        for item in resources:
            seed = seeds[item['source_url']]
            self.assertTrue(seed['decision'].startswith('fetch'))
            self.assertTrue(item['id'].startswith('gapfill:'))
            self.assertEqual((item['state'], item['gap_type'], item['family']), (seed['state'], seed['gap_type'], seed['family']))
            self.assertIn(item['capture_kind'], ('provider_capture_not_original_http_bytes', 'official_http_original'))
            self.assertEqual(item['original_http_bytes'], item['capture_kind'] == 'official_http_original')
            self.assertEqual(digest(ROOT / item['raw_path']), item['raw_sha256'])
            self.assertTrue((ROOT / item['raw_path']).resolve().is_relative_to(HERE))
            if item['text_path']: self.assertEqual(digest(ROOT / item['text_path']), item['text_sha256'])
            self.assertIsNone(item['source_as_of']); self.assertIsNone(item['temporal']['effective_from'])

    def test_every_candidate_is_a_resource_or_a_gap(self):
        gaps = json.loads((HERE / 'gaps.json').read_text(encoding='utf-8'))['gaps']
        urls = [r['source_url'] for r in rows('resources.jsonl')] + [g['url'] for g in gaps]
        self.assertEqual(sorted(urls), sorted(s['url'] for s in gapfill.frozen_seeds()))

    def test_no_credential_material_in_provider_receipts(self):
        for file in (HERE / '.firecrawl').glob('*.json'):
            raw = file.read_bytes()
            self.assertNotIn(b'Bearer ', raw); self.assertNotRegex(raw.decode('utf-8', 'replace'), r'\bfc-[A-Za-z0-9]{16,}')


class Adapter(unittest.TestCase):
    def test_batch_passes_both_gates_and_attaches_by_exact_url(self):
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        self.assertEqual(gate['status'], 'passed'); self.assertIs(gate['ready'], True)
        self.assertEqual(gate['resources_sha256'], digest(HERE / 'resources.jsonl'))
        records = source_captures.load(HERE)
        self.assertEqual(len(records), len(rows('resources.jsonl')))
        status = {b['name']: b for b in source_captures.batches()}
        self.assertEqual(status[HERE.name]['status'], 'loaded')
        first = rows('resources.jsonl')[0]
        attached = [a for a in source_captures.attachments(first['source_url']) if a['id'] == first['id']]
        self.assertEqual(len(attached), 1)
        self.assertEqual(attached[0]['state'], first['state']); self.assertNotIn('raw_path', attached[0])

    def test_gate_fails_closed_when_resources_are_altered(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ('validation.json', 'seeds.jsonl', 'gaps.json'): shutil.copy(HERE / name, Path(temp) / name)
            (Path(temp) / 'resources.jsonl').write_bytes((HERE / 'resources.jsonl').read_bytes() + b'\n{"id":"gapfill:x","source_url":"https://example.gov/"}\n')
            self.assertEqual(source_captures._read_batch(Path(temp)), ({}, 'validation_gate_failed'))


if __name__ == '__main__':
    unittest.main()
