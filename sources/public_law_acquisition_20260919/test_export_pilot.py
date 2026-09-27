from pathlib import Path
import tempfile
import unittest
from export_pilot import check_capture, sha
from pilot_reader import page_reading


class CaptureReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'raw.html').write_bytes(b'<main>Exact original court page</main>')
        (self.root / 'text.txt').write_bytes(b'Exact original court page')
        self.seed = {'url': 'https://court.example/rules'}
        self.row = {'url': self.seed['url'], 'status': 'downloaded', 'raw_path': 'raw.html', 'text_path': 'text.txt', 'sha256': sha((self.root / 'raw.html').read_bytes())}
        self.receipt = {'requested_url': self.seed['url'], 'status': 'downloaded', 'http_status': 200, 'raw_complete': True, 'sha256': self.row['sha256'], 'byte_count': (self.root / 'raw.html').stat().st_size, 'text_sha256': sha((self.root / 'text.txt').read_bytes())}

    def tearDown(self):
        self.tmp.cleanup()

    def check(self):
        return check_capture(self.seed, self.row, self.receipt, self.root)

    def test_valid_hash_bound_source(self):
        self.assertEqual(self.check()[3], b'Exact original court page')

    def test_wrong_requested_source_cannot_attach(self):
        self.receipt['requested_url'] = 'https://court.example/other'
        with self.assertRaises(ValueError): self.check()

    def test_http200_soft404_rejected(self):
        self.receipt['title'] = '404 Page Not Found - United States Court of Appeals'
        with self.assertRaisesRegex(ValueError, 'Source error page'): self.check()

    def test_partial_or_error_page_cannot_publish(self):
        for key, value in [('raw_complete', False), ('status', 'challenge'), ('http_status', 302)]:
            old = self.receipt[key]; self.receipt[key] = value
            with self.assertRaises(ValueError): self.check()
            self.receipt[key] = old

    def test_modified_original_rejected(self):
        (self.root / 'raw.html').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.check()

    def test_modified_extraction_rejected(self):
        (self.root / 'text.txt').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.check()

    def test_artifact_escape_rejected(self):
        self.row['raw_path'] = '../unrelated.txt'
        with self.assertRaises(ValueError): self.check()

    def test_reader_drops_chrome_keeps_main_links_and_contextual_sidebar(self):
        raw = b'<html><body><header>Search form Text Size</header><nav>Unrelated menus</nav><aside><nav id="block-menu-block-us-courts-menu-blocks-side-nav"><a href="/judge-one">Judge One</a></nav></aside><div id="main-content-wrapper"><h1>Judges</h1><div class="breadcrumb">You are here</div><p>Biographical links to the left.</p></div></body></html>'
        out = page_reading(raw, self.seed['url'], 'Judges')
        self.assertNotIn('Search form', out['text'])
        self.assertNotIn('You are here', out['text'])
        self.assertNotIn('Unrelated menus', out['text'])
        self.assertIn('[Judge One](https://court.example/judge-one)', out['text'])

    def test_reader_preserves_published_effective_dates_and_document_link(self):
        raw = b'<main><h1>Rules</h1><p>Effective December 1, 2025.</p><a href="/rules.pdf">Rulebook</a></main>'
        out = page_reading(raw, self.seed['url'], 'Rules')
        self.assertIn('December 1, 2025', out['text'])
        self.assertIn('[Rulebook](https://court.example/rules.pdf)', out['text'])


if __name__ == '__main__':
    unittest.main()
