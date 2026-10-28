-- rollback: forward-only — reaped run rows and board-publish events are gone; drop function ops.reap_expired_runs(integer) and index ops.run_expires_at_idx to undo the shape
-- expand-contract: contract — bounded batched deletes of expired ops.run rows and week-old publish-board-snapshot events; their readers were checked first (WR573.S3.J3)
-- lock-review: ops.run is ~140k rows / 61 MB; a plain index build takes seconds under a write-blocking lock, and the deletes run in 5,000-row batches
-- 1038_reap_expired_runs.sql
--
-- ops.run is appended to by tools/ops-record.py on every job and check step and
-- nothing ever removed a row. ops.reap_expired_runs is the one retention rule,
-- applied to one run key at a time so each call reads only that key's rows:
-- tools/ops-record.py calls it for the key it just recorded, and this migration
-- calls it in batches for every key to clear the backlog. A key that never runs
-- again keeps what it has; it no longer grows.
--
-- A row is reaped only when every reader that could still want it has a newer
-- answer. It must be past its own expiry by 14 days (tools/health-check.py reads
-- 14 days of attempt starts), and it must not be:
--   * the newest row of its (service, environment, kind, run_key, state): the
--     schedule board, lead-automation last_run, v_service_environment_health and
--     bin/monthly-gate.py read the latest row of a kind;
--   * among the newest three rows of its (service, environment, kind, run_key):
--     ops.clear_recovered_incident and ops-record's healthy streak read three;
--   * cited by an incident link, an incident's recovery evidence, a Work Request
--     or defect receipt, or a release receipt's recovery run (ON DELETE RESTRICT).
--
-- The board publish event is no longer written (board-answers.js): the board
-- version number is its own audit trail and no reader filters on that verb.

create index if not exists run_expires_at_idx on ops.run (expires_at) where expires_at is not null;

create or replace function ops.reap_expired_runs(
  p_service_id uuid, p_environment text, p_run_key text, p_limit integer default 500)
returns integer
language sql security definer
set search_path to pg_catalog, pg_temp as $fn$
  with ranked as (
    select r.id, r.expires_at,
           row_number() over (partition by r.kind order by r.observed_at desc, r.id desc) as key_rank,
           row_number() over (partition by r.kind, r.state order by r.observed_at desc, r.id desc) as state_rank
      from ops.run r
     where r.service_id = p_service_id and r.environment = p_environment and r.run_key = p_run_key),
  cited as (
    select lower(l.ref) as id from ops.incident_link l where l.kind = 'run'
    union all select substring(i.recovery_evidence_ref from 9) from ops.incident i
     where i.recovery_evidence_ref like 'ops.run:%'
    union all select substring(e.receipt_ref from 9) from ops.work_request_criterion_evidence e
     where e.receipt_ref like 'ops.run:%'
    union all select substring(v.evidence_ref from 9) from public.defect_resolution v
     where v.evidence_ref like 'ops.run:%'
    union all select a.recovery_run_id::text from ops.release_approval_receipt a
    union all select b.recovery_run_id::text from ops.release_readiness_receipt b),
  doomed as (
    select x.id from ranked x
     where x.expires_at < now() - interval '14 days'
       and x.key_rank > 3 and x.state_rank > 1
       and x.id::text not in (select c.id from cited c where c.id is not null)
     order by x.expires_at
     limit greatest(coalesce(p_limit, 500), 0)),
  gone as (
    delete from ops.run d using doomed where d.id = doomed.id returning 1)
  select count(*)::integer from gone;
$fn$;

comment on function ops.reap_expired_runs(uuid, text, text, integer) is
  'Delete up to p_limit runs of one (service, environment, run_key) that expired over 14 days ago and that no reader still needs: not the newest of their kind and state, not among the newest three of their kind, and not cited as evidence. Returns the number deleted.';

revoke all on function ops.reap_expired_runs(uuid, text, text, integer) from public;
grant execute on function ops.reap_expired_runs(uuid, text, text, integer) to carr_jobs;

do $$
declare
  v_key record;
  v_deleted integer;
begin
  for v_key in select distinct r.service_id, r.environment, r.run_key
                 from ops.run r where r.expires_at < now() - interval '14 days' loop
    loop
      v_deleted := ops.reap_expired_runs(v_key.service_id, v_key.environment, v_key.run_key, 5000);
      exit when v_deleted < 5000;
    end loop;
  end loop;

  loop
    delete from public.event e
     where e.id in (select x.id from public.event x
                     where x.verb = 'publish-board-snapshot'
                       and x.occurred_at < now() - interval '7 days'
                     limit 5000);
    get diagnostics v_deleted = row_count;
    exit when v_deleted = 0;
  end loop;
end
$$;
