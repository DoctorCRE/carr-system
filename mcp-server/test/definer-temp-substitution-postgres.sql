-- Temporary-object substitution regression for a real pre-0764 definer.
--
-- ops.v5_a05_assurance_cadence_batch is granted to carr_reader and promises
-- that a reader-scoped caller cannot aim it at another partner (0617). Before
-- 0764 its path was `pg_catalog, ops, public` with no pg_temp entry, so
-- PostgreSQL searched the caller's temporary schema FIRST for its unqualified
-- `actor` and `signal_event` relations. A caller with only TEMP and EXECUTE
-- could then redirect "joe" to another partner's id and forge the joined
-- signal row, reading that partner's notification.
--
-- Part 1 (positive control, every run): an exact clone of the 0617 routine
-- -- body and path, checked byte-for-byte against the migration by
-- definer-hardening-regressions.py -- MUST still be exploitable here, which
-- proves the hostile objects below are a real attack and not a no-op.
-- Part 2 (the regression): the installed routine, called with the SAME hostile
-- objects, must return only joe's own (empty) batch. Against a pre-0764
-- database Part 2 fails; against 0764 + 0767 it passes.
--
-- Fixture role: a synthetic NOLOGIN role holding only schema USAGE on ops,
-- database TEMP (PostgreSQL grants TEMP to PUBLIC by default) and EXECUTE on
-- the two routines -- the routine's production EXECUTE grant is carr_reader's.
-- It holds no table privilege and no CREATE on any schema; the DO block below
-- asserts that. Every row, role and grant rolls back.
-- https://www.postgresql.org/docs/17/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY
\set ON_ERROR_STOP on
begin;
create role definer_temp_attacker nologin noinherit nosuperuser nocreatedb nocreaterole;
grant usage on schema ops to definer_temp_attacker;
do $$begin
  execute format('grant temporary on database %I to definer_temp_attacker',current_database());
end$$;
grant execute on function ops.v5_a05_assurance_cadence_batch(text) to definer_temp_attacker;

-- One synthetic notification addressed to the other partner. Its event row
-- deliberately does not exist, so no honest caller can ever see it.
select id as victim_actor from public.actor
 where slug='dell' and kind='human' and active \gset
alter table ops.notification disable trigger user;
insert into ops.notification(id,subject_type,subject_ref,event_ref,event_source,
  recipient_actor,reason,severity,deep_link,dedupe_key)
values ('41000000-0000-4000-8000-000000000001','definer-fixture','synthetic-1',
  '41000000-0000-4000-8000-000000000002','signal_event',:'victim_actor',
  'Synthetic partner-only notification','completion','/fixture','definer-temp-fixture');
alter table ops.notification enable trigger user;

create function pg_temp.legacy_v5_a05_assurance_cadence_batch(p_recipient_slug text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $legacy$
declare v_result jsonb; v_recipient uuid;
begin
  if p_recipient_slug is null or p_recipient_slug not in ('joe', 'dell') then
    raise exception using errcode = '42501',
      message = 'the V5-A05 morning batch is readable only for a partner recipient';
  end if;
  select id into v_recipient from actor
   where slug = p_recipient_slug and kind = 'human' and active;
  if v_recipient is null then
    raise exception using errcode = '42501',
      message = 'the V5-A05 morning batch is readable only for an active partner recipient';
  end if;

  select coalesce(jsonb_agg(row_to_json(batch) order by batch.created_at desc), '[]'::jsonb)
    into v_result
  from (
    select n.id as notification_id, n.reason, n.severity, n.subject_type, n.subject_ref,
           n.deep_link, n.created_at, s.signal_kind as reason_id
      from ops.notification n
      join signal_event s on s.id = n.event_ref and n.event_source = 'signal_event'
      left join ops.notification_read r
        on r.notification_id = n.id and r.recipient_actor = n.recipient_actor
     where n.recipient_actor = v_recipient
       and s.producer = 'v5-a05-delivery-cadence'
       and r.notification_id is null
       and not exists (select 1 from ops.notification_delivery h
                        where h.notification_id = n.id and h.state = 'held_until_morning'
                          and h.held_until > now())
     order by n.created_at desc
     limit 50
  ) batch;
  return v_result;
end;
$legacy$;
revoke all on function pg_temp.legacy_v5_a05_assurance_cadence_batch(text) from public;
grant execute on function pg_temp.legacy_v5_a05_assurance_cadence_batch(text) to definer_temp_attacker;

set session authorization definer_temp_attacker;
create temp table actor(id uuid,slug text,kind text,active boolean);
insert into actor values (:'victim_actor','joe','human',true);
create temp table signal_event(id uuid,producer text,signal_kind text);
insert into signal_event values
  ('41000000-0000-4000-8000-000000000002','v5-a05-delivery-cadence','attacker-forged');
do $$begin
  if session_user<>'definer_temp_attacker' or current_user<>session_user
     or has_table_privilege(current_user,'public.actor','SELECT')
     or has_table_privilege(current_user,'public.signal_event','SELECT')
     or has_table_privilege(current_user,'ops.notification','SELECT')
     or has_schema_privilege(current_user,'ops','CREATE')
     or has_schema_privilege(current_user,'public','CREATE') then
    raise exception 'attacker fixture has unexpected authority';
  end if;
end$$;

-- Part 1: the pre-0764 shape is exploitable.
do $$
declare leaked jsonb := pg_temp.legacy_v5_a05_assurance_cadence_batch('joe');
begin
  if leaked->0->>'notification_id' is distinct from '41000000-0000-4000-8000-000000000001'
     or leaked->0->>'reason_id' is distinct from 'attacker-forged' then
    raise exception 'positive control failed: the pre-0764 shape did not resolve attacker TEMP objects (got %)',leaked;
  end if;
end$$;

-- Part 2: the installed routine resists the same objects.
do $$
declare served jsonb := ops.v5_a05_assurance_cadence_batch('joe');
begin
  if served is distinct from '[]'::jsonb then
    raise exception 'ops.v5_a05_assurance_cadence_batch resolved attacker TEMP objects and served another partner''s notification: %',served;
  end if;
  if (select count(*) from pg_temp.actor)<>1 or (select count(*) from pg_temp.signal_event)<>1 then
    raise exception 'hostile temporary objects were not present for the hardened call';
  end if;
end$$;
reset session authorization;
rollback;
