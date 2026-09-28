const DEFAULT_ORIGIN = 'https://xosqzzsnhxcyehcnirpa.supabase.co';
const BUCKET = 'corpus-originals';

export function canonicalRoute(route) {
  const url = new URL(route, 'https://archive.invalid');
  if (url.origin !== 'https://archive.invalid') throw new Error('Invalid artifact route');
  const pairs = [...url.searchParams.entries()].sort((a,b) => a[0].localeCompare(b[0]) || a[1].localeCompare(b[1]));
  const query = new URLSearchParams(pairs).toString();
  return url.pathname + (query ? '?' + query : '');
}

export function createContext(env=process.env, transport=fetch) {
  const origin = (env.CORPUS_SUPABASE_URL || DEFAULT_ORIGIN).replace(/\/$/,'');
  if (origin !== DEFAULT_ORIGIN) throw new Error('Unexpected Supabase project');
  const secret = env.CORPUS_SUPABASE_SECRET_KEY;
  if (!secret) throw new Error('CORPUS_SUPABASE_SECRET_KEY is required on the server');
  const request = async (path, options={}) => {
    const response = await transport(origin + path, {
      ...options, redirect:'error',
      headers:{ apikey:secret, 'content-type':'application/json', ...options.headers },
      signal:AbortSignal.timeout(60000)
    });
    if (!response.ok) throw new Error(`Archive data request failed (${response.status})`);
    return response.status===204 ? null : response.json();
  };
  const rpc = (name,payload) => request('/rest/v1/rpc/'+name,{method:'POST',body:JSON.stringify(payload)});
  const memo = new Map();
  async function readContext(key) {
    const rows=await request('/rest/v1/corpus_context?key=eq.'+encodeURIComponent(key)+'&ready=eq.true&select=data');
    const value=rows[0]?.data??null;
    if(!value?.__corpus_chunked_v1)return value;
    if(!Array.isArray(value.parts)||value.parts.length>1000||!value.parts.every(p=>p.startsWith(key+':part:')))throw new Error('Invalid context part manifest');
    const chunks=[];
    for(let i=0;i<value.parts.length;i+=25){
      const keys=value.parts.slice(i,i+25);
      const filter='in.('+keys.map(k=>JSON.stringify(k)).join(',')+')';
      const rows=await request('/rest/v1/corpus_context?key='+encodeURIComponent(filter)+'&ready=eq.true&select=key,data');
      const indexed=new Map(rows.map(row=>[row.key,row.data]));
      const results=keys.map(key=>indexed.get(key));
      if(results.some(part=>typeof part!=='string'))return null;
      chunks.push(...results);
    }
    const raw=chunks.join(''),digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))].map(n=>n.toString(16).padStart(2,'0')).join('');
    if(digest!==value.sha256)throw new Error('Context part integrity check failed');
    return JSON.parse(raw);
  }
  return {
    async query({datasets=null,filters={},q='',limit=25,offset=0,sort='ordinal'}={}) {
      return rpc('corpus_query',{p_datasets:datasets,p_filters:filters,p_q:q,p_limit:limit,p_offset:offset,p_sort:sort});
    },
    async queryBounded({dataset,filters={},q='',limit=25,offset=0,cap=10000}={}) {
      return rpc('corpus_query_bounded',{p_dataset:dataset,p_filters:filters,p_q:q,p_limit:limit,p_offset:offset,p_count_cap:cap});
    },
    async detail(id,datasets=null,{full=false}={}) {
      return rpc('corpus_detail',{p_id:String(id),p_datasets:datasets,p_full:full});
    },
    async dataset(id) {
      if (!memo.has(id)) memo.set(id,request('/rest/v1/corpus_datasets?id=eq.'+encodeURIComponent(id)+'&select=*').then(async rows=>{
        const row=rows[0]||null;
        if(row?.metadata?.context_key&&row.ready){const meta=await readContext(row.metadata.context_key);return meta?{...row,metadata:meta}:{...row,ready:false};}
        return row;
      }));
      return memo.get(id);
    },
    async datasets() {return request('/rest/v1/corpus_datasets?select=id,label,ready,expected_records,imported_records');},
    async context(key) {
      const cacheKey='context:'+key;
      if(!memo.has(cacheKey))memo.set(cacheKey,readContext(key));
      return memo.get(cacheKey);
    },
    async asset(route,{optional=false}={}) {
      const rows=await request('/rest/v1/corpus_artifacts?route=eq.'+encodeURIComponent(canonicalRoute(route))+'&ready=eq.true&select=object_key,mime,filename');
      if (!rows.length) return optional?null:Response.json({error:'This file is not available in the hosted collection yet. Saved reader text and publisher links may still be available.',code:'publication_pending'},{status:503});
      const asset=rows[0];
      const signed=await request('/storage/v1/object/sign/'+BUCKET+'/'+asset.object_key,{method:'POST',body:JSON.stringify({expiresIn:120})});
      const target=new URL('/storage/v1'+signed.signedURL,origin);
      if (!target.pathname.startsWith('/storage/v1/object/sign/')) throw new Error('Unexpected signed artifact path');
      if (!asset.mime.startsWith('image/') && asset.filename) target.searchParams.set('download',asset.filename);
      return new Response(null,{status:302,headers:{location:target.href,'cache-control':'private, no-store'}});
    },
    rpc
  };
}
