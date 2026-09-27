import json,unittest
from pathlib import Path
from classify import classify,classify_link
ROOT=Path(__file__).resolve().parents[2]
CHECKPOINT=ROOT/'sources/county_litigation_firecrawl_20260919/checkpoints/20260919T100728840281Z/resources.jsonl'

class ClassifierTests(unittest.TestCase):
    def test_incidental_rule_citation_is_not_page_purpose(self):
        c=classify('Family Law Records | Superior Court of California | County of Orange','Family Law Records\nCalifornia Rules of Court prohibit viewing confidential records. Effective January 1, 2019 is the cited law date.','https://www.occourts.org/divisions/family-law/family-law-records','text/html')
        self.assertEqual(c['resource_type'],'court_information');self.assertEqual(c['legal_status'],'unknown')
    def test_word_boundary(self):
        self.assertEqual(classify_link('Court Information','https://court.example/information')['resource_type'],'court_information')
    def test_fee_waiver_form_packet_citing_rules_is_not_a_rule(self):
        result=classify('*Packet - Fee Waiver PDF Document','SELF-HELP FORM PACKET\nSUPERIOR COURT OF CALIFORNIA\nCOUNTY OF ORANGE\nCal. Rules of Court, rules 3.51, 8.26, and 8.818\nFW-001 Request to Waive Court Fees\nFill in court name and street address:\nCase Number:\nCase Name:','https://www.occourts.org/system/files/selfhelp/shc-fw-01.pdf','application/pdf')
        self.assertEqual(result['resource_type'],'court_form');self.assertEqual(result['document_shape'],'form_document')
    def test_incidental_rule_citation_in_pdf_is_not_rule_purpose(self):
        result=classify('Records access instructions','SUPERIOR COURT OF CALIFORNIA\nCOUNTY OF ORANGE\nCal. Rules of Court govern access to these records.','https://www.occourts.org/instructions.pdf','application/pdf')
        self.assertNotEqual(result['resource_type'],'local_rule')
    def test_generic_pdf_with_explicit_rule_caption_stays_rule(self):
        result=classify('Document','SUPERIOR COURT OF CALIFORNIA\nLOCAL RULES\nContents','https://court.example/document.pdf','application/pdf')
        self.assertEqual(result['resource_type'],'local_rule')
    def test_county_prefixed_rule_captions_are_explicit_rule_purpose(self):
        for caption in ['Asotin County District Court Local Rules','Jefferson County District Court Local Court Rules','Skagit County District and Municipal Court Local Rules 1']:
            with self.subTest(caption=caption):
                result=classify('Document',caption+'\nTable of Rules\n','https://www.courts.wa.gov/document.pdf','application/pdf')
                self.assertEqual(result['resource_type'],'local_rule')
    def test_official_wa_local_rule_pdf_path_preserves_rule_category(self):
        result=classify('Document','Enumclaw Municipal Court\nTable of Rules\nIntroduction/Adoption','https://www.courts.wa.gov/court_rules/pdf/LCR/17/MUN/Enumclaw/LCR_Enumclaw_MUN.pdf','application/pdf')
        self.assertEqual(result['resource_type'],'local_rule')
    def test_real_pilot_regressions(self):
        rows=[json.loads(x) for x in CHECKPOINT.read_text(encoding='utf8').splitlines()]
        expected={'/rules-court':('local_rule','rule_index'),'/l1018.pdf':('court_information','records_retention_guidance'),'/l1038.pdf':('court_form','form_document'),'/index.pdf':('local_rule','rule_index'),'/memo-local-rules.pdf':('local_rule','rule_change_notice')}
        for suffix,pair in expected.items():
            row=next(r for r in rows if r['source_url'].endswith(suffix));text=(ROOT/row['text_path']).read_text(encoding='utf8')
            title=(row.get('seed_provenance') or {}).get('anchor_text') if row['mime_type']=='application/pdf' else row['title']
            result=classify(title or row['title'],text,row['source_url'],row['mime_type'])
            with self.subTest(url=row['source_url']):self.assertEqual((result['resource_type'],result['document_shape']),pair)
        row=next(r for r in rows if r['source_url'].endswith('/09div4.pdf'))
        result=classify(row['title'],(ROOT/row['text_path']).read_text(encoding='utf8'),row['source_url'],row['mime_type'])
        self.assertEqual(result['legal_status'],'repealed_as_published')
    def test_navigation_and_cited_rule_are_not_body(self):
        result=classify('Rules of Court | Superior Court','See California Rule 10.855.\n[Division 10 - LOCAL EMERGENCY RULE 1](division10.pdf)\n'+'navigation '*400,'https://court.example/rules-court','text/html')
        self.assertEqual(result['document_shape'],'rule_index')
    def proposed_order_links(self):
        # Literal labels and hrefs in the saved Fourteenth Circuit landing page.
        return [
            {'label':"Click Here For Chief Judge Patterson's memo on Proposed Order Training",'url':'https://jud14.flcourts.org/uploaded/media/Memo-Submission%20of%20Proposed%20Orders%20to%20Judiciary.pdf'},
            {'label':'Attorney Instructions for Successful Submissions of Proposed Orders to Judiciary','url':'https://jud14.flcourts.org/uploaded/media/Attorney%20Instructions%20for%20Successful%20Submissions%20of%20Proposed%20Orders%20to%20Judiciary.pdf'}]
    def test_proposed_order_instruction_links_and_word_boundaries(self):
        for link in self.proposed_order_links():
            with self.subTest(label=link['label']):
                result=classify_link(link['label'],link['url'])
                self.assertEqual(result['resource_type'],'filing_guidance');self.assertTrue(result['eligible'])
        self.assertEqual(classify_link('',self.proposed_order_links()[0]['url'])['resource_type'],'filing_guidance')
        for label in ['Proposed Disorders Training','Unproposed Order Training','Proposed Order Retraining']:
            with self.subTest(label=label):self.assertFalse(classify_link(label,'https://example.org/resource.pdf')['eligible'])
    def test_link_only_guidance_landing_is_not_substantive(self):
        links=self.proposed_order_links()
        text='# E-Filing Proposed Orders\n\n'+'\n\n'.join('### '+r['label'] for r in links)+'\n\nJustice in Florida will be Accessible, Fair, Effective, Responsive, and Accountable.'
        result=classify('E-Filing Proposed Orders | Fourteenth Judicial Circuit',text,'https://jud14.flcourts.org/e-filing-proposed-orders','text/html',links)
        self.assertEqual(result['document_shape'],'filing_guidance_index');self.assertFalse(result['substantive'])
        self.assertEqual(len(result['evidence'][0]['linked_documents']),2)
    def test_short_actual_guidance_and_missing_link_evidence_stay_guide(self):
        links=self.proposed_order_links();title='E-Filing Proposed Orders | Fourteenth Judicial Circuit'
        bodies=['# E-Filing Proposed Orders\n\nSubmit proposed orders through the portal before 5 p.m.',
                '# E-Filing Proposed Orders\n\n'+links[0]['label']+'\n\nAttach the proposed order as a separate PDF.']
        for text in bodies:
            with self.subTest(text=text):
                result=classify(title,text,'https://jud14.flcourts.org/e-filing-proposed-orders','text/html',links)
                self.assertEqual(result['document_shape'],'guide');self.assertTrue(result['substantive'])
        result=classify(title,'# E-Filing Proposed Orders\n\n'+links[0]['label'],'https://jud14.flcourts.org/e-filing-proposed-orders','text/html',[])
        self.assertEqual(result['document_shape'],'guide')
    def test_linked_operative_instruction_is_still_guidance(self):
        label='Submit proposed orders through the portal before 5 p.m.'
        links=[{'label':label,'url':'https://court.example/proposed-order-submission.pdf'}]
        result=classify('E-Filing | Superior Court','# E-Filing\n\n'+label,'https://court.example/e-filing','text/html',links)
        self.assertEqual(result['document_shape'],'guide');self.assertTrue(result['substantive'])

if __name__=='__main__':unittest.main()
