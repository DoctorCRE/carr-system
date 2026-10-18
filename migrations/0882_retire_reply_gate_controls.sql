-- rollback: forward-only — re-declaring a retired control means restoring its hook and map entry, then letting ops/sync_control_catalog.py reinsert the row.
-- PR #1801 removes these controls, not their prose rules. Keep the binding
-- foreign key and active-approval trigger intact, as in 0348 and 0881.
do $retire_reply_gates$
declare
  key text;
  bound integer;
  claimed integer;
begin
  foreach key in array array[
    'artifact_read',
    'blocker_decider',
    'chat_lint',
    'close_before_open',
    'commit_claims',
    'completion_evidence',
    'conduct_stop',
    'drift_claim',
    'escalation',
    'gate_edit',
    'ledger_capture',
    'loose_work',
    'map_architecture',
    'peer_broadcast',
    'rule_shape',
    'settings_change',
    'staged_gate_bless',
    'unfiled_bug',
    'writing_lint'
  ] loop
    select count(*) into bound
      from ops.rule_control_binding where control_key = key;
    if bound > 0 then
      raise exception '0882 REFUSED: % still has % rule binding(s)', key, bound;
    end if;

    select count(*) into claimed
      from ops.rule_approval_receipt ar
      join rule r on r.id = ar.rule_id and r.status = 'active'
     where key = any(ar.requested_control_keys);
    if claimed > 0 then
      raise exception '0882 REFUSED: % backs % active approved rule(s)', key, claimed;
    end if;

    delete from ops.enforcement_control_catalog where control_key = key;
  end loop;
end $retire_reply_gates$;
