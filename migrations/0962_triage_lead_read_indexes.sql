-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data, read contract, grant, or existing index changes.
-- lock-review: SHARE locks on four accumulating queues, then activity and event.
-- Ordinary builds use runner lock/statement timeouts; concurrent builds cannot
-- run in tools/migrate.py's transaction. Rehearsed on synthetic rows, not production sizes.

create index next_action_open_due_idx
  on public.next_action (due_on, id)
  where status = 'open' and due_on is not null;
create index capture_post_call_action_open_due_idx
  on public.capture_post_call_action (due_on, id)
  where status = 'open' and due_on is not null;
create index critical_date_open_due_idx
  on public.critical_date (due_on, id)
  where status = 'open';
create index ingest_inbox_new_received_idx
  on public.ingest_inbox (received_at, id)
  where status = 'new';

create index activity_lead_timeline_idx
  on public.activity (lead_id, occurred_at desc, id desc)
  where lead_id is not null;
create index event_lead_stage_mutation_idx
  on public.event (subject_id, mutation_order desc)
  where subject_type = 'lead' and new_value->>'stage' is not null;
