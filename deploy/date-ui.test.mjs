import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../delivery/archive-directory/app.js',import.meta.url),'utf8');
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.textContent='';this.classList={add(){}};}
 append(...nodes){this.children.push(...nodes);}
 addEventListener(){}
}
const flatten=node=>[node.textContent,...node.children.map(flatten)].join('\n');
function setup(){
 const context=vm.createContext({document:{createElement:tag=>new Element(tag)},route:{params:new URLSearchParams()},displayTitle:item=>item.title,kindLabel:()=> 'Rule',human:String});
 for(const name of ['el','append','dateValue','date','documentRows']){
  const start=source.indexOf(`function ${name}(`),next=/\n(?:async )?function \w+\(/g;assert.ok(start>=0);next.lastIndex=start+10;
  vm.runInContext(source.slice(start,next.exec(source)?.index??source.length),context);
 }
 return context;
}
test('structured date values render as dates and missing objects never stringify as object Object',()=>{
 const context=setup();assert.equal(context.date({value:'2026-09-27',basis:'Source date'}),'Sep 27, 2026');
 assert.equal(context.date({value:null,basis:null}),'Not recorded');assert.equal(context.date({basis:'No date recorded'}),'Not recorded');
 assert.equal(context.date('2026'),'2026');assert.equal(context.date('2026-09'),'Sep 2026');
});
test('law rows fall back to saved date when structured source-as-of value is absent, retaining its basis',()=>{
 const context=setup(),body=new Element('tbody');
 const item={title:'Civil rule',has_text:true,facets:{dates:{source_as_of:{value:null,basis:null},saved_at:{value:'2026-09-27T12:00:00Z',basis:'Capture timestamp'}}}};
 const before=structuredClone(item);context.documentRows([item],body);
 assert.match(flatten(body),/Saved Sep 27, 2026/);assert.doesNotMatch(flatten(body),/Source as of|\[object Object\]/);
 assert.equal(body.children[0].children[3].children[1].title,'Capture timestamp');assert.deepEqual(item,before);
});
test('a real source date retains priority and missing dates produce no misleading row stamp',()=>{
 const context=setup(),body=new Element('tbody');
 context.documentRows([{title:'Rule',facets:{dates:{source_as_of:{value:'2026-08-14',basis:'Publisher snapshot'},saved_at:{value:'2026-09-27'}}}}],body);
 assert.match(flatten(body),/Source as of Aug 14, 2026/);assert.doesNotMatch(flatten(body),/Saved Sep/);
 assert.equal(body.children[0].children[3].children[1].title,'Publisher snapshot');
 const empty=new Element('tbody');context.documentRows([{title:'Rule',facets:{dates:{source_as_of:{value:null},saved_at:{value:null}}}}],empty);
 assert.doesNotMatch(flatten(empty),/Source as of|Saved|\[object Object\]/);
});
