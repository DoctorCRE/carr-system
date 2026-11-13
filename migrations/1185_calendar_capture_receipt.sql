-- Expose only calendar capture activity receipts to bounded readers.
-- rollback: remove this view after readers stop using it.
-- expand-contract: additive owner-rights projection; no base-table grant.
-- lock-review: new view only; no table rewrite or data mutation.
CREATE VIEW public.v_calendar_capture_receipt AS
SELECT actor_id, idempotency_key, response->>'activity_id' AS activity_id
FROM public.tool_call
WHERE verb = 'log-activity' AND idempotency_key LIKE 'calcap-%';
