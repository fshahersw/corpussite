"""Adapter tests: a tiny hand-made publication (hash gate, temporal labels, no paths) plus live-data checks."""
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import federal_regulations as regs

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'sources/federal_regulations_20260919'
TEMPORAL_KEYS = {'captured_at', 'captured_at_basis', 'source_as_of', 'source_as_of_basis', 'published_at',
                 'published_at_basis', 'effective_from', 'effective_from_basis', 'effective_to', 'effective_to_basis'}


def load_build():
    spec = importlib.util.spec_from_file_location('federal_regulations_build', SOURCE / 'build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / 'pub'
        self.folder.mkdir()
        self.build = load_build()
        self.oul = Path(self.tmp.name) / 'oul.sqlite3'
        self.make_oul()
        self.make_publication()
        self.patches = [patch.object(regs, 'DATA', self.folder), patch.object(regs, 'OUL_DB', self.oul)]
        for item in self.patches:
            item.start()
        regs.reset_cache()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        regs.reset_cache()
        self.tmp.cleanup()

    def make_oul(self):
        db = sqlite3.connect(self.oul)
        db.execute('CREATE TABLE records(id TEXT UNIQUE NOT NULL,source_id TEXT,title TEXT,text TEXT)')
        db.execute("CREATE VIRTUAL TABLE records_fts USING fts5(title,citation,text,content='records',content_rowid='rowid')")
        self.oul_text = '(a) Publisher copy of postmarketing reporting text mentioning pharmacovigilance.'
        db.execute('INSERT INTO records(rowid,id,source_id,title,text) VALUES(7,?,?,?,?)',
                   ('oul:abc', 'CFR_T21_P314_S314_80', '§ 314.80 Postmarketing reporting of adverse drug experiences.', self.oul_text))
        db.execute("INSERT INTO records_fts(rowid,title,citation,text) VALUES(7,'t','c',?)", (self.oul_text,))
        db.commit()
        db.close()

    def make_publication(self):
        path = self.folder / 'regulations.sqlite3'
        db = sqlite3.connect(path)
        self.build.create_schema(db)
        db.execute("INSERT INTO titles(title,name,ecfr_latest_amended_on,ecfr_latest_issue_date,ecfr_up_to_date_as_of,ecfr_titles_captured_at,structure_as_of,structure_captured_at) VALUES('21','Food and Drugs','2026-09-16','2026-09-16','2026-09-17','2026-09-19T04:32:09Z','2026-09-16','2026-09-19T04:32:36Z')")
        db.execute("INSERT INTO parts(title,part,heading,chapter,chapter_name,in_slice,ecfr_structure_sections,structure_as_of,oul_rows,gpo_sections,gpo_authority,gpo_source_note,gpo_volume,gpo_amddate_raw,gpo_amddate_iso,versions_rows,versions_latest_amendment_date,versions_captured_at,fr_documents,fr_count_reported,fr_captured_at) VALUES('21','314','Applications for FDA Approval to Market a New Drug','I','Food and Drug Administration',1,2,'2026-09-16',1,2,'21 U.S.C. 321, 355.','50 FR 7493, Feb. 22, 1985, unless otherwise noted.','5','Aug. 19, 2026','2026-08-19',3,'2024-06-18','2026-09-19T12:00:00+00:00',1,1,'2026-09-19T12:05:00+00:00')")
        db.execute("INSERT INTO parts(title,part,heading,in_slice,gpo_sections) VALUES('21','315','Diagnostic Radiopharmaceuticals',0,0)")
        text = '(a) Definitions. Each applicant shall report serious adverse drug experiences within 15 calendar days.'
        db.execute("INSERT INTO section_index(id,title,part,section,heading,subpart,in_slice,in_gpo,in_oul,in_ecfr_structure,reserved,versions_count,latest_amendment_date,latest_issue_date) VALUES(1,'21','314','314.80','Postmarketing reporting of adverse drug experiences.','B',1,1,1,1,0,2,'2024-06-18','2024-06-20')")
        db.execute("INSERT INTO section_index(id,title,part,section,heading,subpart,in_slice,in_gpo,in_oul,in_ecfr_structure,reserved,versions_count) VALUES(2,'21','314','314.81','Other postmarketing reports.','B',1,1,0,1,0,1)")
        db.execute("INSERT INTO gpo_sections(title,part,section,heading,subpart,node,volume,amddate_raw,amddate_iso,text,text_sha256,text_length,amendment_citations,captured_at) VALUES('21','314','314.80','§ 314.80   Postmarketing reporting of adverse drug experiences.','B','21:5.0.1.1.4.2.1.18','5','Aug. 19, 2026','2026-08-19',?,?,?,?,'2026-09-13T03:24:57.269306+00:00')",
                   (text, hashlib.sha256(text.encode()).hexdigest(), len(text), json.dumps(['[50 FR 7493, Feb. 22, 1985, as amended at 79 FR 33088, June 10, 2014]'])))
        db.execute("INSERT INTO gpo_sections(title,part,section,heading,subpart,node,volume,amddate_raw,amddate_iso,text,text_sha256,text_length,amendment_citations,captured_at) VALUES('21','314','314.81','§ 314.81   Other postmarketing reports.','B','n','5','Aug. 19, 2026','2026-08-19','Annual reports.',?,15,'[]','2026-09-13T03:24:57.269306+00:00')",
                   (hashlib.sha256(b'Annual reports.').hexdigest(),))
        db.execute("INSERT INTO oul_provisions(oul_rowid,oul_id,source_id,citation,title,part,section,heading,status,snapshot,snapshot_date,publisher_year,last_amended_year,text_length,text_sha256,source_url,captured_at) VALUES(7,'oul:abc','CFR_T21_P314_S314_80','21 C.F.R. § 314.80 (2026)','21','314','314.80','§ 314.80 Postmarketing reporting of adverse drug experiences.','in_force','v2026.08','2026-08-14',2026,2014,?,?,'https://www.ecfr.gov/current/title-21/part-314/section-314.80','2026-09-18T18:28:56+00:00')",
                   (len(self.oul_text), hashlib.sha256(self.oul_text.encode()).hexdigest()))
        for row in [('314.80', '2016-12-23', '2016-12-23', '2016-12-31', 1, 0), ('314.80', '2024-06-18', '2024-06-18', '2024-06-20', 1, 0),
                    ('314.81', '2016-12-23', '2016-12-23', '2016-12-31', 1, 0), ('314.99', '2016-12-23', '2016-12-23', '2016-12-31', 1, 0),
                    ('314.99', '2020-01-02', '2020-01-02', '2020-01-02', 1, 1)]:
            db.execute("INSERT INTO versions(title,part,section,name,subpart,type,version_date,amendment_date,issue_date,substantive,removed,captured_at) VALUES('21','314',?,?,'B','section',?,?,?,?,?,'2026-09-19T12:00:00+00:00')",
                       (row[0], '§ ' + row[0], row[1], row[2], row[3], row[4], row[5]))
        db.execute("INSERT INTO fr_documents(document_number,citation,title,type,action,publication_date,effective_on,signing_date,comments_close_on,dates_text,docket_ids,regulation_id_numbers,agencies,agency_slugs,cfr_references,html_url,pdf_url,volume,start_page,end_page,local_full_text,captured_at) VALUES('2014-13414','79 FR 33072','Postmarketing Safety Reports for Human Drug and Biological Products; Electronic Submission Requirements','Rule','Final rule.','2014-06-10','2015-06-10',NULL,NULL,'This rule is effective June 10, 2015.',?,?,?,?,?,'https://www.federalregister.gov/documents/2014/06/10/2014-13414/x','https://www.govinfo.gov/content/pkg/FR-2014-06-10/pdf/2014-13414.pdf',79,33072,33092,'[]','2026-09-19T12:05:00+00:00')",
                   (json.dumps(['Docket No. FDA-2008-N-0334']), json.dumps(['0910-AF96']),
                    json.dumps([{'name': 'Food and Drug Administration', 'slug': 'food-and-drug-administration', 'id': 199, 'parent_id': 221}]),
                    json.dumps(['food-and-drug-administration']), json.dumps([{'title': 21, 'part': '314'}])))
        db.execute("INSERT INTO fr_doc_parts(document_number,title,part,basis) VALUES('2014-13414','21','314','cfr_references')")
        db.execute("INSERT INTO section_fr_citations(title,section,citation_printed,volume,page,document_number) VALUES('21','314.80','79 FR 33088',79,33088,'2014-13414')")
        db.execute("INSERT INTO reconciliation(title,part,section,in_gpo,in_oul,oul_rows,in_ecfr_structure,heading_match,text_comparison,gpo_text_length,oul_text_lengths,similarity,category) VALUES('21','314','314.80',1,1,1,1,1,'differs',?,?,0.4,'text_differs')",
                   (len(text), json.dumps([len(self.oul_text)])))
        self.build.fill_search(db)
        db.commit()
        db.close()
        agencies = [{'id': 'agency:food-and-drug-administration', 'slug': 'food-and-drug-administration', 'name': 'Food and Drug Administration',
                     'short_name': 'FDA', 'parent_slug': 'health-and-human-services-department',
                     'cfr_references': [{'title': 21, 'chapter': 'I'}], 'slice_parts': ['cfr:21:314'],
                     'temporal': self.build.temporal(captured_at='2026-09-19T04:33:26Z', captured_at_basis='scout receipt')}]
        (self.folder / 'agencies.jsonl').write_text('\n'.join(json.dumps(a) for a in agencies) + '\n', encoding='utf-8')
        self.build.write_validation(self.folder, ['regulations.sqlite3', 'agencies.jsonl'], counts={'sections': 2}, checks=[], inputs=[],
                                    rows={'regulations.sqlite3': 2, 'agencies.jsonl': 1})

    def test_titles_parts_and_slice_flag(self):
        self.assertTrue(regs.info()['ready'])
        titles = regs.titles()['titles']
        self.assertEqual([t['title'] for t in titles], ['21'])
        self.assertEqual(titles[0]['id'], 'cfr:21')
        self.assertEqual(regs.parts('21')['total'], 1)
        everything = regs.parts('21', include_all=True)
        self.assertEqual([p['part'] for p in everything['parts']], ['314', '315'])
        part = regs.parts('21')['parts'][0]
        self.assertEqual(part['id'], 'cfr:21:314')
        self.assertEqual(part['authority_note_as_printed'], '21 U.S.C. 321, 355.')
        self.assertEqual(part['temporal']['source_as_of'], '2026-08-19')
        self.assertIn('Aug. 19, 2026', part['temporal']['source_as_of_basis'])

    def test_section_has_separate_texts_history_and_related_documents(self):
        row = regs.section('21 CFR 314.80')
        self.assertEqual(row['id'], 'cfr:21:314.80')
        self.assertEqual(regs.section('21 C.F.R. § 314.80')['id'], row['id'])
        self.assertEqual(regs.section('cfr:21:314.80')['id'], row['id'])
        sources = {t['source']: t for t in row['texts']}
        self.assertEqual(set(sources), {'gpo_ecfr_xml', 'open_us_law'})
        self.assertIn('15 calendar days', sources['gpo_ecfr_xml']['text'])
        self.assertEqual(sources['open_us_law']['text'], self.oul_text)
        self.assertTrue(sources['open_us_law']['text_hash_verified'])
        self.assertEqual(sources['gpo_ecfr_xml']['temporal']['source_as_of'], '2026-08-19')
        self.assertEqual(sources['open_us_law']['temporal']['source_as_of'], '2026-08-14')
        self.assertIsNone(sources['gpo_ecfr_xml']['temporal']['effective_from'])
        self.assertEqual([h['ecfr_amendment_date'] for h in row['history']], ['2016-12-23', '2024-06-18'])
        self.assertEqual(row['amendment_citations_as_printed'], ['[50 FR 7493, Feb. 22, 1985, as amended at 79 FR 33088, June 10, 2014]'])
        document = row['related_fr_documents']['documents'][0]
        self.assertEqual(document['id'], 'fr_doc:2014-13414')
        self.assertIn('cited_in_printed_amendment_note', document['relations'])
        self.assertEqual(row['reconciliation']['category'], 'text_differs')
        self.assertIsNone(regs.section('21 CFR 999.1'))
        self.assertIsNone(regs.section('garbage'))

    def test_effective_date_trap_is_never_promoted(self):
        document = regs.fr_document('2014-13414')
        self.assertEqual(document['publication_date'], '2014-06-10')
        self.assertEqual(document['effective_on_publisher_extracted'], '2015-06-10')
        self.assertEqual(document['dates_text_raw'], 'This rule is effective June 10, 2015.')
        self.assertEqual(document['temporal']['published_at'], '2014-06-10')
        self.assertIsNone(document['temporal']['effective_from'])
        self.assertIn('unverified', document['temporal']['effective_from_basis'])
        self.assertEqual(document['docket_ids'], ['Docket No. FDA-2008-N-0334'])
        self.assertEqual(document['regulation_id_numbers'], ['0910-AF96'])

    def test_sections_as_of_uses_version_metadata_only(self):
        current = regs.sections('21:314')
        self.assertEqual([s['section'] for s in current['sections']], ['314.80', '314.81'])
        past = regs.sections('314', as_of='2019-06-01', title='21')
        self.assertEqual([s['section'] for s in past['sections']], ['314.80', '314.81', '314.99'])
        later = regs.sections('cfr:21:314', as_of='2021-01-01')
        self.assertEqual([s['section'] for s in later['sections']], ['314.80', '314.81'])
        self.assertTrue(later['as_of']['supported'])
        self.assertIn('not point-in-time text', later['as_of']['caveat'])
        self.assertFalse(regs.sections('21:314', as_of='2010-01-01')['as_of']['supported'])
        self.assertFalse(regs.sections('21:314', as_of='not-a-date')['as_of']['supported'])

    def test_search_text_filters_and_date_types(self):
        hits = regs.search(q='calendar days')
        self.assertEqual([r['id'] for r in hits['results']], ['cfr:21:314.80'])
        self.assertIn('gpo_text', hits['results'][0]['matched_in'])
        publisher = regs.search(q='pharmacovigilance')
        self.assertEqual([r['id'] for r in publisher['results']], ['cfr:21:314.80'])
        self.assertEqual(publisher['results'][0]['matched_in'], ['open_us_law_text'])
        self.assertEqual(regs.search(q='electronic submission', record_type='fr_document')['results'][0]['id'], 'fr_doc:2014-13414')
        amended = regs.search(date_type='amendment_date', dfrom='2024-01-01', dto='2024-12-31')
        self.assertEqual([r['id'] for r in amended['results']], ['cfr:21:314.80'])
        self.assertEqual(amended['date_filter']['label'], regs.DATE_TYPES['amendment_date'])
        published = regs.search(date_type='fr_publication_date', dfrom='2014-06-01', dto='2014-06-30')
        self.assertEqual([r['record_type'] for r in published['results']], ['fr_document'])
        self.assertEqual(regs.search(date_type='fr_effective_on', dfrom='2014-06-01', dto='2014-06-30')['total'], 0)
        self.assertEqual(regs.search(agency='food-and-drug-administration', record_type='section')['total'], 2)
        self.assertEqual(regs.search(agency='no-such-agency')['total'], 0)
        self.assertIn('error', regs.search(date_type='made_up'))
        self.assertEqual(regs.search(q='"unbalanced (quote')['total'], 0)
        self.assertEqual(len(regs.search(limit=1)['results']), 1)

    def test_agencies(self):
        rows = regs.agencies()['agencies']
        self.assertEqual(rows[0]['slug'], 'food-and-drug-administration')
        self.assertEqual(rows[0]['slice_parts'], ['cfr:21:314'])

    def test_every_record_has_temporal_block_and_no_paths(self):
        outputs = [regs.info(), regs.titles(), regs.parts('21'), regs.sections('21:314'), regs.section('21 CFR 314.80'),
                   regs.search(q='postmarketing'), regs.agencies(), regs.fr_document('2014-13414')]
        for block in walk(outputs):
            if isinstance(block, dict) and 'temporal' in block:
                self.assertEqual(set(block['temporal']), TEMPORAL_KEYS)
            if isinstance(block, str):
                self.assertNotIn(str(self.folder), block)
                self.assertNotIn('Users', block)
                self.assertNotIn('sqlite3', block)
        for record in [regs.titles()['titles'][0], regs.parts('21')['parts'][0], regs.sections('21:314')['sections'][0],
                       regs.section('21 CFR 314.80'), regs.fr_document('2014-13414'), regs.agencies()['agencies'][0],
                       regs.section('21 CFR 314.80')['history'][0]]:
            self.assertEqual(set(record['temporal']), TEMPORAL_KEYS)

    def test_tampered_database_fails_closed(self):
        with (self.folder / 'regulations.sqlite3').open('ab') as out:
            out.write(b' ')
        regs.reset_cache()
        self.assertFalse(regs.info()['ready'])
        self.assertEqual(regs.titles()['titles'], [])
        self.assertEqual(regs.parts('21')['parts'], [])
        self.assertIsNone(regs.section('21 CFR 314.80'))
        self.assertEqual(regs.search(q='postmarketing')['results'], [])
        self.assertEqual(regs.agencies()['agencies'], [])

    def test_gate_requires_passed_and_ready(self):
        gate = json.loads((self.folder / 'validation.json').read_text(encoding='utf-8'))
        for change in ({'status': 'failed'}, {'ready': False}, {'data_files': []}):
            (self.folder / 'validation.json').write_text(json.dumps(gate | change), encoding='utf-8')
            regs.reset_cache()
            self.assertFalse(regs.info()['ready'], change)

    def test_publisher_text_change_is_not_served_as_verified(self):
        db = sqlite3.connect(self.oul)
        db.execute("UPDATE records SET text='silently changed' WHERE rowid=7")
        db.commit()
        db.close()
        row = regs.section('21 CFR 314.80')
        publisher = [t for t in row['texts'] if t['source'] == 'open_us_law'][0]
        self.assertFalse(publisher['text_hash_verified'])
        self.assertIsNone(publisher['text'])

    def test_missing_publisher_index_degrades_without_failing(self):
        with patch.object(regs, 'OUL_DB', self.folder / 'absent.sqlite3'):
            row = regs.section('21 CFR 314.80')
            publisher = [t for t in row['texts'] if t['source'] == 'open_us_law'][0]
            self.assertIsNone(publisher['text'])
            self.assertEqual([r['id'] for r in regs.search(q='calendar days')['results']], ['cfr:21:314.80'])
            self.assertEqual(regs.search(q='pharmacovigilance')['total'], 0)


@unittest.skipUnless((SOURCE / 'validation.json').exists(), 'real publication not built yet')
class LivePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        regs.reset_cache()

    def test_ready_and_ninety_eight_parts(self):
        info = regs.info()
        self.assertTrue(info['ready'])
        self.assertEqual(info['counts']['selected_parts'], 98)
        self.assertEqual([t['title'] for t in regs.titles()['titles']], ['16', '21', '40', '49'])
        self.assertEqual(sum(regs.parts(t)['total'] for t in ('16', '21', '40', '49')), 98)
        self.assertEqual(regs.parts('21', include_all=True)['total'], 275)

    def test_known_section(self):
        row = regs.section('21 CFR 314.80')
        self.assertEqual({t['source'] for t in row['texts']}, {'gpo_ecfr_xml', 'open_us_law'})
        gpo = [t for t in row['texts'] if t['source'] == 'gpo_ecfr_xml'][0]
        self.assertIn('adverse drug experience', gpo['text'].lower())
        self.assertTrue(row['amendment_citations_as_printed'])
        self.assertIsNone(gpo['temporal']['effective_from'])

    def test_title_without_local_official_text_reads_publisher_text_on_demand(self):
        row = regs.section('16 CFR 1115.4')
        self.assertEqual([t['source'] for t in row['texts']], ['open_us_law'])
        if regs.OUL_DB.exists():
            self.assertTrue(row['texts'][0]['text_hash_verified'])
            self.assertTrue(row['texts'][0]['text'])

    def test_search_runs(self):
        result = regs.search(q='medical device report', title='21', part='803', limit=5)
        self.assertGreater(result['total'], 0)
        self.assertLessEqual(len(result['results']), 5)


if __name__ == '__main__':
    unittest.main()
