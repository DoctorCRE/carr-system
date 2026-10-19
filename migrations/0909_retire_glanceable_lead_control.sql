-- rollback: forward-only; restore checker source and declarations before reseeding its control.
-- expand-contract: contract; remove the catalog row after all source callers and declarations retire it.
-- lock-review: one primary-key catalog delete; refuse bindings and active approvals first.
-- Source CI no longer declares or runs glanceable_lead (WR-000003).
-- Match the guarded control retirement in 0836; retain live rule authority.
do $$
begin
  if exists (select 1 from ops.rule_control_binding
              where control_key = 'glanceable_lead') then
    raise exception '0909 REFUSED: glanceable_lead still has rule bindings';
  end if;
  if exists (select 1 from ops.rule_approval_receipt ar
              join public.rule r on r.id = ar.rule_id and r.status = 'active'
              where 'glanceable_lead' = any(ar.requested_control_keys)) then
    raise exception '0909 REFUSED: glanceable_lead backs active approved rules';
  end if;
  delete from ops.enforcement_control_catalog where control_key = 'glanceable_lead';
end $$;
