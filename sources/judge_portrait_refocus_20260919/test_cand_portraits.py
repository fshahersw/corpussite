import io
import json
from pathlib import Path
import unittest
from PIL import Image
from cand_capture import ExactImageConfig, profile_evidence, HERE
from cand_finalize import BRIDGES, COURT, reviewed_match, validate_image, candidate_inventory
from finalize import ROOT, sha, rows

URL='https://cand.uscourts.gov/judges/je/example-jane'
IMAGE='https://cand.uscourts.gov/sites/default/files/Jane-Example.jpg'


def fixture(alt='Judge Jane Example'):
    return f'''<link rel="canonical" href="{URL}"><h1>District Judge Jane Example</h1>
    <article class="node node--type-cand-judge"><div class="field--name-field-judge-media-image">
    <img src="{IMAGE}" alt="{alt}"></div></article>'''.encode()


class PortraitBoundaryTests(unittest.TestCase):
    def test_exact_individual_picture_context(self):
        p=profile_evidence(fixture(),URL)
        self.assertEqual(p['image_source_url'],IMAGE)
        self.assertIsNone(p['entity_id'])

    def test_decorative_gavel_is_rejected(self):
        with self.assertRaises(ValueError): profile_evidence(fixture('Gavel Photo'),URL)

    def test_wrong_page_identity_and_foreign_host_rejected(self):
        with self.assertRaises(ValueError): profile_evidence(fixture(),URL+'-other')
        with self.assertRaises(ValueError): profile_evidence(fixture(),'https://fake.example/judges/je/example-jane')

    def test_config_only_frozen_url_not_nearby_images(self):
        cfg=ExactImageConfig.from_dict({'allow':[{'host':'cand.uscourts.gov','path_prefixes':['/sites/default/files/']}]})
        cfg.exact_urls=frozenset([IMAGE])
        self.assertTrue(cfg.allowed(IMAGE)[0])
        self.assertFalse(cfg.allowed(IMAGE+'?unobserved=1')[0])
        self.assertFalse(cfg.allowed(IMAGE.replace('Jane','Other'))[0])
        self.assertTrue(cfg.respect_robots)
        self.assertTrue(cfg.pause_host_on_access_block)

    def test_name_and_court_without_biofacts_cannot_match(self):
        p={'name':'Jane Example','biography':'CollegeA1980Commission2000'}
        e={'entity_id':'one','name':'Jane Example','courts':[COURT],'education':['Different College'],'appointments':[]}
        bridge=('one',[('education','CollegeA1980','education','College A,1980'),('commission','Commission2000','appointments','Commission2000')])
        with self.assertRaises(ValueError): reviewed_match(p,[e],bridge)

    def test_ambiguous_corroborating_entities_cannot_match(self):
        p={'name':'Jane Example','biography':'CollegeA1980 Commission2000'}
        e={'entity_id':'one','name':'Jane Example','courts':[COURT],'education':['CollegeA1980'],'appointments':['Commission2000']}
        bridge=('one',[('education','CollegeA1980','education','CollegeA1980'),('commission','Commission2000','appointments','Commission2000')])
        with self.assertRaises(ValueError): reviewed_match(p,[e,{**e,'entity_id':'two'}],bridge)

    def test_invalid_or_hash_mismatched_image_rejected(self):
        def receipt(data): return {'status':'downloaded','http_status':200,'raw_complete':True,'requested_url':IMAGE,'sha256':sha(data),'byte_count':len(data)}
        data=b'<html>not a portrait</html>'
        record={'url':IMAGE,'status':'downloaded','sha256':sha(data)}
        with self.assertRaises(ValueError): validate_image(data,record,receipt(data))
        b=io.BytesIO();Image.new('RGB',(80,100)).save(b,format='PNG');data=b.getvalue()
        r=receipt(data);record['sha256']=sha(data)
        self.assertEqual(validate_image(data,record,r)['mime'],'image/png')
        with self.assertRaises(ValueError): validate_image(data+b'x',record,r)

    def test_candidate_search_does_not_promote_namesake(self):
        result=candidate_inventory({'name':'Jane Example'},[{'entity_id':'other','name':'Jane A. Example','aliases':[],'courts':['Other Court']}])
        self.assertEqual(result['candidate_count'],1)
        self.assertEqual(result['same_court_candidate_count'],0)
        self.assertTrue(result['not_an_identity_merge'])

    def test_package_originals_and_identity_evidence_are_bound(self):
        images=rows(HERE/'images.jsonl'); accepted=rows(HERE/'accepted_links.jsonl')
        self.assertEqual(len(images),15);self.assertEqual(len(accepted),6)
        for image in images:
            data=(ROOT/image['path']).read_bytes()
            self.assertEqual(sha(data),image['sha256']);self.assertEqual(len(data),image['bytes'])
        for link in accepted:
            evidence=(ROOT/link['identity_evidence_path']).read_bytes()
            self.assertEqual(sha(evidence),link['identity_evidence_sha256'])
            proof=json.loads(evidence)
            self.assertEqual(proof['entity_id'],link['entity_id'])
            self.assertGreaterEqual(len(proof['facts']),2)
            self.assertFalse(proof['facial_matching'])


if __name__=='__main__': unittest.main()
