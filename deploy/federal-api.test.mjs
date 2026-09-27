import test from 'node:test';
import assert from 'node:assert/strict';
import {handleFederal,parseReference,isoBound,normalizeFirm,asOfSections} from './federal-api.mjs';

const fixture={
  'federal:search-template':{available:true,results:[]},
  'federal:info':{date_types:{amendment_date:'eCFR amendment metadata',fr_publication_date:'FR publication'}},
  'agency:specs':[{dataset:'drug',group:'enforcement',id_prefix:'drug:'},{dataset:'device',group:'enforcement',id_prefix:'device:'}],
};
function fake(extra={}) {const calls=[];return {calls,context:async key=>({...fixture,...extra})[key]??null,query:async options=>(calls.push(options),{total:1,items:[{id:'one'}]}),detail:async(id,datasets)=>({id,datasets}),asset:async path=>({path})};}

test('CFR parser preserves explicit title and section, including bare lookup',()=>{
  assert.deepEqual(parseReference('21 C.F.R. § 314.80'),{title:'21',part:'314',section:'314.80'});
  assert.deepEqual(parseReference('cfr:21:314'),{title:'21',part:'314',section:null});
  assert.deepEqual(parseReference('314.80'),{title:null,part:'314',section:'314.80'});
  assert.deepEqual(parseReference('cfr:21:112.48-112.49'),{title:'21',part:'112',section:'112.48-112.49'});
  assert.equal(parseReference('21 CFR 314.80 trailing'),null);
});
test('date ranges preserve native prefix behavior and reject malformed bounds',()=>{
  assert.equal(isoBound('2026'),'2026-01-01');assert.equal(isoBound('2026-02',true),'2026-02-31');assert.equal(isoBound('last year'),false);
});
test('as-of listings exclude removed and unknown sections and use version-specific headings',()=>{
  const saved={current:[{section:'1.1',heading:'Current'},{section:'1.2'},{section:'1.9'}],historical_templates:{'1.3':{section:'1.3',heading:'Latest'}},as_of_template:{history_start:'2020-01-01',basis:'Version metadata'},versions:[
    {section:'1.1',version_date:'2020-01-01',removed:0,amendment_date:null,issue_date:null},
    {section:'1.2',version_date:'2020-01-01',removed:0},
    {section:'1.3',version_date:'2020-01-01',name:'§ 1.3 Historical heading',subpart:'A'},
    {section:'1.2',version_date:'2021-01-01',removed:1},
    {section:'1.3',version_date:'2023-01-01',name:'§ 1.3 New heading'},
  ]};
  const value=asOfSections(saved,'2022-01-01');assert.deepEqual(value.rows.map(r=>r.section),['1.1','1.3']);assert.equal(value.rows[1].heading,'Historical heading');assert.equal(value.as_of.sections_without_version_metadata_omitted,1);
  assert.equal(asOfSections(saved,'2020-02-30').as_of.supported,false);assert.equal(asOfSections(saved,'2019-01-01').as_of.supported,false);
});
test('regulation search requires named date basis and constrains compound part identity',async()=>{
  const c=fake();const bad=await handleFederal('/api/regulations/search',{dfrom:'2026'},c);assert.match(bad.error,/date_type is required/);assert.equal(c.calls.length,0);
  await handleFederal('/api/regulations/search',{part:'cfr:21:314',date_type:'amendment_date',dfrom:'2020'},c);
  assert.deepEqual(c.calls[0].datasets,['federal_regulations_sections']);assert.equal(c.calls[0].filters.part_id,'cfr:21:314');assert.equal(c.calls[0].filters.__date_any,'amendment_date');assert.equal(c.calls[0].filters.__prefix,'1');
});
test('agency filters use group membership, literal normalized firm and exact product code',async()=>{
  assert.equal(normalizeFirm("O'Brien & Sons, Inc."),'OBRIEN AND SONS INC');
  const c=fake();await handleFederal('/api/agency/search',{dataset:'enforcement',firm:'Acme, Inc.',status:'Ongoing',product_code:'abc',q:'aspi'},c);
  assert.deepEqual(c.calls[0].datasets,['agency_safety_drug','agency_safety_device']);assert.deepEqual(c.calls[0].filters.__contains,{firm_norm:'ACME INC'});assert.equal(c.calls[0].filters.status,'ongoing');assert.equal(c.calls[0].filters.product_code,'ABC');assert.equal(c.calls[0].sort,'date_desc');
});

test('unknown regulation agency never falls through to unrelated document slugs',async()=>{
  const c=fake({'federal:agencies':{agencies:[{slug:'food-and-drug-administration'}]}});
  const result=await handleFederal('/api/regulations/search',{agency:'unknown'},c);
  assert.equal(result.total,0);assert.equal(c.calls.length,0);
});
test('CFR classifications paginate native order without changing PMA totals',async()=>{
  const c=fake({'agency:cfr:21:870':{classifications:[{id:'a'},{id:'b'},{id:'c'}],product_codes:['A','B'],related_pma_rows:92}});
  const value=await handleFederal('/api/agency/cfr',{citation:'870',page:'2',limit:'2'},c);
  assert.equal(value.total,3);assert.deepEqual(value.classifications,[{id:'c'}]);assert.equal(value.related_pma_rows,92);assert.equal(c.calls.length,0);
});
test('agency details retain native ID prefix and only registered originals resolve',async()=>{
  const c=fake();assert.deepEqual(await handleFederal('/api/agency/record',{dataset:'drug',id:'3'},c),{id:'drug:3',datasets:['agency_safety_drug']});
  assert.deepEqual(await handleFederal('/agency-safety/files/original-1',{},c),{path:'/agency-files/original-1'});
  assert.equal((await handleFederal('/api/agency/record',{dataset:'other',id:'3'},c)).status,404);assert.equal(await handleFederal('/api/judges',{},c),null);
});
