-- 0334_workflow_mode_ladder.sql
-- ops.enqueue_job accepted any mode with no acceptance check: a workflow could
-- be scheduled straight into canary or live with no evidence it had ever run
-- clean in a lower tier.  This closes that gap inside the one function every
-- enqueue path calls, so a scheduler, a backfill, and a human operator all
-- answer to the same ladder:
--   shadow  -- always permitted.
--   canary  -- requires an accepted shadow acceptance row for this exact
--              workflow_key/version, AND the workflow's own canary contract
--              must be enabled.  A deterministic definition whose
--              execution_contract names canary.enabled=false can never reach
--              canary here, no matter what acceptance evidence exists --
--              matching lib.control_plane.deterministic_args, which already
--              refuses to resolve canary arguments for that same contract.
--   live    -- requires an accepted canary acceptance row, EXCEPT a workflow
--              whose contract explicitly disables canary (canary.enabled is
--              present and false): for that one shape, accepted shadow is the
--              highest evidence the workflow can ever produce, so accepted
--              shadow alone satisfies live.
--   replay  -- unchanged; this migration adds no replay rule.

begin;

create or replace function ops.enqueue_job(
  p_definition_key text,
  p_definition_version integer,
  p_scheduled_for timestamptz,
  p_payload jsonb,
  p_idempotency_key text,
  p_mode text default 'live'
) returns ops.job
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  d ops.job_definition%rowtype;
  j ops.job%rowtype;
  canary_disabled boolean;
begin
  select * into d from ops.job_definition
   where key=p_definition_key and version=p_definition_version and enabled;
  if not found then
    raise exception 'job definition % v% is not enabled',p_definition_key,p_definition_version;
  end if;
  if p_mode not in ('shadow','canary','live','replay') then
    raise exception 'invalid job mode %',p_mode;
  end if;
  -- A missing canary key (every cognition contract, and any deterministic
  -- contract that never named one) is not the same claim as an explicit
  -- canary.enabled=false; only the explicit false is a contractual refusal.
  canary_disabled := (d.execution_contract #>> '{canary,enabled}') = 'false';
  if p_mode='canary' then
    if canary_disabled then
      raise exception 'workflow % cannot enqueue canary mode: canary is contractually disabled for definition v%',
        p_definition_key,p_definition_version;
    end if;
    if not exists (
      select 1 from ops.workflow_acceptance
       where workflow_key=p_definition_key and workflow_version=p_definition_version
         and mode='shadow' and status='accepted'
    ) then
      raise exception 'workflow % cannot enqueue canary mode: no accepted shadow acceptance evidence for definition v%',
        p_definition_key,p_definition_version;
    end if;
  elsif p_mode='live' then
    if canary_disabled then
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key=p_definition_key and workflow_version=p_definition_version
           and mode='shadow' and status='accepted'
      ) then
        raise exception 'workflow % cannot enqueue live mode: canary is contractually disabled and no accepted shadow acceptance evidence exists for definition v%',
          p_definition_key,p_definition_version;
      end if;
    elsif not exists (
      select 1 from ops.workflow_acceptance
       where workflow_key=p_definition_key and workflow_version=p_definition_version
         and mode='canary' and status='accepted'
    ) then
      raise exception 'workflow % cannot enqueue live mode: no accepted canary acceptance evidence for definition v%',
        p_definition_key,p_definition_version;
    end if;
  end if;
  insert into ops.job
    (definition_key,definition_version,idempotency_key,scheduled_for,mode,payload,
     max_attempts,timeout_seconds)
  values
    (d.key,d.version,p_idempotency_key,p_scheduled_for,p_mode,coalesce(p_payload,'{}'::jsonb),
     (d.retry_policy->>'max_attempts')::integer,
     (d.retry_policy->>'timeout_seconds')::integer)
  on conflict do nothing
  returning * into j;
  if j.id is null then
    select * into j from ops.job
     where idempotency_key=p_idempotency_key
        or (definition_key=p_definition_key
            and definition_version=p_definition_version
            and scheduled_for=p_scheduled_for)
     order by (idempotency_key=p_idempotency_key) desc
     limit 1;
    if j.id is null
       or j.definition_key <> p_definition_key
       or j.definition_version <> p_definition_version
       or j.scheduled_for <> p_scheduled_for
       or j.payload <> coalesce(p_payload,'{}'::jsonb)
       or j.mode <> p_mode then
      raise exception 'duplicate delivery conflicts with the canonical scheduled job';
    end if;
  end if;
  return j;
end $$;

commit;

do $$
declare definition text;
begin
  if to_regprocedure('ops.enqueue_job(text,integer,timestamptz,jsonb,text,text)') is null then
    raise exception '0334 FAILED: ops.enqueue_job is missing';
  end if;
  select pg_get_functiondef('ops.enqueue_job(text,integer,timestamptz,jsonb,text,text)'::regprocedure)
    into definition;
  if definition not like '%canary_disabled%'
     or definition not like '%cannot enqueue canary mode%'
     or definition not like '%cannot enqueue live mode%'
     or definition not like '%no accepted shadow acceptance evidence%'
     or definition not like '%no accepted canary acceptance evidence%' then
    raise exception '0334 FAILED: ops.enqueue_job does not enforce the workflow mode ladder';
  end if;
  -- The original duplicate-delivery reconciliation and unmodified base body
  -- must still be present so this migration is additive, not a rewrite.
  if definition not like '%duplicate delivery conflicts with the canonical scheduled job%' then
    raise exception '0334 FAILED: ops.enqueue_job lost its duplicate-delivery reconciliation';
  end if;
  if not has_function_privilege('carr_jobs',
       'ops.enqueue_job(text,integer,timestamptz,jsonb,text,text)'::regprocedure,'execute') then
    raise exception '0334 FAILED: carr_jobs lost enqueue_job execute privilege';
  end if;
end $$;
