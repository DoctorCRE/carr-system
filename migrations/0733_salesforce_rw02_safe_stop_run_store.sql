-- V5-RW02 safe stops and follow-ups: the server-side attended-run outcome
-- ledger behind the 5-consecutive-clean counter (decision 493de438), the
-- revocation record for Dell's Salesforce-read consent (decision bf194d7d),
-- the read-only doors the browser read adapter uses for the consent record and
-- for earlier loop filings, and the deal invoiced marker that brings
-- closed-won deals not yet invoiced into the reconciliation absence scope.
-- No function here contacts Salesforce or grants an external effect.

-- ---------------------------------------------------------------------------
-- 1. The invoiced marker.
-- ---------------------------------------------------------------------------

alter table public.deal add column invoiced_on date;

comment on column public.deal.invoiced_on is
  'The date the deal was invoiced (V5-RW02, 0733). Set through update-deal. Salesforce reconciliation compares every deal that is open or closed won and NOT yet invoiced, because a won deal still owes Dell''s Salesforce its opportunity until the invoice goes out (corporate credit and payment depend on it). Null means not invoiced.';

-- The view keeps its columns in order and gains invoiced_on at the end; it
-- still exposes neither the Salesforce placeholder columns nor source_row.
create or replace view public.v_deal_reconciliation_read as
select d.id, d.name, d.salesforce_id, d.version as base_version,
       d.phase, d.outcome, d.closed_on, d.invoiced_on
  from public.deal d;

-- ---------------------------------------------------------------------------
-- 2. The attended-run outcome ledger.
-- ---------------------------------------------------------------------------

create table ops.rw02_attended_run (
  seq bigint generated always as identity primary key,
  tenant text not null default 'carr-internal' check (tenant = 'carr-internal'),
  action_kind text not null check (action_kind in (
    'commission_agreement_prepare', 'etl_document_prepare', 'opportunity_create',
    'opportunity_link_record', 'opportunity_phase_update', 'presence_membership_reconciliation')),
  run_ref text not null check (run_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$'),
  outcome text not null check (outcome in ('clean', 'corrected', 'failed', 'refused', 'stopped')),
  stop_class text check (stop_class is null or stop_class in (
    'auth_challenge', 'binding', 'consent', 'inconsistent_result', 'policy_conflict', 'ui_drift',
    'unexpected_recipient_or_account')),
  reason_id text check (reason_id is null or reason_id ~ '^[a-z][a-z0-9_]{2,63}$'),
  execution_mode text not null default 'attended' check (execution_mode = 'attended'),
  recorded_by text not null,
  recorded_at timestamptz not null default clock_timestamp(),
  unique (action_kind, run_ref),
  -- A stop names its class and reason; nothing else does.
  check ((outcome = 'stopped') = (stop_class is not null and reason_id is not null)),
  check ((stop_class is null) = (reason_id is null))
);

comment on table ops.rw02_attended_run is
  'Append-only V5-RW02 attended-run outcomes, one per (kind, run). The consecutive-clean count per kind is derived from these rows: every run that is not clean resets it (decision 493de438). Reaching the threshold promotes nothing.';

revoke all on ops.rw02_attended_run from public, carr_reader, carr_writer, carr_jobs, carr_authority;

create table ops.rw02_consent_revocation (
  id uuid primary key default gen_random_uuid(),
  decision_id uuid not null,
  revoked_by text not null check (revoked_by in ('joe', 'dell')),
  revoked_by_actor_id uuid not null,
  human_quote text not null check (length(btrim(human_quote)) >= 3 and length(human_quote) <= 2000),
  idempotency_key uuid not null unique,
  revoked_at timestamptz not null default clock_timestamp()
);

comment on table ops.rw02_consent_revocation is
  'Append-only revocations of a partner consent decision that V5-RW02 Salesforce reads depend on. A revoked consent stops the next run before the browser is touched. To resume, a partner logs a NEW consent decision and a reviewed change pins it.';

revoke all on ops.rw02_consent_revocation from public, carr_reader, carr_writer, carr_jobs, carr_authority;

create or replace function ops.refuse_rw02_safe_stop_rewrite()
returns trigger language plpgsql as $fn$
begin
  raise exception using
    message = 'rw02_safe_stop_append_only',
    detail = format('%s on %s is refused; it is append-only', tg_op, tg_table_name);
end
$fn$;

create trigger rw02_attended_run_append_only
  before update or delete on ops.rw02_attended_run
  for each row execute function ops.refuse_rw02_safe_stop_rewrite();
create trigger rw02_attended_run_no_truncate
  before truncate on ops.rw02_attended_run
  for each statement execute function ops.refuse_rw02_safe_stop_rewrite();
create trigger rw02_consent_revocation_append_only
  before update or delete on ops.rw02_consent_revocation
  for each row execute function ops.refuse_rw02_safe_stop_rewrite();
create trigger rw02_consent_revocation_no_truncate
  before truncate on ops.rw02_consent_revocation
  for each statement execute function ops.refuse_rw02_safe_stop_rewrite();

-- Consecutive clean runs of one kind: clean runs after the last run of that
-- kind that was not clean. Another kind's runs never count or reset.
create or replace function ops.rw02_consecutive_clean(p_action_kind text)
returns integer
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select count(*)::integer
    from ops.rw02_attended_run r
   where r.action_kind = p_action_kind
     and r.outcome = 'clean'
     and r.seq > coalesce((select max(u.seq) from ops.rw02_attended_run u
                            where u.action_kind = p_action_kind and u.outcome <> 'clean'), 0)
$$;

-- Record one run's outcome. A replay with the same facts answers the stored
-- row; the same run with different facts is refused. A read run claimed clean
-- after it recorded a page stop is refused: the ledger cannot be told a
-- stopped run was clean.
create or replace function ops.rw02_record_run(
  p_action_kind text, p_run_ref text, p_outcome text, p_stop_class text, p_reason_id text
) returns jsonb
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_row ops.rw02_attended_run%rowtype;
  v_replayed boolean := false;
begin
  v_actor := ops.f01_context_actor_slug();
  if v_actor is null or length(v_actor) = 0 then raise exception 'rw02_actor_unknown'; end if;
  select * into v_row from ops.rw02_attended_run
   where action_kind = p_action_kind and run_ref = p_run_ref for update;
  if found then
    if v_row.outcome is distinct from p_outcome or v_row.stop_class is distinct from p_stop_class
       or v_row.reason_id is distinct from p_reason_id or v_row.recorded_by is distinct from v_actor then
      raise exception 'rw02_run_outcome_conflict';
    end if;
    v_replayed := true;
  else
    if p_outcome = 'clean' and exists (
         select 1 from ops.rw02_runtime_record s
          where s.operation = 'record-salesforce-page-stop'
            and starts_with(s.idempotency_key, 'rw02-read:' || p_run_ref || ':')) then
      raise exception 'rw02_clean_run_contradicted';
    end if;
    -- Two first recordings of one run can race past the select above (it
    -- locks nothing while no row exists). The loser inserts nothing and is
    -- answered as a replay or a typed conflict, never a raw unique violation.
    insert into ops.rw02_attended_run (action_kind, run_ref, outcome, stop_class, reason_id, recorded_by)
    values (p_action_kind, p_run_ref, p_outcome, p_stop_class, p_reason_id, v_actor)
    on conflict (action_kind, run_ref) do nothing
    returning * into v_row;
    if not found then
      select * into v_row from ops.rw02_attended_run
       where action_kind = p_action_kind and run_ref = p_run_ref;
      if v_row.outcome is distinct from p_outcome or v_row.stop_class is distinct from p_stop_class
         or v_row.reason_id is distinct from p_reason_id or v_row.recorded_by is distinct from v_actor then
        raise exception 'rw02_run_outcome_conflict';
      end if;
      v_replayed := true;
    end if;
  end if;
  return jsonb_build_object(
    'action_kind', v_row.action_kind, 'run_ref', v_row.run_ref, 'outcome', v_row.outcome,
    'stop_class', v_row.stop_class, 'reason_id', v_row.reason_id, 'recorded_by', v_row.recorded_by,
    'recorded_at', to_char(v_row.recorded_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'replayed', v_replayed,
    'consecutive_clean', ops.rw02_consecutive_clean(v_row.action_kind));
end;
$$;

-- The ordered history of one kind, oldest first.
create or replace function ops.rw02_attended_runs(p_action_kind text)
returns table(run_ref text, action_kind text, outcome text, execution_mode text)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select r.run_ref, r.action_kind, r.outcome, r.execution_mode
    from ops.rw02_attended_run r
   where r.action_kind = p_action_kind
   order by r.seq
$$;

-- ---------------------------------------------------------------------------
-- 3. Consent: the decision record, and its revocation.
-- ---------------------------------------------------------------------------

-- What the adapter needs to decide consent, and nothing more: whether the
-- decision exists as a logged decision, under which partner's sponsorship,
-- whether it carries a partner's literal words, and whether it was revoked.
create or replace function ops.rw02_consent_record(p_decision_id uuid)
returns jsonb
language sql stable security definer
set search_path = pg_catalog, public, ops
as $$
  select jsonb_build_object(
    'record', (select jsonb_build_object(
                  'decision_id', e.subject_id::text,
                  'sponsoring_human_slug', e.sponsoring_human_slug,
                  'human_quote_present', coalesce(length(btrim(e.human_quote)), 0) > 0)
                 from public.event e
                 join public.record_source rs
                   on rs.entity_type = 'event' and rs.entity_id = e.id
                  and rs.source_system = 'decision-history'
                where e.subject_id = p_decision_id
                  and e.subject_type = 'decision'
                  and e.verb = 'log-decision'
                order by e.occurred_at, e.id
                limit 1),
    'revoked', exists (select 1 from ops.rw02_consent_revocation r where r.decision_id = p_decision_id))
$$;

-- A partner withdraws a consent decision. The partner is the verified human
-- the server sets for a humanOnly act, never an argument. The acting actor the
-- server established for the transaction must also resolve through
-- ops.portfolio_writer_actor_id (the 0700 consent precedent): an active actor,
-- and when that actor is a human, the same human as the verified partner. Its
-- id is stored with the revocation, so every revocation names who made it.
create or replace function ops.rw02_revoke_consent(
  p_decision_id uuid, p_human_quote text, p_idempotency_key uuid
) returns jsonb
language plpgsql volatile security definer
set search_path = pg_catalog, public, ops
as $$
declare
  v_partner text := nullif(current_setting('carr.verified_human_actor_slug', true), '');
  v_actor_id uuid;
  v_row ops.rw02_consent_revocation%rowtype;
begin
  if v_partner is null or v_partner not in ('joe', 'dell') then
    raise exception 'rw02_verified_partner_required';
  end if;
  begin
    v_actor_id := ops.portfolio_writer_actor_id();
  -- Only the identity refusals it raises (P0001) mean "no verified partner";
  -- a connection, serialization or any other error propagates as itself.
  exception when raise_exception then
    raise exception 'rw02_verified_partner_required';
  end;
  select * into v_row from ops.rw02_consent_revocation where idempotency_key = p_idempotency_key;
  if found then
    if v_row.decision_id <> p_decision_id or v_row.revoked_by <> v_partner
       or v_row.revoked_by_actor_id <> v_actor_id or v_row.human_quote <> p_human_quote then
      raise exception 'rw02_revocation_conflict';
    end if;
  else
    if not exists (select 1 from public.event e
                    where e.subject_id = p_decision_id and e.subject_type = 'decision'
                      and e.verb = 'log-decision') then
      raise exception 'rw02_consent_decision_unknown';
    end if;
    insert into ops.rw02_consent_revocation (decision_id, revoked_by, revoked_by_actor_id, human_quote, idempotency_key)
    values (p_decision_id, v_partner, v_actor_id, p_human_quote, p_idempotency_key)
    returning * into v_row;
  end if;
  return jsonb_build_object('revocation_id', v_row.id, 'decision_id', v_row.decision_id::text,
    'revoked_by', v_row.revoked_by,
    'revoked_at', to_char(v_row.revoked_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. Earlier loop filings for one finding (the loop-episode read).
-- ---------------------------------------------------------------------------

-- Every add-loop tool call filed under the base key or one of its episode
-- keys (<base>:e<n>), with the loop's status, or null when the loop row is
-- missing. Only RW02 finding keys are answered.
create or replace function ops.rw02_loop_episodes(p_base_key text)
returns table(idempotency_key text, status text)
language sql stable security definer
set search_path = pg_catalog, public, ops
as $$
  select tc.idempotency_key, li.status
    from public.tool_call tc
    left join public.loop_item li
      on (tc.response->>'loop_id') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     and li.id = (tc.response->>'loop_id')::uuid
   where p_base_key ~ '^rw02-(missing-joe|unknown-to-carr):[0-9a-f]{32}$'
     and tc.verb = 'add-loop'
     -- Exactly the base key or a well-formed episode key (<base>:e2 and up).
     -- A malformed look-alike (<base>:eX, <base>:e0) is not an episode and is
     -- not answered, so no writer can wedge reconciliation with one. The base
     -- is hex-only (checked above), so it is safe inside the pattern.
     and tc.idempotency_key ~ ('^' || p_base_key || '(:e([2-9]|[1-9][0-9]{1,5}))?$')
   order by tc.created_at, tc.idempotency_key
$$;

revoke all on function ops.refuse_rw02_safe_stop_rewrite() from public;
revoke all on function ops.rw02_consecutive_clean(text) from public;
revoke all on function ops.rw02_record_run(text,text,text,text,text) from public;
revoke all on function ops.rw02_attended_runs(text) from public;
revoke all on function ops.rw02_consent_record(uuid) from public;
revoke all on function ops.rw02_revoke_consent(uuid,text,uuid) from public;
revoke all on function ops.rw02_loop_episodes(text) from public;
grant execute on function ops.rw02_record_run(text,text,text,text,text) to carr_writer, carr_authority;
grant execute on function ops.rw02_revoke_consent(uuid,text,uuid) to carr_writer, carr_authority;
grant execute on function ops.rw02_consecutive_clean(text) to carr_reader, carr_writer, carr_authority;
grant execute on function ops.rw02_attended_runs(text) to carr_reader, carr_writer, carr_authority;
grant execute on function ops.rw02_consent_record(uuid) to carr_reader, carr_writer, carr_authority;
grant execute on function ops.rw02_loop_episodes(text) to carr_reader, carr_writer, carr_authority;

do $rw02_0733$
begin
  if (select count(*) from pg_trigger
       where tgrelid in ('ops.rw02_attended_run'::regclass, 'ops.rw02_consent_revocation'::regclass)
         and not tgisinternal) <> 4 then
    raise exception '0733 FAILED: RW02 append-only or truncate guard missing';
  end if;
  if has_table_privilege('carr_writer', 'ops.rw02_attended_run', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('carr_reader', 'ops.rw02_attended_run', 'SELECT')
     or has_table_privilege('carr_writer', 'ops.rw02_consent_revocation', 'INSERT,UPDATE,DELETE,TRUNCATE') then
    raise exception '0733 FAILED: an RW02 ledger is directly reachable by a runtime role';
  end if;
  if not has_table_privilege('carr_reader', 'public.v_deal_reconciliation_read', 'SELECT') then
    raise exception '0733 FAILED: the reconciliation view lost its reader grant';
  end if;
end
$rw02_0733$;
