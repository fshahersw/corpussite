import test from 'node:test';
import assert from 'node:assert/strict';
import {handleCloud} from './cloud-api.mjs';

const rows=[{id:'counties',ready:true,imported_records:3144,expected_records:3144},{id:'laws',ready:false,imported_records:10,expected_records:100}];
const preview={id:'early-release',scope:'partial',validated:true,datasets:['counties'],expected_dataset_count:69,available_views:['counties']};
const health=(datasets,markers={})=>handleCloud(new Request('https://example.com/api/health'),{datasets:async()=>datasets,context:async key=>markers[key]??null});
test('an exact completed preview is usable but never a full release',async()=>{
 const result=await health(rows,{'publication:preview':preview});
 assert.equal(result.usable,true);assert.equal(result.ready,false);assert.equal(result.release,null);
 assert.equal(result.release_scope,'partial');assert.equal(result.expected_dataset_count,69);
 assert.deepEqual(result.available_views,['counties']);
});
test('missing, unvalidated, unready and count-mismatched previews stay staging',async()=>{
 for(const marker of [null,{...preview,validated:false},{...preview,datasets:[]},{...preview,datasets:['missing']},{...preview,datasets:['laws']}]){
  const result=await health(rows,{'publication:preview':marker});assert.equal(result.usable,false);assert.equal(result.ready,false);
 }
 const result=await health([{...rows[0],imported_records:3143}],{'publication:preview':preview});
 assert.equal(result.usable,false);
});
test('a validated full release takes precedence over its earlier preview',async()=>{
 const result=await health(rows,{'publication:preview':preview,'publication:release':{id:'full',validated:true,datasets:['counties']}});
 assert.equal(result.ready,true);assert.equal(result.usable,true);assert.equal(result.release_scope,'full');assert.equal(result.release,'full');assert.equal(result.preview,null);
});
