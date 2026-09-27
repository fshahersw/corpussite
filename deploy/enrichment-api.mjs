/* Optional dated addition: unpublished contexts never masquerade as empty saved data. */
const absent=()=>({available:false,total:0,items:[],nodes:[],edges:[],qualification:'This source addition has not been published.'});
const integer=(value,fallback,max)=>Number.isFinite(Number(value))&&Number(value)>0?Math.min(max,Math.floor(Number(value))):fallback;
const text=value=>String(value??'').toLowerCase();
export async function handleEnrichment(path,p,ctx){
  if(!['/api/enrichment','/api/enrichment/graph','/api/enrichment/record','/api/enrichment/file'].includes(path))return null;
  const unavailable=()=>path.endsWith('/file')||path.endsWith('/record')?Response.json({error:'This source addition has not been published.'},{status:503}):absent();
  const dataset=await ctx.dataset('gap_enrichment_20260927');
  if(!dataset?.ready)return unavailable();
  const data=await ctx.context('enrichment:index');
  if(!data?.available)return unavailable();
  if(path==='/api/enrichment/file'){
    if(!['original','text'].includes(p.kind||'original')||!data.resources.some(r=>r.id===p.id))return Response.json({error:'Source artifact not found'},{status:404});
    return ctx.asset('/api/enrichment/file?id='+encodeURIComponent(p.id).replaceAll('%3A',':')+'&kind='+(p.kind||'original'));
  }
  if(path==='/api/enrichment/record'){
    if(!data.resources.some(r=>r.id===p.id))return Response.json({error:'Source addition not found'},{status:404});
    return await ctx.context('enrichment:record:'+p.id)??Response.json({error:'This source reader has not been published.'},{status:503});
  }
  if(path==='/api/enrichment/graph'){
    const entity=p.entity||'',known=data.graph_entities?.includes(entity),graph=known?await ctx.context('enrichment:graph:'+entity):null;
    if(known&&(!graph?.available||graph.entity!==entity))return{...absent(),entity,qualification:'This evidence graph has not been published.'};
    const edges=(graph?.edges||[]).slice(0,integer(p.limit,100,500)),total=graph?.total||0;
    const ids=new Set(edges.flatMap(e=>[e.source,e.target]));
    return{available:true,entity,total,edges,nodes:(graph?.nodes||[]).filter(n=>ids.has(n.id)),truncated:edges.length<total,summary:data.summary,qualification:data.qualification};
  }
  let found=data.resources;
  for(const key of ['state','lane','resource_type','document_shape'])if(p[key])found=found.filter(r=>text(r[key]||(key==='resource_type'?r.category:''))===text(p[key]));
  if(p.mdl){const ids=new Set(data.scopes?.['mdl:'+p.mdl]||[]);found=found.filter(r=>ids.has(r.id)||String(r.mdl_number||'')===String(p.mdl));}
  if(p.county){const ids=new Set(data.scopes?.['county:'+p.county]||[]);found=found.filter(r=>ids.has(r.id));}
  if(p.q){const terms=text(p.q).split(/\s+/).filter(Boolean);found=found.filter(r=>terms.every(t=>text(['title','state','native_id','resource_type','source_url'].map(k=>r[k]||'').join(' ')).includes(t)));}
  found=[...found].sort((a,b)=>text(a.state).localeCompare(text(b.state))||a.title.localeCompare(b.title)||a.id.localeCompare(b.id));
  const page=integer(p.page,1,1000000),limit=integer(p.limit,25,100);
  return {available:true,total:found.length,page,limit,items:found.slice((page-1)*limit,page*limit),summary:data.summary,generated_at:data.generated_at,qualification:data.qualification,
    facets:Object.fromEntries(['state','lane','resource_type','document_shape'].map(k=>[k,[...new Set(data.resources.map(r=>r[k]).filter(Boolean))].sort()]))};
}
