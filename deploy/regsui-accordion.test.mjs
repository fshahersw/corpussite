import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Exercise the real renderer with the same small DOM contract style as test_ui_release.cjs.
const source=fs.readFileSync(new URL('../delivery/archive-directory/regsui.js',import.meta.url),'utf8');
const start=source.indexOf('  function browseStrip(');
const end=source.indexOf('\n  async function regulationsPage(',start);
assert.ok(start>=0&&end>start,'Regulation title browser must remain present');

class Element {
  constructor(tag,className='',text='') {
    this.tagName=tag.toUpperCase();this.className=className;this.textContent=text;
    this.children=[];this.attributes={};this.listeners={};this.hidden=false;
  }
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=[...nodes];}
  get firstChild() {return this.children[0]??null;}
  setAttribute(name,value) {this.attributes[name]=String(value);}
  getAttribute(name) {return this.attributes[name]??null;}
  addEventListener(name,listener) {(this.listeners[name]??=[]).push(listener);}
  async click() {await Promise.all((this.listeners.click??[]).map(listener=>listener({type:'click'})));}
}

function setup(api) {
  const context=vm.createContext({api,encodeURIComponent,
    el:(...args)=>new Element(...args),append:(target,...nodes)=>target.append(...nodes),
    count:value=>String(value??0),text:value=>String(value??''),
    routeLink:(label,view,params)=>Object.assign(new Element('a','',label),{href:'#'+view+'?'+new URLSearchParams(params)})});
  vm.runInContext(source.slice(start,end),context);
  const target=new Element('div');
  context.browseStrip(target,[{title:21,name:'Food and Drugs',parts_in_slice:1,parts_total:2,sections_in_slice:10,official_text_local:true}]);
  const [row,slot]=target.firstChild.children;
  return {open:row.firstChild,slot};
}

test('regulation title reopens its saved parts after repeated collapse without refetching',async()=>{
  let calls=0;
  const {open,slot}=setup(async url=>{calls++;assert.equal(url,'/api/regulations/parts?title=21');return {parts:[{part:314,heading:'Applications'}]};});
  await open.click();
  const grid=slot.firstChild;
  assert.equal(open.getAttribute('aria-expanded'),'true');
  assert.equal(grid.firstChild.firstChild.href,'#regulations?title=21&part=21%3A314');
  for(let i=0;i<3;i++) {
    await open.click();
    assert.equal(open.getAttribute('aria-expanded'),'false');
    assert.equal(slot.hidden,true);
    await open.click();
    assert.equal(open.getAttribute('aria-expanded'),'true');
    assert.equal(slot.hidden,false);
    assert.equal(slot.firstChild,grid);
  }
  assert.equal(calls,1);
});

test('collapse during a pending request remains collapsed and rapid reopen does not duplicate it',async()=>{
  let resolve,calls=0;
  const data=new Promise(done=>{resolve=done;});
  const {open,slot}=setup(()=>{calls++;return data;});
  const initial=open.click();
  await open.click();
  assert.equal(slot.hidden,true);
  await open.click();
  await open.click();
  resolve({parts:[{part:314,heading:'Applications'}]});
  await initial;
  assert.equal(open.getAttribute('aria-expanded'),'false');
  assert.equal(slot.hidden,true);
  await open.click();
  assert.equal(slot.hidden,false);
  assert.equal(slot.firstChild.firstChild.firstChild.textContent,'Part 314');
  assert.equal(calls,1);
});

test('a failed part request can retry after collapse and reopen',async()=>{
  let calls=0;
  const {open,slot}=setup(async()=>{if(++calls===1)throw Error('Unavailable');return {parts:[{part:314,heading:'Applications'}]};});
  await open.click();
  assert.match(slot.firstChild.textContent,/could not be read/);
  await open.click();
  await open.click();
  assert.equal(slot.hidden,false);
  assert.equal(slot.firstChild.firstChild.firstChild.textContent,'Part 314');
  assert.equal(calls,2);
});

test('empty title results remain readable after reopening',async()=>{
  const {open,slot}=setup(async()=>({parts:[]}));
  await open.click();
  await open.click();
  await open.click();
  assert.equal(slot.hidden,false);
  assert.match(slot.firstChild.firstChild.textContent,/No part of this title is held in full/);
});

async function searchPage(values,result) {
  const main=new Element('main'),inputs={},requests=[];
  const context=vm.createContext({main,URLSearchParams,encodeURIComponent,
    installStyle:()=>{},get:key=>values[key]??'',params:()=>({...values}),
    SEARCH_KEYS:['q','agency','record_type','date_type','dfrom','dto'],CITATION:/^(\d+) CFR (\S+)$/,
    el:(...args)=>new Element(...args),append:(target,...nodes)=>target.append(...nodes),
    count:value=>String(value??0),text:value=>String(value??''),human:value=>value,
    filterField:(label,name,type,value)=>({label:new Element('label','',label),input:inputs[name]=Object.assign(new Element(type),{value})}),
    setOptions:()=>{},action:label=>new Element('button','',label),navigate:()=>{},
    emptyState:(target,title,description)=>target.append(new Element('h2','',title),new Element('p','',description)),
    api:async url=>{requests.push(url);
      if(url==='/api/regulations/info')return {counts:{},date_types:{latest_amendment_date:'Latest amendment'}};
      if(url==='/api/regulations/titles')return {titles:[]};
      if(url.startsWith('/api/regulations/agencies'))return {agencies:[]};
      if(url.startsWith('/api/regulations/search?'))return typeof result==='function'?result():result;
      throw Error('Unexpected test request');
    }});
  const pageStart=source.indexOf('  async function regulationsPage(');
  const pageEnd=source.indexOf('\n  /* --------------------------------------------------------------------------------- export */',pageStart);
  assert.ok(pageEnd>pageStart);
  vm.runInContext(source.slice(pageStart,pageEnd),context);
  await context.regulationsPage({aborted:false},{});
  return {main,inputs,requests};
}

const allNodes=node=>[node,...node.children.flatMap(allNodes)];

test('missing date type keeps the form and avoids the native API HTTP400 without inventing a basis',async()=>{
  for(const field of ['dfrom','dto']) {
    const {main,inputs,requests}=await searchPage({[field]:'2026-01-01'},()=>{throw Error('HTTP 400: date_type is required with dfrom/dto');});
    const nodes=allNodes(main),copy=nodes.map(node=>node.textContent).join('\n');
    assert.match(copy,/Choose a Date type before applying From or To dates/);
    assert.doesNotMatch(copy,/No matching regulations|Unable to load directory/);
    assert.equal(main.firstChild.tagName,'FORM');
    assert.equal(nodes.find(node=>node.getAttribute('role')==='alert').textContent,'Choose a Date type before applying From or To dates.');
    assert.equal(inputs[field].value,'2026-01-01');
    assert.equal(inputs.date_type.value,'');
    assert.equal(inputs.date_type.getAttribute('aria-invalid'),'true');
    assert.equal(requests.some(url=>url.startsWith('/api/regulations/search?')),false);
  }
});

test('other API validation errors are visible while genuine empty results keep the empty state',async()=>{
  const invalid=await searchPage({q:'filing'},{available:true,results:[],error:'unknown record_type; choose section or fr_document'});
  const invalidCopy=allNodes(invalid.main).map(node=>node.textContent).join('\n');
  assert.match(invalidCopy,/unknown record_type/);
  assert.doesNotMatch(invalidCopy,/No matching regulations/);
  const empty=await searchPage({q:'no match'},{available:true,results:[]});
  assert.match(allNodes(empty.main).map(node=>node.textContent).join('\n'),/No matching regulations/);
});

test('an explicit date basis is sent unchanged and actual request failures still propagate',async()=>{
  const values={dfrom:'2026-01-01',date_type:'latest_amendment_date'};
  const {main,requests}=await searchPage(values,{available:true,results:[]});
  const params=new URL(requests.at(-1),'https://local.invalid').searchParams;
  assert.equal(params.get('date_type'),'latest_amendment_date');
  assert.equal(params.get('dfrom'),'2026-01-01');
  assert.match(allNodes(main).map(node=>node.textContent).join('\n'),/No matching regulations/);
  await assert.rejects(searchPage(values,()=>{throw Error('Network unavailable');}),/Network unavailable/);
});
