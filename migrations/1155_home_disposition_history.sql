-- rollback: retire v6 in a forward migration; retain transition evidence.
-- expand-contract: expand; legacy metadata stays null and v1-v5 reads stay unchanged.
-- lock-review: nullable columns without rewrite; receipt normalization locks batch rows.
set local lock_timeout='5s';
set local statement_timeout='30s';

alter table ops.home_inbox_disposition add column settlement jsonb, add column snooze jsonb;
-- Old exact-undo snapshots must compare equal to rows with the new nullable columns.
update ops.home_inbox_batch b set receipts=(
  select jsonb_agg(e||jsonb_build_object(
    'before',case when jsonb_typeof(e->'before')='object' then e->'before'||'{"settlement":null,"snooze":null}'::jsonb else e->'before' end,
    'after',case when jsonb_typeof(e->'after')='object' then e->'after'||'{"settlement":null,"snooze":null}'::jsonb else e->'after' end
  ) order by ord) from jsonb_array_elements(b.receipts) with ordinality r(e,ord));
create table ops.home_inbox_transition (
  sequence bigint generated always as identity primary key,
  actor_id uuid not null references public.actor(id),
  item_id text not null,
  request_key uuid not null,
  action text not null check(action in ('settle','snooze','unsettle','unsnooze')),
  occurred_at timestamptz not null default now(),
  method text not null check(method in ('single','bulk')),
  batch_id uuid,
  unique(actor_id,request_key,item_id)
);
create index home_inbox_transition_actor_item on ops.home_inbox_transition(actor_id,item_id,sequence desc);
revoke all on ops.home_inbox_transition from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace function ops.write_home_inbox_disposition(p_actor uuid,p_item jsonb,p_material text,p_command text,p_until timestamptz)
returns jsonb language sql set search_path=pg_catalog,pg_temp as $$
  insert into ops.home_inbox_disposition(actor_id,item_id,disposition,snoozed_until,source_version,material_version,explicit_unsettle,settlement,snooze)
  values(p_actor,p_item->>'item_id',case p_command when 'snooze' then 'snoozed' when 'settle' then 'settled' else 'inbox' end,
    p_until,p_item->>'source_version',p_material,p_command='unsettle',
    case when p_command='settle' then current_setting('carr.home_transition',true)::jsonb end,
    case when p_command='snooze' then current_setting('carr.home_transition',true)::jsonb end)
  on conflict(actor_id,item_id) do update set disposition=excluded.disposition,snoozed_until=excluded.snoozed_until,
    source_version=excluded.source_version,material_version=excluded.material_version,
    explicit_unsettle=excluded.explicit_unsettle,version=ops.home_inbox_disposition.version+1,updated_at=now(),
    settlement=case when p_command='settle' and p_item->>'disposition'='settled'
      then ops.home_inbox_disposition.settlement else excluded.settlement end,
    snooze=case when p_command='snooze' and p_item->>'disposition'='snoozed'
      and (p_item->>'snoozed_until')::timestamptz=p_until then ops.home_inbox_disposition.snooze else excluded.snooze end
  returning to_jsonb(home_inbox_disposition.*);
$$;

alter function ops.change_home_inbox(uuid,text,jsonb,timestamptz) rename to change_home_inbox_without_history;
revoke all on function ops.change_home_inbox_without_history(uuid,text,jsonb,timestamptz) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create function ops.change_home_inbox(p_key uuid,p_command text,p_items jsonb,p_until timestamptz)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); result jsonb; metadata jsonb; count_items integer;
  v_action text:=split_part(p_command,'_',1); v_method text; v_batch uuid;
  previous_context text:=current_setting('carr.home_transition',true);
begin
  if a is null then raise insufficient_privilege; end if;
  perform pg_advisory_xact_lock(hashtextextended('home-inbox:'||a::text,0));
  if exists(select 1 from ops.home_inbox_write where actor_id=a and idempotency_key=p_key) then
    return ops.change_home_inbox_without_history(p_key,p_command,p_items,p_until);
  end if;
  if p_command like '%_undo' then
    select count(*) into count_items from ops.home_inbox_batch b,
      lateral jsonb_array_elements(b.receipts) e where b.actor_id=a and b.batch_key=(p_items->>'batch_key')::uuid
      and (p_items->'items' is null or p_items->'items'='null'::jsonb or exists(
        select 1 from jsonb_array_elements(p_items->'items') i where i->>'item_id'=e->>'item_id'));
    v_action:=case v_action when 'snooze' then 'unsnooze' else 'unsettle' end;
  elsif jsonb_typeof(p_items)='array' then count_items:=jsonb_array_length(p_items);
  end if;
  v_method:=case when count_items>1 then 'bulk' else 'single' end;
  v_batch:=case when count_items>1 then p_key end;
  metadata:=jsonb_build_object('at',to_char(now() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'actor_id',a,'method',v_method,'batch_id',v_batch);
  perform set_config('carr.home_transition',metadata::text,true);
  result:=ops.change_home_inbox_without_history(p_key,p_command,p_items,p_until);
  perform set_config('carr.home_transition',coalesce(previous_context,''),true);
  if result->>'ok'='true' then
    insert into ops.home_inbox_transition(actor_id,item_id,request_key,action,method,batch_id)
      select a,r->>'item_id',p_key,v_action,v_method,v_batch
      from jsonb_array_elements(result->'results') r
      where r->>'status'='applied' or r->>'ok'='true';
  end if;
  return result;
end $$;
revoke all on function ops.change_home_inbox(uuid,text,jsonb,timestamptz) from public,carr_reader;
grant execute on function ops.change_home_inbox(uuid,text,jsonb,timestamptz) to carr_writer;

create function ops.read_home_inbox_v6() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); snapshot jsonb;
begin
  if a is null then raise insufficient_privilege; end if;
  snapshot:=ops.read_home_inbox_v5();
  return snapshot||jsonb_build_object('schema_version','home-inbox.v6','items',(
    select coalesce(jsonb_agg(i||jsonb_build_object(
      'settlement',case when i->>'disposition'='settled' then d.settlement
        when i->>'disposition'='inbox' and i->>'source_state'='done' and not (i->>'explicit_unsettle')::boolean
          and i->>'source_type' in ('next_action','capture_post_call_action','email_proposal','fact_review','tour_feedback')
        then jsonb_build_object('at',i->'completed_at','actor_id',null,'method','automatic','batch_id',null) end,
      'snooze',case when i->>'disposition'='snoozed' then d.snooze end,
      'last_disposition_change',case when e.sequence is not null then jsonb_build_object(
        'at',to_char(e.occurred_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'actor_id',e.actor_id,'method',e.method,'batch_id',e.batch_id,'action',e.action,'request_key',e.request_key) end
      ) order by ord),'[]') from jsonb_array_elements(snapshot->'items') with ordinality entries(i,ord)
    left join lateral(select d.* from ops.home_inbox_disposition d where d.actor_id=a
      and (d.item_id=i->>'item_id' or d.v2_item_id=i->>'item_id')
      order by (d.item_id=i->>'item_id') desc,d.updated_at desc,d.item_id limit 1) d on true
    left join lateral(select * from ops.home_inbox_transition t where t.actor_id=a and t.item_id=i->>'item_id'
      order by t.sequence desc limit 1) e on true));
end $$;
revoke all on function ops.read_home_inbox_v6() from public,carr_reader,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_home_inbox_v6() to carr_writer;

create function ops.read_recently_settled_home_inbox(p_limit integer default 20) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare snapshot jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 then
    return jsonb_build_object('ok',false,'error','invalid_limit');
  end if;
  snapshot:=ops.read_home_inbox_v6();
  return snapshot||jsonb_build_object('schema_version','home-recent-settles.v1','items',(
    select coalesce(jsonb_agg(i order by (i->'settlement'->>'at')::timestamptz desc,i->>'item_id'),'[]')
    from (select i from jsonb_array_elements(snapshot->'items') i
      where i->'settlement'->>'at' is not null
      order by (i->'settlement'->>'at')::timestamptz desc,i->>'item_id' limit p_limit) limited));
end $$;
revoke all on function ops.read_recently_settled_home_inbox(integer) from public,carr_reader,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_recently_settled_home_inbox(integer) to carr_writer;

do $append_grant$
declare contract jsonb; signature text;
begin
  contract:=ops.scac_current_grant_contract();
  foreach signature in array array['ops.read_home_inbox_v6()','ops.read_recently_settled_home_inbox(p_limit integer)'] loop
    contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||jsonb_build_array(
      jsonb_build_object('grantee','carr_writer','signature',signature,'grantable',false,'privilege','execute',
        'ingress_key','db-function-acl:'||signature||':carr_writer:execute','ingress_kind','db_function_acl',
        'function_kind','f','security_definer',true,'volatility','s','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp'))));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
