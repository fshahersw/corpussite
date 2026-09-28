-- Page identities before loading JSON payloads; retain native filtering and ready gates.
create or replace function public.corpus_documents_local(p_params jsonb default '{}',p_filters jsonb default '{}')
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare answer jsonb; lim integer=least(greatest(coalesce((p_params->>'limit')::integer,50),1),100);
 off integer=greatest(coalesce((p_params->>'offset')::integer,0),0); terms tsquery;
begin
 if coalesce(p_params->>'q','')<>'' then terms=phraseto_tsquery('simple',left(p_params->>'q',300)); end if;
 with matched as materialized (
 select r.id,r.dataset,r.filters->>'display_id' display_id,r.filters->'availability' availability,r.title from public.corpus_records r
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

 ), candidate_keys as materialized (
 -- The count/sort relation contains identities and small scalar metadata only.
 select m.dataset,m.id record_id,m.id,m.title,1::bigint sources,false grouped
 from matched m
 where p_params->>'view'='sources'
 and (coalesce(p_params->>'availability','')='' or coalesce(m.availability @> to_jsonb(p_params->>'availability'),false))
 union all
 select r.dataset,r.id,g.id,g.metadata->>'title',(g.metadata->>'source_count')::bigint,true
 from (select distinct display_id from matched where display_id is not null) membership
 join public.corpus_display_groups g on g.id=membership.display_id
 join public.corpus_records r on r.id=g.preferred_id
  and r.dataset=any(array['federal','focused','judge_enrichment','judge_entities','judge_vendor','pending_publication','provider_laws','seeger','trellis_browser_counties'])
 join public.corpus_datasets d on d.id=r.dataset and d.ready
 where coalesce(p_params->>'view','grouped')<>'sources'
 and (coalesce(p_params->>'availability','')='' or coalesce((r.filters->'availability') @> to_jsonb(p_params->>'availability'),false))
 ), selected as materialized (
 select * from candidate_keys order by lower(title),id limit lim offset off
 ), rendered as (
 -- Detoast/build item JSON only after the selected page is bounded to <=100 rows.
 select s.id,s.title,case when s.grouped then r.item || jsonb_build_object(
  'id',s.id,'title',s.title,'state',g.metadata->>'state','county',g.metadata->>'county',
  'source_count',s.sources::integer,'group_basis',g.metadata->>'group_basis','text_url','/api/text?id='||s.id)
  else r.item end item
 from selected s
 join public.corpus_records r on r.dataset=s.dataset and r.id=s.record_id
 left join public.corpus_display_groups g on s.grouped and g.id=s.id
 )
 select jsonb_build_object('total',(select count(*) from candidate_keys),
 'source_total',coalesce((select sum(sources) from candidate_keys),0),
 'items',coalesce((select jsonb_agg(item order by lower(title),id) from rendered),'[]'::jsonb)) into answer;
 return answer;
end;$$;
revoke all on function public.corpus_documents_local(jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.corpus_documents_local(jsonb,jsonb) to service_role;
