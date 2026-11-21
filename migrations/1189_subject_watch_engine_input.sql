-- rollback: switch off engine intake, then retire the two entry points, resume trigger, target function and progress columns in a forward migration.
-- expand-contract: add nullable scheduler progress and dedicated entry points. The two-argument sweep stays in place.
-- lock-review: nullable columns need a short metadata lock on the small watch delivery table; no rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

alter table ops.subject_watch_delivery
  add column engine_checked_at timestamptz,
  add column engine_floor_at timestamptz,
  add column engine_attempted_at timestamptz,
  add column engine_missing_at timestamptz,
  add column engine_phase text,
  add column engine_phase_change_id bigint;

create function ops.subject_watch_engine_resume() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  update ops.subject_watch_delivery set engine_floor_at=clock_timestamp() where watch_id=new.id;
  return new;
end $$;
revoke all on function ops.subject_watch_engine_resume() from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create trigger subject_watch_engine_resume after update on ops.subject_watch_v2_subscription
for each row when (old.state='paused' and new.state='watching' and old.id=new.id)
execute function ops.subject_watch_engine_resume();

create function ops.subject_watch_engine_targets()
returns table(watch_id uuid,lifecycle_version integer,deal_id uuid,subscriber text,actor_id uuid,
  include_self boolean,since timestamptz,engine_checked_at timestamptz,engine_floor_at timestamptz,
  engine_attempted_at timestamptz,engine_phase text,engine_phase_change_id bigint)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select w.id as watch_id,w.lifecycle_version,w.subject_id as deal_id,a.slug as subscriber,w.actor_id,w.include_self,
    greatest(w.subscribed_at,q.engine_checked_at-interval '5 minutes') as since,q.engine_checked_at,q.engine_floor_at,
    q.engine_attempted_at,q.engine_phase,q.engine_phase_change_id
  from (
    select id,actor_id,subject_type,subject_id,subscribed_at,1 as lifecycle_version,true as include_self
    from ops.subject_watch
    union all
    select id,actor_id,subject_type,subject_id,subscribed_at,lifecycle_version,
      coalesce((policy->>'include_self_changes')::boolean,true)
    from ops.subject_watch_v2_subscription where state='watching'
  ) w
  join public.actor a on a.id=w.actor_id and a.active and a.kind='human' and a.slug in ('joe','dell')
  join ops.subject_watch_delivery q on q.watch_id=w.id and q.failure_count<2
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
  select coalesce(jsonb_agg(to_jsonb(w)-'engine_attempted_at' order by w.engine_attempted_at nulls first,w.watch_id),'[]') into batch
  from (
    select watch_id,lifecycle_version,deal_id,subscriber,since,engine_attempted_at
    from ops.subject_watch_engine_targets()
    where greatest(since,engine_floor_at)<=p_now and (engine_checked_at is null or engine_checked_at<p_now)
      and watch_id<>all(p_attempted) and ops.subject_watch_target('deal',deal_id) is not null
    order by engine_attempted_at nulls first,watch_id limit p_limit
  ) w;
  return batch;
end $$;

create function ops.sweep_subject_watches(p_now timestamptz,p_limit integer,p_engine jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  w record; item record; edit record; revision integer;
  source_key text; delta jsonb; source_id uuid;
  prior_phase text; phase_change_id bigint; failures integer:=0;
begin
  if jsonb_typeof(p_engine) is distinct from 'array' or jsonb_array_length(p_engine)>4 then
    raise exception 'watch_batch_limit';
  end if;
  if not pg_try_advisory_xact_lock(hashtextextended('subject-watch-sweep-cursor',0)) then
    return jsonb_build_object('scanned',0,'emitted',0);
  end if;
  for item in select * from jsonb_to_recordset(p_engine) as x(watch_id uuid,lifecycle_version integer,status text,changes jsonb)
  loop
    select * into w from ops.subject_watch_engine_targets() where watch_id=item.watch_id;
    if not found or not pg_try_advisory_xact_lock(hashtextextended(w.actor_id::text||':deal:'||w.deal_id::text,0)) then continue; end if;
    select * into w from ops.subject_watch_engine_targets() where watch_id=item.watch_id;
    if not found or w.lifecycle_version is distinct from item.lifecycle_version
      or greatest(w.since,w.engine_floor_at)>p_now or w.engine_checked_at>=p_now then continue; end if;
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
      if item.status is distinct from 'ok' or jsonb_typeof(item.changes) is distinct from 'array' then
        raise exception 'invalid_engine_watch_attempt';
      end if;

      prior_phase:=w.engine_phase;
      phase_change_id:=w.engine_phase_change_id;
      for edit in select * from jsonb_to_recordset(item.changes) as x(change_id bigint,table_name text,action text,field text,
        old_value jsonb,new_value jsonb,actor text,for_user text,changed_at timestamptz)
        where changed_at between w.since and p_now
          and (table_name<>'deal_phase_change' or (action='insert' and field='phase'))
        order by changed_at,change_id,field
      loop
        -- Inserts have no old phase. Advance the baseline even for muted or paused moves.
        if edit.table_name='deal_phase_change' then
          if edit.change_id<=phase_change_id then continue; end if;
          if jsonb_typeof(edit.new_value) is distinct from 'string' then raise exception 'invalid_engine_watch_phase'; end if;
          if phase_change_id is null then
            select btrim(phase) into prior_phase from public.deal where id=w.deal_id;
          end if;
          edit.old_value:=to_jsonb(prior_phase);
          prior_phase:=edit.new_value#>>'{}';
          phase_change_id:=edit.change_id;
        end if;
        if edit.changed_at<w.engine_floor_at or
          (not w.include_self and (edit.actor is not distinct from w.subscriber or edit.for_user is not distinct from w.subscriber))
          then continue; end if;
        source_key:='engine:'||edit.change_id||':'||edit.field;
        if exists(select 1 from ops.subject_watch_change where watch_generation_id=w.watch_id and semantic_key=source_key)
          then continue; end if;
        delta:=ops.classify_subject_watch_change(jsonb_build_object(edit.field,edit.old_value),jsonb_build_object(edit.field,edit.new_value));
        if delta='[]' then continue; end if;

        insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,cause,sponsoring_human_slug)
        values(edit.changed_at,coalesce(
          (select id from public.actor where slug=edit.actor and kind='human'),
          (select id from public.actor where slug='system' and active)),
          'subject-watch-engine-change','system',w.watch_id,'automation_job',
          case when edit.for_user in ('joe','dell') then edit.for_user end)
        returning id into source_id;

        insert into ops.subject_watch_change(watch_generation_id,actor_id,subject_type,subject_id,semantic_key,source_event_id,
          source_revision,policy_version,category,changes,evidence_refs,occurred_at,eligible_at)
        values(w.watch_id,w.actor_id,'deal',w.deal_id,source_key,source_id,revision,w.lifecycle_version,'material_changed',
          delta,jsonb_build_array(jsonb_build_object('event_id',source_id,'activity_id',null)),edit.changed_at,p_now)
        on conflict(watch_generation_id,semantic_key) do nothing;
      end loop;
      update ops.subject_watch_delivery set engine_checked_at=p_now,engine_phase=prior_phase,
        engine_phase_change_id=phase_change_id where watch_id=w.watch_id;
    exception when sqlstate '57014' or others then
      failures:=failures+1;
      raise warning 'engine watch intake failed for % (%)',w.watch_id,sqlstate;
    end;
  end loop;
  if failures>0 and failures=jsonb_array_length(p_engine) then
    return jsonb_build_object('scanned',0,'emitted',0,'engine_failures',failures);
  end if;
  return ops.sweep_subject_watches(p_now,p_limit)||jsonb_build_object('engine_failures',failures);
end $$;

revoke all on function ops.read_subject_watch_engine_batch(timestamptz,integer,uuid[]),
  ops.sweep_subject_watches(timestamptz,integer,jsonb) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_subject_watch_engine_batch(timestamptz,integer,uuid[]),
  ops.sweep_subject_watches(timestamptz,integer,jsonb) to carr_writer;

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
