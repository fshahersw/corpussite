import test from 'node:test';
import assert from 'node:assert/strict';
import {handleEnrichment} from './enrichment-api.mjs';
const resources=[{id:'one',title:'Shared court rule',state:'MI',resource_type:'rules'},{id:'two',title:'Different county',state:'MI',resource_type:'forms'}];
const edges=[{source:'county:26001',target:'url:a',relation:'listed_filing_source',scope:'statewide'}, {source:'url:a',target:'one',relation:'captured_as'}, {source:'county:26003',target:'two',relation:'explicit_county_reference'}, {source:'one',target:'mdl:1234',relation:'cites_mdl'}];
const data={available:true,resources,scopes:{'county:26001':['one'],'county:26003':['two'],'mdl:1234':['one']},graph_entities:[...new Set(edges.flatMap(e=>[e.source,e.target]))],summary:{},qualification:'Source links only'};
const nodes=[{id:'one'},{id:'url:a'},{id:'county:26001'}];
const ctx={dataset:async()=>({ready:true}),context:async k=>{
  if(k==='enrichment:index')return data;
  if(!k.startsWith('enrichment:graph:'))return null;
  const entity=k.slice('enrichment:graph:'.length),found=edges.filter(e=>e.source===entity||e.target===entity);
  return{available:true,entity,edges:found,nodes,total:found.length};
}};
test('county additions follow recorded source links without widening to every record in the state',async()=>{
  const result=await handleEnrichment('/api/enrichment',{county:'26001'},ctx);assert.deepEqual(result.items.map(r=>r.id),['one']);
  assert.equal((await handleEnrichment('/api/enrichment',{county:'99999'},ctx)).total,0);
});
test('graph requires an entity and preserves scope and relation evidence',async()=>{
  assert.equal((await handleEnrichment('/api/enrichment/graph',{},ctx)).total,0);
  const result=await handleEnrichment('/api/enrichment/graph',{entity:'county:26001'},ctx);assert.equal(result.edges[0].scope,'statewide');assert.equal(result.total,1);
});
test('held enrichment stays unavailable and unknown routes fall through',async()=>{
  const hidden=await handleEnrichment('/api/enrichment',{}, {dataset:async()=>({ready:false}),context:async()=>data});assert.equal(hidden.available,false);
  const hiddenContext=await handleEnrichment('/api/enrichment',{}, {dataset:async()=>({ready:true}),context:async()=>null});assert.equal(hiddenContext.available,false);
  assert.equal(await handleEnrichment('/api/not-enrichment',{},ctx),null);
});
test('MDL filters require exact identifiers, and file kind/identity is constrained',async()=>{
  assert.equal((await handleEnrichment('/api/enrichment',{mdl:'1234'},ctx)).total,1);
  assert.equal((await handleEnrichment('/api/enrichment',{mdl:'123'},ctx)).total,0);
  const invalid=await handleEnrichment('/api/enrichment/file',{id:'one',kind:'../../private'},ctx);assert.equal(invalid.status,404);
  const stale=await handleEnrichment('/api/enrichment/record',{id:'stale'},{...ctx,context:async key=>key==='enrichment:index'?data:{id:'stale',text:'old'}});assert.equal(stale.status,404);
});

test('listing and file lookup never request the full graph or an entity context',async()=>{
  const calls=[];const lean={...ctx,context:async key=>{calls.push(key);assert.equal(key,'enrichment:index');return data;},asset:async()=>new Response('original')};
  await handleEnrichment('/api/enrichment',{county:'26001'},lean);
  const file=await handleEnrichment('/api/enrichment/file',{id:'one'},lean);
  assert.equal(file.status,200);assert.equal(calls.length,2);
});

test('known held graph and reader are unavailable, unknown identifiers remain absent',async()=>{
  const partial={...ctx,context:async key=>key==='enrichment:index'?data:null};
  assert.equal((await handleEnrichment('/api/enrichment/graph',{entity:'county:26001'},partial)).available,false);
  const unknown=await handleEnrichment('/api/enrichment/graph',{entity:'county:99999'},partial);
  assert.equal(unknown.available,true);assert.equal(unknown.total,0);
  assert.equal((await handleEnrichment('/api/enrichment/record',{id:'one'},partial)).status,503);
});
