"""Unit tests for the offline builder's pure helpers (no network, no large inputs)."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('federal_regulations_build', HERE / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

XML = """<?xml version="1.0" encoding="UTF-8"?>
<DLPSTEXTCLASS><TEXT><BODY>
<ECFRBRWS>
<AMDDATE>Aug. 19, 2026
</AMDDATE>
<DIV1 N="5" NODE="21:5" TYPE="TITLE"><HEAD>Title 21—Food and Drugs--Volume 5</HEAD>
<DIV3 N="I" NODE="21:5.0.1" TYPE="CHAPTER"><HEAD> CHAPTER I—FOOD AND DRUG ADMINISTRATION</HEAD>
<DIV4 N="D" NODE="21:5.0.1.1" TYPE="SUBCHAP"><HEAD>SUBCHAPTER D—DRUGS FOR HUMAN USE</HEAD>
<DIV5 N="314" NODE="21:5.0.1.1.4" TYPE="PART"><HEAD>PART 314—APPLICATIONS FOR FDA APPROVAL TO MARKET A NEW DRUG </HEAD>
<AUTH><HED>Authority:</HED><PSPACE>21 U.S.C. 321, 355.</PSPACE></AUTH>
<SOURCE><HED>Source:</HED><PSPACE>50 FR 7493, Feb. 22, 1985, unless otherwise noted.</PSPACE></SOURCE>
<DIV6 N="B" NODE="21:5.0.1.1.4.2" TYPE="SUBPART"><HEAD>Subpart B—Applications</HEAD>
<DIV8 N="§ 314.80" NODE="21:5.0.1.1.4.2.1.12" TYPE="SECTION">
<HEAD>§ 314.80   Postmarketing reporting of adverse drug experiences.</HEAD>
<P>(a) <I>Definitions.</I> The following definitions apply:
</P>
<EXTRACT><P>Quoted paragraph one.</P><P>Quoted paragraph two.</P></EXTRACT>
<P>(b) Each applicant shall report within 15 calendar days.</P>
<EDNOTE><HED>Editorial Note:</HED><PSPACE>Nomenclature changes appear at 69 FR 13717, Mar. 24, 2004.</PSPACE></EDNOTE>
<CITA TYPE="N">[50 FR 7493, Feb. 22, 1985, as amended at 79 FR 33088, June 10, 2014]


</CITA>
</DIV8>
<DIV8 N="314.81" NODE="21:5.0.1.1.4.2.1.13" TYPE="SECTION"><HEAD>314.81   [Reserved]</HEAD></DIV8>
</DIV6>
<DIV9 N="Appendix A" NODE="21:5.0.1.1.4.9" TYPE="APPENDIX"><HEAD>Appendix A to Part 314</HEAD><P>Appendix text.</P></DIV9>
</DIV5></DIV4></DIV3></DIV1></ECFRBRWS>
</BODY></TEXT></DLPSTEXTCLASS>
"""


class HelperTests(unittest.TestCase):
    def test_printed_dates(self):
        self.assertEqual(build.parse_printed_date('Sept. 8, 2026'), '2026-09-08')
        self.assertEqual(build.parse_printed_date('June 10, 2014'), '2014-06-10')
        self.assertEqual(build.parse_printed_date('Dec. 18, 2025 '), '2025-12-18')
        self.assertEqual(build.parse_printed_date('May 23, 1985'), '1985-05-23')
        self.assertIsNone(build.parse_printed_date('Foo. 1, 2000'))
        self.assertIsNone(build.parse_printed_date(''))

    def test_fr_citations_from_printed_note(self):
        cites = build.parse_fr_citations('[50 FR 7493, Feb. 22, 1985; 50 FR 14212, Apr. 11, 1985, as amended at 79 FR 33088, June 10, 2014; 81 FR 59131]')
        self.assertEqual([(c['volume'], c['page'], c['date_iso']) for c in cites],
                         [(50, 7493, '1985-02-22'), (50, 14212, '1985-04-11'), (79, 33088, '2014-06-10'), (81, 59131, None)])
        self.assertEqual(cites[2]['citation_printed'], '79 FR 33088, June 10, 2014')

    def test_section_number_forms(self):
        self.assertEqual(build.section_number('§ 314.80', ''), '314.80')
        self.assertEqual(build.section_number('892.2100', ''), '892.2100')
        self.assertEqual(build.section_number('', '§ 1.1465   Something.'), '1.1465')
        self.assertEqual(build.section_number('§ 3.2a', ''), '3.2a')
        self.assertIsNone(build.section_number('Appendix A', 'Appendix A to Part 5'))

    def test_strip_prefix_and_sort_keys(self):
        self.assertEqual(build.strip_section_prefix('§ 314.80   Postmarketing reporting.', '314.80'), 'Postmarketing reporting.')
        self.assertEqual(build.strip_section_prefix('Postmarketing reporting.', '314.80'), 'Postmarketing reporting.')
        self.assertLess(build.part_sort_key('99'), build.part_sort_key('100'))
        self.assertLess(build.part_sort_key('83-98'), build.part_sort_key('99'))
        self.assertLess(build.section_sort_key('314.9'), build.section_sort_key('314.80'))
        self.assertLess(build.section_sort_key('314.80'), build.section_sort_key('314.80a'))

    def test_temporal_block_shape(self):
        block = build.temporal(source_as_of='2026-08-19', source_as_of_basis='x')
        self.assertEqual(set(block), {'captured_at', 'captured_at_basis', 'source_as_of', 'source_as_of_basis', 'published_at', 'published_at_basis',
                                      'effective_from', 'effective_from_basis', 'effective_to', 'effective_to_basis'})
        self.assertIsNone(block['effective_from'])

    def test_gpo_xml_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'title.xml'
            path.write_text(XML, encoding='utf-8')
            parsed = build.parse_gpo_xml(path)
        self.assertEqual(parsed['volumes'][0], {'volume': '5', 'amddate_raw': 'Aug. 19, 2026', 'amddate_iso': '2026-08-19', 'head': 'Title 21—Food and Drugs--Volume 5'})
        part = parsed['parts'][0]
        self.assertEqual((part['part'], part['authority'], part['source_note'], part['sections'], part['appendices']),
                         ('314', '21 U.S.C. 321, 355.', '50 FR 7493, Feb. 22, 1985, unless otherwise noted.', 2, 1))
        self.assertEqual(part['chapter'], 'I')
        first, second = parsed['sections']
        self.assertEqual(first['section'], '314.80')
        self.assertEqual(first['subpart'], 'B')
        self.assertEqual(first['text'].split('\n'), ['(a) Definitions. The following definitions apply:', 'Quoted paragraph one.', 'Quoted paragraph two.',
                                                     '(b) Each applicant shall report within 15 calendar days.'])
        self.assertEqual(first['citations'], ['[50 FR 7493, Feb. 22, 1985, as amended at 79 FR 33088, June 10, 2014]'])
        self.assertEqual(first['notes'][0]['kind'], 'EDNOTE')
        self.assertFalse(first['reserved'])
        self.assertEqual((second['section'], second['reserved'], second['text']), ('314.81', True, ''))
        self.assertEqual(parsed['appendices'][0]['label'], 'Appendix A')
        self.assertEqual(parsed['unparsed'], [])

    def test_resolve_citation_requires_explicit_evidence(self):
        docs = {'2014-13414': {'document_number': '2014-13414', 'volume': 79, 'start_page': 33072, 'end_page': 33092, 'publication_date': '2014-06-10'},
                '2014-99999': {'document_number': '2014-99999', 'volume': 79, 'start_page': 33090, 'end_page': 33095, 'publication_date': '2014-06-10'}}
        index = build.build_fr_page_index(docs)
        ok = build.parse_fr_citations('79 FR 33088, June 10, 2014')[0]
        self.assertEqual(build.resolve_citation(ok, index), ('2014-13414', None))
        wrong_date = build.parse_fr_citations('79 FR 33088, June 11, 2014')[0]
        self.assertIsNone(build.resolve_citation(wrong_date, index)[0])
        self.assertIn('printed date differs', build.resolve_citation(wrong_date, index)[1])
        ambiguous = build.parse_fr_citations('79 FR 33091')[0]
        self.assertIn('ambiguous', build.resolve_citation(ambiguous, index)[1])
        exact = build.parse_fr_citations('79 FR 33090')[0]
        self.assertEqual(build.resolve_citation(exact, index)[0], '2014-99999')
        missing = build.parse_fr_citations('50 FR 7493, Feb. 22, 1985')[0]
        self.assertIn('no fetched', build.resolve_citation(missing, index)[1])

    def test_compare_and_reconcile(self):
        self.assertEqual(build.compare_texts('a  b\nc', 'a b c [50 FR 7493, Feb. 22, 1985]'), ('identical', 1.0))
        kind, sim = build.compare_texts('one two three four', 'one two three five')
        self.assertEqual(kind, 'differs')
        self.assertGreater(sim, 0.5)
        gpo = {'heading': '§ 314.80   Postmarketing reporting.', 'text': 'x y z', 'text_length': 5}
        oul = [{'oul_rowid': 7, 'heading': '§ 314.80 Postmarketing reporting.', 'text_length': 5}]
        rec = build.reconcile('314.80', gpo, oul, True, {7: 'x y z'})
        self.assertEqual((rec['category'], rec['heading_match'], rec['similarity']), ('identical', 1, 1.0))
        rec = build.reconcile('314.80', gpo, oul, True, {7: 'x y q'})
        self.assertEqual(rec['category'], 'text_differs')
        self.assertEqual(build.reconcile('314.81', gpo, [], True, {})['category'], 'gpo_only')
        self.assertEqual(build.reconcile('314.82', None, oul, False, {})['category'], 'oul_only')
        self.assertEqual(build.reconcile('314.83', None, [], True, {})['category'], 'structure_only')

    def test_structure_walker(self):
        tree = {'type': 'title', 'identifier': '49', 'children': [
            {'type': 'subtitle', 'identifier': 'B', 'children': [
                {'type': 'chapter', 'identifier': 'V', 'label_description': 'NHTSA', 'children': [
                    {'type': 'part', 'identifier': '573', 'label_description': 'Defect and Noncompliance Responsibility and Reports', 'reserved': False, 'size': 10, 'children': [
                        {'type': 'section', 'identifier': '573.6', 'label_description': 'Defect and noncompliance information report.', 'reserved': False, 'size': 5, 'received_on': 'x'}]}]},
                {'type': 'part', 'identifier': '1', 'label_description': 'Loose part', 'reserved': False, 'size': 1, 'children': []}]}]}
        out = build.parse_structure(tree)
        self.assertEqual([(p['part'], p['chapter'], p['subtitle'], p['sections']) for p in out['parts']], [('573', 'V', 'B', 1), ('1', None, 'B', 0)])
        self.assertEqual(out['sections'][0]['part'], '573')

    def test_validation_envelope_and_search_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = sqlite3.connect(folder / 'regulations.sqlite3')
            build.create_schema(db)
            db.execute("INSERT INTO section_index(id,title,part,section,heading) VALUES(1,'21','314','314.80','Postmarketing reporting')")
            db.execute("INSERT INTO gpo_sections(title,part,section,heading,text,text_sha256,text_length,captured_at) VALUES('21','314','314.80','h','fifteen calendar days','s',21,'c')")
            db.execute("INSERT INTO fr_documents(document_number,title,publication_date,captured_at) VALUES('2014-13414','Electronic Submission Requirements','2014-06-10','c')")
            build.fill_search(db)
            build.fill_search(db)  # re-runnable
            hits = db.execute("SELECT rowid FROM search_fts WHERE search_fts MATCH 'calendar'").fetchall()
            self.assertEqual(hits, [(1,)])
            self.assertEqual(db.execute("SELECT count(*) FROM search_fts WHERE search_fts MATCH 'electronic'").fetchone()[0], 1)
            db.commit()
            db.close()
            envelope = build.write_validation(folder, ['regulations.sqlite3'], counts={'x': 1}, checks=[{'name': 'a', 'passed': True}], rows={'regulations.sqlite3': 1})
            self.assertEqual((envelope['status'], envelope['ready'], envelope['schema_version']), ('passed', True, '1'))
            self.assertEqual(envelope['data_files'][0]['path'], 'regulations.sqlite3')
            self.assertEqual(len(envelope['data_files'][0]['sha256']), 64)
            self.assertTrue((folder / 'validation.json').exists())
            failed = build.write_validation(folder, [], checks=[{'name': 'a', 'passed': False}])
            self.assertEqual((failed['status'], failed['ready']), ('failed', False))
            self.assertEqual(set(json.loads((folder / 'validation.json').read_text())), {'schema_version', 'status', 'ready', 'validated_at', 'data_files',
                                                                                          'counts', 'checks', 'qualification', 'license_ref', 'inputs'})


if __name__ == '__main__':
    unittest.main()
