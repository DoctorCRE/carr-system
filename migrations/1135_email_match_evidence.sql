-- rollback: forward-only; retain diagnostic match evidence.
-- expand-contract: expand; nullable JSON evidence has no backfill.
-- lock-review: short metadata-only ALTER on email_proposal; no row rewrite or backfill.
alter table public.email_proposal add column match_evidence jsonb;
