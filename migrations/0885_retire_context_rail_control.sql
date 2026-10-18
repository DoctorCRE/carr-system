-- rollback: forward-only — re-declaring context_rail means restoring hooks/context-handoff-gate.py and its map entry, then letting ops/sync_control_catalog.py reinsert the row.
-- PR #1810 deletes hooks/context-handoff-gate.py. Retire its catalog control
-- with the same guards as 0882: no rule binding and no active approved rule
-- may still claim it.
do $retire_context_rail$
declare
  bound integer;
  claimed integer;
begin
  select count(*) into bound
    from ops.rule_control_binding where control_key = 'context_rail';
  if bound > 0 then
    raise exception '0885 REFUSED: context_rail still has % rule binding(s)', bound;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'context_rail' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0885 REFUSED: context_rail backs % active approved rule(s)', claimed;
  end if;

  delete from ops.enforcement_control_catalog where control_key = 'context_rail';
end $retire_context_rail$;
