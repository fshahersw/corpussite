-- Database size for import runners that must stay under the provisioned disk
-- (Supabase switches to read-only at 95% of it). Server-only.
create or replace function public.corpus_database_bytes()
returns bigint language sql stable security invoker set search_path = '' as $$
  select pg_database_size(current_database());
$$;
revoke all on function public.corpus_database_bytes() from public, anon, authenticated;
grant execute on function public.corpus_database_bytes() to service_role;
notify pgrst, 'reload schema';
