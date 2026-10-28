-- rollback: forward-only — reaped run rows are gone; drop function ops.reap_expired_runs(uuid, text, text, integer) and index ops.run_expires_at_idx to undo the shape
-- expand-contract: contract — bounded batched deletes of expired ops.run rows; preserve every event and reader-visible run (WR573.S3.J3)
-- lock-review: ops.run is ~140k rows / 61 MB; the runner permits only transactional DDL, so this index uses its write-blocking lock, and the deletes run in 1,000-row batches
-- 1051_reap_expired_runs.sql
--
-- ops.run is appended to by tools/ops-record.py on every job and check step and
-- nothing ever removed a row. ops.reap_expired_runs is the one retention rule,
-- applied to one run key at a time so each call reads only that key's rows:
-- tools/ops-record.py calls it for the key it just recorded, and this migration
-- calls it in batches for every key to clear the backlog. A key that never runs
-- again keeps what it has; it no longer grows.
--
-- A row must be terminal, expired by a day, and started over 14 days ago
-- (tools/health-check.py reads 14 days of attempt starts). It must not be:
--   * the newest row of its (service, environment, kind, run_key, state): the
--     schedule board, lead-automation last_run, v_service_environment_health and
--     bin/monthly-gate.py read the latest row of a kind. Preserve both
--     observed_at and ended_at orderings because spool writes can arrive late;
--   * among the newest three succeeded/failed/timed_out rows across kinds,
--     the sequence ops.clear_recovered_incident and _healthy_streak read;
--   * cited by an incident link, an incident's recovery evidence, a Work Request
--     or defect receipt, or a release receipt's recovery run (ON DELETE RESTRICT).
--
-- Publication events remain durable history for Doc Activity. Reap only run
-- observations outside the retention windows and protected reader selections.
-- The runner requires one transaction, so CREATE INDEX CONCURRENTLY is not
-- supported here. Its lock and timeout are enforced by the migration runner.

create index if not exists run_expires_at_idx on ops.run (expires_at) where expires_at is not null;

create or replace function ops.reap_expired_runs(
  p_service_id uuid, p_environment text, p_run_key text, p_limit integer default 1000)
returns integer
language sql security definer
set search_path to pg_catalog, pg_temp as $fn$
  with ranked as (
    select r.id, r.expires_at, r.started_at, r.observed_at, r.state,
           row_number() over (partition by r.kind, r.state order by r.observed_at desc, r.id desc) as state_rank,
           row_number() over (partition by r.kind, r.state order by r.ended_at desc nulls last, r.id desc) as completion_rank,
           count(*) filter (where r.state in ('succeeded','failed','timed_out')) over
             (order by r.observed_at desc, r.id desc rows unbounded preceding) as health_rank
      from ops.run r
     where r.service_id = p_service_id and r.environment = p_environment and r.run_key = p_run_key),
  cited as (
    select lower(l.ref) as id from ops.incident_link l where l.kind = 'run'
    union all select lower(substring(i.recovery_evidence_ref from 9)) from ops.incident i
     where i.recovery_evidence_ref like 'ops.run:%'
    union all select lower(substring(e.receipt_ref from 9)) from ops.work_request_criterion_evidence e
     where e.receipt_ref like 'ops.run:%'
    union all select lower(substring(v.evidence_ref from 9)) from public.defect_resolution v
     where v.evidence_ref like 'ops.run:%'
    union all select a.recovery_run_id::text from ops.release_approval_receipt a
    union all select b.recovery_run_id::text from ops.release_readiness_receipt b),
  doomed as (
    select x.id from ranked x
     where x.expires_at < now() - interval '1 day'
       and coalesce(x.started_at, x.observed_at) < now() - interval '14 days'
       and x.state in ('succeeded', 'failed', 'timed_out', 'cancelled', 'skipped', 'stale')
       and x.state_rank > 1 and x.completion_rank > 1
       and (x.state not in ('succeeded','failed','timed_out') or x.health_rank > 3)
       and x.id::text not in (select c.id from cited c where c.id is not null)
     order by x.expires_at
     limit least(greatest(coalesce(p_limit, 1000), 0), 1000)),
  gone as (
    delete from ops.run d using doomed where d.id = doomed.id returning 1)
  select count(*)::integer from gone;
$fn$;

comment on function ops.reap_expired_runs(uuid, text, text, integer) is
  'Delete at most 1000 terminal runs of one (service, environment, run_key) that expired over a day ago and started over 14 days ago and that no reader still needs: not the newest of their kind and state, not among the newest three health-state rows, and not cited as evidence. Returns the number deleted.';

revoke all on function ops.reap_expired_runs(uuid, text, text, integer) from public;
grant execute on function ops.reap_expired_runs(uuid, text, text, integer) to carr_jobs;

do $contract$
declare
  contract jsonb := ops.scac_current_grant_contract();
  capability jsonb := jsonb_build_object(
    'config', jsonb_build_array('search_path=pg_catalog, pg_temp'),
    'grantee', 'carr_jobs', 'parallel', 'u', 'grantable', false,
    'privilege', 'execute', 'volatility', 'v', 'function_kind', 'f',
    'security_definer', true, 'ingress_kind', 'db_function_acl',
    'signature', 'ops.reap_expired_runs(p_service_id uuid, p_environment text, p_run_key text, p_limit integer)',
    'ingress_key', 'db-function-acl:ops.reap_expired_runs(p_service_id uuid, p_environment text, p_run_key text, p_limit integer):carr_jobs:execute');
begin
  if not (contract->'secdef_execute') @> jsonb_build_array(capability) then
    contract := jsonb_set(contract, '{secdef_execute}',
      (contract->'secdef_execute') || jsonb_build_array(capability));
    execute format(
      'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
      'SELECT ' || quote_literal(contract::text) || '::jsonb');
  end if;
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception '1051 FAILED: reaper grant differs from the exact grant contract';
  end if;
end $contract$;

do $$
declare
  v_key record;
  v_deleted integer;
begin
  for v_key in select distinct r.service_id, r.environment, r.run_key
                 from ops.run r where r.expires_at < now() - interval '1 day' loop
    loop
      v_deleted := ops.reap_expired_runs(v_key.service_id, v_key.environment, v_key.run_key, 1000);
      exit when v_deleted < 1000;
    end loop;
  end loop;

end
$$;
