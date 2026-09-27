import hashlib
import json
import unittest
from pathlib import Path

from pilot_reader import page_reading

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


class SectionNavigationTests(unittest.TestCase):
    def test_reviewed_ca11_section_keeps_judge_links(self):
        rows = [json.loads(l) for l in (HERE/'resources.jsonl').read_text(encoding='utf-8').splitlines() if l]
        row = next(r for r in rows if r['source_url']=='https://www.ca11.uscourts.gov/judges')
        out = page_reading((ROOT/row['raw_path']).read_bytes(), row['source_url'], row['title'])
        urls = {r['url'] for r in out['links']}
        self.assertIn('https://www.ca11.uscourts.gov/judges/hon-william-h-pryor-jr', urls)
        self.assertIn('https://www.ca11.uscourts.gov/hon-embry-j-kidd', urls)
        self.assertEqual(len(urls), 28)
        self.assertFalse(out['notes']['limited_text'])

    def test_nested_sidebar_survives_cleanup(self):
        raw = b'<div id="main-content-wrapper"><aside><nav id="block-menu-block-us-courts-menu-blocks-side-nav"><a href="/judge-one">Judge One</a><a href="/judge-one">Duplicate</a></nav></aside><h1>Judges</h1><p>See links to the left.</p></div>'
        out = page_reading(raw, 'https://court.example/judges', 'Judges')
        self.assertIn('[Judge One](https://court.example/judge-one)', out['text'])
        self.assertEqual(len(out['links']), 1)
        self.assertFalse(out['notes']['limited_text'])
    def test_exact_current_branch_excludes_unrelated_siblings(self):
        raw = b'<main><h1>Forms</h1><aside><nav id="block-menu-block-us-courts-menu-blocks-side-nav"><ul><li><a href="/forms">Forms</a><ul><li><a href="/appeal.pdf">Notice of Appeal</a></li></ul></li><li><a href="/task-force">Task Force</a></li></ul></nav></aside><p>Use left side menu.</p></main>'
        out = page_reading(raw, 'https://court.example/forms', 'Forms')
        self.assertEqual([r['url'] for r in out['links']], ['https://court.example/appeal.pdf'])
        self.assertNotIn('Task Force', out['text'])
    def test_missing_sidebar_explicit_limited_note(self):
        out = page_reading(b'<main><h1>Judges</h1><p>See links to the left.</p></main>', 'https://court.example/judges', 'Judges')
        self.assertTrue(out['notes']['limited_text'])
        self.assertIn('Reading note:', out['text'])
        self.assertEqual(out['links'], [])
    def test_general_sidenav_not_mistaken_for_section(self):
        raw = b'<main><h1>Forms</h1><ul class="usa-sidenav"><li><a href="/careers">Careers</a></li></ul><p>See links to the left.</p></main>'
        # Main-list content remains source text; it is not copied again as supplemental navigation.
        out = page_reading(raw, 'https://court.example/forms', 'Forms')
        self.assertEqual(out['notes']['section_navigation_links_preserved'], 0)
    def test_six_saved_pages_recover_observed_links_only(self):
        targets = {
            'https://www.ca1.uscourts.gov/judges': '/david-j-barron',
            'https://www.ca1.uscourts.gov/rules-procedures': '/sites/ca1/files/rulebook.pdf#page=10',
            'https://www.ca3.uscourts.gov/allforms': '/attorney-discipline-forms',
            'https://www.ca8.uscourts.gov/judges': '/active-and-senior-judges',
            'https://www.cadc.uscourts.gov/forms': '/forms-subject',
            'https://www.cadc.uscourts.gov/judges': '/bios'}
        rows = [json.loads(l) for l in (HERE/'resources.jsonl').read_text(encoding='utf-8').splitlines() if l]
        for row in rows:
            if row['source_url'] not in targets: continue
            raw = (ROOT/row['raw_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row['raw_sha256'])
            out = page_reading(raw, row['source_url'], row['title'])
            with self.subTest(url=row['source_url']):
                self.assertTrue(any(r['url'].endswith(targets[row['source_url']]) for r in out['links']))
                self.assertEqual(len(out['links']), len({r['url'] for r in out['links']}))
                self.assertGreater(out['notes']['section_navigation_links_preserved'], 0)
                self.assertFalse(out['notes']['limited_text'])
                if '/allforms' in row['source_url']: self.assertNotIn('Task Force', out['text'])
                if '/rules-procedures' in row['source_url']: self.assertIn('December 1, 2016', out['text'])
        self.assertEqual(sum(row['source_url'] in targets for row in rows), 6)

if __name__ == '__main__': unittest.main()
