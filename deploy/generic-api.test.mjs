import test from 'node:test';
import assert from 'node:assert/strict';
import {aliases,handleGeneric,queryOptions} from './generic-api.mjs';

const data=(id,metadata={})=>({id,ready:true,metadata:{listing:{available:true,total:8,limit:25,filters:[{name:'q'},{name:'state'},{name:'court'},{name:'dfrom'},{name:'dto'}],columns:[{key:'title'}]},...metadata}});

test('all 32 canonical adapters and 61 aliases are registered',()=>{
  assert.equal(new Set(Object.values(aliases)).size,32);
  assert.equal(Object.keys(aliases).length,61);
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
