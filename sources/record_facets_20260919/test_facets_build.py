"""Invariants of the built sidecar (facets.sqlite3 + validation.json). Skipped when the build has not run yet."""
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / 'facets.sqlite3'
GATE = HERE / 'validation.json'
CATEGORIES = {'statutes', 'rules', 'constitutions', 'regulations', 'forms', 'guidance', 'directories', 'judges', 'other'}
VALIDITY = {'ok', 'parked_redirect', 'redirect_stub', 'spa_shell', 'error_page', 'challenge', 'empty', 'no_capture'}
LANDER = '6dc9c7fc93bb488bb0520a6c780a8d3c0fb5486a4711aca49b4c53fac7393023'


def built():
    return DB.is_file() and GATE.is_file()


@unittest.skipUnless(built(), 'facets.sqlite3 not built yet')
class BuildOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = json.loads(GATE.read_text(encoding='utf-8'))
        cls.con = sqlite3.connect('file:' + DB.as_posix() + '?mode=ro', uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def one(self, sql, *args):
        return self.con.execute(sql, args).fetchone()[0]

    def test_gate_envelope_and_hash(self):
        gate = self.gate
        self.assertEqual(gate['schema_version'], '1')
        self.assertEqual(gate['status'], 'passed')
        self.assertIs(gate['ready'], True)
        pins = [x for x in gate['data_files'] if x['path'] == 'facets.sqlite3']
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]['sha256'], hashlib.sha256(DB.read_bytes()).hexdigest())
        self.assertEqual(pins[0]['rows'], self.one('SELECT count(*) FROM facets'))
        self.assertTrue(all(c['passed'] for c in gate['checks']))
        self.assertTrue(any(i['path'].endswith('build_receipt.json') and i['sha256'] for i in gate['inputs']))

    def test_one_row_per_directory_record(self):
        meta = dict(self.con.execute('SELECT key, value FROM meta'))
        self.assertEqual(int(meta['records']), 58310)
        self.assertEqual(int(meta['directory_records']), 58310)
        self.assertEqual(self.one('SELECT count(*) FROM facets'), 58310)
        self.assertEqual(self.one('SELECT count(DISTINCT record_id) FROM facets'), 58310)

    def test_review_states_are_not_categories(self):
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE derived_category IN ('needs_content_review','law_document_title_evidence_needs_review')"), 0)
        self.assertEqual(self.one("SELECT count(DISTINCT review_state) FROM facets WHERE original_kind='needs_content_review'"), 1)
        self.assertEqual(self.one("SELECT review_state FROM facets WHERE original_kind='needs_content_review' LIMIT 1"), 'needs_content_review')
        self.assertGreater(self.one("SELECT count(*) FROM facets WHERE original_kind='needs_content_review' AND derived_category='statutes'"), 4500)
        self.assertGreater(self.one("SELECT count(*) FROM facets WHERE original_kind IN ('needs_content_review','law_document_title_evidence_needs_review') AND derived_category IN ('statutes','constitutions','rules','regulations')"), 9500)

    def test_judges_and_trellis_and_seeger_rules(self):
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE dataset LIKE 'judge%' AND derived_category!='judges'"), 0)
        self.assertGreater(self.one("SELECT count(*) FROM facets WHERE category_basis LIKE '%Trellis state-rules%' AND derived_category='constitutions'"), 500)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE dataset='seeger' AND original_kind='court_form_or_other_document' AND doc_subtype IS NULL"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE dataset='seeger' AND original_kind='court_form_or_other_document' AND derived_category NOT IN ('forms','rules')"), 0)

    def test_every_value_has_a_basis_and_allowlists(self):
        for value, basis in (('derived_category', 'category_basis'), ('review_state', 'review_basis'), ('doc_subtype', 'subtype_basis'),
                             ('file_type', 'file_type_basis'), ('jurisdiction_level', 'jurisdiction_basis'), ('saved_at', 'saved_at_basis'),
                             ('source_as_of', 'source_as_of_basis'), ('effective_from', 'effective_from_basis'), ('display_title', 'title_basis')):
            self.assertEqual(self.one(f"SELECT count(*) FROM facets WHERE {value} IS NOT NULL AND ({basis} IS NULL OR {basis}='')"), 0, value)
        self.assertEqual({r[0] for r in self.con.execute('SELECT DISTINCT derived_category FROM facets')} - CATEGORIES, set())
        self.assertEqual({r[0] for r in self.con.execute('SELECT DISTINCT validity FROM facets')} - VALIDITY, set())

    def test_jurisdiction_rules(self):
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE state='Federal' AND jurisdiction_basis NOT LIKE '%ederal%'"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE multi_state=1 AND state IS NOT NULL"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE dataset='seeger' AND state='Federal'"), 6703)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE jurisdiction_basis LIKE '%hostname%'"), 0)

    def test_dates_and_last_modified(self):
        for lo, hi, value in (('saved_lo', 'saved_hi', 'saved_at'), ('source_lo', 'source_hi', 'source_as_of'), ('effective_lo', 'effective_hi', 'effective_from')):
            self.assertEqual(self.one(f"SELECT count(*) FROM facets WHERE ({value} IS NULL) != ({lo} IS NULL) OR {lo}>{hi}"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE saved_at_basis LIKE '%Last-Modified%'"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE saved_at IS NOT NULL AND length(saved_lo)!=10"), 0)

    def test_shells_and_wrong_joins(self):
        self.assertGreaterEqual(self.one("SELECT count(*) FROM facets WHERE original_sha256=? AND validity='parked_redirect'", LANDER), 89)
        self.assertGreater(self.one('SELECT count(*) FROM wrong_county_joins'), 4800)
        self.assertEqual(self.one("SELECT count(*) FROM wrong_county_joins w JOIN facets f ON f.record_id=w.record_id WHERE f.validity='ok'"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE dataset='federal' AND has_original=0 AND validity!='no_capture'"), 0)

    def test_titles_and_path_freedom(self):
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE display_title IS NULL OR trim(display_title)=''"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE title_issue='url' AND display_title LIKE 'http%'"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE title_issue IN ('url','placeholder','generic','boilerplate') AND title_evidence IS NULL"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM facets WHERE title_evidence LIKE '%/raw/%' OR title_evidence LIKE '%:/Users/%' OR title_evidence LIKE '%.sqlite3%'"), 0)


if __name__ == '__main__':
    unittest.main()
