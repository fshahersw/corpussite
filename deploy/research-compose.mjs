/** Live composition over immutable source snapshots. Never merge identity by name. */
export function resolveJudgeMdls(profile,descriptor){
 const entity=profile.entity_id||profile.structured?.entity_id;
 if(!descriptor?.ready)return {available:false,total:null,results:[],entity_id:entity||null,reason:'MDL registry unavailable'};
 if(!entity)return {available:false,total:null,results:[],entity_id:null,reason:'No canonical judge identity is linked'};
 const meta=descriptor.metadata||{},rows=(meta.filter_index||[]).filter(row=>(Array.isArray(row.filters?.entity_id)?row.filters.entity_id:[row.filters?.entity_id]).includes(entity)).map(r=>r.item).filter(Boolean);
 return {available:true,total:rows.length,results:rows,entity_id:entity,as_of:meta.listing?.as_of||null,basis:[...new Set(rows.map(r=>r.judge_link_basis).filter(Boolean))].join('+')||'recorded_entity_relationship',derived_from:'published_mdls_descriptor',composition_version:'entity-pages-v1'};
}
export async function composeJudge(profile,ctx){
 const out={...profile};
 const check=async id=>{try{return await ctx.dataset(id)}catch{return null}};
 const [mdls,disclosures,people]=await Promise.all(['mdls','judge_disclosures','people'].map(check));
 out.mdls=resolveJudgeMdls(profile,mdls);
 if(!disclosures?.ready)out.disclosures=null;
 if(!people?.ready){out.evidence=null;if(out.structured)out.structured={...out.structured,courtlistener:null};}
 return out;
}
