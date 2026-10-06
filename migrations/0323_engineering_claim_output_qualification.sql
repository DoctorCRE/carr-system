-- 0323_engineering_claim_output_qualification.sql
--
-- Repair the first live Engineering Passport claim path without rewriting the
-- frozen 0310 migration.  The RETURNS TABLE output `job_id` is a PL/pgSQL
-- variable.  0310's unqualified `returning job_id` in the attempt CTE is
-- therefore ambiguous at execution time, so PostgreSQL rolls the entire claim
-- statement back before it can lease a job.  Qualify the INSERT target's
-- returned column; no selection, lease, or privilege semantics change.

begin;

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
  if btrim(coalesce(p_worker,''))='' or p_limit < 1 or p_lease_seconds < 1 then
    raise exception 'worker, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id
      from ops.job j
      join ops.job_definition d
        on d.key=j.definition_key and d.version=j.definition_version
      join ops.engineering_execution_envelope e on e.job_id=j.id
     where d.enabled and j.definition_key='engineering-slice'
       and j.definition_version=1 and j.state in ('queued','retry_wait')
       and j.next_attempt_at <= now()
     order by j.scheduled_for,j.created_at
     for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set
      state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),
      leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
    from candidate c where j.id=c.id
    returning j.*
  ), attempts as (
    insert into ops.job_attempt as claimed_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning claimed_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c
    join ops.job_definition d on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.job_id=c.id;
end $$;

revoke all on function ops.engineering_claim_slice(text,integer,integer) from public;
grant execute on function ops.engineering_claim_slice(text,integer,integer) to carr_jobs;

do $$
begin
  if not has_function_privilege('carr_jobs',
       'ops.engineering_claim_slice(text,integer,integer)'::regprocedure, 'EXECUTE') then
    raise exception '0323 FAILED: carr_jobs cannot execute the repaired Engineering claim function';
  end if;
  if exists (
    select 1
      from pg_proc p
      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
     where p.oid='ops.engineering_claim_slice(text,integer,integer)'::regprocedure
       and acl.grantee=0 and acl.privilege_type='EXECUTE'
  ) then
    raise exception '0323 FAILED: repaired Engineering claim function is PUBLIC executable';
  end if;
end $$;

commit;
