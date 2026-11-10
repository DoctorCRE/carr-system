-- rollback: forward-only; restore implementations before restoring these controls.
-- expand-contract: contract; retire only obsolete catalog rows, preserving rule records and receipts.
-- lock-review: four primary-key deletions; refuse bound or actively approved controls.
-- PR 1801 removed reply gates; PR 2102 removed delegation and tier ceremony.
-- Their source declarations are gone, but old database rows can survive.
do $retire_stale_deploy_controls$
declare
  key text;
begin
  foreach key in array array[
    'completion_evidence', 'conduct_stop', 'delegation_tripwire', 'executor_tier'
  ] loop
    if exists (select 1 from ops.rule_control_binding where control_key = key) then
      raise exception '1171 REFUSED: % still has rule bindings', key;
    end if;
    if exists (
      select 1 from ops.rule_approval_receipt ar
      join rule r on r.id = ar.rule_id and r.status = 'active'
      where key = any(ar.requested_control_keys)
    ) then
      raise exception '1171 REFUSED: % backs active approved rules', key;
    end if;
    delete from ops.enforcement_control_catalog where control_key = key;
  end loop;
end $retire_stale_deploy_controls$;
