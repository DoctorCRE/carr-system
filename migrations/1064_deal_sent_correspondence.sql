-- rollback: forward fix only; preserve sent correspondence in activity.
-- expand-contract: append nullable email_context to the existing Deal Room activity read.
-- lock-review: brief view catalog replacement; no activity rewrite or new table grant.
create or replace view public.v_deal_room_activity as
select x.id, x.deal_id, x.occurred_at, a.slug as actor,
       x.kind, x.summary, x.detail, x.source,
       to_jsonb(x)->'email_context' as email_context
from public.activity x join public.actor a on a.id=x.actor_id
where x.deal_id is not null;
