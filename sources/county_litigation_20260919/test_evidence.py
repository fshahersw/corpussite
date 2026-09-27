import hashlib,tempfile,unittest
from pathlib import Path
import build

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        build._MATCHERS=None
        self.inventory=[{'name':'Orange County','geoid':'06059','state':'California','usps':'CA'},
                        {'name':'Orange County','geoid':'12095','state':'Florida','usps':'FL'},
                        {'name':'King County','geoid':'53033','state':'Washington','usps':'WA'}]
    def test_old_context_cannot_supply_county(self):
        county,proof=build.geography('Court documents','Superior Court forms', [{'jurisdiction':{'geoid':'12095'}}],self.inventory,{},'https://courts.example/forms')
        self.assertIsNone(county);self.assertEqual(proof['status'],'unresolved')
    def test_explicit_state_and_county(self):
        county,proof=build.geography('Superior Court of California, County of Orange','Forms',[],self.inventory,{},'https://courts.example/forms')
        self.assertEqual(county['geoid'],'06059');self.assertTrue(proof['evidence'])
    def test_postal_code_in_county_title_is_explicit_but_not_territory(self):
        county,proof=build.geography('Orange County CA Government','Public information',[],self.inventory,{},'https://government.example')
        self.assertEqual(county['geoid'],'06059');self.assertEqual(proof['level'],'unknown');self.assertIn('CA',proof['evidence'][0]['state_excerpt'])
    def test_postal_address_can_disambiguate_exact_county_name(self):
        county,proof=build.geography('Superior Court of Orange County','Contact: Santa Ana, CA 92701',[],self.inventory,{},'https://court.example')
        self.assertEqual(county['geoid'],'06059');self.assertIn('CA 92701',proof['evidence'][0]['state_excerpt'])
    def test_incidental_order_table_county_does_not_identify_whole_index(self):
        county,proof=build.geography('Administrative Court Orders | Fourteenth Judicial Circuit','Florida Courts\nTitle | Description | Court\nRescinding Orange County Administrative Order\n| Orange County Court |',[],self.inventory,{},'https://court.example/orders',principal_text='Administrative Court Orders')
        self.assertIsNone(county)
    def test_incidental_body_county_not_principal_identity(self):
        county,proof=build.geography('E-filing Instructions | Regional Court','Florida guidance\nProcedure for Submitting Proposed Orders in Orange County\nFollow the portal instructions.',[],self.inventory,{},'https://court.example/filing')
        self.assertIsNone(county)
    def test_shared_website_does_not_establish_county(self):
        url='https://government.example/'
        county,proof=build.geography('Government','County links',[],self.inventory,{build.normurl(url):self.inventory[:2]},url)
        self.assertIsNone(county)
    def test_exact_website_is_discovery_only(self):
        url='https://government.example/'
        county,proof=build.geography('Government','County links',[],self.inventory,{build.normurl(url):self.inventory[:1]},url)
        self.assertEqual(county['geoid'],'06059');self.assertEqual(proof['level'],'unknown');self.assertIsNone(proof['county_fips'])
    def test_multi_county_opening_stays_unknown(self):
        county,_=build.geography('Superior Court','Orange County, California and King County, Washington',[],self.inventory,{},'https://court.example')
        self.assertIsNone(county)
    def test_readbound_rejects_changed_hash_and_outside_path(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source.txt';source.write_bytes(b'evidence')
            digest=hashlib.sha256(b'evidence').hexdigest()
            self.assertEqual(build.readbound(source,digest,root),b'evidence')
            source.write_bytes(b'changed')
            with self.assertRaises(ValueError):build.readbound(source,digest,root)
            inside=root/'inside';inside.mkdir()
            with self.assertRaises(ValueError):build.readbound(source,hashlib.sha256(b'changed').hexdigest(),inside)

if __name__=='__main__':unittest.main()
