/* Saved navigation and source relationships; metadata dates remain source dates. */
import {publishedSupplements} from './published-supplements.mjs';
const num=(value,fallback,max=100)=>Math.min(max,Math.max(1,parseInt(value,10)||fallback));
const lower=value=>String(value??'').trim().toLowerCase();
const notFound=()=>Response.json({error:'Saved reference not found'},{status:404});
const publicationPending=()=>Response.json({error:'These saved resources have not been published yet. Use Refresh to check their availability.',code:'publication_pending',available:false},{status:503});
const coveragePendingNote='Open US Law is not published in this release. Its snapshot counts are retained only as pending inventory, excluded from available coverage. Topic candidates that depend on the unpublished collection are also held. Gap flags retain their historical saved-inventory basis; they are not a survey of currently published law.';
function pendingCoverage(source){
 const result=structuredClone(source);
 function row(value){
  const inventory={available:false,status:'publication_pending',by_family:{}};
  for(const [family,cell] of Object.entries(value.families||{}))if(Object.hasOwn(cell,'third_party_snapshot')){inventory.by_family[family]=cell.third_party_snapshot;cell.third_party_snapshot=0;}
  for(const field of ['open_us_law_rule_sets','open_us_law_other_rows'])if(Object.hasOwn(value,field)){inventory[field]=value[field];value[field]={};}
  value.pending_inventory={...value.pending_inventory,open_us_law:inventory};
  if(Object.hasOwn(value,'topic_counts')){value.pending_inventory.topic_candidates={available:false,status:'source_dependency_unpublished',topic_counts:value.topic_counts};value.topic_counts={};}
  if(Array.isArray(value.gaps)&&!value.gaps.includes('open_us_law_not_published'))value.gaps.push('open_us_law_not_published');
 }
 if(result.families)row(result);
 for(const value of result.rows||[])row(value);
 if(result.totals){
  const inventory={available:false,status:'publication_pending',by_family:{}};
  for(const [family,cell] of Object.entries(result.totals.by_family||{}))if(Object.hasOwn(cell,'third_party_snapshot')){inventory.by_family[family]=cell.third_party_snapshot;cell.third_party_snapshot=0;}
  if(Object.hasOwn(result.totals,'open_us_law_court_rule_rows_typed')){inventory.open_us_law_court_rule_rows_typed=result.totals.open_us_law_court_rule_rows_typed;result.totals.open_us_law_court_rule_rows_typed=0;}
  result.pending_inventory={...result.pending_inventory,open_us_law:inventory};
 }
 if(result.rows&&result.gaps)result.gaps.open_us_law_not_published=result.rows.map(value=>value.abbr);
 result.publication={...result.publication,open_us_law_available:false,scope:'published_collections_only',gap_basis:'historical_saved_inventory'};
 result.qualification=[result.qualification,coveragePendingNote].filter(Boolean).join(' ');
 if(result.definitions)result.definitions.third_party_snapshot='Published Open US Law snapshot rows only. Unpublished counts are retained separately under pending_inventory.open_us_law.';
 return result;
}
const pageRows=(rows,p,max=100)=>{const page=num(p.page,1,1e7),limit=num(p.limit,30,max);return {total:rows.length,items:rows.slice((page-1)*limit,page*limit),page,limit};};
function sectionFacet(rows){
 const groups=new Map();
 for(const r of rows){if(!groups.has(r.section_h2))groups.set(r.section_h2,{value:r.section_h2,label:r.section_h2,count:0,states:new Set(),sub:new Map()});const g=groups.get(r.section_h2);g.count++;g.states.add(r.usps||'FEDERAL');g.sub.set(r.section_h3||'',(g.sub.get(r.section_h3||'')||0)+1);}
 return [...groups.values()].sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label)).map(g=>({value:g.value,label:g.label,count:g.count,states:g.states.size,subsections:[...g.sub].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).map(([value,count])=>({value,label:value||'No sub-label published',count}))}));
}
export async function handleNavigation(path,p,ctx){
 const context=key=>ctx.context(key);
 const publishedContext=async key=>(await context(key))??publicationPending();
 const coverageContext=async key=>{const data=await context(key);if(!data)return publicationPending();if(!data.families&&!data.rows?.some(row=>row.families))return data;return (await ctx.dataset('open_us_law'))?.ready?data:pendingCoverage(data);};
 if(path==='/api/coverage/matrix')return coverageContext('coverage:matrix');
 if(path==='/api/coverage/venues')return publishedContext('coverage:venues');
 if(path.startsWith('/api/coverage/')){
  const aliases=await context('state:aliases'),state=p.state?aliases?.[lower(p.state)]:null;
  if(path==='/api/coverage/state')return !aliases?publicationPending():state?coverageContext('coverage:state:'+state):notFound();
  if(path==='/api/coverage/topics'||path==='/api/coverage/labels'){
   const topic=path.endsWith('topics'),dataset=topic?'coverage_topics':'coverage_labels',filters={};
   const meta=await ctx.dataset(dataset),base=topic?await context('coverage:topics'):{};
   if(!meta?.ready)return publicationPending();
   if(p.state)filters.state=state||'__invalid__';
   for(const k of topic?['topic']:['law_body_class','rule_set','confidence'])if(p[k])filters[k]=p[k];
   const page=num(p.page,1,1e7),limit=num(p.limit,25,200);
   let result;
   if(topic&&p.topic){
    const order=await context('coverage:topic_order');
    const ids=p.state&&!state?[]:order?.[(state||'')+'|'+p.topic]||[];
    const selected=ids.slice((page-1)*limit,page*limit);
    result=selected.length?await ctx.query({datasets:[dataset],filters:{__ids:selected},limit,offset:0}):{items:[]};
    const positions=new Map(selected.map((id,index)=>[id,index]));
    result.items.sort((a,b)=>(positions.get(a.provision_id)??1e9)-(positions.get(b.provision_id)??1e9));
    result.total=ids.length;
   }else result=await ctx.query({datasets:[dataset],filters,limit,offset:(page-1)*limit});
   if(topic)for(const item of result.items){const tags=item._topic_evidence||[];item.match=p.topic?tags.find(t=>t.topic===p.topic)?.match:item.match;item.query_ids=[...new Set(tags.filter(t=>!p.topic||t.topic===p.topic).map(t=>t.query_id).filter(Boolean))].sort();delete item._topic_evidence;}
   return {...base,...result,available:!!meta?.ready,page,limit,pages:Math.ceil(result.total/limit),state,topic:p.topic||null,...(topic?{by_source_tier:p.state&&!state?{}:(await context('coverage:topic_tiers'))?.[(state||'')+'|'+(p.topic||'')]||{}}:{qualification:'Automated structural triage of saved text; not a legal review and no statement of legal currency.'})};
  }
 }
 if(path.startsWith('/api/county-filing')){
  const key=path.endsWith('/coverage')?'coverage':path.endsWith('/state-counties')?'state-counties:'+String(p.state||'').toUpperCase():path.endsWith('/state')?'state:'+String(p.state||'').toUpperCase():'county:'+p.fips;
  return await context('county-filing:'+key)||{available:false};
 }
 if(path==='/api/county-registry')return await context('county-registry:'+p.geoid)||{available:false,geoid:p.geoid};
 if(path.startsWith('/api/trellis-coverage')){
  if(path.endsWith('/receipt'))return ctx.asset(path+'?'+new URLSearchParams({id:p.id}));
  if(path.endsWith('/summary'))return publishedContext('trellis:summary');
  if(path.endsWith('/progress')){const progress=await context('trellis:progress');if(!p.state)return progress??publicationPending();const row=progress?.states?.find(r=>[lower(r.state),lower(r.state_name)].includes(lower(p.state).replaceAll('-',' ')));return row?{...row,available:true,as_of:progress.as_of,qualification:progress.qualification}:{available:false};}
  const rows=await context('trellis:counties')||[];
  if(path.endsWith('/county')){const matched=rows.filter(r=>p.fips?r.fips===p.fips:r.state===String(p.state).toUpperCase()&&lower(r.county)===lower(p.name));return matched.length===1?{...matched[0],available:true}:{available:false};}
  if(path.endsWith('/state')){const state=(await context('trellis:states')||[]).find(r=>r.state===String(p.state).toUpperCase());return state?{...state,counties:rows.filter(r=>r.state===state.state).sort((a,b)=>a.county.localeCompare(b.county))}:{available:false};}
  const selected=rows.filter(r=>(!p.state||r.state===p.state.toUpperCase())&&(!p.q||lower(r.county+' '+r.state+' '+r.state_name).includes(lower(p.q)))&&(!p.practice_area||(r.practice_areas||[]).some(a=>lower(a)===lower(p.practice_area)))&&[['detail','detail_captured'],['has_documents','has_documents'],['venue','venue'],['fips_resolved','fips']].every(([param,key])=>!['true','false'].includes(p[param])||(['venue','fips'].includes(key)?!!r[key]:r[key])===(p[param]==='true'))).sort((a,b)=>a.state.localeCompare(b.state)||a.county.localeCompare(b.county)||a.id.localeCompare(b.id));
  const offset=Math.max(0,parseInt(p.offset)||0),limit=num(p.limit,50,500),summary=await context('trellis:summary');
  return {available:!!summary?.available,total:selected.length,items:selected.slice(offset,offset+limit),limit,offset,qualification:summary?.qualification,license_ref:summary?.license_ref};
 }
 if(path.startsWith('/api/resource')){
  if(!await context('doj:states'))return {ready:false,found:false,items:[],total:0};
  if(path==='/api/resources/states')return publishedContext('doj:states');
  if(path==='/api/resources/circuits')return publishedContext('doj:circuits');
  if(path==='/api/resources/edges'){const rows=(await context('doj:edges')||[]).filter(r=>!p.relation||r.relation===p.relation);return {ready:true,...pageRows(rows,p)};}
  const all=await context('doj:resources')||[],qualification=await context('doj:qualification');
  if(path==='/api/resource'){const r=all.find(r=>r.record_id===p.id);return r?{ready:true,...r}:notFound();}
  const aliases=await context('doj:aliases')||{},state=aliases[lower(p.state)];
  let rows=all.filter(r=>p.state?(r.usps||'FEDERAL')===state:r.page_kind==='state_resource_page');
  if(path==='/api/resources/sections')return {ready:true,items:sectionFacet(rows),qualification};
  if(!state)return {ready:true,found:false,state:null,...pageRows([],p),facets:{sections:[]},qualification};
  const terms=lower(p.q).split(/\s+/).filter(Boolean);
  rows=rows.filter(r=>terms.every(t=>lower([r.link_text,r.url,r.host,r.section_path,r.evidence_line].join(' ')).includes(t))&&(!['yes','no'].includes(lower(p.in_directory))||(lower(p.in_directory)==='yes'?(r.directory_ref_ids||[]).length>0:r.not_in_source_directory===true)));
  const facets={sections:sectionFacet(rows)};
  if(p.section)rows=rows.filter(r=>[lower(r.section_h2),lower(r.section_path)].includes(lower(p.section)));
  rows.sort((a,b)=>a.position-b.position);
  const states=await context('doj:states');return {ready:true,found:true,state:state==='FEDERAL'?states.federal_page:states.items.find(r=>r.usps===state),...pageRows(rows,p),facets,qualification};
 }
 if(path==='/api/collections')return publishedContext('collections');
 if(path==='/api/collection'){
  const data=await context('collection:'+p.id);if(!data)return notFound();
  const rows=data.rows.filter(r=>(!p.q||lower(['title','description','excerpt','court_label','state','resource_kind'].map(k=>r[k]||'').join(' ')).includes(lower(p.q)))&&(!p.family||lower(r.registry_family)===lower(p.family))&&(!p.kind||lower(r.resource_kind)===lower(p.kind)));
  const result=pageRows(rows,{...p,limit:p.page_size||20},50);return {...data.card,...result,page_size:result.limit};
 }
 if(path==='/api/supplements')return p.name?publishedContext('supplement:'+p.name):(await context('supplements'))??(await publishedSupplements(ctx))??publicationPending();
 return null;
}
