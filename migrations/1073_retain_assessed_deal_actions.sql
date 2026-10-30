-- rollback: reconcile retained open deal actions before restoring the unique deal-owner index.
-- lock-review: replace an index on next_action; no row rewrite or deletion.
drop index public.one_open_next_action_per_owner;
create unique index one_open_next_action_per_owner on public.next_action(subject_type,subject_id,owner_id)
  where status='open' and item_title is null and subject_type <> 'deal';
create index open_deal_next_actions_per_owner on public.next_action(subject_id,owner_id)
  where status='open' and item_title is null and subject_type='deal';
