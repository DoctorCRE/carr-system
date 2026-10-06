-- 0383_control_plane_not_configured_state.sql
-- A deterministic entrypoint exiting 78 (EX_CONFIG) is this repo's own
-- convention for "ran, found a credential or setting it needs absent, wrote
-- nothing, and said so" -- bin/nightly.sh and every other launchd chain
-- already read that as SKIP, never FAIL. tools/control-plane.py's run_once
-- did not: it treated every nonzero return the same way, so a 78 burned the
-- job's whole retry budget on a config gap no retry could ever close and
-- landed it in dead_lettered next to a real broken workflow. Measured
-- 2026-08-27: notes-sweep-hourly's canary exits 78 every window because
-- ~/.config/carr/notes-canary.env has never existed on this Mac, and the
-- ledger recorded that as a broken workflow.
--
-- Rule 88e9b5eb: "not authorized" and "not possible" are different findings
-- and must never be reported as the same one. This closes that gap the same
-- way 0158 closed timeout-vs-failure: a distinct terminal state and a
-- distinct receipt kind, honest and separable from both succeeded and
-- failed/timed_out/dead_lettered -- never routed through retry_wait, because
-- retrying a missing credential changes nothing until a human resolves it.

begin;

alter table ops.job drop constraint job_state_check;
alter table ops.job add constraint job_state_check
  check (state in ('queued','running','retry_wait','waiting_approval','succeeded',
                   'failed','timed_out','cancelled','dead_lettered','skipped'));

alter table ops.job drop constraint terminal_job_has_ended;
alter table ops.job add constraint terminal_job_has_ended
  check (state not in ('succeeded','failed','timed_out','cancelled','dead_lettered','skipped')
         or ended_at is not null);

alter table ops.job_attempt drop constraint job_attempt_state_check;
alter table ops.job_attempt add constraint job_attempt_state_check
  check (state in ('running','succeeded','failed','timed_out','cancelled','skipped'));

alter table ops.job_receipt drop constraint job_receipt_kind_check;
alter table ops.job_receipt add constraint job_receipt_kind_check
  check (kind in ('completion','failure','timeout','dead_letter','approval','override','skipped'));

create or replace function ops.skip_job(
  p_job_id uuid, p_lease_token uuid, p_detail text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  -- NOT A FAILURE. This is terminal on the attempt that just ran, always --
  -- never the fail_job/timeout_job branch that checks attempt < max_attempts
  -- and routes back through retry_wait. A missing credential does not
  -- self-heal on a timer, so spending retry budget on it only delays the
  -- honest dead_lettered outcome a real failure deserves, and this job never
  -- reaches dead_lettered for this reason at all.
  --
  -- last_failure_class/last_failure_detail are deliberately left untouched:
  -- those columns are read as failure evidence elsewhere (0308's health
  -- view), and a not-configured run is not that. The message lives only in
  -- the immutable 'skipped' receipt below, where a reader has to ask for it
  -- by name instead of tripping over it in a failure surface.
  update ops.job_attempt set state='skipped',ended_at=now(),detail=p_detail
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state='skipped',ended_at=now(),
         lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
   where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,'skipped',concat('skipped:',j.id,':',j.attempt),
           jsonb_build_object('detail',p_detail));
  return 'skipped';
end $$;

revoke all on function ops.skip_job(uuid,uuid,text) from public;
grant execute on function ops.skip_job(uuid,uuid,text) to carr_jobs;

commit;

do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conrelid='ops.job'::regclass and conname='job_state_check'
       and pg_get_constraintdef(oid) like '%skipped%'
  ) then
    raise exception '0383 FAILED: ops.job state check does not admit skipped';
  end if;
  if not exists (
    select 1 from pg_constraint
     where conrelid='ops.job'::regclass and conname='terminal_job_has_ended'
       and pg_get_constraintdef(oid) like '%skipped%'
  ) then
    raise exception '0383 FAILED: terminal_job_has_ended does not cover skipped';
  end if;
  if not exists (
    select 1 from pg_constraint
     where conrelid='ops.job_attempt'::regclass and conname='job_attempt_state_check'
       and pg_get_constraintdef(oid) like '%skipped%'
  ) then
    raise exception '0383 FAILED: ops.job_attempt state check does not admit skipped';
  end if;
  if not exists (
    select 1 from pg_constraint
     where conrelid='ops.job_receipt'::regclass and conname='job_receipt_kind_check'
       and pg_get_constraintdef(oid) like '%skipped%'
  ) then
    raise exception '0383 FAILED: ops.job_receipt kind check does not admit skipped';
  end if;
  if to_regprocedure('ops.skip_job(uuid,uuid,text)') is null then
    raise exception '0383 FAILED: ops.skip_job is missing';
  end if;
  if not has_function_privilege('carr_jobs','ops.skip_job(uuid,uuid,text)'::regprocedure,'execute') then
    raise exception '0383 FAILED: carr_jobs lacks execute on ops.skip_job';
  end if;
end $$;
