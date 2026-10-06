-- WR-000116 -- the measured proof for the notification-preference PAIR.
-- Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW:
--   * that the READ returns the documented defaults for an actor with no row
--     and INSERTS NOTHING. The returned object is identical either way, so the
--     only honest evidence is a count(*) on ops.notification_preference taken
--     across the call. A JavaScript assertion on the answer cannot see this.
--   * that the compare-and-swap is the PREDICATED UPDATE. A stale base_version
--     must leave the stored version exactly where it was, which is a statement
--     about the row and not about the return value.
--   * that the FIRST SAVE against base_version 1 with no row lands at version
--     2. This is the only place where a version the caller was told (1) does
--     not name a row that exists, and it is the most likely defect in the pair.
--   * that a REPLAYED idempotency key returns the same result and moves
--     nothing -- proved through ops.notification_preference_write, which the
--     verb handler's own envelope would otherwise answer from its cache.
--   * that quiet_now is TRUE inside a MIDNIGHT WRAP-AROUND. A 22:00-07:00
--     window under a naive `start <= now < end` comparison is false at every
--     hour of the night and green in every daytime test. The hour is reached by
--     choosing the TIMEZONE in which the current instant is inside the window,
--     computed in this file's own SQL -- never by hard-coding a zone name that
--     passes for eight months and fails in one.
--   * that both functions reach carr_writer and carr_authority on the exact
--     argument types and reach neither carr_reader, nor carr_jobs, nor public.
--     A stale arity would name a different function and prove nothing.

\set ON_ERROR_STOP on

do $wr116_grants$
declare v_read text := 'ops.notification_preference_facts()';
        v_write text := 'ops.set_notification_preference(integer,boolean,time,time,text,boolean,uuid)';
        v_fn text;
begin
  foreach v_fn in array array[v_read, v_write] loop
    if not has_function_privilege('carr_writer', v_fn, 'execute')
       or not has_function_privilege('carr_authority', v_fn, 'execute')
       or has_function_privilege('carr_reader', v_fn, 'execute')
       or has_function_privilege('carr_jobs', v_fn, 'execute')
       or has_function_privilege('public', v_fn, 'execute') then
      raise exception 'WR-000116: % does not reach exactly the writer and authority bundles', v_fn;
    end if;
  end loop;

  -- The read is STABLE, so it can run inside the writer connection's
  -- `begin read only` transaction; the write is VOLATILE.
  if (select provolatile from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'notification_preference_facts') is distinct from 's' then
    raise exception 'WR-000116: the read door is not STABLE and would fail read-only in production';
  end if;
  if (select provolatile from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'set_notification_preference') is distinct from 'v' then
    raise exception 'WR-000116: the write door is not VOLATILE';
  end if;

  -- ZERO ARGUMENTS, not merely "no actor argument".
  if (select pronargs from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'notification_preference_facts') is distinct from 0 then
    raise exception 'WR-000116: the read door takes an argument; it must take none at all';
  end if;
  if (select pg_get_function_identity_arguments(p.oid) from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'set_notification_preference')
     not in ('p_base_version integer, p_device_opt_in boolean, p_quiet_hours_start time without time zone, p_quiet_hours_end time without time zone, p_timezone text, p_clear_quiet_hours boolean, p_idempotency_key uuid') then
    raise exception 'WR-000116: the write door signature moved: %',
      (select pg_get_function_identity_arguments(p.oid) from pg_proc p
         join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'ops' and p.proname = 'set_notification_preference');
  end if;

  -- 0527 adds NO table grant of any kind. Both relations stay unreachable
  -- directly, exactly as 0521:326-328 left the preference table.
  if exists (
    select 1 from unnest(array['ops.notification_preference',
      'ops.notification_preference_write']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)) then
    raise exception 'WR-000116: the preference store became directly writable by a runtime bundle';
  end if;
  if exists (
    select 1 from unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    where has_table_privilege(grantee, 'ops.notification_preference_write', 'select')) then
    raise exception 'WR-000116: the replay ledger took a select grant it was never given';
  end if;
end $wr116_grants$;

-- The acting-actor context is installed DIRECTLY here, exactly as the writer
-- connection installs it, so every case below proves the function rather than
-- the handler's discipline.
select set_config('carr.acting_actor_slug', 'joe', false);
select set_config('carr.verified_human_actor_slug', 'joe', false);

-- EVERY COMPARISON BELOW IS `is distinct from`, NEVER `<>`. A jsonb ->> that
-- finds no key returns NULL, `NULL <> 'x'` is NULL, and `if NULL then` is
-- FALSE -- so a refusal answering with no `version` key at all would slip past
-- a `<>` assertion silently. Mutation M3 proved exactly that against an earlier
-- draft of this file: the node half went red and this half stayed green.
do $wr116_read$
declare v_actor uuid; v_before bigint; v_after bigint; v_answer jsonb;
begin
  select id into v_actor from public.actor where slug = 'joe' and kind = 'human' and active;
  delete from ops.notification_preference where actor = v_actor;
  delete from ops.notification_preference_write where actor = v_actor;

  select count(*) into v_before from ops.notification_preference;
  v_answer := ops.notification_preference_facts();
  select count(*) into v_after from ops.notification_preference;

  -- DEFAULTS WITHOUT AN INSERT. The count is the whole assertion: the returned
  -- object would be identical if the read had written the row.
  if v_after is distinct from v_before then
    raise exception 'WR-000116: the read INSERTED a row -- % before, % after', v_before, v_after;
  end if;
  if v_answer->>'exists' is distinct from 'false'
     or v_answer->>'device_opt_in' is distinct from 'false'
     or v_answer->'quiet_hours_start' is distinct from 'null'::jsonb
     or v_answer->'quiet_hours_end' is distinct from 'null'::jsonb
     or v_answer->>'timezone' is distinct from 'UTC'
     or v_answer->>'version' is distinct from '1'
     or v_answer->>'quiet_now' is distinct from 'false' then
    raise exception 'WR-000116: the no-row answer is not the table''s declared defaults: %', v_answer;
  end if;
end $wr116_read$;

do $wr116_write$
declare v_actor uuid; r jsonb; v_key uuid := gen_random_uuid(); v_version integer;
begin
  select id into v_actor from public.actor where slug = 'joe' and kind = 'human' and active;

  -- THE FIRST SAVE. base_version 1 is what the read above documented, and a
  -- bare predicated update would answer version_conflict here.
  r := ops.set_notification_preference(1, true, null, null, 'UTC', null, gen_random_uuid());
  if r->>'ok' is distinct from 'true' or r->>'version' is distinct from '2' then
    raise exception 'WR-000116: the first save against base_version 1 did not land at version 2: %', r;
  end if;

  -- A STALE base_version. The refusal is a named reason and the row does not move.
  r := ops.set_notification_preference(1, false, null, null, null, null, gen_random_uuid());
  if r->>'reason_id' is distinct from 'version_conflict' or (r->>'current_version')::integer is distinct from 2 then
    raise exception 'WR-000116: a stale base_version was not refused as version_conflict: %', r;
  end if;
  select version into v_version from ops.notification_preference where actor = v_actor;
  if v_version is distinct from 2 then
    raise exception 'WR-000116: a refused compare-and-swap moved the version to %', v_version;
  end if;

  -- THE REPLAY. Same key, same payload: the same result, deduplicated, and the
  -- version does not move a second time.
  r := ops.set_notification_preference(2, false, null, null, null, null, v_key);
  if r->>'version' is distinct from '3' or r->>'deduplicated' is distinct from 'false' then
    raise exception 'WR-000116: the first application of a fresh key is wrong: %', r;
  end if;
  r := ops.set_notification_preference(2, false, null, null, null, null, v_key);
  if r->>'version' is distinct from '3' or r->>'deduplicated' is distinct from 'true' then
    raise exception 'WR-000116: a replayed idempotency key did not return the same result: %', r;
  end if;
  select version into v_version from ops.notification_preference where actor = v_actor;
  if v_version is distinct from 3 then
    raise exception 'WR-000116: a REPLAY moved the stored version to %', v_version;
  end if;
  if (select count(*) from ops.notification_preference_write where idempotency_key = v_key) is distinct from 1 then
    raise exception 'WR-000116: one idempotency key did not leave exactly one receipt';
  end if;

  -- BOTH-OR-NEITHER, refused BY NAME before any write rather than as a 23514
  -- check violation from the table's own constraint.
  r := ops.set_notification_preference(3, null, '22:00', null, null, null, gen_random_uuid());
  if r->>'reason_id' is distinct from 'notification_preference_quiet_hours_incomplete' then
    raise exception 'WR-000116: an incomplete quiet-hours pair was not refused by name: %', r;
  end if;
  select version into v_version from ops.notification_preference where actor = v_actor;
  if v_version is distinct from 3 then
    raise exception 'WR-000116: a refusal happened AFTER a write; version is now %', v_version;
  end if;

  -- AN UNKNOWN TIMEZONE, refused here rather than raising 22023 at READ time
  -- from the other door on every later call.
  r := ops.set_notification_preference(3, null, null, null, 'Nowhere/Nada', null, gen_random_uuid());
  if r->>'reason_id' is distinct from 'notification_preference_timezone_unknown' then
    raise exception 'WR-000116: an unknown timezone was not refused by name: %', r;
  end if;

  -- A CLEAR CARRYING TIMES is two intentions at once.
  r := ops.set_notification_preference(3, null, '22:00', null, null, true, gen_random_uuid());
  if r->>'reason_id' is distinct from 'notification_preference_quiet_hours_conflicting_request' then
    raise exception 'WR-000116: a clear request carrying a time was not refused by name: %', r;
  end if;
end $wr116_write$;

do $wr116_quiet$
declare v_actor uuid; v_night text; v_day text; r jsonb; v_version integer;
begin
  select id into v_actor from public.actor where slug = 'joe' and kind = 'human' and active;

  -- THE ZONE IS CHOSEN HERE, IN SQL, so the case is deterministic at any
  -- wall-clock hour the suite runs. A hard-coded name passes for eight months.
  select name into v_night from pg_timezone_names
   where (now() at time zone name)::time >= time '02:00'
     and (now() at time zone name)::time <  time '03:00'
     and name like '%/%' order by name limit 1;
  select name into v_day from pg_timezone_names
   where (now() at time zone name)::time >= time '12:00'
     and (now() at time zone name)::time <  time '13:00'
     and name like '%/%' order by name limit 1;
  if v_night is null or v_day is null then
    raise exception 'WR-000116: no timezone currently sits at 02:00 or at 12:00 local';
  end if;

  select version into v_version from ops.notification_preference where actor = v_actor;

  -- WRAP-AROUND. 22:00-07:00 evaluated at 02:00 local. Under a naive
  -- `start <= now < end` this is false at every hour of the night.
  r := ops.set_notification_preference(v_version, null, '22:00', '07:00', v_night, null,
                                       gen_random_uuid());
  if r->>'ok' is distinct from 'true' then
    raise exception 'WR-000116: the wrap-around window would not save: %', r;
  end if;
  if ops.notification_preference_facts()->>'quiet_now' is distinct from 'true' then
    raise exception 'WR-000116: 22:00-07:00 is not quiet at 02:00 local in %', v_night;
  end if;

  -- THE SAME STORED WINDOW, THE SAME INSTANT, THE OTHER SIDE OF THE BOUNDARY.
  -- This is the assertion a UTC-only implementation cannot pass.
  r := ops.set_notification_preference((r->>'version')::integer, null, null, null, v_day, null,
                                       gen_random_uuid());
  if ops.notification_preference_facts()->>'quiet_now' is distinct from 'false' then
    raise exception 'WR-000116: one instant and one window gave the same answer in % and %',
      v_night, v_day;
  end if;

  -- The daytime companion. It passes under BOTH implementations and proves
  -- nothing on its own; it is here so the wrap-around case is not alone.
  r := ops.set_notification_preference((r->>'version')::integer, null, '09:00', '17:00', null, null,
                                       gen_random_uuid());
  if ops.notification_preference_facts()->>'quiet_now' is distinct from 'true' then
    raise exception 'WR-000116: 09:00-17:00 is not quiet at 12:00 local in %', v_day;
  end if;

  -- CLEARED MARKS NONE, and both columns really are null.
  r := ops.set_notification_preference((r->>'version')::integer, null, null, null, null, true,
                                       gen_random_uuid());
  if r->'quiet_hours_start' is distinct from 'null'::jsonb or r->'quiet_hours_end' is distinct from 'null'::jsonb then
    raise exception 'WR-000116: clear_quiet_hours did not null BOTH columns: %', r;
  end if;
  if ops.notification_preference_facts()->>'quiet_now' is distinct from 'false' then
    raise exception 'WR-000116: cleared quiet hours still report the moment as quiet';
  end if;

  delete from ops.notification_preference_write where actor = v_actor;
  delete from ops.notification_preference where actor = v_actor;
end $wr116_quiet$;

\echo 'WR-000116 notification preferences: defaults without an insert, the compare-and-swap, the first save, the replay, the named refusals, and quiet hours across a midnight wrap-around'
