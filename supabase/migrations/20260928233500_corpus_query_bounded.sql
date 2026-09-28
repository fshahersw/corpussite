-- Listing query for multi-million-row collections. corpus_query counts every
-- match and its per-key filter test cannot use corpus_records_filters_idx, so a
-- listing over a two-million-row dataset exceeds the 8 s request timeout.
--
-- corpus_query_bounded serves one dataset and keeps corpus_query's per-row
-- filter test as the exact check, adding GIN-indexable containment for each
-- string filter (array or scalar record shape). It counts at most p_count_cap
-- matches: a match set within the cap is sorted in full; a larger one is paged
-- newest-first straight off corpus_records_ordinal_idx, where matches are dense
-- enough that a page needs a short scan. total is null when no selective filter
-- or search applies (callers know the published size); total_capped reports
-- that more than p_count_cap rows match. Special filters (date ranges,
-- __contains, __county, __ids, __date_any) raise so callers choose corpus_query.
create or replace function public.corpus_query_bounded(
  p_dataset text, p_filters jsonb default '{}', p_q text default '',
  p_limit integer default 25, p_offset integer default 0, p_count_cap integer default 10000
) returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
  lim integer := least(greatest(p_limit,1),500);
  off integer := greatest(p_offset,0);
  cap integer := least(greatest(p_count_cap,1),100000);
  exact constant text := 'r.dataset = $1 and not exists (select 1 from jsonb_each($2) f where not coalesce((r.filters -> f.key) @> f.value, false))';
  selective text := '';
  plain jsonb := '{}';
  terms tsquery;
  probe bigint;
  items jsonb;
  f record;
begin
  if not exists (select 1 from public.corpus_datasets d where d.id = p_dataset and d.ready) then
    return jsonb_build_object('total',0,'total_capped',false,'items','[]'::jsonb,'limit',lim,'offset',off);
  end if;
  for f in select key, value from jsonb_each(coalesce(p_filters,'{}'::jsonb)) loop
    if f.key like '\_\_%' escape '\' then
      if f.key <> '__prefix' then
        raise exception 'corpus_query_bounded does not support filter %', f.key using errcode = '22023';
      end if;
    elsif jsonb_typeof(f.value) <> 'string' then
      raise exception 'corpus_query_bounded filter % must be a single string', f.key using errcode = '22023';
    else
      plain := plain || jsonb_build_object(f.key, f.value);
      if f.key <> '_listing' then
        selective := selective || format(' and (r.filters @> %L::jsonb or r.filters @> %L::jsonb)',
          jsonb_build_object(f.key, jsonb_build_array(f.value)), jsonb_build_object(f.key, f.value));
      end if;
    end if;
  end loop;
  if coalesce(p_q,'') <> '' then
    if p_filters->>'__prefix' in ('true','1') then
      select coalesce(string_agg(quote_literal(lexeme) || ':*', ' & '), '')::tsquery
        into terms
        from (
          select lexeme
          from ts_debug('simple'::regconfig, left(p_q,300)) token
          cross join lateral unnest(token.lexemes) lexeme
          where token.alias not in ('asciihword','hword','numhword')
          limit 16
        ) query_lexemes;
    else terms := websearch_to_tsquery('simple', left(p_q,300)); end if;
    if numnode(terms) = 0 then
      return jsonb_build_object('total',0,'total_capped',false,'items','[]'::jsonb,'limit',lim,'offset',off);
    end if;
    selective := selective || ' and r.search_vector @@ $3';
  end if;
  if selective <> '' then
    execute format('select count(*) from (select 1 from public.corpus_records r where %s%s limit $4) s', exact, selective)
      into probe using p_dataset, plain, terms, cap + 1;
  end if;
  if probe <= cap then
    execute format('select coalesce(jsonb_agg(item order by ordinal, id), ''[]''::jsonb) from (select r.item, r.ordinal, r.id'
      ' from public.corpus_records r where %s%s order by r.ordinal + 0, r.id limit $4 offset $5) page', exact, selective)
      into items using p_dataset, plain, terms, lim, off;
  else
    -- Dense or unfiltered: walk the (dataset, ordinal, id) index and stop at the page.
    perform set_config('enable_bitmapscan', 'off', true);
    perform set_config('enable_seqscan', 'off', true);
    execute format('select coalesce(jsonb_agg(item order by ordinal, id), ''[]''::jsonb) from (select r.item, r.ordinal, r.id'
      ' from public.corpus_records r where %s%s order by r.ordinal, r.id limit $4 offset $5) page', exact, selective)
      into items using p_dataset, plain, terms, lim, off;
    perform set_config('enable_bitmapscan', 'on', true);
    perform set_config('enable_seqscan', 'on', true);
  end if;
  return jsonb_build_object('total', case when probe is not null then least(probe, cap) end, 'total_capped', coalesce(probe > cap, false),
    'items', items, 'limit', lim, 'offset', off);
end;
$$;
revoke all on function public.corpus_query_bounded(text,jsonb,text,integer,integer,integer) from public, anon, authenticated;
grant execute on function public.corpus_query_bounded(text,jsonb,text,integer,integer,integer) to service_role;
notify pgrst, 'reload schema';
