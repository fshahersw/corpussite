import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {handleNavigation} from './navigation-api.mjs';

const screens={
  court_spine:'court_spine_20260919',
  court_documents:'court_document_library_20260919',
  mdls:'jpml_mdl_20260919',
  state_proceedings:'state_coordinated_proceedings_20260919',
};
const descriptor=id=>({id,ready:true,expected_records:5,imported_records:5});
const supplement=name=>({name,ready:true,status:'passed',qualification:'Saved source snapshot.'});
function setup(){
  const datasets=Object.keys(screens).map(descriptor),values={},reads=[];
  for(const name of Object.values(screens))values['supplement:'+name]=supplement(name);
  const ctx={async datasets(){return datasets;},async context(key){reads.push(key);return values[key]??null;}};
  return {ctx,datasets,values,reads};
}
const list=ctx=>handleNavigation('/api/supplements',{},ctx);

test('held global directory lists the independently published court and MDL supplements',async()=>{
  const {ctx,values,reads}=setup(),before=structuredClone(values);
  // Even a separately readable local validation envelope is not enough to add
  // a screen outside the explicitly accepted set.
  values['supplement:mdl_docket_activity_20260919']=supplement('mdl_docket_activity_20260919');
  const result=await list(ctx);
  assert.deepEqual(result.items.map(item=>item.name).sort(),Object.values(screens).sort());
  assert.equal(result.total,4);assert.equal(result.ready,4);
  assert.match(result.qualification,/individually published/);
  assert.ok(!reads.includes('supplement:mdl_docket_activity_20260919'));
  for(const [key,value] of Object.entries(before))assert.deepEqual(values[key],value,'Stored gates remain unchanged');
  assert.equal(values.supplements,undefined,'The global publication gate remains held');
});

test('a supplement needs both its published named gate and a complete published catalog',async()=>{
  for(const mutation of [
    ({datasets})=>datasets.splice(0,1),
    ({datasets})=>{datasets[0].ready=false;},
    ({datasets})=>{datasets[0].ready='true';},
    ({datasets})=>{datasets[0].imported_records=4;},
    ({datasets})=>{delete datasets[0].imported_records;delete datasets[0].expected_records;},
    ({datasets})=>{datasets[0].expected_records=-1;datasets[0].imported_records=-1;},
    ({values})=>{delete values['supplement:'+screens.court_spine];},
    ({values})=>{values['supplement:'+screens.court_spine].ready=false;},
    ({values})=>{values['supplement:'+screens.court_spine].name=screens.mdls;},
  ]){
    const fixture=setup();mutation(fixture);
    const result=await list(fixture.ctx);
    assert.ok(!result.items.some(item=>item.name===screens.court_spine));
    assert.equal(result.total,3);
  }
});

test('regulation navigation requires all three datasets; accepted context-only screens use their named gates',async()=>{
  const {ctx,datasets,values}=setup(),name='federal_regulations_20260919';
  values['supplement:'+name]=supplement(name);
  values['supplement:doj_state_resource_map_20260919']=supplement('doj_state_resource_map_20260919');
  for(const id of ['federal_regulations_parts','federal_regulations_sections'])datasets.push(descriptor(id));
  let result=await list(ctx);
  assert.ok(!result.items.some(item=>item.name===name));
  assert.ok(result.items.some(item=>item.name==='doj_state_resource_map_20260919'));
  datasets.push(descriptor('federal_regulations_documents'));
  result=await list(ctx);assert.ok(result.items.some(item=>item.name===name));
});

test('no accepted supplement stays pending and a published global directory retains its contract',async()=>{
  const pending=await list({datasets:async()=>[],context:async()=>null});
  assert.equal(pending.status,503);assert.equal((await pending.json()).code,'publication_pending');
  const global={items:[supplement('other')],total:1,ready:1,qualification:'Full published directory.'};
  assert.equal(await list({context:async()=>global,datasets:async()=>{throw new Error('No fallback is needed');}}),global);
  const {ctx,values}=setup();
  assert.equal(await handleNavigation('/api/supplements',{name:screens.court_spine},ctx),values['supplement:'+screens.court_spine]);
});

class Element{
  constructor(tag,className='',text=''){this.tagName=tag;this.className=className;this.textContent=text;this.children=[];this.attributes={};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(name,value){this.attributes[name]=value;}
}
test('existing frontend loader restores Courts and MDLs tabs and only their published collections',async()=>{
  const {ctx}=setup(),bar=new Element('nav'),strip=new Element('nav'),calls=[];
  const context=vm.createContext({window:{ARCHIVE_PUBLICATION:{available_views:['judges']}},
    document:{querySelectorAll:()=>[],querySelector:selector=>selector==='#hub-tabs'?bar:strip},
    el:(...args)=>new Element(...args),main:{querySelector:()=>null},partialHostedRelease:()=>true,
    api:async path=>{calls.push(path);return list(ctx);},setTimeout:()=>{throw new Error('A published directory must not need a retry');},
  });
  const app=fs.readFileSync(new URL('../delivery/archive-directory/app.js',import.meta.url),'utf8');
  vm.runInContext(app.slice(app.indexOf('const HUBS=['),app.indexOf('async function render()')),context);
  context.renderHubTabs('judges');
  assert.deepEqual(bar.children.map(item=>item.href),['#judges']);
  const areas=fs.readFileSync(new URL('../delivery/archive-directory/areas.js',import.meta.url),'utf8');
  const start=areas.indexOf('(function loadReadySupplements(attempt)'),end=areas.indexOf('})(1);',start)+'})(1);'.length;
  vm.runInContext(areas.slice(start,end),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,['/api/supplements']);
  assert.deepEqual(bar.children.map(item=>item.href),['#judges','#mdls','#courts']);
  context.renderHubTabs('mdls');
  assert.deepEqual(Array.from(context.window.ACTIVE_SEGMENTS.items,item=>item.view),['mdls','state-proceedings']);
  context.renderHubTabs('courts');
  assert.deepEqual(Array.from(context.window.ACTIVE_SEGMENTS.items,item=>item.view),['courts','court-documents']);
  assert.ok(bar.children.every(item=>!item.children.some(child=>child.className==='publication-pending-label')));
});
