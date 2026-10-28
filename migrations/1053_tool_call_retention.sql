-- rollback: forward-only for the deleted rows (expired replay receipts and retired ask-jev-attempt rows; no reader remains). To stop retention: drop trigger tool_call_expire on public.tool_call; drop function public.tool_call_expire(); drop index public.tool_call_expiry_idx.
-- expand-contract: contract — deletes tool_call rows older than 30 days and every retired ask-jev-attempt row (one DELETE each); from this release the server replays only receipts inside the 30-day window (durable receipts excepted).
-- lock-review: public.tool_call holds ~465k rows (1 GB with TOAST). The runner wraps this file in one transaction, so verb writes wait for the whole migration: the non-concurrent partial index build (SHARE lock) plus two deletes removing ~311k rows (one sequential scan for the verb, the new index for age). Rehearsed at production row counts in ~6 s.
-- WR573.S1.J1 (database audit 2026-10-28, finding F1): tool_call is the
-- idempotency replay ledger. It kept every write's response forever. A retry
-- now replays for 30 days (mcp-server/src/replay-receipt.ts REPLAY_WINDOW);
-- after that the key is unknown and the write runs as new.

-- Durable receipts are read back by key and never expire: RW02 page-stop and
-- loop-episode keys (ops.rw02_record_run, ops.rw02_loop_episodes), legacy
-- calcap- calendar captures (catch-me-up), and the Work Request acts whose actor
-- work-request-intake.js joins by key. replay-receipt.ts durableReceipt names
-- the same set for the server's replay lookup. The predicate is spelled out in
-- the index and the trigger alike so the planner matches the partial index.
create index tool_call_expiry_idx on public.tool_call (created_at)
  where not (verb in ('review-and-triage', 'accept-ready-plan', 'accept-outcome-feedback')
       or idempotency_key like 'rw02-%' or idempotency_key like 'calcap-%');

-- One-time cleanup, one plain DELETE per target. ask-jev-attempt rows were the
-- retired Jev gateway's attempt log (0935 dropped its readers; last row
-- 2026-10-19); no verb or function replays or reads them. The verb delete is one
-- sequential scan of the ~225 MB heap (planner cost ~31k), so it gets no index.
delete from public.tool_call where verb = 'ask-jev-attempt';
delete from public.tool_call
 where not (verb in ('review-and-triage', 'accept-ready-plan', 'accept-outcome-feedback')
       or idempotency_key like 'rw02-%' or idempotency_key like 'calcap-%')
   and created_at < now() - interval '30 days';

-- Retention rides the write path: every receipt insert frees an expired row
-- under the same key (so the key can run as new) and deletes up to 20 other
-- expired rows, oldest first. No daemon or job; writers hold no DELETE grant.
create function public.tool_call_expire() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
begin
  delete from public.tool_call t
   where t.idempotency_key = new.idempotency_key
     and not (t.verb in ('review-and-triage', 'accept-ready-plan', 'accept-outcome-feedback')
       or t.idempotency_key like 'rw02-%' or t.idempotency_key like 'calcap-%')
     and t.created_at < now() - interval '30 days';
  delete from public.tool_call t where t.idempotency_key in (
    select o.idempotency_key from public.tool_call o
     where not (o.verb in ('review-and-triage', 'accept-ready-plan', 'accept-outcome-feedback')
       or o.idempotency_key like 'rw02-%' or o.idempotency_key like 'calcap-%')
       and o.created_at < now() - interval '30 days'
     order by o.created_at limit 20 for update skip locked);
  return new;
end
$$;
revoke all on function public.tool_call_expire() from public;
create trigger tool_call_expire before insert on public.tool_call
  for each row execute function public.tool_call_expire();
