-- Portable regression after applying migrations to an isolated PostgreSQL test DB.
-- Requires psql with ON_ERROR_STOP=1; no pgTAP or application dependency needed.
-- Example: psql --set=ON_ERROR_STOP=1 --file=supabase/tests/corpus_search_vector.sql
-- Use normal libpq environment/service configuration for the test connection.
-- Temporary synthetic rows only; rollback. No corpus rows/readiness are modified.
begin;
set local statement_timeout = '60s';
set local lock_timeout = '5s';

create temporary table corpus_search_vector_regression (
  id integer primary key, title text not null, text text not null,
  note text not null default '',
  search_vector tsvector generated always as
    (to_tsvector('pg_catalog.simple'::regconfig, title || ' ' || left(text,1000000))) stored
) on commit drop;
create index corpus_search_vector_regression_gin on corpus_search_vector_regression using gin(search_vector);
insert into corpus_search_vector_regression(id,title,text) values (1,'Civil filing','Rules forms Fees 2026');
create temporary table corpus_search_vector_before on commit drop as
select search_vector,
  pg_relation_filenode('pg_temp.corpus_search_vector_regression'::regclass) table_file,
  pg_relation_filenode('pg_temp.corpus_search_vector_regression_gin'::regclass) index_file
from corpus_search_vector_regression where id=1;
alter table corpus_search_vector_regression alter column search_vector drop expression;
create trigger regression_search_vector_trg before insert or update of title,text,search_vector
on corpus_search_vector_regression for each row execute function public.corpus_set_search_vector();

do $$
declare
  body text;
  original_title text := 'Synthetic regression';
  candidate text;
  ordinary tsvector;
  bounded tsvector;
  failed_original boolean := false;
  msg text;
  before_row record;
  after_row record;
begin
  select * into before_row from corpus_search_vector_before;
  select * into after_row from corpus_search_vector_regression where id=1;
  if before_row.search_vector is distinct from after_row.search_vector
     or before_row.table_file is distinct from pg_relation_filenode('pg_temp.corpus_search_vector_regression'::regclass)
     or before_row.index_file is distinct from pg_relation_filenode('pg_temp.corpus_search_vector_regression_gin'::regclass) then
    raise exception 'DROP EXPRESSION failed stored-value or heap/index preservation check';
  end if;
  ordinary := to_tsvector('pg_catalog.simple'::regconfig, 'Civil filing Rules forms Fees 2026');
  if public.corpus_bounded_search_vector('Civil filing','Rules forms Fees 2026') is distinct from ordinary
     or public.corpus_bounded_search_vector('', '') is distinct from ''::tsvector
     or public.corpus_bounded_search_vector(null, 'body') is not null then
    raise exception 'Ordinary or null-input search-vector parity failed';
  end if;
  if public.corpus_bounded_search_vector('Règles — Gebühren','Sécurité § 12 民事訴訟') is distinct from
     to_tsvector('pg_catalog.simple'::regconfig,'Règles — Gebühren Sécurité § 12 民事訴訟') then
    raise exception 'Multilingual ordinary search-vector parity failed';
  end if;

  -- 110,000 distinct 9-character lexemes produce a real oversized tsvector.
  select string_agg('lex' || lpad(g::text,6,'0'),' ' order by g) into body
  from generate_series(1,110000) as g;
  candidate := original_title || ' ' || left(body,1000000);
  begin
    perform to_tsvector('pg_catalog.simple'::regconfig,candidate);
  exception when program_limit_exceeded then
    get stacked diagnostics msg = message_text;
    if msg !~ '^string is too long for tsvector \([0-9]+ bytes, max [0-9]+ bytes\)$' then raise; end if;
    failed_original := true;
  end;
  if not failed_original then raise exception 'Synthetic fixture did not reach the real tsvector size limit'; end if;
  bounded := public.corpus_bounded_search_vector(original_title,body);
  if bounded is distinct from to_tsvector('pg_catalog.simple'::regconfig,left(candidate,char_length(candidate)/2)) then
    raise exception 'Oversized search vector did not preserve the expected halved prefix';
  end if;
  insert into corpus_search_vector_regression(id,title,text,search_vector)
  values (2,original_title,body,'injected'::tsvector);
  select * into after_row from corpus_search_vector_regression where id=2;
  if after_row.text is distinct from body or after_row.title is distinct from original_title
     or after_row.search_vector is distinct from bounded then
    raise exception 'Trigger changed source text/title or accepted caller vector';
  end if;
  update corpus_search_vector_regression set search_vector='injected'::tsvector where id=1;
  if (select search_vector from corpus_search_vector_regression where id=1) is distinct from ordinary then
    raise exception 'Direct search-vector update bypassed recomputation';
  end if;
  update corpus_search_vector_regression set title='Updated filing',text='Service and fees' where id=1;
  if (select search_vector from corpus_search_vector_regression where id=1) is distinct from
     to_tsvector('pg_catalog.simple'::regconfig,'Updated filing Service and fees') then
    raise exception 'Title/body update did not recompute vector';
  end if;
  update corpus_search_vector_regression set note='unrelated metadata' where id=2;
  if (select search_vector from corpus_search_vector_regression where id=2) is distinct from bounded then
    raise exception 'Unrelated update changed a preserved vector';
  end if;
  if has_function_privilege('anon','public.corpus_bounded_search_vector(text,text)','EXECUTE')
     or has_function_privilege('authenticated','public.corpus_bounded_search_vector(text,text)','EXECUTE')
     or has_function_privilege('anon','public.corpus_set_search_vector()','EXECUTE')
     or has_function_privilege('authenticated','public.corpus_set_search_vector()','EXECUTE') then
    raise exception 'Search functions are exposed to client roles';
  end if;
  if not has_function_privilege('service_role','public.corpus_bounded_search_vector(text,text)','EXECUTE')
     or not has_function_privilege('service_role','public.corpus_set_search_vector()','EXECUTE')
     or exists(select 1 from pg_proc where oid in (
         'public.corpus_bounded_search_vector(text,text)'::regprocedure,
         'public.corpus_set_search_vector()'::regprocedure)
       and (prosecdef or proconfig is distinct from array['search_path=""']::text[])) then
    raise exception 'Server execution or invoker/search-path contract failed';
  end if;
end;
$$;
select jsonb_build_object('status','passed','fixture','synthetic_unique_lexemes',
  'ordinary_parity',true,'oversized_vector',true,'source_preserved',true,
  'heap_and_index_preserved',true,'trigger_recomputation',true,'client_grants_revoked',true) as regression;
rollback;
