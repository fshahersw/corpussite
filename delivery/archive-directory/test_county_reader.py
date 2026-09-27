import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import county_reader

class CountyReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'saved.json'
        self.url = 'https://trellis.law/coverage/oklahoma/major'
    def view(self, body):
        self.path.write_text(json.dumps({'html': body}), encoding='utf-8')
        return county_reader.reading_view(self.url, self.path, hashlib.sha256(self.path.read_bytes()).hexdigest())
    def test_short_section_retained_without_navigation_or_case_links(self):
        result = self.view('<nav><a href="/case/123">Unrelated case</a> Pricing Select all</nav><div class="top-county-info-block__container"><h1>Major County District Courts Records</h1><div><h2>Population</h2><h3>7,527</h3></div><div><h2>County Seat</h2><h3>Fairview</h3></div></div>')
        self.assertEqual(result['county_profile']['fields'], {'population':'7,527','county_seat':'Fairview'})
        self.assertNotIn('Pricing',result['text']); self.assertEqual(result['links'],[])
    def test_heading_only_is_honest_empty_fields(self):
        result = self.view('<div class="top-county-info-block__container"><h1>Lowndes County Superior Courts Records</h1></div>')
        self.assertEqual(result['county_profile']['status'],'heading_only')
        self.assertEqual(result['county_profile']['fields'],{})
    def test_wrong_host_and_hash_do_not_supply_fields(self):
        self.assertIsNone(county_reader.reading_view('https://evil.test/coverage/a/b',self.path))
        self.path.write_text('changed')
        self.assertEqual(county_reader.reading_view(self.url,self.path,'0'*64)['text'],'')
    def test_only_known_fields_and_http_website(self):
        result = self.view('<div class="top-county-info-block__container"><h1>Major County District Courts Records</h1><div><h2>Website</h2><h3><a href="javascript:alert(1)">Unsafe</a></h3></div><div><h2>Featured cases</h2><h3>Case noise</h3></div></div>')
        self.assertIsNone(result['county_profile']['website_url']); self.assertNotIn('Case noise',result['text'])
    def test_explicit_courthouse_cards_retained_as_links(self):
        result=self.view('<div class="top-county-info-block__container"><h1>Orange County Superior Courts Records</h1><div class="county-coverage-info"><p>Saved court background.</p></div></div><div id="courthouses"><div class="courthouse-col-main"><a href="https://court.example.gov/location/central">Central Justice Center</a></div></div><div id="latest-cases"><a href="/case/123">Case result</a></div>')
        self.assertEqual(result['county_profile']['overview'],'Saved court background.')
        self.assertEqual(result['county_profile']['courthouses'][0]['name'],'Central Justice Center')
        self.assertEqual(result['county_profile']['courthouses'][0]['availability'],'source_link_only')
        self.assertNotIn('Case result',result['text'])
    def test_saved_browser_dom_representation_is_preserved(self):
        self.path.write_text(json.dumps({'source_url':self.url,'dom_profile_headings':[{'tag':'h1','text':'Major County District Courts Records'},{'tag':'h2','text':'COUNTY SEAT'},{'tag':'h3','text':'Fairview'},{'tag':'h2','text':'WEBSITE'},{'tag':'h3','text':'Truncated…','links':[{'url':'https://county.example.gov/'}]}]}),encoding='utf-8')
        result=county_reader.reading_view(self.url,self.path,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result['county_profile']['fields']['county_seat'],'Fairview')
        self.assertEqual(result['county_profile']['website_url'],'https://county.example.gov/')
        self.assertEqual(result['notes']['method'],'saved_rendered_dom_county_fields')

if __name__ == '__main__': unittest.main()
