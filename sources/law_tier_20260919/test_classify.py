"""Unit tests for the structural classifier and rule-set typing (synthetic text modelled on saved samples)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify as cl  # noqa: E402

STATUTE = ('TITLE 40 HIGHWAYS AND BRIDGES CHAPTER 15 CONSOLIDATION OF HIGHWAY DISTRICTS\n'
           + ''.join('40 - 15%02d. CONSOLIDATION OF DISTRICTS. (%d) Any highway district within the state may be consolidated with any '
                     'other district, and the board of commissioners shall give notice of the election to the voters of the district '
                     'in the manner provided by law. [40 - 15%02d, added 1985, ch. 253, sec. 2, p. 666.]\n' % (i, i, i) for i in range(1, 12)))
TOC = ('Statutes & Constitution : View Statutes\n'
       + ''.join('Chapter %d NONPARTISAN ELECTIONS Part I GENERAL PROVISIONS (ss. %d.011-%d.345)\n' % (i, i, i) for i in range(100, 160)))
NAV = ('Pennsylvania General Assembly Senate Home Members Leadership Committees Session Find My Senator House Home Members '
       'Leadership Committees Search Help Text Size Print Back Visitor Information Contact Us Privacy Accessibility ' * 3)
RULE = ('Rule 240 1.9: Duties to Former Clients - KS Courts Skip to content Search Public Attorneys Judges About the Courts\n'
        + '(a) A lawyer who has formerly represented a client in a matter shall not thereafter represent another person in the same or a '
          'substantially related matter in which that person\'s interests are materially adverse to the interests of the former client '
          'unless the former client gives informed consent, confirmed in writing. (b) A lawyer shall not knowingly represent a person in '
          'the same matter in which a firm with which the lawyer formerly was associated had previously represented a client. ' * 6)


class ClassifierTests(unittest.TestCase):
    def label(self, text, categories, title='', url='', fmt='pdf', html=None):
        feats = cl.text_features(text)
        if html is not None: feats.update(html)
        return cl.classify(feats, text, categories, title, url, fmt)

    def test_statute_chapter_is_a_statute_body(self):
        out = self.label(STATUTE, ['statutes'], 'T40CH15', 'https://legislature.idaho.gov/statutesrules/idstat/Title40/T40CH15.pdf')
        self.assertEqual(out['law_body_class'], 'statute_body')
        self.assertEqual(out['confidence'], 'high')
        self.assertIn('modal', out['class_basis'])

    def test_family_follows_category_and_title_evidence(self):
        self.assertEqual(self.label(STATUTE, ['constitution'], 'Constitution of the State')['law_body_class'], 'constitution_body')
        self.assertEqual(self.label(STATUTE, ['court_rules'], 'Rules of Civil Procedure')['law_body_class'], 'court_rule_body')
        self.assertEqual(self.label(STATUTE, ['administrative_code'], 'Administrative Code')['law_body_class'], 'regulation_body')
        mixed = self.label(STATUTE, ['constitution', 'statutes'], 'State Constitution', 'https://x.gov/constitution.pdf')
        self.assertEqual(mixed['law_body_class'], 'constitution_body')

    def test_table_of_contents_is_a_hub(self):
        out = self.label(TOC, ['statutes'], 'View Statutes', 'https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Index', 'html',
                         {'link_ratio': 0.8, 'links': 120, 'nonlink_chars': 400, 'nonlink_function_ratio': 0.05})
        self.assertEqual(out['law_body_class'], 'hub_or_index')

    def test_site_chrome_only_is_navigation(self):
        out = self.label(NAV, ['statutes'], 'Title 37', 'https://www.palegis.us/statutes/consolidated/view-statute?ttl=37', 'html',
                         {'link_ratio': 0.9, 'links': 60, 'nonlink_chars': 90, 'nonlink_function_ratio': 0.02})
        self.assertEqual(out['law_body_class'], 'navigation')

    def test_repealed_stub_and_amendment_order_are_notices(self):
        stub = '--- PAGE 1 --- CHAPTER 30-24 ACTIONS BY AND AGAINST EXECUTORS [Repealed by S.L. 1973, ch. 257, § 82] Page No. 1'
        self.assertEqual(self.label(stub, ['statutes'])['law_body_class'], 'notice')
        order = ('IN THE COURT OF COMMON PLEAS FOR THE STATE OF DELAWARE ORDER AMENDING RULE 58 OF THE RULES OF CRIMINAL PROCEDURE '
                 'This 5th day of August 2015, IT IS ORDERED that Rule 58 is amended by deleting the material in brackets. ' * 2)
        self.assertEqual(self.label(order, ['court_rules'])['law_body_class'], 'notice')

    def test_blank_heavy_document_is_a_form(self):
        form = ('PETITION FORM Case No. ________ Plaintiff ____________ Defendant ____________ Signature ____________ '
                'Date ________ Print Name ____________ Address ____________ Phone ________ ') * 3
        self.assertEqual(self.label(form, ['court_rules'], 'Civil cover sheet form')['law_body_class'], 'form')

    def test_rule_page_with_site_chrome_is_still_a_rule_body(self):
        out = self.label(RULE, ['court_rules'], 'Rule 240 1.9: Duties to Former Clients', 'https://kscourts.gov/Rules-Orders/Rules/1-9', 'html',
                         {'link_ratio': 0.2, 'links': 40, 'nonlink_chars': 2600, 'nonlink_function_ratio': 0.4})
        self.assertEqual(out['law_body_class'], 'court_rule_body')

    def test_session_law_and_empty_text_fall_to_other(self):
        out = self.label(STATUTE, ['statutes'], '1887 Statutes of Nevada, Pages 145-163', 'https://www.leg.state.nv.us/Statutes/13th1887/Stats1887R01.html', 'html')
        self.assertEqual((out['law_body_class'], out['class_detail']), ('other', 'session_law_compilation'))
        self.assertEqual(self.label('', ['statutes'])['law_body_class'], 'other')
        self.assertEqual(self.label('', ['statutes'])['confidence'], 'low')

    def test_every_label_has_basis_confidence_and_no_currency_claim(self):
        for text in (STATUTE, TOC, NAV, RULE, ''):
            out = self.label(text, ['statutes'])
            self.assertIn(out['law_body_class'], cl.CLASSES)
            self.assertIn(out['confidence'], ('high', 'medium', 'low'))
            self.assertTrue(out['class_basis'])
            self.assertIs(out['legal_currency_asserted'], False)


class RuleSetTests(unittest.TestCase):
    def test_typing_from_set_names(self):
        cases = {
            'Texas Rules of Civil Procedure': 'civil_procedure',
            'North Dakota Rules of Evidence': 'evidence',
            'Oregon Rules of Appellate Procedure': 'appellate',
            'Rules of Civil Appellate Procedure': 'appellate',
            'Alabama Rules of Criminal Procedure': 'criminal',
            'IL Court Rules Article XII: Local Rules': 'local',
            'Rules of Practice for the Eighth Judicial District Court': 'local',
            'North Dakota Rules of Professional Conduct': 'professional_conduct',
            'Code of Judicial Conduct': 'professional_conduct',
            'Florida Rules of Juvenile Procedure': 'other',
            'Family Court Civil Rules': 'other',
            'Reglas de Procedimiento Civil de Puerto Rico': 'civil_procedure',
            'Article II: Rules on Civil Proceedings in the Trial Court': 'civil_procedure',
            'Supreme Court Rules of Professional Practice': 'professional_conduct',
            'Alaska Bar Rules': 'professional_conduct',
            'Rules Governing Admission to the Mississippi Bar': 'professional_conduct',
            'Admission and Practice Rules': 'professional_conduct',
            'Reglamento de Admision para Aspirantes al Ejercicio de la Abogacia': 'professional_conduct',
            'Professional Rules (cond)': 'professional_conduct',
            'Rules of the Land Court': 'other',
            'Florida Rules for Certified and Court-Appointed Mediators': 'other',
            'Chapter 16 - Iowa Rules of Electronic Procedure': 'other',
            'Article I: General Rules': 'other',
        }
        for name, expected in cases.items():
            self.assertEqual(cl.rule_set_type([('title_name', name)])[0], expected, name)

    def test_no_reliable_signal_is_untyped(self):
        for mixed in ('Connecticut Practice Book', 'West Virginia Trial Court Rules', 'Uniform Superior Court Rules',
                      'Rules of the Supreme Court of Georgia', 'Massachusetts Trial Court Rules', 'Michigan Court Rules'):
            self.assertEqual(cl.rule_set_type([('title_name', mixed)]), (None, None, None), mixed)
        self.assertEqual(cl.rule_set_type([('title_name', None)]), (None, None, None))

    def test_other_details_name_the_specialised_area(self):
        self.assertEqual(cl.rule_set_type([('title_name', 'Rules of the Land Court')])[:2], ('other', 'specialised_court'))
        self.assertEqual(cl.rule_set_type([('title_name', 'Iowa Rules of Electronic Procedure')])[:2], ('other', 'administration'))
        self.assertEqual(cl.rule_set_type([('title_name', 'Alaska Bar Rules')])[:2], ('professional_conduct', 'bar_admission_or_discipline'))

    def test_basis_names_field_and_pattern(self):
        kind, detail, basis = cl.rule_set_type([('title_name', 'PA Court Rules'), ('chapter_name', 'Juvenile Court Rules')])
        self.assertEqual((kind, detail), ('other', 'juvenile'))
        self.assertIn('chapter_name', basis)


if __name__ == '__main__':
    unittest.main()
