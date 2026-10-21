-- rollback: drop index if exists public.tool_call_jev_settlement_idx;
-- lock-review: one transactional index build on tool_call; holds SHARE until commit.
-- The audit checks settled attempts across all history, beside unrelated ledger traffic.
create index if not exists tool_call_jev_settlement_idx
  on public.tool_call (idempotency_key, actor_id, (response->>'receipt_id'))
  where verb = 'ask-jev-attempt' and response->>'cache_hit' = 'true';
