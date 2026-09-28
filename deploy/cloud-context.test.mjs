import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createContext, canonicalRoute } from './cloud-context.mjs';
import { handleCloud } from './cloud-api.mjs';

const env={CORPUS_SUPABASE_SECRET_KEY:'test-only-server-key'};
test('context pins the project and never inherits unrelated Supabase credentials',()=>{
  assert.throws(()=>createContext({SUPABASE_SECRET_KEY:'unrelated'}),/required/);
  assert.throws(()=>createContext({...env,CORPUS_SUPABASE_URL:'https://other.supabase.co'}),/Unexpected/);
  assert.throws(()=>canonicalRoute('https://other.invalid/files/x'),/Invalid/);
});
test('unpublished contexts stay unavailable and server requests reject redirects',async()=>{
  const ctx=createContext(env,async(url,options)=>{
    assert.match(url,/ready=eq.true/);assert.equal(options.redirect,'error');
    assert.equal(options.headers.apikey,env.CORPUS_SUPABASE_SECRET_KEY);
    return Response.json([]);
  });
  assert.equal(await ctx.context('held'),null);
});
test('large contexts require every published part and matching content hash',async()=>{
  const raw=JSON.stringify({text:'Court rules \u00a7 1'});
  const data={root:{__corpus_chunked_v1:true,parts:['root:part:abc:0','root:part:abc:1'],sha256:createHash('sha256').update(raw).digest('hex')},'root:part:abc:0':raw.slice(0,9),'root:part:abc:1':raw.slice(9)};
  let calls=0;
  const transport=async url=>{
    calls++;
    const filter=new URL(url).searchParams.get('key');
    if(filter.startsWith('in.'))return Response.json(JSON.parse('['+filter.slice(4,-1)+']').reverse().filter(key=>key in data).map(key=>({key,data:data[key]})));
    const key=filter.slice(3);return Response.json(key in data?[{data:data[key]}]:[]);
  };
  assert.deepEqual(await createContext(env,transport).context('root'),JSON.parse(raw));
  assert.equal(calls,2,'parts are fetched together and restored to manifest order');
  data['root:part:abc:1']='wrong';
  await assert.rejects(createContext(env,transport).context('root'),/integrity/);
  delete data['root:part:abc:1'];
  assert.equal(await createContext(env,transport).context('root'),null);
});
test('private originals redirect to same-project signed downloads',async()=>{
  const ctx=createContext(env,async url=>url.includes('/object/sign/')?Response.json({signedURL:'/object/sign/corpus-originals/aa/hash?token=test'}):Response.json([{object_key:'aa/hash',mime:'application/pdf',filename:'court rules.pdf'}]));
  const response=await ctx.asset('/files/x?b=2&a=1');
  const location=new URL(response.headers.get('location'));
  assert.equal(response.status,302);assert.equal(location.origin,'https://xosqzzsnhxcyehcnirpa.supabase.co');
  assert.equal(location.pathname,'/storage/v1/object/sign/corpus-originals/aa/hash');
  assert.equal(location.searchParams.get('download'),'court rules.pdf');
});
test('partial imports never report a complete hosted release',async()=>{
  const ctx={datasets:async()=>[{id:'one',ready:true,expected_records:1,imported_records:1}],context:async()=>null};
  const request=new Request('https://local.invalid/api/health');
  assert.equal((await handleCloud(request,ctx)).ready,false);
  ctx.context=async()=>({id:'release',validated:true,datasets:['one','missing']});
  assert.equal((await handleCloud(request,ctx)).ready,false);
  ctx.context=async()=>({id:'release',validated:true,datasets:['one']});
  assert.equal((await handleCloud(request,ctx)).ready,true);
});
