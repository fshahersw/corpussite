import unittest
from readable import html_body,reading_view,plain_profile

class ReadingChecks(unittest.TestCase):
    def test_legal_body_and_numbers_survive_navigation_cleanup(self):
        source='<html><nav>Home Sign in <a href="/ads">Upgrade</a></nav><main><h1>Rule 12</h1><p>(a) A party must respond within 21 days.</p><p>§ 120. The amount is $5,000.</p><footer>Cookies</footer></main></html>'
        body,links,notes=html_body(source,'https://example.gov')
        self.assertIn('21 days',body);self.assertIn('§ 120.',body);self.assertIn('$5,000',body)
        self.assertNotIn('Sign in',body);self.assertNotIn('Cookies',body);self.assertTrue(notes['semantic_main_selected'])
    def test_legal_table_of_contents_is_not_discarded(self):
        source='<main><h1>Rules</h1><ul>'+''.join(f'<li><a href="/{i}">Rule {i}: Procedure</a></li>' for i in range(1,12))+'</ul></main>'
        body,_,_=html_body(source,'https://example.gov');self.assertIn('Rule 11: Procedure',body)
    def test_json_metadata_does_not_become_document_prose(self):
        result=reading_view('{"capture_kind":"directory","http_status":200}')
        self.assertEqual(result['text'],'');self.assertEqual(result['notes']['method'],'metadata_without_readable_body')
    def test_markdown_links_become_readable_and_remain_traceable(self):
        result=reading_view('# Rule 1\n\nRead [Rule 2](https://example.gov/rule2).\n\nSign in')
        self.assertIn('Read Rule 2.',result['text']);self.assertNotIn('Sign in',result['text']);self.assertEqual(result['links'][0]['url'],'https://example.gov/rule2')
    def test_profile_is_plain_language_with_unknown_status(self):
        body=plain_profile({'name':'Jane Example','courts':['First District'],'native_record':{'education':[{'institution':'Example University','degree':'JD'}]}})
        self.assertIn('Jane Example',body);self.assertIn('Example University',body);self.assertNotIn('"native_record"',body)

if __name__=='__main__':unittest.main(verbosity=2)
