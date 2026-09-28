import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Use the actual shared filter helpers and source-additions renderer. In a
// browser, setting a select's value before its options exist loses selection.
class Element {
  constructor(tag,text=''){this.tagName=tag.toUpperCase();this.children=[];this.textContent=text;this.listeners={};this.attributes={};this._value='';}
  append(...nodes){this.children.push(...nodes.filter(Boolean));}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  set value(value){this._value=this.tagName==='SELECT'&&!this.children.some(c=>c.tagName==='OPTION'&&c.value===value)?'':value;}
  get value(){return this._value;}
}
const app=readFileSync(new URL('../delivery/archive-directory/app.js',import.meta.url),'utf8');
const helpers=app.slice(app.indexOf('function filterField('),app.indexOf('function readFilters('));
const enrichment=readFileSync(new URL('../delivery/archive-directory/enrichment.js',import.meta.url),'utf8');
const descendants=node=>[node,...node.children.flatMap(descendants)];
async function render(params,facets={state:['WI','LA'],resource_type:['local_rule','standing_order'],document_shape:['rule_body','order_body'],lane:['county_documents','laws']}){
  const main=new Element('main'),calls=[],navigations=[];
  const el=(tag,cls='',text='')=>new Element(tag,text);
  const context=vm.createContext({window:{},URLSearchParams,main,el,
    append:(parent,...children)=>{parent.append(...children);return parent;},
    human:value=>String(value||'').replaceAll('_',' '),count:value=>String(value),heading:()=>{},
    routeLink:text=>el('a','',text),emptyState:()=>{},pagination:()=>el('nav'),footnote:text=>el('p','',text),
    api:async route=>{calls.push(route);return {available:true,total:0,items:[],facets,summary:{original_documents:0},qualification:'Source evidence',page:1,limit:25};},
    navigate:(view,values)=>navigations.push({view,values})});
  vm.runInContext(helpers,context);vm.runInContext(enrichment,context);
  await context.window.ARCHIVE_AREAS.additions.render({aborted:false},{params:new URLSearchParams(params)});
  const nodes=descendants(main),fields=Object.fromEntries(nodes.filter(n=>['INPUT','SELECT'].includes(n.tagName)).map(n=>[n.name,n]));
  return {fields,calls,navigations,submit:()=>nodes.find(n=>n.tagName==='FORM').listeners.submit({preventDefault(){}})};
}

test('source-additions rendering retains all five URL filter values and Apply preserves county scope',async()=>{
  const filters={q:'civil rules',state:'WI',resource_type:'local_rule',document_shape:'rule_body',lane:'county_documents'};
  const result=await render({...filters,county:'55025',page:'3'});
  for(const [key,value] of Object.entries(filters))assert.equal(result.fields[key].value,value,key);
  const sent=new URL(result.calls[0],'https://archive.invalid');
  for(const [key,value] of Object.entries(filters))assert.equal(sent.searchParams.get(key),value);
  result.submit();assert.equal(result.navigations[0].view,'additions');
  assert.deepEqual({...result.navigations[0].values},{...filters,county:'55025'});
});

test('empty filters show All and selected values missing from current facets remain visible',async()=>{
  const empty=await render({});
  for(const field of Object.values(empty.fields))assert.equal(field.value,'');
  const selected=await render({state:'WI',resource_type:'local_rule',document_shape:'rule_body',lane:'county_documents'},{});
  for(const [key,value] of Object.entries({state:'WI',resource_type:'local_rule',document_shape:'rule_body',lane:'county_documents'})){
    assert.equal(selected.fields[key].value,value,key);
    assert.ok(selected.fields[key].children.some(option=>option.value===value),key);
  }
});
