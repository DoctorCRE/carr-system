-- 0617_delivery_cadence_a05.sql
-- DoctorCRE v5 slice V5-A05: delivery cadence, escalation and the
-- decision-ready quiet-hours queue -- the production store and doors behind
-- mcp-server/src/delivery-cadence-a05.v5.js's pure classifiers.
--
-- WHAT THIS MIGRATION ADDS, and nothing else:
--
--   1. ops.v5_a05_cadence_receipt -- append-only evidence that a subject's
--      assurance cadence checked in, expiring 14 days after issuance
--      (V5_A05_CADENCE_INTERVAL_DAYS in the JS module). This is the
--      "expiring cadence receipt" (checkable_done concrete_output).
--
--   2. ops.v5_a05_cadence_status(subject_type, subject_ref) -- STABLE,
--      read-only, takes NO caller-supplied `now`: it reads clock_timestamp()
--      itself, because a caller-supplied instant is exactly the "clock reset"
--      excluded_scope names, and nothing server-side may accept one. It
--      mirrors evaluateCadenceReceipt's math over the receipt history for one
--      subject. The JS module remains the tested, canonical specification of
--      that math (27 tests). This SQL function is verified against the local
--      disposable-Postgres migration lane (`./run.sh local-db-ci --class
--      migration`), which applies this migration and exercises the record/
--      status door round trip; a dedicated SQL<->JS parity test asserting the
--      two agree byte-for-byte on shared fixture vectors is NOT included in
--      this PR and is a disclosed gap -- see the PR body.
--
--   3. ops.notification_quiet_now(actor) -- extracted, not duplicated, from
--      ops.mint_notification's own inline quiet-hours boolean (0521:216-223).
--      ops.mint_notification is then redefined (DROP + CREATE, because adding
--      a parameter changes the argument-type signature and CREATE OR REPLACE
--      cannot widen it) to call the extracted helper and take two new
--      trailing parameters, p_bypass_quiet_hours and p_hold_for_morning, both
--      boolean default false. Every existing 9-argument call site --
--      investigation.js's record-signal is the only one -- resolves to this
--      same function with both defaulted false, UNCHANGED BEHAVIOUR, because
--      Postgres permits a call that omits trailing defaulted arguments.
--      record-signal's own verb schema is not touched by this migration.
--
--   4. The morning hold (PR #1236 review round 2, item 2): a
--      held_until_morning delivery state with a server-computed held_until
--      on ops.notification_delivery, ops.notification_morning_release_at, and
--      the feed and morning-brief batch reads that hide a held item until its
--      hold ends. See the section comment below.
--
-- Sealed by 0618 as SCAC v75 over main's v74 (0614); the pair is one atomic
-- migration group in tools/migrate.py.
--
-- WHAT THIS IS NOT: a second quiet-hours computation. ops.notification_preference
-- (0521/0527) remains the only quiet-hours preference store and
-- set-notification-preference remains the only door that writes it. This
-- migration extends the one existing reader of that store rather than
-- building a second one, per the standing instruction "extend these; don't
-- build parallel ones".

do $v5a05_preflight$
begin
  if to_regprocedure('ops.mint_notification(text,uuid,text,text,text,text,text,text,text)') is null then
    raise exception '0617 requires the 0521 ops.mint_notification (nine-argument) door';
  end if;
  if to_regprocedure('ops.notification_feed_facts(timestamptz,integer)') is null then
    raise exception '0617 requires the 0521 notification feed read door';
  end if;
  if to_regprocedure('ops.scac_reference_monitor_guard()') is null then
    raise exception '0617 requires the SIEP-18 reference-monitor guard function';
  end if;
end $v5a05_preflight$;

create table ops.v5_a05_cadence_receipt (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  subject_type text not null check (btrim(subject_type) <> ''),
  subject_ref text not null check (btrim(subject_ref) <> ''),
  issued_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null,
  evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(evidence) = 'object'),
  -- Set when this receipt follows a PRIOR receipt for the same subject whose
  -- expires_at had already passed at issuance -- the "replan on miss" fact,
  -- recorded on the receipt that resolves the miss rather than inferred later.
  replan_of uuid references ops.v5_a05_cadence_receipt(id),
  created_by uuid not null references actor(id),
  idempotency_key uuid not null,
  check (expires_at = issued_at + interval '14 days'),
  unique (organization_tenant_id, idempotency_key)
);

comment on table ops.v5_a05_cadence_receipt is
  'V5-A05: append-only 14-day cadence receipts. One row per assurance check-in for a subject; expires_at is always issued_at+14 days, matching V5_A05_CADENCE_INTERVAL_DAYS in mcp-server/src/delivery-cadence-a05.v5.js.';

create index v5_a05_cadence_receipt_subject_idx
  on ops.v5_a05_cadence_receipt (organization_tenant_id, subject_type, subject_ref, issued_at desc);

create or replace function ops.v5_a05_stamp_tenant()
returns trigger language plpgsql set search_path = pg_catalog, ops
as $$
declare tenant text := ops.completion_runtime_tenant();
begin
  if new.organization_tenant_id is not null and new.organization_tenant_id is distinct from tenant then
    raise exception 'v5-a05 cadence receipt tenant is server-derived';
  end if;
  new.organization_tenant_id := tenant;
  return new;
end;
$$;

create trigger v5_a05_cadence_receipt_stamp_tenant before insert
on ops.v5_a05_cadence_receipt for each row execute function ops.v5_a05_stamp_tenant();

create trigger v5_a05_cadence_receipt_immutable before update or delete
on ops.v5_a05_cadence_receipt for each row execute function ops.completion_reject_mutation();
create trigger v5_a05_cadence_receipt_no_truncate before truncate
on ops.v5_a05_cadence_receipt for each statement execute function ops.completion_reject_mutation();

create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.v5_a05_cadence_receipt for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.v5_a05_cadence_receipt for each statement execute function ops.scac_reference_monitor_guard();

-- ---------------------------------------------------------------------------
-- ops.v5_a05_cadence_status -- the production mirror of evaluateCadenceReceipt.
-- STABLE, no caller-supplied `now`, no write. This is what the read-only
-- `cadence-status` verb and the read-only live acceptance check both call.
-- ---------------------------------------------------------------------------

create or replace function ops.v5_a05_cadence_status(p_subject_type text, p_subject_ref text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_tenant text := ops.completion_runtime_tenant();
  v_now timestamptz := clock_timestamp();
  v_last ops.v5_a05_cadence_receipt%rowtype;
  v_receipts_in_window integer;
  v_miss_count integer;
  -- REVIEW FINDING 3 (Opus adversarial review of PR #1236, round 1): a
  -- subject that has NEVER received a receipt returned no_receipt_on_record
  -- forever, unconditionally -- the sweep's own comment already says that
  -- status is never escalated, so a subject nobody ever sends a receipt for
  -- can never fire the sweep, which is precisely the failure the sweep
  -- exists to catch. v_activation_anchor is a genuine server-side fact (this
  -- migration's own applied_at in public.schema_migrations -- never a
  -- caller-supplied instant, which would reopen the clock-reset hole), used
  -- ONLY to start the interval for a subject with zero receipt history. Once
  -- one real receipt exists, v_last.expires_at takes over completely; the
  -- anchor is never consulted again for that subject.
  v_activation_anchor timestamptz;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'reading v5-a05 cadence status requires the writer or authority capability';
  end if;

  select applied_at into v_activation_anchor from public.schema_migrations
   where filename = '0617_delivery_cadence_a05.sql';

  select * into v_last from ops.v5_a05_cadence_receipt
   where organization_tenant_id = v_tenant and subject_type = p_subject_type and subject_ref = p_subject_ref
   order by issued_at desc limit 1;

  select count(*) into v_receipts_in_window from ops.v5_a05_cadence_receipt
   where organization_tenant_id = v_tenant and subject_type = p_subject_type and subject_ref = p_subject_ref
     and issued_at >= v_now - interval '14 days' and issued_at <= v_now;

  select count(*) into v_miss_count from (
    select issued_at,
           lag(issued_at) over (order by issued_at) as prior_issued_at
      from ops.v5_a05_cadence_receipt
     where organization_tenant_id = v_tenant and subject_type = p_subject_type and subject_ref = p_subject_ref
  ) gaps where prior_issued_at is not null and issued_at - prior_issued_at > interval '14 days';

  if v_last.id is null then
    -- No receipt has EVER been issued. Until the activation anchor plus one
    -- interval has passed, that is honestly "nothing to report yet" --
    -- before v_activation_anchor+14d, a young subject has not had a chance
    -- to check in. Once that window closes with STILL no receipt, it is a
    -- real miss (review finding 3): the subject went the whole interval,
    -- from a real server fact, without ever checking in, and the sweep must
    -- be able to escalate that.
    if v_activation_anchor is not null and v_now > v_activation_anchor + interval '14 days' then
      return jsonb_build_object(
        'schema_version', 'doctorcre-v5-delivery-cadence.v1', 'tenant', v_tenant,
        'subject', jsonb_build_object('type', p_subject_type, 'ref', p_subject_ref),
        'interval_days', 14, 'status', 'missed',
        'reason_id', 'cadence_interval_exceeded_since_activation', 'requires_replan', true,
        'last_receipt_issued_at', null, 'expires_at', v_activation_anchor + interval '14 days',
        'days_since_last_receipt', extract(epoch from (v_now - v_activation_anchor)) / 86400.0,
        'receipts_in_window', v_receipts_in_window, 'miss_count_in_history', v_miss_count);
    end if;
    return jsonb_build_object(
      'schema_version', 'doctorcre-v5-delivery-cadence.v1', 'tenant', v_tenant,
      'subject', jsonb_build_object('type', p_subject_type, 'ref', p_subject_ref),
      'interval_days', 14, 'status', 'no_receipt_on_record',
      'reason_id', 'no_cadence_receipt_on_record', 'requires_replan', false,
      'last_receipt_issued_at', null, 'expires_at', null, 'days_since_last_receipt', null,
      'receipts_in_window', v_receipts_in_window, 'miss_count_in_history', v_miss_count);
  end if;

  if v_now <= v_last.expires_at then
    return jsonb_build_object(
      'schema_version', 'doctorcre-v5-delivery-cadence.v1', 'tenant', v_tenant,
      'subject', jsonb_build_object('type', p_subject_type, 'ref', p_subject_ref),
      'interval_days', 14, 'status', 'current', 'reason_id', 'within_cadence_interval',
      'requires_replan', false, 'last_receipt_issued_at', v_last.issued_at,
      'expires_at', v_last.expires_at,
      'days_since_last_receipt', extract(epoch from (v_now - v_last.issued_at)) / 86400.0,
      'receipts_in_window', v_receipts_in_window, 'miss_count_in_history', v_miss_count);
  end if;

  return jsonb_build_object(
    'schema_version', 'doctorcre-v5-delivery-cadence.v1', 'tenant', v_tenant,
    'subject', jsonb_build_object('type', p_subject_type, 'ref', p_subject_ref),
    'interval_days', 14, 'status', 'missed', 'reason_id', 'cadence_interval_exceeded',
    'requires_replan', true, 'last_receipt_issued_at', v_last.issued_at,
    'expires_at', v_last.expires_at,
    'days_since_last_receipt', extract(epoch from (v_now - v_last.issued_at)) / 86400.0,
    'receipts_in_window', v_receipts_in_window, 'miss_count_in_history', v_miss_count);
end;
$$;

comment on function ops.v5_a05_cadence_status(text,text) is
  'V5-A05: read-only production mirror of evaluateCadenceReceipt. Reads clock_timestamp() itself; takes no caller-supplied now (excluded_scope: clock reset).';

-- ---------------------------------------------------------------------------
-- ops.v5_a05_record_cadence_receipt -- the one write door onto the receipt
-- table. It performs no escalation itself: that is the scheduled sweep's job
-- (included_scope "replan on miss" is a detection+escalation duty, not an
-- issuance-time side effect), reading this table through
-- ops.v5_a05_cadence_status and raising through raise-delivery-cadence-alert.
--
-- REVIEW FINDING 2 (Opus adversarial review of PR #1236, round 1): the prior
-- signature took p_evidence jsonb straight from the caller -- any writer
-- could mint a fresh receipt with evidence {}, which is exactly the "clock
-- reset" Q008.D2 forbids (a subject resets its own miss clock by asserting
-- it, with nothing behind the assertion).
--
-- WHAT THE DESIGN ASKS FOR AND WHY IT IS NOT BUILT: the coordinator's
-- direction was to look up a qualifying row in the Completion Register
-- (ops.completion_receipt / ops.completion_subject / ops.completion_observation,
-- migration 0431) and store it as a foreign key, with no caller evidence at
-- all. THAT SCHEMA EXISTS BUT IS UNPOPULATED IN PRODUCTION: grepping every
-- migration and every mcp-server handler, the only INSERTs into
-- ops.completion_subject/ops.completion_receipt/ops.completion_observation
-- anywhere in this repository are the completion-register-schema-local-pg-
-- gate.py selftest's own fixture rows. No producer exists that creates a
-- completion_subject for an engineering_program (or any) subject, so there
-- is no real row this function could look up for
-- (engineering_program, doctorcre-v5) or any other V5-A05 subject. Wiring a
-- fake stable_key/capability_class convention to satisfy the letter of an
-- FK here would be inventing the very producer the coordinator asked NOT to
-- invent.
--
-- WHAT IS FIXED NOW, bounded to what a producer-less system can honestly do:
-- caller-supplied evidence is removed from the signature entirely. Evidence
-- is now assembled server-side only (issuer, tenant, timestamp) and can
-- never be asserted by the caller, closing the actual clock-reset hole (a
-- writer can no longer manufacture ANY evidence content, real or fake) even
-- though it cannot yet cite a verified Completion Register outcome. Wiring
-- a real Completion Register producer for the V5-A05 subject, and returning
-- to this function to add the FK the design calls for, is named follow-up
-- work, not silently assumed done.
-- ---------------------------------------------------------------------------

create or replace function ops.v5_a05_record_cadence_receipt(
  p_subject_type text, p_subject_ref text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_tenant text := ops.completion_runtime_tenant();
  v_actor uuid := ops.portfolio_writer_actor_id();
  v_now timestamptz := clock_timestamp();
  v_prior ops.v5_a05_cadence_receipt%rowtype;
  v_replan_of uuid := null;
  v_row ops.v5_a05_cadence_receipt%rowtype;
  v_evidence jsonb;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'recording a v5-a05 cadence receipt requires the writer or authority capability';
  end if;
  if btrim(coalesce(p_subject_type, '')) = '' or btrim(coalesce(p_subject_ref, '')) = '' then
    raise exception 'subject_type and subject_ref are required' using errcode = '22023';
  end if;

  -- Server-computed only. No caller-supplied field is ever stored here --
  -- see the header comment above for why this is not yet a Completion
  -- Register foreign key.
  v_evidence := jsonb_build_object(
    'schema_version', 'v5-a05-cadence-receipt-evidence.v1',
    'issued_by', v_actor, 'issued_at', v_now,
    'completion_register_outcome_id', null,
    'disclosed_gap', 'no Completion Register producer exists yet for this subject; see migrations/0617_delivery_cadence_a05.sql');

  select * into v_prior from ops.v5_a05_cadence_receipt
   where organization_tenant_id = v_tenant and subject_type = p_subject_type and subject_ref = p_subject_ref
   order by issued_at desc limit 1;
  if v_prior.id is not null and v_prior.expires_at < v_now then
    v_replan_of := v_prior.id;
  end if;

  insert into ops.v5_a05_cadence_receipt
    (subject_type, subject_ref, issued_at, expires_at, evidence, replan_of, created_by, idempotency_key)
  values
    (p_subject_type, p_subject_ref, v_now, v_now + interval '14 days',
     v_evidence, v_replan_of, v_actor, p_idempotency_key)
  on conflict (organization_tenant_id, idempotency_key) do nothing
  returning * into v_row;

  if v_row.id is null then
    select * into v_row from ops.v5_a05_cadence_receipt
     where organization_tenant_id = v_tenant and idempotency_key = p_idempotency_key;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'receipt_id', v_row.id,
      'issued_at', v_row.issued_at, 'expires_at', v_row.expires_at, 'replan_of', v_row.replan_of);
  end if;

  return jsonb_build_object('ok', true, 'deduplicated', false, 'receipt_id', v_row.id,
    'issued_at', v_row.issued_at, 'expires_at', v_row.expires_at, 'replan_of', v_row.replan_of);
end;
$$;

comment on function ops.v5_a05_record_cadence_receipt(text,text,uuid) is
  'V5-A05: the one write door onto ops.v5_a05_cadence_receipt. Records that a subject checked in; performs no escalation itself. Evidence is server-computed only -- no caller-supplied evidence field exists (review finding 2); a real Completion Register foreign key is disclosed follow-up work, not yet buildable because no producer populates the Completion Register for this subject.';

-- ---------------------------------------------------------------------------
-- ops.notification_quiet_now -- extracted verbatim from ops.mint_notification
-- (0521:216-223), unchanged behaviour, so both the redefined mint_notification
-- and any future reader compute quiet-hours from exactly one place.
-- ---------------------------------------------------------------------------

create or replace function ops.notification_quiet_now(p_actor uuid)
returns boolean language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_pref ops.notification_preference%rowtype; v_local time;
begin
  select * into v_pref from ops.notification_preference where actor = p_actor;
  if v_pref.quiet_hours_start is null then
    return false;
  end if;
  v_local := (now() at time zone coalesce(v_pref.timezone, 'UTC'))::time;
  return case
    when v_pref.quiet_hours_start <= v_pref.quiet_hours_end
      then v_local >= v_pref.quiet_hours_start and v_local < v_pref.quiet_hours_end
    else v_local >= v_pref.quiet_hours_start or v_local < v_pref.quiet_hours_end
  end;
end;
$$;

comment on function ops.notification_quiet_now(uuid) is
  'V5-A05: extracted from ops.mint_notification''s own inline quiet-hours boolean (0521). The one quiet-hours computation; nothing duplicates it.';

-- ---------------------------------------------------------------------------
-- ops.notification_preference_facts, redefined to CALL ops.notification_quiet_now
-- instead of carrying its own second copy of the same boolean.
--
-- Review finding 9 (Opus adversarial review of PR #1236, round 1): this
-- migration's own header claimed "any future reader compute[s] quiet-hours
-- from exactly one place", but ops.notification_preference_facts (0527) kept
-- its own inline copy of the identical computation until now. Same argument
-- list, same return shape, same zero-argument caller contract -- only the
-- v_quiet assignment changes, from the inline case expression to a call.
-- ---------------------------------------------------------------------------

create or replace function ops.notification_preference_facts()
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_pref ops.notification_preference%rowtype; v_quiet boolean;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'reading notification preferences requires the writer or authority capability';
  end if;
  v_actor := ops.portfolio_writer_actor_id();

  select * into v_pref from ops.notification_preference where actor = v_actor;
  v_quiet := ops.notification_quiet_now(v_actor);

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
  'WR-000116/V5-A05: reads the acting actor''s own notification preferences and whether quiet hours cover this instant, via ops.notification_quiet_now (the one quiet-hours computation; this no longer carries its own copy). ZERO arguments: the actor is resolved inside the body.';

-- ---------------------------------------------------------------------------
-- Held-until-morning delivery (PR #1236 review, round 2, item 2).
--
-- checkable_done 2 says "ordinary blockers batch": an ordinary alert that
-- needs Joe's authority belongs to the morning approval batch, not to the
-- moment it was raised. Before this, a batch_for_morning alert was minted with
-- its device row `pending` at 15:00 exactly like an interruption. Now the mint
-- takes p_hold_for_morning, and a held notification's delivery rows carry the
-- explicit state 'held_until_morning' plus the server-computed instant the
-- hold ends (held_until). The feed and the morning-brief batch both hide a
-- held notification until that instant; nothing pushes it before then.
--
-- The morning window is the recipient's own: it opens at the end of their
-- quiet hours when they have set any, else at 07:00, in their preference
-- timezone (UTC when unset, the same default the quiet-hours computation
-- uses), and stays open for four hours. An alert raised INSIDE the window is
-- not held -- it is already morning. No device push sender exists in this
-- repository (tools/restore-watermark.py PROVIDER_READERS is empty), so a held
-- row is never handed to a provider; restore-watermark classifies it with the
-- other non-pending, provider-free rows.
-- ---------------------------------------------------------------------------

alter table ops.notification_delivery add column held_until timestamptz;
alter table ops.notification_delivery drop constraint notification_delivery_state_check;
alter table ops.notification_delivery add constraint notification_delivery_state_check
  check (state = any (array['pending','delivered','suppressed_quiet_hours','failed','held_until_morning']));
alter table ops.notification_delivery drop constraint notification_delivery_check1;
alter table ops.notification_delivery add constraint notification_delivery_check1
  check ((state in ('pending','held_until_morning')) = (settled_at is null));
alter table ops.notification_delivery add constraint notification_delivery_held_until_matches_state
  check ((state = 'held_until_morning') = (held_until is not null));

comment on column ops.notification_delivery.held_until is
  'V5-A05: when a held_until_morning row''s hold ends -- the start of the recipient''s next morning window, computed by ops.notification_morning_release_at at mint time. Null for every other state.';

create or replace function ops.notification_morning_release_at(p_actor uuid)
returns timestamptz language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_pref ops.notification_preference%rowtype; v_tz text; v_open time;
        v_local timestamp; v_today_open timestamp;
begin
  select * into v_pref from ops.notification_preference where actor = p_actor;
  v_tz := coalesce(v_pref.timezone, 'UTC');
  v_open := case when v_pref.quiet_hours_start is not null and v_pref.quiet_hours_end is not null
                 then v_pref.quiet_hours_end else time '07:00' end;
  v_local := now() at time zone v_tz;
  v_today_open := date_trunc('day', v_local) + v_open;
  if v_local >= v_today_open and v_local < v_today_open + interval '4 hours' then
    return now(); -- inside the morning window: nothing to hold.
  end if;
  if v_local < v_today_open then
    return v_today_open at time zone v_tz;
  end if;
  return (v_today_open + interval '1 day') at time zone v_tz;
end;
$$;

comment on function ops.notification_morning_release_at(uuid) is
  'V5-A05: the start of the actor''s next morning window (quiet-hours end, else 07:00, in their preference timezone; four hours long), or now() when called inside it. Owner-only; ops.mint_notification calls it.';

-- ---------------------------------------------------------------------------
-- ops.mint_notification, redefined with two new trailing parameters.
-- DROP + CREATE because the argument-type list changes; every existing
-- 9-argument call (investigation.js record-signal, the only one) still
-- resolves here with both new parameters defaulted false -- unchanged
-- behaviour for every caller that does not know they exist.
-- ---------------------------------------------------------------------------

-- The role-bundle full-rebuild composer (tools/schema_snapshot_grants.py)
-- accumulates GRANT/REVOKE text across every migration; it has no notion of
-- DROP FUNCTION removing an object's ACL, so the 0521 grant on the old
-- 9-argument signature must be explicitly revoked here or the composer keeps
-- expecting a live grant on a function that no longer exists after the drop
-- below (staging-app-writer-provision-db-gate / staging-database-login-
-- provision-db-gate: "carr_writer differs from canonical full-rebuild plan").
revoke all on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

drop function ops.mint_notification(text,uuid,text,text,text,text,text,text,text);

create function ops.mint_notification(
  p_event_source text, p_event_ref uuid, p_subject_type text, p_subject_ref text,
  p_reason text, p_severity text, p_deep_link text, p_dedupe_key text,
  p_recipient_slug text, p_bypass_quiet_hours boolean default false,
  p_hold_for_morning boolean default false)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_recipient uuid; v_id uuid; v_prior uuid; v_quiet boolean; v_source_exists boolean;
        v_release timestamptz; v_held boolean := false;
begin
  if not (pg_has_role(current_user, 'carr_writer', 'member')
       or pg_has_role(current_user, 'carr_authority', 'member')) then
    raise exception using errcode = '42501',
      message = 'minting a notification requires the writer or authority capability';
  end if;
  if p_bypass_quiet_hours and p_hold_for_morning then
    raise exception 'a notification cannot both bypass quiet hours and be held for morning'
      using errcode = '22023';
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

  select id into v_recipient from public.actor
   where slug = p_recipient_slug and kind = 'human' and active = true;
  if v_recipient is null then
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

  -- V5-A05 morning hold: computed once, applied to every channel alike, so
  -- the in-app row and the device row agree on when the hold ends.
  if p_hold_for_morning then
    v_release := ops.notification_morning_release_at(v_recipient);
    v_held := v_release > now();
  end if;

  if v_held then
    insert into ops.notification_delivery(notification_id, channel, state, settled_at, held_until)
    values (v_id, 'in_app', 'held_until_morning', null, v_release);
  else
    insert into ops.notification_delivery(notification_id, channel, state)
    values (v_id, 'in_app', 'pending');
  end if;

  if exists (select 1 from ops.notification_preference where actor = v_recipient and device_opt_in) then
    if v_held then
      insert into ops.notification_delivery(notification_id, channel, state, settled_at, held_until)
      values (v_id, 'device', 'held_until_morning', null, v_release);
    else
      -- p_bypass_quiet_hours, true only for V5-A05's server-verified urgent
      -- (security_incident/data_loss/outward_harm) alerts, short-circuits the
      -- SAME quiet-hours computation rather than replacing it with a second
      -- one. record-signal never sets either new argument, so its calls are
      -- byte-identical to 0521's behaviour.
      v_quiet := (not p_bypass_quiet_hours) and ops.notification_quiet_now(v_recipient);
      insert into ops.notification_delivery(notification_id, channel, state, settled_at)
      values (v_id, 'device',
              case when v_quiet then 'suppressed_quiet_hours' else 'pending' end,
              case when v_quiet then now() else null end);
    end if;
  end if;

  return jsonb_build_object('ok', true, 'minted', true, 'notification_id', v_id,
    'recipient_actor', v_recipient, 'severity', p_severity, 'dedupe_key', p_dedupe_key,
    'bypassed_quiet_hours', p_bypass_quiet_hours,
    'held_for_morning', v_held, 'held_until', case when v_held then v_release end);
end $$;

comment on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean,boolean) is
  'WR-000113/V5-A05: the only notification writer. p_bypass_quiet_hours and p_hold_for_morning (both default false) are the only additions -- see ops.notification_quiet_now and ops.notification_morning_release_at.';

-- ---------------------------------------------------------------------------
-- ops.notification_feed_facts, redefined (same signature, same caller
-- contract) so a notification held for the morning window is not listed or
-- counted before its hold ends. Every other row is returned exactly as 0521
-- returned it.
-- ---------------------------------------------------------------------------

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
         and not exists (select 1 from ops.notification_delivery h
                          where h.notification_id = n.id and h.state = 'held_until_morning'
                            and h.held_until > now())
       order by n.created_at desc limit v_limit) f;

  select count(*) into v_unread from ops.notification n
   where n.recipient_actor = v_actor
     and not exists (select 1 from ops.notification_read r
                      where r.notification_id = n.id and r.recipient_actor = v_actor)
     and not exists (select 1 from ops.notification_delivery h
                      where h.notification_id = n.id and h.state = 'held_until_morning'
                        and h.held_until > now());

  return jsonb_build_object('ok', true, 'unread_count', v_unread, 'notifications', v_rows);
end $$;

comment on function ops.notification_feed_facts(timestamptz,integer) is
  'WR-000113/V5-A05: resolves the caller''s own actor internally and returns only that actor''s notifications, omitting any still held for the morning window. There is no argument for a recipient.';

-- ---------------------------------------------------------------------------
-- ops.v5_a05_assurance_cadence_batch -- the morning brief's ONLY door onto
-- ops.notification/ops.notification_read for the assurance_cadence section.
-- Review finding (Opus adversarial review of PR #1236, round 1): morning-brief
-- read these two tables DIRECTLY in mcp-server/src/tools.js, on the reader
-- connection, with no carr_reader grant at all -- tools/test-handler-reads-
-- are-granted.py fails, and in production the section reads "unavailable"
-- every day (42501). A raw table grant to carr_reader would let any reader-
-- scoped caller read every actor's notifications; this function is scoped
-- server-side to exactly one recipient (the authenticated actor morning-brief
-- already resolved), the same shape as v5_a05_cadence_status/
-- notification_quiet_now above. STABLE, no write, SECURITY DEFINER because
-- ops.notification/ops.notification_read stay ungranted to carr_reader
-- directly.
-- ---------------------------------------------------------------------------

-- Review round 2, item 4: the recipient slug is an argument (morning-brief
-- runs on the stateless reader connection, which carries no actor context,
-- and passes its server-derived sponsor exactly as the renewal-queue section
-- beside it does), so this door itself refuses any slug that is not an active
-- human partner -- a reader-scoped caller cannot aim it at an arbitrary actor.
-- It also returns only notifications whose morning hold (if any) has ended,
-- so the brief is where a held item surfaces, and not before.
create or replace function ops.v5_a05_assurance_cadence_batch(p_recipient_slug text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
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
$$;

comment on function ops.v5_a05_assurance_cadence_batch(text) is
  'V5-A05: the morning brief''s only door onto ops.notification/ops.notification_read for the assurance_cadence section; granted to carr_reader so the reader connection never touches those tables directly. Refuses any recipient that is not an active human partner (joe, dell) and omits items still held for the morning window.';

-- ---------------------------------------------------------------------------
-- Grants.
-- ---------------------------------------------------------------------------

grant select on ops.v5_a05_cadence_receipt to carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.v5_a05_cadence_receipt
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.v5_a05_cadence_status(text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.v5_a05_cadence_status(text,text) to carr_writer, carr_authority;

revoke all on function ops.v5_a05_record_cadence_receipt(text,text,uuid)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.v5_a05_record_cadence_receipt(text,text,uuid) to carr_writer, carr_authority;

-- Review finding 9 (Opus adversarial review of PR #1236, round 1): nothing
-- external ever needs to call this directly -- ops.mint_notification and
-- ops.notification_preference_facts are both themselves SECURITY DEFINER and
-- call it internally, which Postgres checks against their OWNER's
-- privileges, not the connecting role's. A carr_writer/carr_authority grant
-- here was therefore never load-bearing and only widened who could probe
-- another actor's quiet-hours state directly. Owner-only.
revoke all on function ops.notification_quiet_now(uuid)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- Owner-only for the same reason: ops.mint_notification (SECURITY DEFINER)
-- is its only caller.
revoke all on function ops.notification_morning_release_at(uuid)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean,boolean)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean,boolean)
  to carr_writer, carr_authority;

revoke all on function ops.v5_a05_assurance_cadence_batch(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.v5_a05_assurance_cadence_batch(text) to carr_reader;
