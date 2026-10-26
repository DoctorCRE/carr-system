-- rollback: forward-only; restores 0982's body if replayed, which only re-admits a receiptless decline on a reused key
-- lock-review: replace one function without rewriting or locking business tables
-- 0986_decline_requires_its_receipt.sql
-- Review follow-up on 0982. The withdrawal receipt insert used ON CONFLICT DO
-- NOTHING and the update ran regardless. Sourced rows are protected by their
-- immutability trigger, but WR-AI program rows have no receipt-checking trigger,
-- so a reused idempotency key could decline one with no receipt for that
-- transition. A conflict now refuses by name and nothing changes. Replays of
-- the same verb call are answered from tool_call before this function runs.

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
  v_receipts integer;
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
  get diagnostics v_receipts = row_count;
  if v_receipts <> 1 then
    raise exception 'withdrawal_receipt_conflict: idempotency key % already holds a withdrawal receipt; nothing was declined',
      p_idempotency_key;
  end if;

  update ops.work_request w
     set state = 'declined', exit_reason = p_exit_reason, closed_at = v_now,
         version = w.version + 1, updated_at = v_now
   where w.id = v_id;

  return query
    select w.ref, w.state, w.version, w.exit_reason, w.closed_at
      from ops.work_request w where w.id = v_id;
end;
$fn$;
