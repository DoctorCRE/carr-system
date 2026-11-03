-- rollback: forward-only; retain human resolution history.
-- expand-contract: expand; nullable fields preserve existing consumers.
-- lock-review: short ALTER on email_proposal with resolution constraint validation; no row backfill.
alter table public.email_proposal
  add column disposition text check (disposition in ('filed','handled','deferred')),
  add column resolution_note text check (length(resolution_note)<=2000),
  add column deferred_until timestamptz,
  add column correction_provenance jsonb;
alter table public.email_proposal drop constraint email_proposal_resolution;
alter table public.email_proposal add constraint email_proposal_resolution check (
  (status='pending' and resolved_by is null and resolved_at is null and resolution_reason is null) or
  (status='confirmed' and (kind<>'reply_draft' or disposition is not distinct from 'handled')
    and resolved_by is not null and resolved_at is not null) or
  (status='dismissed' and resolved_by is not null and resolved_at is not null and resolution_reason is not null) or
  (status in ('approved_to_send','sent','failed') and kind='reply_draft' and resolved_by is not null and resolved_at is not null));
