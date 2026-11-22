-- rollback: switch off engine intake. Retire engine intake and restore the original watch functions in a forward migration.
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

create function ops.subject_watch_reply_id(p_activity uuid) returns uuid
language plpgsql stable set search_path=pg_catalog,pg_temp as $$
begin
  if to_regclass('next.outside_id') is null then return p_activity; end if;
  return coalesce(
    (select email_activity_id from next.outside_id where source='old_system' and value=p_activity::text),
    (select e.activity_id from public.activity a join public.email mail on mail.id=a.email_id
      join next.email e on e.message_id=mail.message_id where a.id=p_activity),
    p_activity);
end $$;

create function ops.subject_watch_engine_reply_live(p_reply uuid,p_deal uuid,p_subscriber text) returns boolean
language plpgsql stable set search_path=pg_catalog,pg_temp as $$
begin
  if to_regclass('next.email') is null then return false; end if;
  return exists(
    select from next.email e join next.email_mailbox m using(activity_id)
    join next.app_user u on u.id=m.user_id
    join next.activity_about a on a.activity_id=e.activity_id and a.deal_id=p_deal
    where e.activity_id=p_reply and u.slug=p_subscriber
      and not exists(select from next.party_email address
        join next.app_user sender on sender.id=address.party_id where address.address=e.sender));
end $$;

create function ops.subject_watch_carr_reply_claimed(p_watch uuid,p_reply uuid,p_deal uuid,p_subscriber text)
returns boolean language sql stable set search_path=pg_catalog,pg_temp as $$
  select exists(
    select from ops.subject_watch_change c
    cross join lateral jsonb_array_elements(c.evidence_refs) ref
    where c.watch_generation_id=p_watch and c.semantic_key not like 'engine-reply:%'
      and ref->>'activity_id' is not null and ops.subject_watch_reply_id((ref->>'activity_id')::uuid)=p_reply
      and (c.disposition='emitted' or (c.disposition='pending' and c.failure_count<2 and exists(
        select from public.activity a where a.id=(ref->>'activity_id')::uuid and a.deal_id=p_deal
          and a.email_source->>'mailbox_owner'=p_subscriber
          and coalesce(a.email_context#>>'{lifecycle,state}','live')='live')))
  ) or exists(
    select from ops.notification n join public.actor a on a.id=n.recipient_actor
    where a.slug=p_subscriber and n.subject_type='deal' and n.subject_ref=p_deal::text
      and n.dedupe_key like 'subject-watch:'||p_watch||':reply:%'
      and ops.subject_watch_reply_id(case when pg_input_is_valid(split_part(n.dedupe_key,':',4),'uuid')
        then split_part(n.dedupe_key,':',4)::uuid end)=p_reply
  )
$$;

create function ops.claim_subject_watch_carr_reply(p_watch uuid,p_activity uuid) returns boolean
language plpgsql set search_path=pg_catalog,pg_temp as $$
declare reply_key text;
begin
  if not exists(select from ops.subject_watch_change where watch_generation_id=p_watch
      and semantic_key like 'engine-reply:%') then return true; end if;
  reply_key:='engine-reply:'||ops.subject_watch_reply_id(p_activity);
  if exists(select from ops.subject_watch_change where watch_generation_id=p_watch and semantic_key=reply_key
      and disposition='emitted') then return false; end if;
  delete from ops.subject_watch_change where watch_generation_id=p_watch and semantic_key=reply_key and disposition='pending';
  return true;
end $$;

revoke all on function ops.subject_watch_reply_id(uuid),ops.subject_watch_engine_reply_live(uuid,uuid,text),
  ops.subject_watch_carr_reply_claimed(uuid,uuid,uuid,text),ops.claim_subject_watch_carr_reply(uuid,uuid)
  from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

-- Both entry points use the same delivery loop, with engine receipts visible only to the enabled entry point.
do $delivery$
declare body text:=pg_get_functiondef('ops.sweep_subject_watches(timestamptz,integer)'::regprocedure);
  patch record;
  source_filter text:=$filter$(p_engine or split_part(c.semantic_key,':',1) not in ('engine','engine-reply'))$filter$;
begin
  for patch in select * from (values
    ($old$ops.sweep_subject_watches(p_now timestamp with time zone, p_limit integer DEFAULT 50)$old$,
      $new$ops.deliver_subject_watches(p_now timestamp with time zone, p_limit integer, p_engine boolean)$new$),
    ($old$where c.watch_generation_id=s.id and c.disposition='pending'$old$,
      'where '||source_filter||' and c.watch_generation_id=s.id and c.disposition=''pending'''),
    ($old$update ops.subject_watch_change set disposition='suppressed' where watch_generation_id=w.id and disposition='pending';$old$,
      'update ops.subject_watch_change c set disposition=''suppressed'' where '||source_filter||
      ' and watch_generation_id=w.id and disposition=''pending'';'),
    ($old$update ops.subject_watch_change c set disposition='suppressed'
       where c.watch_generation_id=w.id$old$, $new$if p_engine then
         delete from ops.subject_watch_change c where c.watch_generation_id=w.id and c.disposition='pending'
           and case when c.semantic_key like 'engine-reply:%' then
             not pol->'change_kinds' ? 'reply_arrived'
             or not ops.subject_watch_engine_reply_live(split_part(c.semantic_key,':',2)::uuid,w.subject_id,slug_value)
             or ops.subject_watch_carr_reply_claimed(w.id,split_part(c.semantic_key,':',2)::uuid,w.subject_id,slug_value)
           else false end;
       end if;
       update ops.subject_watch_change c set disposition='suppressed'
       where $new$||source_filter||' and c.watch_generation_id=w.id'),
    ($old$or c.category='reply_arrived' and (not pol$old$,
      $new$or c.category='reply_arrived' and c.semantic_key not like 'engine-reply:%' and (not pol$new$),
    ($old$where a.email_source->>'mailbox_owner'=slug_value$old$,
      $new$where a.email_source->>'mailbox_owner'=slug_value and (not p_engine or a.deal_id=w.subject_id)$new$),
    ($old$select id from ops.subject_watch_change where watch_generation_id=w.id and disposition='pending'$old$,
      'select id from ops.subject_watch_change c where '||source_filter||
      ' and watch_generation_id=w.id and disposition=''pending'''||$new$
             and case when c.semantic_key like 'engine-reply:%' then
               ops.subject_watch_engine_reply_live(split_part(c.semantic_key,':',2)::uuid,w.subject_id,slug_value)
             else true end$new$)
  ) p(before,after) loop
    if strpos(body,patch.before)=0 then raise exception 'watch_delivery_definition_changed'; end if;
    body:=replace(body,patch.before,patch.after);
  end loop;
  execute body;
end $delivery$;

revoke all on function ops.deliver_subject_watches(timestamptz,integer,boolean)
  from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace function ops.sweep_subject_watches(p_now timestamptz,p_limit integer default 50)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  return ops.deliver_subject_watches(p_now,p_limit,false);
end $$;

-- A CARR reply can take over an undelivered receipt. Both capture paths hold the watch lock.
do $carr_reply$
declare capture text:=pg_get_functiondef('ops.flush_subject_watch_changes()'::regprocedure);
  immediate text:=pg_get_functiondef('ops.consume_subject_watch_event(uuid)'::regprocedure);
  capture_match text:='has_reply:=true;';
  immediate_match text:=$old$order by sw.id limit 2 for update of sw
        loop
          perform ops.mint_notification$old$;
begin
  if strpos(capture,capture_match)=0 or strpos(immediate,immediate_match)=0 then
    raise exception 'watch_carr_reply_definition_changed';
  end if;
  execute replace(capture,capture_match,$new$if not ops.claim_subject_watch_carr_reply(w.id,reply.id) then continue; end if;
      has_reply:=true;$new$);
  execute replace(immediate,immediate_match,$new$order by sw.id limit 2
        loop
          perform pg_advisory_xact_lock(hashtextextended(s.actor_id::text||':deal:'||d.id::text,0));
          perform from ops.subject_watch where id=s.id for update;
          if not found or not ops.claim_subject_watch_carr_reply(s.id,reply.id) then continue; end if;
          perform ops.mint_notification$new$);
end $carr_reply$;

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
          select distinct 'engine-reply:'||reply_id::uuid,'reply_arrived',null::text,null::text,p_now,'[]'::jsonb,reply_id::uuid
          from jsonb_array_elements_text(item.reply_activity_ids) as replies(reply_id) where w.replies
            and ops.subject_watch_engine_reply_live(reply_id::uuid,w.deal_id,w.subscriber)
            and not ops.subject_watch_carr_reply_claimed(w.watch_id,reply_id::uuid,w.deal_id,w.subscriber)
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
    delivery:=ops.deliver_subject_watches(p_now,p_limit,true);
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

create or replace function ops.read_subject_watch_changes(p_kind text,p_id uuid,p_watch uuid,p_cursor uuid,p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.subject_watch_actor(); result jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'watch_limit_invalid'; end if;
  if ops.subject_watch_target(p_kind,p_id) is null then
    return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
  if not exists(select 1 from ops.subject_watch where id=p_watch and actor_id=a and subject_type=p_kind and subject_id=p_id)
    and not exists(select 1 from ops.subject_watch_v2_subscription where id=p_watch and actor_id=a and subject_type=p_kind and subject_id=p_id and state<>'removed') then
    return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
  select coalesce(jsonb_agg(to_jsonb(page) order by page.id),'[]') into result from (
    select id,watch_generation_id,subject_type,subject_id,source_event_id,source_revision,policy_version,
      category,changes,(select coalesce(jsonb_agg(ref),'[]') from jsonb_array_elements(evidence_refs) ref
      where case when semantic_key like 'engine-reply:%' then
        ops.subject_watch_engine_reply_live((ref->>'activity_id')::uuid,subject_id,(select slug from public.actor where id=a))
      else ref->>'activity_id' is null or exists(select 1 from public.activity mail join public.actor owner on owner.id=a
        where mail.id=(ref->>'activity_id')::uuid and mail.email_source->>'mailbox_owner'=owner.slug
          and coalesce(mail.email_context#>>'{lifecycle,state}','live')='live') end) evidence_refs,
      occurred_at,recorded_at,eligible_at,disposition,notification_id
    from ops.subject_watch_change where actor_id=a and watch_generation_id=p_watch
      and (category<>'reply_arrived' or case when semantic_key like 'engine-reply:%' then
        ops.subject_watch_engine_reply_live(split_part(semantic_key,':',2)::uuid,subject_id,(select slug from public.actor where id=a))
      else exists(select 1 from jsonb_array_elements(evidence_refs) ref join public.activity mail on mail.id=(ref->>'activity_id')::uuid join public.actor owner on owner.id=a
        where mail.email_source->>'mailbox_owner'=owner.slug
        and coalesce(mail.email_context#>>'{lifecycle,state}','live')='live') end)
      and (p_cursor is null or id>p_cursor) order by id limit p_limit+1
  ) page;
  return jsonb_build_object('ok',true,'schema_version','subject-watch-change.v1','changes',
    (select coalesce(jsonb_agg(value order by ordinality),'[]') from jsonb_array_elements(result) with ordinality where ordinality<=p_limit),
    'has_more',jsonb_array_length(result)>p_limit,'next_cursor',case when jsonb_array_length(result)>p_limit then result->(p_limit-1)->>'id' end);
end $$;
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
