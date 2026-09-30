/** Research-facing contracts over the existing published corpus and native-ID projections. */
const groups = {
  law: ['Law & precedent', 'Codes, constitutions, regulations and cited authorities'],
  courts: ['Courts & practice', 'Court documents, judicial profiles and local practice'],
  science: ['Regulatory & science', 'Safety evidence, approvals, recalls and scientific reports'],
  matters: ['Matters & evidence', 'Dockets, filings, counsel and coordinated proceedings'],
  sources: ['Sources & reference', 'Source registries, captured pages and evidence connections']
};
// A dataset is never replaced with a generic page route. Its native identifier travels to the reader.
const definitions = {
  seeger:['law','State law and court rules','provisions'],indiana_code:['law','Indiana Code','sections'],sd_statutes:['law','South Dakota statutes','source records'],state_codes:['law','State-code collections','collections'],
  open_us_law:['law','Open U.S. Law snapshots','publisher records'],public_laws:['law','Public laws & U.S. Code','acts'],federal_register_history:['law','Federal Register','publications'],
  federal_regulations_sections:['law','Federal regulatory sections','sections'],federal_regulations_parts:['law','Federal regulatory parts','parts'],federal_regulations_documents:['law','Federal regulatory documents','documents'],
  citation_index:['law','Cited authorities','citation records'],citation_reference:['law','Reporters & citation forms','references'],limitation_periods:['law','Limitation periods','source records'],
  court_documents:['courts','Court rules, forms & orders','documents'],court_forms_expansion_20260912:['courts','Additional court forms','documents'],court_spine:['courts','Court registry','court records'],
  judges:['courts','Judge directory','judge records'],judge_entities:['courts','Consolidated judge profiles','profiles'],people:['courts','Judicial biographies','people'],judge_disclosures:['courts','Judicial financial disclosures','filings'],
  counties:['courts','Counties & court resources','county records'],county_litigation:['courts','County litigation resources','records'],court_statistics:['courts','Court statistics','publications'],uscourts_pages:['courts','U.S. Courts publications','captures'],docsupload_coverage:['courts','Court document crosswalk','source records'],
  agency_science_documents:['science','Agency & scientific documents','documents'],cpsc_injury_data:['science','Consumer-product injuries','observations'],
  agency_safety_cpsc_recalls_local:['science','CPSC recalls','recalls'],agency_safety_fda_press_recalls:['science','FDA recall announcements','announcements'],agency_safety_fda_warning_letters:['science','FDA warning letters','letters'],
  agency_safety_openfda_crl:['science','FDA complete-response letters','letters'],agency_safety_openfda_device_classification:['science','Medical-device classifications','classifications'],agency_safety_openfda_device_enforcement:['science','Medical-device enforcement','records'],
  agency_safety_openfda_device_pma:['science','Premarket device approvals','records'],agency_safety_openfda_drug_enforcement:['science','Drug enforcement & recalls','records'],agency_safety_openfda_drug_shortages:['science','Drug shortages','records'],
  agency_safety_openfda_drugsfda:['science','Drugs@FDA approvals','records'],agency_safety_openfda_food_enforcement:['science','Food enforcement & recalls','records'],agency_safety_openfda_orangebook:['science','Orange Book products','records'],
  mdls:['matters','Federal MDL registry','proceedings'],mdl_case_inventory:['matters','Matter & docket directory','dockets'],mdl_docket_activity:['matters','Docket entries','entries'],mdl_docket_documents:['matters','Docket documents','document references'],
  state_proceedings:['matters','State coordinated proceedings','source records'],counsel_directory:['matters','Firms & attorneys','directory records'],mdl_counsel:['matters','Master-docket counsel','records'],mdl_appearances:['matters','Counsel appearances','appearances'],
  expert_rulings:['matters','Expert-related filings','filings'],settlements:['matters','Settlement notices','source records'],verdict_reports:['matters','Verdict & settlement reports','reports'],
  sources:['sources','Legal source directory','sources'],url_directory:['sources','Discovered source addresses','URLs'],source_documents:['sources','Saved source documents','documents'],saved_pages:['sources','Captured web pages','captures'],
  focused:['sources','Official-source captures','records'],federal:['sources','Federal references','records'],gap_enrichment_20260927:['sources','Source additions & connections','records'],county_enrichment_20260928:['sources','County rules & orders additions','records']
};
export const STATE_NAMES = Object.freeze(Object.fromEntries('AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|DC:District of Columbia|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming|AS:American Samoa|GU:Guam|MP:Northern Mariana Islands|PR:Puerto Rico|VI:Virgin Islands'.split('|').map(x=>x.split(':'))));
const integer=(x,min,max,fallback)=>Number.isFinite(Number(x))?Math.max(min,Math.min(max,Math.floor(Number(x)))):fallback;
const fail=(message,status=400)=>Response.json({error:message},{status});
export const validMdl=x=>/^[1-9]\d{0,5}$/.test(String(x));
export const recordKey=(dataset,id)=>JSON.stringify([dataset,String(id)]);
export function collectionInfo(id) {const d=definitions[id];return d?{id,group:d[0],group_label:groups[d[0]][0],label:d[1],unit:d[2]}:null;}
const factMap=facts=>Object.fromEntries((Array.isArray(facts)?facts:[]).filter(f=>Array.isArray(f)&&f.length>1));
export function normalizeCourt(row) {
  const facts=factMap(row.facts);
  const inUse=row.in_use??facts['In use (CourtListener flag)']??null;
  const end=row.end_date??facts['Ended (CourtListener end_date)']??null;
  const ended=typeof end==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(end)&&end<=new Date().toISOString().slice(0,10);
  const historical=ended||inUse==='no';
  return {...row,state:row.state||'',historical,status:ended?'historical':inUse==='no'?'not_in_use':inUse==='yes'?'in_use_as_reported':'not_recorded',statusConflict:ended&&inUse==='yes',in_use:inUse,end_date:end};
}
export function displayRecord(dataset,item,recordId=item?.id) {
  const description=item?.cells?.description;
  const title=dataset==='mdl_docket_documents'&&description?description:item?.title||item?.name||String(recordId||'Source record');
  return {dataset,record_id:String(recordId),key:recordKey(dataset,recordId),title,original_title:item?.title||null,title_basis:description&&dataset==='mdl_docket_documents'?'source_document_description':'source_title',item:item||{}};
}
function metaConfig(descriptor,params) {
  const meta=descriptor.metadata||{};
  const modeParam=meta.mode_parameter;
  const modes=meta.listing_modes||{};
  const mode=modeParam?(params[modeParam]||Object.keys(modes)[0]):null;
  if(modeParam&&mode&&!Object.hasOwn(modes,mode))throw new Error('Unknown collection mode');
  return {meta,modeParam,mode,config:mode&&modes[mode]?modes[mode]:meta.listing||{}};
}
async function allPublished(ctx){return (await ctx.datasets()).filter(d=>d.ready===true&&definitions[d.id]);}
async function records(params,ctx) {
  if(params.dfrom||params.dto)return fail('Date-range filtering is not supported in this workspace query; use the collection-specific source reader.');
  const q=String(params.q||'').trim().slice(0,300);
  let datasets,descriptor,config={},filters={},modeParam,mode;
  if(params.dataset){
    if(!definitions[params.dataset])return fail('Unknown research collection',404);
    descriptor=await ctx.dataset(params.dataset);
    if(!descriptor?.ready)return fail('This collection is not published',503);
    let configured;try{configured=metaConfig(descriptor,params);}catch(e){return fail(e.message);}
    ({config,modeParam,mode}=configured);datasets=[params.dataset];
  }else{
    if(q.length<2)return fail('Enter at least two characters to search across collections');
    datasets=(await allPublished(ctx)).filter(d=>!params.group||definitions[d.id][0]===params.group).map(d=>d.id);
  }
  const allowed=new Set(['dataset','group','q','limit','page','docket','state','court','mdl',...(config.filters||[]).map(f=>f.name),...(modeParam?[modeParam]:[])]);
  for(const key of Object.keys(params))if(!allowed.has(key))return fail(`Unsupported filter: ${key}`);
  for(const key of ['state','court','mdl',...(config.filters||[]).map(f=>f.name)]){
    if(!['q','mode','dfrom','dto'].includes(key)&&params[key])filters[key]=String(params[key]).slice(0,300);
  }
  if(params.mdl&&!validMdl(params.mdl))return fail('Invalid MDL identifier');
  if(params.state&&!STATE_NAMES[params.state])return fail('Unknown jurisdiction code');
  if(params.state)filters.state_label=STATE_NAMES[params.state];
  if(modeParam&&mode)filters[modeParam]=mode;
  const limit=integer(params.limit,1,100,40),page=integer(params.page,1,251,1);
  if(params.docket&&!/^\d+$/.test(params.docket))return fail('Invalid native docket identifier');
  const data=await ctx.rpc('corpus_workspace_records',{p_datasets:datasets,p_q:q,p_filters:filters,p_limit:limit,p_offset:(page-1)*limit,p_docket:params.docket||''});
  const total=q?data.total:Object.keys(filters).length||params.docket?null:config.total??null;
  return {...data,items:(data.items||[]).map(r=>displayRecord(r.dataset,r.item,r.record_id)),total,page,limit,
    collection:descriptor?collectionInfo(descriptor.id||params.dataset):null,
    filters:config.filters||[],columns:config.columns||[],mode_parameter:modeParam||null,
    modes:descriptor?Object.keys(descriptor.metadata?.listing_modes||{}):[],mode:mode||null,
    qualification:config.qualification||null, searched_collections:datasets.length};
}
async function atlas(params,ctx) {
  if(params.state&&!STATE_NAMES[params.state])return fail('Unknown jurisdiction',400);
  const [raw,descriptor]=await Promise.all([ctx.rpc('corpus_workspace_courts',{p_state:params.state||''}),ctx.dataset('mdls')]);
  const courts=(raw||[]).map(normalizeCourt),byId=new Map(courts.map(c=>[c.id,c]));
  const matters=descriptor?.ready?(descriptor.metadata?.filter_index||[]).map(r=>r.item).filter(Boolean):[];
  const mapped=matters.filter(m=>byId.has(m.cl_court_id));
  const summary={};
  for(const c of courts){const key=c.state||'UNMAPPED';summary[key]??={court_records:0,historical:0,pending_mdls:0,pending_actions:0};summary[key].court_records++;if(c.historical)summary[key].historical++;}
  for(const m of mapped)if(m.status==='pending'){const key=byId.get(m.cl_court_id).state||'UNMAPPED';summary[key].pending_mdls++;summary[key].pending_actions+=Number(m.actions_pending)||0;}
  return {state:params.state||'',courts:params.state?courts:courts.map(({facts,...c})=>c),matters:mapped,summary,
    unlinked_mdls:params.state?null:matters.filter(m=>!byId.has(m.cl_court_id)).map(m=>({mdl:m.mdl_number,court_id:m.cl_court_id||null,title:m.title})),
    as_of:descriptor?.metadata?.listing?.as_of||null,
    basis:'Matter-to-court links use exact CourtListener court IDs; jurisdiction comes from the court registry explicit state field. Court records include historical and unknown-status entries.'};
}
async function matters(params,ctx) {
  const keys=['mdls','mdl_docket_activity','mdl_docket_documents','mdl_case_inventory','court_spine'];
  const descriptors=await Promise.all(keys.map(k=>ctx.dataset(k)));
  const mdls=descriptors[0];if(!mdls?.ready)return fail('MDL registry is not published',503);
  const coverage={};
  for(let i=1;i<4;i++){const d=descriptors[i];if(!d?.ready)continue;for(const o of d.metadata?.listing?.filters?.find(f=>f.name==='mdl')?.options||[]){coverage[o.value]??={};coverage[o.value][['','entries','documents','cases'][i]]=o.count;}}
  const rows=(mdls.metadata?.filter_index||[]).map(r=>r.item).filter(Boolean).map(m=>({...m,coverage:coverage[String(m.mdl_number)]||{}}));
  const q=String(params.q||'').toLowerCase();
  const selected=rows.filter(m=>(!params.status||params.status==='all'||m.status===params.status)&&(!params.court||m.cl_court_id===params.court)&&(!params.type||m.litigation_type===params.type)&&(!q||[m.title,m.mdl_number,m.master_docket,m.judge_name_as_printed,m.court_name].join(' ').toLowerCase().includes(q)));
  selected.sort((a,b)=>(b.actions_pending??-1)-(a.actions_pending??-1));
  return {items:selected,registry_total:rows.length,total:selected.length,as_of:mdls.metadata?.listing?.as_of,
    types:[...new Set(rows.map(m=>m.litigation_type).filter(Boolean))].sort(),
    courts:[...new Map(rows.map(m=>[m.cl_court_id,{id:m.cl_court_id,label:m.court_name}])).values()],
    qualification:mdls.metadata?.listing?.qualification};
}
export async function handleWorkspace(path,params={},ctx) {
  if(!path.startsWith('/api/workspace/'))return null;
  if(path==='/api/workspace/catalog'){
    const datasets=await ctx.datasets();
    return {groups:Object.entries(groups).map(([id,[label,description]])=>({id,label,description})),
      collections:datasets.filter(d=>definitions[d.id]).map(d=>({...collectionInfo(d.id),ready:d.ready===true,records:d.imported_records??null})),
      held:datasets.filter(d=>!d.ready).map(d=>({id:d.id,label:collectionInfo(d.id)?.label||d.label,records:d.imported_records})),
      states:STATE_NAMES};
  }
  if(path==='/api/workspace/records')return records(params,ctx);
  if(path==='/api/workspace/record'){
    if(!definitions[params.dataset])return fail('Unknown research collection',404);
    const d=await ctx.dataset(params.dataset);if(!d?.ready)return fail('This collection is not published',503);
    if(!params.id||String(params.id).length>500)return fail('Invalid record identifier');
    const detail=await ctx.detail(params.id,[params.dataset],{full:false});
    if(!detail)return fail('Record not found in the selected collection',404);
    let related=null;
    if(['mdl_docket_activity','mdl_docket_documents'].includes(params.dataset)){
      try{related=await ctx.rpc('corpus_workspace_entry_relations',{p_dataset:params.dataset,p_record:params.id});related={...related,items:(related.items||[]).map(r=>displayRecord(r.dataset,r.item,r.record_id))};}
      catch{related={available:false,error:'Exact entry relationships could not be loaded. Source text remains available.',items:[]};}
    }
    return {dataset:params.dataset,record_id:params.id,detail,related,collection:collectionInfo(params.dataset)};
  }
  if(path==='/api/workspace/atlas')return atlas(params,ctx);
  if(path==='/api/workspace/matters')return matters(params,ctx);
  if(path==='/api/workspace/dockets'){
    if(params.mdl&&!validMdl(params.mdl))return fail('Invalid MDL identifier');
    return ctx.rpc('corpus_workspace_dockets',{p_mdl:params.mdl||''});
  }
  return fail('Unknown research workspace route',404);
}
