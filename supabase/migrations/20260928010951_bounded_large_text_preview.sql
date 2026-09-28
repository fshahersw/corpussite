-- Exact complete reader text remains in hash-verified private Storage artifacts.
-- The database keeps the same first 1,000,000 searchable characters as before.
-- Do not claim p_full returns the entire document when only a preview is stored.
create or replace function public.corpus_detail(p_id text, p_datasets text[] default null, p_full boolean default false)
returns jsonb language sql stable security invoker set search_path = '' as $$
  with selected as (
    select r.detail,r.text,
      case when r.detail->>'full_text_offloaded'='true'
                and r.detail->>'full_text_characters' ~ '^[0-9]{1,18}$'
        then greatest((r.detail->>'full_text_characters')::bigint,length(r.text)::bigint)
        else length(r.text)::bigint end as complete_characters
    from public.corpus_records r join public.corpus_datasets d on d.id=r.dataset and d.ready
    where r.id=p_id and (p_datasets is null or r.dataset=any(p_datasets))
    order by r.dataset limit 1
  ), preview as (
    select *,case when p_full then text else left(text,60000) end as returned_text
    from selected
  )
  select detail || jsonb_build_object(
    'text',returned_text,
    'text_characters',complete_characters,
    'text_preview_characters',length(returned_text),
    'text_truncated',length(returned_text)<complete_characters
  ) from preview;
$$;
revoke all on function public.corpus_detail(text,text[],boolean) from public, anon, authenticated;
grant execute on function public.corpus_detail(text,text[],boolean) to service_role;
notify pgrst, 'reload schema';
