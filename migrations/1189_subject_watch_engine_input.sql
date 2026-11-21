-- rollback: switch off engine intake. Restore legacy reply predicates and retire engine intake objects in a forward migration.
-- expand-contract: add nullable scheduler progress and dedicated entry points. The two-argument sweep stays in place.
-- lock-review: nullable columns need a short metadata lock on the small watch delivery table; no rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

alter table ops.subject_watch_delivery
  add column engine_checked_at timestamptz,
  add column engine_floor_at timestamptz,
  add column engine_attempted_at timestamptz,
  add column engine_missing_at timestamptz;

create table ops.subject_watch_engine_start (
  singleton boolean primary key default true check(singleton),
  started_at timestamptz not null
);
revoke all on ops.subject_watch_engine_start from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_engine_resume() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  update ops.subject_watch_delivery set engine_floor_at=clock_timestamp() where watch_id=new.id
    and exists(select 1 from ops.subject_watch_engine_start);
  return new;
end $$;
revoke all on function ops.subject_watch_engine_resume() from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create trigger subject_watch_engine_resume after update on ops.subject_watch_v2_subscription
for each row when (old.state='paused' and new.state='watching' and old.id=new.id)
execute function ops.subject_watch_engine_resume();

create function ops.subject_watch_engine_targets()
returns table(watch_id uuid,lifecycle_version integer,deal_id uuid,subscriber text,actor_id uuid,
  include_self boolean,replies boolean,since timestamptz,started_at timestamptz,engine_checked_at timestamptz,
  engine_attempted_at timestamptz)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select w.id as watch_id,w.lifecycle_version,w.subject_id as deal_id,a.slug as subscriber,w.actor_id,w.include_self,w.replies,
    greatest(floor.started_at+interval '1 microsecond',q.engine_checked_at-interval '5 minutes'),
    floor.started_at,q.engine_checked_at,q.engine_attempted_at
  from (
    select id,actor_id,subject_type,subject_id,subscribed_at,1 as lifecycle_version,true as include_self,true as replies
    from ops.subject_watch
    union all
    select id,actor_id,subject_type,subject_id,subscribed_at,lifecycle_version,
      coalesce((policy->>'include_self_changes')::boolean,true),policy->'change_kinds' ? 'reply_arrived'
    from ops.subject_watch_v2_subscription where state='watching'
  ) w
  join public.actor a on a.id=w.actor_id and a.active and a.kind='human' and a.slug in ('joe','dell')
  join ops.subject_watch_delivery q on q.watch_id=w.id and q.failure_count<2
  cross join ops.subject_watch_engine_start s
  cross join lateral (select greatest(w.subscribed_at,s.started_at,q.engine_floor_at) as started_at) floor
  where w.subject_type='deal' and q.engine_missing_at is null
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
  if not pg_try_advisory_xact_lock(hashtextextended('subject-watch-sweep-cursor',0)) then return null; end if;
  insert into ops.subject_watch_engine_start(started_at) values(p_now) on conflict do nothing;
  select coalesce(jsonb_agg(to_jsonb(w)-'engine_attempted_at' order by w.engine_attempted_at nulls first,w.watch_id),'[]') into batch
  from (
    select watch_id,lifecycle_version,deal_id,subscriber,since,engine_attempted_at
    from ops.subject_watch_engine_targets()
    where started_at<=p_now and (engine_checked_at is null or engine_checked_at<p_now)
      and watch_id<>all(p_attempted) and ops.subject_watch_target('deal',deal_id) is not null
    order by engine_attempted_at nulls first,watch_id limit p_limit
  ) w;
  return batch;
end $$;

create function ops.sweep_subject_watches(p_now timestamptz,p_limit integer,p_engine jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  w record; item record; edit record; revision integer;
  source_id uuid; delivery jsonb;
  failures integer:=0;
begin
  if jsonb_typeof(p_engine) is distinct from 'array' or jsonb_array_length(p_engine)>4 then
    raise exception 'watch_batch_limit';
  end if;
  if not pg_try_advisory_xact_lock(hashtextextended('subject-watch-sweep-cursor',0)) then
    return jsonb_build_object('scanned',0,'emitted',0);
  end if;
  for item in select * from jsonb_to_recordset(p_engine) as x(watch_id uuid,lifecycle_version integer,status text,changes jsonb,reply_activity_ids jsonb)
  loop
    select * into w from ops.subject_watch_engine_targets() where watch_id=item.watch_id;
    if not found or not pg_try_advisory_xact_lock(hashtextextended(w.actor_id::text||':deal:'||w.deal_id::text,0)) then continue; end if;
    select * into w from ops.subject_watch_engine_targets() where watch_id=item.watch_id;
    if not found or w.lifecycle_version is distinct from item.lifecycle_version
      or w.started_at>p_now or w.engine_checked_at>=p_now then continue; end if;
    revision:=(ops.subject_watch_target('deal',w.deal_id)->>'version')::integer;
    if revision is null then continue; end if;

    perform from ops.subject_watch_delivery where watch_id=w.watch_id for update skip locked;
    if not found then continue; end if;
    update ops.subject_watch_delivery set engine_attempted_at=greatest(engine_attempted_at,p_now) where watch_id=w.watch_id;
    if item.status='retry' then
      failures:=failures+1;
      continue;
    end if;

    begin
      if item.status='missing' then
        insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,cause)
        values(p_now,(select id from public.actor where slug='system' and active),
          'subject-watch-engine-target-missing','system',w.watch_id,'automation_job');
        update ops.subject_watch_delivery set engine_missing_at=p_now where watch_id=w.watch_id;
        continue;
      end if;
      if item.status is distinct from 'ok' or jsonb_typeof(item.changes) is distinct from 'array'
        or jsonb_typeof(item.reply_activity_ids) is distinct from 'array' then raise exception 'invalid_engine_watch_attempt'; end if;

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
          select 'engine:'||change_id as source_key,'material_changed' as category,actor,for_user,changed_at,
            ops.classify_subject_watch_change(jsonb_object_agg(field,old_value),jsonb_object_agg(field,new_value)) as delta,
            null::uuid as activity_id
          from jsonb_to_recordset(item.changes) as x(change_id bigint,table_name text,action text,field text,
            old_value jsonb,new_value jsonb,actor text,for_user text,changed_at timestamptz)
          join fields using (table_name)
          where changed_at between w.since and p_now and actor<>'old_system' and field=any(fields.material)
            and (required_action is null or action=required_action)
            and (w.include_self or (actor is distinct from w.subscriber and for_user is distinct from w.subscriber))
          group by change_id,actor,for_user,changed_at
        ), receipts as (
          select * from material where delta<>'[]'
          union all
          select distinct 'engine-reply:'||reply_id::uuid,'reply_arrived',null,null,p_now,'[]',reply_id::uuid
          from jsonb_array_elements_text(item.reply_activity_ids) as replies(reply_id) where w.replies
        )
        select * from receipts r where not exists(
          select from ops.subject_watch_change where watch_generation_id=w.watch_id and semantic_key=r.source_key)
        order by changed_at,source_key
      loop
        insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,cause,sponsoring_human_slug)
        values(edit.changed_at,coalesce(
          (select id from public.actor where slug=edit.actor and kind='human'),
          (select id from public.actor where slug='system' and active)),
          'subject-watch-engine-change','system',w.watch_id,'automation_job',
          case when edit.for_user in ('joe','dell') then edit.for_user end)
        returning id into source_id;

        insert into ops.subject_watch_change(watch_generation_id,actor_id,subject_type,subject_id,semantic_key,source_event_id,
          source_revision,policy_version,category,changes,evidence_refs,occurred_at,eligible_at)
        values(w.watch_id,w.actor_id,'deal',w.deal_id,edit.source_key,source_id,revision,w.lifecycle_version,edit.category,
          edit.delta,jsonb_build_array(jsonb_build_object('event_id',source_id,'activity_id',edit.activity_id)),edit.changed_at,p_now)
        on conflict(watch_generation_id,semantic_key) do nothing;
      end loop;
      update ops.subject_watch_delivery set engine_checked_at=p_now where watch_id=w.watch_id;
    exception when sqlstate '57014' or others then
      failures:=failures+1;
      raise warning 'engine watch intake failed for % (%)',w.watch_id,sqlstate;
    end;
  end loop;
  begin
    delivery:=ops.sweep_subject_watches(p_now,p_limit);
    -- A total engine outage leaves the sweep cursor alone when there was no legacy work.
    if failures>0 and failures=jsonb_array_length(p_engine) and delivery->>'scanned'='0' then
      raise sqlstate 'P1189';
    end if;
  exception when sqlstate 'P1189' then null;
  end;
  return delivery||jsonb_build_object('engine_failures',failures);
end $$;

revoke all on function ops.read_subject_watch_engine_batch(timestamptz,integer,uuid[]),
  ops.sweep_subject_watches(timestamptz,integer,jsonb) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_subject_watch_engine_batch(timestamptz,integer,uuid[]),
  ops.sweep_subject_watches(timestamptz,integer,jsonb) to carr_writer;

-- Engine reply evidence comes from the subscriber-scoped feed, without a legacy activity row.
do $engine_reply$
declare sweep text:=pg_get_functiondef('ops.sweep_subject_watches(timestamptz,integer)'::regprocedure);
  readback text:=pg_get_functiondef('ops.read_subject_watch_changes(text,uuid,uuid,uuid,integer)'::regprocedure);
  patched text;
  delivery_match text:=$old$join public.activity a on a.id=(refs.value->>'activity_id')::uuid
             join public.event e on e.id=(refs.value->>'event_id')::uuid
             where a.email_source->>'mailbox_owner'=slug_value
               and coalesce(a.email_context#>>'{lifecycle,state}','live')='live'$old$;
  read_match text:=$old$and (category<>'reply_arrived' or exists$old$;
  evidence_match text:=$old$where ref->>'activity_id' is null or exists$old$;
begin
  patched:=replace(sweep,delivery_match,$new$join public.event e on e.id=(refs.value->>'event_id')::uuid
             left join public.activity a on a.id=(refs.value->>'activity_id')::uuid
             where (c.semantic_key like 'engine-reply:%' or (a.email_source->>'mailbox_owner'=slug_value
               and coalesce(a.email_context#>>'{lifecycle,state}','live')='live'))$new$);
  if patched=sweep or strpos(readback,read_match)=0 or strpos(readback,evidence_match)=0 then
    raise exception 'watch_reply_predicate_changed';
  end if;
  execute patched;
  execute replace(replace(readback,read_match,$new$and (category<>'reply_arrived' or semantic_key like 'engine-reply:%' or exists$new$),
    evidence_match,$new$where ref->>'activity_id' is null or semantic_key like 'engine-reply:%' or exists$new$);
end $engine_reply$;

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
    ('ops.sweep_subject_watches(p_now timestamp with time zone, p_limit integer, p_engine jsonb)')
  ) f(signature);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
