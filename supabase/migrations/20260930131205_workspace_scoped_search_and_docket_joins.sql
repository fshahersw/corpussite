create or replace function public.corpus_workspace_records(
 p_datasets text[], p_q text default '',p_filters jsonb default '{}',p_limit integer default 40,p_offset integer default 0,p_docket text default ''
) returns jsonb language plpgsql stable security invoker set search_path='' set statement_timeout='12s' as $$
declare
 cond text := 'r.dataset = any($1) and exists(select 1 from public.corpus_datasets d where d.id=r.dataset and d.ready) and (not r.filters ? ''_listing'' or (r.filters->''_listing'') @> ''"yes"''::jsonb)';
 lim integer:=least(greatest(p_limit,1),100); off integer:=least(greatest(p_offset,0),10000); f record;
 terms tsquery; payload jsonb; probe integer; capped boolean;
begin
 if coalesce(cardinality(p_datasets),0)=0 then return jsonb_build_object('items','[]'::jsonb,'has_more',false,'total',0); end if;
 for f in select key,value from jsonb_each(coalesce(p_filters,'{}')) loop
  if jsonb_typeof(f.value)<>'string' or f.key ~ '^_' then raise exception 'Unsupported filter' using errcode='22023'; end if;
  if f.key='state' then
   cond:=cond||format(' and (upper(r.state)=%L or r.state=%L or (r.state='''' and exists(select 1 from public.corpus_workspace_court_map c where c.state=%L and ((r.filters->''court'') @> to_jsonb(c.court_id) or (c.registry_key is not null and (r.filters->''court'') @> to_jsonb(c.registry_key))))))', f.value#>>'{}', (p_filters->>'state_label'), f.value#>>'{}');
  elsif f.key <> 'state_label' then
   cond:=cond||format(' and (r.filters @> %L::jsonb or r.filters @> %L::jsonb)',jsonb_build_object(f.key,jsonb_build_array(f.value)),jsonb_build_object(f.key,f.value));
  end if;
 end loop;
 if coalesce(p_docket,'')<>'' then
  if p_docket !~ '^[0-9]+$' then raise exception 'Invalid native docket identifier' using errcode='22023'; end if;
  cond:=cond||' and exists(select 1 from public.corpus_workspace_docket_links w where w.source_dataset=r.dataset and w.source_record_id=r.id and w.cl_docket_id=$3)';
 end if;
 if length(trim(coalesce(p_q,'')))>0 then
  terms:=websearch_to_tsquery('simple',left(p_q,300));
  if numnode(terms)=0 then return jsonb_build_object('items','[]'::jsonb,'has_more',false,'total',0); end if;
  cond:=cond||' and r.search_vector @@ $2';
  execute format('with sample as materialized(select r.dataset,r.id,r.ordinal,r.item,ts_rank_cd(r.search_vector,$2) as score from public.corpus_records r where %s limit 2001), page as(select * from sample order by score desc,dataset,ordinal,id limit $4 offset $5) select jsonb_build_object(''items'',coalesce((select jsonb_agg(jsonb_build_object(''dataset'',dataset,''record_id'',id,''item'',item) order by score desc,dataset,ordinal,id) from page),''[]''::jsonb),''total'',least((select count(*) from sample),2000),''total_capped'',(select count(*)>2000 from sample),''has_more'',(select count(*) from sample)>$5+$4,''facets'',coalesce((select jsonb_object_agg(dataset,n) from (select dataset,count(*) n from sample group by dataset) f),''{}''::jsonb),''count_basis'',''Bounded indexed matches; when capped, ranking and facets cover the first 2,001 indexed matches, not the entire corpus'')',cond)
  into payload using p_datasets,terms,p_docket,lim,off;
 else
  execute format('with page as materialized(select r.dataset,r.id,r.ordinal,r.item from public.corpus_records r where %s order by r.ordinal,r.dataset,r.id limit $4 offset $5), shown as(select * from page order by ordinal,dataset,id limit $6) select jsonb_build_object(''items'',coalesce((select jsonb_agg(jsonb_build_object(''dataset'',dataset,''record_id'',id,''item'',item) order by ordinal,dataset,id) from shown),''[]''::jsonb),''has_more'',(select count(*) from page)>$6,''total'',null,''count_basis'',''Source order; no cross-type unique-document count is inferred'')',cond)
  into payload using p_datasets,terms,p_docket,lim+1,off,lim;
 end if;
 return payload;
end; $$;
revoke all on function public.corpus_workspace_records(text[],text,jsonb,integer,integer,text) from public,anon,authenticated;
grant execute on function public.corpus_workspace_records(text[],text,jsonb,integer,integer,text) to service_role;

create or replace function public.corpus_workspace_courts(p_state text default '') returns jsonb
language sql stable security invoker set search_path='' as $$
 select coalesce(jsonb_agg(jsonb_build_object('id',c.court_id,'title',c.title,'state',c.state,'system',c.system,'type',c.court_type,'registry_key',c.registry_key,'parent_id',c.parent_id,'in_use',c.in_use,'end_date',c.end_date,'facts',case when p_state<>'' then c.facts else '[]'::jsonb end) order by c.state,c.system,c.court_type,c.title),'[]'::jsonb)
 from public.corpus_workspace_court_map c join public.corpus_datasets d on d.id=c.source_dataset and d.ready
 where p_state='' or c.state=p_state;
$$;
revoke all on function public.corpus_workspace_courts(text) from public,anon,authenticated;
grant execute on function public.corpus_workspace_courts(text) to service_role;

create or replace function public.corpus_workspace_dockets(p_mdl text default '') returns jsonb
language sql stable security invoker set search_path='' as $$
 with scoped as materialized(
 select l.* from public.corpus_workspace_docket_links l join public.corpus_datasets d on d.id=l.source_dataset and d.ready
 where p_mdl='' or l.mdl=p_mdl
 ), grouped as (
 select cl_docket_id,max(court_id) court_id,max(docket_number) docket_number,
 count(*) filter(where source_dataset='mdl_docket_activity') entries,
 count(*) filter(where source_dataset='mdl_docket_documents') documents,
 min(event_date) filter(where source_dataset='mdl_docket_activity') first_entered,
 max(event_date) filter(where source_dataset='mdl_docket_activity') last_entered,
 min(source_record_id) filter(where source_dataset='mdl_case_inventory') case_record_id,
 array_agg(distinct mdl) filter(where mdl<>'') mdls
 from scoped where cl_docket_id is not null group by cl_docket_id
 ), linked as(
 select count(*) n from scoped a where a.source_dataset='mdl_docket_activity' and a.entry_number is not null and exists(select 1 from scoped b where b.source_dataset='mdl_docket_documents' and b.cl_docket_id=a.cl_docket_id and b.entry_number=a.entry_number)
 )
 select jsonb_build_object('dockets',coalesce((select jsonb_agg(to_jsonb(g) order by entries desc,documents desc,cl_docket_id) from grouped g),'[]'::jsonb),'unresolved_records',(select count(*) from scoped where cl_docket_id is null),'entries_with_document_links',(select n from linked),'link_basis','Exact CourtListener docket identifier + entry number. Separate source inventories are retained; this does not establish complete dockets.');
$$;
revoke all on function public.corpus_workspace_dockets(text) from public,anon,authenticated;
grant execute on function public.corpus_workspace_dockets(text) to service_role;
notify pgrst,'reload schema';
