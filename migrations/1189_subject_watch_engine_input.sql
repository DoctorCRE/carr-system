-- rollback: switch off engine intake.
-- expand-contract: add engine delivery state and entry points without backfilling watches.
-- lock-review: the resume trigger locks the subscription table during installation.
set local lock_timeout='5s';
set local statement_timeout='30s';

create table ops.subject_watch_engine_delivery (
  watch_id uuid primary key,
  checked_at timestamptz,
  attempted_at timestamptz,
  missing_at timestamptz,
  last_emitted_at timestamptz,
  failure_count integer not null default 0,
  failure_code text
);
revoke all on ops.subject_watch_engine_delivery from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create table ops.subject_watch_engine_start (
  singleton boolean primary key default true check(singleton),
  started_at timestamptz not null
);
revoke all on ops.subject_watch_engine_start from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create table ops.subject_watch_engine_resume_floor (
  watch_id uuid primary key,
  resumed_at timestamptz not null
);
revoke all on ops.subject_watch_engine_resume_floor from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_engine_resume() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  insert into ops.subject_watch_engine_resume_floor(watch_id,resumed_at)
  select new.id,clock_timestamp() where new.subject_type='deal' and exists(select 1 from ops.subject_watch_engine_start)
  on conflict(watch_id) do update set resumed_at=excluded.resumed_at;
  return new;
end $$;
revoke all on function ops.subject_watch_engine_resume() from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create trigger subject_watch_engine_resume after update on ops.subject_watch_v2_subscription
for each row when (old.state='paused' and new.state='watching' and old.id=new.id)
execute function ops.subject_watch_engine_resume();

create function ops.subject_watch_engine_targets()
returns table(watch_id uuid,lifecycle_version integer,deal_id uuid,subscriber text,actor_id uuid,
  include_self boolean,since timestamptz,started_at timestamptz,checked_at timestamptz,
  attempted_at timestamptz,last_emitted_at timestamptz)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select w.id as watch_id,w.lifecycle_version,w.subject_id as deal_id,a.slug as subscriber,w.actor_id,w.include_self,
    greatest(floor.started_at+interval '1 microsecond',q.checked_at-interval '5 minutes'),
    floor.started_at,q.checked_at,q.attempted_at,q.last_emitted_at
  from (
    select id,actor_id,subject_type,subject_id,subscribed_at,1 as lifecycle_version,true as include_self
    from ops.subject_watch
    union all
    select id,actor_id,subject_type,subject_id,subscribed_at,lifecycle_version,
      coalesce((policy->>'include_self_changes')::boolean,true)
    from ops.subject_watch_v2_subscription where state='watching'
  ) w
  join public.actor a on a.id=w.actor_id and a.active and a.kind='human' and a.slug in ('joe','dell')
  left join ops.subject_watch_engine_delivery q on q.watch_id=w.id
  left join ops.subject_watch_engine_resume_floor r on r.watch_id=w.id
  cross join ops.subject_watch_engine_start s
  cross join lateral (select greatest(w.subscribed_at,s.started_at,r.resumed_at) as started_at) floor
  where w.subject_type='deal' and q.missing_at is null
$$;
revoke all on function ops.subject_watch_engine_targets() from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.read_subject_watch_engine_batch(
  p_now timestamptz,p_limit integer,p_attempted uuid[] default '{}'
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare batch jsonb;
begin
  if p_now is null or p_limit is null or p_limit not between 1 and 4
    or p_attempted is null or cardinality(p_attempted)>16 then raise exception 'watch_batch_limit'; end if;
  insert into ops.subject_watch_engine_start(started_at) values(p_now) on conflict do nothing;
  select coalesce(jsonb_agg(to_jsonb(w)-'attempted_at' order by w.attempted_at nulls first,w.watch_id),'[]') into batch
  from (
    select watch_id,lifecycle_version,deal_id,subscriber,since,attempted_at
    from ops.subject_watch_engine_targets()
    where started_at<=p_now and (checked_at is null or checked_at<p_now)
      and (last_emitted_at is null or last_emitted_at<=p_now-interval '5 minutes')
      and watch_id<>all(p_attempted) and ops.subject_watch_target('deal',deal_id) is not null
    order by attempted_at nulls first,watch_id limit p_limit
  ) w;
  return batch;
end $$;

create function ops.sweep_subject_watch_engine(p_now timestamptz,p_engine jsonb) returns integer
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  w record; item record; edit record; revision integer;
  source_id uuid; receipt_id uuid; receipt_ids uuid[]; minted_id uuid;
  failures integer:=0;
begin
  if p_now is null or jsonb_typeof(p_engine) is distinct from 'array' or jsonb_array_length(p_engine)>4 then
    raise exception 'watch_batch_limit';
  end if;
  for item in select * from jsonb_to_recordset(p_engine) as x(watch_id uuid,lifecycle_version integer,status text,changes jsonb)
  loop
    if not pg_try_advisory_xact_lock(hashtextextended('subject-watch-engine:'||item.watch_id,0)) then continue; end if;
    select * into w from ops.subject_watch_engine_targets() where watch_id=item.watch_id;
    if not found or w.lifecycle_version is distinct from item.lifecycle_version or w.started_at>p_now
      or w.checked_at>=p_now or w.last_emitted_at>p_now-interval '5 minutes' then continue; end if;
    revision:=(ops.subject_watch_target('deal',w.deal_id)->>'version')::integer;
    if revision is null then continue; end if;

    insert into ops.subject_watch_engine_delivery(watch_id,attempted_at) values(w.watch_id,p_now)
    on conflict(watch_id) do update set attempted_at=greatest(ops.subject_watch_engine_delivery.attempted_at,p_now);
    begin
      if item.status='retry' then
        update ops.subject_watch_engine_delivery set failure_count=failure_count+1,failure_code='engine_read_failed'
        where watch_id=w.watch_id;
        failures:=failures+1;
        continue;
      end if;
      if item.status='missing' then
        insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,cause)
        values(p_now,(select id from public.actor where slug='system' and active),
          'subject-watch-engine-target-missing','system',w.watch_id,'automation_job');
        update ops.subject_watch_engine_delivery set missing_at=p_now,failure_count=0,failure_code=null where watch_id=w.watch_id;
        continue;
      end if;
      if item.status is distinct from 'ok' or jsonb_typeof(item.changes) is distinct from 'array' then
        raise exception 'invalid_engine_watch_attempt';
      end if;

      receipt_ids:='{}';
      for edit in
        with fields(table_name,material,required_action) as (values
          ('deal','{owner_user_id,parking_reason}'::text[],null::text),
          ('deal_phase_change','{phase}','insert'), -- happened_at records when the phase moved.
          ('next_step','{status,owner_party_id,due_on,item_title,done_at}',null), -- created_at only dates the insert.
          ('critical_date','{status,kind,due_on}',null),
          ('proposal','{next_step_id,keeps_deal_date,decided_at}',null),
          ('lease_version','{version,executed_on,expiration_on}',null),
          ('lease_option','{kind,notice_on}',null)
        ), material as (
          select 'engine:'||change_id as source_key,actor,for_user,changed_at,
            ops.classify_subject_watch_change(jsonb_object_agg(field,old_value),jsonb_object_agg(field,new_value)) as delta
          from jsonb_to_recordset(item.changes) as x(change_id bigint,table_name text,action text,field text,
            old_value jsonb,new_value jsonb,actor text,for_user text,changed_at timestamptz)
          join fields using (table_name)
          where changed_at between w.since and p_now and actor<>'old_system' and field=any(fields.material)
            and (required_action is null or action=required_action)
            and (w.include_self or (actor is distinct from w.subscriber and for_user is distinct from w.subscriber))
          group by change_id,actor,for_user,changed_at
        )
        select * from material r where delta<>'[]' and not exists(
          select from ops.subject_watch_change where watch_generation_id=w.watch_id and semantic_key=r.source_key)
        order by changed_at,source_key limit 100
      loop
        insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,cause,sponsoring_human_slug)
        values(edit.changed_at,coalesce(
          (select id from public.actor where slug=edit.actor and kind='human'),
          (select id from public.actor where slug='system' and active)),
          'subject-watch-engine-change','system',w.watch_id,'automation_job',
          case when edit.for_user in ('joe','dell') then edit.for_user end)
        returning id into source_id;

        -- Only committed, emitted engine receipts enter the shared history.
        insert into ops.subject_watch_change(watch_generation_id,actor_id,subject_type,subject_id,semantic_key,source_event_id,
          source_revision,policy_version,category,changes,evidence_refs,occurred_at,eligible_at,disposition)
        values(w.watch_id,w.actor_id,'deal',w.deal_id,edit.source_key,source_id,revision,w.lifecycle_version,'material_changed',
          edit.delta,jsonb_build_array(jsonb_build_object('event_id',source_id,'activity_id',null)),edit.changed_at,p_now,'emitted')
        returning id into receipt_id;
        receipt_ids:=array_append(receipt_ids,receipt_id);
      end loop;

      if cardinality(receipt_ids)>0 then
        minted_id:=(ops.mint_notification('event',source_id,'deal',w.deal_id::text,
          'Watched deal has '||cardinality(receipt_ids)||' updates to review','action_required','/deals?deal='||w.deal_id,
          'subject-watch:engine:'||w.watch_id||':'||receipt_ids[1],w.subscriber,false,false)->>'notification_id')::uuid;
        if minted_id is null then raise exception 'watch_notification_not_minted'; end if;
        update ops.subject_watch_change set notification_id=minted_id where id=any(receipt_ids);
      end if;

      update ops.subject_watch_engine_delivery set failure_count=0,failure_code=null,
        last_emitted_at=case when cardinality(receipt_ids)>0 then p_now else last_emitted_at end,
        checked_at=case when cardinality(receipt_ids)<100 then p_now else checked_at end
      where watch_id=w.watch_id;
    exception when sqlstate 'SW001' then continue;
    when sqlstate '57014' or others then
      update ops.subject_watch_engine_delivery set failure_count=failure_count+1,failure_code=sqlstate where watch_id=w.watch_id;
      failures:=failures+1;
    end;
  end loop;
  return failures;
end $$;

revoke all on function ops.read_subject_watch_engine_batch(timestamptz,integer,uuid[]),
  ops.sweep_subject_watch_engine(timestamptz,jsonb) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_subject_watch_engine_batch(timestamptz,integer,uuid[]),
  ops.sweep_subject_watch_engine(timestamptz,jsonb) to carr_writer;

do $watch_notifications$
declare definition text; updated text;
begin
  definition:=pg_get_functiondef('ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean,boolean)'::regprocedure);
  updated:=replace(definition,$old$where id=p_event_ref and verb='subject-watch-scheduled-sweep'$old$,
    $new$where id=p_event_ref and verb=case when p_dedupe_key like 'subject-watch:engine:%'
      then 'subject-watch-engine-change' else 'subject-watch-scheduled-sweep' end$new$);
  if updated=definition then raise exception 'watch delivery guard missing from notification minter'; end if;
  definition:=updated;
  -- A pause that commits after the insert takes its snapshot can let one alert through.
  updated:=replace(definition,$old$values (p_subject_type, p_subject_ref, p_event_ref, p_event_source,
    v_recipient, p_reason, p_severity, p_deep_link, p_dedupe_key,
    nullif(current_setting('carr.correlation_id', true), ''))$old$,
    $new$select p_subject_type, p_subject_ref, p_event_ref, p_event_source,
    v_recipient, p_reason, p_severity, p_deep_link, p_dedupe_key,
    nullif(current_setting('carr.correlation_id', true), '')
  where case when p_dedupe_key like 'subject-watch:engine:%' then exists(
    select from ops.subject_watch_change c join ops.subject_watch_engine_targets() w
      on w.watch_id=c.watch_generation_id and w.lifecycle_version=c.policy_version
    where c.id=split_part(p_dedupe_key,':',4)::uuid) else true end$new$);
  if updated=definition then raise exception 'watch notification insert missing'; end if;
  definition:=updated;
  updated:=replace(definition,$old$where recipient_actor = v_recipient and dedupe_key = p_dedupe_key;$old$,
    $new$where recipient_actor = v_recipient and dedupe_key = p_dedupe_key;
    if v_prior is null and p_dedupe_key like 'subject-watch:engine:%' then raise sqlstate 'SW001'; end if;$new$);
  if updated=definition then raise exception 'watch notification deduplication missing'; end if;
  execute updated;

  definition:=pg_get_functiondef('ops.home_inbox_sources_v2(uuid)'::regprocedure);
  updated:=replace(definition,$old$), notifications as materialized (
    select n.* from ops.notification n$old$,$new$), visible_notifications as not materialized (
    select n.*, n.dedupe_key like 'subject-watch:engine:%' as engine_watch from ops.notification n$new$);
  if updated=definition then raise exception 'Home notification source missing'; end if;
  definition:=updated;
  -- Apply Home's existing limit before adding engine alerts.
  updated:=replace(definition,$old$      order by created_at desc,id desc limit 10001
  ), groups as ($old$,$new$  ), notifications as materialized (
    (select * from visible_notifications where not engine_watch order by created_at desc,id desc limit 10001)
    union all
    (select * from visible_notifications where engine_watch order by created_at desc,id desc limit 10001)
  ), groups as ($new$);
  if updated=definition then raise exception 'Home notification limit missing'; end if;
  definition:=updated;
  updated:=replace(definition,$old$jsonb_agg(to_jsonb(n) order by created_at desc,id desc)->0 latest,$old$,
    $new$jsonb_agg(to_jsonb(n) order by n.engine_watch,created_at desc,id desc)->0 latest,$new$);
  if updated=definition then raise exception 'Home notification representative missing'; end if;
  execute updated;
end $watch_notifications$;

do $contract$
declare contract jsonb:=ops.scac_current_grant_contract(); entries jsonb;
begin
  select jsonb_agg(jsonb_build_object(
    'grantee','carr_writer','signature',signature,'grantable',false,'privilege','execute',
    'ingress_key','db-function-acl:'||signature||':carr_writer:execute','ingress_kind','db_function_acl',
    'function_kind','f','security_definer',true,'volatility','v','parallel','u',
    'config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
  into entries from (values
    ('ops.read_subject_watch_engine_batch(p_now timestamp with time zone, p_limit integer, p_attempted uuid[])'),
    ('ops.sweep_subject_watch_engine(p_now timestamp with time zone, p_engine jsonb)')
  ) f(signature);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
