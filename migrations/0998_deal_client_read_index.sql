-- rollback: forward-only; a successor may drop this performance-only index.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index build takes a SHARE lock on public.deal; the runner owns the
-- transaction, so CREATE INDEX CONCURRENTLY is unavailable. Retain runner timeouts.
-- WR-000520 (schema audit Q5): deal.client_id is a foreign key with no index. The Clients
-- workspace MAX(closed_on), contact logging's active-deal list and client merge all filter by it.
create index deal_client_closed_idx
  on public.deal (client_id, closed_on desc nulls last);
