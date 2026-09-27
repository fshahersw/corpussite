create or replace function public.corpus_query(
  p_datasets text[] default null, p_filters jsonb default '{}', p_q text default '',
  p_limit integer default 25, p_offset integer default 0, p_sort text default 'ordinal'
) returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
  answer jsonb;
  terms tsquery;
begin
  if p_q <> '' then
    if p_filters->>'__prefix' in ('true','1') then
      terms := to_tsquery('simple',coalesce(array_to_string(array(select quote_literal(t[1]) || ':*' from regexp_matches(lower(left(p_q,300)),'[[:alnum:]]+','g') t),' & '),''));
    else terms := websearch_to_tsquery('simple',left(p_q,300)); end if;
  end if;
  with matched as not materialized (
    select r.* from public.corpus_records r
    join public.corpus_datasets d on d.id = r.dataset and d.ready
    where (p_datasets is null or r.dataset = any(p_datasets))
      and (terms is null or r.search_vector @@ terms)
      and not exists (
        select 1 from jsonb_each(p_filters) f
        where f.key not like '\_\_%' escape '\'
          and not (case when jsonb_typeof(f.value)='array' then exists(select 1 from jsonb_array_elements(f.value) v where coalesce((r.filters -> f.key) @> v, false)) else coalesce((r.filters -> f.key) @> f.value, false) end)
      )
      and not exists (select 1 from jsonb_each_text(coalesce(p_filters->'__contains','{}'::jsonb)) f where position(lower(f.value) in lower(coalesce(r.filters->>f.key,''))) = 0)
      and (not (p_filters ? '__county') or r.county_geoids @> array[p_filters->>'__county'])
      and (p_filters ? '__date_any' or not (p_filters ? '__dfrom') or coalesce(r.filters->>(p_filters->>'__date_type'),'') >= p_filters->>'__dfrom')
      and (p_filters ? '__date_any' or not (p_filters ? '__dto') or coalesce(r.filters->>(p_filters->>'__date_type'),'') <= (p_filters->>'__dto') || 'T23:59:59.999999Z')
    and (not (p_filters ? '__date_any') or exists (
        select 1 from jsonb_array_elements_text(case when jsonb_typeof(r.filters->(p_filters->>'__date_any'))='array' then r.filters->(p_filters->>'__date_any') else jsonb_build_array(r.filters->(p_filters->>'__date_any')) end) v
        where v is not null and (not(p_filters ? '__dfrom') or v>=p_filters->>'__dfrom')
        and (not(p_filters ? '__dto') or v<=(p_filters->>'__dto')||'T23:59:59.999999Z')
      ))
  ), selected as (
    select * from matched
    order by
      case when p_sort='rank' then (filters->>'__rank')::bigint end,
      case when p_sort='title' then lower(title) end,
      case when p_sort='title_desc' then lower(title) end desc,
      case when p_sort='date_desc' then filters->>'date' end desc nulls last,
      ordinal, id
    limit least(greatest(p_limit,1),500) offset greatest(p_offset,0)
  )
  select jsonb_build_object('total',(select count(*) from matched),
    'items',coalesce((select jsonb_agg(item) from selected),'[]'::jsonb),
    'limit',least(greatest(p_limit,1),500),'offset',greatest(p_offset,0)) into answer;
  return answer;
end;
$$;
revoke all on function public.corpus_query(text[],jsonb,text,integer,integer,text) from public, anon, authenticated;
grant execute on function public.corpus_query(text[],jsonb,text,integer,integer,text) to service_role;
