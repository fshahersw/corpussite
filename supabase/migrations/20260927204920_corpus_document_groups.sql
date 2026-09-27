create table public.corpus_display_groups (
 id text primary key, preferred_id text not null, metadata jsonb not null
);
alter table public.corpus_display_groups enable row level security;
revoke all on public.corpus_display_groups from public,anon,authenticated;
grant all on public.corpus_display_groups to service_role;
create index corpus_records_display on public.corpus_records ((filters->>'display_id'));
create index corpus_records_source_rowid on public.corpus_records (dataset,ordinal);

create or replace function public.corpus_documents_local(p_params jsonb default '{}',p_filters jsonb default '{}')
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare answer jsonb; lim integer=least(greatest(coalesce((p_params->>'limit')::integer,50),1),100);
 off integer=greatest(coalesce((p_params->>'offset')::integer,0),0); terms tsquery;
begin
 if coalesce(p_params->>'q','')<>'' then terms=phraseto_tsquery('simple',left(p_params->>'q',300)); end if;
 with matched as not materialized (
 select r.id,r.dataset,r.item,r.filters,r.title,r.ordinal from public.corpus_records r
 join public.corpus_datasets d on d.id=r.dataset and d.ready
 where r.dataset=any(array['federal','focused','judge_enrichment','judge_entities','judge_vendor','pending_publication','provider_laws','seeger','trellis_browser_counties'])
 and (coalesce(p_params->>'dataset','')='' or r.dataset=p_params->>'dataset')
 and (coalesce(p_params->>'group','all')='all' or coalesce((r.filters->'groups') @> to_jsonb(p_params->>'group'),false))
 and (coalesce(p_params->>'state','')='' or position('; '||(p_params->>'state')||'; ' in '; '||r.state||'; ')>0)
 and (coalesce(p_params->>'county','')='' or r.county_geoids @> array[p_params->>'county'])
 and (terms is null or r.search_vector @@ terms)
 and (case when p_params->>'view'='sources' then r.dataset<>'judge_entities' else coalesce(r.filters->>'retrieval_eligible','true') not in ('false','0') or p_params->>'kind'='administrative_document' end)
 and not exists(select 1 from jsonb_each(p_filters) f where not(case when jsonb_typeof(f.value)='array' then exists(select 1 from jsonb_array_elements(f.value) v where coalesce((r.filters->f.key) @> v,false)) else coalesce((r.filters->f.key) @> f.value,false) end))
 and (coalesce(p_params->>'date_type','')='' or
  ((r.filters->>(p_params->>'date_type')) is null and coalesce(p_params->>'undated','1')<>'0') or
  ((r.filters->>(p_params->>'date_type')) is not null
   and (coalesce(p_params->>'dfrom','')='' or r.filters->>(p_params->>'date_hi') >= p_params->>'dfrom')
   and (coalesce(p_params->>'dto','')='' or r.filters->>(p_params->>'date_lo') <= p_params->>'dto')))
 ), groups as not materialized (
 select g.id,g.metadata,r.item,g.metadata->>'title' title
 from public.corpus_display_groups g join public.corpus_records r on r.id=g.preferred_id
 join public.corpus_datasets d on d.id=r.dataset and d.ready
 where exists(select 1 from matched m where m.filters->>'display_id'=g.id)
 and (coalesce(p_params->>'availability','')='' or coalesce((r.filters->'availability') @> to_jsonb(p_params->>'availability'),false))
 ), candidates as (
 select m.id,m.item,m.title,1::bigint sources from matched m
 where p_params->>'view'='sources' and (coalesce(p_params->>'availability','')='' or coalesce((m.filters->'availability') @> to_jsonb(p_params->>'availability'),false))
 union all
 select g.id,g.item || jsonb_build_object('id',g.id,'title',g.metadata->>'title','state',g.metadata->>'state','county',g.metadata->>'county','source_count',(g.metadata->>'retained_members')::integer,'group_basis',g.metadata->>'group_basis','text_url','/api/text?id='||g.id),g.title,(g.metadata->>'retained_members')::bigint
 from groups g where coalesce(p_params->>'view','grouped')<>'sources'
 ), selected as(select * from candidates order by lower(title),id limit lim offset off)
 select jsonb_build_object('total',(select count(*) from candidates),'source_total',coalesce((select sum(sources) from candidates),0),
 'items',coalesce((select jsonb_agg(item) from selected),'[]'::jsonb)) into answer;
 return answer;
end;$$;
revoke all on function public.corpus_documents_local(jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.corpus_documents_local(jsonb,jsonb) to service_role;

create or replace function public.corpus_group_detail(p_id text,p_full boolean default false)
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare g public.corpus_display_groups; result jsonb; sources jsonb;
begin
 select * into g from public.corpus_display_groups where id=p_id;
 if not found then return null; end if;
 result=public.corpus_detail(g.preferred_id,array['federal','focused','judge_enrichment','judge_entities','judge_vendor','pending_publication','provider_laws','seeger','trellis_browser_counties'],p_full);
 if result is null then return null; end if;
 select jsonb_agg(r.item order by lower(r.title),r.id) into sources from public.corpus_records r join public.corpus_datasets d on d.id=r.dataset and d.ready
 where r.id in (select jsonb_array_elements_text(g.metadata->'member_ids'));
 return result || jsonb_build_object('id',g.id,'title',g.metadata->>'title','state',g.metadata->>'state','county',g.metadata->>'county','source_count',g.metadata->'retained_members','group_basis',g.metadata->>'group_basis','text_url','/api/text?id='||g.id,'source_records',coalesce(sources,'[]'::jsonb));
end;$$;
revoke all on function public.corpus_group_detail(text,boolean) from public,anon,authenticated;
grant execute on function public.corpus_group_detail(text,boolean) to service_role;
