import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../delivery/archive-directory/app.js',import.meta.url),'utf8');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.textContent='';this.attributes={};this.hidden=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];}
}
const flatten=node=>[node.textContent,...node.children.map(flatten)].join('\n');
const elements=node=>[node,...node.children.flatMap(elements)];
function setup(health){
  const banner=new Element('section');let fetches=0,outlines=0;
  const context=vm.createContext({URLSearchParams,AbortController,setTimeout,clearTimeout,console,
    window:{ARCHIVE_PUBLICATION:health,LawReader:{browser:()=>outlines++}},
    location:{protocol:'https:',hostname:'archive.example'},
    document:{querySelector:()=>banner,createElement:tag=>new Element(tag)},
    views:{overview:{},laws:{},documents:{},judges:{}},
    route:{view:'laws',params:new URLSearchParams({state:'Michigan'})},
    fetch:async()=>{fetches++;return {ok:true,json:async()=>health};},
    count:value=>new Intl.NumberFormat('en-US').format(value),
    extensionArea:()=>null,navName:name=>name,renderHubTabs:()=>{},announce:()=>{},
    datasetName:value=>value,date:value=>value,
    evidenceDates:()=>new Element('dl'),action:label=>Object.assign(new Element('button'),{textContent:label}),
  });
  for(const name of ['el','append','makeHash','routeLink','notice','api','partialHostedRelease','hostedDatasetPending','publicationLinks','checkPublicationStatus','errorState','emptyState','lawHubOpen','renderLawHub']){
    const match=new RegExp(`(?:async )?function ${name}\\(`).exec(source);assert.ok(match,name);
    const start=match.index,next=/\n(?:async )?function [\w]+\(/g;next.lastIndex=start+match[0].length;
    const end=next.exec(source)?.index??source.length;vm.runInContext(source.slice(start,end),context);
  }
  return {context,banner,fetches:()=>fetches,outlines:()=>outlines};
}
const partial={service:'legal-archive-supabase',ready:false,usable:true,release_scope:'partial',expected_dataset_count:4,
  available_views:['overview','laws','documents'],datasets:[{id:'focused',ready:true,records:103},{id:'seeger',ready:true,records:27},{id:'open_us_law',ready:false,records:900}]};

test('partial release counts only ready catalog records, gives working navigation and fetches health once',async()=>{
  const {context,banner,fetches}=setup(partial);await context.checkPublicationStatus();
  assert.equal(fetches(),1);assert.equal(banner.hidden,false);
  assert.match(flatten(banner),/Early release/);assert.match(flatten(banner),/2 of 4 collections · 130 catalog records/);
  assert.doesNotMatch(flatten(banner),/1,030|complete collection|importing/i);
  assert.deepEqual(elements(banner).filter(node=>node.tagName==='a').map(node=>node.href),['#overview','#laws','#documents']);
});
test('local and fully published hosted archives do not show an early-release notice',async()=>{
  for(const health of [{...partial,service:'legal-archive',ready:true},{...partial,ready:true,release_scope:'full'}]){
    const {context,banner}=setup(health);await context.checkPublicationStatus();assert.equal(banner.hidden,true);
  }
});
test('held hosted archive stays publication-pending until an audited usable release exists',async()=>{
  const {context,banner}=setup({...partial,usable:false});await context.checkPublicationStatus();
  assert.match(flatten(banner),/Publication pending/);assert.doesNotMatch(flatten(banner),/Early release/);
});
test('failed health remains a connection problem and never a fabricated partial release',async()=>{
  const {context,banner}=setup(null);context.fetch=async()=>{throw new Error('offline');};await context.checkPublicationStatus();
  assert.match(flatten(banner),/Connection check unavailable/);assert.doesNotMatch(flatten(banner),/catalog records/);
});
test('API error code survives HTTP failure and pending state offers published navigation without server advice',async()=>{
  const {context,banner}=setup(partial);context.fetch=async()=>({ok:false,status:503,json:async()=>({error:'Coverage has not yet been published.',code:'publication_pending'})});
  let error;try{await context.api('/api/coverage');}catch(value){error=value;}
  assert.equal(error.code,'publication_pending');assert.equal(error.status,503);
  context.errorState(banner,error,()=>{});assert.match(flatten(banner),/This collection is not published yet/);
  assert.doesNotMatch(flatten(banner),/Unable to load|server is stopped|launch instructions/);
  assert.ok(elements(banner).some(node=>node.href==='#documents'));
});
test('partial law browser retains published categories and source collections while avoiding held bulk outline requests',()=>{
  const {context,outlines}=setup(partial),target=new Element('div');
  context.renderLawHub({categories:[{id:'rules',label:'Court rules',total:12}],datasets:[{id:'focused',label:'Collected law and rules',total:12}]},target);
  assert.equal(outlines(),0);assert.match(flatten(target),/Open US Law bulk provision collection/);
  assert.match(flatten(target),/Court rules/);assert.match(flatten(target),/12 records/);
  assert.ok(elements(target).some(node=>node.href==='#laws?state=Michigan&category=rules'));
  assert.ok(elements(target).some(node=>node.href==='#laws?state=Michigan&dataset=focused'));
  assert.equal(elements(target).find(node=>node.tagName==='details').open,true);
});
test('ready bulk or local law browser still opens the existing outline',()=>{
  for(const health of [{...partial,datasets:[...partial.datasets.filter(row=>row.id!=='open_us_law'),{id:'open_us_law',ready:true,records:900}]},{service:'legal-archive',ready:true}]){
    const {context,outlines}=setup(health),target=new Element('div');context.renderLawHub({categories:[],datasets:[]},target);
    assert.equal(outlines(),1);assert.doesNotMatch(flatten(target),/not yet published/);
  }
});
