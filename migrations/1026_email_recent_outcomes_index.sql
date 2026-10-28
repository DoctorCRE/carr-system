-- rollback: drop index if exists public.email_proposal_recent_outcomes;
-- lock-review: index creation scans email_proposal and takes a write-blocking lock.
create index if not exists email_proposal_recent_outcomes
  on public.email_proposal (owner, result_recorded_at desc, id desc)
  where kind='reply_draft' and status in ('sent','failed') and result_recorded_at is not null;
