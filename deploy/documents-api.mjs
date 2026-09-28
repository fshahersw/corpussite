const integer=(v,f,max=100)=>Math.min(max,Math.max(1,parseInt(v,10)||f));
const mainDatasets=['federal','focused','judge_enrichment','judge_entities','judge_vendor','pending_publication','provider_laws','seeger','trellis_browser_counties'];
const advanced={file_type:'file_type',record_type:'record_type',review:'review_state',jur_level:'jurisdiction_level',subtype:'doc_subtype',dtype:'representation'};
const notFound=()=>Response.json({error:'Document not found in the categorized corpus'},{status:404});
const validDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value)&&!value.startsWith('0000')&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString().slice(0,10)===value;
export function documentFilters(params,config){
 const filters={};let valid=true;
 for(const [p,f] of Object.entries(advanced))if(params[p]){filters[f]=params[p];if(!config.facets?.[p]?.[params[p]])valid=false;}
 if(params.kind)filters.kind=params.kind;
 if(params.category){filters.categories=params.category;if(!config.labels[params.category])valid=false;}
 if(params.validity!=='any')filters.validity=params.validity||['ok','no_capture'];
 const dates=config.date_types?.[params.date_type];
 if(params.date_type&&!dates)valid=false;
 for(const k of ['dfrom','dto'])if(params[k]&&!validDate(params[k]))valid=false;
 const p={...params};if(dates)[p.date_lo,p.date_hi,p.date_type]=dates;
 return {filters,params:p,valid};
}
function counts(local,bulk,p){
 const available=(r)=>!p.availability||p.availability==='text'&&r.saved_text||p.availability==='original'&&r.saved_file||p.availability==='link_only'&&!r.saved_text&&!r.saved_file&&r.has_link;
 const selected=local.filter(r=>(!p.state||r.states.includes(p.state))&&(!p.category||r.categories.includes(p.category))&&(!p.dataset||r.dataset===p.dataset)&&available(r));
 const nbulk=bulk.filter(r=>(!p.state||r.state===p.state)&&(!p.category||r.category===p.category)&&(!p.dataset||p.dataset==='open_us_law')&&(!p.availability||['text','original'].includes(p.availability))).reduce((n,r)=>n+(p.availability==='text'?r.text_records:r.records),0);
 return {local_documents:new Set(selected.map(r=>r.display_id)).size,local_source_observations:selected.filter(r=>r.dataset!=='judge_entities').length,bulk_records:nbulk,total:new Set(selected.map(r=>r.display_id)).size+nbulk};
}
export async function handleDocuments(path,p,ctx){
 if(!['/api/documents','/api/record','/api/text','/api/summary','/api/explore'].includes(path))return null;
 if(path==='/api/record'||path==='/api/text'){
  const full=path==='/api/text',id=String(p.id||'');
  if(full){const original=await ctx.asset('/api/text?'+new URLSearchParams({id}),{optional:true});if(original)return original;}
  const isCore=!id.startsWith('oul:')&&!id.startsWith('county-litigation:');
  const grouped=isCore?await ctx.rpc('corpus_group_detail',{p_id:id,p_full:full}):null;
  const value=grouped||await ctx.detail(id,id.startsWith('oul:')?['open_us_law']:id.startsWith('county-litigation:')?['county_litigation']:mainDatasets,{full});
  if(!value)return notFound();
  if(value.facets?.category_label)value.category_label=value.facets.category_label;
  if(full&&value.full_text_offloaded)return Response.json({error:'The complete text download is not yet published',code:'publication_pending'},{status:503});
  if(full)return new Response(value.text||'',{headers:{'content-type':'text/plain; charset=utf-8'}});
  if(!grouped&&mainDatasets.includes(value.dataset)&&!value.source_records?.length){const {metadata,text,source_records,...source}=value;value.source_records=[source];}
  return value;
 }
 const datasets=await ctx.datasets(),ready=new Set(datasets.filter(d=>d.ready).map(d=>d.id));
 if(path==='/api/summary'){
  const base=await ctx.context('core:summary');if(!base)return {ready:false};
  const result=structuredClone(base),bulk=datasets.find(d=>d.id==='open_us_law');
  result.enrichment.open_us_law={...result.enrichment.open_us_law,ready:!!bulk?.ready,records:bulk?.ready?bulk.imported_records:0,indexed_records:bulk?.imported_records||0};
  result.migration={...result.migration,ready_datasets:ready.size,total_datasets:datasets.length};return result;
 }
 const config=await ctx.context('core:configuration');if(!config)return Response.json({error:'Document configuration is not published'},{status:503});
 if(path==='/api/explore'){
  const data=await ctx.context('core:explore');const group=p.group||'laws';
  const local=data.local.filter(r=>ready.has(r.dataset)&&(group==='all'||r.groups.includes(group))),bulk=['all','laws'].includes(group)&&ready.has('open_us_law')?data.bulk:[];
  const selected=Object.fromEntries(['state','category','dataset','availability'].map(k=>[k,p[k]||'']));
  const count=(params)=>counts(local,bulk,params);
  return {group,state:selected.state,filters:selected,totals:count(selected),
   jurisdictions:[...new Set([...config.states,...bulk.map(r=>r.state)])].sort().map(state=>({state,label:state,...count({...selected,state})})),
   categories:Object.entries(config.labels).filter(([k])=>k!=='other').map(([id,label])=>({id,label,...count({...selected,category:id})})),
   datasets:[...new Set([...local.map(r=>r.dataset),...bulk.map(r=>r.dataset)])].sort().map(id=>({id,label:data.datasets.find(d=>d.id===id)?.title||id.replaceAll('_',' '),...count({...selected,dataset:id}),snapshot_label:id==='open_us_law'?data.bulk_info.snapshot:'Saved source records; editions vary',source_as_of_label:'Legal currency varies by record; no uniform as-of date is verified.'})).filter(r=>r.total),
   availability:[['text','Readable text'],['original','Saved source file'],['link_only','Source link only']].map(([id,label])=>({id,label,...count({...selected,availability:id})})),
   count_basis:'Distinct local display groups plus separate publisher records. Facet memberships can overlap.',text_availability_basis:'Verified saved text and original source artifacts.',bulk_text_counts_verified:true,
   limitations:['Saved inventory counts are not completion percentages or certification of current law.','Publisher original files are compilation snapshots, not implied official PDFs.','Dates retain their individual source meanings.']};
 }
 const page=integer(p.page,1,1e7),limit=integer(p.limit,50),offset=(page-1)*limit;
 const {filters,params,valid}=documentFilters(p,config);
 const local=valid?await ctx.rpc('corpus_documents_local',{p_params:{...params,limit,offset},p_filters:filters}):{total:0,source_total:0,items:[]};
 const bulkEligible=valid&&ready.has('open_us_law')&&['','all','laws'].includes(p.group||'')&&['','open_us_law'].includes(p.dataset||'')&&!p.county&&!Object.keys({...advanced,validity:1,date_type:1,dfrom:1,dto:1,undated:1}).some(k=>p[k]);
 let bulk={items:[],total:0};
 if(bulkEligible){const filters={};for(const k of ['state','kind','category','availability'])if(p[k])filters[k]=p[k];bulk=await ctx.query({datasets:['open_us_law'],filters,q:p.q?'"'+p.q.replaceAll('"',' ')+'"':'',limit:Math.max(1,limit-local.items.length),offset:Math.max(0,offset-local.total)});}
 return {...config,facet_options:config.facets,facets_ready:true,categories:Object.entries(config.labels).filter(([id])=>id!=='other').map(([id,label])=>({id,label})),
  items:[...local.items,...(local.items.length<limit?bulk.items:[])],total:local.total+bulk.total,source_total:local.source_total+bulk.total,page,limit,
  view:p.view==='sources'?'sources':'grouped',order:'Local collections by title, then bulk records in publisher order',facet_note:'Mapped categories only. Capture quality, source versions and review flags are retained; legal currency is not certified.'};
}
