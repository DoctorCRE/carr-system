-- WR-000113: the R03 notification store.
--
-- severity is a CLOSED THREE-VALUE ENUM WITH NO 'info', deliberately narrower
-- than public.signal_event.severity (db/schema.sql:47387, info|warning|critical).
-- That divergence IS acceptance criterion R03-NO-PROGRESS-SPAM: there is no
-- severity a progress ping could be minted under.
--
-- deep_link's regex forbids a scheme, a host and a query -- it is a RELATIVE
-- PATH ONLY, which is what makes the follow-time permission recheck
-- (dealroom-web.js:993-1006 resolves the session on every request) unavoidable
-- rather than optional.
--
-- NO TRANSACTION CONTROL. This file is the third member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0519/0520/0521/0522. The savepoint that bounds the
-- mint's blast radius lives in mcp-server/src/investigation.js -- JavaScript --
-- and deliberately not here.

do $wr113_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0520_doc_conversation_store.sql'
      and sha256 = 'cdf0120133e4f575a5e661d447ba3d2a58c93dfc64019edd2c90fd2bff11e54f') then
    raise exception '0521 requires the exact 0520 Doc conversation store';
  end if;
  if to_regprocedure('ops.doc_conversation_facts(uuid,text,integer,integer)') is null then
    raise exception '0521 requires the 0520 conversation read door';
  end if;
end $wr113_preflight$;

create table ops.notification (
  id uuid primary key default gen_random_uuid(),
  subject_type text not null check (btrim(subject_type) <> ''),
  subject_ref text not null check (btrim(subject_ref) <> ''),
  -- public.event.id or public.signal_event.id, selected by event_source.
  event_ref uuid not null,
  event_source text not null check (event_source in ('event','signal_event')),
  recipient_actor uuid not null references public.actor(id),
  reason text not null check (btrim(reason) <> '' and length(reason) <= 500),
  severity text not null check (severity in ('action_required','completion','failure')),
  -- A RELATIVE PATH ONLY: no scheme, no host, no query, no token. PostgreSQL's
  -- POSIX engine caps a bounded repetition at 255, so the bound is carried by
  -- length() beside the character class rather than inside it.
  -- A RELATIVE PATH ONLY: no scheme, no host (so no protocol-relative '//host'
  -- either), no query and no token. PostgreSQL's POSIX engine caps a bounded
  -- repetition at 255, so the length bound sits beside the class rather than
  -- inside it.
  deep_link text not null check (
    deep_link ~ '^/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$' and length(deep_link) <= 301),
  dedupe_key text not null check (btrim(dedupe_key) <> ''),
  created_at timestamptz not null default now(),
  correlation_id text,
  unique (recipient_actor, dedupe_key)
);

create table ops.notification_delivery (
  id uuid primary key default gen_random_uuid(),
  notification_id uuid not null references ops.notification(id),
  channel text not null check (channel in ('in_app','device')),
  state text not null check (state in ('pending','delivered','suppressed_quiet_hours','failed')),
  attempted_at timestamptz not null default now(),
  settled_at timestamptz,
  failure_reason text,
  check ((state = 'failed') = (failure_reason is not null)),
  check ((state = 'pending') = (settled_at is null)),
  unique (notification_id, channel)
);

create table ops.notification_read (
  notification_id uuid not null references ops.notification(id),
  recipient_actor uuid not null references public.actor(id),
  read_at timestamptz not null default now(),
  primary key (notification_id, recipient_actor)
);

create table ops.notification_preference (
  actor uuid primary key references public.actor(id),
  device_opt_in boolean not null default false,
  quiet_hours_start time,
  quiet_hours_end time,
  timezone text not null default 'UTC',
  version integer not null default 1 check (version >= 1),
  check ((quiet_hours_start is null) = (quiet_hours_end is null))
);

create or replace function ops.notification_rows_immutable()
returns trigger language plpgsql as $$ begin
  raise exception 'notification and notification read rows are immutable';
end $$;

create trigger notification_immutable before update or delete
on ops.notification for each row execute function ops.notification_rows_immutable();
create trigger notification_no_truncate before truncate
on ops.notification for each statement execute function ops.notification_rows_immutable();
create trigger notification_read_immutable before update or delete
on ops.notification_read for each row execute function ops.notification_rows_immutable();
create trigger notification_read_no_truncate before truncate
on ops.notification_read for each statement execute function ops.notification_rows_immutable();
-- notification_delivery needs UPDATE for its own state transitions, so it gets
-- a delete/truncate raiser only.
create trigger notification_delivery_no_delete before delete
on ops.notification_delivery for each row execute function ops.notification_rows_immutable();
create trigger notification_delivery_no_truncate before truncate
on ops.notification_delivery for each statement execute function ops.notification_rows_immutable();

-- SIEP-18 reference-monitor guards on all four relations.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.notification for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.notification for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.notification_delivery for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.notification_delivery for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.notification_read for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.notification_read for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.notification_preference for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.notification_preference for each statement execute function ops.scac_reference_monitor_guard();

-- -------------------------------------------------------------------------
-- ops.mint_notification -- NINE arguments.
--
-- The ninth is the SPONSORING PARTNER'S SLUG, computed server-side by
-- personalScopeForActor in the one handler that calls this (record-signal,
-- mcp-server/src/investigation.js) and unreachable from that verb's closed
-- inputSchema. It is NOT a recipient uuid: this function resolves it itself,
-- against ACTIVE HUMAN rows only, so a caller cannot name an arbitrary actor.
-- The resolution body is public.retrieval_visibility_actor_id(text) inlined
-- (db/schema.sql:35315-35322).
--
-- WHAT BOUNDS AN ADMITTED CALLER, stated so a reviewer can check it:
--   1. no dedupe key the caller chose -- it is derived at the call site from
--      the stored row's producer and signal_key;
--   2. no source row it has not just proved to exist;
--   3. no recipient uuid -- only a slug, resolved here to one of the two
--      partner rows, and the minted item confers no access because deep_link
--      is a relative path re-authorised at follow time.
-- pg_has_role adds exactly one fact -- that the session is a member of one of
-- the two bundles the grant already names -- so it catches a mis-granted or
-- re-granted function and nothing else.
-- -------------------------------------------------------------------------

create or replace function ops.mint_notification(
  p_event_source text, p_event_ref uuid, p_subject_type text, p_subject_ref text,
  p_reason text, p_severity text, p_deep_link text, p_dedupe_key text,
  p_recipient_slug text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_recipient uuid; v_id uuid; v_prior uuid; v_pref ops.notification_preference%rowtype;
        v_local time; v_quiet boolean; v_source_exists boolean;
begin
  -- The 0230:68 idiom: a definer function granted to a routine bundle carries
  -- its own membership check.
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'minting a notification requires the writer or authority capability';
  end if;

  select (case p_event_source
            when 'event' then exists (select 1 from public.event where id = p_event_ref)
            when 'signal_event' then exists (select 1 from public.signal_event where id = p_event_ref)
            else false end)
    into v_source_exists;
  if not v_source_exists then
    raise exception 'notification_requires_an_existing_event'
      using errcode = '23503',
      detail = format('no %s row named %s', p_event_source, p_event_ref);
  end if;

  -- The retrieval_visibility_actor_id(text) body, inlined. ACTIVE HUMAN ONLY.
  select id into v_recipient from public.actor
   where slug = p_recipient_slug and kind = 'human' and active = true;
  if v_recipient is null then
    -- ops.notification.recipient_actor is NOT NULL and there is no honest value
    -- here, so this function writes NOTHING and RAISES NOTHING: a raise would
    -- abort the caller's transaction (25P02) and roll back the signal insert
    -- that is the actual product.
    return jsonb_build_object('ok', true, 'minted', false,
      'reason_id', 'no_sponsoring_partner');
  end if;

  insert into ops.notification(subject_type, subject_ref, event_ref, event_source,
    recipient_actor, reason, severity, deep_link, dedupe_key, correlation_id)
  values (p_subject_type, p_subject_ref, p_event_ref, p_event_source,
    v_recipient, p_reason, p_severity, p_deep_link, p_dedupe_key,
    nullif(current_setting('carr.correlation_id', true), ''))
  on conflict (recipient_actor, dedupe_key) do nothing
  returning id into v_id;

  if v_id is null then
    select id into v_prior from ops.notification
     where recipient_actor = v_recipient and dedupe_key = p_dedupe_key;
    return jsonb_build_object('ok', true, 'minted', false, 'deduplicated', true,
      'notification_id', v_prior, 'recipient_actor', v_recipient);
  end if;

  insert into ops.notification_delivery(notification_id, channel, state)
  values (v_id, 'in_app', 'pending');

  select * into v_pref from ops.notification_preference where actor = v_recipient;
  if coalesce(v_pref.device_opt_in, false) then
    v_quiet := false;
    if v_pref.quiet_hours_start is not null then
      v_local := (now() at time zone coalesce(v_pref.timezone, 'UTC'))::time;
      v_quiet := case
        when v_pref.quiet_hours_start <= v_pref.quiet_hours_end
          then v_local >= v_pref.quiet_hours_start and v_local < v_pref.quiet_hours_end
        else v_local >= v_pref.quiet_hours_start or v_local < v_pref.quiet_hours_end
      end;
    end if;
    -- SUPPRESSED IS RECORDED, never absent and never dropped.
    insert into ops.notification_delivery(notification_id, channel, state, settled_at)
    values (v_id, 'device',
            case when v_quiet then 'suppressed_quiet_hours' else 'pending' end,
            case when v_quiet then now() else null end);
  end if;

  return jsonb_build_object('ok', true, 'minted', true, 'notification_id', v_id,
    'recipient_actor', v_recipient, 'severity', p_severity, 'dedupe_key', p_dedupe_key);
end $$;

comment on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text) is
  'WR-000113: the only notification writer. Verifies the named source row exists, resolves the sponsoring partner slug to an ACTIVE HUMAN row, and returns a no-op rather than raising when there is none.';

create or replace function ops.notification_feed_facts(p_after timestamptz, p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_limit integer; v_rows jsonb; v_unread integer;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'reading the notification feed requires the writer or authority capability';
  end if;
  v_actor := ops.portfolio_writer_actor_id();
  v_limit := least(greatest(coalesce(p_limit, 50), 1), 200);

  select coalesce(jsonb_agg(to_jsonb(f) order by f.created_at desc), '[]'::jsonb) into v_rows
    from (
      select n.id, n.severity, n.reason, n.subject_type, n.subject_ref, n.deep_link,
             n.created_at, r.read_at,
             coalesce((select jsonb_agg(jsonb_build_object('channel', d.channel, 'state', d.state)
                                        order by d.channel)
                         from ops.notification_delivery d where d.notification_id = n.id),
                      '[]'::jsonb) as delivery
        from ops.notification n
        left join ops.notification_read r
          on r.notification_id = n.id and r.recipient_actor = n.recipient_actor
       where n.recipient_actor = v_actor
         and (p_after is null or n.created_at > p_after)
       order by n.created_at desc limit v_limit) f;

  select count(*) into v_unread from ops.notification n
   where n.recipient_actor = v_actor
     and not exists (select 1 from ops.notification_read r
                      where r.notification_id = n.id and r.recipient_actor = v_actor);

  return jsonb_build_object('ok', true, 'unread_count', v_unread, 'notifications', v_rows);
end $$;

comment on function ops.notification_feed_facts(timestamptz,integer) is
  'WR-000113: resolves the caller''s own actor internally and returns only that actor''s notifications. There is no argument for a recipient.';

-- Inserts into ops.notification_read and NOTHING else. It never touches
-- ops.notification or any source task row -- R03-STATUS-DISTINCT, enforced by
-- the ABSENCE of a grant rather than by discipline.
create or replace function ops.acknowledge_notification(
  p_notification uuid, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_read_at timestamptz; v_owned boolean;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'acknowledging a notification requires the writer or authority capability';
  end if;
  v_actor := ops.portfolio_writer_actor_id();
  select true into v_owned from ops.notification
   where id = p_notification and recipient_actor = v_actor;
  if v_owned is null then
    return jsonb_build_object('ok', false, 'reason_id', 'notification_not_found');
  end if;

  insert into ops.notification_read(notification_id, recipient_actor)
  values (p_notification, v_actor)
  on conflict (notification_id, recipient_actor) do nothing
  returning read_at into v_read_at;

  if v_read_at is null then
    select read_at into v_read_at from ops.notification_read
     where notification_id = p_notification and recipient_actor = v_actor;
    return jsonb_build_object('ok', true, 'acknowledged', true, 'deduplicated', true,
      'read_at', v_read_at, 'idempotency_key', p_idempotency_key);
  end if;
  return jsonb_build_object('ok', true, 'acknowledged', true, 'deduplicated', false,
    'read_at', v_read_at, 'idempotency_key', p_idempotency_key);
end $$;

comment on function ops.acknowledge_notification(uuid,uuid) is
  'WR-000113: writes ops.notification_read and nothing else. The notification row and every source task row stay untouched.';

-- -------------------------------------------------------------------------
-- Grants. NO carr_reader grant on any of the four relations, deliberately:
-- ops.notification_feed_facts is the only door, exactly as
-- ops.doc_conversation_facts is for the sibling slice and
-- public.v_partner_room_turn is for the partner room. A count or a row a reader
-- could select around the feed function would defeat the per-recipient scoping
-- computed inside it.
--
-- The mint's EXECUTE reaches carr_writer because the ONE production call site
-- -- record-signal -- is a plain `write: true` verb (investigation.js:119),
-- which mcp.js:689 hands DATABASE_URL_WRITER and :726-729 runs as carr_writer.
-- An authority-only execute would raise permission denied there. NINE argument
-- types: a stale eight-type signature revokes and grants NOTHING and leaves the
-- real function carrying public's default execute.
-- -------------------------------------------------------------------------

grant select on ops.notification,ops.notification_delivery,ops.notification_read,
                ops.notification_preference to carr_writer,carr_authority;

revoke insert,update,delete,truncate on ops.notification,ops.notification_delivery,
  ops.notification_read,ops.notification_preference
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

revoke all on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text)
  to carr_writer, carr_authority;

revoke all on function ops.notification_feed_facts(timestamptz,integer)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.notification_feed_facts(timestamptz,integer)
  to carr_writer, carr_authority;

revoke all on function ops.acknowledge_notification(uuid,uuid)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.acknowledge_notification(uuid,uuid)
  to carr_writer, carr_authority;
