-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data, read contract, grant, or existing index changes.
-- lock-review: ordinary builds take SHARE locks on three history tables; retain
-- runner lock/statement timeouts. Synthetic rehearsal does not establish production lock duration.

create index critical_date_deal_due_idx
  on public.critical_date (deal_id, due_on, id);
create index next_action_deal_history_idx
  on public.next_action (subject_id, created_at, id)
  where subject_type = 'deal';
create index capture_post_call_action_deal_history_idx
  on public.capture_post_call_action (deal_id, accepted_at, id);
