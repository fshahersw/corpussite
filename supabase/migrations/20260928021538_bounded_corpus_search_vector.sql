-- Keep existing stored vectors and their GIN index; do not rewrite corpus text.
-- DROP EXPRESSION retains column values (PostgreSQL ALTER TABLE documentation).
-- Root applies this while import writers are stopped. No readiness gates change.
begin;
set local lock_timeout = '5s';

create function public.corpus_bounded_search_vector(p_title text, p_text text)
returns pg_catalog.tsvector
language plpgsql immutable strict parallel safe security invoker
set search_path = ''
as $$
declare
  candidate text := p_title || ' ' || pg_catalog.left(p_text, 1000000);
  prefix_characters integer := pg_catalog.char_length(candidate);
  failure_message text;
begin
  loop
    begin
      -- The first attempt is exactly the previous stored generation expression.
      return pg_catalog.to_tsvector('pg_catalog.simple'::pg_catalog.regconfig, candidate);
    exception when program_limit_exceeded then
      get stacked diagnostics failure_message = message_text;
      -- Do not mask any other 54000 condition, memory error, timeout or data error.
      if failure_message !~ '^string is too long for tsvector \([0-9]+ bytes, max [0-9]+ bytes\)$'
         or prefix_characters = 0 then
        raise;
      end if;
      prefix_characters := prefix_characters / 2;
      candidate := pg_catalog.left(candidate, prefix_characters);
    end;
  end loop;
end;
$$;

create function public.corpus_set_search_vector()
returns trigger
language plpgsql security invoker
set search_path = ''
as $$
begin
  new.search_vector := public.corpus_bounded_search_vector(new.title, new.text);
  return new;
end;
$$;

revoke all on function public.corpus_bounded_search_vector(text, text) from public, anon, authenticated;
revoke all on function public.corpus_set_search_vector() from public, anon, authenticated;
grant execute on function public.corpus_bounded_search_vector(text, text) to service_role;
grant execute on function public.corpus_set_search_vector() to service_role;

-- Metadata-only conversion: existing vectors/GIN entries remain in place.
alter table public.corpus_records alter column search_vector drop expression;
create trigger corpus_records_search_vector_trg
before insert or update of title, text, search_vector
on public.corpus_records
for each row execute function public.corpus_set_search_vector();

comment on function public.corpus_bounded_search_vector(text, text) is
  'Preserves the original simple search vector when it fits; only a confirmed tsvector byte-limit error halves the searchable candidate prefix. Source text is unchanged.';
commit;
