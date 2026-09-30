/** Entity composition over published sources. Original observations remain unchanged. */
import {handlePlacesJudges} from './places-judges-api.mjs';
import {handleReferencesMdl} from './references-mdl-api.mjs';
import {handleGeneric,aliases} from './generic-api.mjs';
import {handleDocuments} from './documents-api.mjs';
import {handleWorkspace,collectionInfo,normalizeCourt,STATE_NAMES} from './workspace-api.mjs';
import {composeJudge} from './research-compose.mjs';
import {projectDocument} from './research-projection.mjs';
const bad=(error,status=400)=>Response.json({error},{status});
const valid=x=>typeof x==='string'&&x.length>0&&x.length<=500&&!/[\u0000-\u001f]/.test(x);
const integer=(x,f=1,max=10000)=>Math.min(max,Math.max(1,Math.floor(Number(x))||f));
const stateCode=x=>x==='US'?'US':STATE_NAMES[String(x).toUpperCase()]?String(x).toUpperCase():Object.keys(STATE_NAMES).find(k=>STATE_NAMES[k].toLowerCase()===String(x).toLowerCase())||'';
const maybe=async fn=>{try{return await fn()}catch{return {available:false,error:'This source section could not be loaded.'}}};
async function enabled(ctx,name){return (await ctx.dataset(name))?.ready===true;}
async function names(p,ctx){
 const kind=({judges:'judge',attorneys:'attorney',firms:'firm'})[p.kind]||p.kind||'judge';
 if(!['judge','attorney','firm'].includes(kind))return bad('Unknown name directory');
 const code=p.state?stateCode(p.state):'';if(p.state&&!code)return bad('Unknown jurisdiction');
 if(p.court&&!/^[A-Za-z0-9_.:-]{1,120}$/.test(p.court))return bad('Invalid court identity');
 const page=integer(p.page),limit=integer(p.limit,30,100);
 if(!await enabled(ctx,kind==='judge'?'judges':'counsel_directory'))return bad('Name directory is not published',503);
 const data=await ctx.rpc('corpus_research_directory',{p_kind:kind,p_q:String(p.q||'').slice(0,200),p_state:code?STATE_NAMES[code]||code:'',p_court:p.court||'',p_offset:(page-1)*limit,p_limit:limit,p_native_person:p.person||''});
 return {...data,page,limit,kind};
}
async function judge(p,ctx){
 const profile=await handlePlacesJudges('/api/judge',{id:p.id},ctx);if(profile instanceof Response)return profile;
 const composed=await composeJudge(profile,ctx),personId=composed.structured?.ids?.cl_person_id;
 let person=null;if(personId&&await enabled(ctx,'people'))person=await maybe(()=>ctx.detail(personId,['people'],{full:false}));
 return {...composed,canonical_id:composed.entity_id||composed.id,person};
}
async function court(p,ctx){
 if(!await enabled(ctx,'court_spine'))return bad('Court registry is not published',503);
 const d=await ctx.detail(p.id,['court_spine'],{full:false});if(!d)return bad('Court not found',404);
 const facts=Object.fromEntries((d.facts||[]).filter(Array.isArray)),state=String(facts.State||'').slice(0,2);
 const [atlas,judges,attorneys,mdls]=await Promise.all([maybe(()=>handleWorkspace('/api/workspace/atlas',{state:STATE_NAMES[state]?state:''},ctx)),maybe(()=>names({kind:'judge',court:p.id,limit:'12'},ctx)),maybe(()=>names({kind:'attorney',court:p.id,limit:'10'},ctx)),maybe(()=>handleReferencesMdl('/api/mdls',{court:p.id,status:'all',limit:'200'},ctx))]);
 const c=atlas?.courts?.find(c=>c.id===p.id)||{id:p.id,state};
 return {id:p.id,name:d.title,detail:d,court:normalizeCourt(c),state,children:(atlas?.courts||[]).filter(row=>row.parent_id===p.id),judges,attorneys,matters:mdls};
}
async function resolve(p,ctx){
 if(p.kind==='person'){
  if(!/^[1-9]\d{0,11}$/.test(p.id||''))return bad('Unknown person',404);
  const found=await names({kind:'judge',person:p.id,limit:'2'},ctx);if(found instanceof Response)return found;
  return found.total===1?{type:'judge',id:found.items[0].id}:{type:'person',id:p.id,resolved:false};
 }
 if(p.kind==='disclosure'){
  const detail=await handleGeneric('/api/area/judge-disclosures/item',{id:p.id},ctx);if(detail instanceof Response)return detail;
  const person=(detail.facts||[]).find(f=>f[0]==='CourtListener person id')?.[1];
  if(person){const found=await names({kind:'judge',person:String(person),limit:'2'},ctx);if(found.total===1)return {type:'judge',id:found.items[0].id,section:'disclosures',filing:p.id};}
  return {type:'document',dataset:'judge_disclosures',id:p.id,resolved:false};
 }
 return bad('Unknown resolution type');
}
async function sourceDocument(p,ctx){
 if(p.dataset==='core')return handleDocuments('/api/record',{id:p.id},ctx);
 if(!collectionInfo(p.dataset)&&!aliases[p.dataset])return bad('Unknown source collection',404);
 if(!await enabled(ctx,p.dataset))return bad('Source collection is not published',503);
 const alias=Object.entries(aliases).find(([,d])=>d===p.dataset)?.[0];
 const detail=alias?await handleGeneric('/api/area/'+alias+'/item',{id:p.id},ctx):await ctx.detail(p.id,[p.dataset],{full:false});
 if(detail instanceof Response)return detail;if(!detail)return bad('Source record not found',404);
 const projected=projectDocument(p.dataset,detail,p.dataset==='federal_regulations_sections'?await enabled(ctx,'open_us_law'):false);
 return {...projected,dataset:p.dataset,record_id:p.id};
}
export async function handleResearch(path,p={},ctx){
 if(!path.startsWith('/api/research/'))return null;const route=path.slice('/api/research/'.length);
 if(['judge','court','resolve','document','counsel'].includes(route)&&!valid(p.id))return bad('Invalid source identity',404);
 if(route==='names')return names(p,ctx);if(route==='judge')return judge(p,ctx);if(route==='court')return court(p,ctx);if(route==='resolve')return resolve(p,ctx);if(route==='document')return sourceDocument(p,ctx);
 if(route==='counsel'){const d=await handleGeneric('/api/area/counsel-directory/item',{id:p.id},ctx);if(d instanceof Response)return d;return {id:p.id,kind:p.id.startsWith('firm')?'firm':'attorney',name:d.title,detail:d};}
 if(route==='law-tree'){
  if((p.state||'IN')!=='IN'||p.kind&&p.kind!=='statutes')return {available:false,nodes:[],items:[],reason:'No published native outline for this selection'};
  for(const k of ['title','article','chapter'])if(p[k]&&!/^[0-9.]{1,20}$/.test(p[k]))return bad('Invalid law hierarchy selection');
  return ctx.rpc('corpus_research_law_tree',{p_title:p.title||'',p_article:p.article||'',p_chapter:p.chapter||'',p_record:p.record||'',p_offset:(integer(p.page)-1)*50,p_limit:50});
 }
 if(route==='records')return handleWorkspace('/api/workspace/records',p,ctx);
 return bad('Unknown research route',404);
}
