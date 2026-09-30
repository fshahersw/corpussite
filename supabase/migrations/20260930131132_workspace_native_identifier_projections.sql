-- Additive projections. Original source rows, publication gates and ACLs are untouched.
create table if not exists public.corpus_workspace_court_map (
  court_id text primary key,
  state text not null default '',
  title text not null,
  system text,
  court_type text,
  registry_key text,
  parent_id text,
  in_use text,
  end_date text,
  facts jsonb not null default '[]',
  source_dataset text not null default 'court_spine',
  source_record_id text not null,
  refreshed_at timestamptz not null default now()
);
alter table public.corpus_workspace_court_map enable row level security;
revoke all on public.corpus_workspace_court_map from public,anon,authenticated;
grant select,insert,update,delete on public.corpus_workspace_court_map to service_role;
create index if not exists corpus_workspace_court_state on public.corpus_workspace_court_map(state,court_id);

create table if not exists public.corpus_workspace_docket_links (
  source_dataset text not null,
  source_record_id text not null,
  mdl text not null default '',
  cl_docket_id text,
  court_id text,
  docket_number text,
  entry_number text,
  event_date text,
  date_basis text,
  document_type text,
  evidence_url text,
  linkage_basis text not null,
  refreshed_at timestamptz not null default now(),
  primary key(source_dataset,source_record_id,mdl)
);
alter table public.corpus_workspace_docket_links enable row level security;
revoke all on public.corpus_workspace_docket_links from public,anon,authenticated;
grant select,insert,update,delete on public.corpus_workspace_docket_links to service_role;
create index if not exists corpus_workspace_docket_mdl on public.corpus_workspace_docket_links(mdl,cl_docket_id,entry_number);
create index if not exists corpus_workspace_docket_native on public.corpus_workspace_docket_links(cl_docket_id,entry_number);

-- Normalize only explicitly labelled facts. No name-only or hostname joins.
insert into public.corpus_workspace_court_map(court_id,state,title,system,court_type,registry_key,parent_id,in_use,end_date,facts,source_record_id)
select r.id,r.state,r.title,coalesce(r.filters->'system'->>0,r.filters->>'system'),coalesce(r.filters->'type'->>0,r.filters->>'type'),
 split_part(f.fact_data->>'Local registry key',' ',1),
 substring(f.fact_data->>'Parent court (CourtListener)' from '\(([^()]+)\)$'),
 f.fact_data->>'In use (CourtListener flag)',f.fact_data->>'Ended (CourtListener end_date)',coalesce(r.detail->'facts','[]'),r.id
from public.corpus_records r
join public.corpus_datasets d on d.id=r.dataset and d.ready
cross join lateral (select coalesce(jsonb_object_agg(v->>0,v->>1),'{}') as fact_data from jsonb_array_elements(coalesce(r.detail->'facts','[]')) v where jsonb_typeof(v)='array') f
where r.dataset='court_spine'
on conflict(court_id) do update set state=excluded.state,title=excluded.title,system=excluded.system,court_type=excluded.court_type,registry_key=excluded.registry_key,parent_id=excluded.parent_id,in_use=excluded.in_use,end_date=excluded.end_date,facts=excluded.facts,refreshed_at=now();

insert into public.corpus_workspace_docket_links(source_dataset,source_record_id,mdl,cl_docket_id,court_id,docket_number,entry_number,event_date,date_basis,document_type,evidence_url,linkage_basis)
select r.dataset,r.id,coalesce(m.mdl,''),
 coalesce(substring(u.url from '/docket/([0-9]+)/'),f.fact_data->>'CourtListener docket id'),
 coalesce(f.fact_data->>'Court',f.fact_data->>'Court (CourtListener id)',substring(f.member_docket from '\(([^()]+)\)$')),
 coalesce(f.fact_data->>'Docket number',f.fact_data->>'Docket number (AWS release)',regexp_replace(f.member_docket,' \([^()]+\)$','')),
 coalesce(r.item->'cells'->>'entry_number',f.fact_data->>'Entry number'),
 coalesce(r.item->'cells'->>'published_at',r.item->'cells'->>'entry_date_filed',r.item->'cells'->>'filed'),
 case r.dataset when 'mdl_docket_activity' then 'entered_date_parsed_from_description' when 'mdl_docket_documents' then 'entry_filed_date' else 'case_filed_date' end,
 coalesce(r.item->'cells'->>'entry_type',r.item->'cells'->>'doc_type'),u.url,
 'Exact native CourtListener docket URL/identifier; entry number as recorded; MDL from the existing source crosswalk'
from public.corpus_records r
join public.corpus_datasets d on d.id=r.dataset and d.ready
cross join lateral (
 select coalesce(jsonb_object_agg(v->>0,v->>1),'{}') as fact_data,
 max(v->>1) filter(where (v->>0) like 'Member docket (%') as member_docket
 from jsonb_array_elements(coalesce(r.detail->'facts','[]')) v where jsonb_typeof(v)='array'
) f
left join lateral (
 select l->>'url' as url from jsonb_array_elements(coalesce(r.item->'links','[]')) l
 where l->>'url' ~ '^https://(www\.)?courtlistener\.com/docket/[0-9]+/' limit 1
) u on true
left join lateral (
 select value as mdl from jsonb_array_elements_text(case jsonb_typeof(r.filters->'mdl') when 'array' then r.filters->'mdl' when 'string' then jsonb_build_array(r.filters->'mdl') else '[]'::jsonb end)
 where value ~ '^[1-9][0-9]{0,5}$'
) m on true
where r.dataset in ('mdl_docket_activity','mdl_docket_documents','mdl_case_inventory')
on conflict(source_dataset,source_record_id,mdl) do update set cl_docket_id=excluded.cl_docket_id,court_id=excluded.court_id,docket_number=excluded.docket_number,entry_number=excluded.entry_number,event_date=excluded.event_date,date_basis=excluded.date_basis,document_type=excluded.document_type,evidence_url=excluded.evidence_url,refreshed_at=now();

