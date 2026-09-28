import test from 'node:test';
import assert from 'node:assert/strict';
import {aliases,handleGeneric,queryOptions} from './generic-api.mjs';

const data=(id,metadata={})=>({id,ready:true,metadata:{listing:{available:true,total:8,limit:25,filters:[{name:'q'},{name:'state'},{name:'court'},{name:'dfrom'},{name:'dto'}],columns:[{key:'title'}]},...metadata}});

test('all 34 canonical adapters and 65 aliases are registered',()=>{
  assert.equal(new Set(Object.values(aliases)).size,34);
  assert.equal(Object.keys(aliases).length,65);
});

const courtListing=(total,options)=>({listing:{available:true,total,limit:25,qualification:'Primary scope.',filters:[{name:'q'},{name:'doc_type',options}],columns:[{key:'document'}]}});
function courtContext(addition,others={}) {
  const calls={};
  const context={
    dataset:async id=>id==='court_documents'
      ? {id,ready:true,expected_records:3,imported_records:3,metadata:courtListing(3,[{value:'court_form',label:'Court form',count:2},{value:'local_rule',label:'Local rule',count:1}])}
      : id==='court_forms_expansion_20260912' ? addition : others[id] ?? null,
    query:async q=>(calls.query=q,{total:7,items:[{id:'court-forms-20260912:abc'}]}),
    detail:async(...args)=>(calls.detail=args,{title:'Form',facts:[],sections:[]}),
  };
  return {calls,context};
}
const addition=(overrides={})=>({id:'court_forms_expansion_20260912',ready:true,expected_records:4,imported_records:4,
  metadata:{listing:{qualification:'Official court forms (retrieved 2026-09-12).',filters:[{name:'doc_type',options:[{value:'court_form',label:'Court form',count:3},{value:'supporting_material',label:'Instructions / supporting material',count:1}]}]}},...overrides});

test('an unpublished or incomplete addition never joins its host area',async()=>{
  for (const held of [{ready:false},addition({ready:false}),addition({imported_records:3}),null]) {
    const {calls,context}=courtContext(held);
    const result=await handleGeneric('/api/area/court-documents',{},context);
    assert.deepEqual(calls.query.datasets,['court_documents']);
    assert.deepEqual(result.filters[1].options.map(o=>[o.value,o.count]),[['court_form',2],['local_rule',1]]);
    assert.equal(result.qualification,'Primary scope.');
    await handleGeneric('/api/area/court-documents/item',{id:'x'},context);
    assert.deepEqual(calls.detail[1],['court_documents']);
  }
});
test('a published addition joins listing, merged facet counts and record details',async()=>{
  const {calls,context}=courtContext(addition());
  const result=await handleGeneric('/api/area/court-documents',{doc_type:'court_form'},context);
  assert.deepEqual(calls.query.datasets,['court_documents','court_forms_expansion_20260912']);
  assert.deepEqual(calls.query.filters,{_listing:'yes',doc_type:'court_form'});
  assert.deepEqual(result.filters[1].options.map(o=>[o.value,o.count]),[['court_form',5],['local_rule',1],['supporting_material',1]]);
  assert.equal(result.qualification,'Primary scope. Also included: Official court forms (retrieved 2026-09-12).');
  assert.equal(result.total,7);
  await handleGeneric('/api/area/court-documents/item',{id:'court-forms-20260912:abc'},context);
  assert.deepEqual(calls.detail,['court-forms-20260912:abc',['court_documents','court_forms_expansion_20260912'],{full:false}]);
});
test('each addition joins independently; a held one never appears beside a published one',async()=>{
  const docket={id:'mdl_3080_docket_documents_20260928',ready:false,expected_records:2,imported_records:2,metadata:{listing:{filters:[]}}};
  const {calls,context}=courtContext(addition(),{mdl_3080_docket_documents_20260928:docket});
  await handleGeneric('/api/area/court-documents',{},context);
  assert.deepEqual(calls.query.datasets,['court_documents','court_forms_expansion_20260912']);
  docket.ready=true;
  await handleGeneric('/api/area/court-documents',{},context);
  assert.deepEqual(calls.query.datasets,['court_documents','court_forms_expansion_20260912','mdl_3080_docket_documents_20260928']);
});
test('addition originals use their own registered route',async()=>{
  let path;const context={asset:async p=>(path=p,new Response(null,{status:302,headers:{Location:'https://example.test/signed'}}))};
  const route='/supplement-files/court_forms_expansion_20260912/court-forms-20260912:abc';
  assert.equal((await handleGeneric(route,{},context)).status,302);
  assert.equal(path,route);
});
test('listing preserves filters/columns and uses dynamic filtered count and pagination',async()=>{
  let request;
  const context={dataset:async id=>data(id),query:async opts=>(request=opts,{total:3,items:[{id:'1',title:'Saved court'}]})};
  const result=await handleGeneric('/api/area/courts',{state:'CA',page:'2',limit:'10'},context);
  assert.equal(result.total,3);assert.equal(result.page,2);assert.equal(result.results[0].id,'1');
  assert.deepEqual(request.filters,{_listing:'yes',state:'CA'});assert.equal(request.offset,10);
  assert.deepEqual(result.columns,[{key:'title'}]);
});
test('unpublished dataset is unavailable and unhandled routes are null',async()=>{
  const context={dataset:async()=>({ready:false})};
  assert.equal((await handleGeneric('/api/area/courts',{},context)).available,false);
  assert.equal(await handleGeneric('/api/counties',{},context),null);
  assert.equal((await handleGeneric('/api/area/unknown',{},context)).status,404);
});
test('detail output is the public contract and is scoped to its dataset',async()=>{
  let call;
  const context={dataset:async id=>data(id),detail:async(...args)=>(call=args,{title:'A',facts:[],sections:[]})};
  assert.equal((await handleGeneric('/api/area/courts/item',{id:'ca'},context)).title,'A');
  assert.deepEqual(call,['ca',['court_spine'],{full:false}]);
});
test('mode changes select the appropriate listing configuration',()=>{
  const dataset=data('cpsc_injury_data',{mode_parameter:'dataset',listing_modes:{neiss:{filters:[{name:'product'}],columns:[{key:'product'}]},saferproducts:{filters:[{name:'state'}],columns:[{key:'manufacturer'}]}}});
  const o=queryOptions(dataset,{dataset:'saferproducts',state:'TX'});
  assert.deepEqual(o.query.filters,{_listing:'yes',state:'TX',dataset:'saferproducts'});
  assert.equal(o.config.columns[0].key,'manufacturer');
});
test('compound CFR references cannot match title and part from unrelated citations',()=>{
  const dataset=data('federal_register_history',{listing:{filters:['cfr_title','cfr_part'].map(name=>({name}))}});
  assert.equal(queryOptions(dataset,{cfr_title:'21',cfr_part:'314'}).query.filters.cfr_pair,'21:314');
  assert.equal(queryOptions(dataset,{cfr_part:'314'}).query.filters.cfr_part,undefined);
});
test('date bounds map to scalar recorded date fields',()=>{
  const o=queryOptions(data('settlements'),{dfrom:'2026-01-01',dto:'bad'});
  assert.deepEqual(o.query.filters,{_listing:'yes',__dfrom:'2026-01-01',__date_type:'date'});
});
test('state-code route uses native IDs and prefixes only listing identities',async()=>{
  let query;
  const context={dataset:async id=>data(id),query:async q=>(query=q,{total:1,items:[{id:'23',title:'IC'}]}),detail:async(id,datasets)=>({id,datasets,title:'Code section'})};
  const listed=await handleGeneric('/api/area/state-codes',{state:'IN'},context);
  assert.equal(listed.results[0].id,'IN:23');assert.deepEqual(query.datasets,['indiana_code']);
  const detail=await handleGeneric('/api/area/state-codes/item',{id:'IN:23'},context);
  assert.equal(detail.id,'23');assert.deepEqual(detail.datasets,['indiana_code']);
});
test('artifact uses only the registered manifest route',async()=>{
  let path;const context={asset:async p=>(path=p,new Response(null,{status:302,headers:{Location:'https://example.test/signed'}}))};
  assert.equal((await handleGeneric('/supplement-files/court_reference/ca',{},context)).status,302);
  assert.equal(path,'/supplement-files/court_reference/ca');
  assert.equal((await handleGeneric('/supplement-files/unknown/ca',{},context)).status,404);
});

test('detail joins retain native court overlay order and deduplicate fact labels',async()=>{
  const context={dataset:async id=>data(id),detail:async()=>({facts:[['State','CA']],sections:[{heading:'Original'}]}),context:async key=>({facts:[['State','Wrong'],['Court type','District']],sections:[{heading:'Facts'},{heading:'History'}],citation_section:{heading:'Citations'}})};
  const result=await handleGeneric('/api/area/courts/item',{id:'ca'},context);
  assert.deepEqual(result.facts.map(f=>f[0]),['State','Court type']);assert.equal(result.facts[0][1],'CA');assert.deepEqual(result.sections.map(s=>s.heading),['Facts','Original','History','Citations']);
});

test('known docket activity remains accessible with an honest unknown subtype label',async()=>{
  const context={dataset:async id=>data(id,{listing:{filters:[{name:'entry_type',options:[{value:'other',label:'Other'}]}]}}),query:async()=>({total:1,items:[{id:'one',cells:{entry_type:'Other'},badges:['Other']}]}),detail:async()=>({facts:[['Entry type (classifier)','Other']]})};
  const result=await handleGeneric('/api/area/mdl-activity',{},context);
  assert.equal(result.results[0].cells.entry_type,'Other (unclassified subtype)');assert.equal(result.filters[0].options[0].value,'other');assert.equal(result.filters[0].options[0].label,'Other (unclassified subtype)');
  assert.equal((await handleGeneric('/api/area/mdl-activity/item',{id:'one'},context)).facts[0][1],'Other (unclassified subtype)');
});
