-- rollback: remove v5 and waiting commands before dropping waiting state and receipts.
-- expand-contract: expand; v1-v4 and canonical ownership remain unchanged.
-- lock-review: additive tables/functions and row completion triggers; no data rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

create table ops.home_waiting (
  actor_id uuid not null references public.actor(id),
  item_id text not null,
  identity_kind text check(identity_kind in ('party','actor')),
  identity_id uuid,
  reason text not null,
  evidence jsonb not null check(jsonb_typeof(evidence)='array'),
  version integer not null check(version>0),
  updated_at timestamptz not null default now(),
  primary key(actor_id,item_id),
  check((identity_kind is null)=(identity_id is null))
);
create table ops.home_waiting_write (
  actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  request jsonb not null,
  result jsonb not null,
  recorded_at timestamptz not null default now(),
  primary key(actor_id,idempotency_key)
);
revoke all on ops.home_waiting,ops.home_waiting_write from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.home_waiting_actor(p_tenant text) returns uuid
language plpgsql stable set search_path=pg_catalog,pg_temp as $$
begin
  if p_tenant is distinct from 'carr-internal' or
    coalesce(nullif(current_setting('carr.organization_tenant_id',true),''),'carr-internal')<>'carr-internal' then
    raise exception using errcode='42501',message='home_waiting_tenant_mismatch';
  end if;
  return ops.portfolio_writer_actor_id();
end $$;
revoke all on function ops.home_waiting_actor(text) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.home_waiting_identity(p_kind text,p_id uuid) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  with recursive chain as (
    select id,name,merged_into,deleted_at,array[id] visited from public.party
      where p_kind='party' and id=p_id
    union all
    select p.id,p.name,p.merged_into,p.deleted_at,c.visited||p.id
      from chain c join public.party p on p.id=c.merged_into
      where c.deleted_at is null and not p.id=any(c.visited) and cardinality(c.visited)<32
  ), resolved as (
    select id,name from chain where merged_into is null and deleted_at is null
    union all
    select id,display_name from public.actor where p_kind='actor' and id=p_id and active
  )
  select coalesce((select jsonb_build_object('kind',p_kind,'id',id,'display_name',name,'available',true)
    from resolved limit 1),jsonb_build_object('kind',p_kind,'id',p_id,'display_name',null,'available',false))
$$;
revoke all on function ops.home_waiting_identity(text,uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.read_home_inbox_v5() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.home_waiting_actor('carr-internal'); snapshot jsonb;
begin
  snapshot:=ops.read_home_inbox_v4();
  return snapshot||jsonb_build_object('schema_version','home-inbox.v5','items',(
    select coalesce(jsonb_agg(i||jsonb_build_object('waiting_version',coalesce(w.version,0),
      'waiting_on',case when w.identity_id is not null and i->>'source_state'<>'done' then
        jsonb_build_object('identity',ops.home_waiting_identity(w.identity_kind,w.identity_id),
          'reason',w.reason,'evidence',w.evidence,'version',w.version) else null end) order by ord),'[]')
    from jsonb_array_elements(snapshot->'items') with ordinality e(i,ord)
    left join ops.home_waiting w on w.actor_id=a and w.item_id=i->>'item_id'));
end $$;
revoke all on function ops.read_home_inbox_v5() from public,carr_reader,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_home_inbox_v5() to carr_writer;

create function ops.change_home_waiting(p_tenant text,p_key uuid,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.home_waiting_actor(p_tenant); item jsonb; prior ops.home_waiting_write%rowtype;
  v integer; kind text:=p_args#>>'{target,kind}'; target uuid; result jsonb; completed boolean:=false;
begin
  if a is null then raise insufficient_privilege; end if;
  if p_key is null or p_args->>'command' is null or p_args->>'command' not in ('set','clear')
    or coalesce(length(btrim(p_args->>'reason')),0) not between 1 and 2000
    or jsonb_typeof(p_args->'evidence') is distinct from 'array'
    or jsonb_array_length(p_args->'evidence') not between 1 and 20
    or exists(select 1 from jsonb_array_elements(p_args->'evidence') e where jsonb_typeof(e)<>'string' or length(btrim(e#>>'{}')) not between 1 and 2000)
    or coalesce(p_args->>'base_version','') !~ '^[0-9]{1,10}$'
    or coalesce(p_args->>'item_version','') !~ '^[a-f0-9]{32}$'
    or coalesce(length(p_args->>'item_id'),0) not between 1 and 1000 then
    return jsonb_build_object('ok',false,'error','invalid_waiting_command');
  end if;
  if (p_args->>'base_version')::bigint>power(2,31)-2 then
    return jsonb_build_object('ok',false,'error','invalid_waiting_command');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('home-waiting-key:'||p_key,0));
  select * into prior from ops.home_waiting_write where actor_id=a and idempotency_key=p_key;
  if found then
    if prior.request<>p_args then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  -- A source row lock orders the set against completion and ownership changes.
  if p_args->>'item_id' like 'next_action:%' then
    select status in ('done','dropped') into completed from public.next_action where id::text=substr(p_args->>'item_id',13) for share;
  elsif p_args->>'item_id' like 'capture_post_call_action:%' then
    select status in ('done','dropped') into completed from public.capture_post_call_action where id::text=substr(p_args->>'item_id',26) for share;
  elsif p_args->>'item_id' like 'email_proposal:%' then
    select status in ('sent','dismissed') into completed from public.email_proposal where id::text=substr(p_args->>'item_id',16) for share;
  elsif p_args->>'item_id' like 'capture_session:%' then
    select state='done' into completed from public.capture_session where id::text=substr(p_args->>'item_id',17) for share;
  end if;
  perform pg_advisory_xact_lock(hashtextextended('home-waiting:'||(p_args->>'item_id'),0));
  select i into item from ops.home_inbox_items_v3(a) i where i->>'item_id'=p_args->>'item_id';
  if item is null then return jsonb_build_object('ok',false,'error','inbox_item_not_found'); end if;
  select version into v from ops.home_waiting where actor_id=a and item_id=p_args->>'item_id' for update;
  v:=coalesce(v,0);
  if v<>(p_args->>'base_version')::integer or item->>'base_version'<>p_args->>'item_version' then
    return jsonb_build_object('ok',false,'error','version_conflict','current_version',v);
  end if;
  if p_args->>'command'='set' then
    if completed or item->>'source_state'='done' then return jsonb_build_object('ok',false,'error','inbox_item_completed'); end if;
    if kind is null or kind not in ('actor','party') or coalesce(p_args#>>'{target,id}','') !~* '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' then
      return jsonb_build_object('ok',false,'error','invalid_waiting_identity');
    end if;
    target:=(p_args#>>'{target,id}')::uuid;
    if kind='party' then
      perform 1 from public.party where id=target and deleted_at is null and merged_into is null for share;
    else
      perform 1 from public.actor where id=target and active for share;
    end if;
    if not found then return jsonb_build_object('ok',false,'error','invalid_waiting_identity'); end if;
  end if;
  insert into ops.home_waiting(actor_id,item_id,identity_kind,identity_id,reason,evidence,version)
    values(a,p_args->>'item_id',case when p_args->>'command'='set' then kind end,target,p_args->>'reason',p_args->'evidence',v+1)
    on conflict(actor_id,item_id) do update set identity_kind=excluded.identity_kind,identity_id=excluded.identity_id,
      reason=excluded.reason,evidence=excluded.evidence,version=excluded.version,updated_at=now();
  result:=jsonb_build_object('ok',true,'schema_version','home-waiting.v1','item_id',p_args->>'item_id',
    'waiting_version',v+1,'waiting_on',case when target is not null then
      jsonb_build_object('identity',ops.home_waiting_identity(kind,target),'reason',p_args->>'reason','evidence',p_args->'evidence','version',v+1) else null end);
  insert into ops.home_waiting_write(actor_id,idempotency_key,request,result) values(a,p_key,p_args,result);
  return result;
end $$;
revoke all on function ops.change_home_waiting(text,uuid,jsonb) from public,carr_reader,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.change_home_waiting(text,uuid,jsonb) to carr_writer;

create function ops.clear_completed_home_waiting() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare row_data jsonb:=to_jsonb(new); key text; done boolean:=false; changed record;
begin
  case tg_table_name
    when 'next_action','capture_post_call_action' then
      key:=tg_table_name||':'||(row_data->>'id'); done:=row_data->>'status' in ('done','dropped');
    when 'capture_session' then
      key:='capture_session:'||(row_data->>'id'); done:=row_data->>'state'='done';
    when 'email_proposal' then
      key:='email_proposal:'||(row_data->>'id'); done:=row_data->>'status' in ('sent','dismissed');
    when 'fact_review_decision','canonical_fact' then
      key:='fact_review:'||jsonb_build_array(row_data->>'entity_ref',row_data->>'fact_key')::text;
      done:=row_data->>'organization_tenant_id'='carr-internal' and not coalesce((ops.fact_review_field('carr-internal',row_data->>'entity_ref',row_data->>'fact_key')->>'pending')::boolean,true);
    when 'tour_share_feedback_event' then
      key:='tour_feedback:'||(row_data->>'share_grant_id'); done:=row_data->>'organization_tenant_id'='carr-internal';
    when 'notification' then
      key:='notification:'||jsonb_build_array(row_data->>'subject_type',row_data->>'subject_ref')::text;
      done:=row_data->>'severity'='completion';
  end case;
  if done then
    perform pg_advisory_xact_lock(hashtextextended('home-waiting:'||key,0));
    if tg_table_name='notification' then
      -- Read after the lock so concurrent notifications use Home's current visibility and ordering.
      if not exists(select 1 from ops.home_inbox_sources_v2((row_data->>'recipient_actor')::uuid) s
        where s.item_id=key and s.source_state='done') then
        return new;
      end if;
    end if;
    for changed in update ops.home_waiting set identity_kind=null,identity_id=null,version=version+1,updated_at=now()
      where item_id=key and identity_id is not null
        and (tg_table_name<>'notification' or actor_id::text=row_data->>'recipient_actor')
      returning actor_id,item_id,version
    loop
      insert into ops.home_waiting_write(actor_id,idempotency_key,request,result)
        values(changed.actor_id,gen_random_uuid(),jsonb_build_object('command','complete','item_id',key),
          jsonb_build_object('ok',true,'schema_version','home-waiting.v1','item_id',key,'waiting_version',changed.version,'waiting_on',null));
    end loop;
  end if;
  return new;
end $$;
revoke all on function ops.clear_completed_home_waiting() from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create trigger home_waiting_complete after update on public.next_action for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after update on public.capture_post_call_action for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after update on public.capture_session for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after update on public.email_proposal for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after insert on ops.canonical_fact for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after insert on ops.fact_review_decision for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after insert on ops.tour_share_feedback_event for each row execute function ops.clear_completed_home_waiting();
create trigger home_waiting_complete after insert on ops.notification for each row execute function ops.clear_completed_home_waiting();

do $append_grant$
declare contract jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||
    '[{"grantee":"carr_writer","signature":"ops.read_home_inbox_v5()","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.read_home_inbox_v5():carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"s","parallel":"u","config":["search_path=pg_catalog, pg_temp"]},{"grantee":"carr_writer","signature":"ops.change_home_waiting(p_tenant text, p_key uuid, p_args jsonb)","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.change_home_waiting(p_tenant text, p_key uuid, p_args jsonb):carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"v","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
