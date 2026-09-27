/** Federal/agency API contracts using source-bound exported rows and contexts. */
const SECTION='federal_regulations_sections', DOCUMENT='federal_regulations_documents', PART='federal_regulations_parts';
const SECTION_DATES=['amendment_date','ecfr_issue_date','gpo_amendment_marker','oul_snapshot_date'];
const DOCUMENT_DATES=['fr_publication_date','fr_effective_on','fr_signing_date','fr_comments_close_on'];
const bad=(message,status=404)=>Response.json({error:message},{status});
const str=value=>String(Array.isArray(value)?value[0]??'':value??'').trim();
const integer=(value,fallback,max=100)=>Number.isFinite(Number(value)) && Number(value)>0 ? Math.min(max,Math.floor(Number(value))) : fallback;
const pageInfo=(params={},defaultLimit=25,max=100)=>({page:integer(params.page,1,1e6),limit:integer(params.limit,defaultLimit,max)});
const pageRows=(rows,page,limit)=>({total:rows.length,page,limit,pages:Math.ceil(rows.length/limit),results:rows.slice((page-1)*limit,page*limit)});
const isDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10)===value;
const has=Object.prototype.hasOwnProperty;

export function isoBound(value,end=false) {
  const input=str(value);if(!input)return null;
  if(!/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(input))return false;
  return input.length===4?input+(end?'-12-31':'-01-01'):input.length===7?input+(end?'-31':'-01'):input;
}
export function parseReference(value,title='') {
  const input=str(value);
  const match=/^(?:cfr:(\d{1,2}):|(\d{1,2})\s*:\s*|(\d{1,2})\s*C\.?\s*F\.?\s*R\.?\s*(?:part\s+|pt\.?\s*|§+\s*|section\s+|sec\.?\s*)?)?(\d{1,4}[a-z]?)(?:\.(\d{1,5}[a-z0-9]*(?:[-–]\d{1,4}\.\d{1,5}[a-z]?)?))?\s*$/i.exec(input);
  if(!match)return null;
  const found=match[1]||match[2]||match[3]||str(title);
  if(found && !/^\d+$/.test(found))return null;
  return {title:found||null,part:match[4].toLowerCase(),section:match[5]?`${match[4]}.${match[5]}`.toLowerCase():null};
}
export function normalizeFirm(value) {
  return str(value).normalize('NFKC').toUpperCase().replaceAll('&',' AND ').replace(/['’`]/g,'').replace(/[^\p{L}\p{N}\s]|_/gu,' ').replace(/\s+/g,' ').trim();
}

export function asOfSections(saved,requested) {
  const dated=(saved.versions||[]).filter(v=>v.version_date);
  const date=isDate(requested)?requested:null;
  const start=saved.as_of_template?.history_start || dated.map(v=>v.version_date).sort()[0] || null;
  const block={...saved.as_of_template,requested,date,supported:false,reason:null,history_start:start,sections_without_version_metadata_omitted:0,listed:0};
  if(!date)return {rows:[],as_of:{...block,reason:'as_of must be a calendar date written YYYY-MM-DD'}};
  if(!dated.length || !start)return {rows:[],as_of:{...block,reason:'no eCFR version metadata for this part'}};
  if(date<start)return {rows:[],as_of:{...block,reason:`as_of precedes the eCFR version history for this part (starts ${start}); earlier states are unknown`}};
  const latest=new Map(),current=new Map((saved.current||[]).map(r=>[r.section,r]));
  const versioned=new Set(dated.map(v=>v.section));
  for(const version of dated)if(version.version_date<=date)latest.set(version.section,version);
  const result=[];
  for(const [ident,v] of latest) {
    if(v.removed)continue;
    let row=current.get(ident)||saved.historical_templates?.[ident];
    if(!row)continue;
    if(!current.has(ident))row={...row,heading:(v.name||'').replace(new RegExp('^§+\\s*'+ident.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\s*'),'').trim()||null,subpart:v.subpart??null};
    result.push({...row,as_of_state:{version_date:v.version_date,ecfr_amendment_date:v.amendment_date,ecfr_issue_date:v.issue_date,basis:block.basis}});
  }
  result.sort((a,b)=>a.section.localeCompare(b.section,'en',{numeric:true}));
  return {rows:result,as_of:{...block,supported:true,listed:result.length,sections_without_version_metadata_omitted:(saved.current||[]).filter(r=>!versioned.has(r.section)).length}};
}

async function queryWindow(context,options) {
  const result=await context.query({...options,limit:Math.min(options.limit,500)});
  let items=result.items||[];
  while(items.length<options.limit && options.offset+items.length<result.total) {
    const next=await context.query({...options,limit:Math.min(options.limit-items.length,500),offset:options.offset+items.length});
    if(!next.items?.length)break;items=items.concat(next.items);
  }
  return {...result,items};
}

async function regulationSearch(params,context) {
  const template=await context.context('federal:search-template');
  if(!template)return {available:false,reason:'Federal regulation publication unavailable',results:[],total:0};
  const {page,limit}=pageInfo(params),filters={};
  for(const key of ['q','title','part','agency','record_type','date_type','dfrom','dto'])filters[key]=str(params[key])||null;
  const base={...template,total:0,results:[],page,limit,pages:0,filters,date_filter:null};
  const recordType=filters.record_type,dateType=filters.date_type;
  const info=await context.context('federal:info'),dateTypes=info?.date_types || {};
  if(recordType && !['section','fr_document'].includes(recordType))return {...base,error:'record_type must be one of section, fr_document'};
  if(dateType && !has.call(dateTypes,dateType))return {...base,error:'unknown date_type; choose one of '+Object.keys(dateTypes).join(', ')};
  const low=isoBound(filters.dfrom),high=isoBound(filters.dto,true);
  if(low===false||high===false)return {...base,error:'dfrom/dto must be YYYY, YYYY-MM or YYYY-MM-DD'};
  if((low||high)&&!dateType)return {...base,error:'date_type is required with dfrom/dto; choose one of '+Object.keys(dateTypes).join(', ')};
  const queryFilters={__prefix:'1'};
  if(filters.part) {
    const ref=parseReference(filters.part,filters.title);
    if(!ref?.title)return {...base,error:'part must be cfr:<title>:<part>, <title>:<part>, "<title> CFR <part>" or <part> with title='};
    queryFilters.part_id=`cfr:${ref.title}:${ref.part}`;
  } else if(filters.title) {
    const title=filters.title.toLowerCase().replace(/^cfr:/,'');
    if(!/^\d+$/.test(title))return {...base,error:'title must be a CFR title number'};
    queryFilters.title=title;
  }
  if(filters.agency) {
    const slug=filters.agency.toLowerCase().replace(/^agency:/,'');
    const agencies=await context.context('federal:agencies');
    if(!agencies?.agencies?.some(a=>a.slug.toLowerCase()===slug))return base;
    queryFilters.agency=slug;
  }
  let datasets=[SECTION,DOCUMENT];
  if(recordType==='section'||SECTION_DATES.includes(dateType))datasets=[SECTION];
  if(recordType==='fr_document'||DOCUMENT_DATES.includes(dateType))datasets=[DOCUMENT];
  if(recordType==='section'&&DOCUMENT_DATES.includes(dateType)||recordType==='fr_document'&&SECTION_DATES.includes(dateType))return base;
  if(dateType) {
    queryFilters.date_types=dateType;
    queryFilters.__date_type=dateType;queryFilters.__dfrom=low||'0000-00-00';queryFilters.__dto=high||'9999-99-99';
    if(SECTION_DATES.includes(dateType))queryFilters.__date_any=dateType;
    base.date_filter={type:dateType,label:dateTypes[dateType],from:low,to:high,applies_to:SECTION_DATES.includes(dateType)?'section':'fr_document'};
  }
  const result=await context.query({datasets,filters:queryFilters,q:filters.q||'',limit,offset:(page-1)*limit,sort:'ordinal'});
  return {...base,total:result.total,pages:Math.ceil(result.total/limit),results:result.items||[],
    publisher_index:{available:true,searched:Boolean(filters.q),basis:'Search uses exported text whose source hash was verified during publication.'}};
}

async function agencySearch(params,context,firmExact=false) {
  const specs=await context.context('agency:specs');
  const {page,limit}=pageInfo(params);
  const filters={};for(const key of ['dataset','q','firm','classification','status','product_code','date_type','dfrom','dto'])filters[key]=str(params[key])||null;
  const base={available:true,reason:null,total:0,page,limit,pages:0,results:[],filters};
  if(!specs)return {...base,available:false,reason:'Agency publication unavailable'};
  const selected=specs.filter(s=>!filters.dataset || s.dataset===filters.dataset || s.group===filters.dataset);
  if(!selected.length)return base;
  const low=isoBound(filters.dfrom),high=isoBound(filters.dto,true);
  if(low===false||high===false)return {...base,error:'dfrom/dto must be YYYY, YYYY-MM or YYYY-MM-DD'};
  const queryFilters={__prefix:'1'};
  if(filters.firm) {
    const normalized=normalizeFirm(filters.firm);if(!normalized)return base;
    if(firmExact)queryFilters.firm_norm=normalized;else queryFilters.__contains={firm_norm:normalized};
  }
  for(const key of ['classification','status'])if(filters[key])queryFilters[key]=filters[key].toLowerCase();
  if(filters.product_code)queryFilters.product_code=filters.product_code.toUpperCase();
  if(filters.date_type||low||high) {
    queryFilters.__date_type=filters.date_type||'sort_date';queryFilters.__dfrom=low||'0000-00-00';queryFilters.__dto=high||'9999-99-99';
    if(filters.date_type)queryFilters.date_types=filters.date_type;
  }
  const result=await context.query({datasets:selected.map(s=>'agency_safety_'+s.dataset),filters:queryFilters,q:filters.q||'',limit,offset:(page-1)*limit,sort:'date_desc'});
  return {...base,total:result.total,pages:Math.ceil(result.total/limit),results:result.items||[]};
}

export async function handleFederal(path,params={},context) {
  if(path.startsWith('/agency-files/'))return context.asset(path);
  if(path.startsWith('/agency-safety/files/'))return context.asset(path.replace('/agency-safety/files/','/agency-files/'));
  if(path==='/api/agency-hub')return await context.context('agency:hub') || [];
  if(path==='/api/agency-hub/item')return await context.context('agency:hub:'+str(params.key)) || bad('Agency not found');
  if(path==='/api/agency/status')return await context.context('agency:status') || {available:false,records:0,datasets:0};
  if(path==='/api/agency/datasets')return await context.context('agency:datasets') || {items:[],status:{available:false}};
  if(path==='/api/agency/search')return agencySearch(params,context);
  if(path==='/api/agency/record') {
    const specs=await context.context('agency:specs');const spec=specs?.find(s=>s.dataset===str(params.dataset));
    if(!spec)return bad('Agency record not found');
    const id=str(params.id).startsWith(spec.id_prefix)?str(params.id):spec.id_prefix+str(params.id);
    return await context.detail(id,['agency_safety_'+spec.dataset],{full:true}) || bad('Agency record not found');
  }
  if(path==='/api/agency/firm') {
    const name=str(params.name),normalized=normalizeFirm(name),{page,limit}=pageInfo(params);
    const empty={available:true,query:name,firm_norm:normalized||null,firm_id:null,names:[],datasets:{},total:0,page,limit,pages:0,results:[],merge_rule:'exact normalised string equality only'};
    if(!normalized)return empty;
    const info=await context.context('agency:firm:'+normalized);if(!info)return empty;
    const results=await agencySearch({firm:name,page,limit},context,true);
    return {...empty,...info,total:results.total,pages:results.pages,results:results.results};
  }
  if(path==='/api/agency/cfr') {
    const ref=parseReference(str(params.citation),'21'),{page,limit}=pageInfo(params,100);
    const empty={available:true,query:str(params.citation),citation:null,title:null,part:null,section:null,total:0,page,limit,pages:0,classifications:[],product_codes:[],related_pma_rows:0,basis:'publisher field regulation_number on openFDA device/classification; related PMA rows share the product code'};
    if(!ref || ref.title!=='21')return empty;
    const key=ref.section||ref.part,more=await context.context('agency:cfr:21:'+key);
    const result=pageRows(more?.classifications||[],page,limit);
    return {...empty,...more,citation:'cfr:21:'+key,title:'21',part:ref.part,section:ref.section,total:result.total,pages:result.pages,classifications:result.results};
  }
  if(path==='/api/regulations/info')return await context.context('federal:info') || {ready:false,available:false,counts:{}};
  if(path==='/api/regulations/titles')return await context.context('federal:titles') || {available:false,titles:[],total:0};
  if(path==='/api/regulations/agencies') {
    const saved=await context.context('federal:agencies');if(!saved)return {available:false,agencies:[],total:0};
    const slice=str(params.slice_only||'1')==='1',query=str(params.q).toLowerCase();
    const list=(saved.agencies||[]).filter(a=>(!slice||a.slice_parts?.length)&&(!query||[a.name,a.display_name,a.short_name,a.slug].join(' ').toLowerCase().includes(query)));
    return {...saved,total:list.length,slice_only:slice,q:query||null,agencies:list};
  }
  if(path==='/api/regulations/parts') {
    const {page,limit}=pageInfo(params,500,1000),title=str(params.title).toLowerCase().replace(/^cfr:/,''),includeAll=str(params.include_all)==='1';
    const empty={available:true,reason:null,title:null,include_all:includeAll,total:0,page,limit,pages:0,parts:[]};
    if(!/^\d+$/.test(title))return {...empty,error:'title must be a CFR title number'};
    const titles=await context.context('federal:titles'),titleRow=titles?.titles?.find(r=>r.title===title);if(!titleRow)return empty;
    const result=await queryWindow(context,{datasets:[PART],filters:{title,...(!includeAll?{in_slice:'1'}:{})},q:'',limit,offset:(page-1)*limit,sort:'ordinal'});
    return {...empty,title:titleRow,total:result.total,pages:Math.ceil(result.total/limit),parts:result.items||[]};
  }
  if(path==='/api/regulations/sections') {
    const {page,limit}=pageInfo(params,500,1000),ref=parseReference(str(params.part),params.title);
    const empty={available:true,reason:null,found:false,part:null,as_of:null,total:0,page,limit,pages:0,sections:[]};
    if(!ref?.title)return {...empty,error:'part must be given as cfr:<title>:<part>, <title>:<part>, "<title> CFR <part>" or <part> with title='};
    const saved=await context.context(`federal:part:${ref.title}:${ref.part}`);if(!saved)return empty;
    const selected=str(params.as_of)?asOfSections(saved,str(params.as_of)):{rows:saved.current||[],as_of:null};
    const paged=pageRows(selected.rows,page,limit);
    return {...empty,found:true,part:saved.part,as_of:selected.as_of,total:paged.total,pages:paged.pages,sections:paged.results};
  }
  if(path==='/api/regulations/search')return regulationSearch(params,context);
  if(path==='/api/regulations/document')return await context.detail('fr_doc:'+str(params.id).replace(/^fr_doc:/,''),[DOCUMENT],{full:true}) || bad('Federal Register document not found');
  if(path==='/api/regulation') {
    const ref=parseReference(params.citation);if(!ref?.section)return bad('Regulation section not found');
    let id=ref.title?`cfr:${ref.title}:${ref.section}`:null;
    if(!id) {
      const result=await context.query({datasets:[SECTION],filters:{section:ref.section},q:'',limit:2,offset:0,sort:'ordinal'});
      if(result.total!==1)return bad('Regulation section not found');id=result.items[0].id;
    }
    const detail=await context.detail(id,[SECTION],{full:true});if(!detail)return bad('Regulation section not found');
    const related=await context.context(`federal:part:${detail.title}:${detail.part}`);
    return {...detail,fr_history:related?.fr_history||null};
  }
  if(path.startsWith('/api/regulations/')||path.startsWith('/api/agency/'))return bad('Unknown federal data view');
  return null;
}
