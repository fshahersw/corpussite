-- Exact saved-publisher outline. Access is limited to the authenticated server.
create table public.corpus_law_collections (
  state text not null, kind text not null, provisions bigint not null, headings bigint not null,
  primary key (state,kind)
);
create table public.corpus_law_nodes (
  id bigint primary key, state text not null, kind text not null, parent bigint not null,
  position integer not null, direct bigint not null, total bigint not null,
  label text, raw_label text, label_basis text, has_children boolean not null,
  ordered_rowids bigint[], metadata jsonb not null default '{}'
);
create index corpus_law_nodes_parent_idx on public.corpus_law_nodes(state,kind,parent,position);
create table public.corpus_law_segments (
  node bigint not null references public.corpus_law_nodes(id), lo bigint not null, hi bigint not null check(hi>=lo),
  primary key(node,lo)
);
create index corpus_law_segments_lo_idx on public.corpus_law_segments(lo desc);
alter table public.corpus_law_collections enable row level security;
alter table public.corpus_law_nodes enable row level security;
alter table public.corpus_law_segments enable row level security;
revoke all on public.corpus_law_collections,public.corpus_law_nodes,public.corpus_law_segments from public,anon,authenticated;
grant select,insert,update,delete on public.corpus_law_collections,public.corpus_law_nodes,public.corpus_law_segments to service_role;

create function public.corpus_law_path(p_node bigint) returns jsonb
language sql stable security invoker set search_path='' as $$
  with recursive chain as (
    select id,parent,coalesce(nullif(label,''),raw_label) label,0 depth from public.corpus_law_nodes where id=p_node
    union all
    select n.id,n.parent,coalesce(nullif(n.label,''),n.raw_label),c.depth+1
    from public.corpus_law_nodes n join chain c on n.id=c.parent where c.depth<200
  ) select coalesce(jsonb_agg(jsonb_build_object('id',id,'label',label) order by depth desc),'[]'::jsonb) from chain;
$$;

create function public.corpus_law_provision_rows(p_node bigint,p_offset integer,p_limit integer) returns jsonb
language plpgsql stable security invoker set search_path='' as $$
declare h public.corpus_law_nodes%rowtype; s record; answer jsonb='[]'; piece jsonb; skipped bigint=0;
  first_id bigint; take_count integer; remaining integer=least(greatest(p_limit,1),200);
begin
  select * into h from public.corpus_law_nodes where id=p_node;
  if not found then return answer; end if;
  if h.ordered_rowids is not null then
    select coalesce(jsonb_agg(jsonb_build_object('id',r.id,'title',r.title,'citation',coalesce(r.filters->>'citation',''),
      'status',coalesce(r.filters->>'status','')) order by wanted.position),'[]'::jsonb) into answer
    from unnest(h.ordered_rowids[greatest(p_offset,0)+1:greatest(p_offset,0)+remaining]) with ordinality wanted(rowid,position)
    join public.corpus_records r on r.dataset='open_us_law' and r.ordinal=wanted.rowid;
    return answer;
  end if;
  for s in select lo,hi from public.corpus_law_segments where node=p_node order by lo loop
    if skipped+s.hi-s.lo+1>greatest(p_offset,0) then
      first_id=s.lo+greatest(0,p_offset-skipped); take_count=least(s.hi-first_id+1,remaining);
      select coalesce(jsonb_agg(jsonb_build_object('id',r.id,'title',r.title,'citation',coalesce(r.filters->>'citation',''),
        'status',coalesce(r.filters->>'status','')) order by r.ordinal),'[]'::jsonb) into piece
      from public.corpus_records r where r.dataset='open_us_law' and r.ordinal between first_id and first_id+take_count-1;
      answer=answer||piece;remaining=remaining-take_count;
    end if;
    skipped=skipped+s.hi-s.lo+1;
    exit when remaining=0;
  end loop;
  return answer;
end;
$$;

create function public.corpus_law_outline(p_action text,p_state text default '',p_kind text default '',p_parent bigint default 0,
  p_node bigint default 0,p_id text default '',p_offset integer default 0,p_limit integer default 100) returns jsonb
language plpgsql stable security invoker set search_path='' as $$
declare meta jsonb; result jsonb; h public.corpus_law_nodes%rowtype; rows jsonb; num integer; walk bigint=greatest(p_parent,0);
  source_row bigint; source_state text; source_kind text; s public.corpus_law_segments%rowtype;
  location bigint; before_id bigint; after_id bigint; previous_item jsonb; next_item jsonb;
begin
  select data into meta from public.corpus_context where key='law_outline';
  if coalesce((meta->>'ready')::boolean,false)=false or not exists(select 1 from public.corpus_datasets where id='open_us_law' and ready) then
    return jsonb_build_object('available',false,'reason','The categorized law catalog and outline have not passed hosted publication checks.',
      'collections','[]'::jsonb,'nodes','[]'::jsonb,'results','[]'::jsonb);
  end if;
  if p_action in ('collections','children') and not (meta->'state_names' ? p_state) then
    return jsonb_build_object('available',false,'reason','unknown jurisdiction','collections','[]'::jsonb,'nodes','[]'::jsonb);
  end if;
  if p_action='collections' then
    select coalesce(jsonb_agg(jsonb_build_object('kind',kind,'label',coalesce(meta->'kind_labels'->>kind,kind),
      'provisions',provisions,'headings',headings) order by case kind when 'statutes' then 0 when 'constitutions' then 1
      when 'court_rules' then 2 when 'regulations' then 3 when 'guidance' then 4 else 5 end,provisions desc),'[]'::jsonb) into rows
    from public.corpus_law_collections where state=p_state;
    return jsonb_build_object('available',true,'state',meta->'state_names'->>p_state,'usps',p_state,'qualification',meta->>'qualification',
      'statute_audit',meta->'statute_audits'->p_state,'collections',rows);
  elsif p_action='children' then
    loop
      select count(*) into num from public.corpus_law_nodes where state=p_state and kind=p_kind and parent=walk;
      exit when num<>1;
      select * into h from public.corpus_law_nodes where state=p_state and kind=p_kind and parent=walk limit 1;
      exit when not h.has_children or h.direct<>0;walk=h.id;
    end loop;
    select coalesce(jsonb_agg(jsonb_build_object('id',id,'label',coalesce(nullif(label,''),nullif(raw_label,''),'Untitled heading'),
      'provisions',total,'direct',direct,'has_children',has_children,'label_basis',label_basis) order by position),'[]'::jsonb) into rows
    from public.corpus_law_nodes where state=p_state and kind=p_kind and parent=walk;
    return jsonb_build_object('available',true,'parent',walk,'path',public.corpus_law_path(walk),'nodes',rows);
  elsif p_action='provisions' then
    select * into h from public.corpus_law_nodes where id=p_node;
    if not found then return jsonb_build_object('available',true,'total',0,'results','[]'::jsonb,'path','[]'::jsonb);end if;
    result=jsonb_build_object('available',true,'node',p_node,'total',h.direct,'offset',greatest(p_offset,0),'limit',least(greatest(p_limit,1),200),
      'path',public.corpus_law_path(p_node),'results',public.corpus_law_provision_rows(p_node,p_offset,p_limit));
    if h.ordered_rowids is not null then result=result||jsonb_build_object('order','citation');end if;return result;
  elsif p_action='context' then
    if p_id not like 'oul:%' then return jsonb_build_object('available',false,'reason','not a law-collection record');end if;
    select ordinal,filters->>'state_usps',filters->>'kind' into source_row,source_state,source_kind
      from public.corpus_records where dataset='open_us_law' and id=p_id;
    if not found then return jsonb_build_object('available',false,'reason','record not found');end if;
    select * into s from public.corpus_law_segments where lo<=source_row order by lo desc limit 1;
    if not found or s.hi<source_row then return jsonb_build_object('available',false,'reason','record is outside the outline');end if;
    select * into h from public.corpus_law_nodes where id=s.node;
    if h.ordered_rowids is not null then
      location=array_position(h.ordered_rowids,source_row);before_id=h.ordered_rowids[location-1];after_id=h.ordered_rowids[location+1];
    else
      select coalesce(sum(hi-lo+1),0)+(source_row-s.lo)+1 into location from public.corpus_law_segments where node=s.node and hi<s.lo;
      if source_row>s.lo then before_id=source_row-1;else select max(hi) into before_id from public.corpus_law_segments where node=s.node and hi<source_row;end if;
      if source_row<s.hi then after_id=source_row+1;else select min(lo) into after_id from public.corpus_law_segments where node=s.node and lo>source_row;end if;
    end if;
    select jsonb_build_object('id',id,'title',title,'citation',coalesce(filters->>'citation','')) into previous_item
      from public.corpus_records where dataset='open_us_law' and ordinal=before_id;
    select jsonb_build_object('id',id,'title',title,'citation',coalesce(filters->>'citation','')) into next_item
      from public.corpus_records where dataset='open_us_law' and ordinal=after_id;
    return jsonb_build_object('available',true,'state',coalesce(meta->'state_names'->>source_state,source_state),'usps',source_state,
      'kind',source_kind,'kind_label',coalesce(meta->'kind_labels'->>source_kind,source_kind),'node',s.node,'path',public.corpus_law_path(s.node),
      'position',location,'of',h.direct,'previous',previous_item,'next',next_item);
  end if;
  return jsonb_build_object('available',false,'reason','Unknown outline action');
end;
$$;
revoke all on function public.corpus_law_path(bigint),public.corpus_law_provision_rows(bigint,integer,integer),
  public.corpus_law_outline(text,text,text,bigint,bigint,text,integer,integer) from public,anon,authenticated;
grant execute on function public.corpus_law_path(bigint),public.corpus_law_provision_rows(bigint,integer,integer),
  public.corpus_law_outline(text,text,text,bigint,bigint,text,integer,integer) to service_role;
notify pgrst,'reload schema';
