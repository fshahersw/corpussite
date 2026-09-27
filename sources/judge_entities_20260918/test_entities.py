"""Identity fixtures: false merges, supported variants, and display preservation."""
from pathlib import Path
import importlib.util,unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('judge_entity_builder',ROOT/'scripts/build_judge_entities.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def observation(key,name='Alex Q. Smith',court='U.S. District Court for the District of Alaska',state='AK',profile=None,education=None,biography=None,existing=None,source='trellis_profile',contacts=None,birth=None):
    p={'name':name,'state_code':state,'courts':[court] if court else [],'judge_system':'federal','source_url':profile or 'https://example.test/roster','native_record':{'education':education or [],'biography':biography,'professional_contacts':contacts or []}}
    if profile:p['native_record']['profile_url']=profile
    if birth:p['native_record']['dob']=birth
    o={'member_key':key,'dataset':'fixture','source_observation_id':key,'existing_judge_id':existing,'name':name,'source_class':source,'judge_system':'federal','payload':p,'source_anchor':{'fixture':True}}
    o['features']=m.feature(p);return o

EDU=[{'institution':'Sample University','degree_year':'1990'},{'institution':'Sample Law School','degree_year':'1993'}]

class IdentityTests(unittest.TestCase):
    def test_name_only_never_merges(self):
        a=observation('a');b=observation('b',court=None)
        self.assertFalse(m.decide(a,b)[0])
    def test_same_name_court_without_corrob_does_not_merge(self):
        self.assertFalse(m.decide(observation('a'),observation('b'))[0])
    def test_identical_profile_url_can_link_initial_variant(self):
        a=observation('a',name='A. Quincy Smith',profile='https://trellis.law/judge/a.quincy.smith')
        b=observation('b',name='Alex Quincy Smith',profile='https://trellis.law/judge/a.quincy.smith')
        self.assertTrue(m.decide(a,b)[0])
    def test_same_name_different_court_kept_separate(self):
        a=observation('a',education=EDU);b=observation('b',court='U.S. District Court for the District of Oregon',state='OR',education=EDU)
        self.assertFalse(m.decide(a,b)[0])
    def test_middle_initial_requires_support(self):
        a=observation('a',name='Alex Q. Smith');b=observation('b',name='Alex Quincy Smith')
        self.assertFalse(m.decide(a,b)[0])
        a=observation('a',name='Alex Q. Smith',education=EDU);b=observation('b',name='Alex Quincy Smith',education=EDU)
        self.assertTrue(m.decide(a,b)[0])
    def test_suffix_and_dob_conflicts_block(self):
        self.assertFalse(m.decide(observation('a',name='Alex Q. Smith Jr.',education=EDU),observation('b',education=EDU))[0])
        self.assertFalse(m.decide(observation('a',education=EDU,birth='1950-01-01'),observation('b',education=EDU,birth='1980-01-01'))[0])
    def test_competing_profile_ids_block(self):
        a=observation('a',profile='https://trellis.law/judge/alex.q.smith',education=EDU)
        b=observation('b',profile='https://trellis.law/judge/alex.q.smith.2',education=EDU)
        self.assertFalse(m.decide(a,b)[0])
    def test_missing_middle_name_not_implicitly_filled(self):
        self.assertFalse(m.compatible_names('Alex Smith','Alex Q. Smith'))
    def test_structural_federal_court_alias(self):
        self.assertEqual(m.court_key('United States District Court, California Southern'),m.court_key('CA - U.S. District Court for the Southern District of California'))
        self.assertNotEqual(m.court_key('United States District Court, California Southern'),m.court_key('U.S. District Court for the Northern District of California'))
    def test_generic_county_court_needs_same_county(self):
        a=observation('a',court='County Court at Law',education=EDU);b=observation('b',court='County Court at Law',education=EDU)
        self.assertFalse(m.decide(a,b)[0])
    def test_no_transitive_middle_initial_bridge(self):
        a=observation('a',name='Alex Quincy Smith',education=EDU)
        b=observation('b',name='Alex Q. Smith',education=EDU)
        c=observation('c',name='Alex Quentin Smith',education=EDU)
        groups,mapping,decisions=m.resolve([a,b,c])
        self.assertEqual(len(groups),2)
        self.assertNotEqual(mapping['a'],mapping['c'])
        self.assertTrue(any(d['basis']==['would_bridge_conflicting_group_members'] for d in decisions))
    def test_existing_group_preserved(self):
        a=observation('a',existing='verified1');b=observation('b',existing='verified1')
        groups,_,decisions=m.resolve([a,b]);self.assertEqual(len(groups),1)
        self.assertEqual(decisions[0]['decision'],'linked_existing_validated_group')
    def test_rich_profile_preferred_and_claims_retained(self):
        a=observation('a',existing='verified',source='trellis_directory')
        b=observation('b',existing='verified',biography='Alex Smith served in the named court. Source current service is unverified.',education=EDU)
        groups,mapping,decisions=m.resolve([a,b])
        facts=[{'member_key':'a','dataset':'fixture','original_id':'f1'},{'member_key':'b','dataset':'fixture','original_id':'f2'}]
        analyses=[{'member_key':'a','dataset':'fixture','original_id':'a1','payload':{'analysis_id':'a1','value':1,'period':{'year':2024},'outcome':'granted','limitations':['Reported cases; no inferred rate'],'custom_measure_context':'Preserve publisher context'},'input_evidence':{'fixture':True}}]
        entities,members=m.create_entities([a,b],groups,mapping,decisions,facts,analyses)
        entity=entities[0];self.assertEqual(entity['best_profile_member'],'b')
        self.assertFalse(entity['profile_text'].startswith('{'));self.assertIn('Alex Smith served',entity['profile_text'])
        self.assertEqual(len(entity['fact_ids']),2);self.assertEqual(len(entity['analysis_ids']),1)
        self.assertEqual(entity['analyses'][0]['source_observation_id'],'a')
        self.assertEqual(entity['analyses'][0]['outcome'],'granted')
        self.assertEqual(entity['analyses'][0]['limitations'],['Reported cases; no inferred rate'])
        self.assertEqual(entity['analyses'][0]['custom_measure_context'],'Preserve publisher context')
        self.assertFalse(entity['current_service_verified'])
        self.assertEqual(len(members),2)

if __name__=='__main__':unittest.main(verbosity=2)
