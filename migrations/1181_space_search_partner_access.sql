-- Saved searches share partner deal-read eligibility with client needs.
-- rollback: forward-only; restore prior guards in a successor migration.
-- lock-review: function replacements only; no table rewrite or data mutation.
set local lock_timeout='5s';
set local statement_timeout='30s';

create function ops.space_search_deal_visible(p_tenant text,p_deal uuid,p_actor text) returns boolean
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 select p_tenant='carr-internal'
 and current_setting('carr.organization_tenant_id',true)=p_tenant
 and exists(
   select 1 from public.deal d join public.client c on c.id=d.client_id
   join public.party p on p.id=c.party_id
   cross join lateral (select nullif(current_setting('carr.space_search_deal_access',true),'')::jsonb decision) ctx
   join public.actor a on a.slug=ctx.decision->>'slug' and a.active
   join public.actor sponsor on sponsor.slug=ctx.decision->>'partner' and sponsor.active
   where d.id=p_deal and c.merged_into is null and p.merged_into is null and p.deleted_at is null
   and a.slug=current_setting('carr.acting_actor_slug',true)
   and sponsor.slug=current_setting('carr.verified_human_actor_slug',true)
   and p_actor in (a.slug,a.id::text)
   and ctx.decision->'deal'=jsonb_build_object('id',d.id,'version',d.version)
 );
$$;
revoke all on function ops.space_search_deal_visible(text,uuid,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.read_deal_space_searches(p_tenant text,p_actor text,p_deal uuid,p_search uuid) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare output jsonb;
begin
 if p_deal is null or not coalesce(ops.space_search_deal_visible(p_tenant,p_deal,p_actor),false) then
   raise exception 'space_search_unavailable'; end if;
 select jsonb_build_object('searches',coalesce(jsonb_agg(ops.deal_space_search_projection(s) order by s.updated_at desc,s.id),'[]'),
 'truncated',(select count(*)>100 from ops.deal_space_search where organization_tenant_id=p_tenant and deal_id=p_deal and (p_search is null or id=p_search)))
 into output from (select * from ops.deal_space_search where organization_tenant_id=p_tenant and deal_id=p_deal
 and (p_search is null or id=p_search) order by updated_at desc,id limit 100) s;
 if p_search is not null and jsonb_array_length(output->'searches')=0 then raise exception 'space_search_unavailable'; end if;
 return output;
end $$;

create or replace function ops.save_deal_space_search(p_tenant text,p_actor text,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare s ops.deal_space_search; d uuid:=(p_args->>'deal_id')::uuid; sid uuid:=(p_args->>'search_id')::uuid;
begin
 if d is null or not coalesce(ops.space_search_deal_visible(p_tenant,d,p_actor),false) then raise exception 'space_search_unavailable'; end if;
 if p_args->'client_prefill_source'<>'null'::jsonb and not exists(select 1 from public.deal
 where id=d and client_id=(p_args->'client_prefill_source'->>'client_id')::uuid) then raise exception 'space_search_prefill_mismatch'; end if;
 if (p_args->>'expected_version')::integer=0 then
   insert into ops.deal_space_search(id,organization_tenant_id,deal_id,name,status,criteria,requirements,client_prefill_source)
   values(sid,p_tenant,d,p_args->>'name',p_args->>'status',p_args->'criteria',p_args->>'requirements',nullif(p_args->'client_prefill_source','null'))
   on conflict(id) do nothing returning * into s;
   if s.id is null then raise exception 'space_search_version_conflict'; end if;
 else
   select * into s from ops.deal_space_search where id=sid and deal_id=d and organization_tenant_id=p_tenant for update;
   if s.id is null then raise exception 'space_search_unavailable'; end if;
   if s.version<>(p_args->>'expected_version')::integer then raise exception 'space_search_version_conflict'; end if;
   update ops.deal_space_search set name=p_args->>'name',status=p_args->>'status',criteria=p_args->'criteria',
   requirements=p_args->>'requirements',client_prefill_source=nullif(p_args->'client_prefill_source','null'),version=version+1,updated_at=now()
   where id=sid returning * into s;
 end if;
 return ops.deal_space_search_projection(s);
end $$;

create or replace function ops.run_deal_space_search(p_tenant text,p_actor text,p_deal uuid,p_search uuid,p_version integer) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare s ops.deal_space_search; result jsonb;
begin
 if p_deal is null or not coalesce(ops.space_search_deal_visible(p_tenant,p_deal,p_actor),false) then raise exception 'space_search_unavailable'; end if;
 select * into s from ops.deal_space_search where id=p_search and deal_id=p_deal and organization_tenant_id=p_tenant for update;
 if s.id is null then raise exception 'space_search_unavailable'; end if;
 if s.version<>p_version then raise exception 'space_search_version_conflict'; end if;
 if s.status<>'saved' then raise exception 'space_search_not_runnable'; end if;
 result:=ops.search_tour_properties(p_tenant,p_actor,s.criteria);
 if result is null or jsonb_typeof(result->'items')<>'array' then raise exception 'space_search_run_unavailable'; end if;
 update ops.deal_space_search set version=version+1,last_run_at=now(),run_criteria=criteria,
 result_property_ids=coalesce((select jsonb_agg(item->'property_id') from jsonb_array_elements(result->'items') item),'[]'),
 has_more=(result->>'has_more')::boolean,next_cursor=result->>'cursor',updated_at=now()
 where id=p_search returning * into s;
 return ops.deal_space_search_projection(s);
end $$;
