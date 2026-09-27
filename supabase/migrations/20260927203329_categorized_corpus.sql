-- Categorized, source-bound research catalog. The Vercel server authenticates readers.
-- No anonymous table/function access; ingestion uses the server secret only.
create table public.corpus_datasets (
  id text primary key,
  label text not null,
  ready boolean not null default false,
  expected_records bigint not null default 0,
  imported_records bigint not null default 0,
  manifest_sha256 text,
  metadata jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table public.corpus_records (
  dataset text not null references public.corpus_datasets(id),
  id text not null,
  category text not null check (category not in ('', 'unknown', 'uncategorized')),
  state text,
  county_geoids text[] not null default '{}',
  title text not null default '',
  source_url text,
  ordinal bigint not null default 0,
  item jsonb not null,
  detail jsonb not null default '{}',
  text text not null default '',
  filters jsonb not null default '{}',
  search_vector tsvector generated always as
    (to_tsvector('simple'::regconfig, title || ' ' || left(text, 1000000))) stored,
  primary key (dataset, id)
);
create index corpus_records_id_idx on public.corpus_records(id);
create index corpus_records_category_state_idx on public.corpus_records(category, state);
create index corpus_records_ordinal_idx on public.corpus_records(dataset, ordinal, id);
create index corpus_records_title_idx on public.corpus_records(dataset, lower(title), id);
create index corpus_records_filters_idx on public.corpus_records using gin(filters jsonb_path_ops);
create index corpus_records_counties_idx on public.corpus_records using gin(county_geoids);
create index corpus_records_search_idx on public.corpus_records using gin(search_vector);

create table public.corpus_artifacts (
  route text primary key,
  sha256 text not null check (sha256 ~ '^[a-f0-9]{64}$'),
  object_key text not null,
  bytes bigint not null check (bytes >= 0),
  mime text not null default 'application/octet-stream',
  filename text,
  ready boolean not null default false
);
create index corpus_artifacts_sha_idx on public.corpus_artifacts(sha256);

create table public.corpus_context (
  key text primary key,
  data jsonb not null,
  source_sha256 text,
  captured_at timestamptz not null default now()
);

alter table public.corpus_datasets enable row level security;
alter table public.corpus_records enable row level security;
alter table public.corpus_artifacts enable row level security;
alter table public.corpus_context enable row level security;
revoke all on public.corpus_datasets, public.corpus_records, public.corpus_artifacts, public.corpus_context from anon, authenticated, public;
grant select, insert, update, delete on public.corpus_datasets, public.corpus_records, public.corpus_artifacts, public.corpus_context to service_role;

create function public.corpus_query(
  p_datasets text[] default null, p_filters jsonb default '{}', p_q text default '',
  p_limit integer default 25, p_offset integer default 0, p_sort text default 'ordinal'
) returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
  answer jsonb;
  terms tsquery;
begin
  if p_q <> '' then terms := websearch_to_tsquery('simple', left(p_q,300)); end if;
  with matched as (
    select r.* from public.corpus_records r
    join public.corpus_datasets d on d.id = r.dataset and d.ready
    where (p_datasets is null or r.dataset = any(p_datasets))
      and (terms is null or r.search_vector @@ terms)
      and not exists (
        select 1 from jsonb_each(p_filters) f
        where f.key not like '\_\_%' escape '\'
          and not coalesce((r.filters -> f.key) @> f.value, false)
      )
      and (not (p_filters ? '__county') or r.county_geoids @> array[p_filters->>'__county'])
      and (not (p_filters ? '__dfrom') or coalesce(r.filters->>(p_filters->>'__date_type'),'') >= p_filters->>'__dfrom')
      and (not (p_filters ? '__dto') or coalesce(r.filters->>(p_filters->>'__date_type'),'') <= (p_filters->>'__dto') || 'T23:59:59.999999Z')
  ), selected as (
    select * from matched
    order by
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

create function public.corpus_detail(p_id text, p_datasets text[] default null, p_full boolean default false)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select r.detail || jsonb_build_object('text',case when p_full then r.text else left(r.text,60000) end,
    'text_characters',length(r.text),'text_truncated',not p_full and length(r.text)>60000)
  from public.corpus_records r join public.corpus_datasets d on d.id=r.dataset and d.ready
  where r.id=p_id and (p_datasets is null or r.dataset=any(p_datasets))
  order by r.dataset limit 1;
$$;
revoke all on function public.corpus_detail(text,text[],boolean) from public, anon, authenticated;
grant execute on function public.corpus_detail(text,text[],boolean) to service_role;

notify pgrst, 'reload schema';
