-- Deal-bound saved criteria and canonical property-search snapshots.
-- rollback: forward-only; retire consumers before dropping this additive table.
-- lock-review: new table and functions only; no existing table rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

create table ops.deal_space_search (
 id uuid primary key,
 organization_tenant_id text not null,
 deal_id uuid not null references public.deal(id),
 version integer not null default 1 check(version>0),
 name text not null check(length(name) between 1 and 240),
 status text not null check(status in ('draft','saved','archived')),
 criteria jsonb not null check(jsonb_typeof(criteria)='object'),
 requirements text not null check(length(requirements)<=8000),
 client_prefill_source jsonb,
 last_run_at timestamptz,
 run_criteria jsonb,
 result_property_ids jsonb not null default '[]' check(jsonb_typeof(result_property_ids)='array' and jsonb_array_length(result_property_ids)<=100),
 has_more boolean not null default false,
 next_cursor text,
 updated_at timestamptz not null default now()
);
create index deal_space_search_deal on ops.deal_space_search(organization_tenant_id,deal_id,updated_at desc,id);
revoke all on ops.deal_space_search from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.deal_space_search_projection(s ops.deal_space_search) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
 select to_jsonb(s)-'organization_tenant_id' || jsonb_build_object('state',case
 when s.status='archived' then 'archived'
 when s.status='draft' then 'draft'
 when s.last_run_at is not null and (s.criteria is distinct from s.run_criteria or s.last_run_at<=now()-interval '24 hours') then 'stale'
 else 'saved' end)
$$;
revoke all on function ops.deal_space_search_projection(ops.deal_space_search) from public;

create function ops.read_deal_space_searches(p_tenant text,p_actor text,p_deal uuid,p_search uuid) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare output jsonb;
begin
 if p_deal is null or not coalesce(ops.tour_deal_visible(p_tenant,p_deal,p_actor),false) then
   raise exception 'space_search_unavailable'; end if;
 select jsonb_build_object('searches',coalesce(jsonb_agg(ops.deal_space_search_projection(s) order by s.updated_at desc,s.id),'[]'),
 'truncated',(select count(*)>100 from ops.deal_space_search where organization_tenant_id=p_tenant and deal_id=p_deal and (p_search is null or id=p_search)))
 into output from (select * from ops.deal_space_search where organization_tenant_id=p_tenant and deal_id=p_deal
 and (p_search is null or id=p_search) order by updated_at desc,id limit 100) s;
 if p_search is not null and jsonb_array_length(output->'searches')=0 then raise exception 'space_search_unavailable'; end if;
 return output;
end $$;

create function ops.save_deal_space_search(p_tenant text,p_actor text,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare s ops.deal_space_search; d uuid:=(p_args->>'deal_id')::uuid; sid uuid:=(p_args->>'search_id')::uuid;
begin
 if d is null or not coalesce(ops.tour_deal_visible(p_tenant,d,p_actor),false) then raise exception 'space_search_unavailable'; end if;
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

create function ops.run_deal_space_search(p_tenant text,p_actor text,p_deal uuid,p_search uuid,p_version integer) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare s ops.deal_space_search; result jsonb;
begin
 if p_deal is null or not coalesce(ops.tour_deal_visible(p_tenant,p_deal,p_actor),false) then raise exception 'space_search_unavailable'; end if;
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

revoke all on function ops.read_deal_space_searches(text,text,uuid,uuid),ops.save_deal_space_search(text,text,jsonb),ops.run_deal_space_search(text,text,uuid,uuid,integer) from public;
grant execute on function ops.read_deal_space_searches(text,text,uuid,uuid),ops.save_deal_space_search(text,text,jsonb),ops.run_deal_space_search(text,text,uuid,uuid,integer) to carr_writer;
do $append_grant$
declare contract jsonb:=ops.scac_current_grant_contract(); entries jsonb;
begin
 select jsonb_agg(jsonb_build_object('grantee','carr_writer','signature',signature,'grantable',false,'privilege','execute',
 'ingress_key','db-function-acl:'||signature||':carr_writer:execute','ingress_kind','db_function_acl','function_kind','f',
 'security_definer',true,'volatility',volatility,'parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
 into entries from (values
 ('ops.read_deal_space_searches(p_tenant text, p_actor text, p_deal uuid, p_search uuid)','s'),
 ('ops.save_deal_space_search(p_tenant text, p_actor text, p_args jsonb)','v'),
 ('ops.run_deal_space_search(p_tenant text, p_actor text, p_deal uuid, p_search uuid, p_version integer)','v')
 ) f(signature,volatility);
 contract:=jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||entries);
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
