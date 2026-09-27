"""Projection contracts and optional parity against the actual dated local bundle.

No source files, publication gates, exports, or remote services are modified.
"""
import copy
import importlib.util
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('dated_enrichment_exporter', Path(__file__).with_name('enrichment.py'))
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)
native = exporter.enrichment


class ProjectionTests(unittest.TestCase):
    def fixture(self):
        records = [
            {'id':'shared','title':'Shared rules','state':'MI','category':'rules','document_shape':'body','original_url':'/api/enrichment/file?id=shared',
             'source_url':'https://court.gov/rules','source_path':'C:/private/raw.pdf','text':'Not for the small index'},
            {'id':'other','title':'Other county','state':'MI','resource_type':'forms'},
            {'id':'mdl-order','title':'Transfer order','mdl_number':'1234','resource_type':'transfer_order'},
        ]
        edges = [
            {'source':'county:26001','target':'url:rules','relation':'listed_filing_source','scope':'statewide',
             'evidence':{'quote':'Statewide rules','source_url':'https://court.gov/rules','source_path':'C:/private/raw.pdf'}},
            {'source':'url:rules','target':'shared','relation':'captured_as'},
            {'source':'county:26003','target':'other','relation':'source_names_county'},
            {'source':'shared','target':'mdl:1234','relation':'cites_mdl'},
        ]
        ids = {e[k] for e in edges for k in ('source','target')}
        return {'available':True,'generated_at':'2026-09-27','summary':{'resources':3},'qualification':'Evidence only',
                'resources':records,'edges':edges,'nodes':[{'id':ident,'label':ident,'metadata_path':'/tmp/private'} for ident in sorted(ids)]}

    def test_index_keeps_reader_identity_without_text_graph_or_private_paths(self):
        data = self.fixture(); before = copy.deepcopy(data)
        index, graphs = exporter.projection(data)
        self.assertNotIn('edges',index); self.assertNotIn('nodes',index)
        self.assertEqual(set(index['graph_entities']),set(graphs),'Known graph identities must remain distinguishable from missing publication contexts')
        self.assertEqual([r['id'] for r in index['resources']],['shared','other','mdl-order'])
        self.assertEqual(index['resources'][0]['original_url'],'/api/enrichment/file?id=shared')
        encoded = json.dumps(index)
        self.assertNotIn('Not for the small index',encoded)
        self.assertNotIn('C:/private',encoded); self.assertNotIn('source_path',encoded)
        self.assertEqual(data,before,'Projection must not mutate the source bundle')

    def test_scope_keeps_explicit_county_links_and_exact_mdl_identifiers(self):
        index, _ = exporter.projection(self.fixture())
        self.assertEqual(index['scopes']['county:26001'],['shared'])
        self.assertEqual(index['scopes']['county:26003'],['other'])
        self.assertEqual(index['scopes']['mdl:1234'],['mdl-order','shared'])
        self.assertNotIn('mdl:123',index['scopes'])

    def test_graph_preserves_evidence_and_removes_nested_private_locations(self):
        _, graphs = exporter.projection(self.fixture())
        graph = graphs['county:26001']
        self.assertEqual(graph['edges'][0]['scope'],'statewide')
        self.assertEqual(graph['edges'][0]['evidence']['quote'],'Statewide rules')
        self.assertEqual({n['id'] for n in graph['nodes']},{'county:26001','url:rules'})
        encoded=json.dumps(graph)
        for private in ('C:/private','/tmp/private','source_path','metadata_path'):
            self.assertNotIn(private,encoded)

    def test_high_degree_graph_is_bounded_without_losing_true_total_or_edge_order(self):
        data=self.fixture()
        data['edges']=[{'source':'state:MI','target':f'node:{i}','relation':'contains','evidence':{'quote':str(i)}} for i in range(650)]
        data['nodes']=[{'id':ident,'label':ident} for ident in ['state:MI',*(f'node:{i}' for i in range(650))]]
        _, graphs=exporter.projection(data); graph=graphs['state:MI']
        self.assertEqual(graph['total'],650); self.assertEqual(len(graph['edges']),500)
        self.assertEqual([e['evidence']['quote'] for e in graph['edges']],[str(i) for i in range(500)])
        self.assertEqual(len(graph['nodes']),501)
        self.assertNotIn('node:500',{n['id'] for n in graph['nodes']})

    def test_self_edge_is_counted_once(self):
        data=self.fixture(); data['edges']=[{'source':'shared','target':'shared','relation':'links_to'}]
        _, graphs=exporter.projection(data)
        self.assertEqual(graphs['shared']['total'],1)


@unittest.skipUnless((ROOT/'sources/gap_enrichment_20260927/bundle.json').exists(),'Dated local corpus is not checked into the code repository')
class ActualBundleParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=json.loads((ROOT/'sources/gap_enrichment_20260927/bundle.json').read_text(encoding='utf-8'))
        cls.index,cls.graphs=exporter.projection(cls.bundle)

    def test_small_index_and_native_rule_section_parent_integrity(self):
        self.assertLess(len(json.dumps(self.index,ensure_ascii=False,separators=(',',':')).encode()),2_000_000)
        self.assertEqual(len(self.index['resources']),len(self.bundle['resources']))
        ids={r['id'] for r in self.bundle['resources']}
        sections=[r for r in self.bundle['resources'] if r.get('lane')=='rule_sections']
        self.assertEqual(len(sections),689)
        self.assertEqual(len({r['native_id'] for r in sections}),689)
        self.assertEqual(len({r['raw_sha256'] for r in sections}),2)
        for r in self.bundle['resources']:
            if r.get('parent_compilation_id'):self.assertIn(r['parent_compilation_id'],ids)
        for r in sections:
            self.assertEqual(r['document_shape'],'section'); self.assertTrue(r.get('source_page'))

    def test_hosted_projection_matches_native_listing_and_graph_contracts(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node runtime unavailable')
        queries=[{}, {'state':'MI'}, {'county':'26163'}, {'county':'26001'}, {'county':'99999'},
                 {'mdl':'2873'}, {'mdl':'2996'}, {'state':'OK'}, {'lane':'rule_sections'},
                 {'q':'MCR 1.101'}, {'document_shape':'section'}, {'state':'MI','page':'2'}]
        graph_queries=[{'entity':entity,'limit':str(limit)} for entity in ('state:MI','county:26163','mdl:2873','unknown:identifier') for limit in (1,100,500)]
        context={'enrichment:index':self.index}
        for q in graph_queries:
            if q['entity'] in self.graphs:context['enrichment:graph:'+q['entity']]=self.graphs[q['entity']]
        script="""
import fs from 'node:fs';import {handleEnrichment} from './deploy/enrichment-api.mjs';
const {context,queries,graph_queries}=JSON.parse(fs.readFileSync(0,'utf8'));
const ctx={dataset:async()=>({ready:true}),context:async key=>context[key]??null};
const lists=[];for(const q of queries){const r=await handleEnrichment('/api/enrichment',q,ctx);lists.push({total:r.total,ids:r.items.map(x=>x.id)});}
const graphs=[];for(const q of graph_queries){const r=await handleEnrichment('/api/enrichment/graph',q,ctx);graphs.push({total:r.total,edges:r.edges,nodes:r.nodes,truncated:!!r.truncated});}
process.stdout.write(JSON.stringify({lists,graphs}));
"""
        result=subprocess.run([node,'--input-type=module','-e',script],input=json.dumps({'context':context,'queries':queries,'graph_queries':graph_queries}),text=True,encoding='utf-8',cwd=ROOT,capture_output=True,check=True)
        hosted=json.loads(result.stdout)
        with patch.object(native,'state',return_value=self.bundle):
            for q,actual in zip(queries,hosted['lists']):
                local=native.listing(q)
                self.assertEqual(actual,{'total':local['total'],'ids':[r['id'] for r in local['items']]},q)
            for q,actual in zip(graph_queries,hosted['graphs']):
                local=native.graph(q)
                self.assertEqual(actual['total'],local['total'],q)
                self.assertEqual(actual['edges'],local['edges'],q)
                self.assertEqual({n['id']:n for n in actual['nodes']},{n['id']:n for n in local['nodes']},q)
                self.assertEqual(actual['truncated'],local['truncated'],q)


if __name__=='__main__':unittest.main(verbosity=2)
