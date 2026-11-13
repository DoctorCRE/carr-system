-- rollback: forward-only; restore prior consumers before removing the additive identity column.
-- expand-contract: expand; append item_id without changing existing view columns or response fields.
-- lock-review: view metadata replacement only; no row rewrite or new scan.
set local lock_timeout='5s';
set local statement_timeout='30s';

create or replace view v_deal_room_action as
select n.id,n.subject_id as deal_id,a.slug as owner,n.description,n.due_on,n.status,n.updated_at,n.hold_until,
       n.item_title,n.item_bullets,n.source_ref,n.created_at as created_order,n.version,'next_action:'||n.id as item_id
  from next_action n join actor a on a.id=n.owner_id where n.subject_type='deal'
union all
select pca.id,pca.deal_id,a.slug,pca.description,pca.due_on,pca.status,pca.updated_at,null::date,
       pca.item_title,pca.item_bullets,pca.source_ref,pca.accepted_at,pca.version,'capture_post_call_action:'||pca.id
  from capture_post_call_action pca join actor a on a.id=pca.owner_id;
