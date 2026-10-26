-- rollback: forward-only; declined program rows keep their withdrawal receipts and reasons
-- lock-review: replace three functions without rewriting or locking business tables
-- 0982_decline_unstarted_program_work_request.sql
-- Joe approved retiring the two old capability-program catalogs on 2026-10-26.
-- decline-work-request is the one withdrawal door, so it now also admits
-- program rows (WR-AI-*, WR-SIEP-*) that are captured, triaged, or ready and
-- unstarted. Started, verifying and closed program rows still refuse.

create or replace function ops.decline_sourced_work_request(
  p_work_request text, p_base_version integer, p_exit_reason text,
  p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, exit_reason text, closed_at timestamp with time zone)
language plpgsql security definer
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
declare
  v_id uuid;
  v_actor public.actor%rowtype;
  v_row ops.work_request%rowtype;
  v_now timestamp with time zone := now();
begin
  if p_exit_reason is null or btrim(p_exit_reason) = '' then
    raise exception 'withdrawal_reason_missing: a withdrawal must record why';
  end if;
  select a.* into v_actor from public.actor a where a.slug = p_actor_slug;
  if v_actor.id is null then
    raise exception 'unknown_actor: unknown actor %', p_actor_slug;
  end if;
  if not coalesce(v_actor.active and (
    v_actor.kind = 'human'
    or (v_actor.kind = 'automation'
        and nullif(btrim(current_setting('carr.sponsoring_human_slug', true)), '') in ('joe', 'dell'))
  ), false) then
    raise exception 'sponsored_actor_required: declining a Work Request requires an active human actor or a sponsored agent'
      using errcode = '42501';
  end if;

  select w.* into v_row from ops.work_request w where w.ref = p_work_request for update;
  v_id := v_row.id;
  if v_id is null then
    raise exception 'work_request_not_found: no Work Request %', p_work_request;
  end if;
  if v_row.state not in ('captured','triaged','ready')
     or (v_row.capture_idempotency_key is null and v_row.program_key is null)
     or (v_row.state = 'ready' and (
          v_row.claimed_at is not null or v_row.executor_actor is not null or v_row.started_at is not null
          or exists (select 1 from ops.work_request_execution_assignment a where a.work_request_id = v_id))) then
    raise exception 'work_request_not_declinable: % is in state %; only captured, triaged, or unstarted ready sourced or program requests may be declined',
      p_work_request, v_row.state;
  end if;
  if v_row.version <> p_base_version then
    raise exception 'work_request_version_conflict: % is in state % at version %, not version %',
      p_work_request, v_row.state, v_row.version, p_base_version;
  end if;

  insert into ops.work_request_withdrawal_receipt
    (work_request_id, idempotency_key, base_version, result_version, final_state,
     exit_reason, superseded_by, withdrawn_by_actor_id, withdrawn_at)
  values (v_id, p_idempotency_key, p_base_version, p_base_version + 1, 'declined',
          p_exit_reason, null, v_actor.id, v_now)
  on conflict (idempotency_key) do nothing;

  update ops.work_request w
     set state = 'declined', exit_reason = p_exit_reason, closed_at = v_now,
         version = w.version + 1, updated_at = v_now
   where w.id = v_id;

  return query
    select w.ref, w.state, w.version, w.exit_reason, w.closed_at
      from ops.work_request w where w.id = v_id;
end;
$fn$;

-- The SIEP guards only knew the forward package path. A receipted decline of an
-- unstarted ready package is the one exit added; the transition guard checks it.
create or replace function ops.siep_program_identity_guard()
returns trigger
language plpgsql security definer
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
begin
  if old.program_key='carr-system-integrity-elimination-v1'
     and not (old.state='ready' and new.state='declined')
     and (to_jsonb(new)-array['state','executor_actor','claimed_at','started_at','closed_at','updated_at','version',
                                  'completion_kind','completion_evidence','verification_accepted_at','verification_evidence_ref'])
         is distinct from
         (to_jsonb(old)-array['state','executor_actor','claimed_at','started_at','closed_at','updated_at','version',
                                  'completion_kind','completion_evidence','verification_accepted_at','verification_evidence_ref']) then
    raise exception 'SIEP package identity and contract projection are immutable';
  end if;
  return new;
end $fn$;

create or replace function ops.siep_program_transition_guard()
returns trigger
language plpgsql security definer
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
begin
  if old.program_key is distinct from 'carr-system-integrity-elimination-v1' then return new; end if;
  if old.state='ready' and new.state='declined' then
    if new.version<>old.version+1
       or old.executor_actor is not null or old.claimed_at is not null or old.started_at is not null
       or (to_jsonb(new)-array['state','exit_reason','closed_at','version','updated_at'])
          is distinct from (to_jsonb(old)-array['state','exit_reason','closed_at','version','updated_at'])
       or not exists (
         select 1 from ops.work_request_withdrawal_receipt r
          where r.work_request_id=old.id and r.base_version=old.version
            and r.result_version=new.version and r.final_state='declined'
            and r.exit_reason=new.exit_reason and r.superseded_by is null) then
      raise exception 'SIEP package decline requires an unstarted ready package and its withdrawal receipt';
    end if;
    return new;
  end if;
  if old.state='confirmed_closed' and new is distinct from old then raise exception 'closed SIEP package is immutable'; end if;
  if old.state is not distinct from new.state and new is distinct from old then raise exception 'SIEP package mutations require a typed state transition'; end if;
  if old.state is distinct from new.state and (old.state,new.state) not in (
    ('ready','claimed'),('claimed','in_progress'),('in_progress','verification'),
    ('verification','awaiting_release'),('awaiting_release','released'),('released','confirmed_closed')
  ) then raise exception 'invalid SIEP package state transition'; end if;
  if new.version<>old.version+1 then raise exception 'SIEP package transitions require exact version plus one'; end if;
  if old.state='ready' and (new.executor_actor is distinct from 'system' or old.executor_actor is not null) then
    raise exception 'SIEP claim executor is server-derived';
  end if;
  if old.state<>'ready' and new.executor_actor is distinct from old.executor_actor then
    raise exception 'SIEP executor binding is immutable after claim';
  end if;
  if not exists(
    select 1 from public.event e where e.subject_type='work_request' and e.subject_id=old.id
      and e.verb=case when old.state='ready' then 'siep-claim-package' else 'siep-transition-package' end
      and e.old_value=to_jsonb(old.state) and e.new_value->>'state'=new.state
      and (e.new_value->>'version')::integer=new.version
  ) then raise exception 'SIEP package transition requires its exact command event'; end if;
  if new.state<>'confirmed_closed' and (
    new.completion_kind is distinct from old.completion_kind
    or new.completion_evidence is distinct from old.completion_evidence
    or new.verification_accepted_at is distinct from old.verification_accepted_at
    or new.verification_evidence_ref is distinct from old.verification_evidence_ref
    or new.closed_at is distinct from old.closed_at
  ) then raise exception 'SIEP completion fields may change only at terminal package closure'; end if;
  return new;
end $fn$;
