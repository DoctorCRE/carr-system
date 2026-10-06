-- WR-000116: the notification-preference pair.
--
-- 0521 shipped ops.notification_preference and NOTHING has ever read it or
-- written it. The quiet-hours columns have been carried since the notification
-- slice shipped and ops.mint_notification (0521:204-220) is their only reader.
-- This file adds the TWO SECURITY DEFINER functions that let a partner read and
-- set their own preferences, ONE relation that makes the write's replay clause
-- durable, and their grants, AND NOTHING ELSE.
--
-- NO INDEX. ops.notification_preference is keyed on actor (0521:76, primary
-- key), both functions look a row up by exactly that key, and the table holds
-- one row per partner -- two rows in production. An index on a two-row table
-- keyed by its own primary key is not an optimisation, it is an extra object in
-- a sealed catalog.
--
-- NO TRIGGER IS AMENDED OR DROPPED on any relation 0521 created. The four
-- notification relations keep the immutability and reference-monitor triggers
-- 0521:90-121 attached to them; ops.notification_preference already carries its
-- SIEP-18 pair at 0521:118-121 and this file does not touch it.
--
-- ONE NEW RELATION, flagged loudly rather than slipped in.
-- ops.notification_preference_write is a replay ledger, and AC-PREF-WRITE's
-- "returns the same result without a second row change" cannot be satisfied
-- without durable replay state somewhere. The preference row's own primary key
-- is `actor` -- one row per partner forever -- so there is no natural
-- idempotency key the way ops.doc_conversation's row id is one (0523:45-49),
-- and a version column cannot tell a replay from a second intentional save of
-- the same values. It takes NO GRANT OF ANY KIND: the definer writes it, the
-- runtime bundles cannot.
--
-- NO TRANSACTION CONTROL. This file is the first member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0527/0528 declared in tools/migrate.py.

do $wr116_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0521_r03_notifications.sql'
      and sha256 = '64db47424b53bd261641b15f0e9f9ec31bbbd5d0d28c00308ac1647f490033b7') then
    raise exception '0527 requires the exact 0521 R03 notification store';
  end if;
  if to_regprocedure('ops.notification_feed_facts(timestamptz,integer)') is null then
    raise exception '0527 requires the 0521 notification feed read door';
  end if;
  if to_regprocedure('ops.acknowledge_notification(uuid,uuid)') is null then
    raise exception '0527 requires the 0521 notification acknowledgement door';
  end if;
end $wr116_preflight$;

-- -------------------------------------------------------------------------
-- The replay ledger.
--
-- request_digest exists because a replay is only a replay when the SECOND call
-- asks for the same thing. A key replayed with a DIFFERENT payload is a caller
-- defect -- two intentions under one key -- and it is refused by name rather
-- than silently answered with the first call's result. That is the shape
-- 0520:128-149 uses for a relayed msg_id.
-- -------------------------------------------------------------------------

create table ops.notification_preference_write (
  idempotency_key uuid primary key,
  actor uuid not null references public.actor(id),
  applied_at timestamptz not null default now(),
  request_digest text not null check (request_digest ~ '^[0-9a-f]{64}$'),
  result jsonb not null
);

-- SIEP-18 reference-monitor guards, the 0521:118-121 shape.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.notification_preference_write for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.notification_preference_write for each statement execute function ops.scac_reference_monitor_guard();

-- -------------------------------------------------------------------------
-- 1. ops.notification_preference_facts -- ZERO ARGUMENTS.
--
-- Not "no actor argument": none at all. There is nothing this read is
-- parameterised by. It answers about one row, the acting actor's, and the
-- acting actor is derived inside the body by ops.portfolio_writer_actor_id()
-- exactly as 0521:239 derives it for the feed. AC-PREF-READ's "the actor is
-- derived server-side; no caller argument names an actor" is satisfied by
-- construction rather than by a handler's discipline.
--
-- STABLE, exactly like ops.notification_feed_facts at 0521:230, because the
-- verb declares writerConnection with NO write flag and therefore runs inside
-- the `begin read only` transaction. A volatile function that inserted would
-- not fail review; it would fail in production on the first call.
--
-- DEFAULTS WITHOUT AN INSERT. A missing row answers with the table's own
-- declared defaults -- device_opt_in false (0521:77), both quiet-hour times
-- null (0521:78-79), timezone 'UTC' (0521:80), version 1 (0521:81) -- and
-- writes nothing. `exists` is the honest field that lets the page tell "you
-- have never set this" from "you set it back to the defaults", and lets a test
-- assert the no-row branch ran rather than finding a row that happened to
-- match.
--
-- quiet_now is computed HERE and nowhere else, so there is exactly ONE
-- implementation of "is it quiet". The expression is lifted from
-- ops.mint_notification at 0521:207-213 rather than retyped, including its
-- `else` branch: a 22:00-07:00 window under a naive start <= now < end is false
-- at every hour of the night.
-- -------------------------------------------------------------------------

create or replace function ops.notification_preference_facts()
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_pref ops.notification_preference%rowtype;
        v_local time; v_quiet boolean;
begin
  -- The 0230:68 idiom: a definer function granted to a routine bundle carries
  -- its own membership check, so a mis-grant is caught rather than silently
  -- widening the door.
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'reading notification preferences requires the writer or authority capability';
  end if;
  v_actor := ops.portfolio_writer_actor_id();

  select * into v_pref from ops.notification_preference where actor = v_actor;

  v_quiet := false;
  if v_pref.quiet_hours_start is not null then
    v_local := (now() at time zone coalesce(v_pref.timezone, 'UTC'))::time;
    v_quiet := case
      when v_pref.quiet_hours_start <= v_pref.quiet_hours_end
        then v_local >= v_pref.quiet_hours_start and v_local < v_pref.quiet_hours_end
      else v_local >= v_pref.quiet_hours_start or v_local < v_pref.quiet_hours_end
    end;
  end if;

  return jsonb_build_object(
    'ok', true,
    'exists', v_pref.actor is not null,
    'device_opt_in', coalesce(v_pref.device_opt_in, false),
    'quiet_hours_start', v_pref.quiet_hours_start,
    'quiet_hours_end', v_pref.quiet_hours_end,
    'timezone', coalesce(v_pref.timezone, 'UTC'),
    'version', coalesce(v_pref.version, 1),
    'quiet_now', v_quiet);
end $$;

comment on function ops.notification_preference_facts() is
  'WR-000116: reads the acting actor''s own notification preferences and whether quiet hours cover this instant. ZERO arguments: the actor is resolved inside the body. A missing row returns the table''s declared defaults and inserts nothing.';

-- -------------------------------------------------------------------------
-- 2. ops.set_notification_preference -- SEVEN arguments, no actor among them.
--
-- volatile (the default): this one IS a write and the verb carries write: true,
-- so mcp.js opens an ordinary transaction.
--
-- p_clear_quiet_hours exists because null cannot mean two things. Every other
-- argument uses null for "leave alone" (the coalesce idiom 0523:269-274 uses).
-- Quiet hours are cleared by setting BOTH columns to null, which is
-- indistinguishable from "leave alone" if null is the only signal. Without an
-- explicit clear flag the table's both-or-neither CHECK (0521:82) can be
-- satisfied but never returned to its off state through this door.
--
-- ORDERED REFUSALS, EVERY ONE BEFORE ANY WRITE, each with a reason_id rather
-- than a constraint error:
--   1. a missing idempotency key;
--   2. a clear request that also carries times;
--   3. an incomplete quiet-hours pair, judged against the POST-MERGE pair --
--      what the row would BECOME -- and not against the arguments alone, so
--      setting only `end` when a `start` is already stored succeeds;
--   4. an unknown timezone. Without this, now() at time zone 'Nowhere' raises
--      22023 at READ time, from the OTHER door, on every later call: a write
--      that poisons a read.
--
-- THE FIRST SAVE HAS NO ROW TO SWAP AGAINST. The read documents version = 1 for
-- a missing row, so a partner's first save arrives with base_version = 1 and a
-- bare predicated update would match nothing and answer version_conflict, which
-- is wrong. The insert is therefore predicated on the SAME base version the
-- update carries (the 0523:255-260 pattern), runs FIRST, and lands at version 2
-- -- which the update's own predicate then no longer matches, so a successful
-- first save is reported from the freshly inserted row rather than as a
-- conflict. A base_version other than 1 against a missing row IS
-- version_conflict: the caller is holding a version that never existed.
-- -------------------------------------------------------------------------

create or replace function ops.set_notification_preference(
  p_base_version integer, p_device_opt_in boolean,
  p_quiet_hours_start time, p_quiet_hours_end time,
  p_timezone text, p_clear_quiet_hours boolean, p_idempotency_key uuid)
returns jsonb language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_pref ops.notification_preference%rowtype;
        v_prior ops.notification_preference_write%rowtype;
        v_digest text; v_clear boolean; v_start time; v_end time;
        v_version integer; v_result jsonb; v_row ops.notification_preference%rowtype;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'setting notification preferences requires the writer or authority capability';
  end if;
  v_actor := ops.portfolio_writer_actor_id();
  v_clear := coalesce(p_clear_quiet_hours, false);

  if p_idempotency_key is null then
    return jsonb_build_object('ok', false,
      'reason_id', 'notification_preference_idempotency_key_required');
  end if;

  -- THE REPLAY LOOKUP IS THE FIRST ACT AFTER THE KEY CHECK. A replayed key with
  -- the same payload returns the first call's result and touches nothing; a
  -- replayed key with a DIFFERENT payload is two intentions under one key and
  -- is refused by name.
  v_digest := encode(public.digest(convert_to(jsonb_build_object(
    'actor', v_actor, 'base_version', p_base_version, 'device_opt_in', p_device_opt_in,
    'quiet_hours_start', p_quiet_hours_start, 'quiet_hours_end', p_quiet_hours_end,
    'timezone', p_timezone, 'clear_quiet_hours', v_clear)::text, 'UTF8'), 'sha256'), 'hex');
  select * into v_prior from ops.notification_preference_write
   where idempotency_key = p_idempotency_key;
  if v_prior.idempotency_key is not null then
    if v_prior.request_digest <> v_digest then
      return jsonb_build_object('ok', false,
        'reason_id', 'notification_preference_idempotency_key_reused');
    end if;
    return v_prior.result || jsonb_build_object('deduplicated', true);
  end if;

  select * into v_pref from ops.notification_preference where actor = v_actor;

  if v_clear and (p_quiet_hours_start is not null or p_quiet_hours_end is not null) then
    return jsonb_build_object('ok', false,
      'reason_id', 'notification_preference_quiet_hours_conflicting_request');
  end if;

  -- The POST-MERGE pair: what the row would become.
  if v_clear then
    v_start := null; v_end := null;
  else
    v_start := coalesce(p_quiet_hours_start, v_pref.quiet_hours_start);
    v_end   := coalesce(p_quiet_hours_end,   v_pref.quiet_hours_end);
  end if;
  if (v_start is null) <> (v_end is null) then
    return jsonb_build_object('ok', false,
      'reason_id', 'notification_preference_quiet_hours_incomplete');
  end if;

  if p_timezone is not null
     and not exists (select 1 from pg_catalog.pg_timezone_names where name = p_timezone) then
    return jsonb_build_object('ok', false,
      'reason_id', 'notification_preference_timezone_unknown');
  end if;

  -- Insert-if-absent-under-base_version-1, else compare-and-swap.
  insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start,
                                          quiet_hours_end, timezone, version)
  select v_actor, coalesce(p_device_opt_in, false), v_start, v_end,
         coalesce(p_timezone, 'UTC'), 2
   where p_base_version = 1
     and not exists (select 1 from ops.notification_preference where actor = v_actor)
  on conflict (actor) do nothing;

  -- The compare-and-swap is the predicated update and NOT FOUND *is* the
  -- refusal. A first save that just landed sits at version 2 and p_base_version
  -- of 1 no longer matches it, so the update finds nothing -- which is why the
  -- row is re-read below and a freshly inserted row is reported as success
  -- rather than as a conflict.
  update ops.notification_preference
     set device_opt_in = coalesce(p_device_opt_in, device_opt_in),
         quiet_hours_start = case when v_clear then null
                                  else coalesce(p_quiet_hours_start, quiet_hours_start) end,
         quiet_hours_end   = case when v_clear then null
                                  else coalesce(p_quiet_hours_end, quiet_hours_end) end,
         timezone = coalesce(p_timezone, timezone),
         version  = version + 1
   where actor = v_actor and version = p_base_version
  returning version into v_version;

  if v_version is null then
    select * into v_row from ops.notification_preference where actor = v_actor;
    if v_pref.actor is null and v_row.actor is not null and p_base_version = 1 then
      v_version := v_row.version;
    else
      return jsonb_build_object('ok', false, 'reason_id', 'version_conflict',
        'current_version', (select version from ops.notification_preference
                             where actor = v_actor));
    end if;
  end if;

  select * into v_row from ops.notification_preference where actor = v_actor;
  v_result := jsonb_build_object(
    'ok', true,
    'exists', true,
    'device_opt_in', v_row.device_opt_in,
    'quiet_hours_start', v_row.quiet_hours_start,
    'quiet_hours_end', v_row.quiet_hours_end,
    'timezone', v_row.timezone,
    'version', v_version,
    'deduplicated', false);

  insert into ops.notification_preference_write(idempotency_key, actor, request_digest, result)
  values (p_idempotency_key, v_actor, v_digest, v_result);

  return v_result;
end $$;

comment on function ops.set_notification_preference(integer,boolean,time,time,text,boolean,uuid) is
  'WR-000116: the only door that sets a partner''s own notification preferences. No argument names an actor. Four ordered refusals return a reason_id before any write, the compare-and-swap is a predicated update whose NOT FOUND is the refusal, a first save against base_version 1 inserts under the same predicate, and one idempotency key applies exactly once.';

-- -------------------------------------------------------------------------
-- Grants. carr_writer AND carr_authority, exactly as 0521:337-338 grants the
-- feed and 0523:296-300 grants the write doors: the app calls both of these as
-- the signed-in partner, who arrives on the writer connection and holds no
-- sponsor-scoped authority binding. An authority-only door is one the product
-- cannot open however correct its SQL is.
--
-- The argument types are spelled in full in every revoke and every grant. A
-- stale arity revokes NOTHING and would leave PUBLIC execute on a security
-- definer.
--
-- NO TABLE GRANT OF ANY KIND on ops.notification_preference_write: the definer
-- writes it and the runtime bundles cannot. The revoke below is the 0521:326-328
-- shape and revokes privileges nobody held.
-- -------------------------------------------------------------------------

revoke insert,update,delete,truncate on ops.notification_preference_write
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

revoke all on function ops.notification_preference_facts()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.notification_preference_facts()
  to carr_writer,carr_authority;

revoke all on function ops.set_notification_preference(integer,boolean,time,time,text,boolean,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.set_notification_preference(integer,boolean,time,time,text,boolean,uuid)
  to carr_writer,carr_authority;
