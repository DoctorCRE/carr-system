-- 0325_engineering_claim_envelope_eligibility.sql
--
-- Make executable-envelope eligibility one database-owned predicate used at
-- every controller boundary. The original claim joined any envelope for a
-- queued job, so expired/read-only/superseded generations could receive a
-- lease. Receipt persistence must decide again because an envelope or its
-- agent session may become terminal while Codex is working.

begin;

create or replace function ops.engineering_envelope_is_executable(
  p_envelope_id uuid, p_job_id uuid, p_minimum_remaining_seconds integer default 60
) returns boolean
language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare lineage_plan uuid;
        lineage_slice text;
begin
  if p_minimum_remaining_seconds < 0 then return false; end if;
  select e.slice_plan_id,e.slice_ref into lineage_plan,lineage_slice
    from ops.engineering_execution_envelope e
   where e.id=p_envelope_id and e.job_id=p_job_id;
  if not found then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || lineage_plan::text || ':' || lineage_slice,0));
  return exists (
    select 1
      from ops.engineering_execution_envelope e
      join ops.job j on j.id=e.job_id
      join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
      join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
      join ops.work_request w on w.id=e.work_request_id
      join ops.capability_agent_session s on s.id=e.agent_session_id
      join public.actor a on a.id=s.executor_actor_id
      cross join lateral (select ops.engineering_admission_source(w.ref) as source) current_source
     where e.id=p_envelope_id and j.id=p_job_id
       and d.enabled and j.definition_key='engineering-slice'
       and j.definition_version=1 and j.mode='shadow'
       and j.payload->>'work_request'=w.ref
       and j.payload->>'slice_ref'=e.slice_ref
       and j.payload->>'plan_digest'=sp.plan_digest
       and j.payload->>'generation' ~ '^[1-9][0-9]*$'
       and current_source.source is not null
       and current_source.source->'work_request'->>'id'='wr:' || e.work_request_id::text
       and (current_source.source->'work_request'->>'version')::integer=e.state_version
       and current_source.source->'work_request'->>'canonical_record_digest'=e.canonical_record_digest
       and current_source.source->'accepted_plan'->>'record_id'=e.accepted_plan_id::text
       and current_source.source->'accepted_plan'->>'digest'=sp.accepted_plan_hash
       and sp.work_request_id=e.work_request_id
       and sp.accepted_plan_id=e.accepted_plan_id
       and sp.work_request_version=e.state_version
       and s.work_request_id=e.work_request_id
       and s.state not in ('completed','cancelled')
       and a.active and a.kind='automation' and a.slug='codex'
       -- The packet's expiry is caller-controlled JSON.  Parse it only through
       -- PostgreSQL's non-throwing validator, then bind it exactly to the
       -- immutable expiry column before any lease or attempt can be created.
       and case when pg_input_is_valid(e.envelope->>'expires_at','timestamp with time zone')
                then (e.envelope->>'expires_at')::timestamptz=e.expires_at
                else false end
       and e.expires_at>statement_timestamp()+make_interval(secs=>p_minimum_remaining_seconds)
       and (j.state<>'running' or (j.lease_token is not null
            and j.leased_until>statement_timestamp()
            and e.expires_at>j.leased_until+make_interval(secs=>p_minimum_remaining_seconds)))
       and e.envelope->'server_binding'->'authority'->>'read_only'='false'
       and e.envelope->'server_binding'->'authority'->>'capability_profile'=
           'capability:engineering-repository-write'
       and e.envelope->'server_binding'->'adapter'->>'surface'='codex_desktop'
       and e.envelope->'request'->'allowed_actions'=
           '["repository:create-worktree","repository:create-branch","repository:write-declared-scope","repository:run-checks","repository:commit","repository:push-branch","repository:open-pr"]'::jsonb
       and not exists (select 1 from ops.engineering_execution_envelope successor
                        where successor.supersedes_envelope_id=e.id)
       and not exists (select 1 from ops.engineering_slice_receipt receipt
                        where receipt.envelope_id=e.id)
  );
end $$;

create or replace function ops.engineering_finalize_slice_receipt(
  p_envelope_id uuid,p_lease_token uuid,p_receipt jsonb,
  p_receipt_digest text,p_executor_actor_id uuid
) returns ops.engineering_slice_receipt
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype;
        row ops.engineering_slice_receipt%rowtype;
        terminal_state text;
begin
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  select * into row from ops.engineering_record_slice_receipt(
    p_envelope_id,p_lease_token,p_receipt,p_receipt_digest,p_executor_actor_id);
  if row.outcome='claimed_complete' then
    if not ops.complete_job(e.job_id,p_lease_token,
      jsonb_build_object('engineering_receipt_id',row.id,'receipt_digest',p_receipt_digest),
      'engineering:' || row.id::text) then
      raise exception 'engineering completion did not transition the claimed job';
    end if;
  else
    terminal_state:=ops.fail_job(e.job_id,p_lease_token,'engineering_' || row.outcome,
      'typed engineering receipt reported non-complete outcome');
    if terminal_state not in ('retry_wait','dead_lettered') then
      raise exception 'engineering failure did not transition the claimed job';
    end if;
  end if;
  return row;
end $$;

create or replace function ops.engineering_claim_slice(
  p_worker text, p_limit integer default 1, p_lease_seconds integer default 1800
) returns table (
  job_id uuid, lease_token uuid, definition_key text, definition_version integer,
  payload jsonb, execution_kind text, execution_contract jsonb,
  attempt integer, timeout_seconds integer, mode text
)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_limit is distinct from 1
     or p_lease_seconds is null or p_lease_seconds<1 then
    raise exception 'worker, exactly one claim and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id
      from ops.job j
      join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
      join ops.engineering_execution_envelope e on e.job_id=j.id
     where d.enabled and j.definition_key='engineering-slice'
       and j.definition_version=1 and j.state in ('queued','retry_wait')
       and j.next_attempt_at<=now() and j.attempt<j.max_attempts
       and ops.engineering_envelope_is_executable(e.id,j.id,p_lease_seconds+60)
     order by j.scheduled_for,j.created_at
     for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set
      state='running',attempt=j.attempt+1,lease_owner=p_worker,lease_token=gen_random_uuid(),
      leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
    from candidate c where j.id=c.id returning j.*
  ), attempts as (
    insert into ops.job_attempt as claimed_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning claimed_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c join ops.job_definition d
      on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.job_id=c.id;
end $$;

-- Session terminalization must serialize with the same lineage lock used by
-- the executable predicate.  Once a controller has a live lease, cancellation
-- or completion is deferred rather than creating a binding-to-launch gap.
create or replace function ops.guard_engineering_session_terminalization()
returns trigger language plpgsql set search_path=pg_catalog,ops,public
as $$
declare lineage record;
begin
  if new.state not in ('completed','cancelled') or new.state=old.state then
    return new;
  end if;
  for lineage in
    select distinct e.slice_plan_id,e.slice_ref
     from ops.engineering_execution_envelope e
     where e.agent_session_id=old.id
     order by e.slice_plan_id,e.slice_ref
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      'engineering-envelope:' || lineage.slice_plan_id::text || ':' || lineage.slice_ref,0));
    if exists (
      select 1 from ops.engineering_execution_envelope e
      join ops.job j on j.id=e.job_id
       where e.agent_session_id=old.id and e.slice_plan_id=lineage.slice_plan_id
         and e.slice_ref=lineage.slice_ref and j.state='running'
         and j.lease_token is not null and j.leased_until>statement_timestamp()
    ) then
      raise exception 'engineering session terminalization deferred while its dispatch lease is live';
    end if;
  end loop;
  return new;
end $$;

drop trigger if exists engineering_session_terminalization_guard on ops.capability_agent_session;
create trigger engineering_session_terminalization_guard
before update of state on ops.capability_agent_session
for each row execute function ops.guard_engineering_session_terminalization();
revoke all on function ops.guard_engineering_session_terminalization()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

revoke all on function ops.engineering_controller_binding(uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
drop function ops.engineering_controller_binding(uuid,uuid);

create function ops.engineering_controller_binding(
  p_envelope_id uuid,p_job_id uuid,p_lease_token uuid
) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare binding jsonb;
begin
  if p_lease_token is null
     or not ops.engineering_envelope_is_executable(p_envelope_id,p_job_id) then return null; end if;
  select jsonb_build_object(
    'envelope_id',e.id::text,'envelope_digest',e.envelope_digest,
    'slice_ref',e.slice_ref,'plan_digest',sp.plan_digest,'slice_plan',sp.plan,
    'executor_actor',jsonb_build_object('id',a.id::text,'slug',a.slug),
    'agent_session_id',s.id::text
  ) into binding
    from ops.engineering_execution_envelope e
    join ops.job j on j.id=e.job_id
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
    join ops.capability_agent_session s on s.id=e.agent_session_id
    join public.actor a on a.id=s.executor_actor_id
   where e.id=p_envelope_id and e.job_id=p_job_id and j.state='running'
     and j.lease_token=p_lease_token and j.leased_until>statement_timestamp();
  return binding;
end $$;
revoke all on function ops.engineering_controller_binding(uuid,uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.engineering_record_slice_receipt(
  p_envelope_id uuid,p_lease_token uuid,p_receipt jsonb,
  p_receipt_digest text,p_executor_actor_id uuid
) returns ops.engineering_slice_receipt
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype;
        a ops.job_attempt%rowtype;
        session_executor uuid;
        row ops.engineering_slice_receipt%rowtype;
begin
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  if not ops.engineering_envelope_is_executable(e.id,e.job_id) then
    raise exception 'engineering envelope is no longer executable';
  end if;
  select executor_actor_id into session_executor from ops.capability_agent_session
   where id=e.agent_session_id;
  if session_executor is null or p_executor_actor_id is distinct from session_executor then
    raise exception 'engineering receipt executor is not the server-bound agent session';
  end if;
  select attempt_row.* into a from ops.job_attempt attempt_row
    join ops.job j on j.id=attempt_row.job_id
   where attempt_row.job_id=e.job_id and attempt_row.attempt=j.attempt
     and attempt_row.lease_token=p_lease_token and attempt_row.state='running'
     and j.state='running' and j.lease_token=p_lease_token
     and j.leased_until>statement_timestamp() for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  if p_receipt->>'envelope_digest'<>e.envelope_digest
     or p_receipt->>'slice_ref'<>e.slice_ref
     or p_receipt->>'attempt_id'<>('attempt:' || a.attempt)
     or p_receipt->>'outcome' not in ('claimed_complete','failed','blocked','reopened') then
    raise exception 'engineering receipt is not bound to the claimed envelope';
  end if;
  insert into ops.engineering_slice_receipt
    (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,
     executor_actor_id,receipt_digest,outcome,receipt)
  values (a.id,e.id,e.work_request_id,e.slice_ref,p_receipt->>'attempt_id',
          session_executor,p_receipt_digest,p_receipt->>'outcome',p_receipt)
  returning * into row;
  return row;
end $$;

create or replace function ops.guard_engineering_envelope_supersession()
returns trigger language plpgsql set search_path=pg_catalog,ops,public
as $$
declare prior ops.engineering_execution_envelope%rowtype;
        prior_count integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || new.slice_plan_id::text || ':' || new.slice_ref,0));
  select count(*) into prior_count from ops.engineering_execution_envelope
   where slice_plan_id=new.slice_plan_id and slice_ref=new.slice_ref;
  if prior_count=0 then
    if new.supersedes_envelope_id is not null then
      raise exception 'first engineering envelope cannot supersede another envelope';
    end if;
    return new;
  end if;
  if new.supersedes_envelope_id is null then
    raise exception 'later engineering envelope must name its immutable predecessor';
  end if;
  select * into prior from ops.engineering_execution_envelope where id=new.supersedes_envelope_id;
  if not found or prior.slice_plan_id<>new.slice_plan_id or prior.slice_ref<>new.slice_ref
     or prior.accepted_plan_id<>new.accepted_plan_id or prior.work_request_id<>new.work_request_id then
    raise exception 'engineering envelope predecessor is outside the exact slice binding';
  end if;
  if exists (select 1 from ops.engineering_execution_envelope
              where supersedes_envelope_id=prior.id) then
    raise exception 'engineering envelope predecessor already has a successor';
  end if;
  if exists (select 1 from ops.job j where j.id=prior.job_id and j.state='running'
              and j.lease_token is not null and j.leased_until>now()) then
    raise exception 'leased engineering envelope cannot be superseded';
  end if;
  if prior.expires_at>now()
     and coalesce((prior.envelope->'server_binding'->'authority'->>'read_only')::boolean,true)=false
     and exists (select 1 from ops.capability_agent_session s where s.id=prior.agent_session_id
                   and s.state not in ('completed','cancelled'))
     and not exists (select 1 from ops.engineering_slice_receipt r
                      where r.envelope_id=prior.id and r.outcome in ('failed','blocked','reopened')) then
    raise exception 'current executable engineering envelope cannot be superseded';
  end if;
  return new;
end $$;

revoke all on function ops.engineering_envelope_is_executable(uuid,uuid,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_claim_slice(text,integer,integer) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.guard_engineering_envelope_supersession() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.engineering_claim_slice(text,integer,integer),
  ops.engineering_controller_binding(uuid,uuid,uuid),
  ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid) to carr_jobs;

do $$
declare fn text;
begin
  for fn in select unnest(array[
    'ops.engineering_envelope_is_executable(uuid,uuid,integer)',
    'ops.engineering_claim_slice(text,integer,integer)',
    'ops.guard_engineering_session_terminalization()',
    'ops.engineering_controller_binding(uuid,uuid,uuid)',
    'ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)',
    'ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)',
    'ops.guard_engineering_envelope_supersession()'])
  loop
    if exists (select 1 from pg_proc p cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
                where p.oid=fn::regprocedure and acl.grantee=0 and acl.privilege_type='EXECUTE')
       or has_function_privilege('carr_reader',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_writer',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_authority',fn::regprocedure,'EXECUTE') then
      raise exception '0325 FAILED: public or forbidden role can execute %',fn;
    end if;
  end loop;
  for fn in select unnest(array[
    'ops.engineering_envelope_is_executable(uuid,uuid,integer)',
    'ops.guard_engineering_session_terminalization()',
    'ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)',
    'ops.guard_engineering_envelope_supersession()'])
  loop
    if has_function_privilege('carr_jobs',fn::regprocedure,'EXECUTE') then
      raise exception '0325 FAILED: carr_jobs can execute private %',fn;
    end if;
  end loop;
  if has_function_privilege('carr_jobs',
       'ops.engineering_envelope_is_executable(uuid,uuid,integer)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
       'ops.engineering_claim_slice(text,integer,integer)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
       'ops.engineering_controller_binding(uuid,uuid,uuid)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
       'ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_jobs',
       'ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_reader',
       'ops.engineering_controller_binding(uuid,uuid,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_writer',
       'ops.engineering_controller_binding(uuid,uuid,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_authority',
       'ops.engineering_controller_binding(uuid,uuid,uuid)'::regprocedure,'EXECUTE')
     or exists (
       select 1 from pg_proc p
       cross join lateral aclexplode(coalesce(p.proacl, acldefault('f',p.proowner))) acl
        where p.oid='ops.engineering_controller_binding(uuid,uuid,uuid)'::regprocedure
          and acl.grantee=0 and acl.privilege_type='EXECUTE')
     or has_table_privilege('carr_jobs','ops.work_request','SELECT')
     or has_table_privilege('carr_jobs','ops.job','UPDATE')
     or has_table_privilege('carr_jobs','ops.job_attempt','INSERT') then
    raise exception '0325 FAILED: Engineering controller least-privilege boundary widened or is incomplete';
  end if;
end $$;

commit;
