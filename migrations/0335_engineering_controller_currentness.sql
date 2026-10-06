-- 0335_engineering_controller_currentness.sql
--
-- INC-20260826-02: an Engineering Passport job may receive a lease only while
-- its exact immutable envelope, the server session, and every JSON binding are
-- current.  This is intentionally a forward fix: no issued evidence is
-- rewritten and an expired job remains available for a legitimate successor.

begin;

alter table ops.capability_agent_session
  add column if not exists lease_expires_at timestamptz;

comment on column ops.capability_agent_session.lease_expires_at is
  'Server-issued bounded execution lease for Engineering Passport dispatch. NULL legacy sessions are never executable.';

create or replace function ops.engineering_safe_timestamptz(p_value text)
returns timestamptz
language plpgsql immutable strict
set search_path=pg_catalog
as $$
begin
  return p_value::timestamptz;
exception when others then
  return null;
end $$;

create or replace function ops.capability_agent_session_lease_immutable()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops,public as $$
begin
  if tg_op='UPDATE' and new.lease_expires_at is distinct from old.lease_expires_at then
    raise exception 'capability agent session lease is immutable';
  end if;
  return new;
end $$;

drop trigger if exists capability_agent_session_lease_immutable_before_write on ops.capability_agent_session;
create trigger capability_agent_session_lease_immutable_before_write
  before update on ops.capability_agent_session for each row
  execute function ops.capability_agent_session_lease_immutable();

-- Actor status is revocable authority, not a lease snapshot.  The row-level
-- UPDATE lock conflicts with the controller's FOR SHARE authority lock, so an
-- actor cannot be deactivated or reclassified after binding and before the
-- worker consumes that binding.
create or replace function ops.guard_engineering_actor_authority_update()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
begin
  if (new.active,new.kind,new.slug) is distinct from (old.active,old.kind,old.slug)
     and exists (
       select 1 from ops.capability_agent_session s
       join ops.engineering_execution_envelope e on e.agent_session_id=s.id
       join ops.job j on j.id=e.job_id
       where s.executor_actor_id=old.id
         and j.definition_key='engineering-slice' and j.definition_version=1
         and j.state='running' and j.lease_token is not null
         and j.leased_until>clock_timestamp()
     ) then
    raise exception 'Engineering actor authority is reserved by a live scoped lease';
  end if;
  return new;
end $$;

drop trigger if exists engineering_actor_authority_guard_before_update on public.actor;
create trigger engineering_actor_authority_guard_before_update
  before update on public.actor for each row
  execute function ops.guard_engineering_actor_authority_update();
-- Once an Engineering claim is live, the Work Request fields that define its
-- canonical digest are reserved until the scoped claim succeeds, fails, or
-- expires. This closes the database-binding to adapter-launch race.
create or replace function ops.engineering_work_request_currentness_guard()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
begin
  if (new.ref,new.state,new.version,new.title,new.desired_outcome,new.acceptance_criteria)
       is distinct from
     (old.ref,old.state,old.version,old.title,old.desired_outcome,old.acceptance_criteria)
     and exists (
       select 1 from ops.engineering_execution_envelope e
       join ops.job j on j.id=e.job_id
       where e.work_request_id=old.id
         and j.definition_key='engineering-slice' and j.definition_version=1
         and j.state='running' and j.leased_until>=clock_timestamp()
     ) then
    raise exception 'Work Request currentness is reserved by a live Engineering claim';
  end if;
  return new;
end $$;

drop trigger if exists engineering_work_request_currentness_guard_before_update on ops.work_request;
create trigger engineering_work_request_currentness_guard_before_update
  before update on ops.work_request for each row
  execute function ops.engineering_work_request_currentness_guard();

-- A live Engineering lease reserves the exact definition through adapter
-- launch. Direct definition writes remain possible only after scoped
-- finalization, failure, or lease expiry.
create or replace function ops.guard_engineering_job_definition_currentness_update()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
begin
  if (to_jsonb(new)-'updated_at') is distinct from (to_jsonb(old)-'updated_at')
     and old.key='engineering-slice' and old.version=1
     and exists (
       select 1 from ops.job j
        where j.definition_key=old.key and j.definition_version=old.version
          and j.state='running' and j.lease_token is not null
          and j.leased_until>clock_timestamp()
     ) then
    raise exception 'Engineering job definition currentness is reserved by a live scoped lease';
  end if;
  return new;
end $$;

drop trigger if exists engineering_job_definition_currentness_guard_before_update
  on ops.job_definition;
create trigger engineering_job_definition_currentness_guard_before_update
  before update on ops.job_definition for each row
  execute function ops.guard_engineering_job_definition_currentness_update();

-- This is a read-only, server-derived explanation surface.  It deliberately
-- does not alter a queued job merely because a replacement can still arrive.
create or replace function ops.engineering_envelope_currentness(
  p_envelope_id uuid, p_job_id uuid
) returns jsonb
language sql stable security definer
set search_path=pg_catalog,ops,public
as $$
  with candidate as (
    select e.*, j.id as queue_job_id, j.payload, j.definition_key, j.definition_version, j.mode,
           d.enabled, sp.plan_digest, sp.accepted_plan_hash,
           sp.accepted_plan_id as slice_plan_accepted_plan_id, sp.work_request_id as slice_plan_work_request_id,
           sp.work_request_version as slice_plan_work_request_version, sp.plan as slice_plan,
           s.state as session_state,
           s.lease_expires_at as session_lease_expires_at, s.work_request_id as session_work_request_id,
           s.source_commit_sha as session_source_commit_sha, s.worktree_ref as session_worktree_ref,
           s.scope_ref as session_scope_ref,
           actor.slug as executor_slug, actor.active as executor_active,
           actor.kind as executor_kind, ops.engineering_admission_source(w.ref) as source
      from ops.engineering_execution_envelope e
      join ops.job j on j.id=e.job_id
      join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
      join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
      join ops.work_request w on w.id=e.work_request_id
      join ops.capability_agent_session s on s.id=e.agent_session_id
      join public.actor actor on actor.id=s.executor_actor_id
     where e.id=p_envelope_id and e.job_id=p_job_id
  ), checked as (
    select c.*,
      ops.engineering_safe_timestamptz(c.envelope->>'expires_at') as json_expires_at,
      ops.engineering_safe_timestamptz(c.envelope->>'issued_at') as json_issued_at,
      ops.engineering_safe_timestamptz(c.envelope->'agent_session'->>'lease_expires_at') as json_session_expires_at,
      exists(select 1 from ops.engineering_execution_envelope successor
               where successor.supersedes_envelope_id=c.id) as has_successor,
      exists(select 1 from ops.engineering_slice_receipt receipt
               where receipt.envelope_id=c.id
                 and receipt.outcome='claimed_complete') as has_receipt
    from candidate c
  )
  select jsonb_build_object(
    'eligible',
      enabled and definition_key='engineering-slice' and definition_version=1 and mode='shadow'
      and payload->>'work_request' = source->'work_request'->>'ref'
      and payload->>'slice_ref'=slice_ref and payload->>'plan_digest'=plan_digest
      and jsonb_typeof(payload->'generation')='number' and (payload->>'generation') ~ '^[1-9][0-9]*$'
      and source is not null
      and source->'work_request'->>'id'='wr:'||work_request_id::text
      and (source->'work_request'->>'version') ~ '^[1-9][0-9]*$'
      and (source->'work_request'->>'version')::integer=state_version
      and source->'work_request'->>'canonical_record_digest'=canonical_record_digest
      and source->'accepted_plan'->>'record_id'=accepted_plan_id::text
      and source->'accepted_plan'->>'digest'=accepted_plan_hash
      and slice_plan_accepted_plan_id=accepted_plan_id and slice_plan_accepted_plan_id::text=source->'accepted_plan'->>'record_id'
      and slice_plan_work_request_id=work_request_id and slice_plan_work_request_id::text=regexp_replace(source->'work_request'->>'id','^wr:','')
      and slice_plan_work_request_version=state_version
      and slice_plan->>'plan_digest'=plan_digest
      and slice_plan->'accepted_plan_revision'->>'id'=source->'accepted_plan'->>'plan_ref'
      and slice_plan->'accepted_plan_revision'->>'revision'=source->'accepted_plan'->>'revision'
      and slice_plan->'accepted_plan_revision'->>'digest'=source->'accepted_plan'->>'digest'
      and jsonb_typeof(envelope)='object' and envelope->>'schema_version'='execution-envelope.v1'
      and envelope->>'envelope_id'='env:'||id::text
      and envelope->>'work_request_id'='wr:'||work_request_id::text
      and envelope->'request'->>'job_ref'='job:'||queue_job_id::text
      and envelope->'request'->>'input_digest' is not null
      and envelope->'plan_revision'->>'id'=source->'accepted_plan'->>'plan_ref'
      and envelope->'plan_revision'->>'revision'=source->'accepted_plan'->>'revision'
      and envelope->'plan_revision'->>'digest'=source->'accepted_plan'->>'digest'
      and envelope->'plan_revision'->>'digest'=accepted_plan_hash
      and envelope->'phase_binding'->>'phase_id'='phase:'||slice_ref
      and envelope->'state_binding'->>'state_version'=state_version::text
      and envelope->'state_binding'->>'canonical_record_digest'=canonical_record_digest
      and envelope->'agent_session'->>'id'='session:'||agent_session_id::text
      and json_issued_at is not null and envelope->>'issued_at'=to_char(issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
      and json_issued_at=issued_at
      and json_expires_at is not null and envelope->>'expires_at'=to_char(expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
      and json_expires_at=expires_at and expires_at>issued_at and expires_at<=issued_at+interval '30 minutes' and json_expires_at>statement_timestamp()
      and json_session_expires_at is not null and session_lease_expires_at is not null
      and envelope->'agent_session'->>'lease_expires_at'=to_char(session_lease_expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
      and json_session_expires_at=session_lease_expires_at and json_session_expires_at=expires_at and json_session_expires_at>statement_timestamp()
      and session_work_request_id=work_request_id and session_state in ('claimed','in_progress')
      and session_source_commit_sha=repeat('0',40) and session_worktree_ref='engineering:server-admission'
      and session_scope_ref='slice:'||slice_ref
      and executor_active and executor_kind='automation' and executor_slug='codex'
      and envelope->'server_binding'->'authority'->>'read_only'='false'
      and envelope->'server_binding'->'authority'->>'capability_profile'='capability:engineering-repository-write'
      and envelope->'server_binding'->'adapter'->>'surface'='codex_desktop'
      and envelope#>>'{server_binding,adapter,adapter_id}'='adapter:codex-desktop'
      and envelope->'server_binding'->'identity'->>'agent_principal_id'='agent:codex'
      and envelope->'server_binding'->'identity'->>'runtime_principal'='runtime:codex'
      and envelope->'request'->'allowed_actions'=
        '["repository:create-worktree","repository:create-branch","repository:write-declared-scope","repository:run-checks","repository:commit","repository:push-branch","repository:open-pr"]'::jsonb
      and not has_successor and not has_receipt,
    'dispatch_runway_sufficient',
      expires_at>=statement_timestamp()+interval '960 seconds'
      and session_lease_expires_at>=statement_timestamp()+interval '960 seconds',
    'reason', case
      when id is null then 'envelope_or_job_not_found'
      when has_successor then 'superseded_envelope'
      when has_receipt then 'already_receipted'
      when jsonb_typeof(envelope)<>'object' or envelope->>'schema_version'<>'execution-envelope.v1' then 'malformed_envelope_schema'
      when json_issued_at is null or json_issued_at<>issued_at or json_expires_at is null or json_expires_at<>expires_at or expires_at<=issued_at or expires_at>issued_at+interval '30 minutes' or json_expires_at<=statement_timestamp() then 'envelope_expired_or_mismatched'
      when json_session_expires_at is null or session_lease_expires_at is null or json_session_expires_at<>session_lease_expires_at or json_session_expires_at<=statement_timestamp() then 'agent_session_lease_expired_or_mismatched'
      when session_state not in ('claimed','in_progress') or session_work_request_id<>work_request_id then 'agent_session_not_active'
      when envelope->'server_binding'->'authority'->>'read_only'<>'false' then 'read_only_authority'
      when source is null then 'source_not_current'
      else 'identity_or_currentness_mismatch' end
  ) from checked;
$$;

create or replace function ops.engineering_envelope_is_executable(p_envelope_id uuid,p_job_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,ops,public
as $$ select coalesce((ops.engineering_envelope_currentness(p_envelope_id,p_job_id)->>'eligible')::boolean,false) $$;

-- Only terminalize rows that cannot acquire a successor: an orphan has no
-- envelope at all, and an explicitly superseded predecessor is immutable.
-- Expired/currentness-held jobs are intentionally left queued and explainable
-- through engineering_envelope_currentness while successor admission remains possible.
create or replace function ops.engineering_retire_permanently_ineligible_jobs()
returns integer language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare retired integer;
begin
  with doomed as (
    select j.id,j.attempt,case when e.id is null then 'engineering_orphaned_job' else 'engineering_superseded_predecessor' end reason
      from ops.job j left join ops.engineering_execution_envelope e on e.job_id=j.id
     where j.definition_key='engineering-slice' and j.definition_version=1
       and j.state in ('queued','retry_wait')
       and (e.id is null or exists(select 1 from ops.engineering_execution_envelope successor where successor.supersedes_envelope_id=e.id))
     for update of j skip locked
  ), changed as (
    update ops.job j set state='dead_lettered',ended_at=now(),lease_owner=null,lease_token=null,
      leased_until=null,last_failure_class=d.reason,last_failure_detail='permanently ineligible engineering job',updated_at=now()
      from doomed d where j.id=d.id returning j.id,j.attempt,d.reason
  ), receipts as (
    insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    select id,attempt,'dead_letter','engineering-currentness:'||id::text||':'||attempt::text,
      jsonb_build_object('reason',reason,'derived_by','ops.engineering_retire_permanently_ineligible_jobs') from changed
    returning 1
  ) select count(*) into retired from receipts;
  return retired;
end $$;

create or replace function ops.engineering_claim_slice(
  p_worker text,p_limit integer default 1,p_lease_seconds integer default 960
) returns table(job_id uuid,lease_token uuid,definition_key text,definition_version integer,payload jsonb,execution_kind text,execution_contract jsonb,attempt integer,timeout_seconds integer,mode text)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare v_job_id uuid; v_envelope_id uuid; v_slice_plan_id uuid; v_slice_ref text;
        v_work_request_id uuid; v_executor_actor_id uuid;
        v_currentness jsonb; v_claim_at timestamptz; v_runway_sufficient boolean;
begin
  if btrim(coalesce(p_worker,''))='' or p_limit is distinct from 1 then raise exception 'worker and exactly one claim are required'; end if;
  if p_lease_seconds is distinct from 960 then raise exception 'engineering controller lease must be 960 seconds'; end if;
  -- Identifier lookup is deliberately unlocked.  Every authority predicate is
  -- re-read only after the global session-first lock order below.
  select j.id,e.id,e.slice_plan_id,e.slice_ref,e.work_request_id
    into v_job_id,v_envelope_id,v_slice_plan_id,v_slice_ref,v_work_request_id
    from ops.job j
    join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
    join ops.engineering_execution_envelope e on e.job_id=j.id
   where d.enabled and j.definition_key='engineering-slice' and j.definition_version=1
     and j.state in ('queued','retry_wait') and j.next_attempt_at<=now()
     and j.attempt<j.max_attempts
     and ops.engineering_envelope_is_executable(e.id,j.id)
     and coalesce((ops.engineering_envelope_currentness(e.id,j.id)
                    ->>'dispatch_runway_sufficient')::boolean,false)
   order by j.scheduled_for,j.created_at limit 1;
  if not found then return; end if;
  select s.executor_actor_id into v_executor_actor_id
    from ops.capability_agent_session s
    join ops.engineering_execution_envelope e on e.agent_session_id=s.id
   where e.id=v_envelope_id and e.job_id=v_job_id for share of s;
  if not found then return; end if;
  perform 1 from public.actor a
   where a.id=v_executor_actor_id and a.active and a.kind='automation' and a.slug='codex'
   order by a.id for share;
  if not found then return; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:'||v_slice_plan_id::text||':'||v_slice_ref,0));
  perform 1 from ops.engineering_execution_envelope e
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
   where e.id=v_envelope_id and e.job_id=v_job_id
   for key share of e,sp;
  if not found then return; end if;
  perform 1 from ops.work_request where id=v_work_request_id for share;
  if not found then return; end if;
  perform 1 from ops.job_definition d
   where d.key='engineering-slice' and d.version=1 for share;
  if not found then return; end if;
  perform 1 from ops.job j
   where j.id=v_job_id for update;
  if not found then return; end if;
  v_claim_at:=clock_timestamp();
  perform 1 from ops.job j
    join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
   where j.id=v_job_id and d.enabled and j.definition_key='engineering-slice'
     and j.definition_version=1 and j.state in ('queued','retry_wait')
     and j.next_attempt_at<=v_claim_at and j.attempt<j.max_attempts;
  if not found then return; end if;
  v_currentness:=ops.engineering_envelope_currentness(v_envelope_id,v_job_id);
  select e.expires_at>=v_claim_at+make_interval(secs=>p_lease_seconds)
         and s.lease_expires_at is not null
         and s.lease_expires_at>=v_claim_at+make_interval(secs=>p_lease_seconds)
    into v_runway_sufficient
    from ops.engineering_execution_envelope e
    join ops.capability_agent_session s on s.id=e.agent_session_id
   where e.id=v_envelope_id and e.job_id=v_job_id;
  if coalesce((v_currentness->>'eligible')::boolean,false) is not true
     or coalesce(v_runway_sufficient,false) is not true then return; end if;
  return query with claimed as (
    update ops.job j set state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),leased_until=v_claim_at+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,v_claim_at),updated_at=v_claim_at
    where j.id=v_job_id and j.state in ('queued','retry_wait')
    returning j.*
  ), attempts as (
    insert into ops.job_attempt as claimed_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c returning claimed_attempt.job_id
  ) select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode from claimed c join ops.job_definition d on d.key=c.definition_key and d.version=c.definition_version join attempts a on a.job_id=c.id;
end $$;

drop function if exists ops.engineering_controller_binding(uuid,uuid);

create or replace function ops.engineering_controller_binding(
  p_envelope_id uuid,p_job_id uuid,p_lease_token uuid
)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare binding jsonb; lineage_plan uuid; lineage_slice text; lineage_work_request uuid;
        v_executor_actor_id uuid; v_binding_at timestamptz;
begin
  if p_lease_token is null then return null; end if;
  -- Unlocked identifiers first; all mutable authority is re-read after locks.
  select e.slice_plan_id,e.slice_ref,e.work_request_id,e.agent_session_id
    into lineage_plan,lineage_slice,lineage_work_request,v_executor_actor_id
    from ops.engineering_execution_envelope e
   where e.id=p_envelope_id and e.job_id=p_job_id;
  if not found then return null; end if;
  select s.executor_actor_id into v_executor_actor_id
    from ops.capability_agent_session s
    join ops.engineering_execution_envelope e on e.agent_session_id=s.id
   where e.id=p_envelope_id and e.job_id=p_job_id for share of s;
  if not found then return null; end if;
  perform 1 from public.actor a
   where a.id=v_executor_actor_id and a.active and a.kind='automation' and a.slug='codex'
   order by a.id for share;
  if not found then return null; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:'||lineage_plan::text||':'||lineage_slice,0));
  perform 1 from ops.engineering_execution_envelope e
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
   where e.id=p_envelope_id and e.job_id=p_job_id for key share of e,sp;
  if not found then return null; end if;
  perform 1 from ops.work_request where id=lineage_work_request for share;
  if not found then return null; end if;
  perform 1 from ops.job_definition d
   where d.key='engineering-slice' and d.version=1 and d.enabled for share;
  if not found then return null; end if;
  perform 1 from ops.job where id=p_job_id for share;
  if not found then return null; end if;
  v_binding_at:=clock_timestamp();
  if not ops.engineering_envelope_is_executable(p_envelope_id,p_job_id) then return null; end if;
  select jsonb_build_object(
    'envelope_id',e.id::text,'envelope_digest',e.envelope_digest,
    'slice_ref',e.slice_ref,'plan_digest',sp.plan_digest,'slice_plan',sp.plan,
    'executor_actor',jsonb_build_object('id',a.id::text,'slug',a.slug),
    'agent_session_id',s.id::text,
    'agent_session_lease_expires_at',to_char(s.lease_expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'job_lease_expires_at',to_char(j.leased_until at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
  ) into binding
    from ops.engineering_execution_envelope e
    join ops.job j on j.id=e.job_id
    join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
    join ops.capability_agent_session s on s.id=e.agent_session_id
    join public.actor a on a.id=s.executor_actor_id
   where e.id=p_envelope_id and e.job_id=p_job_id
     and a.active and a.kind='automation' and a.slug='codex'
     and d.enabled and d.key='engineering-slice' and d.version=1
     and j.state='running' and j.lease_token=p_lease_token
     and j.leased_until>=v_binding_at+interval '930 seconds';
  return binding;
end $$;

-- These are intentionally private predicate helpers for the SECURITY DEFINER
-- receipt seam below.  Each substitutes an empty JSON container before
-- iterating so malformed caller JSON is a false predicate, never a set-return
-- type error that could bypass a fail-closed branch.
create or replace function ops.engineering_receipt_exact_object(p_value jsonb,p_keys text[])
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='object'
     and (select array_agg(key order by key)
            from jsonb_object_keys(case when jsonb_typeof(p_value)='object' then p_value else '{}'::jsonb end) as keys(key))
         is not distinct from
         (select array_agg(key order by key) from unnest(p_keys) as keys(key));
$$;

create or replace function ops.engineering_receipt_identifier_array(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='array'
     and not exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_value)='array' then p_value else '[]'::jsonb end) value
        where jsonb_typeof(value)<>'string'
           or not coalesce((value#>>'{}') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     )
     and (select count(*)=count(distinct (value#>>'{}'))
            from jsonb_array_elements(case when jsonb_typeof(p_value)='array'
                                           then p_value else '[]'::jsonb end) value);
$$;

create or replace function ops.engineering_receipt_identifier_sets_equal(p_left jsonb,p_right jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select coalesce(ops.engineering_receipt_identifier_array(p_left),false)
     and coalesce(ops.engineering_receipt_identifier_array(p_right),false)
     and not exists (
       (select value#>>'{}'
         from jsonb_array_elements(case when jsonb_typeof(p_left)='array' then p_left else '[]'::jsonb end) value)
       except
       (select value#>>'{}'
         from jsonb_array_elements(case when jsonb_typeof(p_right)='array' then p_right else '[]'::jsonb end) value)
     )
     and not exists (
       (select value#>>'{}'
         from jsonb_array_elements(case when jsonb_typeof(p_right)='array' then p_right else '[]'::jsonb end) value)
       except
       (select value#>>'{}'
         from jsonb_array_elements(case when jsonb_typeof(p_left)='array' then p_left else '[]'::jsonb end) value)
     );
$$;

create or replace function ops.engineering_receipt_evidence_array(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='array'
     and not exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_value)='array' then p_value else '[]'::jsonb end) evidence
        where not ops.engineering_receipt_exact_object(
                evidence,array['content_digest','redaction_class','ref'])
           or not coalesce((evidence->>'ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or not coalesce(evidence->>'redaction_class'=any(array['metadata_only','redacted_evidence']),false)
           or not coalesce((evidence->>'content_digest') ~ '^sha256:[0-9a-f]{64}$',false)
     );
$$;

create or replace function ops.engineering_record_slice_receipt(p_envelope_id uuid,p_lease_token uuid,p_receipt jsonb,p_receipt_digest text,p_executor_actor_id uuid)
returns ops.engineering_slice_receipt language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype; s ops.capability_agent_session%rowtype;
        j ops.job%rowtype; a ops.job_attempt%rowtype; v_checked_at timestamptz; v_append_at timestamptz;
        session_executor uuid; session_slug text; receipt_plan_digest text; receipt_plan jsonb; receipt_slice jsonb;
        receipt_outcome text; slice_count integer;
        row ops.engineering_slice_receipt%rowtype;
begin
  -- One atomic seam: lineage, session, job, receipt append, and terminal job
  -- transition all succeed together or roll back together.
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  if p_lease_token is null then raise exception 'engineering claim or lease is not current'; end if;
  if jsonb_typeof(p_receipt) is distinct from 'object'
     or p_receipt->>'schema_version' is distinct from 'engineering-slice-receipt.v1'
     or p_receipt_digest is null or p_receipt_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_receipt_digest is distinct from
        ('sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_receipt),'sha256'),'hex'))
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt,array[
       'actual_component_refs','actual_resource_refs','artifact_refs','attribution','attempt_id','checks',
       'deviations','envelope_digest','evidence_refs','executor_claim','independent_verification_required',
       'outcome','plan_digest','planned_component_refs','planned_resource_refs','reset_reconstruction',
       'schema_version','slice_ref','source_evidence'
     ]),false) then
    raise exception 'engineering receipt is malformed';
  end if;
  -- The identifier lookup above is intentionally unlocked.  From here through
  -- append we retain the global session -> actor -> lineage lock order.
  select * into s from ops.capability_agent_session where id=e.agent_session_id for update;
  if not found then raise exception 'engineering agent session is not current'; end if;
  session_executor := s.executor_actor_id;
  select actor.slug into session_slug
    from public.actor actor
   where actor.id=s.executor_actor_id and actor.active and actor.kind='automation' and actor.slug='codex'
   order by actor.id for share;
  if not found then raise exception 'engineering executor actor is not current'; end if;
  perform pg_advisory_xact_lock(hashtextextended('engineering-envelope:' || e.slice_plan_id::text || ':' || e.slice_ref,0));
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id for key share;
  if not found or e.agent_session_id is distinct from s.id then
    raise exception 'engineering envelope or agent session binding changed';
  end if;
  select plan_digest,plan into receipt_plan_digest,receipt_plan
    from ops.engineering_slice_plan where id=e.slice_plan_id for key share;
  if not found then raise exception 'engineering receipt slice plan is not current'; end if;
  perform 1 from ops.work_request where id=e.work_request_id for share;
  if not found then raise exception 'engineering Work Request is not current'; end if;
  select * into j from ops.job where id=e.job_id for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  select * into a from ops.job_attempt
   where job_id=j.id and attempt=j.attempt and lease_token is not distinct from p_lease_token
     and state is not distinct from 'running'
   for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  -- statement_timestamp() is fixed at function entry and can be stale after a
  -- session/actor/lineage/job lock wait.  Sample only after all ordered locks.
  v_checked_at := clock_timestamp();
  if j.state is distinct from 'running' or j.lease_token is distinct from p_lease_token
     or j.leased_until is null or j.leased_until<v_checked_at
     or e.expires_at<=v_checked_at or s.lease_expires_at is null or s.lease_expires_at<=v_checked_at
     or (s.state is distinct from 'claimed' and s.state is distinct from 'in_progress') then
    raise exception 'engineering claim, envelope, or agent-session lease is not current';
  end if;
  if not ops.engineering_envelope_is_executable(e.id,e.job_id) then raise exception 'engineering envelope is no longer executable'; end if;
  if session_executor is null or p_executor_actor_id is distinct from session_executor then raise exception 'engineering receipt executor is not the server-bound agent session'; end if;
  if not found or p_receipt->>'plan_digest' is distinct from receipt_plan_digest
     or p_receipt->>'envelope_digest' is distinct from e.envelope_digest
     or p_receipt->>'slice_ref' is distinct from e.slice_ref
     or p_receipt->>'attempt_id' is distinct from ('attempt:'||a.attempt) then
    raise exception 'engineering receipt is not bound to the claimed envelope';
  end if;
  -- The immutable slice-plan projection is the only declaration of what the
  -- direct receipt seam may claim.  Re-validate the narrow plan surface here
  -- rather than trusting a caller-side Passport validator.
  if not coalesce(ops.engineering_receipt_exact_object(receipt_plan,array[
       'accepted_plan_revision','plan_digest','schema_version','slices','work_request'
     ]),false)
     or receipt_plan->>'schema_version' is distinct from 'engineering-slice-plan.v1'
     or receipt_plan->>'plan_digest' is distinct from receipt_plan_digest
     or not coalesce(ops.engineering_receipt_exact_object(receipt_plan->'work_request',array[
       'canonical_record_digest','id','state_version'
     ]),false)
     or receipt_plan->'work_request' is distinct from jsonb_build_object(
       'id','wr:'||e.work_request_id::text,
       'state_version',e.state_version,
       'canonical_record_digest',e.canonical_record_digest)
     or not coalesce(ops.engineering_receipt_exact_object(receipt_plan->'accepted_plan_revision',array[
       'digest','id','revision'
     ]),false)
     or receipt_plan->'accepted_plan_revision' is distinct from e.envelope->'plan_revision'
     or jsonb_typeof(receipt_plan->'slices') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt_plan->'slices')='array'
                          then jsonb_array_length(receipt_plan->'slices')>0 else false end,false) then
    raise exception 'engineering receipt slice plan is malformed or not bound to the envelope';
  end if;
  select count(*) into slice_count
    from jsonb_array_elements(case when jsonb_typeof(receipt_plan->'slices')='array'
                                   then receipt_plan->'slices' else '[]'::jsonb end) candidate
   where candidate->>'slice_ref'=e.slice_ref;
  if slice_count<>1 then
    raise exception 'engineering receipt slice plan does not name exactly one bound slice';
  end if;
  select candidate into receipt_slice
    from jsonb_array_elements(receipt_plan->'slices') candidate
   where candidate->>'slice_ref'=e.slice_ref;
  if not coalesce(ops.engineering_receipt_exact_object(receipt_slice,array[
       'baseline_evidence_refs','concurrency_posture','declared_component_refs','declared_plan_step_refs',
       'declared_resource_refs','definition_of_done','dependency_refs','forbidden_change_refs','manual_qa_required',
       'objective','ordinal','planned_checks','release_requirement','risk_class','scope_boundary','slice_ref'
     ]),false)
     or receipt_slice->>'slice_ref' is distinct from e.slice_ref
     or jsonb_typeof(receipt_slice->'ordinal') is distinct from 'number'
     or not coalesce((receipt_slice->>'ordinal') ~ '^[1-9][0-9]*$',false)
     or exists (select 1 from unnest(array['objective','definition_of_done','scope_boundary']) field
                 where jsonb_typeof(receipt_slice->field) is distinct from 'string'
                    or not coalesce(btrim(receipt_slice->>field)<>'',false))
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'dependency_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'declared_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'declared_plan_step_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'forbidden_change_refs'),false)
     or not coalesce(ops.engineering_receipt_evidence_array(receipt_slice->'baseline_evidence_refs'),false)
     or jsonb_typeof(receipt_slice->'planned_checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt_slice->'planned_checks')='array'
                          then jsonb_array_length(receipt_slice->'planned_checks')>0 else false end,false)
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(receipt_slice->'planned_checks')='array'
                                        then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where not coalesce(ops.engineering_receipt_exact_object(planned_check,array[
                'check_ref','evidence_requirement','failure_condition'
              ]),false)
           or not coalesce((planned_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or jsonb_typeof(planned_check->'failure_condition') is distinct from 'string'
           or not coalesce(btrim(planned_check->>'failure_condition')<>'',false)
           or not coalesce(planned_check->>'evidence_requirement'=any(array['redacted_evidence_required','metadata_only_sufficient']),false)
     )
     or exists (
       select 1 from jsonb_array_elements(receipt_slice->'planned_checks') planned_check
       group by planned_check->>'check_ref' having count(*)>1
     )
     or not coalesce(receipt_slice->>'concurrency_posture'=any(array['parallel_safe','serial_after_dependencies','exclusive_resource']),false)
     or jsonb_typeof(receipt_slice->'manual_qa_required') is distinct from 'boolean'
     or not coalesce(receipt_slice->>'risk_class'=any(array['R0','R1','R2','R3','R4','R5','R6']),false)
     or not coalesce(receipt_slice->>'release_requirement'=any(array['required','not_required']),false) then
    raise exception 'engineering receipt bound slice plan is not fully typed';
  end if;
  receipt_outcome := p_receipt->>'outcome';
  if receipt_outcome is null or not coalesce(receipt_outcome=any(array['claimed_complete','failed','blocked','reopened']),false) then
    raise exception 'engineering receipt outcome is invalid';
  end if;
  if not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'planned_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'actual_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'planned_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'actual_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'artifact_refs'),false)
     or not coalesce(ops.engineering_receipt_evidence_array(p_receipt->'evidence_refs'),false)
     or jsonb_typeof(p_receipt->'checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(p_receipt->'checks')='array'
                          then jsonb_array_length(p_receipt->'checks')>0 else false end,false)
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_receipt->'checks')='array'
                                        then p_receipt->'checks' else '[]'::jsonb end) receipt_check
        where not coalesce(ops.engineering_receipt_exact_object(receipt_check,array[
                'check_ref','evidence_refs','state'
              ]),false)
           or not coalesce((receipt_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or not coalesce(receipt_check->>'state'=any(array['passed','failed','blocked','not_run']),false)
           or not coalesce(ops.engineering_receipt_evidence_array(receipt_check->'evidence_refs'),false)
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
       group by receipt_check->>'check_ref' having count(*)>1
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
        where not exists (
          select 1 from jsonb_array_elements(receipt_slice->'planned_checks') planned_check
           where planned_check->>'check_ref'=receipt_check->>'check_ref'
        )
     )
     or exists (
       select 1 from jsonb_array_elements(receipt_slice->'planned_checks') planned_check
        where not exists (
          select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
           where receipt_check->>'check_ref'=planned_check->>'check_ref'
        )
     )
     or exists (
       select 1
         from jsonb_array_elements(p_receipt->'checks') receipt_check
         join jsonb_array_elements(receipt_slice->'planned_checks') planned_check
           on planned_check->>'check_ref'=receipt_check->>'check_ref'
        where receipt_check->>'state'='passed'
          and (jsonb_array_length(receipt_check->'evidence_refs')=0
               or not exists (
                 select 1 from jsonb_array_elements(receipt_check->'evidence_refs') evidence
                  where evidence->>'redaction_class'=case planned_check->>'evidence_requirement'
                    when 'redacted_evidence_required' then 'redacted_evidence' else 'metadata_only' end
               ))
     )
     or jsonb_typeof(p_receipt->'deviations') is distinct from 'array'
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_receipt->'deviations')='array'
                                        then p_receipt->'deviations' else '[]'::jsonb end) deviation
        where not coalesce(ops.engineering_receipt_exact_object(deviation,array[
                'category','deviation_ref','evidence_refs','impact','out_of_scope_component_refs',
                'out_of_scope_resource_refs','plan_revision_required','reason','review_state'
              ]),false)
           or not coalesce((deviation->>'deviation_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or exists (select 1 from unnest(array['category','reason','impact']) field
                       where jsonb_typeof(deviation->field) is distinct from 'string'
                          or not coalesce(btrim(deviation->>field)<>'',false))
           or jsonb_typeof(deviation->'plan_revision_required') is distinct from 'boolean'
           or not coalesce(ops.engineering_receipt_evidence_array(deviation->'evidence_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_resource_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_component_refs'),false)
           or not coalesce(deviation->>'review_state'=any(array['unreviewed','reviewed','resolved']),false)
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'deviations') deviation
       group by deviation->>'deviation_ref' having count(*)>1
     )
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          p_receipt->'planned_resource_refs',receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          p_receipt->'planned_component_refs',receipt_slice->'declared_component_refs'),false)
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'actual_resource_refs') actual_ref
        where not exists (
          select 1 from jsonb_array_elements(receipt_slice->'declared_resource_refs') declared_ref
           where declared_ref=actual_ref
        ) and not exists (
          select 1 from jsonb_array_elements(p_receipt->'deviations') deviation,
                        jsonb_array_elements(deviation->'out_of_scope_resource_refs') approved_ref
           where deviation->>'review_state'='resolved' and approved_ref=actual_ref
        )
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'actual_component_refs') actual_ref
        where not exists (
          select 1 from jsonb_array_elements(receipt_slice->'declared_component_refs') declared_ref
           where declared_ref=actual_ref
        ) and not exists (
          select 1 from jsonb_array_elements(p_receipt->'deviations') deviation,
                        jsonb_array_elements(deviation->'out_of_scope_component_refs') approved_ref
           where deviation->>'review_state'='resolved' and approved_ref=actual_ref
        )
     )
     or (receipt_outcome='claimed_complete' and (
       jsonb_array_length(p_receipt->'artifact_refs')=0
       or jsonb_array_length(p_receipt->'evidence_refs')=0
       or exists (select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
                   where receipt_check->>'state' is distinct from 'passed')
     ))
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'source_evidence',array[
          'branch_ref','evidence_refs','source_sha','worktree_ref'
        ]),false)
     or exists (select 1 from unnest(array['worktree_ref','branch_ref','source_sha']) field
                 where jsonb_typeof(p_receipt->'source_evidence'->field) is distinct from 'string'
                    or not coalesce(btrim(p_receipt->'source_evidence'->>field)<>'',false)
                    or (field<>'source_sha' and not coalesce(
                         (p_receipt->'source_evidence'->>field) ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)))
     or not coalesce(ops.engineering_receipt_evidence_array(p_receipt->'source_evidence'->'evidence_refs'),false)
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'reset_reconstruction',array[
          'fresh_session','inherited_transcript_used','reconstruction_free','remediation_action'
        ]),false)
     or p_receipt->'reset_reconstruction'->'fresh_session' is distinct from 'true'::jsonb
     or p_receipt->'reset_reconstruction'->'inherited_transcript_used' is distinct from 'false'::jsonb
     or jsonb_typeof(p_receipt->'reset_reconstruction'->'reconstruction_free') is distinct from 'boolean'
     or (p_receipt->'reset_reconstruction'->'reconstruction_free'='false'::jsonb and
         (jsonb_typeof(p_receipt->'reset_reconstruction'->'remediation_action') is distinct from 'string'
          or not coalesce(btrim(p_receipt->'reset_reconstruction'->>'remediation_action')<>'',false)))
     or (p_receipt->'reset_reconstruction'->'reconstruction_free'='true'::jsonb and
         p_receipt->'reset_reconstruction'->'remediation_action' is distinct from 'null'::jsonb
         and (jsonb_typeof(p_receipt->'reset_reconstruction'->'remediation_action') is distinct from 'string'
              or not coalesce(btrim(p_receipt->'reset_reconstruction'->>'remediation_action')<>'',false)))
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'executor_claim',array[
          'claim_state','claimed_at','claimed_by'
        ]),false)
     or p_receipt->'executor_claim'->>'claim_state' is distinct from 'executor_claim'
     or not coalesce((p_receipt->'executor_claim'->>'claimed_by') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     or jsonb_typeof(p_receipt->'executor_claim'->'claimed_at') is distinct from 'string'
     or not coalesce(btrim(p_receipt->'executor_claim'->>'claimed_at')<>'',false)
     or p_receipt->'independent_verification_required' is distinct from 'true'::jsonb
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'attribution',array[
          'actor_ref','adapter_ref','session_ref'
        ]),false)
     or exists (select 1 from unnest(array['actor_ref','session_ref','adapter_ref']) field
                 where not coalesce((p_receipt->'attribution'->>field) ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false))
     or session_slug is null or p_receipt->'executor_claim'->>'claimed_by' is distinct from session_slug
     or e.envelope#>>'{server_binding,identity,agent_principal_id}' is null
     or e.envelope#>>'{agent_session,id}' is null
     or e.envelope#>>'{server_binding,adapter,adapter_id}' is null
     or p_receipt->'attribution'->>'actor_ref' is distinct from e.envelope#>>'{server_binding,identity,agent_principal_id}'
     or p_receipt->'attribution'->>'session_ref' is distinct from e.envelope#>>'{agent_session,id}'
     or p_receipt->'attribution'->>'adapter_ref' is distinct from e.envelope#>>'{server_binding,adapter,adapter_id}' then
    raise exception 'engineering receipt typed contract is invalid';
  end if;
  -- No receipt may cross the append boundary on authority sampled only before
  -- JSON validation.  All rows remain locked from the first ordered check.
  v_append_at := clock_timestamp();
  if j.state is distinct from 'running' or j.lease_token is distinct from p_lease_token
     or j.leased_until is null or j.leased_until<v_append_at
     or e.expires_at<=v_append_at or s.lease_expires_at is null or s.lease_expires_at<=v_append_at
     or (s.state is distinct from 'claimed' and s.state is distinct from 'in_progress') then
    raise exception 'engineering claim, envelope, or agent-session lease is not current at receipt append';
  end if;
  if not ops.engineering_envelope_is_executable(e.id,e.job_id) then
    raise exception 'engineering envelope is no longer executable at receipt append';
  end if;
  insert into ops.engineering_slice_receipt(job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,executor_actor_id,receipt_digest,outcome,receipt) values(a.id,e.id,e.work_request_id,e.slice_ref,p_receipt->>'attempt_id',session_executor,p_receipt_digest,p_receipt->>'outcome',p_receipt) returning * into row;
  return row;
end $$;

-- The controller may not append immutable evidence in one transaction and
-- transition the queue lease in another.  This is the sole typed runtime
-- door: a refused transition rolls back the receipt insert as well.
create or replace function ops.engineering_finalize_slice_receipt(
  p_envelope_id uuid,p_lease_token uuid,p_receipt jsonb,
  p_receipt_digest text,p_executor_actor_id uuid
) returns ops.engineering_slice_receipt
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype;
        row ops.engineering_slice_receipt%rowtype;
        j ops.job%rowtype;
        terminal_state text;
        transitioned integer;
        v_transition_at timestamptz;
begin
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  select * into row from ops.engineering_record_slice_receipt(
    p_envelope_id,p_lease_token,p_receipt,p_receipt_digest,p_executor_actor_id);
  select * into j from ops.job where id=e.job_id for update;
  v_transition_at:=clock_timestamp();
  if not found or j.definition_key<>'engineering-slice' or j.state<>'running'
     or j.lease_token<>p_lease_token or j.leased_until<=v_transition_at then
    raise exception 'engineering job does not hold this live scoped lease';
  end if;
  if row.outcome='claimed_complete' then
    update ops.job_attempt set state='succeeded',ended_at=v_transition_at
     where id=row.job_attempt_id and job_id=j.id and attempt=j.attempt
       and lease_token=p_lease_token and state='running';
    get diagnostics transitioned=row_count;
    if transitioned<>1 then raise exception 'engineering completion attempt is not current'; end if;
    update ops.job set state='succeeded',ended_at=v_transition_at,
           lease_owner=null,lease_token=null,leased_until=null,updated_at=v_transition_at
     where id=j.id and state='running' and lease_token=p_lease_token;
    get diagnostics transitioned=row_count;
    if transitioned<>1 then raise exception 'engineering completion did not transition the claimed job'; end if;
    insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,'completion','engineering:'||row.id::text,
           jsonb_build_object('engineering_receipt_id',row.id,'receipt_digest',p_receipt_digest));
    update ops.capability_agent_session
       set state='cancelled',cancelled_at=v_transition_at,version=version+1
     where id=e.agent_session_id and work_request_id=e.work_request_id
       and state in ('claimed','in_progress');
    get diagnostics transitioned=row_count;
    if transitioned<>1 then raise exception 'engineering agent session could not be atomically retired'; end if;
  else
    terminal_state:=case when j.attempt<j.max_attempts then 'retry_wait' else 'dead_lettered' end;
    update ops.job_attempt set state='failed',ended_at=v_transition_at,
           failure_class='engineering_'||row.outcome,
           detail='typed engineering receipt reported non-complete outcome'
     where id=row.job_attempt_id and job_id=j.id and attempt=j.attempt
       and lease_token=p_lease_token and state='running';
    get diagnostics transitioned=row_count;
    if transitioned<>1 then raise exception 'engineering failure attempt is not current'; end if;
    update ops.job set state=terminal_state,
           next_attempt_at=case when terminal_state='retry_wait'
             then v_transition_at+make_interval(secs=>ops.retry_delay_seconds(j)) else next_attempt_at end,
           ended_at=case when terminal_state='dead_lettered' then v_transition_at else null end,
           last_failure_class='engineering_'||row.outcome,
           last_failure_detail='typed engineering receipt reported non-complete outcome',
           lease_owner=null,lease_token=null,leased_until=null,updated_at=v_transition_at
     where id=j.id and state='running' and lease_token=p_lease_token;
    get diagnostics transitioned=row_count;
    if transitioned<>1 then raise exception 'engineering failure did not transition the claimed job'; end if;
    insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,case when terminal_state='dead_lettered' then 'dead_letter' else 'failure' end,
           concat('engineering-failure:',j.id,':',j.attempt),
           jsonb_build_object('failure_class','engineering_'||row.outcome,
             'detail','typed engineering receipt reported non-complete outcome','next_state',terminal_state));
  end if;
  return row;
end $$;

-- Adapter/controller failures have no valid typed slice receipt.  They still
-- require a scoped, lease-bound retry/dead-letter door; generic fail_job is
-- fenced from Engineering rows below.
create or replace function ops.engineering_fail_claim(
  p_job_id uuid,p_lease_token uuid,p_failure_class text,p_detail text
) returns text
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare j ops.job%rowtype; next_state text; transitioned integer; v_now timestamptz;
begin
  select * into j from ops.job where id=p_job_id for update;
  v_now:=clock_timestamp();
  if not found or j.definition_key<>'engineering-slice' or j.state<>'running'
     or j.lease_token<>p_lease_token or j.leased_until<=v_now then
    raise exception 'engineering job does not hold this live scoped lease';
  end if;
  next_state:=case when j.attempt<j.max_attempts then 'retry_wait' else 'dead_lettered' end;
  update ops.job_attempt set state='failed',ended_at=v_now,
         failure_class=p_failure_class,detail=left(coalesce(p_detail,''),1000)
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token and state='running';
  get diagnostics transitioned=row_count;
  if transitioned<>1 then raise exception 'engineering failure attempt is not current'; end if;
  update ops.job set state=next_state,
         next_attempt_at=case when next_state='retry_wait'
           then v_now+make_interval(secs=>ops.retry_delay_seconds(j)) else next_attempt_at end,
         ended_at=case when next_state='dead_lettered' then v_now else null end,
         last_failure_class=p_failure_class,last_failure_detail=left(coalesce(p_detail,''),1000),
         lease_owner=null,lease_token=null,leased_until=null,updated_at=v_now
   where id=j.id and state='running' and lease_token=p_lease_token;
  get diagnostics transitioned=row_count;
  if transitioned<>1 then raise exception 'engineering failure did not transition the claimed job'; end if;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
  values(j.id,j.attempt,case when next_state='dead_lettered' then 'dead_letter' else 'failure' end,
         concat('engineering-failure:',j.id,':',j.attempt),
         jsonb_build_object('failure_class',p_failure_class,
           'detail',left(coalesce(p_detail,''),1000),'next_state',next_state));
  return next_state;
end $$;

-- The Passport projection serializes this database-owned reviewer stamp, so
-- the column and its constrained vocabulary must exist before function parse.
alter table ops.engineering_reviewer_fact
  add column if not exists contract_version text;
alter table ops.engineering_reviewer_fact
  drop constraint if exists engineering_reviewer_fact_contract_version_check;
alter table ops.engineering_reviewer_fact
  add constraint engineering_reviewer_fact_contract_version_check
  check (contract_version is null or contract_version='engineering-review.v1');
-- The Passport read keeps reviewer ledger wrappers internal while exposing
-- the persisted actor status/slug needed to reject malformed historical
-- review rows during closure projection.  The public engineering-passport.v1
-- output remains the flattened fact payload built by the runtime.
create or replace function ops.engineering_passport_facts(p_work_request text)
returns jsonb language sql stable security definer
set search_path=pg_catalog,ops,public
as $$
  select jsonb_build_object(
    'source',ops.engineering_admission_source(p_work_request),
    'slice_plans',coalesce((
      select jsonb_agg(to_jsonb(sp) order by sp.created_at)
        from ops.engineering_slice_plan sp
        join ops.work_request w on w.id=sp.work_request_id
       where w.ref=p_work_request
    ),'[]'::jsonb),
    'envelopes',coalesce((
      select jsonb_agg(to_jsonb(e) order by e.created_at)
        from ops.engineering_execution_envelope e
        join ops.work_request w on w.id=e.work_request_id
       where w.ref=p_work_request
    ),'[]'::jsonb),
    'receipts',coalesce((
      select jsonb_agg(
               to_jsonb(r)||jsonb_build_object(
                 'executor_actor_active',executor.active,
                 'executor_actor_slug',executor.slug
               ) order by r.created_at
             )
        from ops.engineering_slice_receipt r
        join ops.work_request w on w.id=r.work_request_id
        join public.actor executor on executor.id=r.executor_actor_id
       where w.ref=p_work_request
    ),'[]'::jsonb),
    'reviewer_facts',coalesce((
      select jsonb_agg(
               to_jsonb(f)||jsonb_build_object(
                 'contract_version',f.contract_version,
                 'reviewer_actor_active',reviewer.active,
                 'reviewer_actor_slug',reviewer.slug
               ) order by f.created_at
             )
        from ops.engineering_reviewer_fact f
        join ops.work_request w on w.id=f.work_request_id
        join public.actor reviewer on reviewer.id=f.reviewer_actor_id
       where w.ref=p_work_request
    ),'[]'::jsonb)
  );
$$;

-- Reviewer evidence is an authority-bearing dependency fact.  carr_writer
-- Reviewer evidence is an authority-bearing dependency fact.  carr_writer
-- retains the narrow INSERT surface established by 0310, but the database
-- independently binds every append to the exact immutable receipt rather
-- than trusting the MCP validator or a job-local attempt label.
-- Reviewer facts are stamped only by this trigger. Historical rows without
-- this database-owned version are insufficient for SIEP replay authority.

create or replace function ops.guard_engineering_reviewer_fact_insert()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare r ops.engineering_slice_receipt%rowtype;
        e ops.engineering_execution_envelope%rowtype;
        sp ops.engineering_slice_plan%rowtype;
        executor_session ops.capability_agent_session%rowtype;
        executor_slug text; reviewer_slug text; receipt_deviation_refs jsonb;
        locked_actor_count integer;
begin
  if new.contract_version is not null then
    raise exception 'engineering reviewer contract version is caller-controlled';
  end if;
  if not coalesce(ops.engineering_receipt_exact_object(new.fact,array[
       'attempt_id','evidence_refs','is_independent','resolved_deviation_refs',
       'reviewed_deviation_refs','reviewer_ref','session_ref','slice_ref','state'
     ]),false)
     or not coalesce(new.fact->>'state'=any(array['passed','failed','blocked']),false)
     or new.state is distinct from new.fact->>'state'
     or jsonb_typeof(new.fact->'is_independent') is distinct from 'boolean'
     or new.fact->'is_independent' is distinct from 'true'::jsonb
     or not coalesce((new.fact->>'attempt_id') ~ '^attempt:[1-9][0-9]*$',false)
     or not coalesce((new.fact->>'slice_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     or not coalesce((new.fact->>'reviewer_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     or not coalesce((new.fact->>'session_ref') ~ '^session:[A-Za-z0-9][A-Za-z0-9._:-]{1,119}$',false)
     or not coalesce(ops.engineering_receipt_evidence_array(new.fact->'evidence_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(new.fact->'reviewed_deviation_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(new.fact->'resolved_deviation_refs'),false) then
    raise exception 'engineering reviewer fact is malformed';
  end if;
  -- Derive identifiers unlocked, then retain session -> actor -> lineage order.
  select * into r from ops.engineering_slice_receipt where id=new.receipt_id;
  if not found then raise exception 'engineering reviewer receipt not found'; end if;
  if new.reviewer_actor_id is not distinct from r.executor_actor_id then
    raise exception 'engineering reviewer cannot be the receipt executor';
  end if;
  select * into e from ops.engineering_execution_envelope where id=r.envelope_id;
  if not found then raise exception 'engineering reviewer envelope not found'; end if;
  select * into executor_session from ops.capability_agent_session where id=e.agent_session_id for share;
  if not found then raise exception 'engineering reviewer executor session not found'; end if;
  perform 1 from public.actor actor
   where (actor.id=r.executor_actor_id and actor.active and actor.kind='automation' and actor.slug='codex')
      or (actor.id=new.reviewer_actor_id and actor.active)
   order by actor.id for share;
  select count(*) into locked_actor_count from public.actor actor
   where (actor.id=r.executor_actor_id and actor.active and actor.kind='automation' and actor.slug='codex')
      or (actor.id=new.reviewer_actor_id and actor.active);
  if locked_actor_count<>2 then raise exception 'engineering reviewer actor authority is not current'; end if;
  select actor.slug into executor_slug from public.actor actor where actor.id=r.executor_actor_id;
  select actor.slug into reviewer_slug from public.actor actor where actor.id=new.reviewer_actor_id;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:'||e.slice_plan_id::text||':'||e.slice_ref,0));
  select * into e from ops.engineering_execution_envelope where id=r.envelope_id for key share;
  if not found then raise exception 'engineering reviewer envelope changed'; end if;
  select * into sp from ops.engineering_slice_plan where id=e.slice_plan_id for key share;
  if not found then raise exception 'engineering reviewer slice plan not found'; end if;
  perform 1 from ops.work_request where id=e.work_request_id for share;
  if not found then raise exception 'engineering reviewer Work Request is not current'; end if;
  select * into r from ops.engineering_slice_receipt where id=new.receipt_id for key share;
  if not found then raise exception 'engineering reviewer receipt changed'; end if;

  if not coalesce(ops.engineering_receipt_exact_object(r.receipt,array[
       'actual_component_refs','actual_resource_refs','artifact_refs','attribution','attempt_id','checks',
       'deviations','envelope_digest','evidence_refs','executor_claim','independent_verification_required',
       'outcome','plan_digest','planned_component_refs','planned_resource_refs','reset_reconstruction',
       'schema_version','slice_ref','source_evidence'
     ]),false)
     or r.receipt->>'schema_version' is distinct from 'engineering-slice-receipt.v1'
     or r.receipt_digest is distinct from
        ('sha256:'||encode(public.digest(ops.guidance_import_canonical_json(r.receipt),'sha256'),'hex'))
     or not coalesce(ops.engineering_receipt_exact_object(r.receipt->'attribution',array[
          'actor_ref','adapter_ref','session_ref'
        ]),false)
     or not coalesce((r.receipt#>>'{attribution,session_ref}') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     or r.receipt->'independent_verification_required' is distinct from 'true'::jsonb
     or jsonb_typeof(r.receipt->'deviations') is distinct from 'array'
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(r.receipt->'deviations')='array'
                                        then r.receipt->'deviations' else '[]'::jsonb end) deviation
        where not coalesce(ops.engineering_receipt_exact_object(deviation,array[
                'category','deviation_ref','evidence_refs','impact','out_of_scope_component_refs',
                'out_of_scope_resource_refs','plan_revision_required','reason','review_state'
              ]),false)
           or not coalesce((deviation->>'deviation_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or exists (
             select 1 from unnest(array['category','reason','impact']) field
              where jsonb_typeof(deviation->field) is distinct from 'string'
                 or not coalesce(btrim(deviation->>field)<>'',false)
           )
           or jsonb_typeof(deviation->'plan_revision_required') is distinct from 'boolean'
           or not coalesce(ops.engineering_receipt_evidence_array(deviation->'evidence_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_resource_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_component_refs'),false)
           or not coalesce(deviation->>'review_state'=any(array['unreviewed','reviewed','resolved']),false)
     )
     or exists (
       select 1 from jsonb_array_elements(case when jsonb_typeof(r.receipt->'deviations')='array'
                                               then r.receipt->'deviations' else '[]'::jsonb end) deviation
        group by deviation->>'deviation_ref' having count(*)>1
     )
     or new.work_request_id is distinct from r.work_request_id
     or new.slice_ref is distinct from r.slice_ref
     or new.fact->>'slice_ref' is distinct from r.slice_ref
     or new.fact->>'attempt_id' is distinct from r.attempt_id
     or new.reviewer_session_ref is distinct from new.fact->>'session_ref'
     or new.reviewer_actor_id is not distinct from r.executor_actor_id
     or reviewer_slug is null
     or not coalesce(new.fact->>'reviewer_ref'=any(array[
          reviewer_slug,'actor:'||reviewer_slug,'reviewer:'||reviewer_slug
        ]),false)
     or new.reviewer_session_ref is not distinct from r.receipt#>>'{attribution,session_ref}'
     or r.receipt->>'slice_ref' is distinct from r.slice_ref
     or r.receipt->>'attempt_id' is distinct from r.attempt_id
     or r.receipt->>'outcome' is distinct from r.outcome
     or r.receipt->>'plan_digest' is distinct from sp.plan_digest
     or r.receipt->>'envelope_digest' is distinct from e.envelope_digest
     or e.work_request_id is distinct from r.work_request_id
     or e.slice_ref is distinct from r.slice_ref
     or e.envelope->>'envelope_id' is distinct from 'env:'||e.id::text
     or e.envelope#>>'{request,job_ref}' is distinct from 'job:'||e.job_id::text
     or e.envelope#>>'{agent_session,id}' is distinct from 'session:'||e.agent_session_id::text
     or executor_session.executor_actor_id is distinct from r.executor_actor_id
     or executor_slug is null
     or r.receipt#>>'{attribution,actor_ref}' is distinct from e.envelope#>>'{server_binding,identity,agent_principal_id}'
     or r.receipt#>>'{attribution,session_ref}' is distinct from e.envelope#>>'{agent_session,id}'
     or r.receipt#>>'{attribution,adapter_ref}' is distinct from e.envelope#>>'{server_binding,adapter,adapter_id}' then
    raise exception 'engineering reviewer fact is not independently bound to the exact receipt';
  end if;

  select coalesce(jsonb_agg(to_jsonb(deviation_ref) order by deviation_ref),'[]'::jsonb)
    into receipt_deviation_refs
    from (
      select deviation->>'deviation_ref' as deviation_ref
        from jsonb_array_elements(case when jsonb_typeof(r.receipt->'deviations')='array'
                                       then r.receipt->'deviations' else '[]'::jsonb end) deviation
    ) refs;
  if not coalesce(ops.engineering_receipt_identifier_sets_equal(
       new.fact->'reviewed_deviation_refs',receipt_deviation_refs),false)
     or exists (
       select 1
         from jsonb_array_elements(new.fact->'resolved_deviation_refs') resolved
        where not exists (
          select 1 from jsonb_array_elements(receipt_deviation_refs) expected
           where expected=resolved
        )
     ) then
    raise exception 'engineering reviewer fact does not cover the receipt deviations';
  end if;

  if new.state='passed' and (
       r.outcome is distinct from 'claimed_complete'
       or r.receipt->>'outcome' is distinct from 'claimed_complete'
       or not coalesce(ops.engineering_receipt_identifier_array(r.receipt->'planned_resource_refs'),false)
       or not coalesce(ops.engineering_receipt_identifier_array(r.receipt->'actual_resource_refs'),false)
       or not coalesce(ops.engineering_receipt_identifier_array(r.receipt->'planned_component_refs'),false)
       or not coalesce(ops.engineering_receipt_identifier_array(r.receipt->'actual_component_refs'),false)
       or not coalesce(ops.engineering_receipt_identifier_sets_equal(
            r.receipt->'planned_resource_refs',(
              select dependency_slice->'declared_resource_refs'
                from jsonb_array_elements(sp.plan->'slices') dependency_slice
               where dependency_slice->>'slice_ref'=r.slice_ref
            )),false)
       or not coalesce(ops.engineering_receipt_identifier_sets_equal(
            r.receipt->'planned_component_refs',(
              select dependency_slice->'declared_component_refs'
                from jsonb_array_elements(sp.plan->'slices') dependency_slice
               where dependency_slice->>'slice_ref'=r.slice_ref
            )),false)
       or exists (
         select 1 from jsonb_array_elements(r.receipt->'actual_resource_refs') actual_ref
          where not exists (
            select 1
              from jsonb_array_elements(sp.plan->'slices') dependency_slice,
                   jsonb_array_elements(dependency_slice->'declared_resource_refs') declared_ref
             where dependency_slice->>'slice_ref'=r.slice_ref and declared_ref=actual_ref
          ) and not exists (
            select 1
              from jsonb_array_elements(r.receipt->'deviations') deviation,
                   jsonb_array_elements(deviation->'out_of_scope_resource_refs') approved_ref
             where deviation->>'review_state'='resolved' and approved_ref=actual_ref
          )
       )
       or exists (
         select 1 from jsonb_array_elements(r.receipt->'actual_component_refs') actual_ref
          where not exists (
            select 1
              from jsonb_array_elements(sp.plan->'slices') dependency_slice,
                   jsonb_array_elements(dependency_slice->'declared_component_refs') declared_ref
             where dependency_slice->>'slice_ref'=r.slice_ref and declared_ref=actual_ref
          ) and not exists (
            select 1
              from jsonb_array_elements(r.receipt->'deviations') deviation,
                   jsonb_array_elements(deviation->'out_of_scope_component_refs') approved_ref
             where deviation->>'review_state'='resolved' and approved_ref=actual_ref
          )
       )
       or not coalesce(ops.engineering_receipt_identifier_array(r.receipt->'artifact_refs'),false)
       or not coalesce(case when jsonb_typeof(r.receipt->'artifact_refs')='array'
                            then jsonb_array_length(r.receipt->'artifact_refs')>0 else false end,false)
       or not coalesce(ops.engineering_receipt_evidence_array(r.receipt->'evidence_refs'),false)
       or not coalesce(case when jsonb_typeof(r.receipt->'evidence_refs')='array'
                            then jsonb_array_length(r.receipt->'evidence_refs')>0 else false end,false)
       or jsonb_typeof(r.receipt->'checks') is distinct from 'array'
       or not coalesce(case when jsonb_typeof(r.receipt->'checks')='array'
                            then jsonb_array_length(r.receipt->'checks')>0 else false end,false)
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(r.receipt->'checks')='array'
                                          then r.receipt->'checks' else '[]'::jsonb end) receipt_check
          where not coalesce(ops.engineering_receipt_exact_object(receipt_check,array[
                  'check_ref','evidence_refs','state'
                ]),false)
             or not coalesce((receipt_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
             or receipt_check->>'state' is distinct from 'passed'
             or not coalesce(ops.engineering_receipt_evidence_array(receipt_check->'evidence_refs'),false)
             or not coalesce(case when jsonb_typeof(receipt_check->'evidence_refs')='array'
                                  then jsonb_array_length(receipt_check->'evidence_refs')>0 else false end,false)
       )
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(r.receipt->'checks')='array'
                                          then r.receipt->'checks' else '[]'::jsonb end) receipt_check
          group by receipt_check->>'check_ref' having count(*)>1
       )
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(r.receipt->'checks')='array'
                                          then r.receipt->'checks' else '[]'::jsonb end) receipt_check
          where not exists (
            select 1
              from jsonb_array_elements(sp.plan->'slices') dependency_slice,
                   jsonb_array_elements(dependency_slice->'planned_checks') planned_check
             where dependency_slice->>'slice_ref'=r.slice_ref
               and planned_check->>'check_ref'=receipt_check->>'check_ref'
          )
       )
       or exists (
         select 1
           from jsonb_array_elements(sp.plan->'slices') dependency_slice,
                jsonb_array_elements(dependency_slice->'planned_checks') planned_check
          where dependency_slice->>'slice_ref'=r.slice_ref
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(r.receipt->'checks')='array'
                                               then r.receipt->'checks' else '[]'::jsonb end) receipt_check
               where receipt_check->>'check_ref'=planned_check->>'check_ref'
            )
       )
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(r.receipt->'checks')='array'
                                          then r.receipt->'checks' else '[]'::jsonb end) receipt_check
           join lateral (
             select planned_check
               from jsonb_array_elements(sp.plan->'slices') dependency_slice,
                    jsonb_array_elements(dependency_slice->'planned_checks') planned_check
              where dependency_slice->>'slice_ref'=r.slice_ref
                and planned_check->>'check_ref'=receipt_check->>'check_ref'
           ) planned on true
          where not exists (
            select 1
              from jsonb_array_elements(case when jsonb_typeof(receipt_check->'evidence_refs')='array'
                                             then receipt_check->'evidence_refs' else '[]'::jsonb end) evidence
             where evidence->>'redaction_class'=case planned.planned_check->>'evidence_requirement'
               when 'redacted_evidence_required' then 'redacted_evidence' else 'metadata_only' end
          )
       )
       or not coalesce(ops.engineering_receipt_exact_object(r.receipt->'source_evidence',array[
            'branch_ref','evidence_refs','source_sha','worktree_ref'
          ]),false)
       or exists (
         select 1 from unnest(array['worktree_ref','branch_ref','source_sha']) field
          where jsonb_typeof(r.receipt->'source_evidence'->field) is distinct from 'string'
             or not coalesce(btrim(r.receipt->'source_evidence'->>field)<>'',false)
             or (field<>'source_sha' and not coalesce(
                  (r.receipt->'source_evidence'->>field) ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false))
       )
       or not coalesce(ops.engineering_receipt_evidence_array(r.receipt->'source_evidence'->'evidence_refs'),false)
       or not coalesce(ops.engineering_receipt_exact_object(r.receipt->'reset_reconstruction',array[
            'fresh_session','inherited_transcript_used','reconstruction_free','remediation_action'
          ]),false)
       or r.receipt->'reset_reconstruction'->'fresh_session' is distinct from 'true'::jsonb
       or r.receipt->'reset_reconstruction'->'inherited_transcript_used' is distinct from 'false'::jsonb
       or jsonb_typeof(r.receipt->'reset_reconstruction'->'reconstruction_free') is distinct from 'boolean'
       or (r.receipt->'reset_reconstruction'->'reconstruction_free'='false'::jsonb and
           (jsonb_typeof(r.receipt->'reset_reconstruction'->'remediation_action') is distinct from 'string'
            or not coalesce(btrim(r.receipt->'reset_reconstruction'->>'remediation_action')<>'',false)))
       or (r.receipt->'reset_reconstruction'->'reconstruction_free'='true'::jsonb and
           r.receipt->'reset_reconstruction'->'remediation_action' is distinct from 'null'::jsonb
           and (jsonb_typeof(r.receipt->'reset_reconstruction'->'remediation_action') is distinct from 'string'
            or not coalesce(btrim(r.receipt->'reset_reconstruction'->>'remediation_action')<>'',false)))
       or not coalesce(ops.engineering_receipt_exact_object(r.receipt->'executor_claim',array[
            'claim_state','claimed_at','claimed_by'
          ]),false)
       or r.receipt->'executor_claim'->>'claim_state' is distinct from 'executor_claim'
       or r.receipt->'executor_claim'->>'claimed_by' is distinct from executor_slug
       or jsonb_typeof(r.receipt->'executor_claim'->'claimed_at') is distinct from 'string'
       or not coalesce(btrim(r.receipt->'executor_claim'->>'claimed_at')<>'',false)
       or jsonb_array_length(new.fact->'evidence_refs')=0
       or not coalesce(ops.engineering_receipt_identifier_sets_equal(
            new.fact->'resolved_deviation_refs',receipt_deviation_refs),false)
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(r.receipt->'deviations')='array'
                                          then r.receipt->'deviations' else '[]'::jsonb end) deviation
          where deviation->>'review_state' is distinct from 'resolved'
             or deviation->'plan_revision_required' is distinct from 'false'::jsonb
       )
     ) then
    raise exception 'engineering reviewer pass requires a complete exact receipt and resolved deviations';
  end if;
  new.contract_version:='engineering-review.v1';
  return new;
end $$;

drop trigger if exists engineering_reviewer_fact_contract_guard
  on ops.engineering_reviewer_fact;
create trigger engineering_reviewer_fact_contract_guard
  before insert on ops.engineering_reviewer_fact
  for each row execute function ops.guard_engineering_reviewer_fact_insert();
-- this migration; pre-0335 bindings remain historical evidence, never replay
-- authority.
alter table ops.siep_job_evidence_binding
  add column if not exists engineering_contract_version text;
alter table ops.siep_job_evidence_binding
  drop constraint if exists siep_job_evidence_binding_engineering_contract_check;
alter table ops.siep_job_evidence_binding
  add constraint siep_job_evidence_binding_engineering_contract_check
  check (engineering_contract_version is null or engineering_contract_version='engineering-review.v1');

create or replace function ops.guard_siep_engineering_evidence_binding()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare v_envelope_id uuid; v_plan_id uuid; v_slice_ref text; v_work_request_id uuid;
        v_session_id uuid; v_executor_actor_id uuid; v_reviewer_actor_id uuid;
        v_actor_count integer;
begin
  if new.engineering_contract_version is not null then
    raise exception 'SIEP Engineering contract version is caller-controlled';
  end if;
  -- IDs are read unlocked; every authority condition is re-read after the
  -- session/actor/lineage/envelope/Work-Request lock chain.
  select e.id,e.slice_plan_id,e.slice_ref,e.work_request_id,e.agent_session_id,
         r.executor_actor_id,review.reviewer_actor_id
    into v_envelope_id,v_plan_id,v_slice_ref,v_work_request_id,v_session_id,
         v_executor_actor_id,v_reviewer_actor_id
    from ops.job j
    join ops.engineering_execution_envelope e on e.job_id=j.id
    join ops.engineering_slice_receipt r on r.envelope_id=e.id
    join ops.engineering_reviewer_fact review on review.receipt_id=r.id
   where j.id=new.job_id and j.definition_key='engineering-slice'
   order by r.created_at desc,r.id desc limit 1;
  if not found then raise exception 'SIEP evidence binding requires an Engineering receipt and review'; end if;
  perform 1 from ops.capability_agent_session s where s.id=v_session_id for share;
  if not found then raise exception 'SIEP Engineering session is not current'; end if;
  perform 1 from public.actor a
   where a.id=any(array[v_executor_actor_id,v_reviewer_actor_id]) and a.active
   order by a.id for share;
  select count(*) into v_actor_count from public.actor a
   where a.id=any(array[v_executor_actor_id,v_reviewer_actor_id]) and a.active;
  if v_executor_actor_id is null or v_reviewer_actor_id is null or v_executor_actor_id=v_reviewer_actor_id
     or v_actor_count<>2 then raise exception 'SIEP Engineering actor authority is not current'; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:'||v_plan_id::text||':'||v_slice_ref,0));
  perform 1 from ops.engineering_execution_envelope e
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
   where e.id=v_envelope_id and e.job_id=new.job_id for key share of e,sp;
  if not found then raise exception 'SIEP Engineering envelope is not current'; end if;
  perform 1 from ops.work_request where id=v_work_request_id for share;
  if not found then raise exception 'SIEP Engineering Work Request is not current'; end if;
  if new.definition_key is distinct from 'engineering-slice'
     or new.definition_version is distinct from 1
     or not exists (
       select 1 from ops.job j
       join ops.engineering_execution_envelope e on e.job_id=j.id
       join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id and sp.work_request_id=e.work_request_id
       join ops.engineering_slice_receipt receipt on receipt.envelope_id=e.id and receipt.work_request_id=e.work_request_id
       join ops.job_attempt attempt on attempt.id=receipt.job_attempt_id and attempt.job_id=j.id and attempt.attempt=j.attempt and attempt.state='succeeded'
       join ops.engineering_reviewer_fact review on review.receipt_id=receipt.id and review.work_request_id=receipt.work_request_id and review.slice_ref=receipt.slice_ref
       join public.actor reviewer on reviewer.id=review.reviewer_actor_id and reviewer.slug='joe' and reviewer.active
       join ops.siep_package_contract package on package.package_key=new.package_key and package.work_request_id=e.work_request_id
      where j.id=new.job_id and j.definition_key='engineering-slice' and j.definition_version=1 and j.state='succeeded'
        and e.state_version=new.work_request_version and sp.work_request_version=new.work_request_version
        and receipt.outcome='claimed_complete' and review.state='passed'
        and review.contract_version='engineering-review.v1'
        and review.reviewer_actor_id<>receipt.executor_actor_id
        and exists (select 1 from ops.job_receipt jr where jr.job_id=j.id and jr.attempt=j.attempt and jr.kind='completion')
        and e.id=v_envelope_id
        and not exists (
          select 1 from ops.engineering_execution_envelope successor
           where successor.supersedes_envelope_id=e.id
        )
        and 1=(
          select count(*) from ops.engineering_execution_envelope leaf
           where leaf.slice_plan_id=e.slice_plan_id
             and leaf.slice_ref=e.slice_ref
             and not exists (
               select 1 from ops.engineering_execution_envelope successor
                where successor.supersedes_envelope_id=leaf.id
             )
        )
     ) then
    raise exception 'SIEP evidence binding requires a 0335-verified Engineering receipt and review';
  end if;
  new.engineering_contract_version:='engineering-review.v1';
  return new;
end $$;

drop trigger if exists siep_engineering_evidence_binding_contract_guard on ops.siep_job_evidence_binding;
create trigger siep_engineering_evidence_binding_contract_guard
  before insert on ops.siep_job_evidence_binding
  for each row execute function ops.guard_siep_engineering_evidence_binding();

alter function ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)
  rename to siep_bind_evidence_job_unchecked_0324;
revoke all on function ops.siep_bind_evidence_job_unchecked_0324(text,integer,text,uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
create function ops.siep_bind_evidence_job(
  p_component text,p_base_version integer,p_evidence_kind text,p_job_id uuid,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare result jsonb; contract_version text;
begin
  result:=ops.siep_bind_evidence_job_unchecked_0324(p_component,p_base_version,p_evidence_kind,p_job_id,p_idempotency_key);
  select b.engineering_contract_version into contract_version
    from ops.siep_job_evidence_binding b
    join ops.engineering_execution_envelope envelope on envelope.job_id=b.job_id
   where b.job_id=p_job_id
     and not exists (
       select 1 from ops.engineering_execution_envelope successor
        where successor.supersedes_envelope_id=envelope.id
     )
     and 1=(
       select count(*) from ops.engineering_execution_envelope leaf
        where leaf.slice_plan_id=envelope.slice_plan_id
          and leaf.slice_ref=envelope.slice_ref
          and not exists (
            select 1 from ops.engineering_execution_envelope successor
             where successor.supersedes_envelope_id=leaf.id
          )
     );
  if contract_version is distinct from 'engineering-review.v1' then
    raise exception 'historical or superseded SIEP Engineering evidence binding is not 0335 verified';
  end if;
  return result;
end $$;
revoke all on function ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.siep_bind_evidence_job(text,integer,text,uuid,uuid) to carr_authority;

create or replace function ops.siep_current_evidence_digest(
  p_ledger_kind text,p_ledger_id uuid
) returns text language sql stable security definer
set search_path=pg_catalog,ops,public
as $$
  select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(source_row),'sha256'),'hex')
    from (
      select jsonb_build_object('receipt',to_jsonb(r),'job',to_jsonb(j),'attempt',to_jsonb(a),
                                'binding',to_jsonb(b)) source_row
        from ops.job_receipt r join ops.job j on j.id=r.job_id
        join ops.job_attempt a on a.job_id=j.id and a.attempt=r.attempt
        join ops.siep_job_evidence_binding b on b.job_id=j.id
        join ops.engineering_execution_envelope envelope on envelope.job_id=j.id
       where p_ledger_kind='job_receipt' and r.id=p_ledger_id
         and b.engineering_contract_version='engineering-review.v1'
         and not exists (
           select 1 from ops.engineering_execution_envelope successor
            where successor.supersedes_envelope_id=envelope.id
         )
         and 1=(
           select count(*) from ops.engineering_execution_envelope leaf
            where leaf.slice_plan_id=envelope.slice_plan_id
              and leaf.slice_ref=envelope.slice_ref
              and not exists (
                select 1 from ops.engineering_execution_envelope successor
                 where successor.supersedes_envelope_id=leaf.id
              )
         )
      union all select to_jsonb(e) from public.event e
       where p_ledger_kind='decision_event' and e.id=p_ledger_id
    ) canonical
$$;
revoke all on function ops.siep_current_evidence_digest(text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

-- Replace the JSON/colliding-attempt dependency predicate from 0311 with an
-- exact relational lineage check.  Dependency lineage locks serialize this
-- decision with receipt append and same-slice successor admission, and locks
-- are acquired in lexical order for multi-dependency DAG slices.
create or replace function ops.engineering_enqueue_slice_job(
  p_work_request text,p_slice_ref text,p_plan_digest text,
  p_idempotency_key text,p_generation integer
)
returns ops.job language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare row ops.job%rowtype;
        slice_plan ops.engineering_slice_plan%rowtype;
        dependency_ref text;
        job_key text;
        current_source jsonb;
begin
  if btrim(coalesce(p_work_request,''))='' or btrim(coalesce(p_slice_ref,''))=''
     or p_plan_digest is null or p_plan_digest !~ '^sha256:[0-9a-f]{64}$'
     or btrim(coalesce(p_idempotency_key,''))='' or p_generation is null or p_generation<1 then
    raise exception 'engineering job admission fields are invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-slice:'||p_plan_digest||':'||p_slice_ref,0));
  current_source:=ops.engineering_admission_source(p_work_request);
  if current_source is null then
    raise exception 'engineering job admission source is not current';
  end if;
  select sp.* into slice_plan
    from ops.engineering_slice_plan sp
   where sp.plan_digest=p_plan_digest
     and sp.work_request_id=regexp_replace(current_source->'work_request'->>'id','^wr:','')::uuid
     and sp.accepted_plan_id=(current_source->'accepted_plan'->>'record_id')::uuid
     and sp.accepted_plan_hash=current_source->'accepted_plan'->>'digest'
     and sp.work_request_version=(current_source->'work_request'->>'version')::integer
     and sp.plan->'work_request'->>'canonical_record_digest'=
         current_source->'work_request'->>'canonical_record_digest'
     and sp.plan->'accepted_plan_revision'->>'id'=
         current_source->'accepted_plan'->>'plan_ref'
     and sp.plan->'accepted_plan_revision'->>'digest'=
         current_source->'accepted_plan'->>'digest'
     and exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(sp.plan->'slices')='array'
                                        then sp.plan->'slices' else '[]'::jsonb end) slice_item
        where slice_item->>'slice_ref'=p_slice_ref
     )
   for key share;
  if not found then
    raise exception 'engineering slice is not registered for the exact current plan';
  end if;
  perform 1 from ops.work_request w
   where w.id=slice_plan.work_request_id and w.ref=p_work_request for share;
  if not found or ops.engineering_admission_source(p_work_request) is distinct from current_source then
    raise exception 'engineering job admission source changed during serialization';
  end if;

  for dependency_ref in
    select dependency#>>'{}'
      from jsonb_array_elements(
        coalesce((select slice_item->'dependency_refs'
                    from jsonb_array_elements(slice_plan.plan->'slices') slice_item
                   where slice_item->>'slice_ref'=p_slice_ref),'[]'::jsonb)) dependency
     order by dependency#>>'{}'
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      'engineering-envelope:'||slice_plan.id::text||':'||dependency_ref,0));
  end loop;

  if exists (
    select 1
      from jsonb_array_elements(slice_plan.plan->'slices') slice_item,
           jsonb_array_elements_text(coalesce(slice_item->'dependency_refs','[]'::jsonb)) dependency
     where slice_item->>'slice_ref'=p_slice_ref
       and not exists (
         select 1
           from ops.engineering_slice_receipt receipt
           join ops.engineering_execution_envelope envelope
             on envelope.id=receipt.envelope_id
            and envelope.slice_plan_id=slice_plan.id
            and envelope.work_request_id=slice_plan.work_request_id
            and envelope.slice_ref=dependency
           join ops.capability_agent_session executor_session
             on executor_session.id=envelope.agent_session_id
            and executor_session.executor_actor_id=receipt.executor_actor_id
           join public.actor executor_actor
             on executor_actor.id=receipt.executor_actor_id
            and executor_actor.active
           join ops.engineering_reviewer_fact review
             on review.receipt_id=receipt.id
            and review.work_request_id=receipt.work_request_id
            and review.slice_ref=receipt.slice_ref
           join public.actor reviewer_actor
             on reviewer_actor.id=review.reviewer_actor_id
            and reviewer_actor.active
          where receipt.work_request_id=slice_plan.work_request_id
            and receipt.slice_ref=dependency
            and receipt.outcome='claimed_complete'
            and receipt.receipt->>'outcome'='claimed_complete'
            and coalesce(ops.engineering_receipt_exact_object(receipt.receipt,array[
                  'actual_component_refs','actual_resource_refs','artifact_refs','attribution','attempt_id','checks',
                  'deviations','envelope_digest','evidence_refs','executor_claim','independent_verification_required',
                  'outcome','plan_digest','planned_component_refs','planned_resource_refs','reset_reconstruction',
                  'schema_version','slice_ref','source_evidence'
                ]),false)
            and receipt.receipt->>'schema_version'='engineering-slice-receipt.v1'
            and receipt.receipt_digest=
                ('sha256:'||encode(public.digest(ops.guidance_import_canonical_json(receipt.receipt),'sha256'),'hex'))
            and receipt.receipt->>'plan_digest'=slice_plan.plan_digest
            and receipt.receipt->>'slice_ref'=receipt.slice_ref
            and receipt.receipt->>'attempt_id'=receipt.attempt_id
            and receipt.receipt->>'envelope_digest'=envelope.envelope_digest
            and envelope.envelope->>'envelope_id'='env:'||envelope.id::text
            and envelope.envelope#>>'{request,job_ref}'='job:'||envelope.job_id::text
            and envelope.envelope#>>'{agent_session,id}'='session:'||executor_session.id::text
            and receipt.receipt->'independent_verification_required'='true'::jsonb
            and coalesce(ops.engineering_receipt_exact_object(receipt.receipt->'attribution',array[
                  'actor_ref','adapter_ref','session_ref'
                ]),false)
            and receipt.receipt#>>'{attribution,actor_ref}'=
                envelope.envelope#>>'{server_binding,identity,agent_principal_id}'
            and receipt.receipt#>>'{attribution,session_ref}'=
                envelope.envelope#>>'{agent_session,id}'
            and receipt.receipt#>>'{attribution,adapter_ref}'=
                envelope.envelope#>>'{server_binding,adapter,adapter_id}'
            and jsonb_typeof(receipt.receipt->'deviations')='array'
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'deviations')='array'
                                               then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
               where not coalesce(ops.engineering_receipt_exact_object(deviation,array[
                       'category','deviation_ref','evidence_refs','impact','out_of_scope_component_refs',
                       'out_of_scope_resource_refs','plan_revision_required','reason','review_state'
                     ]),false)
                  or not coalesce((deviation->>'deviation_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
                  or exists (
                    select 1 from unnest(array['category','reason','impact']) field
                     where jsonb_typeof(deviation->field) is distinct from 'string'
                        or not coalesce(btrim(deviation->>field)<>'',false)
                  )
                  or jsonb_typeof(deviation->'plan_revision_required') is distinct from 'boolean'
                  or not coalesce(ops.engineering_receipt_evidence_array(deviation->'evidence_refs'),false)
                  or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_resource_refs'),false)
                  or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_component_refs'),false)
                  or not coalesce(deviation->>'review_state'=any(array['unreviewed','reviewed','resolved']),false)
            )
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'deviations')='array'
                                               then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
               group by deviation->>'deviation_ref'
              having count(*)>1
            )
            and coalesce(ops.engineering_receipt_identifier_array(receipt.receipt->'planned_resource_refs'),false)
            and coalesce(ops.engineering_receipt_identifier_array(receipt.receipt->'actual_resource_refs'),false)
            and coalesce(ops.engineering_receipt_identifier_array(receipt.receipt->'planned_component_refs'),false)
            and coalesce(ops.engineering_receipt_identifier_array(receipt.receipt->'actual_component_refs'),false)
            and coalesce(ops.engineering_receipt_identifier_sets_equal(
                  receipt.receipt->'planned_resource_refs',(
                    select dependency_slice->'declared_resource_refs'
                      from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice
                     where dependency_slice->>'slice_ref'=receipt.slice_ref
                  )),false)
            and coalesce(ops.engineering_receipt_identifier_sets_equal(
                  receipt.receipt->'planned_component_refs',(
                    select dependency_slice->'declared_component_refs'
                      from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice
                     where dependency_slice->>'slice_ref'=receipt.slice_ref
                  )),false)
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'actual_resource_refs')='array'
                                               then receipt.receipt->'actual_resource_refs' else '[]'::jsonb end) actual_ref
               where not exists (
                 select 1
                   from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice,
                        jsonb_array_elements(dependency_slice->'declared_resource_refs') declared_ref
                  where dependency_slice->>'slice_ref'=receipt.slice_ref and declared_ref=actual_ref
               ) and not exists (
                 select 1
                   from jsonb_array_elements(receipt.receipt->'deviations') deviation,
                        jsonb_array_elements(deviation->'out_of_scope_resource_refs') approved_ref
                  where deviation->>'review_state'='resolved' and approved_ref=actual_ref
               )
            )
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'actual_component_refs')='array'
                                               then receipt.receipt->'actual_component_refs' else '[]'::jsonb end) actual_ref
               where not exists (
                 select 1
                   from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice,
                        jsonb_array_elements(dependency_slice->'declared_component_refs') declared_ref
                  where dependency_slice->>'slice_ref'=receipt.slice_ref and declared_ref=actual_ref
               ) and not exists (
                 select 1
                   from jsonb_array_elements(receipt.receipt->'deviations') deviation,
                        jsonb_array_elements(deviation->'out_of_scope_component_refs') approved_ref
                  where deviation->>'review_state'='resolved' and approved_ref=actual_ref
               )
            )
            and coalesce(ops.engineering_receipt_identifier_array(receipt.receipt->'artifact_refs'),false)
            and coalesce(case when jsonb_typeof(receipt.receipt->'artifact_refs')='array'
                              then jsonb_array_length(receipt.receipt->'artifact_refs')>0 else false end,false)
            and coalesce(ops.engineering_receipt_evidence_array(receipt.receipt->'evidence_refs'),false)
            and coalesce(case when jsonb_typeof(receipt.receipt->'evidence_refs')='array'
                              then jsonb_array_length(receipt.receipt->'evidence_refs')>0 else false end,false)
            and jsonb_typeof(receipt.receipt->'checks')='array'
            and coalesce(case when jsonb_typeof(receipt.receipt->'checks')='array'
                              then jsonb_array_length(receipt.receipt->'checks')>0 else false end,false)
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'checks')='array'
                                               then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
               where not coalesce(ops.engineering_receipt_exact_object(receipt_check,array[
                       'check_ref','evidence_refs','state'
                     ]),false)
                  or not coalesce((receipt_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
                  or receipt_check->>'state' is distinct from 'passed'
                  or not coalesce(ops.engineering_receipt_evidence_array(receipt_check->'evidence_refs'),false)
                  or not coalesce(case when jsonb_typeof(receipt_check->'evidence_refs')='array'
                                       then jsonb_array_length(receipt_check->'evidence_refs')>0 else false end,false)
            )
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'checks')='array'
                                               then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
               group by receipt_check->>'check_ref'
              having count(*)>1
            )
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'checks')='array'
                                               then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
               where not exists (
                 select 1
                   from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice,
                        jsonb_array_elements(dependency_slice->'planned_checks') planned_check
                  where dependency_slice->>'slice_ref'=receipt.slice_ref
                    and planned_check->>'check_ref'=receipt_check->>'check_ref'
               )
            )
            and not exists (
              select 1
                from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice,
                     jsonb_array_elements(dependency_slice->'planned_checks') planned_check
               where dependency_slice->>'slice_ref'=receipt.slice_ref
                 and not exists (
                   select 1 from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'checks')='array'
                                                          then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
                    where receipt_check->>'check_ref'=planned_check->>'check_ref'
                 )
            )
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'checks')='array'
                                               then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
                join lateral (
                  select planned_check
                    from jsonb_array_elements(slice_plan.plan->'slices') dependency_slice,
                         jsonb_array_elements(dependency_slice->'planned_checks') planned_check
                   where dependency_slice->>'slice_ref'=receipt.slice_ref
                     and planned_check->>'check_ref'=receipt_check->>'check_ref'
                ) planned on true
               where not exists (
                 select 1 from jsonb_array_elements(case when jsonb_typeof(receipt_check->'evidence_refs')='array'
                                                        then receipt_check->'evidence_refs' else '[]'::jsonb end) evidence
                  where evidence->>'redaction_class'=case planned.planned_check->>'evidence_requirement'
                    when 'redacted_evidence_required' then 'redacted_evidence' else 'metadata_only' end
               )
            )
            and coalesce(ops.engineering_receipt_exact_object(receipt.receipt->'source_evidence',array[
                  'branch_ref','evidence_refs','source_sha','worktree_ref'
                ]),false)
            and not exists (
              select 1 from unnest(array['worktree_ref','branch_ref','source_sha']) field
               where jsonb_typeof(receipt.receipt->'source_evidence'->field) is distinct from 'string'
                  or not coalesce(btrim(receipt.receipt->'source_evidence'->>field)<>'',false)
                  or (field<>'source_sha' and not coalesce(
                       (receipt.receipt->'source_evidence'->>field) ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false))
            )
            and coalesce(ops.engineering_receipt_evidence_array(receipt.receipt->'source_evidence'->'evidence_refs'),false)
            and coalesce(ops.engineering_receipt_exact_object(receipt.receipt->'reset_reconstruction',array[
                  'fresh_session','inherited_transcript_used','reconstruction_free','remediation_action'
                ]),false)
            and receipt.receipt->'reset_reconstruction'->'fresh_session'='true'::jsonb
            and receipt.receipt->'reset_reconstruction'->'inherited_transcript_used'='false'::jsonb
            and jsonb_typeof(receipt.receipt->'reset_reconstruction'->'reconstruction_free')='boolean'
            and (
              (receipt.receipt->'reset_reconstruction'->'reconstruction_free'='false'::jsonb and
               jsonb_typeof(receipt.receipt->'reset_reconstruction'->'remediation_action')='string' and
               btrim(receipt.receipt->'reset_reconstruction'->>'remediation_action')<>'')
              or
              (receipt.receipt->'reset_reconstruction'->'reconstruction_free'='true'::jsonb and
               (receipt.receipt->'reset_reconstruction'->'remediation_action'='null'::jsonb or
                (jsonb_typeof(receipt.receipt->'reset_reconstruction'->'remediation_action')='string' and
                 btrim(receipt.receipt->'reset_reconstruction'->>'remediation_action')<>'')))
            )
            and coalesce(ops.engineering_receipt_exact_object(receipt.receipt->'executor_claim',array[
                  'claim_state','claimed_at','claimed_by'
                ]),false)
            and receipt.receipt->'executor_claim'->>'claim_state'='executor_claim'
            and receipt.receipt->'executor_claim'->>'claimed_by'=executor_actor.slug
            and jsonb_typeof(receipt.receipt->'executor_claim'->'claimed_at')='string'
            and btrim(receipt.receipt->'executor_claim'->>'claimed_at')<>''
            and review.state='passed'
            and review.contract_version='engineering-review.v1'
            and review.fact->>'state'='passed'
            and coalesce(ops.engineering_receipt_exact_object(review.fact,array[
                  'attempt_id','evidence_refs','is_independent','resolved_deviation_refs',
                  'reviewed_deviation_refs','reviewer_ref','session_ref','slice_ref','state'
                ]),false)
            and review.fact->>'slice_ref'=receipt.slice_ref
            and review.fact->>'attempt_id'=receipt.attempt_id
            and review.reviewer_actor_id<>receipt.executor_actor_id
            and review.fact->>'reviewer_ref'=any(array[
                  reviewer_actor.slug,'actor:'||reviewer_actor.slug,'reviewer:'||reviewer_actor.slug
                ])
            and review.reviewer_session_ref=review.fact->>'session_ref'
            and review.reviewer_session_ref<>receipt.receipt#>>'{attribution,session_ref}'
            and review.reviewer_session_ref ~ '^session:[A-Za-z0-9][A-Za-z0-9._:-]{1,119}$'
            and review.fact->>'reviewer_ref' ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
            and review.fact->'is_independent'='true'::jsonb
            and coalesce(ops.engineering_receipt_evidence_array(review.fact->'evidence_refs'),false)
            and coalesce(case when jsonb_typeof(review.fact->'evidence_refs')='array'
                              then jsonb_array_length(review.fact->'evidence_refs')>0 else false end,false)
            and coalesce(ops.engineering_receipt_identifier_sets_equal(
                  review.fact->'reviewed_deviation_refs',
                  coalesce((
                    select jsonb_agg(to_jsonb(deviation->>'deviation_ref') order by deviation->>'deviation_ref')
                      from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'deviations')='array'
                                                     then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
                  ),'[]'::jsonb)),false)
            and coalesce(ops.engineering_receipt_identifier_sets_equal(
                  review.fact->'resolved_deviation_refs',
                  coalesce((
                    select jsonb_agg(to_jsonb(deviation->>'deviation_ref') order by deviation->>'deviation_ref')
                      from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'deviations')='array'
                                                     then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
                  ),'[]'::jsonb)),false)
            and not exists (
              select 1
                from jsonb_array_elements(case when jsonb_typeof(receipt.receipt->'deviations')='array'
                                               then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
               where deviation->>'review_state' is distinct from 'resolved'
                  or deviation->'plan_revision_required' is distinct from 'false'::jsonb
            )
            and not exists (
              select 1 from ops.engineering_execution_envelope successor
               where successor.supersedes_envelope_id=envelope.id
            )
            and 1=(
              select count(*)
                from ops.engineering_execution_envelope leaf
               where leaf.slice_plan_id=slice_plan.id
                 and leaf.slice_ref=receipt.slice_ref
                 and not exists (
                   select 1 from ops.engineering_execution_envelope successor
                    where successor.supersedes_envelope_id=leaf.id
                 )
            )
       )
  ) then
    raise exception 'engineering slice dependencies are not independently verified';
  end if;

  job_key := 'engineering-slice:'||p_plan_digest||':'||p_work_request||':'||
             p_slice_ref||':generation:'||p_generation;
  select * into row from ops.job where idempotency_key=job_key;
  if row.id is not null then return row; end if;
  select * into row from ops.enqueue_job(
    'engineering-slice',1,now(),
    jsonb_build_object('work_request',p_work_request,'slice_ref',p_slice_ref,
                       'plan_digest',p_plan_digest,'generation',p_generation),
    job_key,'shadow');
  return row;
end $$;

-- Preserve 0325's lineage serialization while making malformed JSON authority
-- fail ineligible instead of throwing during safe successor recovery.
-- Direct writer admission reaches this BEFORE INSERT trigger.  It therefore
-- starts with the referenced session and revocable actor authority, not the
-- lineage lock that session terminalization will later acquire.
create or replace function ops.guard_engineering_envelope_supersession()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare prior ops.engineering_execution_envelope%rowtype;
        current_plan ops.engineering_slice_plan%rowtype;
        prior_count integer; v_executor_actor_id uuid;
        current_source jsonb; current_work_ref text;
begin
  select s.executor_actor_id into v_executor_actor_id
    from ops.capability_agent_session s where s.id=new.agent_session_id for share;
  if not found then raise exception 'engineering envelope session is not current'; end if;
  perform 1 from public.actor a
   where a.id=v_executor_actor_id and a.active and a.kind='automation' and a.slug='codex'
   order by a.id for share;
  if not found then raise exception 'engineering envelope executor actor is not current'; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || new.slice_plan_id::text || ':' || new.slice_ref,0));
  select * into current_plan from ops.engineering_slice_plan
   where id=new.slice_plan_id for key share;
  if not found then raise exception 'engineering envelope slice plan is not current'; end if;
  select ref into current_work_ref from ops.work_request
   where id=new.work_request_id for share;
  if not found then raise exception 'engineering envelope Work Request is not current'; end if;
  current_source:=ops.engineering_admission_source(current_work_ref);
  if current_source is null
     or new.work_request_id is distinct from
        regexp_replace(current_source->'work_request'->>'id','^wr:','')::uuid
     or new.accepted_plan_id is distinct from
        (current_source->'accepted_plan'->>'record_id')::uuid
     or new.state_version is distinct from
        (current_source->'work_request'->>'version')::integer
     or new.canonical_record_digest is distinct from
        current_source->'work_request'->>'canonical_record_digest'
     or current_plan.work_request_id is distinct from new.work_request_id
     or current_plan.accepted_plan_id is distinct from new.accepted_plan_id
     or current_plan.accepted_plan_hash is distinct from
        current_source->'accepted_plan'->>'digest'
     or current_plan.work_request_version is distinct from new.state_version
     or current_plan.plan->'accepted_plan_revision'->>'id' is distinct from
        current_source->'accepted_plan'->>'plan_ref'
     or current_plan.plan->'accepted_plan_revision'->>'digest' is distinct from
        current_source->'accepted_plan'->>'digest' then
    raise exception 'engineering envelope source or accepted plan is not current';
  end if;
  select count(*) into prior_count from ops.engineering_execution_envelope
   where slice_plan_id=new.slice_plan_id and slice_ref=new.slice_ref;
  if prior_count=0 then
    if new.supersedes_envelope_id is not null then raise exception 'first engineering envelope cannot supersede another envelope'; end if;
    return new;
  end if;
  if new.supersedes_envelope_id is null then raise exception 'later engineering envelope must name its immutable predecessor'; end if;
  select * into prior from ops.engineering_execution_envelope where id=new.supersedes_envelope_id for key share;
  if not found or prior.slice_plan_id<>new.slice_plan_id or prior.slice_ref<>new.slice_ref
     or prior.accepted_plan_id<>new.accepted_plan_id or prior.work_request_id<>new.work_request_id then
    raise exception 'engineering envelope predecessor is outside the exact slice binding';
  end if;
  if exists (select 1 from ops.engineering_execution_envelope where supersedes_envelope_id=prior.id) then raise exception 'engineering envelope predecessor already has a successor'; end if;
  if exists (select 1 from ops.job j where j.id=prior.job_id and j.state='running' and j.lease_token is not null and j.leased_until>clock_timestamp()) then raise exception 'leased engineering envelope cannot be superseded'; end if;
  if prior.expires_at>clock_timestamp()
     and prior.envelope->'server_binding'->'authority'->>'read_only'='false'
     and exists (select 1 from ops.capability_agent_session s where s.id=prior.agent_session_id and s.state not in ('completed','cancelled'))
     and not exists (select 1 from ops.engineering_slice_receipt r where r.envelope_id=prior.id and r.outcome in ('failed','blocked','reopened')) then
    raise exception 'current executable engineering envelope cannot be superseded';
  end if;
  return new;
end $$;

-- Shared queue claim and heartbeat doors preserve their established behavior
-- for every other definition.  Engineering authority is narrower and must
-- enter through engineering_claim_slice with its immutable envelope checks.
create or replace function ops.claim_job(
  p_worker text,p_limit integer default 1,p_lease_seconds integer default 300
) returns table (
  job_id uuid,lease_token uuid,definition_key text,definition_version integer,
  payload jsonb,execution_kind text,execution_contract jsonb,
  attempt integer,timeout_seconds integer,mode text
) language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_limit<1 or p_lease_seconds<1 then
    raise exception 'worker, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id from ops.job j join ops.job_definition d
      on d.key=j.definition_key and d.version=j.definition_version
     where d.enabled and j.state in ('queued','retry_wait') and j.next_attempt_at<=now()
       and j.definition_key<>'engineering-slice'
       and not (j.definition_key='calendar-prebrief-projection-joe-daily' and j.definition_version=1)
     order by j.scheduled_for,j.created_at for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
      from candidate c where j.id=c.id returning j.*
  ), attempts(claimed_job_id) as (
    insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning ops.job_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c join ops.job_definition d
      on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.claimed_job_id=c.id;
end $$;

create or replace function ops.claim_job_mode(
  p_worker text,p_mode text,p_limit integer default 1,p_lease_seconds integer default 300
) returns table (
  job_id uuid,lease_token uuid,definition_key text,definition_version integer,
  payload jsonb,execution_kind text,execution_contract jsonb,
  attempt integer,timeout_seconds integer,mode text
) language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_mode not in ('shadow','canary','live','replay')
     or p_limit<1 or p_lease_seconds<1 then
    raise exception 'worker, valid mode, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id from ops.job j join ops.job_definition d
      on d.key=j.definition_key and d.version=j.definition_version
     where d.enabled and j.state in ('queued','retry_wait') and j.next_attempt_at<=now()
       and j.mode=p_mode and j.definition_key<>'engineering-slice'
       and not (j.definition_key='calendar-prebrief-projection-joe-daily' and j.definition_version=1)
     order by j.scheduled_for,j.created_at for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
      from candidate c where j.id=c.id returning j.*
  ), attempts(claimed_job_id) as (
    insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning ops.job_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c join ops.job_definition d
      on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.claimed_job_id=c.id;
end $$;

create or replace function ops.heartbeat_job(
  p_job_id uuid,p_lease_token uuid,p_lease_seconds integer default 300
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; n integer;
begin
  select * into j from ops.job where id=p_job_id for update;
  if found and j.definition_key='engineering-slice' then
    raise exception 'engineering jobs require scoped controller functions';
  end if;
  update ops.job set leased_until=now()+make_interval(secs=>p_lease_seconds),updated_at=now()
   where id=p_job_id and state='running' and lease_token=p_lease_token
     and leased_until>=now();
  get diagnostics n=row_count;
  return n=1;
end $$;

-- Generic terminal doors retain their established behavior for every other
-- definition.  Engineering jobs finalize only through the scoped receipt door,
-- or fail through engineering_fail_claim when no typed receipt can exist.
create or replace function ops.complete_job(
  p_job_id uuid,p_lease_token uuid,p_evidence jsonb,p_receipt_ref text
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if found and j.definition_key='engineering-slice' then
    raise exception 'engineering jobs require scoped controller functions';
  end if;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  update ops.job_attempt set state='succeeded',ended_at=now()
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state='succeeded',ended_at=now(),lease_owner=null,
         lease_token=null,leased_until=null,updated_at=now() where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,'completion',p_receipt_ref,coalesce(p_evidence,'{}'::jsonb));
  return true;
end $$;

create or replace function ops.fail_job(
  p_job_id uuid,p_lease_token uuid,p_failure_class text,p_detail text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; next_state text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if found and j.definition_key='engineering-slice' then
    raise exception 'engineering jobs require scoped controller functions';
  end if;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  next_state := case when j.attempt < j.max_attempts then 'retry_wait' else 'dead_lettered' end;
  update ops.job_attempt set state='failed',ended_at=now(),
         failure_class=p_failure_class,detail=p_detail
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state=next_state,
         next_attempt_at=case when next_state='retry_wait'
                              then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                              else next_attempt_at end,
         ended_at=case when next_state='dead_lettered' then now() else null end,
         last_failure_class=p_failure_class,last_failure_detail=p_detail,
         lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
   where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,case when next_state='dead_lettered' then 'dead_letter' else 'failure' end,
           concat('failure:',j.id,':',j.attempt),
           jsonb_build_object('failure_class',p_failure_class,'detail',p_detail,'next_state',next_state));
  return next_state;
end $$;

create or replace function ops.timeout_job(
  p_job_id uuid,p_lease_token uuid,p_detail text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; next_state text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if found and j.definition_key='engineering-slice' then
    raise exception 'engineering jobs require scoped controller functions';
  end if;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  next_state := case when j.attempt < j.max_attempts then 'retry_wait' else 'dead_lettered' end;
  update ops.job_attempt set state='timed_out',ended_at=now(),
         failure_class='execution_timeout',detail=p_detail
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state=next_state,
         next_attempt_at=case when next_state='retry_wait'
                              then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                              else next_attempt_at end,
         ended_at=case when next_state='dead_lettered' then now() else null end,
         last_failure_class='execution_timeout',last_failure_detail=p_detail,
         lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
   where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,case when next_state='dead_lettered' then 'dead_letter' else 'timeout' end,
           concat('timeout:',j.id,':',j.attempt),
           jsonb_build_object('failure_class','execution_timeout','detail',p_detail,'next_state',next_state));
  return next_state;
end $$;

-- Retire obsolete overloads without touching the current three-argument
-- controller binding or the atomic scoped finalizer created above.
revoke all on function ops.engineering_envelope_is_executable(uuid,uuid,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
drop function if exists ops.engineering_envelope_is_executable(uuid,uuid,integer);

revoke all on function ops.engineering_envelope_currentness(uuid,uuid),
  ops.engineering_envelope_is_executable(uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_safe_timestamptz(text),
  ops.capability_agent_session_lease_immutable(),
  ops.engineering_work_request_currentness_guard(),
  ops.guard_engineering_job_definition_currentness_update(),
  ops.guard_engineering_actor_authority_update()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_receipt_exact_object(jsonb,text[]),
  ops.engineering_receipt_identifier_array(jsonb),
  ops.engineering_receipt_identifier_sets_equal(jsonb,jsonb),
  ops.engineering_receipt_evidence_array(jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.guard_engineering_reviewer_fact_insert(),
  ops.guard_engineering_envelope_supersession(),
  ops.guard_engineering_session_terminalization(),
  ops.guard_siep_engineering_evidence_binding()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_enqueue_slice_job(text,text,text,text,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.engineering_enqueue_slice_job(text,text,text,text,integer)
  to carr_writer;

revoke all on function ops.engineering_claim_slice(text,integer,integer),
  ops.engineering_controller_binding(uuid,uuid,uuid),
  ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid),
  ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid),
  ops.engineering_fail_claim(uuid,uuid,text,text),
  ops.engineering_retire_permanently_ineligible_jobs()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.engineering_claim_slice(text,integer,integer),
  ops.engineering_controller_binding(uuid,uuid,uuid),
  ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid),
  ops.engineering_fail_claim(uuid,uuid,text,text),
  ops.engineering_retire_permanently_ineligible_jobs()
  to carr_jobs;
grant execute on function ops.engineering_envelope_currentness(uuid,uuid)
  to carr_reader,carr_writer;

do $$
declare fn text;
begin
  if to_regprocedure('ops.engineering_controller_binding(uuid,uuid)') is not null
     or to_regprocedure('ops.engineering_envelope_is_executable(uuid,uuid,integer)') is not null then
    raise exception '0335 FAILED: obsolete Engineering overload remains executable';
  end if;
  if (select count(*) from pg_proc where pronamespace='ops'::regnamespace
        and proname='engineering_controller_binding')<>1
     or to_regprocedure('ops.engineering_controller_binding(uuid,uuid,uuid)') is null
     or (select count(*) from pg_proc where pronamespace='ops'::regnamespace
          and proname='engineering_envelope_is_executable')<>1
     or to_regprocedure('ops.engineering_envelope_is_executable(uuid,uuid)') is null then
    raise exception '0335 FAILED: Engineering overload catalog is not exact';
  end if;

  for fn in select unnest(array[
    'ops.engineering_envelope_is_executable(uuid,uuid)',
    'ops.engineering_safe_timestamptz(text)',
    'ops.capability_agent_session_lease_immutable()',
    'ops.guard_engineering_actor_authority_update()',
    'ops.engineering_receipt_exact_object(jsonb,text[])',
    'ops.engineering_receipt_identifier_array(jsonb)',
    'ops.engineering_work_request_currentness_guard()',
    'ops.guard_engineering_job_definition_currentness_update()',
    'ops.engineering_receipt_identifier_sets_equal(jsonb,jsonb)',
    'ops.engineering_receipt_evidence_array(jsonb)',
    'ops.guard_engineering_reviewer_fact_insert()',
    'ops.guard_engineering_envelope_supersession()',
    'ops.guard_engineering_session_terminalization()',
    'ops.guard_siep_engineering_evidence_binding()',
    'ops.siep_bind_evidence_job_unchecked_0324(text,integer,text,uuid,uuid)',
    'ops.siep_current_evidence_digest(text,uuid)',
    'ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)'])
  loop

    if exists (
         select 1 from pg_proc p
         cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
          where p.oid=fn::regprocedure and acl.grantee=0 and acl.privilege_type='EXECUTE')
       or has_function_privilege('carr_reader',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_writer',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_jobs',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_authority',fn::regprocedure,'EXECUTE') then
      raise exception '0335 FAILED: private Engineering function is executable by a runtime role: %',fn;
    end if;
  end loop;

  for fn in select unnest(array[
    'ops.engineering_claim_slice(text,integer,integer)',
    'ops.engineering_controller_binding(uuid,uuid,uuid)',
    'ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)',
    'ops.engineering_fail_claim(uuid,uuid,text,text)',
    'ops.engineering_retire_permanently_ineligible_jobs()'])
  loop
    if exists (
         select 1 from pg_proc p
         cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
          where p.oid=fn::regprocedure and acl.grantee=0 and acl.privilege_type='EXECUTE')
       or has_function_privilege('carr_reader',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_writer',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_authority',fn::regprocedure,'EXECUTE')
       or not has_function_privilege('carr_jobs',fn::regprocedure,'EXECUTE') then
      raise exception '0335 FAILED: scoped Engineering controller ACL is widened or incomplete: %',fn;
    end if;
  end loop;

  if exists (
       select 1 from pg_proc p
       cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
        where p.oid='ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure
          and acl.grantee=0 and acl.privilege_type='EXECUTE')
     or not has_function_privilege('carr_reader',
          'ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_writer',
          'ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_jobs',
          'ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_authority',
          'ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
          'ops.claim_job(text,integer,integer)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
          'ops.claim_job_mode(text,text,integer,integer)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
          'ops.heartbeat_job(uuid,uuid,integer)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
          'ops.complete_job(uuid,uuid,jsonb,text)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
          'ops.fail_job(uuid,uuid,text,text)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
          'ops.timeout_job(uuid,uuid,text)'::regprocedure,'EXECUTE')
     or has_table_privilege('carr_jobs','ops.work_request','SELECT')
     or has_table_privilege('carr_jobs','ops.job','UPDATE')
     or has_table_privilege('carr_jobs','ops.job_attempt','INSERT') then
    raise exception '0335 FAILED: Engineering controller least-privilege boundary widened or is incomplete';
  end if;
  if exists (
       select 1 from pg_proc p
       cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
        where p.oid='ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)'::regprocedure
          and acl.grantee=0 and acl.privilege_type='EXECUTE')
     or has_function_privilege('carr_reader',
          'ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_writer',
          'ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_jobs',
          'ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_authority',
          'ops.siep_bind_evidence_job(text,integer,text,uuid,uuid)'::regprocedure,'EXECUTE') then
    raise exception '0335 FAILED: SIEP evidence binding authority ACL is widened or incomplete';
  end if;

end $$;

commit;
