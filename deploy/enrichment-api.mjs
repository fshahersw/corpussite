/* Optional dated addition: unpublished contexts never masquerade as empty saved data. */
const absent=()=>({available:false,total:0,items:[],nodes:[],edges:[],qualification:'This source addition has not been published.'});
const integer=(value,fallback,max)=>Number.isFinite(Number(value))&&Number(value)>0?Math.min(max,Math.floor(Number(value))):fallback;
const text=value=>String(value??'').toLowerCase();
const bridges = new Set(['url','county','mdl','rule','docket-reference','court-source']);
const usable = new Set(['captured_as','excerpt_of','has_native_identifier','source_names_county','listed_filing_source','order_in_mdl','cites_mdl','cites_docket','lists_docket_in_schedule','contains','county_context','named_court','serves_geography_as_source_reported']);
const other = (edge, entity) => edge.source === entity ? edge.target : edge.source;
const relatedNote = 'These are recorded source connections, not legal applicability, current MDL membership, or a finding that two documents are equivalent. Separate captures and versions are retained.';
function clusterKind(path) {
  const relations=path.map(e=>e.relation);
  if(relations.includes('has_native_identifier'))return ['identifier','Same exact recorded rule identifier'];
  if(relations.some(r=>['cites_docket','lists_docket_in_schedule'].includes(r)))return ['docket','Same printed docket reference'];
  if(relations.some(r=>['order_in_mdl','cites_mdl'].includes(r)))return ['mdl','Orders or citations connected to this MDL'];
  if(relations.some(r=>['source_names_county','county_context'].includes(r)))return ['county','Documents that name this county'];
  if(relations.includes('serves_geography_as_source_reported'))return ['county','Court sources linked to this county'];
  if(relations.includes('named_court'))return ['court','Documents naming the same court'];
  if(relations.includes('listed_filing_source'))return ['listed_source','Saved readers of listed filing sources'];
  if(relations.includes('excerpt_of'))return ['source','Sections and captures of the same source'];
  if(relations.includes('contains'))return ['sections','Sections contained in this source'];
  return ['source','Captures of the same source'];
}

// A bounded walk through already-published evidence. No similarity matching or
// state-wide expansion; each result keeps the exact edge path that explains it.
export async function relatedReaders(data, entity, first, loadGraph) {
  const records=new Map(data.resources.map(r=>[r.id,r]));
  const edges=(first?.edges||[]).filter(e=>usable.has(e.relation)&&[e.source,e.target].includes(entity));
  const candidates=[...new Set(edges.map(e=>other(e,entity)).filter(id=>bridges.has(id.split(':')[0])))].sort();
  const selected=candidates.slice(0,8), paths=[];
  let incomplete=Boolean(first?.partial)||(first?.total||0)>(first?.edges?.length||0)||candidates.length>selected.length;
  const pending=[];
  for(const edge of edges)if(records.has(other(edge,entity)))paths.push([other(edge,entity),[edge]]);
  // Independent reads are bounded at eight; absent published contexts are
  // reported as pending rather than presented as an empty cluster.
  const graphs=await Promise.all(selected.map(async id=>[id,await loadGraph(id)]));
  for(const [bridge,graph] of graphs){
    if(!graph?.available||graph.entity!==bridge){pending.push(bridge);incomplete=true;continue;}
    if(graph.total>(graph.edges?.length||0)||graph.partial)incomplete=true;
    const firstEdges=edges.filter(e=>other(e,entity)===bridge);
    for(const edge of graph.edges||[]){
      if(!usable.has(edge.relation)||![edge.source,edge.target].includes(bridge))continue;
      const id=other(edge,bridge);
      if(id===entity||!records.has(id))continue;
      for(const firstEdge of firstEdges)paths.push([id,[firstEdge,edge]]);
    }
  }
  const groups=new Map();
  for(const [id,path] of paths){
    if(id===entity)continue;
    const [key,label]=clusterKind(path);
    if(!groups.has(key))groups.set(key,{key,label,rows:new Map()});
    const rows=groups.get(key).rows;
    if(!rows.has(id))rows.set(id,{...records.get(id),evidence_paths:[]});
    const item=rows.get(id), fingerprint=JSON.stringify(path.map(e=>[e.source,e.relation,e.target]));
    if(!item.evidence_paths.some(p=>JSON.stringify(p.map(e=>[e.source,e.relation,e.target]))===fingerprint)&&item.evidence_paths.length<3)item.evidence_paths.push(path);
  }
  const clusters=[...groups.values()].sort((a,b)=>a.key.localeCompare(b.key)).map(g=>{
    const rows=[...g.rows.values()].sort((a,b)=>(a.title||a.id).localeCompare(b.title||b.id)||a.id.localeCompare(b.id));
    return {key:g.key,label:g.label,matched:rows.length,items:rows.slice(0,6),truncated:rows.length>6};
  });
  return {available:true,entity,clusters,matched:new Set(paths.map(([id])=>id).filter(id=>id!==entity)).size,
    incomplete,pending_entities:pending,walk:{max_hops:2,max_bridges:8,visited_bridges:selected.length,bridge_candidates:candidates.length,max_edges_per_entity:500,max_items_per_cluster:6},qualification:relatedNote};
}
const collections=[{dataset:'gap_enrichment_20260927',prefix:'enrichment:'},{dataset:'county_enrichment_20260928',prefix:'county-enrichment-20260928:'}];
async function loadCollections(ctx){
  const ready=[],pending=[],owners=new Map();
  const inputs=await Promise.all(collections.map(async definition=>{
    try{
      const dataset=await ctx.dataset(definition.dataset);
      if(!dataset)return {...definition};
      if(!dataset.ready)return {...definition,pending:true};
      const data=await ctx.context(definition.prefix+'index');
      return {...definition,data,pending:!data?.available||!Array.isArray(data?.resources)||data.resources.some(r=>!r||typeof r.id!=='string')};
    }catch(error){
      if(definition.dataset==='gap_enrichment_20260927')throw error;
      return {...definition,pending:true};
    }
  }));
  for(const collection of inputs){
    if(collection.pending){pending.push(collection.dataset);continue;}
    if(!collection.data)continue;
    const ids=collection.data.resources.map(r=>r.id);
    if(ids.some(id=>!id||owners.has(id))||new Set(ids).size!==ids.length){pending.push(collection.dataset);continue;}
    ready.push(collection);for(const id of ids)owners.set(id,collection);
  }
  if(!ready.length)return {pending,owners};
  if(ready.length===1)return {data:ready[0].data,ready,pending,owners};
  const scopes={},resources=ready.flatMap(c=>c.data.resources),entities=[...new Set(ready.flatMap(c=>c.data.graph_entities||[]))];
  for(const {data} of ready)for(const [key,ids] of Object.entries(data.scopes||{}))scopes[key]=[...new Set([...(scopes[key]||[]),...ids])];
  const summary={...ready[0].data.summary,resources:resources.length,graph_nodes:entities.length,source_file_count_basis:'per_collection',graph_count_basis:'per_collection'};
  for(const key of ['original_documents','graph_edges','with_text','held','held_resources','held_relationships','duplicates'])summary[key]=ready.reduce((sum,c)=>sum+(c.data.summary?.[key]||0),0);
  for(const [field,key] of [['lane','by_lane'],['state','by_state'],['resource_type','by_type']]){
    summary[key]={};for(const row of resources){const label=row[field]||'unspecified';summary[key][label]=(summary[key][label]||0)+1;}
  }
  summary.by_relation={};for(const {data} of ready)for(const [relation,count] of Object.entries(data.summary?.by_relation||{}))summary.by_relation[relation]=(summary.by_relation[relation]||0)+count;
  const data={...ready[0].data,resources,scopes,graph_entities:entities,summary,generated_at:ready.map(c=>c.data.generated_at||'').sort().at(-1)};
  return {data,ready,pending,owners};
}
async function mergedGraph(entity,ready,ctx){
  const candidates=ready.filter(c=>c.data.graph_entities?.includes(entity));
  const values=await Promise.all(candidates.map(async c=>{
    try{return {collection:c.dataset,graph:await ctx.context(c.prefix+'graph:'+entity)};}
    catch{return {collection:c.dataset,graph:null};}
  }));
  const edges=new Map(),nodes=new Map(),pending=[];let complete=0,unseen=0;
  for(const {collection,graph} of values){
    if(!graph?.available||graph.entity!==entity||!Array.isArray(graph.edges)){pending.push(collection);continue;}
    const key=e=>e.id||JSON.stringify([e.source,e.relation,e.target,e.evidence||{}]);
    if((graph.edges||[]).some(e=>edges.has(key(e))&&JSON.stringify(edges.get(key(e)))!==JSON.stringify(e))){pending.push(collection);continue;}
    complete++;unseen+=Math.max(0,(graph.total||0)-(graph.edges?.length||0));
    for(const edge of graph.edges||[])edges.set(key(edge),edge);
    for(const node of graph.nodes||[])if(!nodes.has(node.id))nodes.set(node.id,node);
  }
  const selected=[...edges.values()].slice(0,500);
  return {available:complete>0||!candidates.length,entity,total:edges.size+unseen,edges:selected,nodes:[...nodes.values()],
    ...(pending.length?{partial:true,pending_collections:pending}:{})};
}
export async function handleEnrichment(path,p,ctx){
  if(!['/api/enrichment','/api/enrichment/graph','/api/enrichment/record','/api/enrichment/file'].includes(path))return null;
  const unavailable=()=>path.endsWith('/file')||path.endsWith('/record')?Response.json({error:'This source addition has not been published.'},{status:503}):absent();
  const {data,ready,pending,owners}=await loadCollections(ctx);
  if(!data?.available)return unavailable();
  const publicPending=pending;
  const heldCountyId=String(p.id||'').startsWith('county-gap:')&&pending.includes('county_enrichment_20260928');
  if(path==='/api/enrichment/file'){
    if(heldCountyId)return Response.json({error:'This county source addition has not been published.'},{status:503});
    if(!['original','text'].includes(p.kind||'original')||!data.resources.some(r=>r.id===p.id))return Response.json({error:'Source artifact not found'},{status:404});
    return ctx.asset('/api/enrichment/file?id='+encodeURIComponent(p.id).replaceAll('%3A',':')+'&kind='+(p.kind||'original'));
  }
  if(path==='/api/enrichment/record'){
    if(heldCountyId)return Response.json({error:'This county source addition has not been published.'},{status:503});
    if(!data.resources.some(r=>r.id===p.id))return Response.json({error:'Source addition not found'},{status:404});
    const record=await ctx.context(owners.get(p.id).prefix+'record:'+p.id);
    return record?.id===p.id?record:Response.json({error:'This source reader has not been published.'},{status:503});
  }
  if(path==='/api/enrichment/graph'){
    const entity=p.entity||'',known=data.graph_entities?.includes(entity),graph=known?await mergedGraph(entity,ready,ctx):null;
    if(known&&(!graph?.available||graph.entity!==entity))return{...absent(),entity,qualification:'This evidence graph has not been published.'};
    const edges=(graph?.edges||[]).slice(0,integer(p.limit,100,500)),total=graph?.total||0;
    const ids=new Set(edges.flatMap(e=>[e.source,e.target]));
    const result={available:true,entity,total,edges,nodes:(graph?.nodes||[]).filter(n=>ids.has(n.id)),truncated:edges.length<total,summary:data.summary,qualification:data.qualification};
    if(publicPending.length||graph?.pending_collections?.length)result.pending_collections=[...new Set([...publicPending,...(graph?.pending_collections||[])])];
    if(p.related==='1'&&entity){
      result.related=await relatedReaders(data,entity,graph,id=>mergedGraph(id,ready,ctx));
      if(result.pending_collections){result.related.incomplete=true;result.related.pending_collections=result.pending_collections;}
    }
    return result;
  }
  let found=data.resources;
  for(const key of ['state','lane','resource_type','document_shape'])if(p[key])found=found.filter(r=>text(r[key]||(key==='resource_type'?r.category:''))===text(p[key]));
  if(p.mdl){const ids=new Set(data.scopes?.['mdl:'+p.mdl]||[]);found=found.filter(r=>ids.has(r.id)||String(r.mdl_number||'')===String(p.mdl));}
  if(p.county){const ids=new Set(data.scopes?.['county:'+p.county]||[]);found=found.filter(r=>ids.has(r.id));}
  if(p.q){const terms=text(p.q).split(/\s+/).filter(Boolean);found=found.filter(r=>terms.every(t=>text(['title','state','native_id','resource_type','source_url'].map(k=>r[k]||'').join(' ')).includes(t)));}
  found=[...found].sort((a,b)=>text(a.state).localeCompare(text(b.state))||a.title.localeCompare(b.title)||a.id.localeCompare(b.id));
  const page=integer(p.page,1,1000000),limit=integer(p.limit,25,100);
  return {available:true,total:found.length,page,limit,items:found.slice((page-1)*limit,page*limit),summary:data.summary,generated_at:data.generated_at,qualification:data.qualification,
    ...(publicPending.length?{pending_collections:publicPending}:{}),
    facets:Object.fromEntries(['state','lane','resource_type','document_shape'].map(k=>[k,[...new Set(data.resources.map(r=>r[k]).filter(Boolean))].sort()]))};
}
