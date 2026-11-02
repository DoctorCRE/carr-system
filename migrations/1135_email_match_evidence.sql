-- rollback: forward-only; retain diagnostic and confirmed association provenance.
-- expand-contract: expand; nullable JSON evidence does not change existing proposal digests.
-- lock-review: short metadata-only ALTER on email_proposal; no row rewrite or backfill.
alter table public.email_proposal
  add column match_evidence jsonb,
  add column confirmed_association jsonb;
