-- V5-R03 bounded producer repair. This follows 0733/0734 (RW02) and 0735
-- (calendar prebrief) in release order. It makes a cross-brain action visible
-- in the existing recipient-scoped notification feed. It never invokes a
-- device provider. The migration reconciles at most 100 already-open actions;
-- a larger population requires a fresh, reviewed release plan.
--
-- This is not the entire V5-R03 slice: snooze, reassignment, due-date wakeup,
-- and a device delivery sender remain separate work. In-app pending means
-- persisted for the feed, not seen or acknowledged by the recipient.

-- The migration ledger must be contiguous. 0733/0734 are an atomic pair;
-- 0735 is the calendar predecessor from PR #1337. A failed preflight leaves
-- no objects or data behind and prevents a later out-of-order 0735 apply.
do $r03_order$
declare v_733 timestamptz; v_734 timestamptz; v_735 timestamptz;
begin
  select applied_at into v_733 from public.schema_migrations
   where filename='0733_salesforce_rw02_safe_stop_run_store.sql';
  select applied_at into v_734 from public.schema_migrations
   where filename='0734_salesforce_rw02_safe_stop_scac_successor.sql';
  select applied_at into v_735 from public.schema_migrations
   where filename='0735_calendar_prebrief_skip_unknown_attendees.sql';
  if v_733 is null or v_734 is null or v_735 is null or
     v_733 > v_734 or v_734 > v_735 then
    raise exception '0736 requires ordered 0733/0734/0735 migration ledger';
  end if;
end $r03_order$;

-- A single, conservative eligibility rule is shared by live inserts and the
-- bounded reconciliation. Current add-loop accepts exact joe/dell/claude
-- ownership. Legacy joint owners are never guessed into a human recipient.
-- Team loops include FYI handoffs and are outside this action-needed repair.
-- Future-dated rows are not interruptions.
create function ops.loop_notification_candidate(p_loop uuid)
returns table(recipient_actor uuid, recipient_slug text)
language sql stable security definer set search_path=pg_catalog,ops,public
as $$
  select r.id, r.slug
    from public.loop_item l
    join public.actor r on r.slug = l.owner and r.kind = 'human' and r.active
    left join ops.notification_preference p on p.actor = r.id
   where l.id = p_loop
     and l.status = 'open' and l.tier = 'shared'
     and l.kind = 'action_required'
     and l.owner in ('joe','dell')
     and l.created_by <> r.id
     and (l.personal_to is null or l.personal_to = r.id)
     and (l.marker <> 'dated' or
          (l.due_on is not null and
           l.due_on <= (now() at time zone coalesce(p.timezone,'UTC'))::date))
$$;
revoke all on function ops.loop_notification_candidate(uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

-- The source event must describe THIS loop. ops.mint_notification itself
-- checks only that an event exists; this wrapper proves the event/loop/owner
-- relationship before it calls the only notification writer.
create function ops.enqueue_loop_notification(p_event uuid)
returns boolean language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_event public.event%rowtype; v_loop public.loop_item%rowtype;
        v_target record; v_result jsonb; v_notification uuid;
        v_automation uuid; v_failure text;
begin
  select * into v_event from public.event where id = p_event;
  if not found or v_event.subject_type <> 'loop' or
     v_event.verb not in ('add-loop','reconcile-loop-notification') then
    return false;
  end if;
  select * into v_loop from public.loop_item where id = v_event.subject_id;
  if not found then return false; end if;
  select * into v_target from ops.loop_notification_candidate(v_loop.id);
  if not found then return false; end if;

  if v_event.verb = 'add-loop' then
    if v_event.actor_id <> v_loop.created_by or
       v_event.new_value->>'owner' is distinct from v_loop.owner or
       v_event.new_value->>'kind' is distinct from v_loop.kind then
      return false;
    end if;
  else
    -- This is a NEW reconciliation act, never a forged historical creation.
    if v_event.cause <> 'import_migration' or
       v_event.new_value->>'source' <> 'current_loop_item' or
       v_event.new_value->>'owner' is distinct from v_loop.owner or
       v_event.new_value->>'kind' is distinct from v_loop.kind or
       not exists (select 1 from public.actor a
                    where a.id = v_event.actor_id and a.slug = 'claude'
                      and a.kind = 'automation') then
      return false;
    end if;
  end if;

  select id into v_automation from public.actor
   where slug='claude' and kind='automation';
  if v_automation is null then return false; end if;

  begin
    v_result := ops.mint_notification(
      'event', v_event.id, 'loop', v_loop.id::text,
      'Action needed on an open loop', 'action_required', '/notifications',
      'loop-action:' || v_loop.id::text || ':' || v_target.recipient_slug,
      v_target.recipient_slug, false, false);
    v_notification := nullif(v_result->>'notification_id','')::uuid;
    if v_notification is null then
      raise exception using errcode='P0001',
        message='mint_without_notification';
    end if;
    -- A metadata-only event uses the existing admitted event surface. The
    -- new verb cannot trigger another enqueue. Keep receipt and mint atomic.
    insert into public.event
      (occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
    values (clock_timestamp(),v_automation,'loop-notification-attempt','loop',v_loop.id,
            jsonb_build_object('source_event_id',v_event.id,
                               'recipient_actor',v_target.recipient_actor,
                               'notification_id',v_notification,
                               'outcome',case when coalesce((v_result->>'deduplicated')::boolean,false)
                                              then 'deduplicated' else 'minted' end),
            'automation_job');
    return true;
  exception when others then
    -- The nested block rolls back a partial mint. A failed receipt write is
    -- also contained: never roll back the original add-loop event for this
    -- best-effort notification producer.
    v_failure := SQLSTATE;
    begin
      insert into public.event
        (occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
      values (clock_timestamp(),v_automation,'loop-notification-attempt','loop',v_loop.id,
              jsonb_build_object('source_event_id',v_event.id,
                                 'recipient_actor',v_target.recipient_actor,
                                 'outcome','failed','failure_code',v_failure),
              'automation_job');
    exception when others then null;
    end;
    return false;
  end;
end $$;
revoke all on function ops.enqueue_loop_notification(uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.loop_notification_on_event()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
begin
  if new.verb = 'add-loop' and new.subject_type = 'loop' then
    perform ops.enqueue_loop_notification(new.id);
  end if;
  return new;
end $$;
revoke all on function ops.loop_notification_on_event()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
create trigger loop_notification_on_event after insert on public.event
  for each row execute function ops.loop_notification_on_event();

-- Reconcile only the current, reviewed action-needed set. A pre-existing
-- matching add-loop event is reused. Imported rows without one receive an
-- honest event of THIS reconciliation, with current ownership; the event does
-- not claim to be their creation record. All paths use the same dedupe key.
do $r03_reconcile$
declare v_count integer; v_automation uuid; v_row record; v_event uuid;
begin
  select count(*) into v_count
    from public.loop_item l
    cross join lateral ops.loop_notification_candidate(l.id) c
   where not exists (select 1 from ops.notification n
                      where n.subject_type='loop' and n.subject_ref=l.id::text
                        and n.recipient_actor=c.recipient_actor);
  if v_count > 100 then
    raise exception '0736 reconciliation cap exceeded: % > 100',v_count;
  end if;
  if v_count = 0 then return; end if;
  select id into v_automation from public.actor
   where slug='claude' and kind='automation';
  if v_automation is null then
    raise exception '0736 reconciliation requires the automation actor';
  end if;
  -- Backlog is in-app first. Do not create a bulk set of device attempts if
  -- either recipient opted in after this source was reviewed.
  if exists (select 1 from ops.notification_preference p
              join public.actor a on a.id=p.actor
             where a.slug in ('joe','dell') and p.device_opt_in) then
    raise exception '0736 reconciliation needs review: recipient device opt-in changed';
  end if;
  for v_row in
    select l.id,l.owner,l.kind,l.created_by
      from public.loop_item l
      cross join lateral ops.loop_notification_candidate(l.id) c
     where not exists (select 1 from ops.notification n
                        where n.subject_type='loop' and n.subject_ref=l.id::text
                          and n.recipient_actor=c.recipient_actor)
     order by l.created_at,l.id
  loop
    select e.id into v_event from public.event e
     where e.subject_type='loop' and e.subject_id=v_row.id
       and e.verb='add-loop' and e.actor_id=v_row.created_by
       and e.new_value->>'owner'=v_row.owner
       and e.new_value->>'kind'=v_row.kind
     order by e.occurred_at,e.id limit 1;
    if v_event is null then
      insert into public.event
        (occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
      values (now(),v_automation,'reconcile-loop-notification','loop',v_row.id,
              jsonb_build_object('source','current_loop_item','owner',v_row.owner,
                                 'kind',v_row.kind),'import_migration')
      returning id into v_event;
    end if;
    perform ops.enqueue_loop_notification(v_event);
    v_event := null;
  end loop;
end $r03_reconcile$;
