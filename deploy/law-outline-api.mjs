/** Law-heading navigation with the same source row IDs and citation order as localhost. */
const actions=new Map([
  ['/api/law-outline','collections'], ['/api/law-outline/children','children'],
  ['/api/law-outline/provisions','provisions'], ['/api/law-outline/context','context'],
]);
const number=(value,fallback=0,max=1000000000,min=0)=>{
  const raw=String(value??'').trim();
  if (!/^[+-]?\d+$/.test(raw)) return fallback;
  return Math.max(min,Math.min(max,Number(raw)));
};
const value=x=>String(x??'').trim();

export async function handleLawOutline(path,params={},context) {
  const action=actions.get(path);
  if (!action) return null;
  const meta=await context.context('law_outline');
  const input=value(params.state), names=meta?.state_names || {};
  const state=Object.hasOwn(names,input.toUpperCase())?input.toUpperCase():
    Object.keys(names).find(code=>String(names[code]).toLowerCase()===input.toLowerCase()) || '';
  return context.rpc('corpus_law_outline',{
    p_action:action,p_state:state,p_kind:value(params.kind),p_parent:number(params.parent),
    p_node:number(params.node),p_id:value(params.id),p_offset:number(params.offset),
    p_limit:number(params.limit,100,200,1),
  });
}
