import test from 'node:test';
import assert from 'node:assert/strict';
import {handleEnrichment} from './enrichment-api.mjs';
const resources=[{id:'one',title:'Shared court rule',state:'MI',resource_type:'rules'},{id:'two',title:'Different county',state:'MI',resource_type:'forms'}];
const edges=[{source:'county:26001',target:'url:a',relation:'listed_filing_source',scope:'statewide'}, {source:'url:a',target:'one',relation:'captured_as'}, {source:'county:26003',target:'two',relation:'explicit_county_reference'}, {source:'one',target:'mdl:1234',relation:'cites_mdl'}];
const data={available:true,resources,scopes:{'county:26001':['one'],'county:26003':['two'],'mdl:1234':['one']},graph_entities:[...new Set(edges.flatMap(e=>[e.source,e.target]))],summary:{},qualification:'Source links only'};
const nodes=[{id:'one'},{id:'url:a'},{id:'county:26001'}];
const ctx={dataset:async id=>id==='gap_enrichment_20260927'?{ready:true}:null,context:async k=>{
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

function relatedFixture(extraEdges=[]) {
  const records=[
    {id:'addition:a',title:'Rule 1',state:'MI',document_shape:'section'},
    {id:'addition:b',title:'Rule 2',state:'MI',document_shape:'section'},
    {id:'addition:old',title:'Rule 1',state:'MI',captured_at:'2025-01-01'},
    {id:'addition:unrelated',title:'Rule 1',state:'MI'}
  ];
  const evidence={source_url:'https://court.gov/rules.pdf',source_sha256:'a'.repeat(64),quote:'Rule 1'};
  const relations=[
    {source:'addition:a',target:'url:rules',relation:'excerpt_of',evidence},
    {source:'addition:b',target:'url:rules',relation:'excerpt_of',evidence},
    {source:'addition:a',target:'rule:MCR_1',relation:'has_native_identifier',evidence},
    {source:'addition:old',target:'rule:MCR_1',relation:'has_native_identifier',evidence},
    {source:'county:26001',target:'state:MI',relation:'in_state'},
    ...extraEdges
  ];
  const index={...data,resources:records,graph_entities:[...new Set(relations.flatMap(e=>[e.source,e.target]))]};
  const calls=[];
  return {calls,index,ctx:{dataset:async id=>id==='gap_enrichment_20260927'?{ready:true}:null,context:async key=>{
    calls.push(key);if(key==='enrichment:index')return index;
    const entity=key.slice('enrichment:graph:'.length),edges=relations.filter(e=>e.source===entity||e.target===entity);
    return {available:true,entity,total:edges.length,edges:edges.slice(0,500),nodes:[]};
  }}};
}

test('related readers retain exact evidence paths and versions without title/state similarity',async()=>{
  const fixture=relatedFixture();
  const result=await handleEnrichment('/api/enrichment/graph',{entity:'addition:a',related:'1',limit:'1'},fixture.ctx);
  const related=result.related;
  assert.equal(result.edges.length,1); // walk uses the full bounded graph, not the display limit
  assert.equal(related.matched,2);assert.equal(related.incomplete,false);
  assert.deepEqual(related.clusters.map(g=>g.key),['identifier','source']);
  const old=related.clusters[0].items[0];assert.equal(old.id,'addition:old');assert.equal(old.captured_at,'2025-01-01');
  assert.equal(old.evidence_paths[0].length,2);
  assert.equal(old.evidence_paths[0][1].evidence.source_sha256,'a'.repeat(64));
  assert.ok(!JSON.stringify(related).includes('addition:unrelated'));
  assert.ok(!fixture.calls.some(key=>key==='enrichment:graph:state:MI'));
});

test('related walks are opt-in, bounded and report missing published graphs',async()=>{
  const fixture=relatedFixture(Array.from({length:12},(_,i)=>({source:'addition:a',target:'county:'+i,relation:'source_names_county'})));
  await handleEnrichment('/api/enrichment/graph',{entity:'addition:a'},fixture.ctx);
  assert.equal(fixture.calls.length,2);
  fixture.calls.length=0;
  const original=fixture.ctx.context;
  fixture.ctx.context=async key=>key==='enrichment:graph:county:0'?null:original(key);
  const result=await handleEnrichment('/api/enrichment/graph',{entity:'addition:a',related:'1'},fixture.ctx);
  assert.equal(result.related.walk.visited_bridges,8);assert.equal(result.related.incomplete,true);
  assert.deepEqual(result.related.pending_entities,['county:0']);
  assert.ok(fixture.calls.length<=10);
});

test('county source path retains statewide scope without expanding every county in the state',async()=>{
  const result=await handleEnrichment('/api/enrichment/graph',{entity:'county:26001',related:'1'},ctx);
  assert.equal(result.related.matched,1);
  const item=result.related.clusters[0].items[0];assert.equal(item.id,'one');
  assert.equal(item.evidence_paths[0][0].scope,'statewide');
  assert.equal(result.related.clusters[0].key,'listed_source');
});

function optionalCollection({held=false,missingIndex=false,missingGraph=false,collision=false,indexError=false,duplicateBaseEdge=false}={}){
  const resource={id:collision?'one':'county-gap:new',title:'New official county rules',state:'WI',source_url:'https://court.gov/local.pdf'};
  const extraEdge={id:'new-edge',source:resource.id,target:'county:26001',relation:'source_names_county',evidence:{quote:'Named county',source_url:resource.source_url}};
  const extra={available:true,resources:[resource],scopes:{'county:26001':[resource.id]},graph_entities:['county:26001',resource.id],summary:{original_documents:1,graph_edges:1}};
  const routes=[],calls=[];
  return {resource,routes,calls,ctx:{dataset:async id=>id==='county_enrichment_20260928'?{ready:!held}:{ready:true},
    context:async key=>{
      calls.push(key);
      if(key==='county-enrichment-20260928:index'){if(indexError)throw Error('Context hash mismatch');return missingIndex?null:extra;}
      if(key==='county-enrichment-20260928:record:'+resource.id)return {...resource,text:'Saved county body'};
      if(key.startsWith('county-enrichment-20260928:graph:')){
        if(missingGraph)return null;
        const graphEdges=duplicateBaseEdge?[edges[0],extraEdge]:[extraEdge];
        return {available:true,entity:key.slice('county-enrichment-20260928:graph:'.length),edges:graphEdges,nodes:[{id:resource.id,resource_id:resource.id},{id:'county:26001'}],total:graphEdges.length};
      }
      return ctx.context(key);
    },asset:async route=>{routes.push(route);return new Response('verified original');}}
  };
}

test('optional collection merges exact county scopes and dispatches reader and original by origin',async()=>{
  const fixture=optionalCollection();
  const listing=await handleEnrichment('/api/enrichment',{county:'26001'},fixture.ctx);
  assert.deepEqual(listing.items.map(r=>r.id).sort(),['county-gap:new','one']);
  const detail=await handleEnrichment('/api/enrichment/record',{id:fixture.resource.id},fixture.ctx);
  assert.equal(detail.text,'Saved county body');
  assert.ok(fixture.calls.includes('county-enrichment-20260928:record:county-gap:new'));
  const file=await handleEnrichment('/api/enrichment/file',{id:fixture.resource.id,kind:'original'},fixture.ctx);
  assert.equal(file.status,200);assert.equal(fixture.routes[0],'/api/enrichment/file?id=county-gap:new&kind=original');
  const graph=await handleEnrichment('/api/enrichment/graph',{entity:'county:26001',related:'1'},fixture.ctx);
  assert.equal(graph.total,2);assert.equal(graph.related.matched,2);
});

test('held or malformed optional collection preserves base readers and honestly holds optional IDs',async()=>{
  for(const options of [{held:true},{missingIndex:true},{collision:true},{indexError:true}]){
    const fixture=optionalCollection(options);
    const listing=await handleEnrichment('/api/enrichment',{county:'26001'},fixture.ctx);
    assert.deepEqual(listing.items.map(r=>r.id),['one']);
    assert.deepEqual(listing.pending_collections,['county_enrichment_20260928']);
    assert.equal((await handleEnrichment('/api/enrichment/record',{id:'county-gap:pending'},fixture.ctx)).status,503);
    assert.equal((await handleEnrichment('/api/enrichment/file',{id:'county-gap:pending'},fixture.ctx)).status,503);
  }
});

test('missing optional graph does not discard published base evidence and reports partial results',async()=>{
  const fixture=optionalCollection({missingGraph:true});
  const graph=await handleEnrichment('/api/enrichment/graph',{entity:'county:26001',related:'1'},fixture.ctx);
  assert.equal(graph.available,true);assert.equal(graph.edges.length,1);assert.equal(graph.related.matched,1);
  assert.equal(graph.related.incomplete,true);assert.deepEqual(graph.pending_collections,['county_enrichment_20260928']);
});

test('shared graph edges and nodes are deduplicated while unrelated collection records stay separate',async()=>{
  const fixture=optionalCollection({duplicateBaseEdge:true});
  const graph=await handleEnrichment('/api/enrichment/graph',{entity:'county:26001'},fixture.ctx);
  assert.equal(graph.total,2);assert.equal(graph.edges.length,2);
  assert.equal(graph.nodes.filter(n=>n.id==='county:26001').length,1);
  const listing=await handleEnrichment('/api/enrichment',{},fixture.ctx);
  assert.equal(listing.total,3);assert.deepEqual(listing.summary.by_state,{MI:2,WI:1});
});
