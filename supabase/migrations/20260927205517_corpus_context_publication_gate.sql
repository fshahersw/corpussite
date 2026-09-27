alter table public.corpus_context add column ready boolean not null default false;
select pg_notify('pgrst','reload schema');
