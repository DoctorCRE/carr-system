-- rollback: forward-only; retain the additive hold date for existing readers.
-- expand-contract: expand; append the hold date without changing action status or history.
-- lock-review: replaces a read-only view; no table rewrite or data mutation.
create or replace view v_deal_room_action as
select n.id, n.subject_id as deal_id, a.slug as owner, n.description,
       n.due_on, n.status, n.updated_at, n.hold_until
  from next_action n join actor a on a.id=n.owner_id
 where n.subject_type='deal'
union all
select pca.id, pca.deal_id, a.slug, pca.description, pca.due_on,
       pca.status, pca.updated_at, null::date as hold_until
  from capture_post_call_action pca join actor a on a.id=pca.owner_id;
