-- 0575_answer_needs_joe_work_request.sql
--
-- The one door that resolves a Work Request waiting on Joe.
--
-- WHY THIS EXISTS. state-machines.v1.json declares "* -> needs_joe" (guard:
-- "one material human decision is named") and "needs_joe -> triaged" (guard:
-- "authorized human decision and evidence recorded; scope and acceptance
-- criteria revalidated") as canonical side transitions, and ops.work_request's
-- base CHECK (0114) has always admitted 'needs_joe' as a legal state. But no
-- verb, function, or trigger in the deployed system ever implemented the
-- needs_joe -> triaged leg: current-work-item and workspace-command-center
-- (0276, 0546) only ever READ needs_joe rows for the "Needs Joe" queue. The
-- DoctorCRE Model Room's "Answer Waiting for Joe" control has nothing to call.
-- This closes that gap with the narrowest possible door: it records Joe's
-- answer text and makes the sole transition the canonical machine allows out
-- of needs_joe, exactly as review-and-triage (0175) makes the sole transition
-- out of captured. It does not create the needs_joe state on any row, assign,
-- dispatch, approve, execute, or make any later transition.
--
-- SATISFYING THE WHOLE GUARD, not half of it. "authorized human decision and
-- evidence recorded" was always the human actor and the answer text -- that
-- half was in the first draft. "scope and acceptance criteria revalidated"
-- was not: nothing forced the answering human to look at the criteria before
-- moving the row, and a needs_joe row with no acceptance criteria at all could
-- be waved through with nothing to revalidate. This version closes both:
--
--   * p_scope_confirmed must be exactly true. It is not a description of
--     what was reviewed -- the base_version compare-and-swap already pins
--     the EXACT row, title, desired_outcome, and acceptance_criteria the
--     human read when they answered, because any concurrent edit bumps the
--     version and voids that base_version. Confirming scope AT that pinned
--     version is what "revalidated" means here: the human is asserting they
--     looked at the criteria this exact version carries, not some criteria
--     in general.
--   * a row with empty or absent acceptance_criteria has nothing to
--     revalidate, so it is refused outright (acceptance_criteria_missing)
--     rather than silently accepted as vacuously satisfied.
--   * the receipt stores a sha256 digest of the acceptance_criteria the
--     human confirmed against, so a later reader can prove which criteria
--     text a given answer actually revalidated, and an optional evidence_ref
--     names where the "evidence recorded" half of the guard lives (a doc
--     conversation turn, a loop, a decision -- this door does not require a
--     particular shape, only that the pointer be carried through to the
--     audit event alongside the answer).
--
-- SCOPE. ops.work_request_sourced_capture_shape (0426) never admits 'needs_joe'
-- for a SOURCED row (capture_idempotency_key is not null): its three arms name
-- captured/triaged/ready/declined/superseded only. A sourced row can therefore
-- never be found by this function's state='needs_joe' guard, and this
-- migration does not widen that shape check -- narrowing which subtype of Work
-- Request the sourced Program 6 pipeline uses is a separate decision, not
-- this door's to make. General (non-sourced, non-program) and program rows
-- carry no per-state field shape requirement at all, so they may already be
-- needs_joe and this function answers them.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs each
-- migration inside its own single transaction.


alter table ops.work_request
  add column if not exists joe_answer_text text,
  add column if not exists joe_answered_by_actor_id uuid references public.actor(id),
  add column if not exists joe_answered_at timestamptz;

-- Shaped after ops.work_request_triage_receipt (0175): a private, append-only-
-- by-convention receipt that authorizes the sole needs_joe-to-triaged
-- transition and carries the idempotency binding for safe replay. Carries the
-- scope-confirmation and evidence pointer that satisfy the rest of the
-- needs_joe -> triaged guard, plus a digest of the exact acceptance_criteria
-- the human confirmed against.
create table if not exists ops.work_request_joe_answer_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id),
  idempotency_key uuid not null unique,
  base_version integer not null check (base_version > 0),
  answer_text text not null check (btrim(answer_text) <> ''),
  scope_confirmed boolean not null check (scope_confirmed),
  evidence_ref text check (evidence_ref is null or btrim(evidence_ref) <> ''),
  acceptance_criteria_digest text not null check (acceptance_criteria_digest ~ '^sha256:[0-9a-f]{64}$'),
  answered_by_actor_id uuid not null references public.actor(id),
  result_version integer not null check (result_version > 0),
  answered_at timestamptz not null default now(),
  unique (work_request_id)
);

comment on table ops.work_request_joe_answer_receipt is
  'Private receipt that authorizes the sole needs_joe-to-triaged transition (ops.answer_work_request_for_joe). Not a dispatch or execution record.';

revoke all on table ops.work_request_joe_answer_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on table ops.work_request_joe_answer_receipt to carr_reader;

create or replace function ops.answer_work_request_for_joe(
  p_work_request text,
  p_base_version integer,
  p_answer_text text,
  p_scope_confirmed boolean,
  p_evidence_ref text,
  p_idempotency_key uuid
)
returns table (
  id uuid,
  ref text,
  state text,
  version integer,
  answer_text text,
  scope_confirmed boolean,
  evidence_ref text,
  acceptance_criteria_digest text,
  answered_by_actor_slug text,
  answered_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor_slug text;
  v_actor public.actor%rowtype;
  v_work_request ops.work_request%rowtype;
  v_receipt ops.work_request_joe_answer_receipt%rowtype;
  v_criteria_digest text;
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_answer_text is null or btrim(p_answer_text) = ''
     or p_idempotency_key is null then
    raise exception 'answering a Work Request for Joe requires a ref, exact positive base version, non-empty answer text, and UUID idempotency key';
  end if;

  if p_scope_confirmed is distinct from true then
    raise exception 'scope_confirmed must be exactly true: the base_version compare-and-swap pins the exact scope and acceptance criteria being revalidated';
  end if;

  if p_evidence_ref is not null and btrim(p_evidence_ref) = '' then
    raise exception 'evidence_ref must be null or a non-empty pointer';
  end if;

  v_actor_slug := ops.authority_actor_slug();
  select a.* into v_actor from public.actor a
   where a.slug = v_actor_slug and a.active and a.kind = 'human'
   for share;
  if not found then
    raise exception 'authority session user is not an active human actor';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('work-request-joe-answer:' || p_idempotency_key, 0));
  select r.* into v_receipt from ops.work_request_joe_answer_receipt r
   where r.idempotency_key = p_idempotency_key
   for share;
  if found then
    select w.* into v_work_request from ops.work_request w where w.id = v_receipt.work_request_id for share;
    if not found
       or v_receipt.base_version is distinct from p_base_version
       or btrim(v_receipt.answer_text) is distinct from btrim(p_answer_text)
       or v_receipt.scope_confirmed is distinct from p_scope_confirmed
       or coalesce(btrim(v_receipt.evidence_ref), '') is distinct from coalesce(btrim(p_evidence_ref), '')
       or v_receipt.answered_by_actor_id is distinct from v_actor.id
       or v_work_request.ref is distinct from p_work_request
       or v_work_request.state is distinct from 'triaged'
       or v_work_request.version is distinct from v_receipt.result_version then
      raise exception 'idempotency key already names a different answer to Joe';
    end if;
    return query select v_work_request.id, v_work_request.ref, v_work_request.state,
      v_work_request.version, v_receipt.answer_text, v_receipt.scope_confirmed, v_receipt.evidence_ref,
      v_receipt.acceptance_criteria_digest, v_actor.slug, v_receipt.answered_at, true;
    return;
  end if;

  select w.* into v_work_request from ops.work_request w
   where w.ref = p_work_request
   for update;
  if not found
     or v_work_request.state is distinct from 'needs_joe'
     or v_work_request.version is distinct from p_base_version then
    raise exception 'only the exact current needs_joe Work Request may be answered';
  end if;

  if v_work_request.acceptance_criteria is null
     or jsonb_typeof(v_work_request.acceptance_criteria) is distinct from 'array'
     or jsonb_array_length(v_work_request.acceptance_criteria) = 0 then
    raise exception 'acceptance_criteria_missing: this Work Request carries no acceptance criteria to revalidate';
  end if;

  v_criteria_digest := 'sha256:' || encode(public.digest(v_work_request.acceptance_criteria::text, 'sha256'), 'hex');

  insert into ops.work_request_joe_answer_receipt
    (work_request_id,idempotency_key,base_version,answer_text,scope_confirmed,evidence_ref,
     acceptance_criteria_digest,answered_by_actor_id,result_version)
  values
    (v_work_request.id,p_idempotency_key,p_base_version,btrim(p_answer_text),p_scope_confirmed,
     nullif(btrim(coalesce(p_evidence_ref,'')),''),v_criteria_digest,v_actor.id,v_work_request.version + 1)
  returning * into v_receipt;

  update ops.work_request w
     set state = 'triaged',
         joe_answer_text = v_receipt.answer_text,
         joe_answered_by_actor_id = v_actor.id,
         joe_answered_at = v_receipt.answered_at,
         version = v_receipt.result_version,
         updated_at = now()
   where w.id = v_work_request.id;
  select w.* into v_work_request from ops.work_request w where w.id = v_work_request.id;
  return query select v_work_request.id, v_work_request.ref, v_work_request.state,
    v_work_request.version, v_receipt.answer_text, v_receipt.scope_confirmed, v_receipt.evidence_ref,
    v_receipt.acceptance_criteria_digest, v_actor.slug, v_receipt.answered_at, false;
end;
$$;

revoke all on function ops.answer_work_request_for_joe(text,integer,text,boolean,text,uuid) from public, carr_reader, carr_writer, carr_jobs;
grant execute on function ops.answer_work_request_for_joe(text,integer,text,boolean,text,uuid) to carr_authority;

comment on function ops.answer_work_request_for_joe(text,integer,text,boolean,text,uuid) is
  'Human-only resolution of the canonical needs_joe -> triaged transition. The answering human is derived from session_user through authority_actor_slug(); no caller actor, tenant, state, assignment, dispatch, approval, or execution field is accepted. scope_confirmed must be exactly true and the row must carry non-empty acceptance_criteria, or the call is refused.';

-- No functional proof block here, matching 0175 (ops.triage_sourced_work_request,
-- the closest analog): ops.authority_actor_slug() derives its answer from
-- session_user, which names an externally provisioned login role
-- (carr_authority_joe / carr_authority_dell, 0273) that this migration lane
-- neither creates nor can SET ROLE into -- only SET SESSION AUTHORIZATION
-- moves session_user, and that is superuser-only and still would not have the
-- role available on a fresh disposable cluster. The DDL, grants, and shapes
-- are proved by mcp-server/test/work-request-joe-answer.test.mjs and
-- mcp-server/test/work-request-joe-answer-migration.test.mjs, and the full
-- end-to-end authority-bound behaviour by ops/joe-answer-work-request-gate.py,
-- which runs against a real disposable PostgreSQL cluster under
-- SET SESSION AUTHORIZATION, exactly as ops/program6-human-triage-gate.py
-- exercises ops.triage_sourced_work_request.
