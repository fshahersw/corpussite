"""Independent legal-content and grouping boundary fixtures; no production writes."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('review_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


readable = load('readable')
presentation = load('presentation')
INTRO = 'The following material is published by the court for litigants and counsel. Consult the stated effective date and the preserved original source document. '


class LegalTextPreservation(unittest.TestCase):
    def view(self, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'court.html'
            path.write_text('<html><body><main><h1>Court rules</h1><p>' + INTRO + '</p>' + content + '</main></body></html>', encoding='utf-8')
            return readable.reading_view(INTRO + ' preserved fallback ', title='Court rules', source_url='https://court.example/rules', raw_path=path)['text']

    def test_legal_header_effective_date_is_retained(self):
        text = self.view('<header><p>Effective July 1, 2026. Applies only to civil cases filed after that date.</p></header><p>Rule 1. Filing procedure.</p>')
        self.assertIn('Effective July 1, 2026', text)

    def test_server_rendered_form_wrapper_preserves_rule_body(self):
        text = self.view('<form id="aspnetForm" method="post"><input type="hidden" name="__VIEWSTATE"><article><h2>Rule 5. Service</h2><p>A motion must be served no later than seven days before the hearing.</p></article></form>')
        self.assertIn('seven days before the hearing', text)

    def test_statutory_aside_note_is_preserved(self):
        text = self.view('<article><h2>Section 12. Filing</h2><p>The clerk shall accept the filing.</p><aside role="note"><p>Exception: This section does not apply to sealed juvenile records.</p></aside></article>')
        self.assertIn('sealed juvenile records', text)

    def test_legal_resource_index_is_not_discarded_as_menu(self):
        labels = ['Civil Local Rules', 'Criminal Local Rules', 'Probate Practice', 'Family Court Rules', 'Juvenile Practice', 'Standing Orders', 'Filing Requirements', 'Appellate Procedures']
        links = ''.join('<li><a href="/' + str(i) + '.pdf">' + label + '</a></li>' for i, label in enumerate(labels))
        text = self.view('<section><h2>Local rulebooks and filing requirements</h2><ul>' + links + '</ul></section>')
        for label in labels: self.assertIn(label, text)

    def test_navigation_controls_removed_without_removing_rule_header(self):
        text = self.view('<nav><a href="/">Home</a><button>Menu</button></nav><header><h2>Article III. Jurisdiction</h2></header><p>The superior court retains jurisdiction.</p>')
        self.assertNotIn('Home', text)
        self.assertIn('Article III. Jurisdiction', text)

    def test_repeated_short_legal_values_are_not_assumed_duplicate_ui(self):
        text, _, _ = readable.clean_text('Deadline table\n30 days\n30 days\nEffective date', '')
        self.assertEqual(text.count('30 days'), 2)

    def test_readonly_legal_textarea_preserves_published_body(self):
        text = self.view('<label>Published rule text</label><textarea readonly>Rule 7. An extension requires a written finding of good cause.</textarea>')
        self.assertIn('written finding of good cause', text)

    def test_legal_effective_header_outside_main_is_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'rules.html'
            path.write_text('<html><body><header><h1>Local Civil Rules</h1><p>Effective January 1, 2025. Superseded July 1, 2026.</p></header><main><h2>Rule 2. Filing</h2><p>'+INTRO+'</p></main></body></html>', encoding='utf-8')
            text = readable.reading_view(INTRO, source_url='https://court.example/rules', raw_path=path)['text']
            self.assertIn('Superseded July 1, 2026', text)


class GroupingBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE records(id TEXT PRIMARY KEY,title TEXT,group_name TEXT,dataset TEXT,state TEXT,county TEXT,kind TEXT,source_url TEXT,quality TEXT,content_id INTEGER,payload TEXT,inline_text TEXT,original_id TEXT,text_id TEXT);')

    def tearDown(self): self.db.close(); self.temp.cleanup()

    def add(self, key, dataset='focused', digest='a'*64, url='https://court.example/rules.pdf', state='Arizona', title='Rules', extra=None):
        payload = {'raw_sha256': digest, 'raw_path': 'raw/' + digest + '.pdf'}
        payload.update(extra or {})
        self.db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (key, title, 'laws', dataset, state, '', 'rules', url, 'Source original', None, json.dumps(payload), '', 'raw-' + key, 'text-' + key))

    def groups(self):
        presentation.build_groups(self.db, self.root)
        return self.db.execute('SELECT id,preferred_id,title,state,county,source_count,group_basis FROM display_groups').fetchall()

    def test_identical_bytes_keep_all_jurisdiction_source_members(self):
        self.add('az', state='Arizona', url='https://az.example/rules.pdf')
        self.add('nm', state='New Mexico', url='https://nm.example/rules.pdf')
        groups = self.groups(); self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][3], 'Arizona; New Mexico')
        members = presentation.source_list(self.db, groups[0][0])
        self.assertEqual({r['source_url'] for r in members}, {'https://az.example/rules.pdf', 'https://nm.example/rules.pdf'})

    def test_focused_versions_with_same_url_stay_distinct(self):
        self.add('old', digest='a'*64, title='Rules effective 2025')
        self.add('new', digest='b'*64, title='Rules effective 2026')
        self.assertEqual(len(self.groups()), 2)

    def test_federal_versions_with_same_url_stay_distinct(self):
        self.add('old', dataset='federal', digest='a'*64, title='Rules effective 2025')
        self.add('new', dataset='federal', digest='b'*64, title='Rules effective 2026')
        self.assertEqual(len(self.groups()), 2)

    def test_same_provider_url_does_not_merge_different_record_bodies(self):
        self.add('s1', dataset='seeger', digest='a'*64, title='Section 1', extra={'raw_path':'title.xml','text_sha256':'c'*64})
        self.add('s2', dataset='seeger', digest='a'*64, title='Section 2', extra={'raw_path':'title.xml','text_sha256':'d'*64})
        self.assertEqual(len(self.groups()), 2)

    def test_identical_text_with_explicit_different_editions_is_not_merged(self):
        for key, year, digest in [('old','2025','a'*64), ('new','2026','b'*64)]:
            self.add(key, dataset='provider_laws', digest=digest, title='Section 12. Filing',
                extra={'raw_path':digest+'.json','text_sha256':'c'*64,'release_point':year,'source_date':year+'-01-01'})
            self.db.execute('UPDATE records SET inline_text=? WHERE id=?', ('The clerk must accept a properly presented filing. '*12, key))
        self.assertEqual(len(self.groups()), 2)

    def test_ineligible_administrative_member_is_not_preferred_over_eligible_source(self):
        self.add('a-eligible', title='Transcript order form', extra={'metadata':{'retrieval_eligible':True}})
        self.add('z-admin', dataset='seeger', title='Transcript Purchase Order', extra={'metadata':{'retrieval_eligible':False}})
        self.db.execute('UPDATE records SET inline_text=? WHERE id=?', ('Administrative document native text. '*20, 'z-admin'))
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][1], 'a-eligible')

    def test_exact_judge_membership_maps_official_and_vendor_to_entity(self):
        entity = presentation.sid('entity:entity-1')
        official = presentation.sid('judge:obs-1')
        vendor = presentation.sid('vendor:obs-1')
        self.add(entity, dataset='judge_entities', extra={'entity_id':'entity-1'})
        self.add(official, dataset='judge_enrichment')
        self.add(vendor, dataset='judge_vendor')
        folder = self.root/'sources/judge_entities_20260918'; folder.mkdir(parents=True)
        entries = [{'dataset':'judge_enrichment','entity_id':'entity-1','source_observation_id':'obs-1'}, {'dataset':'judge_vendor','entity_id':'entity-1','source_observation_id':'obs-1'}]
        (folder/'members.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in entries), encoding='utf-8')
        groups = self.groups(); self.assertEqual(len(groups), 1); self.assertEqual(groups[0][1], entity)
        self.assertEqual({r['dataset'] for r in presentation.source_list(self.db, groups[0][0])}, {'judge_enrichment','judge_vendor'})

    def test_contradictory_judge_membership_must_not_silently_choose_last(self):
        a = presentation.sid('entity:entity-a'); b = presentation.sid('entity:entity-b'); obs = presentation.sid('judge:ambiguous')
        self.add(a, dataset='judge_entities', extra={'entity_id':'entity-a'}); self.add(b, dataset='judge_entities', extra={'entity_id':'entity-b'})
        self.add(obs, dataset='judge_enrichment')
        folder = self.root/'sources/judge_entities_20260918'; folder.mkdir(parents=True)
        (folder/'members.jsonl').write_text(''.join(json.dumps({'dataset':'judge_enrichment','entity_id':e,'source_observation_id':'ambiguous'})+'\n' for e in ['entity-a','entity-b']), encoding='utf-8')
        try:
            groups = self.groups()
        except (ValueError, RuntimeError): return
        own = next((g for g in groups if g[1] == obs), None)
        self.assertIsNotNone(own, 'Conflicting memberships must fail validation or leave the source observation unmerged')


if __name__ == '__main__': unittest.main(verbosity=2)
