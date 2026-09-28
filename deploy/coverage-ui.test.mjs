import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../delivery/archive-directory/areas.js',import.meta.url),'utf8');
const start=source.indexOf('  const FAMILIES ='),end=source.indexOf('  /* ---- Regulations',start);
assert.ok(start>=0&&end>start);
class Element{
 constructor(tag,className='',text=''){this.tagName=tag;this.className=className;this.textContent=text;this.children=[];this.attributes={};this.listeners={};}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(key,fn){this.listeners[key]=fn;}
}
const flatten=node=>[node.textContent,...node.children.map(flatten)].join('\n');
const pending=()=>Object.assign(new Error('Topics are not published'),{code:'publication_pending'});
function setup(params={},failure=pending()){
 const main=new Element('main'),fields={},requests=[];
 const row={name:'Michigan',abbr:'MI',jurisdiction_kind:'state',families:{statutes:{official_capture:{total:2,body:1,unreviewed:1}}},county_layer:{counties_total:83},gaps:[]};
 const context=vm.createContext({URLSearchParams,encodeURIComponent,main,registry:{coverage:{}},route:{params:new URLSearchParams(params)},
  el:(...args)=>new Element(...args),append:(parent,...nodes)=>{parent.append(...nodes);return parent;},count:value=>String(value??0),human:value=>String(value??''),
  action:label=>new Element('button','',label),navigate:()=>{},dataNote:text=>new Element('p','',text),
  filterField:(label,name)=>{const input=new Element('select');fields[name]=input;return {input,label:new Element('label','',label)};},
  setOptions:(input,options,first,value)=>{input.options=options;input.value=value;},
  routeLink:(text,route)=>Object.assign(new Element('a','',text),{href:'#'+route}),
  api:async path=>{requests.push(path);if(path==='/api/coverage/matrix')return {rows:[row],gaps:{},qualification:'Saved inventory.'};if(path.startsWith('/api/coverage/topics'))throw failure;if(path==='/api/coverage/state?state=MI')return row;throw new Error('Unexpected API: '+path);}
 });
 vm.runInContext(source.slice(start,end),context);return {context,main,fields,requests};
}
test('held optional topics leave the nationwide coverage table and jurisdiction selector usable',async()=>{
 const {context,main,fields,requests}=setup();await context.registry.coverage.page({aborted:false});
 assert.match(flatten(main),/Topic search is not yet published/);assert.match(flatten(main),/Michigan/);assert.match(flatten(main),/1 jurisdictions/);
 assert.equal(fields.topic.disabled,true);assert.equal(fields.state.disabled,undefined);assert.equal(fields.state.options[0].value,'MI');
 assert.equal(requests.length,2);assert.doesNotMatch(flatten(main),/undefined|No candidates found/);
});
test('a held topic deep link falls back to the selected state coverage without a second topic request',async()=>{
 const {context,main,requests}=setup({state:'MI',topic:'sol'});await context.registry.coverage.page({aborted:false});
 assert.match(flatten(main),/Michigan: what is saved and what is missing/);assert.match(flatten(main),/Saved coverage/);
 assert.equal(requests.filter(path=>path.startsWith('/api/coverage/topics')).length,1);
 assert.ok(requests.includes('/api/coverage/state?state=MI'));assert.doesNotMatch(flatten(main),/No candidates found/);
});
test('unexpected topic request failures still propagate rather than being mislabeled as publication pending',async()=>{
 const {context}=setup({},new Error('Connection failed'));await assert.rejects(context.registry.coverage.page({aborted:false}),/Connection failed/);
});
