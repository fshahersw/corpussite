/** Existing generic-area response contracts over the categorized hosted catalog. */
const pairs = {
  'court-coverage':'docsupload_coverage', settlements:'settlements', statistics:'court_statistics', courts:'court_spine',
  counsel:'mdl_counsel', urls:'url_directory', uscourts:'uscourts_pages', 'state-proceedings':'state_proceedings',
  'court-documents':'court_documents', 'judge-disclosures':'judge_disclosures', 'federal-register':'federal_register_history',
  'mdl-cases':'mdl_case_inventory', 'mdl-appearances':'mdl_appearances', 'mdl-documents':'mdl_docket_documents',
  'mdl-activity':'mdl_docket_activity', 'mdl-crosswalk':'mdl_crosswalk', 'sd-statutes':'sd_statutes',
  'agency-documents':'agency_science_documents', 'saved-pages':'saved_pages', 'indiana-code':'indiana_code',
  'public-laws':'public_laws', 'state-codes':'state_codes', 'counsel-directory':'counsel_directory',
  'verdict-reports':'verdict_reports', 'cpsc-injury-data':'cpsc_injury_data', 'expert-rulings':'expert_rulings',
  'source-documents':'source_documents', 'citation-guide':'citation_reference', 'limitation-periods':'limitation_periods',
  'citation-index':'citation_index', court_reference:'court_reference', judge_portraits:'judge_portraits',
  'court-forms-expansion':'court_forms_expansion_20260912', 'federal-opinions':'federal_opinions_20260820',
};
export const aliases = Object.freeze({...pairs, ...Object.fromEntries(Object.values(pairs).map(v=>[v,v]))});
// Separately published datasets shown inside an existing area; each joins only once its own gate passes.
const additions = {court_documents:['court_forms_expansion_20260912']};
const extraFilters = {
  court_documents:['court'], url_directory:['court','host'], mdl_case_inventory:['judge'],
  counsel_directory:['court'], public_laws:['year'],
};
const unavailable = reason => ({available:false,reason,total:0,page:1,limit:25,filters:[],columns:[],results:[]});
const bad = (reason,status=404) => Response.json({error:reason},{status});
const integer = (value,fallback,max) => Number.isFinite(Number(value)) && Number(value)>0 ? Math.min(max,Math.floor(Number(value))) : fallback;
const text = value => String(Array.isArray(value)?value[0]??'':value??'').trim();

function presentActivitySubtype(name,value) {
  if(name!=='mdl_docket_activity')return value;
  const label='Other (unclassified subtype)',result={...value};
  if(value.cells?.entry_type==='Other')result.cells={...value.cells,entry_type:label};
  if(value.badges)result.badges=value.badges.map(b=>b==='Other'?label:b);
  if(value.facts)result.facts=value.facts.map(f=>Array.isArray(f)&&String(f[0]).startsWith('Entry type')&&f[1]==='Other'?[f[0],label]:f);
  if(value.filters)result.filters=value.filters.map(f=>f.name==='entry_type'?{...f,options:(f.options||[]).map(o=>o.value==='other'?{...o,label}:o)}:f);
  return result;
}

function listingConfig(dataset,params) {
  const meta=dataset.metadata || dataset;
  let mode;
  if (meta.mode_parameter) {
    mode=text(params[meta.mode_parameter]);
    if (dataset.id==='docsupload_coverage' && text(params.collection)) mode='documents';
    if (!meta.listing_modes?.[mode]) mode=Object.keys(meta.listing_modes || {})[0];
  }
  return {meta,mode,config:presentActivitySubtype(dataset.id,meta.listing_modes?.[mode] || meta.listing || {filters:[],columns:[]})};
}

async function publishedAdditions(name,context) {
  const rows=await Promise.all((additions[name] || []).map(id=>context.dataset(id)));
  return rows.filter(d=>d?.ready && d.expected_records>0 && d.imported_records===d.expected_records);
}

function withAdditions(config,extras) {
  if (!extras.length) return config;
  const filters=(config.filters || []).map(filter=>{
    if (!Array.isArray(filter.options)) return filter;
    const options=new Map(filter.options.map(o=>[o.value,{...o}]));
    for (const extra of extras) {
      const other=(listingConfig(extra,{}).config.filters || []).find(f=>f.name===filter.name);
      for (const o of other?.options || []) {
        const seen=options.get(o.value);
        options.set(o.value,seen?{...seen,count:(seen.count || 0)+(o.count || 0)}:{...o});
      }
    }
    return {...filter,options:[...options.values()]};
  });
  const notes=extras.map(e=>listingConfig(e,{}).config.qualification).filter(Boolean);
  return {...config,filters,qualification:[config.qualification,...notes.map(n=>'Also included: '+n)].filter(Boolean).join(' ')};
}

export function queryOptions(dataset,params={}) {
  const {meta,mode,config}=listingConfig(dataset,params);
  const filters={_listing:'yes'};
  const allowed=new Set([...(config.filters || []).map(f=>f.name),...(extraFilters[dataset.id] || [])]);
  for (const key of allowed) {
    const value=text(params[key]);
    if (value && !['q','dfrom','dto','include_noise','mode','dataset'].includes(key)) filters[key]=value;
  }
  if (meta.mode_parameter && mode) filters[meta.mode_parameter]=mode;
  if (dataset.id==='url_directory') filters.is_noise='0';
  if (dataset.id==='federal_register_history') {
    if (/^\d{1,2}$/.test(text(params.cfr_title)) && text(params.cfr_part)) {
      filters.cfr_pair=`${text(params.cfr_title)}:${text(params.cfr_part)}`; delete filters.cfr_part;
    } else delete filters.cfr_part;
  }
  if (dataset.id==='public_laws' && filters.usc_title && filters.action) {
    filters.usc_effect=`${filters.usc_title}:${filters.action}`; delete filters.usc_title; delete filters.action;
  }
  if (dataset.id==='cpsc_injury_data' && mode==='neiss' && filters.product && !/^\d+$/.test(filters.product)) {
    filters.__contains={product_text:filters.product.replace(/[%_]/g,'')}; delete filters.product;
  }
  if (dataset.id==='counsel_directory' && mode==='philadelphia_liaison') {
    if (filters.role) {filters.__contains={role_text:filters.role.replace(/%/g,'')};delete filters.role;}
    // Native Philadelphia liaison records do not participate in the federal
    // appearance MDL/court join; the original adapter ignores these parameters.
    delete filters.mdl;delete filters.court;
  }
  for (const [key,dest] of [['dfrom','__dfrom'],['dto','__dto']]) {
    const value=text(params[key]);
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {filters[dest]=value;filters.__date_type='date';}
  }
  const page=integer(params.page,1,1000000),limit=integer(params.limit,config.limit || 25,100);
  return {meta,config,mode,page,limit,query:{datasets:[dataset.id],filters,q:text(params.q),limit,offset:(page-1)*limit,sort:'ordinal'}};
}

// Multi-million-row datasets (metadata.bounded) page through corpus_query_bounded, which counts at
// most BOUNDED_CAP matches; unfiltered and single-facet totals come from the published facet counts.
const BOUNDED_CAP=10000;
function boundedTotal(config,filters,q,data) {
  const keys=Object.keys(filters).filter(k=>!k.startsWith('_'));
  if (!q && !keys.length) return {total:config.total ?? 0,capped:false};
  if (!q && keys.length===1) {
    const option=(config.filters || []).find(f=>f.name===keys[0])?.options?.find(o=>o.value===filters[keys[0]]);
    if (Number.isInteger(option?.count)) return {total:option.count,capped:false};
  }
  return {total:data.total ?? 0,capped:Boolean(data.total_capped)};
}

async function boundedListing(name,options,context) {
  const {__dfrom,__dto,__date_type,...filters}=options.query.filters;
  const {q,limit,offset}=options.query;
  if (offset>=BOUNDED_CAP) return {...options.config,...unavailable('Only the first 10,000 matches can be paged. Add a filter or search to narrow the list.')};
  const data=await context.queryBounded({dataset:name,filters,q,limit,offset,cap:BOUNDED_CAP});
  const {total,capped}=boundedTotal(options.config,filters,q,data);
  return {...options.config,available:true,total,total_capped:capped,page:options.page,limit,results:data.items || []};
}

async function stateCodes(params,context,hub) {
  const state=text(params.state).toUpperCase();
  const destination={IN:'indiana_code',SD:'sd_statutes'}[state];
  if (!destination) return null;
  const source=await context.dataset(destination);
  if (!source?.ready) return unavailable('This state code has not passed hosted publication checks.');
  const inner={...params}; delete inner.state;
  const options=queryOptions(source,inner), data=await context.query(options.query);
  const stateFilter=(listingConfig(hub,{}).config.filters || []).find(f=>f.name==='state');
  const filters=(options.config.filters || []).filter(f=>f.name==='q').concat(stateFilter?[stateFilter]:[],(options.config.filters || []).filter(f=>!['q','state'].includes(f.name)));
  return {...options.config,available:true,total:data.total,page:options.page,limit:options.limit,filters,
    results:(data.items || []).map(item=>({...item,id:`${state}:${item.id}`})),
    code:{usps:state,name:state==='IN'?'Indiana Code':'South Dakota Codified Laws',state_name:state==='IN'?'Indiana':'South Dakota'}};
}

export async function handleGeneric(path,params={},context) {
  if (path.startsWith('/supplement-files/')) {
    if (!/^\/supplement-files\/[A-Za-z0-9_-]+\/[^/]+$/.test(path)) return bad('Invalid artifact route');
    const alias=path.split('/')[2];
    if (!aliases[alias]) return bad('Unknown source adapter');
    return await context.asset(path);
  }
  const match=/^\/api\/area\/([^/]+)(\/item)?$/.exec(path);
  if (!match) return null;
  const name=aliases[match[1]];
  if (!name) return bad('Unknown data area');
  const dataset=await context.dataset(name);
  if (!dataset?.ready) return match[2]?Response.json({error:'This collection is still being transferred.',code:'publication_pending'},{status:503}):{...unavailable('This collection is still being transferred.'),code:'publication_pending'};
  if (match[2]) {
    const id=text(params.id);
    if (!id || id.length>500) return bad('Record not found');
    let source=name,native=id;
    if (name==='state_codes') {
      const code=/^(IN|SD):(.+)$/.exec(id);
      if (code) {source=code[1]==='IN'?'indiana_code':'sd_statutes';native=code[2];}
    }
    const extras=source===name?(await publishedAdditions(name,context)).map(d=>d.id):[];
    const loaded=await context.detail(native,[source,...extras],{full:false});
    if (!loaded) return bad('Record not found');
    const note=source===name && !loaded.qualification ? (dataset.metadata || dataset).detail_note : null;
    const detail=presentActivitySubtype(source,note?{...loaded,qualification:note}:loaded);
    const extra=typeof context.context==='function' ? await context.context(`generic:extra:${source}:${native}`) : null;
    if (!extra) return detail;
    const factLabel=f=>Array.isArray(f)?f[0]:f.label;
    const labels=new Set((detail.facts || []).map(factLabel));
    const facts=[...(detail.facts || []),...(extra.facts || []).filter(f=>!labels.has(factLabel(f)))];
    const sections=extra.sections?.length
      ? [extra.sections[0],...(detail.sections || []),...extra.sections.slice(1)]
      : [...(detail.sections || [])];
    if (extra.citation_section) sections.push(extra.citation_section);
    return {...detail,facts,sections};
  }
  if (name==='state_codes') {
    const response=await stateCodes(params,context,dataset);
    if (response) return response;
  }
  const options=queryOptions(dataset,params);
  if (name==='judge_portraits') return {...options.config,available:true,results:[]};
  if ((dataset.metadata || dataset).bounded) return await boundedListing(name,options,context);
  const extras=await publishedAdditions(name,context);
  if (extras.length) options.query.datasets=[name,...extras.map(d=>d.id)];
  const data=await context.query(options.query);
  return {...withAdditions(options.config,extras),available:true,total:data.total,page:options.page,limit:options.limit,results:(data.items || []).map(item=>presentActivitySubtype(name,item))};
}
