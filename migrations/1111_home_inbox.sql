-- rollback: remove Home inbox verbs before dropping these additive disposition objects.
-- lock-review: new tables and functions only; canonical work and notification tables stay unchanged.
create table ops.home_inbox_disposition (
  actor_id uuid not null references public.actor(id),
  item_id text not null,
  disposition text not null check(disposition in ('inbox','snoozed','settled')),
  snoozed_until timestamptz,
  source_version text not null,
  material_version text not null,
  version integer not null default 1 check(version>0),
  explicit_unsettle boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key(actor_id,item_id),
  check((disposition='snoozed')=(snoozed_until is not null))
);
create table ops.home_inbox_write (
  actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  request jsonb not null,
  result jsonb not null,
  recorded_at timestamptz not null default now(),
  primary key(actor_id,idempotency_key)
);
revoke all on ops.home_inbox_disposition,ops.home_inbox_write from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.home_inbox_sources(p_actor uuid)
returns table(item_id text,source_type text,source_id text,source_version text,material_version text,
  source_state text,title text,activity_at timestamptz,hold_until date,unseen boolean)
language sql stable set search_path=pg_catalog,pg_temp as $$
  with tasks as materialized (
    select * from public.next_action where owner_id=p_actor order by id limit 1001
  ), actions as materialized (
    select * from public.capture_post_call_action where owner_id=p_actor order by id limit 1001
  ), sessions as materialized (
    select * from public.capture_session where actor_id=p_actor order by id limit 1001
  ), drafts as materialized (
    select p.* from public.email_proposal p
      join public.activity e on e.id=p.activity_id
      join public.actor a on a.id=p_actor and a.slug=p.owner
      where p.kind='reply_draft' and e.email_source->>'mailbox_owner'=a.slug
      order by p.id limit 1001
  ), notifications as materialized (
    select n.* from ops.notification n where recipient_actor=p_actor order by created_at desc,id limit 10001
  ), groups as (
    select subject_type,subject_ref,
      jsonb_agg(to_jsonb(n) order by created_at desc,id desc)->0 latest,
      md5(coalesce(string_agg(id::text,',' order by created_at,id) filter(where severity<>'completion'),'')) material,
      md5(string_agg(id::text,',' order by created_at,id)) revision,
      bool_or(r.read_at is null) unseen
    from notifications n left join ops.notification_read r on r.notification_id=n.id and r.recipient_actor=p_actor
    group by subject_type,subject_ref
  ), raw_sources(item_id,source_type,source_id,source_version,material_version,source_state,title,activity_at,hold_until,unseen) as (
  select 'next_action:'||id,'next_action',id::text,version::text,
    md5(jsonb_build_array(description,hold_until)::text),
    case when status in ('done','dropped') then 'done' else 'needs_attention' end,
    description,updated_at,hold_until,false from tasks
  union all
  select 'capture_post_call_action:'||id,'capture_post_call_action',id::text,
    md5(jsonb_build_array(status,description,updated_at,completed_at)::text),md5(description),
    case when status in ('done','dropped') then 'done' else 'needs_attention' end,
    description,updated_at,null,false from actions
  union all
  select 'capture_session:'||id,'capture_session',id::text,md5(jsonb_build_array(state,state_at)::text),
    md5(jsonb_build_array(started_at)::text),
    case when state='failed' then 'failed' when state='done' then 'done' else 'working' end,
    'Call processing',state_at,null,false from sessions
  union all
  select 'email_proposal:'||id,'email_proposal',id::text,version::text,md5(coalesce(draft_subject,'')),
    case when status='failed' then 'failed' when status in ('sent','dismissed') then 'done'
      when status='approved_to_send' then 'working' else 'needs_attention' end,
    coalesce(draft_subject,'Reply draft'),coalesce(result_recorded_at,submitted_at,created_at),null,false from drafts
  ), enriched as (
    select s.item_id,s.source_type,s.source_id,s.source_version||':'||coalesce(g.material,md5('')) source_version,
      s.material_version||':'||coalesce(g.material,md5('')) material_version,
      case when (g.latest->>'created_at')::timestamptz>=s.activity_at
        and g.latest->>'severity' in ('failure','action_required')
        then case when g.latest->>'severity'='failure' then 'failed' else 'needs_attention' end
        else s.source_state end source_state,
      s.title,greatest(s.activity_at,(g.latest->>'created_at')::timestamptz) activity_at,s.hold_until,
      coalesce(g.unseen,s.unseen) unseen
    from raw_sources s left join groups g on g.subject_type=s.source_type and g.subject_ref=s.source_id
  )
  select * from enriched
  union all
  select 'notification:'||jsonb_build_array(subject_type,subject_ref)::text,
    'notification',jsonb_build_array(subject_type,subject_ref)::text,revision,material,
    case latest->>'severity' when 'completion' then 'done' when 'failure' then 'failed' else 'needs_attention' end,
    latest->>'reason',(latest->>'created_at')::timestamptz,null,unseen from groups g
    where not exists(select 1 from raw_sources s where s.source_type=g.subject_type and s.source_id=g.subject_ref)
  union all
  select '!overflow','overflow','','','','failed','',null,null,false
    where (select count(*) from notifications)>10000
$$;
revoke all on function ops.home_inbox_sources(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.home_inbox_items(p_actor uuid)
returns setof jsonb language sql stable set search_path=pg_catalog,pg_temp as $$
  with observed as (
    select s.*,d.version disposition_version,d.explicit_unsettle,d.snoozed_until,
      coalesce(d.disposition,'inbox') stored_disposition,
      coalesce(d.material_version<>s.material_version or
        (s.source_state in ('needs_attention','failed') and d.source_version<>s.source_version),false) new_activity
    from ops.home_inbox_sources(p_actor) s
    left join ops.home_inbox_disposition d on d.actor_id=p_actor and d.item_id=s.item_id
  ), placed as (
    select *,case when stored_disposition='snoozed' and (snoozed_until<=now() or new_activity)
      then 'inbox' else stored_disposition end disposition from observed
  )
  select jsonb_build_object('item_id',item_id,'source_type',source_type,'source_id',source_id,
    'source_version',source_version,'source_state',source_state,'title',title,'activity_at',activity_at,
    'hold_until',hold_until,'unseen',unseen,'disposition',disposition,
    'snoozed_until',case when disposition='snoozed' then snoozed_until end,
    'explicit_unsettle',coalesce(explicit_unsettle,false) and not new_activity,
    'section',case when disposition<>'inbox' then disposition when source_state='working' then 'working' else 'inbox' end,
    'base_version',md5(jsonb_build_array(source_version,coalesce(disposition_version,0),disposition)::text))
    from placed order by item_id limit 1001
$$;
revoke all on function ops.home_inbox_items(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.read_home_inbox() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); items jsonb;
begin
  if a is null then raise insufficient_privilege; end if;
  select coalesce(jsonb_agg(i order by i->>'item_id'),'[]') into items from ops.home_inbox_items(a) i;
  if jsonb_array_length(items)>1000 or exists(select 1 from jsonb_array_elements(items) i where i->>'source_type'='overflow') then
    return jsonb_build_object('ok',false,'error','inbox_limit_exceeded','limit',1000);
  end if;
  return jsonb_build_object('ok',true,'schema_version','home-inbox.v1','items',items,
    'working_collapsed',true,'auto_settle_enabled',false);
end $$;
revoke all on function ops.read_home_inbox() from public,carr_reader;
grant execute on function ops.read_home_inbox() to carr_writer;

create function ops.change_home_inbox(p_key uuid,p_command text,p_items jsonb,p_until timestamptz)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); request jsonb; prior ops.home_inbox_write%rowtype;
  entry jsonb; current_item jsonb; result jsonb; results jsonb:='[]'; source_material text;
begin
  if a is null then raise insufficient_privilege; end if;
  if p_key is null or p_command not in ('snooze','settle','unsettle') or p_command is null
    or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) not between 1 and 100
    or (p_command='snooze' and p_until is null) or (p_command<>'snooze' and p_until is not null) then
    return jsonb_build_object('ok',false,'error','invalid_inbox_command');
  end if;
  request:=jsonb_build_array(p_command,p_items,p_until);
  perform pg_advisory_xact_lock(hashtextextended('home-inbox:'||a::text,0));
  select * into prior from ops.home_inbox_write where actor_id=a and idempotency_key=p_key;
  if found then
    if prior.request<>request then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  if p_command='snooze' and (p_until<=now() or not isfinite(p_until)) then
    return jsonb_build_object('ok',false,'error','invalid_snooze_time');
  end if;
  if exists(select 1 from jsonb_array_elements(p_items) i group by i->>'item_id' having count(*)>1) then
    return jsonb_build_object('ok',false,'error','duplicate_inbox_item');
  end if;
  for entry in select value from jsonb_array_elements(p_items) loop
    select i into current_item from ops.home_inbox_items(a) i where i->>'item_id'=entry->>'item_id';
    if current_item is null or current_item->>'source_type'='overflow' then
      result:=jsonb_build_object('ok',false,'error','inbox_item_not_found');
    elsif entry->>'base_version' is distinct from current_item->>'base_version' then
      result:=jsonb_build_object('ok',false,'error','version_conflict','current_version',current_item->>'base_version');
    else
      select material_version into source_material from ops.home_inbox_sources(a) s where s.item_id=entry->>'item_id';
      insert into ops.home_inbox_disposition(actor_id,item_id,disposition,snoozed_until,source_version,material_version,explicit_unsettle)
        values(a,entry->>'item_id',case p_command when 'snooze' then 'snoozed' when 'settle' then 'settled' else 'inbox' end,
          p_until,current_item->>'source_version',source_material,p_command='unsettle')
      on conflict(actor_id,item_id) do update set disposition=excluded.disposition,snoozed_until=excluded.snoozed_until,
        source_version=excluded.source_version,material_version=excluded.material_version,
        explicit_unsettle=excluded.explicit_unsettle,version=ops.home_inbox_disposition.version+1,updated_at=now();
      select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items(a) i where i->>'item_id'=entry->>'item_id';
    end if;
    results:=results||jsonb_build_array(result||jsonb_build_object('item_id',entry->>'item_id'));
  end loop;
  result:=jsonb_build_object('ok',true,'schema_version','home-inbox-command.v1','results',results);
  insert into ops.home_inbox_write(actor_id,idempotency_key,request,result) values(a,p_key,request,result);
  return result;
end $$;
revoke all on function ops.change_home_inbox(uuid,text,jsonb,timestamptz) from public,carr_reader;
grant execute on function ops.change_home_inbox(uuid,text,jsonb,timestamptz) to carr_writer;

do $contract$
declare contract jsonb:=ops.scac_current_grant_contract();
begin
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||(
    select jsonb_agg(jsonb_build_object('config',jsonb_build_array('search_path=pg_catalog, pg_temp'),
      'grantee','carr_writer','parallel','u','grantable',false,'privilege','execute','signature',signature,
      'volatility',volatility,'ingress_key','db-function-acl:'||signature||':carr_writer:execute',
      'ingress_kind','db_function_acl','function_kind','f','security_definer',true) order by signature)
    from (values ('ops.read_home_inbox()','s'),
      ('ops.change_home_inbox(p_key uuid, p_command text, p_items jsonb, p_until timestamp with time zone)','v')) g(signature,volatility)));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
