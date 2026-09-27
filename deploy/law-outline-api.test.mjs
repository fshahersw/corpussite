import test from 'node:test';
import assert from 'node:assert/strict';
import {handleLawOutline} from './law-outline-api.mjs';

function context() {
  const calls=[];
  return {calls,context:async()=>({state_names:{CA:'California',FEDERAL:'Federal'}}),
    rpc:async(name,args)=>{calls.push({name,args});return {available:true,args};}};
}
test('outline state names normalize to the original publisher codes',async()=>{
  const ctx=context();const result=await handleLawOutline('/api/law-outline',{state:'california'},ctx);
  assert.equal(result.args.p_state,'CA');assert.equal(result.args.p_action,'collections');
  assert.equal(ctx.calls[0].name,'corpus_law_outline');
});
test('numeric navigation is bounded and malformed input is not forwarded',async()=>{
  const ctx=context();const result=await handleLawOutline('/api/law-outline/provisions',{node:'24;DROP',offset:'-2',limit:'9999'},ctx);
  assert.equal(result.args.p_node,0);assert.equal(result.args.p_offset,0);assert.equal(result.args.p_limit,200);
  assert.equal((await handleLawOutline('/api/law-outline/provisions',{limit:'-3'},ctx)).args.p_limit,1);
});
test('unknown routes fall through and opaque record IDs remain exact',async()=>{
  const ctx=context();assert.equal(await handleLawOutline('/api/law-outline/fake',{},ctx),null);
  const result=await handleLawOutline('/api/law-outline/context',{id:'oul:example:a'},ctx);
  assert.equal(result.args.p_id,'oul:example:a');assert.equal(result.args.p_action,'context');
});
