-- Unambiguous compound-key repairs are made only to the derived mapping, not the source rows.
with unique_dockets as(select court_id,docket_number,min(cl_docket_id) cl_id from public.corpus_workspace_docket_links where cl_docket_id is not null and court_id is not null and docket_number is not null group by court_id,docket_number having count(distinct cl_docket_id)=1)
update public.corpus_workspace_docket_links d set cl_docket_id=c.cl_id,linkage_basis='Exact court identifier + exact source docket number, uniquely corroborated by native CourtListener docket IDs in other saved source rows; source record remains unchanged',refreshed_at=now() from unique_dockets c where d.cl_docket_id is null and d.court_id=c.court_id and d.docket_number=c.docket_number;
update public.corpus_workspace_docket_links set entry_number=null where entry_number is not null and entry_number !~ '^[0-9]+$';
update public.corpus_workspace_docket_links set event_date=null where event_date='';
create or replace function public.corpus_workspace_entry_relations(p_dataset text,p_record text) returns jsonb language sql stable security invoker set search_path='' as $$
 with original as(select l.* from public.corpus_workspace_docket_links l join public.corpus_datasets d on d.id=l.source_dataset and d.ready where l.source_dataset=p_dataset and l.source_record_id=p_record and l.entry_number is not null), related as(
 select distinct b.source_dataset,b.source_record_id from original a join public.corpus_workspace_docket_links b on b.cl_docket_id=a.cl_docket_id and b.entry_number=a.entry_number and b.source_dataset<>a.source_dataset join public.corpus_datasets d on d.id=b.source_dataset and d.ready
 )
 select jsonb_build_object('items',coalesce((select jsonb_agg(jsonb_build_object('dataset',r.dataset,'record_id',r.id,'item',r.item)) from (select r.dataset,r.id,r.item from related b join public.corpus_records r on r.dataset=b.source_dataset and r.id=b.source_record_id order by r.dataset,r.id limit 100) r),'[]'::jsonb),'basis','Exact native CourtListener docket ID + numeric docket-entry number. Source filing dates and parsed entered dates remain distinct.','total',(select count(*) from related));
$$;
revoke all on function public.corpus_workspace_entry_relations(text,text) from public,anon,authenticated;
grant execute on function public.corpus_workspace_entry_relations(text,text) to service_role;
notify pgrst,'reload schema';
